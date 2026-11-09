/* SPDX-License-Identifier: GPL-2.0-only */
/* CI-only stand-ins. Actual patched resource/caller functions are extracted. */
#ifndef J5PRIME_MCT_MOCK_H
#define J5PRIME_MCT_MOCK_H
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t phys_addr_t;
#define __init
#define CONFIG_FB_ON5XELTE_STAGE0 1
struct simplefb_platform_data { u32 width, height, stride; const char *format; };
#include "../../kernel/include/linux/on5xelte-stage0-fb.h"
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define MCT_NR_LOCAL 8
#define MCT_INT_SPI 0
#define panic(...) do { assert(!"unexpected upstream panic"); abort(); } while (0)
#define pr_err(...) ((void)0)
struct device_node { int synthetic; };
struct clk { const char *name; };
extern void *reg_base;
extern unsigned long clk_rate;
void *of_iomap(struct device_node *np, unsigned int index);
struct clk *of_clk_get_by_name(struct device_node *np, const char *name);
unsigned long clk_get_rate(struct clk *clock);
int clk_prepare_enable(struct clk *clock);
void clk_put(struct clk *clock);
void iounmap(void *base);
bool of_property_read_bool(struct device_node *np, const char *name);
int of_property_count_u32_elems(struct device_node *np, const char *name);
int of_property_read_u32_array(struct device_node *np, const char *name, u32 *values, unsigned int count);
int exynos4_timer_interrupts(struct device_node *np, unsigned int type, const u32 *local, size_t count);
int exynos4_clocksource_init(bool shared);
int exynos4_clockevent_init(void);
#endif
