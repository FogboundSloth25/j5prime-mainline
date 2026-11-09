/* SPDX-License-Identifier: GPL-2.0-only */
#include "mct-kernel-mock.h"
#include "selected-mct-production.c"
void *reg_base;
unsigned long clk_rate;
static struct clk tick_clock = { "fin_pll" }, mct_clock = { "mct" };
static unsigned char mapped_registers[4096];
static int enable_error;
static unsigned int maps, unmaps, enables, puts, interrupts, counters, events, markers;
void *of_iomap(struct device_node *np, unsigned int index)
{ assert(np && index == 0); maps++; return mapped_registers; }
struct clk *of_clk_get_by_name(struct device_node *np, const char *name)
{ assert(np); if (!strcmp(name, "fin_pll")) return &tick_clock; assert(!strcmp(name, "mct")); return &mct_clock; }
unsigned long clk_get_rate(struct clk *clock)
{ assert(clock == &tick_clock); return 26000000; }
int clk_prepare_enable(struct clk *clock)
{ assert(clock == &mct_clock); enables++; return enable_error; }
void clk_put(struct clk *clock)
{ assert(clock == &mct_clock || clock == &tick_clock); puts++; }
void iounmap(void *base)
{ assert(base == mapped_registers); unmaps++; }
void on5xelte_stage0_fb_checkpoint(enum on5xelte_stage0_progress phase)
{ assert(phase == ON5XELTE_STAGE0_MCT_CLOCK_FAILED); markers++; }
bool of_property_read_bool(struct device_node *np, const char *name)
{ assert(np && !strcmp(name, "samsung,frc-shared")); return false; }
int of_property_count_u32_elems(struct device_node *np, const char *name)
{ assert(np && !strcmp(name, "samsung,local-timers")); return -EINVAL; }
int of_property_read_u32_array(struct device_node *np, const char *name, u32 *values, unsigned int count)
{ assert(np && name && values && count); return -EINVAL; }
int exynos4_timer_interrupts(struct device_node *np, unsigned int type, const u32 *local, size_t count)
{ assert(np && type == MCT_INT_SPI && local[0] == 0 && count == MCT_NR_LOCAL); interrupts++; return 0; }
int exynos4_clocksource_init(bool shared)
{ assert(!shared); counters++; return 0; }
int exynos4_clockevent_init(void)
{ events++; return 0; }
int main(int argc, char **argv)
{
    struct device_node np = { 1 };
    int result;
    assert(argc == 2);
    enable_error = atoi(argv[1]);
    result = mct_init_dt(&np, MCT_INT_SPI);
    printf("{\"enable_error\":%d,\"result\":%d,\"maps\":%u,\"unmaps\":%u,"
           "\"enables\":%u,\"puts\":%u,\"interrupts\":%u,\"counters\":%u,"
           "\"events\":%u,\"markers\":%u,\"reg_base_cleared\":%s}\n",
           enable_error, result, maps, unmaps, enables, puts, interrupts, counters, events,
           markers, reg_base == NULL ? "true" : "false");
    return 0;
}
