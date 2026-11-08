// SPDX-License-Identifier: GPL-2.0-only
/*
 * Independently written, local Exynos7570 stage0 clock support.
 *
 * Only the MCT PCLK gate is writable. The PERI bus path in CMU_MIF is read
 * twice and checked, never configured: changing its shared PLLs could also
 * change DRAM clocks. This is not a complete Exynos7570 CMU driver.
 */

#include <linux/clk-provider.h>
#include <linux/err.h>
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/math64.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/string.h>

#define EXYNOS7570_CMU_MIF_BASE		0x10460000
#define EXYNOS7570_CMU_PERI_BASE		0x101f0000
#define EXYNOS7570_STAGE0_CMU_SIZE	0x1000
#define EXYNOS7570_OSC_RATE		26000000

#define MIF_SHARED_PLL_CON(n)		(0x0100 + (n) * 0x20)
#define MIF_SHARED_PLL_CON1(n)		(0x0104 + (n) * 0x20)
#define MIF_SHARED_MUX_CON(n)		(0x0200 + (n) * 4)
#define MIF_SHARED_MUX_STAT(n)		(0x0600 + (n) * 4)
#define MIF_PERI_MUX_CON			0x0258
#define MIF_PERI_MUX_STAT			0x0658
#define MIF_PERI_DIV_CON			0x0454
#define MIF_PERI_GATE			0x0860
#define PERI_BUS0_GATE			0x0810
#define PERI_MCT_GATE_BIT		26

struct exynos7570_bus_snapshot {
	u32 mux, status, divider, gate;
	u32 shared_mux, shared_status, pll, pll_aux;
};

struct exynos7570_bus_clock {
	struct clk_hw hw;
	void __iomem *base;
};

static unsigned long exynos7570_bus_decode(const struct exynos7570_bus_snapshot *s,
					   unsigned long parent_rate)
{
	unsigned int selected = (s->mux >> 12) & 1;
	unsigned int shared = (s->shared_mux >> 12) & 1;
	unsigned int mdiv, pdiv, sdiv;
	u64 rate = parent_rate;

	/*
	 * Both muxes must be enabled and their one-hot status must agree with
	 * the select fields. Reject a divider update or disabled output gate.
	 */
	if (parent_rate != EXYNOS7570_OSC_RATE || !(s->gate & BIT(0)) ||
	    !(s->mux & BIT(21)) || !(s->shared_mux & BIT(21)) ||
	    ((s->status >> 12) & 3) != BIT(selected) ||
	    ((s->shared_status >> 12) & 3) != BIT(shared) ||
	    (s->divider & BIT(25)))
		return 0;

	if (shared) {
		/*
		 * SHARED0/1 use integer PLL14170: MDIV[25:16], PDIV[13:8],
		 * SDIV[2:0]; enable[31] and lock[29] must both be set.
		 * Reject internal bypass in CON1[22]: its output must not be
		 * inferred from the normal PLL coefficients. Never change CON1.
		 */
		if ((s->pll & (BIT(31) | BIT(29))) != (BIT(31) | BIT(29)) ||
		    (s->pll_aux & BIT(22)))
			return 0;
		mdiv = (s->pll >> 16) & 0x3ff;
		pdiv = (s->pll >> 8) & 0x3f;
		sdiv = s->pll & 7;
		if (!mdiv || !pdiv)
			return 0;
		rate = div_u64(rate * mdiv, pdiv << sdiv);
	}

	/*
	 * The bus mux selects SHARED0_DIV2 or SHARED1_DIV2, then a /1..16
	 * divider. Do not invent a fixed PCLK rate or use the oscillator twice.
	 */
	return div_u64(rate, 2 * ((s->divider & 0xf) + 1));
}

static void exynos7570_bus_snapshot(struct exynos7570_bus_clock *bus,
				    struct exynos7570_bus_snapshot *s)
{
	unsigned int selected;

	s->mux = readl(bus->base + MIF_PERI_MUX_CON);
	s->status = readl(bus->base + MIF_PERI_MUX_STAT);
	s->divider = readl(bus->base + MIF_PERI_DIV_CON);
	s->gate = readl(bus->base + MIF_PERI_GATE);
	selected = (s->mux >> 12) & 1;
	s->shared_mux = readl(bus->base + MIF_SHARED_MUX_CON(selected));
	s->shared_status = readl(bus->base + MIF_SHARED_MUX_STAT(selected));
	s->pll = readl(bus->base + MIF_SHARED_PLL_CON(selected));
	s->pll_aux = readl(bus->base + MIF_SHARED_PLL_CON1(selected));
}

static int exynos7570_bus_rate(struct exynos7570_bus_clock *bus,
			       unsigned long parent_rate, unsigned long *rate)
{
	struct exynos7570_bus_snapshot first, second;
	unsigned long decoded;

	exynos7570_bus_snapshot(bus, &first);
	exynos7570_bus_snapshot(bus, &second);
	if (memcmp(&first, &second, sizeof(first)))
		return -EAGAIN;
	decoded = exynos7570_bus_decode(&first, parent_rate);
	if (!decoded)
		return -EINVAL;
	*rate = decoded;
	return 0;
}

static unsigned long exynos7570_bus_recalc(struct clk_hw *hw,
					   unsigned long parent_rate)
{
	struct exynos7570_bus_clock *bus =
		container_of(hw, struct exynos7570_bus_clock, hw);
	unsigned long rate;

	return exynos7570_bus_rate(bus, parent_rate, &rate) ? 0 : rate;
}

static int exynos7570_bus_prepare(struct clk_hw *hw)
{
	struct exynos7570_bus_clock *bus =
		container_of(hw, struct exynos7570_bus_clock, hw);
	struct clk_hw *parent = clk_hw_get_parent(hw);
	unsigned long rate;
	int ret;

	if (!parent)
		return -EINVAL;
	ret = exynos7570_bus_rate(bus, clk_hw_get_rate(parent), &rate);
	if (ret)
		pr_err("exynos7570-stage0: inherited PERI bus is not stable/enabled (%d); no MIF writes\n",
		       ret);
	return ret;
}

static const struct clk_ops exynos7570_bus_ops = {
	.prepare = exynos7570_bus_prepare,
	.recalc_rate = exynos7570_bus_recalc,
};

static int exynos7570_mct_prepare(struct clk_hw *hw)
{
	struct clk_hw *parent = clk_hw_get_parent(hw);

	/* An orphan gate would otherwise be enabled without checking CMU_MIF. */
	return parent && clk_hw_get_rate(parent) ? 0 : -EINVAL;
}

static int exynos7570_mct_enable(struct clk_hw *hw)
{
	int ret = clk_gate_ops.enable(hw);

	if (ret)
		return ret;
	/*
	 * Flush the write and fail before the MCT driver touches timer registers
	 * if firmware did not permit the gate bit to be set.
	 */
	return clk_gate_is_enabled(hw) ? 0 : -EIO;
}

static void exynos7570_mct_disable(struct clk_hw *hw)
{
	clk_gate_ops.disable(hw);
}

static const struct clk_ops exynos7570_mct_ops = {
	.prepare = exynos7570_mct_prepare,
	.enable = exynos7570_mct_enable,
	.disable = exynos7570_mct_disable,
	.is_enabled = clk_gate_is_enabled,
};

static DEFINE_SPINLOCK(exynos7570_peri_lock);

static void __iomem *__init exynos7570_stage0_map(struct device_node *np,
					       resource_size_t address,
					       const char *parent_name)
{
	struct resource res;
	u32 cells;

	if (of_address_to_resource(np, 0, &res) || res.start != address ||
	    resource_size(&res) != EXYNOS7570_STAGE0_CMU_SIZE ||
	    of_property_read_u32(np, "#clock-cells", &cells) || cells != 0 ||
	    of_property_count_strings(np, "clock-names") != 1 ||
	    of_property_match_string(np, "clock-names", parent_name) != 0 ||
	    of_count_phandle_with_args(np, "clocks", "#clock-cells") != 1)
		return ERR_PTR(-EINVAL);

	return of_iomap(np, 0) ?: ERR_PTR(-ENOMEM);
}

static void __init exynos7570_stage0_mif_init(struct device_node *np)
{
	const struct clk_parent_data parent = { .fw_name = "oscclk" };
	const struct clk_init_data init = {
		.name = "exynos7570-stage0-peri-bus",
		.ops = &exynos7570_bus_ops,
		.flags = CLK_IGNORE_UNUSED | CLK_GET_RATE_NOCACHE,
		.parent_data = &parent,
		.num_parents = 1,
	};
	struct exynos7570_bus_clock *bus;
	int ret;

	bus = kzalloc_obj(*bus);
	if (!bus) {
		ret = -ENOMEM;
		goto free_bus;
	}
	bus->base = exynos7570_stage0_map(np, EXYNOS7570_CMU_MIF_BASE, "oscclk");
	if (IS_ERR(bus->base)) {
		ret = PTR_ERR(bus->base);
		goto free_bus;
	}
	bus->hw.init = &init;
	ret = of_clk_hw_register(np, &bus->hw);
	if (ret)
		goto unmap;
	if (!clk_hw_get_parent(&bus->hw) || !clk_hw_get_rate(&bus->hw)) {
		ret = -EINVAL;
		goto unregister;
	}
	ret = of_clk_add_hw_provider(np, of_clk_hw_simple_get, &bus->hw);
	if (!ret)
		return;

unregister:
	clk_hw_unregister(&bus->hw);
unmap:
	iounmap(bus->base);
free_bus:
	pr_err("exynos7570-stage0: no validated PERI bus provider for %pOF (%d)\n",
	       np, ret);
	kfree(bus);
}

CLK_OF_DECLARE(exynos7570_stage0_mif, "samsung,exynos7570-cmu-mif-stage0",
	       exynos7570_stage0_mif_init);

static void __init exynos7570_stage0_peri_init(struct device_node *np)
{
	const struct clk_parent_data parent = { .fw_name = "peri-bus" };
	const struct clk_init_data init = {
		.name = "exynos7570-stage0-mct-pclk",
		.ops = &exynos7570_mct_ops,
		.flags = CLK_IGNORE_UNUSED | CLK_GET_RATE_NOCACHE,
		.parent_data = &parent,
		.num_parents = 1,
	};
	struct clk_gate *gate;
	void __iomem *base;
	int ret;

	base = exynos7570_stage0_map(np, EXYNOS7570_CMU_PERI_BASE, "peri-bus");
	if (IS_ERR(base)) {
		ret = PTR_ERR(base);
		goto error;
	}
	gate = kzalloc_obj(*gate);
	if (!gate) {
		ret = -ENOMEM;
		goto unmap;
	}
	gate->reg = base + PERI_BUS0_GATE;
	gate->bit_idx = PERI_MCT_GATE_BIT;
	/* Positive-enable, ordinary RMW: neither SET_TO_DISABLE nor HIWORD. */
	gate->flags = 0;
	gate->lock = &exynos7570_peri_lock;
	gate->hw.init = &init;
	ret = of_clk_hw_register(np, &gate->hw);
	if (ret)
		goto free_gate;
	if (!clk_hw_get_parent(&gate->hw) ||
	    !clk_hw_get_rate(clk_hw_get_parent(&gate->hw))) {
		ret = -EINVAL;
		goto unregister;
	}
	ret = of_clk_add_hw_provider(np, of_clk_hw_simple_get, &gate->hw);
	if (!ret)
		return;

unregister:
	clk_hw_unregister(&gate->hw);
free_gate:
	kfree(gate);
unmap:
	iounmap(base);
error:
	pr_err("exynos7570-stage0: no MCT PCLK provider for %pOF (%d)\n", np, ret);
}

CLK_OF_DECLARE(exynos7570_stage0_peri, "samsung,exynos7570-cmu-peri-stage0",
	       exynos7570_stage0_peri_init);
