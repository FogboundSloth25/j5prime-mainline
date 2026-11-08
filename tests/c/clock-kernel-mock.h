/* SPDX-License-Identifier: GPL-2.0-only */
/* Minimal host test stand-ins, not Linux headers or a CCF implementation. */
#ifndef J5PRIME_CLOCK_KERNEL_MOCK_H
#define J5PRIME_CLOCK_KERNEL_MOCK_H

#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t resource_size_t;
typedef unsigned int spinlock_t;

#define __iomem
#define __init
#define GFP_KERNEL 0
#define kzalloc_obj(object) kzalloc(sizeof(object), GFP_KERNEL)
#define BIT(n) (1UL << (n))
#define CLK_IGNORE_UNUSED BIT(3)
#define CLK_GET_RATE_NOCACHE BIT(6)
#define DEFINE_SPINLOCK(name) spinlock_t name
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#define ERR_PTR(error) ((void *)(intptr_t)(error))
#define PTR_ERR(ptr) ((long)(intptr_t)(ptr))
#define IS_ERR(ptr) ((uintptr_t)(ptr) >= (uintptr_t)-4095)
#define pr_err mock_pr_err

struct clk_hw;
struct device_node;

struct clk_ops {
	int (*prepare)(struct clk_hw *hw);
	int (*enable)(struct clk_hw *hw);
	void (*disable)(struct clk_hw *hw);
	int (*is_enabled)(struct clk_hw *hw);
	unsigned long (*recalc_rate)(struct clk_hw *hw, unsigned long rate);
};

struct clk_parent_data {
	const char *fw_name;
	int index;
};

struct clk_init_data {
	const char *name;
	const struct clk_ops *ops;
	unsigned long flags;
	const struct clk_parent_data *parent_data;
	u8 num_parents;
};

struct clk_hw {
	const struct clk_init_data *init;
	struct clk_hw *parent;
	unsigned long rate;
	const struct clk_ops *ops;
	struct clk_init_data saved_init;
	struct clk_parent_data saved_parent;
	bool registered;
};

struct clk_gate {
	struct clk_hw hw;
	void *reg;
	u8 bit_idx;
	u8 flags;
	spinlock_t *lock;
};

struct resource {
	resource_size_t start, end;
};

struct device_node {
	struct resource resource;
	int resource_error, cells_error;
	u32 cells;
	int phandle_count, name_count;
	const char *clock_name;
	void *mmio;
	bool map_failed;
	struct clk_hw *parent, *provider;
};

struct of_phandle_args { int args_count; };
struct mock_of_declaration {
	const char *compatible;
	void (*init)(struct device_node *node);
};
#define CLK_OF_DECLARE(name, compatible, fn) \
	static const struct mock_of_declaration name = { compatible, fn }

static inline resource_size_t resource_size(const struct resource *res)
{
	return res->end - res->start + 1;
}

static inline u64 div_u64(u64 value, u32 divisor)
{
	return value / divisor;
}

u32 readl(const void *address);
void writel(u32 value, void *address);
void iounmap(void *address);
void *kzalloc(size_t bytes, unsigned int flags);
void kfree(const void *allocation);
void mock_pr_err(const char *format, ...);
struct clk_hw *clk_hw_get_parent(const struct clk_hw *hw);
unsigned long clk_hw_get_rate(const struct clk_hw *hw);
int clk_gate_is_enabled(struct clk_hw *hw);
extern const struct clk_ops clk_gate_ops;
int of_address_to_resource(struct device_node *node, int index, struct resource *res);
int of_property_read_u32(struct device_node *node, const char *name, u32 *value);
int of_property_count_strings(struct device_node *node, const char *name);
int of_property_match_string(struct device_node *node, const char *name, const char *value);
int of_count_phandle_with_args(struct device_node *node, const char *name, const char *cells);
void *of_iomap(struct device_node *node, int index);
int of_clk_hw_register(struct device_node *node, struct clk_hw *hw);
void clk_hw_unregister(struct clk_hw *hw);
struct clk_hw *of_clk_hw_simple_get(struct of_phandle_args *args, void *data);
int of_clk_add_hw_provider(struct device_node *node,
			  struct clk_hw *(*get)(struct of_phandle_args *, void *), void *data);

#endif
