# Stage0 limits and boot contract

## What a build proves

CI success means that the pinned upstream kernel plus this patch series
compiled, the selected configuration survived Kconfig, and the component
artifacts passed the documented host checks. It does **not** prove that
S-Boot loads this kernel, interrupts fire, the framebuffer survives, or
`/init` runs. Hardware status must be recorded separately after an approved
device experiment.

The source is kept intentionally small: it is not a copied Android DTB,
not a complete clock tree, and not a claim of upstream-ready bindings.
The timer quirk and all new compatible strings are local and unsubmitted.

## Mandatory power-supply class, not charging support

Pinned upstream [`arch/arm64/Kconfig`](https://github.com/gregkh/linux/blob/d50d994bffa3c7c6db2d8bc4c5f0c55e3f46b0cd/arch/arm64/Kconfig)
unconditionally selects `POWER_SUPPLY` (and `POWER_RESET`). Even
`allnoconfig` therefore has the power-supply class built in; requesting
`CONFIG_POWER_SUPPLY=n` cannot survive Kconfig resolution. Stage0 explicitly
requests `CONFIG_POWER_SUPPLY=y` rather than patching the architecture or
ignoring a configuration mismatch.

The class initialization only registers the generic class. Battery/charger
support requires separate drivers. CI inventories **every other symbol** in
the pinned upstream `drivers/power/supply/Kconfig` and requires it to be
disabled, including test devices and optional debug/hwmon helpers. Unexpected
Kconfig includes fail validation rather than hiding a partial inventory.
The strict fragment check remains in place, and regulators, I2C, USB, thermal
and power management are still disabled. This does not provide battery
monitoring, charging or a safe long-running phone system.

## Required before a Linux device test

1. Retrieve a successful CI run's artifacts; verify `SHA256SUMS` and retain
   its repository SHA, source lock, resolved configuration and build report.
2. Inspect the actual ARM64 Image header. The planned `0x50000000` entry
   must meet its alignment/text-offset requirements, and its required memory
   must not overlap the TIMA reservation at `0x52800000`.
3. Integrate with the selected uniLoader fork and its on5xelte port. Check
   the relocated loader's complete memory span, initramfs bounds and DTB
   placement. The previously tested loader-only fixed-address path is not
   equivalent to this relocation/embedded-kernel path.
4. Review and cross-build the host-tested **runtime simplefb handoff** in
   the local uniLoader port. Do not just set the
   placeholder to `okay` or assume `0x67000000` from a command line. Reserve
   the actual scanout backing and reject overlap with Image, initrd, loader
   and known firmware regions.
5. Validate the narrow [stage0 clock providers](clock-stage0.md). The MCT
   PCLK gate is managed, but its CMU_MIF bus path is firmware-owned and only
   read/checked. A disabled or unstable parent is a failed diagnostic boot,
   not permission to change shared PLLs or DRAM clocks. Host tests and a
   successful build do not prove MMIO accessibility or the inherited state.
6. Pack with an exact same-device Samsung Android boot wrapper and a DTBH
   table that covers **rev04**. The mainline DTB is embedded for Linux, not
   substituted for the old firmware-facing Samsung DTBH table.
7. Check PIT target/size, preserve the known-working RECOVERY/TWRP image,
   retain verified host rollback backups, and obtain separate approval of
   the experimental image and partition before **any** phone write.

This repository intentionally has no flash command or downloadable Android
BOOT image. Device backups must not be used as public CI inputs.

## Diagnostic output

The kernel is configured for `console=tty0`, verbose initcalls, no KASLR,
no console blanking and no automatic panic reboot. It does not depend on
UART output: enabling an unvalidated early UART can itself hang a boot.
The force-selected clean kernel command line prevents Android debug/log
arguments from becoming the Linux configuration.

The external gzip/newc initramfs contains only a static `/init`, virtual
filesystem mountpoints and console/kmsg device nodes. `/init` refuses to
run unless it is PID 1, mounts only proc/sysfs/devtmpfs, prints:

```text
MAINLINE_STAGE0_USERSPACE_OK
```

and emits a heartbeat every five seconds. It does not start a shell, mount
storage, bring up USB, contact the network, change regulators or invoke
reboot/poweroff. A marker plus advancing heartbeats is stronger evidence
than a kernel logo, but is still not a full phone port.

If the framebuffer handoff is not applied, the kernel may run with no
visible console. If the MCT gate or interrupt setup is wrong, it may stall
before userspace. A blank screen alone cannot distinguish those failures.

## Safety

There is no Linux charging, thermal protection policy or battery gauge in
this build. Do not leave a future diagnostic boot running unattended or
use it as a daily system. Do not assume the stock hardware charger/watchdog
state is suitable for extended testing. Return promptly to known-working
recovery using the established hardware buttons.

Future work is proper CMU/pinctrl support and timer validation, then USB
diagnostics, PMIC/power/thermal support, and only later storage and native
display/input drivers. None of those is implied by the initial build.
