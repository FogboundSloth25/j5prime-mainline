// SPDX-License-Identifier: GPL-2.0-only
/*
 * Diagnostic S-Boot scanout inheritance, not a DECON/display driver.
 * Independently written for SM-G570F rev04. Every MMIO access is a read.
 * Check/reserve the FULL backing before allocator startup. Publish platform
 * data only after another late, stable measurement; never enable static DT fb.
 */
#include <linux/err.h>
#include <linux/init.h>
#include <linux/initrd.h>
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/kernel.h>
#include <linux/libfdt.h>
#include <linux/memblock.h>
#include <linux/of_fdt.h>
#include <linux/on5xelte-stage0-fb.h>
#include <linux/platform_data/simplefb.h>
#include <linux/platform_device.h>
#include <linux/sizes.h>
#include <linux/string.h>
#include <linux/types.h>
#include <asm/memory.h>
#include <asm/sections.h>

#define DECON_BASE       0x14830000ULL
#define DECON_BYTES      0x1000
#define STAGE0_RAM_BASE  0x40000000ULL
#define STAGE0_RAM_END   0x60000000ULL
/* Exclude the complete camera/video/modem/crypto/GNSS/FDT high zone. */
#define FIRMWARE_HIGH    0x710ef000ULL
#define MAX_FDT_BYTES    SZ_2M
#define MAX_INITRD_BYTES SZ_16M
#define WIDTH           720U
#define HEIGHT          1280U
#define MAX_ROWS        4096U

struct stage0_registers {
	u32 wincon, whole_x, whole_y, offset_x, offset_y;
	u32 top_left, bottom_right, winmap, address;
};
struct stage0_surface {
	struct stage0_registers registers;
	u64 address, visible_bytes, backing_bytes, protected_bytes;
	struct simplefb_platform_data data;
};
static struct stage0_surface saved __initdata;
static phys_addr_t saved_fdt_phys __initdata;
static u32 saved_fdt_bytes __initdata;
static bool prepared __initdata;

static bool __init valid_span(u64 start, u64 bytes)
{
	return bytes && bytes <= U64_MAX - start;
}
static bool __init clear_of(const struct stage0_surface *surface, u64 start, u64 bytes)
{
	return valid_span(start, bytes) &&
		(surface->address >= start + bytes ||
		 start >= surface->address + surface->protected_bytes);
}
static void __init read_snapshot(void __iomem *base, struct stage0_registers *registers)
{
	registers->wincon = readl(base + 0x0050);
	registers->whole_x = readl(base + 0x0130);
	registers->whole_y = readl(base + 0x0134);
	registers->offset_x = readl(base + 0x0170);
	registers->offset_y = readl(base + 0x0174);
	registers->top_left = readl(base + 0x0230);
	registers->bottom_right = readl(base + 0x0234);
	registers->winmap = readl(base + 0x0340);
	registers->address = readl(base + 0x0880);
}
static int __init stable_snapshot(void __iomem *base, struct stage0_registers *registers)
{
	struct stage0_registers second;

	read_snapshot(base, registers);
	read_snapshot(base, &second);
	return memcmp(registers, &second, sizeof(second)) ? -EINVAL : 0;
}
static int __init decode_surface(const struct stage0_registers *registers,
				 struct stage0_surface *surface)
{
	const char *format;
	u64 bytes, end;
	u32 bpp = 4;

	memset(surface, 0, sizeof(*surface));
	if (!(registers->wincon & BIT(0)) || (registers->winmap & BIT(24)) ||
	    registers->top_left ||
	    registers->bottom_right != ((WIDTH - 1) << 13 | (HEIGHT - 1)) ||
	    registers->whole_x != WIDTH || registers->whole_y < HEIGHT ||
	    registers->whole_y > MAX_ROWS || registers->offset_x || registers->offset_y)
		return -EINVAL;
	switch ((registers->wincon >> 2) & 0x1f) {
	case 0: format = "a8r8g8b8"; break;
	case 1: format = "a8b8g8r8"; break;
	case 4: format = "x8r8g8b8"; break;
	case 5: format = "x8b8g8r8"; break;
	case 8: format = "r5g6b5"; bpp = 2; break;
	default: return -EINVAL; /* Modes 3/7 have no Linux format mapping here. */
	}
	bytes = (u64)WIDTH * registers->whole_y * bpp;
	if (!valid_span(registers->address, bytes))
		return -EINVAL;
	end = ALIGN((u64)registers->address + bytes, SZ_4K);
	/* Include the page envelope used by ioremap. First 512 MiB, all high
	 * carveouts and the entire second DRAM bank are unsupported for scanout.
	 */
	if (registers->address % SZ_4K || registers->address < STAGE0_RAM_END ||
	    end > FIRMWARE_HIGH || end <= registers->address)
		return -EINVAL;
	surface->registers = *registers;
	surface->address = registers->address;
	surface->visible_bytes = (u64)WIDTH * HEIGHT * bpp;
	surface->backing_bytes = bytes;
	surface->protected_bytes = end - registers->address;
	surface->data = (struct simplefb_platform_data) {
		.width = WIDTH, .height = HEIGHT, .stride = WIDTH * bpp, .format = format,
	};
	return 0;
}
static bool __init stage0_fdt_string(const void *fdt, int node, const char *property,
			      const char *expected)
{
	int bytes;
	const char *value = fdt_getprop(fdt, node, property, &bytes);

	return value && bytes == (int)strlen(expected) + 1 && !memcmp(value, expected, bytes);
}
static bool __init two_cells(const void *fdt, int node, const char *property)
{
	int bytes;
	const fdt32_t *value = fdt_getprop(fdt, node, property, &bytes);

	return value && bytes == (int)sizeof(*value) && fdt32_ld(value) == 2;
}
static bool __init separate_blocks(u64 a, u64 a_bytes, u64 b, u64 b_bytes)
{
	return a + a_bytes <= b || b + b_bytes <= a;
}

/* Pinned Linux declares fdt_check_full() but does not link its implementation.
 * Independently check v17 blocks, reserve terminator and token grammar using
 * the read-only libfdt helpers actually linked. Board checks follow below.
 */
static bool __init valid_fdt_layout(const void *fdt)
{
	u64 total, structure, structure_bytes, strings, strings_bytes, reserve, end;
	u64 address, bytes;
	const struct fdt_property *property;
	const char *name;
	bool children[32] = { }, root_closed = false;
	int offset = 0, next, depth = 0, length, count;
	u32 tag;

	if (!fdt || fdt_check_header(fdt) || fdt_version(fdt) != 17 ||
	    fdt_last_comp_version(fdt) != 16 || fdt_totalsize(fdt) > MAX_FDT_BYTES)
		return false;
	total = fdt_totalsize(fdt);
	structure = fdt_off_dt_struct(fdt);
	structure_bytes = fdt_size_dt_struct(fdt);
	strings = fdt_off_dt_strings(fdt);
	strings_bytes = fdt_size_dt_strings(fdt);
	reserve = fdt_off_mem_rsvmap(fdt);
	if (structure_bytes < 16 || !strings_bytes ||
	    !separate_blocks(structure, structure_bytes, strings, strings_bytes))
		return false;
	end = reserve;
	for (count = 0; count <= 64; count++) {
		if (end + 16 > total || fdt_get_mem_rsv(fdt, count, &address, &bytes))
			return false;
		end += 16;
		if (!address && !bytes)
			break;
		if (count == 64 || !valid_span(address, bytes))
			return false;
	}
	if (!separate_blocks(reserve, end - reserve, structure, structure_bytes) ||
	    !separate_blocks(reserve, end - reserve, strings, strings_bytes))
		return false;
	while ((u64)offset < structure_bytes) {
		tag = fdt_next_tag(fdt, offset, &next);
		if (next <= offset || (u64)next > structure_bytes)
			return false;
		switch (tag) {
		case FDT_BEGIN_NODE:
			name = fdt_get_name(fdt, offset, &length);
			if (root_closed || depth == 32 || !name || length < 0 || length > 255 ||
			    memchr(name, '/', length) ||
			    (!depth && (offset || length)) || (depth && !length))
				return false;
			if (depth)
				children[depth - 1] = true;
			children[depth++] = false;
			break;
		case FDT_PROP:
			if (!depth || children[depth - 1])
				return false;
			property = fdt_get_property_by_offset(fdt, offset, &length);
			if (!property || length < 0)
				return false;
			name = fdt_get_string(fdt, fdt32_to_cpu(property->nameoff), &length);
			if (!name || length <= 0 || length > 255)
				return false;
			break;
		case FDT_END_NODE:
			if (!depth)
				return false;
			if (!--depth)
				root_closed = true;
			break;
		case FDT_NOP:
			if (!offset)
				return false;
			break;
		case FDT_END:
			return root_closed && !depth && (u64)next == structure_bytes;
		default:
			return false;
		}
		offset = next;
	}
	return false;
}

static bool __init valid_machine_fdt(phys_addr_t physical)
{
	const void *fdt = initial_boot_params;

	return valid_fdt_layout(fdt) &&
		valid_span(physical, fdt_totalsize(fdt)) && physical >= STAGE0_RAM_BASE &&
		(u64)physical + fdt_totalsize(fdt) <= STAGE0_RAM_END &&
		!fdt_node_check_compatible(fdt, 0, "samsung,on5xelte") &&
		!fdt_node_check_compatible(fdt, 0, "samsung,exynos7570") &&
		two_cells(fdt, 0, "#address-cells") && two_cells(fdt, 0, "#size-cells");
}
static int __init fdt_address(const void *fdt, int node, const char *property, u64 *address)
{
	int bytes;
	const void *value = fdt_getprop(fdt, node, property, &bytes);

	if (!value)
		return bytes;
	if (bytes == 4)
		*address = fdt32_ld(value);
	else if (bytes == 8)
		*address = fdt64_ld(value);
	else
		return -EINVAL;
	return 0;
}
static int __init validate_fdt(const struct stage0_surface *surface,
			      phys_addr_t physical, bool check_initrd)
{
	const void *fdt = initial_boot_params;
	const fdt64_t *reg;
	u64 start, bytes, initrd_first, initrd_last;
	int chosen, reserved, node, length, count, frames = 0, first_ret, last_ret;

	if (!valid_machine_fdt(physical) || !clear_of(surface, physical, fdt_totalsize(fdt)))
		return -EINVAL;
	chosen = fdt_path_offset(fdt, "/chosen");
	reserved = fdt_path_offset(fdt, "/reserved-memory");
	if (chosen < 0 || reserved < 0 ||
	    !two_cells(fdt, reserved, "#address-cells") ||
	    !two_cells(fdt, reserved, "#size-cells"))
		return -EINVAL;
	fdt_getprop(fdt, reserved, "ranges", &length);
	if (length)
		return -EINVAL;
	count = fdt_num_mem_rsv(fdt);
	if (count < 0 || count > 64)
		return -EINVAL;
	for (node = 0; node < count; node++) {
		if (fdt_get_mem_rsv(fdt, node, &start, &bytes) || !clear_of(surface, start, bytes))
			return -EINVAL;
	}
	fdt_for_each_subnode(node, fdt, reserved) {
		reg = fdt_getprop(fdt, node, "reg", &length);
		if (!reg || length <= 0 || length % 16 || length > 16 * 16)
			return -EINVAL; /* Dynamic/unknown layouts fail closed. */
		for (count = 0; count < length / 8; count += 2)
			if (!clear_of(surface, fdt64_ld(reg + count), fdt64_ld(reg + count + 1)))
				return -EINVAL;
	}
	if (node != -FDT_ERR_NOTFOUND)
		return -EINVAL;
	/* A firmware-enabled or extra DT simplefb must not bypass this gate. */
	for (node = fdt_next_node(fdt, -1, NULL); node >= 0; node = fdt_next_node(fdt, node, NULL)) {
		if (fdt_node_check_compatible(fdt, node, "simple-framebuffer"))
			continue;
		if (++frames != 1 || node != fdt_path_offset(fdt, "/chosen/framebuffer@67000000") ||
		    !stage0_fdt_string(fdt, node, "status", "disabled"))
			return -EINVAL;
		fdt_getprop(fdt, node, "memory-region", &length);
		if (length != -FDT_ERR_NOTFOUND)
			return -EINVAL;
	}
	if (node != -FDT_ERR_NOTFOUND || frames != 1)
		return -EINVAL;
	first_ret = fdt_address(fdt, chosen, "linux,initrd-start", &initrd_first);
	last_ret = fdt_address(fdt, chosen, "linux,initrd-end", &initrd_last);
	if (first_ret == -FDT_ERR_NOTFOUND && last_ret == -FDT_ERR_NOTFOUND)
		return check_initrd && phys_initrd_size ? -EINVAL : 0;
	if (first_ret || last_ret || initrd_last <= initrd_first ||
	    initrd_last - initrd_first > MAX_INITRD_BYTES ||
	    initrd_first < STAGE0_RAM_BASE || initrd_last > STAGE0_RAM_END ||
	    !clear_of(surface, initrd_first, initrd_last - initrd_first))
		return -EINVAL;
	if (check_initrd && (initrd_first != phys_initrd_start ||
			    initrd_last - initrd_first != phys_initrd_size))
		return -EINVAL;
	return 0;
}
static int __init validate_early_memory(const struct stage0_surface *surface)
{
	struct memblock_region *region;
	u64 kernel_first = __pa_symbol(_text), kernel_last = __pa_symbol(_end);

	/* A text-offset-zero Image at 0x40000000 is the native contract. A
	 * legacy fixed 0x40080000 launch is NOT accepted as satisfying it.
	 */
	if (kernel_first != STAGE0_RAM_BASE || kernel_last <= kernel_first ||
	    kernel_last > 0x42000000ULL ||
	    !clear_of(surface, kernel_first, kernel_last - kernel_first) ||
	    memblock_start_of_DRAM() != STAGE0_RAM_BASE || memblock_end_of_DRAM() != STAGE0_RAM_END)
		return -EINVAL;
	for_each_mem_region(region) {
		if (!region->size)
			continue;
		if (!valid_span(region->base, region->size) || region->base < STAGE0_RAM_BASE ||
		    region->base + region->size > STAGE0_RAM_END ||
		    !clear_of(surface, region->base, region->size))
			return -EINVAL;
	}
	for_each_reserved_mem_region(region) {
		if (!region->size && !region->base)
			continue; /* memblock's initial empty sentinel. */
		if (!clear_of(surface, region->base, region->size))
			return -EINVAL;
	}
	if (phys_initrd_size && (phys_initrd_size > MAX_INITRD_BYTES ||
				!clear_of(surface, phys_initrd_start, phys_initrd_size)))
		return -EINVAL;
	return 0;
}
void __init on5xelte_stage0_fb_prepare(phys_addr_t fdt_phys)
{
	struct stage0_registers registers, final;
	struct stage0_surface candidate;
	void __iomem *base;
	int ret;

	prepared = false;
	memset(&saved, 0, sizeof(saved));
	/* Identify/check the board BEFORE accessing its hard-coded MMIO. */
	if (!valid_machine_fdt(fdt_phys)) {
		pr_err("stage0-fb: invalid board/FDT; no MMIO or scanout publication\n");
		return;
	}
	base = early_ioremap(DECON_BASE, DECON_BYTES);
	if (!base) {
		pr_err("stage0-fb: early DECON mapping failed; no scanout published\n");
		return;
	}
	ret = stable_snapshot(base, &registers);
	if (!ret)
		ret = decode_surface(&registers, &candidate);
	if (!ret)
		ret = validate_fdt(&candidate, fdt_phys, true);
	if (!ret)
		ret = validate_early_memory(&candidate);
	if (!ret)
		ret = memblock_reserve(candidate.address, candidate.protected_bytes);
	if (!ret) {
		read_snapshot(base, &final);
		if (memcmp(&registers, &final, sizeof(final)))
			ret = -EINVAL;
	}
	early_iounmap(base, DECON_BYTES);
	if (ret) {
		/* Any reservation made before detected drift remains reserved. */
		pr_err("stage0-fb: early full-backing/boot-memory gate failed (%d)\n", ret);
		return;
	}
	saved = candidate;
	saved_fdt_phys = fdt_phys;
	saved_fdt_bytes = fdt_totalsize(initial_boot_params);
	prepared = true;
	pr_info("ON5XELTE_STAGE0_FB_RESERVED: base=%#llx backing=%llu page-envelope=%llu\n",
		(unsigned long long)saved.address, (unsigned long long)saved.backing_bytes,
		(unsigned long long)saved.protected_bytes);
}
static int __init on5xelte_stage0_fb_register(void)
{
	struct stage0_registers registers;
	struct platform_device_info info = { };
	struct platform_device *device;
	struct resource resource = { };
	void __iomem *base;
	int ret;

	if (!prepared)
		return 0;
	base = ioremap(DECON_BASE, DECON_BYTES);
	if (!base)
		return -ENOMEM;
	ret = stable_snapshot(base, &registers);
	iounmap(base);
	if (ret || memcmp(&registers, &saved.registers, sizeof(registers)) ||
	    !initial_boot_params || fdt_totalsize(initial_boot_params) != saved_fdt_bytes ||
	    validate_fdt(&saved, saved_fdt_phys, false)) {
		pr_err("stage0-fb: late scanout/FDT drift; no simplefb device created\n");
		return -EINVAL;
	}
	/* No DT, clocks, regulators, pinctrl or power-domain dependencies.
	 * Only the checked visible surface reaches simplefb; the full backing
	 * is reserved separately and entirely outside Linux's allocator RAM.
	 */
	resource.name = "on5xelte-stage0-visible-scanout";
	resource.start = saved.address;
	resource.end = saved.address + saved.visible_bytes - 1;
	resource.flags = IORESOURCE_MEM;
	info.name = "simple-framebuffer";
	info.id = PLATFORM_DEVID_NONE;
	info.res = &resource;
	info.num_res = 1;
	info.data = &saved.data;
	info.size_data = sizeof(saved.data);
	device = platform_device_register_full(&info);
	if (IS_ERR(device))
		return PTR_ERR(device);
	pr_info("ON5XELTE_STAGE0_FB_HANDOFF: %ux%u stride=%u format=%s\n",
		saved.data.width, saved.data.height, saved.data.stride, saved.data.format);
	return 0; /* Registration alone does not prove simplefb probe/panel output. */
}
/* The ordinary simplefb driver initcall precedes this measurement/publication. */
device_initcall_sync(on5xelte_stage0_fb_register);
