/* SPDX-License-Identifier: GPL-2.0-only */
/* Host stand-ins only; real libfdt is used by the production gate. */
#ifndef J5PRIME_FRAMEBUFFER_MOCK_H
#define J5PRIME_FRAMEBUFFER_MOCK_H
#include <errno.h>
#include <endian.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <libfdt.h>
typedef uint8_t u8;
typedef uint16_t __le16;
typedef uint32_t __le32;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t phys_addr_t;
#define __init
#define __initdata
#define __initconst
#define cpu_to_le16(v) htole16(v)
#define cpu_to_le32(v) htole32(v)
#define wmb() mock_progress_barrier()
#define __iomem
#define U64_MAX UINT64_MAX
#define SZ_4K 0x1000ULL
#define SZ_2M 0x200000ULL
#define SZ_16M 0x1000000ULL
#define BIT(n) (1ULL << (n))
#define ALIGN(x,a) (((x) + (a) - 1) & ~((a) - 1))
#define ERR_PTR(error) ((void *)(intptr_t)(error))
#define PTR_ERR(pointer) ((long)(intptr_t)(pointer))
#define IS_ERR(pointer) ((uintptr_t)(pointer) >= (uintptr_t)-4095)
#define IORESOURCE_MEM 0x200
#define PLATFORM_DEVID_NONE (-1)
#define device_initcall_sync(fn)
#define pr_err mock_log
#define pr_info mock_log
struct memblock_region { u64 base, size; };
struct memblock_type { unsigned long cnt; struct memblock_region *regions; };
struct memblock { struct memblock_type memory, reserved; };
extern struct memblock memblock;
#define for_each_mem_region(r) \
    for ((r) = memblock.memory.regions; \
         (r) < memblock.memory.regions + memblock.memory.cnt; (r)++)
#define for_each_reserved_mem_region(r) \
    for ((r) = memblock.reserved.regions; \
         (r) < memblock.reserved.regions + memblock.reserved.cnt; (r)++)
struct simplefb_platform_data { u32 width, height, stride; const char *format; };
#define CONFIG_FB_ON5XELTE_STAGE0 1
#include "../../kernel/include/linux/on5xelte-stage0-fb.h"
struct resource { const char *name; u64 start, end; unsigned long flags; };
struct platform_device { int dummy; };
struct platform_device_info {
    const char *name; int id; const struct resource *res; unsigned int num_res;
    const void *data; size_t size_data;
};
extern void *initial_boot_params;
extern phys_addr_t phys_initrd_start;
extern unsigned long phys_initrd_size;
extern char _text[], _end[];
u64 mock_pa_symbol(const void *pointer);
#define __pa_symbol(sym) mock_pa_symbol(sym)
void mock_log(const char *format, ...);
u32 readl(const void *address);
void *early_ioremap(u64 physical, size_t bytes);
void early_iounmap(void *base, size_t bytes);
void *ioremap(u64 physical, size_t bytes);
void iounmap(void *base);
void memcpy_toio(void *target, const void *source, size_t bytes);
void mock_progress_barrier(void);
u64 memblock_start_of_DRAM(void);
u64 memblock_end_of_DRAM(void);
int memblock_reserve(u64 address, u64 bytes);
struct platform_device *platform_device_register_full(const struct platform_device_info *info);
#endif
