// SPDX-License-Identifier: GPL-2.0-only
/* Exercise the actual local driver with synthetic register/API stand-ins. */
#include "../../kernel/drivers/clk/samsung/clk-exynos7570-stage0.c"

static unsigned int checks;
#define CHECK(expression) do { \
	checks++; \
	if (!(expression)) { \
		fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #expression); \
		exit(1); \
	} \
} while (0)

static u32 mif_regs[0x1000 / 4], peri_regs[0x1000 / 4];
static struct {
	unsigned int allocations, registered, mappings, logs;
	unsigned int iomap_calls, reads, writes, flip_read;
	unsigned int offsets[64];
	bool fail_allocation, block_write;
	int register_error, provider_error, gate_error;
} mock;

u32 readl(const void *address)
{
	uintptr_t ptr = (uintptr_t)address;
	uintptr_t base = (uintptr_t)mif_regs;
	bool mif = ptr >= base && ptr < base + sizeof(mif_regs);
	u32 value;

	if (!mif) {
		base = (uintptr_t)peri_regs;
		CHECK(ptr >= base && ptr < base + sizeof(peri_regs));
	}
	CHECK(!((ptr - base) & 3));
	if (mock.reads < 64)
		mock.offsets[mock.reads] = (unsigned int)(ptr - base);
	value = *(const u32 *)address;
	mock.reads++;
	if (mock.flip_read == mock.reads)
		value ^= BIT(28);
	return value;
}

void writel(u32 value, void *address)
{
	CHECK(address == &peri_regs[0x0810 / 4]);
	CHECK(!((value ^ *(u32 *)address) & ~(u32)BIT(26)));
	mock.writes++;
	if (!mock.block_write)
		*(u32 *)address = value;
}

void *kzalloc(size_t bytes, unsigned int flags)
{
	void *allocation;

	CHECK(flags == GFP_KERNEL);
	if (mock.fail_allocation)
		return NULL;
	allocation = calloc(1, bytes);
	CHECK(allocation != NULL);
	mock.allocations++;
	return allocation;
}

void kfree(const void *allocation)
{
	if (!allocation)
		return;
	CHECK(mock.allocations > 0);
	mock.allocations--;
	free((void *)allocation);
}

void iounmap(void *address)
{
	CHECK(address == mif_regs || address == peri_regs);
	CHECK(mock.mappings > 0);
	mock.mappings--;
}

void mock_pr_err(const char *format, ...)
{
	CHECK(strstr(format, "exynos7570-stage0") != NULL);
	mock.logs++;
}

struct clk_hw *clk_hw_get_parent(const struct clk_hw *hw)
{
	return hw->parent;
}

unsigned long clk_hw_get_rate(const struct clk_hw *hw)
{
	unsigned long parent_rate = hw->parent ? clk_hw_get_rate(hw->parent) : 0;

	if (hw->ops && hw->ops->recalc_rate)
		return hw->ops->recalc_rate((struct clk_hw *)hw, parent_rate);
	return hw->parent ? parent_rate : hw->rate;
}

static int mock_gate_enable(struct clk_hw *hw)
{
	struct clk_gate *gate = container_of(hw, struct clk_gate, hw);

	if (mock.gate_error)
		return mock.gate_error;
	CHECK(gate->flags == 0 && gate->bit_idx == 26 && gate->lock != NULL);
	writel(readl(gate->reg) | BIT(gate->bit_idx), gate->reg);
	return 0;
}

static void mock_gate_disable(struct clk_hw *hw)
{
	struct clk_gate *gate = container_of(hw, struct clk_gate, hw);

	CHECK(gate->flags == 0 && gate->bit_idx == 26 && gate->lock != NULL);
	writel(readl(gate->reg) & ~(u32)BIT(gate->bit_idx), gate->reg);
}

int clk_gate_is_enabled(struct clk_hw *hw)
{
	struct clk_gate *gate = container_of(hw, struct clk_gate, hw);

	CHECK(gate->flags == 0);
	return !!(readl(gate->reg) & BIT(gate->bit_idx));
}

const struct clk_ops clk_gate_ops = {
	.enable = mock_gate_enable,
	.disable = mock_gate_disable,
	.is_enabled = clk_gate_is_enabled,
};

int of_address_to_resource(struct device_node *node, int index, struct resource *res)
{
	CHECK(index == 0);
	*res = node->resource;
	return node->resource_error;
}

int of_property_read_u32(struct device_node *node, const char *name, u32 *value)
{
	CHECK(!strcmp(name, "#clock-cells"));
	*value = node->cells;
	return node->cells_error;
}

int of_property_count_strings(struct device_node *node, const char *name)
{
	CHECK(!strcmp(name, "clock-names"));
	return node->name_count;
}

int of_property_match_string(struct device_node *node, const char *name, const char *value)
{
	CHECK(!strcmp(name, "clock-names"));
	return !strcmp(node->clock_name, value) ? 0 : -EINVAL;
}

int of_count_phandle_with_args(struct device_node *node, const char *name, const char *cells)
{
	CHECK(!strcmp(name, "clocks") && !strcmp(cells, "#clock-cells"));
	return node->phandle_count;
}

void *of_iomap(struct device_node *node, int index)
{
	CHECK(index == 0);
	mock.iomap_calls++;
	if (node->map_failed)
		return NULL;
	mock.mappings++;
	return node->mmio;
}

int of_clk_hw_register(struct device_node *node, struct clk_hw *hw)
{
	if (mock.register_error)
		return mock.register_error;
	CHECK(hw->init && hw->init->num_parents == 1 && hw->init->parent_data);
	CHECK(hw->init->flags == (CLK_IGNORE_UNUSED | CLK_GET_RATE_NOCACHE));
	CHECK(!strcmp(hw->init->parent_data->fw_name, node->clock_name));
	hw->saved_init = *hw->init;
	hw->saved_parent = *hw->init->parent_data;
	hw->saved_init.parent_data = &hw->saved_parent;
	hw->ops = hw->init->ops;
	hw->parent = node->parent;
	hw->init = NULL;
	hw->registered = true;
	mock.registered++;
	return 0;
}

void clk_hw_unregister(struct clk_hw *hw)
{
	CHECK(hw->registered && mock.registered > 0);
	hw->registered = false;
	mock.registered--;
}

struct clk_hw *of_clk_hw_simple_get(struct of_phandle_args *args, void *data)
{
	(void)args;
	return data;
}

int of_clk_add_hw_provider(struct device_node *node,
			  struct clk_hw *(*get)(struct of_phandle_args *, void *), void *data)
{
	CHECK(get == of_clk_hw_simple_get && ((struct clk_hw *)data)->registered);
	if (mock.provider_error)
		return mock.provider_error;
	CHECK(!node->provider);
	node->provider = data;
	return 0;
}

static void reset(void)
{
	CHECK(!mock.allocations && !mock.registered && !mock.mappings);
	memset(&mock, 0, sizeof(mock));
	memset(mif_regs, 0, sizeof(mif_regs));
	memset(peri_regs, 0, sizeof(peri_regs));
}

static struct device_node node_for(bool peri, struct clk_hw *parent)
{
	resource_size_t base = peri ? 0x101f0000 : 0x10460000;
	struct device_node node = {
		.resource = { base, base + 0xfff },
		.phandle_count = 1, .name_count = 1,
		.clock_name = peri ? "peri-bus" : "oscclk",
		.mmio = peri ? peri_regs : mif_regs, .parent = parent,
	};

	return node;
}

static struct exynos7570_bus_snapshot snapshot(unsigned int selected,
		unsigned int shared, unsigned int m, unsigned int p,
		unsigned int s, unsigned int divider)
{
	struct exynos7570_bus_snapshot value = {
		.mux = BIT(21) | (selected << 12),
		.status = (1U << selected) << 12, .divider = divider, .gate = 1,
		.shared_mux = BIT(21) | (shared << 12),
		.shared_status = (1U << shared) << 12,
		.pll = BIT(31) | BIT(29) | (m << 16) | (p << 8) | s,
	};

	return value;
}

static void registers_for(unsigned int selected)
{
	mif_regs[0x0258 / 4] = BIT(21) | (selected << 12);
	mif_regs[0x0658 / 4] = (1U << selected) << 12;
	mif_regs[0x0454 / 4] = 3;
	mif_regs[0x0860 / 4] = 1;
	mif_regs[(0x0200 + selected * 4) / 4] = BIT(21) | BIT(12);
	mif_regs[(0x0600 + selected * 4) / 4] = 2U << 12;
	mif_regs[(0x0100 + selected * 0x20) / 4] = BIT(31) | BIT(29) | (100U << 16) | (2U << 8) | 1;
	mif_regs[(0x0104 + selected * 0x20) / 4] = 0;
}

static void test_decode(void)
{
	const unsigned int ms[] = { 1, 100, 511, 512, 1023 };
	const unsigned int ps[] = { 1, 2, 3, 63 };
	const unsigned long invalid_parents[] = { 0, 24000000, 26000001, ULONG_MAX };
	struct exynos7570_bus_snapshot base = snapshot(0, 1, 100, 2, 1, 3), value;
	unsigned int select, m, p, s, d, i;

	for (select = 0; select < 2; select++)
		for (m = 0; m < sizeof(ms) / sizeof(ms[0]); m++)
			for (p = 0; p < sizeof(ps) / sizeof(ps[0]); p++)
				for (s = 0; s < 8; s++)
					for (d = 0; d < 16; d++) {
						u64 expected = (26000000ULL * ms[m]) / (ps[p] << s) / 2 / (d + 1);
						value = snapshot(select, 1, ms[m], ps[p], s, d);
						CHECK(exynos7570_bus_decode(&value, 26000000) == expected);
					}
	for (select = 0; select < 2; select++)
		for (d = 0; d < 16; d++) {
			value = snapshot(select, 0, 0, 0, 0, d);
			value.pll = 0;
			value.pll_aux = BIT(22);
			CHECK(exynos7570_bus_decode(&value, 26000000) == 26000000U / 2 / (d + 1));
		}
	for (i = 0; i < sizeof(invalid_parents) / sizeof(invalid_parents[0]); i++)
		CHECK(exynos7570_bus_decode(&base, invalid_parents[i]) == 0);
	for (i = 0; i < 11; i++) {
		value = base;
		switch (i) {
		case 0: value.gate &= ~1U; break;
		case 1: value.mux &= ~(u32)BIT(21); break;
		case 2: value.shared_mux &= ~(u32)BIT(21); break;
		case 3: value.status = 2U << 12; break;
		case 4: value.shared_status = 1U << 12; break;
		case 5: value.divider |= BIT(25); break;
		case 6: value.pll &= ~(u32)BIT(31); break;
		case 7: value.pll &= ~(u32)BIT(29); break;
		case 8: value.pll &= ~(0x3ffU << 16); break;
		case 9: value.pll &= ~(0x3fU << 8); break;
		case 10: value.pll_aux |= BIT(22); break;
		}
		CHECK(exynos7570_bus_decode(&value, 26000000) == 0);
	}
	for (i = 0; i < 4; i++) {
		value = base;
		value.status = i << 12;
		CHECK((exynos7570_bus_decode(&value, 26000000) != 0) == (i == 1));
		value = base;
		value.shared_status = i << 12;
		CHECK((exynos7570_bus_decode(&value, 26000000) != 0) == (i == 2));
	}
}

static void test_snapshots(void)
{
	struct clk_hw osc = { .rate = 26000000 };
	struct exynos7570_bus_clock bus = { .base = mif_regs,
		.hw = { .parent = &osc, .ops = &exynos7570_bus_ops } };
	unsigned long rate;
	unsigned int selected, i;

	for (selected = 0; selected < 2; selected++) {
		const unsigned int offsets[] = { 0x0258, 0x0658, 0x0454, 0x0860,
			0x0200 + selected * 4, 0x0600 + selected * 4,
			0x0100 + selected * 0x20, 0x0104 + selected * 0x20 };
		reset();
		registers_for(selected);
		CHECK(exynos7570_bus_rate(&bus, 26000000, &rate) == 0);
		CHECK(rate == 81250000 && mock.reads == 16 && !mock.writes);
		for (i = 0; i < 16; i++)
			CHECK(mock.offsets[i] == offsets[i % 8]);
	}
	for (i = 0; i < 8; i++) {
		reset();
		registers_for(0);
		mock.flip_read = 9 + i;
		rate = ULONG_MAX;
		CHECK(exynos7570_bus_rate(&bus, 26000000, &rate) == -EAGAIN);
		CHECK(rate == ULONG_MAX && !mock.writes);
		mock.reads = 0;
		CHECK(exynos7570_bus_recalc(&bus.hw, 26000000) == 0);
		mock.reads = 0;
		CHECK(exynos7570_bus_prepare(&bus.hw) == -EAGAIN);
	}
	reset();
	registers_for(0);
	mif_regs[0x0860 / 4] = 0;
	CHECK(exynos7570_bus_prepare(&bus.hw) == -EINVAL);
	CHECK(!mock.writes);
	bus.hw.parent = NULL;
	mock.reads = 0;
	CHECK(exynos7570_bus_prepare(&bus.hw) == -EINVAL && !mock.reads);
}

static void test_resources(void)
{
	unsigned int peri, mutation;

	for (peri = 0; peri < 2; peri++)
		for (mutation = 0; mutation < 12; mutation++) {
			struct device_node node = node_for(peri, NULL);
			resource_size_t address = peri ? 0x101f0000 : 0x10460000;
			const char *name = peri ? "peri-bus" : "oscclk";
			void *result;

			reset();
			switch (mutation) {
			case 0: node.resource_error = -EINVAL; break;
			case 1: node.resource.start += 4; break;
			case 2: node.resource.end += 0x1000; break;
			case 3: node.cells_error = -EINVAL; break;
			case 4: node.cells = 1; break;
			case 5: node.phandle_count = 0; break;
			case 6: node.phandle_count = 2; break;
			case 7: node.name_count = 0; break;
			case 8: node.name_count = 2; break;
			case 9: node.clock_name = "unvalidated"; break;
			case 10: node.map_failed = true; break;
			default: break;
			}
			result = exynos7570_stage0_map(&node, address, name);
			if (mutation == 11) {
				CHECK(result == node.mmio && mock.mappings == 1);
				iounmap(result);
			} else {
				CHECK(IS_ERR(result));
				CHECK(PTR_ERR(result) == (mutation == 10 ? -ENOMEM : -EINVAL));
				CHECK(mock.iomap_calls == (mutation == 10 ? 1U : 0U));
			}
			CHECK(!mock.mappings && !mock.writes);
		}
}

static void test_provider_failures(void)
{
	unsigned int peri, failure;

	for (peri = 0; peri < 2; peri++)
		for (failure = 0; failure < 9; failure++) {
			struct clk_hw osc = { .rate = 26000000 };
			struct exynos7570_bus_clock bus = { .base = mif_regs,
				.hw = { .parent = &osc, .ops = &exynos7570_bus_ops } };
			struct device_node node = node_for(peri, peri ? &bus.hw : &osc);

			reset();
			registers_for(0);
			switch (failure) {
			case 0: mock.fail_allocation = true; break;
			case 1: node.resource.start += 4; break;
			case 2: node.map_failed = true; break;
			case 3: mock.register_error = -EIO; break;
			case 4: node.parent = NULL; break;
			case 5: osc.rate = 0; break;
			case 6: mock.provider_error = -EIO; break;
			case 7: mif_regs[0x0860 / 4] = 0; break;
			case 8: mock.flip_read = 9; break;
			}
			if (peri)
				exynos7570_stage0_peri_init(&node);
			else
				exynos7570_stage0_mif_init(&node);
			CHECK(!node.provider && !mock.writes);
			CHECK(!mock.allocations && !mock.registered && !mock.mappings);
			CHECK(mock.logs == 1);
		}
}

static void test_gate_and_provider_chain(void)
{
	struct clk_hw osc = { .rate = 26000000 };
	struct device_node mif = node_for(false, &osc), peri;
	struct clk_gate *gate;
	struct exynos7570_bus_clock *bus;
	u32 inherited[sizeof(mif_regs) / sizeof(mif_regs[0])], random = 0xA53;
	unsigned int i, writes, reads;

	reset();
	registers_for(1);
	exynos7570_stage0_mif.init(&mif);
	CHECK(mif.provider && mock.allocations == 1 && mock.mappings == 1);
	peri = node_for(true, mif.provider);
	exynos7570_stage0_peri.init(&peri);
	CHECK(peri.provider && mock.allocations == 2 && mock.mappings == 2);
	CHECK(!strcmp(exynos7570_stage0_mif.compatible, "samsung,exynos7570-cmu-mif-stage0"));
	CHECK(!strcmp(exynos7570_stage0_peri.compatible, "samsung,exynos7570-cmu-peri-stage0"));
	CHECK(!strcmp(mif.provider->saved_parent.fw_name, "oscclk"));
	CHECK(!strcmp(peri.provider->saved_parent.fw_name, "peri-bus"));
	CHECK(clk_hw_get_rate(peri.provider) == 81250000 && !mock.writes);
	bus = container_of(mif.provider, struct exynos7570_bus_clock, hw);
	gate = container_of(peri.provider, struct clk_gate, hw);
	CHECK(gate->reg == &peri_regs[0x0810 / 4] && gate->bit_idx == 26 && gate->flags == 0);
	CHECK(exynos7570_bus_ops.prepare(mif.provider) == 0);
	CHECK(exynos7570_mct_ops.prepare(peri.provider) == 0);
	memcpy(inherited, mif_regs, sizeof(inherited));
	for (i = 0; i < 1024; i++) {
		u32 before;

		random = random * 1664525U + 1013904223U;
		before = random;
		peri_regs[0x0810 / 4] = before;
		CHECK(exynos7570_mct_ops.enable(peri.provider) == 0);
		CHECK(peri_regs[0x0810 / 4] == (before | BIT(26)));
		CHECK(exynos7570_mct_ops.is_enabled(peri.provider) == 1);
		exynos7570_mct_ops.disable(peri.provider);
		CHECK(peri_regs[0x0810 / 4] == (before & ~(u32)BIT(26)));
		CHECK(exynos7570_mct_ops.is_enabled(peri.provider) == 0);
	}
	CHECK(!memcmp(inherited, mif_regs, sizeof(inherited)));
	peri_regs[0x0810 / 4] = 0;
	mock.block_write = true;
	CHECK(exynos7570_mct_ops.enable(peri.provider) == -EIO);
	CHECK(peri_regs[0x0810 / 4] == 0);
	mock.block_write = false;
	mock.gate_error = -EACCES;
	writes = mock.writes;
	reads = mock.reads;
	CHECK(exynos7570_mct_ops.enable(peri.provider) == -EACCES);
	CHECK(mock.writes == writes && mock.reads == reads);
	mock.gate_error = 0;
	gate->hw.parent = NULL;
	CHECK(exynos7570_mct_ops.prepare(peri.provider) == -EINVAL);
	CHECK(mock.writes == writes);
	gate->hw.parent = mif.provider;
	mif_regs[0x0860 / 4] = 0;
	CHECK(exynos7570_mct_ops.prepare(peri.provider) == -EINVAL);
	CHECK(mock.writes == writes);

	/* Successful built-in providers have kernel lifetime; release them here
	 * only so host sanitizers can verify all failure-path ownership as well.
	 */
	clk_hw_unregister(peri.provider);
	peri.provider = NULL;
	iounmap(peri_regs);
	kfree(gate);
	clk_hw_unregister(mif.provider);
	mif.provider = NULL;
	iounmap(mif_regs);
	kfree(bus);
	reset();
}

int main(void)
{
	test_decode();
	test_snapshots();
	test_resources();
	test_provider_failures();
	test_gate_and_provider_chain();
	printf("clock-stage0: %u synthetic checks passed; mocked CCF/MMIO only\n", checks);
	return 0;
}
