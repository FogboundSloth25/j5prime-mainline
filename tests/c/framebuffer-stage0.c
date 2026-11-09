/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include "framebuffer-kernel-mock.h"
#include "../../kernel/drivers/video/fbdev/on5xelte-stage0-fb.c"
#include "../../kernel/drivers/video/fbdev/on5xelte-stage0-progress.c"

static unsigned int checks, reads, reservations, publications, drift_after;
static bool map_fail, reserve_fail, publication_fail;
static u32 mmio[DECON_BYTES / 4];
#define PROGRESS_GUARD 16U
static u8 progress_storage[PROGRESS_CELL * WIDTH * 4 + 2 * PROGRESS_GUARD];
#define progress_pixels (progress_storage + PROGRESS_GUARD)
static unsigned int progress_maps, progress_unmaps, progress_writes, progress_barriers;
static u64 progress_map_bytes, progress_column;
static bool progress_map_fail;
_Alignas(8) static char flat_tree[16384];
static struct memblock_region memories[8], reserved_regions[32];
struct memblock memblock;
void *initial_boot_params;
phys_addr_t phys_initrd_start;
unsigned long phys_initrd_size;
char _text[1], _end[1];
static u64 kernel_first, kernel_last;
static struct platform_device published;
#define CHECK(test) do { checks++; assert(test); } while (0)

u64 mock_pa_symbol(const void *pointer)
{
    assert(pointer == _text || pointer == _end);
    return pointer == _text ? kernel_first : kernel_last;
}
void mock_log(const char *format, ...) { (void)format; }
u32 readl(const void *address)
{
    ptrdiff_t offset = (const char *)address - (const char *)mmio;
    const unsigned int allowed[] = {0x50,0x130,0x134,0x170,0x174,0x230,0x234,0x340,0x880};
    unsigned int i;
    for (i = 0; i < sizeof(allowed)/sizeof(allowed[0]); i++)
        if (offset == allowed[i]) break;
    assert(i != sizeof(allowed)/sizeof(allowed[0]));
    if (drift_after && reads++ == drift_after) mmio[0x134 / 4] ^= 1;
    else if (!drift_after) reads++;
    return mmio[offset / 4];
}
void *early_ioremap(u64 physical, size_t bytes)
{
    u32 bpp;
    if (physical == DECON_BASE) {
        assert(bytes == DECON_BYTES);
        return map_fail ? NULL : mmio;
    }
    /* Any other physical mapping must be the gate's reserved live surface. */
    CHECK(prepared && reservations == 1 && physical == saved.address);
    CHECK(memblock.reserved.regions[memblock.reserved.cnt - 1].base == physical);
    CHECK(memblock.reserved.regions[memblock.reserved.cnt - 1].size == saved.protected_bytes);
    bpp = saved.data.stride / WIDTH;
    CHECK(bytes > (PROGRESS_CELL - 1) * saved.data.stride && bytes <= saved.protected_bytes);
    progress_column = bytes - (PROGRESS_CELL - 1) * saved.data.stride - PROGRESS_CELL * bpp;
    CHECK(progress_column % (PROGRESS_CELL * bpp) == 0);
    CHECK(progress_column / (PROGRESS_CELL * bpp) < 7);
    progress_map_bytes = bytes;
    progress_maps++;
    return progress_map_fail ? NULL : progress_pixels;
}
void early_iounmap(void *base, size_t bytes)
{
    if (base == mmio) { assert(bytes == DECON_BYTES); return; }
    CHECK(base == progress_pixels && bytes == progress_map_bytes);
    progress_unmaps++;
}
void memcpy_toio(void *target, const void *source, size_t bytes)
{
    uintptr_t pointer = (uintptr_t)target, first = (uintptr_t)progress_pixels;
    u64 offset;
    u32 bpp = saved.data.stride / WIDTH;
    /* Device-register targets and writes outside one visible cell abort. */
    CHECK(prepared && reservations == 1 && pointer >= first);
    offset = pointer - first;
    CHECK((u64)offset + bytes <= progress_map_bytes);
    CHECK((u64)offset / saved.data.stride < PROGRESS_CELL);
    CHECK((u64)offset % saved.data.stride == progress_column);
    CHECK(bytes == PROGRESS_CELL * bpp);
    memcpy(target, source, bytes);
    progress_writes++;
}
void mock_progress_barrier(void) { progress_barriers++; }
void *ioremap(u64 physical, size_t bytes) { return early_ioremap(physical, bytes); }
void iounmap(void *base) { assert(base == mmio); }
u64 memblock_start_of_DRAM(void)
{
    return memblock.memory.cnt ? memblock.memory.regions[0].base : 0;
}
u64 memblock_end_of_DRAM(void)
{
    struct memblock_region *last;
    if (!memblock.memory.cnt) return 0;
    last = memblock.memory.regions + memblock.memory.cnt - 1;
    return last->base + last->size;
}
int memblock_reserve(u64 address, u64 bytes)
{
    reservations++;
    if (reserve_fail) return -ENOMEM;
    assert(memblock.reserved.cnt < 32);
    memblock.reserved.regions[memblock.reserved.cnt++] = (struct memblock_region){address, bytes};
    return 0;
}
struct platform_device *platform_device_register_full(const struct platform_device_info *info)
{
    const struct simplefb_platform_data *data = info->data;
    publications++;
    CHECK(!strcmp(info->name, "simple-framebuffer"));
    CHECK(info->id == PLATFORM_DEVID_NONE && info->num_res == 1);
    CHECK(info->size_data == sizeof(*data));
    CHECK(info->res->start == saved.address);
    CHECK(info->res->end + 1 == saved.address + saved.visible_bytes);
    CHECK(info->res->flags == IORESOURCE_MEM);
    CHECK(data->width == WIDTH && data->height == HEIGHT);
    CHECK(data->stride == saved.data.stride && !strcmp(data->format, saved.data.format));
    return publication_fail ? ERR_PTR(-ENOMEM) : &published;
}
static int path(const char *name)
{
    int node = fdt_path_offset(flat_tree, name);
    assert(node >= 0);
    return node;
}
static void set_u32(const char *node, const char *name, u32 value)
{
    assert(!fdt_setprop_u32(flat_tree, path(node), name, value));
}
static void set_string(const char *node, const char *name, const char *value)
{
    assert(!fdt_setprop_string(flat_tree, path(node), name, value));
}
static void set_reg(const char *node, u64 address, u64 bytes)
{
    fdt64_t cells[2] = {cpu_to_fdt64(address), cpu_to_fdt64(bytes)};
    assert(!fdt_setprop(flat_tree, path(node), "reg", cells, sizeof(cells)));
}
static void reset(void)
{
    const char compatibles[] = "samsung,on5xelte\0samsung,exynos7570";
    memset(mmio, 0, sizeof(mmio));
    mmio[0x50 / 4] = 1; mmio[0x130 / 4] = WIDTH; mmio[0x134 / 4] = 2560;
    mmio[0x234 / 4] = ((WIDTH - 1) << 13) | (HEIGHT - 1); mmio[0x880 / 4] = 0x67000000;
    assert(!fdt_create_empty_tree(flat_tree, sizeof(flat_tree)));
    assert(!fdt_setprop(flat_tree, 0, "compatible", compatibles, sizeof(compatibles)));
    set_u32("/", "#address-cells", 2); set_u32("/", "#size-cells", 2);
    assert(fdt_add_subnode(flat_tree, 0, "chosen") >= 0);
    assert(fdt_add_subnode(flat_tree, path("/chosen"), "framebuffer@67000000") >= 0);
    set_string("/chosen/framebuffer@67000000", "compatible", "simple-framebuffer");
    set_string("/chosen/framebuffer@67000000", "status", "disabled");
    assert(fdt_add_subnode(flat_tree, 0, "reserved-memory") >= 0);
    set_u32("/reserved-memory", "#address-cells", 2); set_u32("/reserved-memory", "#size-cells", 2);
    assert(!fdt_setprop(flat_tree, path("/reserved-memory"), "ranges", NULL, 0));
    assert(fdt_add_subnode(flat_tree, path("/reserved-memory"), "firmware@46000000") >= 0);
    set_reg("/reserved-memory/firmware@46000000", 0x46000000, 0x1000000);
    initial_boot_params = flat_tree; phys_initrd_start = phys_initrd_size = 0;
    kernel_first = 0x40000000; kernel_last = 0x40c00000;
    memories[0] = (struct memblock_region){STAGE0_RAM_BASE, STAGE0_RAM_END - STAGE0_RAM_BASE};
    reserved_regions[0] = (struct memblock_region){kernel_first, kernel_last-kernel_first};
    memblock = (struct memblock){{1, memories}, {1, reserved_regions}};
    reads = reservations = publications = drift_after = 0;
    map_fail = reserve_fail = publication_fail = false; prepared = false;
    progress_maps = progress_unmaps = progress_writes = progress_barriers = 0;
    progress_map_fail = false; progress_map_bytes = progress_column = 0;
    memset(progress_storage, 0xa5, sizeof(progress_storage));
}
static void blocked(void)
{
    on5xelte_stage0_fb_prepare(0x47f00000);
    CHECK(!prepared); CHECK(!reservations); CHECK(!publications); CHECK(!progress_writes);
    CHECK(!on5xelte_stage0_fb_register()); CHECK(!publications);
}
static void ready(void)
{
    on5xelte_stage0_fb_prepare(0x47f00000);
    CHECK(prepared); CHECK(reservations == 1); CHECK(!publications);
    CHECK(progress_maps == 1 && progress_unmaps == 1 && progress_writes == PROGRESS_CELL && progress_barriers == 1);
    CHECK(saved.protected_bytes >= saved.backing_bytes);
    CHECK(!on5xelte_stage0_fb_register()); CHECK(publications == 1);
}
static void modes_and_geometry(void)
{
    unsigned int mode, i;
    const char *formats[32] = {[0]="a8r8g8b8",[1]="a8b8g8r8",[4]="x8r8g8b8",
                              [5]="x8b8g8r8",[8]="r5g6b5"};
    const struct { unsigned int offset; u32 value; } bad[] = {
        {0x50,0},{0x340,1U<<24},{0x230,1},{0x234,0},
        {0x234,((WIDTH-1)<<13)|(HEIGHT-1)|(1U<<31)},
        {0x130,WIDTH+1},{0x134,HEIGHT-1},{0x134,4097},{0x134,UINT32_MAX},
        {0x170,1},{0x174,1},{0x880,0x67000004},
    };
    for (mode=0; mode<32; mode++) {
        reset(); mmio[0x50/4] = 1 | (mode<<2);
        if (!formats[mode]) { blocked(); continue; }
        ready(); CHECK(!strcmp(saved.data.format,formats[mode]));
        CHECK(saved.data.stride == WIDTH*(mode==8 ? 2 : 4));
        CHECK(saved.backing_bytes == (u64)saved.data.stride*2560);
        CHECK(saved.visible_bytes == (u64)saved.data.stride*HEIGHT);
    }
    for (i=0; i<sizeof(bad)/sizeof(bad[0]); i++) {
        reset(); mmio[bad[i].offset/4]=bad[i].value; blocked();
    }
    reset(); mmio[0x134/4]=HEIGHT; ready();
    reset(); mmio[0x134/4]=4096; ready();
}
static void spans_and_full_backing(void)
{
    unsigned int i;
    const u32 bad_addresses[] = {0,0x14830000,0x40000000,0x42000000,0x46000000,
        0x47000000,0x50000000,0x52800000,0x5ffff000,0x710ef000,0x7e000000,
        0x80000000,0xfffff000};
    const u64 backing = (u64)WIDTH*2560*4;
    for (i=0;i<sizeof(bad_addresses)/sizeof(bad_addresses[0]);i++) {
        reset(); mmio[0x880/4]=bad_addresses[i]; blocked();
    }
    reset(); mmio[0x880/4]=STAGE0_RAM_END; ready();
    reset(); mmio[0x880/4]=FIRMWARE_HIGH-backing; ready();
    reset(); mmio[0x880/4]=FIRMWARE_HIGH-backing+SZ_4K; blocked();
    reset(); reserved_regions[memblock.reserved.cnt++] =
        (struct memblock_region){0x67000000+WIDTH*HEIGHT*4+4096,4096}; blocked();
    reset(); assert(!fdt_add_mem_rsv(flat_tree,0x67000000+WIDTH*HEIGHT*4+4096,4096)); blocked();
    reset(); set_reg("/reserved-memory/firmware@46000000",0x67000000+WIDTH*HEIGHT*4+4096,4096); blocked();
    reset(); mmio[0x134/4]=HEIGHT+1;
    reserved_regions[memblock.reserved.cnt++] =
        (struct memblock_region){0x67000000+WIDTH*(HEIGHT+1)*4,1}; blocked();
    reset(); set_reg("/reserved-memory/firmware@46000000",0x67000000+backing,4096); ready();
    reset(); set_reg("/reserved-memory/firmware@46000000",UINT64_MAX-15,32); blocked();
    reset(); set_reg("/reserved-memory/firmware@46000000",0x70000000,0); blocked();
    reset(); reserved_regions[memblock.reserved.cnt++] = (struct memblock_region){UINT64_MAX-2,4}; blocked();
}
static void fdt_and_memory(void)
{
    reset(); set_u32("/", "#address-cells",1); blocked(); CHECK(reads==0);
    reset(); set_u32("/reserved-memory","#size-cells",1); blocked();
    reset(); assert(!fdt_delprop(flat_tree,path("/reserved-memory"),"ranges")); blocked();
    reset(); assert(!fdt_delprop(flat_tree,path("/reserved-memory/firmware@46000000"),"reg")); blocked();
    reset(); set_string("/chosen/framebuffer@67000000","status","okay"); blocked();
    reset(); set_u32("/chosen/framebuffer@67000000","memory-region",7); blocked();
    reset(); assert(fdt_add_subnode(flat_tree,path("/chosen"),"framebuffer@68000000")>=0);
    set_string("/chosen/framebuffer@68000000","compatible","simple-framebuffer");
    set_string("/chosen/framebuffer@68000000","status","disabled"); blocked();
    reset(); set_string("/","compatible","samsung,other-phone"); blocked(); CHECK(reads==0);
    reset(); initial_boot_params=NULL; blocked(); CHECK(reads==0);
    reset(); on5xelte_stage0_fb_prepare(0x67000000); CHECK(!prepared && !reservations && !reads);
    reset(); on5xelte_stage0_fb_prepare(UINT64_MAX-100); CHECK(!prepared && !reservations && !reads);
    reset(); memories[1]=(struct memblock_region){0x61000000,0x1000}; memblock.memory.cnt=2; blocked();
    reset(); memblock.memory.cnt=0; blocked();
    reset(); kernel_first=0x40080000; blocked();
    reset(); kernel_last=0x42000001; blocked();
    reset(); reserved_regions[0]=(struct memblock_region){0,0}; ready();
    reset(); phys_initrd_start=0x42000000; phys_initrd_size=64;
    assert(!fdt_setprop_u64(flat_tree,path("/chosen"),"linux,initrd-start",phys_initrd_start));
    assert(!fdt_setprop_u64(flat_tree,path("/chosen"),"linux,initrd-end",phys_initrd_start+phys_initrd_size)); ready();
    reset(); phys_initrd_start=0x42000000; phys_initrd_size=64; blocked();
    reset(); assert(!fdt_setprop_u64(flat_tree,path("/chosen"),"linux,initrd-start",0x42000000)); blocked();
    reset(); phys_initrd_start=0x42000000; phys_initrd_size=64;
    set_u32("/chosen","linux,initrd-start",0x42000000); set_u32("/chosen","linux,initrd-end",0x42000080); blocked();
    reset(); phys_initrd_start=0x42000000; phys_initrd_size=64;
    set_u32("/chosen","linux,initrd-start",0x42000000); set_u32("/chosen","linux,initrd-end",0x42000040); ready();
}
static void malformed_fdt_layout(void)
{
    u32 offset;
    int property;
    fdt32_t *word;
    reset(); fdt_set_version(flat_tree, 16); blocked(); CHECK(reads == 0);
    reset(); fdt_set_off_dt_strings(flat_tree, fdt_off_dt_struct(flat_tree));
    blocked(); CHECK(reads == 0);
    reset(); fdt_set_off_mem_rsvmap(flat_tree, fdt_off_dt_struct(flat_tree));
    blocked(); CHECK(reads == 0);
    reset(); fdt_set_size_dt_struct(flat_tree, fdt_size_dt_struct(flat_tree) - 4);
    blocked(); CHECK(reads == 0);
    reset(); offset = fdt_off_dt_struct(flat_tree);
    word = (fdt32_t *)(flat_tree + offset); *word = cpu_to_fdt32(FDT_END_NODE);
    blocked(); CHECK(reads == 0);
    reset(); property = fdt_first_property_offset(flat_tree, 0); assert(property >= 0);
    offset = fdt_off_dt_struct(flat_tree) + property;
    word = (fdt32_t *)(flat_tree + offset + 4); *word = cpu_to_fdt32(UINT32_MAX);
    blocked(); CHECK(reads == 0);
    reset(); property = fdt_first_property_offset(flat_tree, 0); assert(property >= 0);
    offset = fdt_off_dt_struct(flat_tree) + property;
    word = (fdt32_t *)(flat_tree + offset + 8); *word = cpu_to_fdt32(UINT32_MAX);
    blocked(); CHECK(reads == 0);
    reset(); offset = fdt_off_mem_rsvmap(flat_tree);
    memset(flat_tree + offset, 0xff, 16); blocked(); CHECK(reads == 0);
}

static void failures_and_drift(void)
{
    reset(); map_fail=true; blocked();
    reset(); drift_after=9; blocked();
    reset(); reserve_fail=true; on5xelte_stage0_fb_prepare(0x47f00000);
    CHECK(!prepared && reservations==1); CHECK(!on5xelte_stage0_fb_register() && !publications);
    reset(); drift_after=18; on5xelte_stage0_fb_prepare(0x47f00000);
    CHECK(!prepared && reservations==1); CHECK(!on5xelte_stage0_fb_register() && !publications);
    reset(); on5xelte_stage0_fb_prepare(0x47f00000); CHECK(prepared);
    mmio[0x134/4]^=1; CHECK(on5xelte_stage0_fb_register()==-EINVAL && !publications);
    reset(); on5xelte_stage0_fb_prepare(0x47f00000); CHECK(prepared);
    drift_after=reads+9; CHECK(on5xelte_stage0_fb_register()==-EINVAL && !publications);
    reset(); on5xelte_stage0_fb_prepare(0x47f00000); CHECK(prepared);
    map_fail=true; CHECK(on5xelte_stage0_fb_register()==-ENOMEM && !publications);
    reset(); on5xelte_stage0_fb_prepare(0x47f00000); CHECK(prepared);
    set_string("/chosen/framebuffer@67000000","status","okay");
    CHECK(on5xelte_stage0_fb_register()==-EINVAL && !publications);
    reset(); on5xelte_stage0_fb_prepare(0x47f00000); CHECK(prepared);
    publication_fail=true; CHECK(on5xelte_stage0_fb_register()==-ENOMEM && publications==1);
}
static u32 progress_pixel_at(u32 x, u32 y, u32 bpp)
{
    u32 wide;
    uint16_t narrow;
    const u8 *pixel = progress_pixels + y * saved.data.stride + x * bpp;
    if (bpp == 2) { memcpy(&narrow, pixel, 2); return le16toh(narrow); }
    memcpy(&wide, pixel, 4); return le32toh(wide);
}
static void progress_cells_and_refusals(void)
{
    const unsigned int modes[] = {0, 1, 4, 5, 8};
    unsigned int i, phase, offset, writes_before, reads_before;
    u8 previous[sizeof(progress_storage)];
    u32 bpp, green, red, white;
    struct simplefb_platform_data data;
    for (i = 0; i < sizeof(modes) / sizeof(modes[0]); i++) {
        reset(); mmio[0x50 / 4] = 1 | (modes[i] << 2);
        on5xelte_stage0_fb_prepare(0x47f00000); CHECK(prepared);
        bpp = modes[i] == 8 ? 2 : 4;
        green = bpp == 2 ? 0x268c : modes[i] == 1 || modes[i] == 5 ? 0xff60d020 : 0xff20d060;
        red = bpp == 2 ? 0xe186 : modes[i] == 1 || modes[i] == 5 ? 0xff3030e0 : 0xffe03030;
        white = bpp == 2 ? 0xffff : 0xffffffff;
        CHECK(progress_pixel_at(2, 2, bpp) == green);
        CHECK(progress_pixel_at(0, 0, bpp) == white);
        for (phase = 2; phase <= 7; phase++) {
            memcpy(previous, progress_storage, sizeof(previous));
            writes_before = progress_writes;
            on5xelte_stage0_fb_checkpoint((enum on5xelte_stage0_progress)phase);
            CHECK(prepared && progress_writes == writes_before + PROGRESS_CELL);
            CHECK(progress_pixel_at((phase - 1) * PROGRESS_CELL + 2, 2, bpp) == (phase == 7 ? red : green));
            for (offset = 0; offset < sizeof(progress_storage); offset++) {
                ptrdiff_t position = (ptrdiff_t)offset - PROGRESS_GUARD;
                if (position >= 0 && (u64)position < (u64)PROGRESS_CELL * saved.data.stride &&
                    (u64)position % saved.data.stride >= (phase - 1) * PROGRESS_CELL * bpp &&
                    (u64)position % saved.data.stride < phase * PROGRESS_CELL * bpp)
                    continue;
                CHECK(progress_storage[offset] == previous[offset]);
            }
        }
        CHECK(progress_maps == 7 && progress_unmaps == 7 && progress_barriers == 7);
    }
    reset(); on5xelte_stage0_fb_checkpoint(ON5XELTE_STAGE0_TIMER_BEGIN);
    CHECK(!reads && !progress_writes);
    reset(); on5xelte_stage0_fb_prepare(0x47f00000); CHECK(prepared);
    reads_before = reads; writes_before = progress_writes;
    on5xelte_stage0_fb_checkpoint((enum on5xelte_stage0_progress)0);
    on5xelte_stage0_fb_checkpoint((enum on5xelte_stage0_progress)8);
    CHECK(reads == reads_before && progress_writes == writes_before);
    data = saved.data;
    CHECK(on5xelte_stage0_progress_draw(saved.address, saved.protected_bytes, &data, 0) == -EINVAL);
    CHECK(on5xelte_stage0_progress_draw(saved.address, saved.protected_bytes, &data, 8) == -EINVAL);
    CHECK(on5xelte_stage0_progress_draw(saved.address, saved.protected_bytes, NULL, 1) == -EINVAL);
    data.stride++; CHECK(on5xelte_stage0_progress_draw(saved.address, saved.protected_bytes, &data, 1) == -EINVAL);
    data = saved.data; data.format = "unsupported";
    CHECK(on5xelte_stage0_progress_draw(saved.address, saved.protected_bytes, &data, 1) == -EINVAL);
    data = saved.data; data.format = NULL;
    CHECK(on5xelte_stage0_progress_draw(saved.address, saved.protected_bytes, &data, 1) == -EINVAL);
    data = saved.data;
    CHECK(on5xelte_stage0_progress_draw(0x40000000, saved.protected_bytes, &data, 1) == -EINVAL);
    CHECK(on5xelte_stage0_progress_draw(0x14830000, saved.protected_bytes, &data, 1) == -EINVAL);
    CHECK(on5xelte_stage0_progress_draw(saved.address + 1, saved.protected_bytes, &data, 1) == -EINVAL);
    CHECK(on5xelte_stage0_progress_draw(UINT64_MAX - 15, saved.protected_bytes, &data, 1) == -EINVAL);
    CHECK(on5xelte_stage0_progress_draw(saved.address, UINT64_MAX, &data, 1) == -EINVAL);
    CHECK(on5xelte_stage0_progress_draw(saved.address, saved.visible_bytes - 1, &data, 1) == -EINVAL);
    CHECK(progress_writes == writes_before && progress_maps == 1);
    reset(); progress_map_fail = true; on5xelte_stage0_fb_prepare(0x47f00000);
    CHECK(prepared && reservations == 1 && progress_maps == 1 && !progress_writes && !progress_unmaps);
    CHECK(!on5xelte_stage0_fb_register() && publications == 1);
    reset(); on5xelte_stage0_fb_prepare(0x47f00000); CHECK(prepared);
    writes_before = progress_writes; mmio[0x134 / 4] ^= 1;
    on5xelte_stage0_fb_checkpoint(ON5XELTE_STAGE0_TIMER_BEGIN);
    CHECK(!prepared && progress_writes == writes_before);
    CHECK(!on5xelte_stage0_fb_register() && !publications);
    reset(); on5xelte_stage0_fb_prepare(0x47f00000); CHECK(prepared);
    writes_before = progress_writes; reads_before = reads;
    set_string("/", "compatible", "samsung,other-phone");
    on5xelte_stage0_fb_checkpoint(ON5XELTE_STAGE0_TIMER_BEGIN);
    CHECK(!prepared && progress_writes == writes_before && reads == reads_before);
    CHECK(!on5xelte_stage0_fb_register() && !publications);
    reset(); drift_after = 27 + 9; on5xelte_stage0_fb_prepare(0x47f00000);
    CHECK(!prepared && reservations == 1 && !progress_writes);
}

int main(void)
{
    modes_and_geometry(); spans_and_full_backing(); fdt_and_memory(); malformed_fdt_layout(); failures_and_drift(); progress_cells_and_refusals();
    printf("framebuffer-stage0: %u synthetic checks passed; real libfdt, mocked MMIO/memblock/platform only; no hardware evidence.\n",checks);
    return 0;
}
