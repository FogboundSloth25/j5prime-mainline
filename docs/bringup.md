# Native stage0 contracts and limits

## What success in CI proves

The pinned upstream kernel plus local patches compiled, selected Kconfig survived
resolution, synthetic stand-ins passed, and actual Image/ELF/archive/DTB/BOOT
components passed the documented checks. It does **not** prove S-Boot acceptance,
loading addresses, firmware FDT selection, Linux entry registers, MMIO access,
interrupts, visible output or `/init` execution. There is no phone interaction
in this workflow. Native hardware status is still **unconfirmed**.

This is an isolated native branch, not a rewrite of the saved loader baseline.
All source edits, project tests and compilation occur in GitHub. Historical
failed attempts, working old smoke, modified loader sources, original backups
and TWRP remain separate and preserved.

## Requested native loading/packaging contract

| Item | Requested/checked contract, not a hardware measurement |
| --- | --- |
| Linux RAM | `0x40000000–0x60000000`, plus forced `mem=512M` |
| ARM64 Image | Non-EFI, flags `0xa`, text offset zero, entry request `0x40000000` |
| Complete Image memory | `_end - _text` including BSS; end at/before `0x42000000` |
| Diagnostic userspace | Checked gzip/newc **built into the Image** |
| Compatibility ramdisk | Exact deterministic trailer-only gzip, request `0x42000000` |
| FDT request area | `0x47f00000`, retained `0x47000000–0x49000000` reservation |
| DTBH | v2, one entry `(7570, 20646, 561349850, 4, 4)`, offset 2048, entry-size field **32** |
| BOOT wrapper | 2048-byte pages, compact 4096-byte write alignment, BOOT limit 32 MiB |

The generated board label is `on5xelte-stage0`; no private firmware template is
used. The legacy component SHA-1 ID, zero padding and `SEANDROIDENFORCE` footer
are format checks, **not Samsung signature authentication**. Firmware may ignore
or reject address/header/DTBH requests. In particular, **old-smoke placement at
`0x40080000` is not proof of the new `0x40000000` native Image contract**.

The built-in Kconfig source is `on5xelte-stage0-initramfs.cpio.gz`, installed in
the kernel `O=` output directory. Pinned upstream `usr/Makefile` copies a single
`.cpio.gz` source instead of recompressing it. CI resolves `__initramfs_start` and
`__initramfs_size` with the cross-toolchain and checks the exact compressed bytes
in both Image and file-backed vmlinux segments, plus the checked static `/init`
and native handoff symbols. An external archive alone is not linkage evidence.

## Read-only native scanout inheritance

The DT's `/chosen/framebuffer@67000000` is a **permanently disabled placeholder**.
Native stage0 must not enable it, assume its address or accept firmware-enabled
DT simplefb. The native configuration also rejects all DT-provided simplefb
surfaces and refuses upstream simplefb's normally nonfatal resource-collision
fallback.

Before ordinary allocator startup, the architecture hook:

1. Bounds/checks the v17 FDT blocks, reservation terminator and token grammar,
   and confirms board/root cell format **before** accessing hard-coded DECON MMIO.
   Pinned Linux declares but does not link `fdt_check_full`; the small independent
   bounded layout gate uses available read-only libfdt helpers instead.
2. Reads the complete supported window-0 metadata twice; rejects drift, disabled
   window/solid fill, cropping, offsets, unknown formats or extra geometry bits.
   Supported modes are 0/1/4/5/8 at exactly 720×1280, backing height 1280–4096.
3. Computes **all backing rows**, visible bytes and the complete page-rounding
   envelope. The backing must be page-aligned and wholly within the conservative
   `0x60000000–0x710ef000` gap. All first-512-MiB Linux RAM, the high firmware zone
   beginning at `0x710ef000`, and the entire second DRAM bank are excluded.
4. Checks actual kernel `_text/_end`, RAM regions, memblock reservations, every
   FDT reserve-map entry and static `/reserved-memory` span, FDT placement and any
   initrd range. Overflow, malformed/dynamic reservations and unexpected DT
   surfaces fail closed. The kernel must actually be placed at `0x40000000`.
5. Reserves the full page envelope and takes another stable-state check. A
   reservation already made before detected drift stays safely reserved.

A late synchronous device initcall rechecks stable DECON and FDT state before
publishing only the checked visible surface as simplefb **platform data**. The
ordinary simplefb driver is already registered, reducing the unmeasured binding
gap. There are no DT clocks/regulators/pinctrl/power-domain dependencies on this
platform device. No DECON, DSIM or display clock writes are made by the handoff.

The reserve/handoff log markers mean only their named gates were reached; platform
registration is not proof of driver probe or panel output. Hardware may change
state later. Host MMIO/memblock/platform stand-ins do not test MMU/cache/traps,
firmware ownership or a physical panel. A rejected handoff can leave Linux
running without visible console; blank output does not diagnose the failure.

## Clocks and mandatory power-supply class

The [narrow clock providers](clock-stage0.md) manage the MCT PCLK gate but only
read/check its CMU_MIF parent. A disabled/unstable parent is a diagnostic refusal,
not permission to change shared PLLs, dividers or DRAM clocks. MCT supplies
clockevents while the architected counter remains the 26 MHz clocksource; actual
interrupt operation has not been demonstrated.

Pinned ARM64 Kconfig selects `POWER_SUPPLY` even under allnoconfig. Stage0 requests
that mandatory class without weakening the architecture. CI inventories every
other symbol in pinned `drivers/power/supply/Kconfig` and requires it disabled,
including optional drivers, test/debug/hwmon helpers. Unexpected Kconfig includes
fail closed. Regulators, I2C, USB, thermal and power management stay disabled.
This is **not battery monitoring, charging or thermal protection**.

## Diagnostic userspace

The built-in archive contains only static `/init`, virtual filesystem mountpoints
and console/kmsg nodes. `/init` refuses execution unless PID 1. It mounts only
proc/sysfs/devtmpfs, prints `MAINLINE_STAGE0_USERSPACE_OK` and emits a heartbeat
every five seconds. It never starts a shell, mounts storage, brings up USB/network,
changes regulators or invokes reboot/poweroff. A marker and advancing heartbeats
would be Linux userspace/timer evidence, not a complete phone port.

## Private preflight before a separately selected hardware experiment

- Retain successful **exact commit/run/attempt** and privately decrypt the single
  recipient-encrypted artifact. Verify ciphertext receipt, internal manifest,
  component/source `SHA256SUMS`, pinned upstream input and resolved configuration.
- Inspect actual Image memory/header, linked archive and diagnostic `/init`,
  generated DTBH/rev04 selection and BOOT header/ID/footer with an independent
  private format preflight. No local source compilation or project test is needed.
- Keep firmware acceptance/actual placement unknown until measured on the phone;
  CI validation cannot turn loading requests into measured entry state.
- Select and approve an exact short **BOOT-only** test separately, with fresh
  target/size/hash and rooted-TWRP checks and verified original rollback backups.
  No successful build/download authorizes a transfer, mount, flush, write or reboot.
- **Never change S-Boot/BL or RECOVERY.** Keep known-working TWRP intact and return
  promptly to it. Stock CTJ8 reference firmware is not the original rollback.

There is no storage, USB/ADB, charging, battery gauge or thermal policy here.
Never leave a future diagnostic boot unattended or treat it as a daily system.
The repository intentionally has no phone flashing command or private device
input. New image/log/source bundles are encrypted before upload even on failure;
a missing/mismatched recipient certificate has no plaintext fallback.
