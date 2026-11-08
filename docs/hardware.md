# Hardware reference and provenance

These are selected, non-unique hardware facts, not a published device dump.
No partition backup, complete boot command line or raw recovery log is used
as a build input or committed here.

## Reference

The phone-local, working TWRP DTBH entry for **SM-G570F / SWA rev04** has
SHA-256 `c12a90c3c99a9d73732299c74436f0e5954c98f34f28ce7a0c0a866f942317a9`.
It was inspected alongside the recovery memory map and early timer messages.
The blob itself remains in the private host backup directory.

Public downstream references, **for hardware research only**:

- [Exynos7570 on5xe board description](https://github.com/awab228/android_kernel_samsung_on5xelte/blob/master/arch/arm64/boot/dts/exynos7570-on5xe_common.dtsi)
- [Downstream kernel source](https://github.com/awab228/android_kernel_samsung_on5xelte)
- [Exynos7570 DECON register definitions](https://github.com/awab228/android_kernel_samsung_on5xelte/blob/master/drivers/video/fbdev/exynos/decon_7570/regs-decon.h)
- [Selected uniLoader fork](https://github.com/FogboundSloth25/uniLoader/tree/master),
  initial checkout commit `c68612d`; the phone-specific loader port is separate
  and has not been pushed there yet.

The Linux base is the official upstream **v7.3.3** stable release, not any
downstream source above. The kernel.org archive SHA-256 is pinned in
`sources.lock.json`; its tag resolves to the recorded upstream commit.
The pin is a checksum of the downloaded archive, not a claim that we have
independently verified a maintainer's PGP signature.

## CPU, interrupts and timers

| Component | Reference value |
| --- | --- |
| CPU | Four Cortex-A53 cores, MPIDR affinity IDs 0–3 |
| PSCI compatible / conduit | `arm,psci` / `smc` (v0.1 IDs) |
| PSCI suspend / off / on | `0xc4000001` / `0x84000002` / `0xc4000003` |
| GIC distributor | `0x104e1000`, size `0x1000` |
| GIC CPU interface | `0x104e2000`, size `0x1000` |
| GIC virtualization registers | `0x104e4000` and `0x104e6000`, each `0x2000` |
| Oscillator / architected counter | 26,000,000 Hz |
| MCT | `0x101b0000`, size `0x800`, downstream Exynos4210-compatible layout |
| MCT interrupt map | Global GIC SPIs 102–105, followed by local SPIs 106–113 |
| UART2 | `0x13820000`, size `0x100`, GIC SPI 423, FIFO 256 bytes |

The reference CPU is Cortex-A53 r0p4. Both the kernel and diagnostic `/init`
retain the linker's Cortex-A53 erratum 843419 workaround. AArch32 userspace
is disabled, so the compat-only erratum 845719 path is not needed in stage0.

The working recovery explicitly reports the architected timer as
**clocksource-only**. Its MCT node is marked clockevent-only. Unmodified
upstream ignores those downstream-only properties, and the architected
clockevent normally outranks MCT on ARM64. Merely copying the old timer DT
would therefore not reproduce that working setup.

The new local timer-compatible quirk retains the architected counter,
per-CPU counter user-access setup and frequency calibration, but does not
register architected timer clockevents. Upstream MCT supplies clockevents.
This is a conservative initial policy, **not proof of a hardware timer
defect**. PPIs are described using the architected level-low convention;
their operation is deliberately not relied on. UART2 is recorded but not
enabled: its clock tree, pinmux and external connection are not validated.

## Memory and screen

Actual downstream DRAM banks (end addresses exclusive):

```text
0x40000000–0x7e400000
0x80000000–0xc0000000
```

Stage0 exposes **only `0x40000000–0x60000000`**. This is a diagnostic limit,
not a claim that the phone has only 512 MiB of RAM. The modem, video, GNSS,
Wi-Fi and high firmware areas are outside that exposed bank.

Known low firmware-log pointers span the region near `0x46000000` and
`0x46a00000`, plus the TIMA log at `0x52800000` (2 MiB). Stage0 conservatively
reserves `0x46000000–0x47000000` and `0x52800000–0x52a00000`. The separate
`0x47000000–0x49000000` reservation protects the current uniLoader relocation
area and its DTB; its size must be checked when integrating a loader bundle.

The initial planned addresses are inherited from the current loader config,
not validated kernel boot addresses:

```text
uniLoader relocation: 0x47000000 (reserved 32 MiB)
external initramfs:   0x42000000 (check size and DT initrd range)
mainline Image:       0x50000000 (span must end at or before 0x52800000)
```

The 720×1280 panel remains initialized by S-Boot. The working recovery has
32-bit pixels, line stride 2880 bytes and virtual size 720×2560. The observed
S-Boot framebuffer argument is `0x67000000`, but it is **not treated as a
guaranteed runtime address or format**. uniLoader reads live DECON window-0
registers and rejects unsupported or unsafe layouts before writing.

The mainline simplefb placeholder stays disabled. A later runtime handoff
must replace its address, visible size, stride and format, reserve the live
buffer (including any scanout backing beyond the visible surface), update
its node/alias, and only then enable it. No DECON/DSIM reset or guessed
Exynos7570 clock-controller register write is part of this stage.
