# J5 Prime: from-scratch upstream Linux bring-up

Target: **Samsung Galaxy J5 Prime SM-G570F**, **on5xelte**, **Exynos7570 / SWA rev04**.

**Experimental stage 0. No mainline Linux boot has been demonstrated on this phone.**
The successful saved uniLoader screen control is loader-only evidence, not proof
of Linux entry, timers, userspace or the new native loading contract.

This isolated branch, **`bringup/native-sboot-stage0`**, integrates an upstream
native BOOT candidate. `main` retains the earlier component/loader baseline.
All new project source changes, synthetic tests and compilation are performed
in GitHub; the local baseline and previous loader products are not overwritten.

The base remains clean **upstream Linux 7.3.3**, pinned by archive SHA-256 and
commit in [`sources.lock.json`](sources.lock.json). This is not a downstream
Android kernel or an existing mainline phone fork. The platform descriptions,
narrow local driver sources, diagnostic `/init` and packaging are independently
written. Existing upstream ARM, GIC, MCT, nolibc and simplefb support is reused.

## Native diagnostic target

- One Cortex-A53 CPU (`maxcpus=1`), PSCI v0.1 IDs; no idle, DVFS or suspend.
- Only `0x40000000–0x60000000` exposed to Linux, enforced by `mem=512M`.
  Conservative low firmware and FDT/historical-loader reservations remain.
- A non-EFI, text-offset-zero ARM64 `Image` **requested at `0x40000000`**.
  Its complete linked memory including BSS must end no later than `0x42000000`.
  **The working old loader's `0x40080000` placement does not satisfy this contract.**
- A static upstream-nolibc `/init` delivered by a **built-in** compressed archive.
  CI checks exact archive bytes and symbol bounds in both `Image` and `vmlinux`.
  The wrapper's small external compatibility ramdisk contains only a trailer.
- Native Linux scanout inheritance: read DECON, check stable uncropped geometry,
  supported format, **all backing rows and page envelope**, RAM/FDT/initrd and
  firmware reservations, then reserve before allocator startup. Recheck late
  state immediately before platform-device publication. No DECON/DSIM or display
  clock reprogramming; the static DT framebuffer remains disabled. See
  [`docs/bringup.md`](docs/bringup.md).
- A generated **one-entry rev04 DTBH v2 / legacy BOOT wrapper**, without any
  firmware template or private device input. Header/footer validity is not
  Samsung authentication or proof that S-Boot will accept the image and requests.
- Architected counter at 26 MHz and upstream MCT clockevents. The narrow local
  [clock providers](docs/clock-stage0.md) manage only the MCT PCLK gate and validate
  its inherited CMU_MIF parent, not shared PLLs, muxes, dividers or DRAM clocks.

There is **no storage, USB/ADB, Wi-Fi/modem, touchscreen, native panel driver,
PMIC, charging, battery monitoring or thermal management**. MMIO accessibility,
clock/timer interrupts, framebuffer survival and `/init` execution are untested.

The earlier upstream component baseline passed in
[Actions run 42514924374](https://github.com/FogboundSloth25/j5prime-mainline/actions/runs/42514924374)
at `cb6f4495f2b34e10129d04f5fac4966753c96e7a`. That is not a native-build result
or a Linux hardware boot. Native results must identify their own exact commit/run.

## Build and test on GitHub Actions only

Use the existing **Mainline stage0** workflow against this isolated branch with
its exact commit input. No project test or compilation is requested locally:

```sh
BRANCH=bringup/native-sboot-stage0
SHA=$(gh api "repos/FogboundSloth25/j5prime-mainline/git/ref/heads/$BRANCH" --jq .object.sha)
gh workflow run mainline.yml --repo FogboundSloth25/j5prime-mainline \
    --ref "$BRANCH" -f expected_commit="$SHA"
```

The workflow refuses a mismatched dispatch/source SHA. CI runs synthetic
Image/ELF/archive/BOOT/DTBH tests, the **production** clock/framebuffer C against
explicit MMIO/API stand-ins with GCC and Clang ASan/UBSan, and synthetic recipient
encryption tests. It then cross-builds and checks the actual kernel, configuration,
DT properties/schemas, linked archive and generated wrapper. The mandatory ARM64
power-supply class is present, but every optional supply driver/helper is checked
disabled. Nothing here demonstrates charging or hardware-safe unattended use.

The workflow has read-only repository permissions, no stored checkout credentials,
no phone access or flashing steps, and no release publishing.

## Recipient-encrypted artifacts

On both success and failure, generated images, logs, reports and corresponding
upstream/port sources are bundled and encrypted with **CMS AES-256-GCM and
RSA-OAEP SHA-256** before the only artifact upload. The production script pins
the authorized public certificate SHA-256 and reads the public certificate from
`STAGE0_BUILD_RECIPIENT_CERT`. A missing/wrong certificate fails closed: **no
plaintext fallback**. The real private key is never a repository/CI input.

Download `on5xelte-native-stage0-encrypted-RUN_ID-ATTEMPT` privately. Check the
ciphertext receipt, decrypt with the privately retained matching recipient key,
and verify the internal bundle manifest, component `SHA256SUMS`, source lock,
commit/run provenance and an independent BOOT preflight. Complete corresponding
source is included in the encrypted bundle and independently available through
this public repository and the pinned public upstream archive.

## A candidate is not phone authorization

`on5xelte-native-stage0-boot-rev04.img` is an **untested native BOOT candidate**.
Do not pass individual `Image` or `.dtb` components to a phone flashing tool.
S-Boot acceptance, actual placement and FDT handoff remain unconfirmed. CI success
alone authorizes no transfer, write, mount, flush or reboot.

Any hardware experiment requires separate selection/approval of the exact image
and a short **BOOT-only** test, fresh verification of the original rollback
backups and known-working TWRP, and a prompt return to recovery. **Never change
S-Boot/BL or RECOVERY.** The stock reference package is not a substitute for the
original phone backups. Previous successful/failed products and loader changes
must remain intact.

Only source and sanitized, non-unique hardware facts belong here. Never commit or
use in public CI any partition/firmware backup, live dump, photo, raw phone log,
serial number, private credential, private key or prebuilt private phone image.

## Layout

| Path | Purpose |
| --- | --- |
| `dts/` | Minimal independently written SoC/rev04 board descriptions |
| `patches/linux/` | Local changes to pinned upstream Linux/bindings |
| `kernel/` | Local clock and read-only scanout handoff source |
| `configs/` | Strictly checked native stage0 configuration |
| `initramfs/` | Built-in static diagnostic userspace source |
| `scripts/`, `tests/` | CI build, format/linkage checks, encrypted export, synthetic tests |
| `docs/hardware.md`, `docs/bringup.md` | Sanitized provenance, contracts and limits |

New code is **GPL-2.0-only**; reused upstream code retains its licensing/attribution.
