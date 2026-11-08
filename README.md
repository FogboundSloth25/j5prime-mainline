# J5 Prime: from-scratch upstream Linux bring-up

Target: **Samsung Galaxy J5 Prime SM-G570F**, **on5xelte**, **Exynos7570**.
The initial hardware reference is the SWA **rev04** board.

**Experimental stage 0. No mainline Linux boot has been demonstrated yet.**
The earlier, user-confirmed uniLoader screen test proves that the loader can
run and draw on this phone; it does not prove Linux, its timers or userspace.

This repository contains independently written platform descriptions and a
small patch series applied to **clean upstream Linux 7.3.3**. It is not an
Android kernel, a pre-existing mainline phone fork, or a complete device port.
The official source archive, its SHA-256 and its upstream commit are pinned
in [`sources.lock.json`](sources.lock.json). Existing upstream ARM, GIC,
Exynos MCT and simple-framebuffer drivers are reused where appropriate.

## First diagnostic target

- Cortex-A53 CPU0 only (`maxcpus=1`); no CPU idle, DVFS or suspend support.
- Only the first 512 MiB of DRAM exposed, with low firmware-log and loader
  reservations. The rest of the real DRAM layout is deliberately not enabled.
- PSCI **v0.1** function IDs from the working device, not assumed PSCI v0.2.
- Architected counter at 26 MHz; MCT clockevents. A local, unsubmitted timer
  quirk preserves the downstream counter/clockevent split without assuming
  that architected timer interrupts work under the stock firmware.
- Linux simplefb/fbcon using the live framebuffer left by S-Boot. The DTB's
  placeholder is **disabled** until uniLoader validates and patches it.
- A small, static, independently written `/init` using upstream nolibc.
  The success marker is
  `MAINLINE_STAGE0_USERSPACE_OK`; subsequent heartbeat messages test timers.

There is **no eMMC/SD, USB/ADB, Wi-Fi, modem, touchscreen, full display driver,
PMIC, charging, battery monitoring or thermal management** in this stage.
The MCT clock gate is initially inherited from firmware, not managed by a
finished Exynos7570 CMU driver. These are bring-up assumptions, not verified
device functionality. See [`docs/bringup.md`](docs/bringup.md).

## Build on GitHub Actions, not the slow local PC

Open the repository's **Actions → Mainline stage0 → Run workflow**, or use:

```sh
gh workflow run mainline.yml --repo FogboundSloth25/j5prime-mainline --ref main
gh run list --repo FogboundSloth25/j5prime-mainline --workflow mainline.yml
gh run download RUN_ID --repo FogboundSloth25/j5prime-mainline \
    --name on5xelte-stage0-RUN_ID --dir /path/to/host/artifacts
```

CI cross-compiles the kernel and `/init`, compiles the board DTB, checks the
requested kernel configuration and ARM64 load limits, validates the DTB,
and uploads checksummed build components and logs. The mandatory ARM64
power-supply class is allowed, but all of its optional support is checked
disabled; this is not charging or battery support. Host-only tests can run
locally without a kernel build or a connected phone:

```sh
python3 -m unittest discover -s tests -v
```

Tests use only synthetic Image/ELF/archive fixtures. Python 3.12 or newer is
required for safe source-archive extraction. The initramfs gzip header has
no host filename or timestamp and is stable across supported Python versions.

The workflow uses read-only repository permissions and does not need a PAT.
It has no phone flashing, release publishing or partition access steps.

## Artifacts are not flashable images

`Image`, the mainline `.dtb`, and `initramfs.cpio.gz` are **build components**.
None is a Samsung Android BOOT image; do not pass them directly to Heimdall.
The normal uniLoader relocation/kernel handoff is still untested on-device.
Runtime framebuffer handoff and a host-checked uniLoader bundle are separate
integration steps; successful compilation does not authorize a phone write.

Any later experiment needs an exact SM-G570F boot wrapper, a compatible
Samsung DTBH table covering rev04, verified rollback backups and **separate,
explicit approval of that image and the target partition**. Keep TWRP intact.
Without charging/thermal support, any eventual device test must be short.

Only code and sanitized hardware facts belong here. Do not commit partition
backups, raw device logs/cmdlines, live device-tree dumps, serial numbers,
private GitHub credentials or prebuilt phone images.

## Layout

| Path | Purpose |
| --- | --- |
| `dts/` | New minimal SoC and rev04 diagnostic board descriptions |
| `patches/linux/` | Local changes to pinned upstream Linux and its bindings |
| `configs/` | Stage0 kernel configuration, checked after Kconfig resolution |
| `initramfs/` | Static diagnostic userspace source, no Android ramdisk |
| `scripts/`, `tests/` | CI build, artifact validation and host-only tests |
| `docs/hardware.md` | Sanitized hardware facts and source provenance |

New code in this repository is licensed under **GPL-2.0-only**. Referenced
upstream code retains its original licensing and attribution.
