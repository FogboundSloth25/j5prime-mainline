# Narrow Exynos7570 stage0 clocks

This is independently written **local diagnostic code**, not a complete CMU
driver or an upstream binding proposal. No clock MMIO has been tested on
the disconnected phone. The existing loader-only screen result does not
validate Linux's clock, timer or interrupt setup.

## Ownership and DT contract

The MCT driver's two inputs are deliberately different:

```text
26 MHz oscclk -------------------------------> MCT fin_pll (tick input)
       |
       +--> read-only CMU_MIF PERI bus view
                --> CMU_PERI MCT PCLK gate ---> MCT mct (register bus clock)
```

- `samsung,exynos7570-cmu-mif-stage0` exports one zero-cell clock. It derives
  the bus rate from the selected firmware configuration; it has no enable,
  disable, set-rate or set-parent writes.
- `samsung,exynos7570-cmu-peri-stage0` exports one zero-cell clock. Only
  **CMU_PERI `0x101f0000 + 0x0810`, bit 26** is managed. Upstream's ordinary
  positive-enable gate operations preserve the other bits with a locked RMW;
  neither inverted polarity nor HIWORD-mask semantics are used. Enable is
  followed by readback and returns an error if the gate remains clear.
- The oscillator is the tick source, not a fabricated second gate handle or
  an assumed PCLK rate. Provider registration and gate preparation reject
  an orphan or zero-rate parent.
- `CLK_IGNORE_UNUSED` on both clocks and `clk_ignore_unused` in the stage0
  command line preserve firmware-owned clocks. They do not implement display
  clocks or make an inaccessible/disabled bus usable.

The source lives in `kernel/drivers/clk/samsung/clk-exynos7570-stage0.c`.
Patch `0003` registers it in pinned upstream Linux and supplies the two local
bindings; the build installs the C source after applying the patch series.
Corresponding source therefore includes both `patches/` and `kernel/`.

## Read-only CMU_MIF decoding

CMU_MIF is physically based at **`0x10460000`**. It also contains clocks
shared with DRAM: stage0 must not configure its PLLs or bus topology.

| Offset(s) | Inherited state checked |
| --- | --- |
| `0x0258`, `0x0658` | PERI mux select `[12]`, enable `[21]`, matching one-hot status `[13:12]` |
| `0x0454` | Divider `[3:0] + 1`; update/busy `[25]` must be clear |
| `0x0860` | PERI bus output gate `[0]` must already be enabled |
| `0x0200/0x0204`, `0x0600/0x0604` | Selected SHARED0/1 mux: oscillator or PLL, enable and matching status |
| `0x0100/0x0120` | Selected integer PLL14170: MDIV `[25:16]`, PDIV `[13:8]`, SDIV `[2:0]`, enable `[31]`, lock `[29]` |
| `0x0104/0x0124` | Selected PLL CON1: internal bypass `[22]` is rejected when the PLL is used |

The PERI mux selects SHARED0_DIV2 or SHARED1_DIV2. The selected shared mux
may instead feed its divide-by-two factor from the oscillator; an unused
PLL need not be enabled in that case. With a selected PLL, zero divisors,
missing enable/lock or internal bypass fail rather than yield a guessed
frequency. All arithmetic uses a widened 64-bit product.

Two complete ordered snapshots must match. A mismatch returns `-EAGAIN`;
a disabled or unsupported path returns an error/zero rate. No polling loop
reconfigures or repairs CMU_MIF. Matching snapshots are a conservative check,
not proof that firmware cannot change a clock later. They cannot prevent a
bus fault if stock firmware denies MMIO access; that remains a device-test
question, not a host-test result.

## Research provenance

Only register facts and topology were used from the public downstream tree
at commit `fdcdfbb4b6964fae7ea7737f948fd8409d3e0350`:

- [Physical CMU mapping](https://github.com/awab228/android_kernel_samsung_on5xelte/blob/fdcdfbb4b6964fae7ea7737f948fd8409d3e0350/drivers/soc/samsung/pwrcal/S5E7570/S5E7570-sfrbase.h)
- [Register offsets](https://github.com/awab228/android_kernel_samsung_on5xelte/blob/fdcdfbb4b6964fae7ea7737f948fd8409d3e0350/drivers/soc/samsung/pwrcal/S5E7570/S5E7570-cmusfr.h)
- [Parent choices, divider widths and gate positions](https://github.com/awab228/android_kernel_samsung_on5xelte/blob/fdcdfbb4b6964fae7ea7737f948fd8409d3e0350/drivers/soc/samsung/pwrcal/S5E7570/S5E7570-cmu.c)
- [Mux status and positive-enable gate semantics](https://github.com/awab228/android_kernel_samsung_on5xelte/blob/fdcdfbb4b6964fae7ea7737f948fd8409d3e0350/drivers/soc/samsung/pwrcal/pwrcal-cmu.c)
- [PLL14170 coefficient and bypass fields](https://github.com/awab228/android_kernel_samsung_on5xelte/blob/fdcdfbb4b6964fae7ea7737f948fd8409d3e0350/drivers/soc/samsung/pwrcal/S5E7570/S5E7570-pll.c)

The CCF API and gate operations come from official upstream Linux at the
commit in `sources.lock.json`. No downstream CMU implementation, rate-setting
table or Android clock framework is installed into upstream Linux.

## Host and CI checks

- The actual local C driver is compiled with `-Werror` and ASan/UBSan against
  explicit test-only CCF/MMIO stand-ins. Tests sweep PLL/divider fields,
  selected parents, all snapshot mismatches, resource/orphan failures,
  registration cleanup, gate readback and preservation of unrelated bits.
- DT tests compile this repository's DTS and mutate its clock phandles,
  ordering, names, register spans and provider availability. Legal phandle
  renumbering is accepted; cycles, inert oscillator gate handles, extra
  specifier cells and guessed fixed bus frequencies are rejected.
- CI resolves the real pinned Kconfig and requires both Samsung CCF and
  `CONFIG_EXYNOS_7570_STAGE0_CLK=y`. It validates the local binding and the
  actual DTB before the expensive kernel compilation.

Host stand-ins are **not** the real Linux CCF or physical MMIO. Passing tests
does not prove that the MCT counts, interrupts arrive, the display survives,
or `/init` runs. A reviewed complete uniLoader bundle and separate approval
of an exact image/partition are still required before any phone write.
