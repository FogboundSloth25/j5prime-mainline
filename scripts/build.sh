#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

# This project intentionally delegates compilation to GitHub-hosted CI.
if [[ ${GITHUB_ACTIONS:-} != true ]]; then
    echo 'Kernel compilation is CI-only. Use the mainline.yml GitHub workflow.' >&2
    exit 1
fi

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$REPO"
WORK=${RUNNER_TEMP:?}/j5prime-mainline-stage0
KERNEL_OUT=$WORK/kernel-out
LOGS=$REPO/out/logs
REPORTS=$REPO/out/reports
DIST=$REPO/dist
SOURCE_DIST=$REPO/out/source
mkdir -p -- "$LOGS" "$REPORTS" "$DIST" "$SOURCE_DIST"

run_logged() {
    local name=$1
    shift
    "$@" 2>&1 | tee "$LOGS/$name.log"
}

export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- LC_ALL=C
export KBUILD_BUILD_USER=mainline-ci KBUILD_BUILD_HOST=github-actions KBUILD_BUILD_VERSION=1
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)
export KBUILD_BUILD_TIMESTAMP
KBUILD_BUILD_TIMESTAMP=$(date -u -d "@$SOURCE_DATE_EPOCH" '+%a %b %d %T UTC %Y')
VERSION=$(python3 -c 'import json; print(json.load(open("sources.lock.json"))["linux"]["version"])')
LINUX=$WORK/linux-$VERSION

run_logged fetch python3 -m scripts.fetch_kernel --work-root "$WORK"
while IFS= read -r patch; do
    [[ -z $patch || $patch == \#* ]] && continue
    [[ $patch =~ ^[0-9]{4}-[a-z0-9-]+\.patch$ ]] || exit 1
    run_logged "apply-$patch" git -C "$LINUX" apply --check "$REPO/patches/linux/$patch"
    git -C "$LINUX" apply "$REPO/patches/linux/$patch"
done < patches/linux/series
install -m 644 dts/exynos7570.dtsi dts/exynos7570-on5xelte-stage0.dts \
    "$LINUX/arch/arm64/boot/dts/exynos/"

# nolibc comes from the same pinned upstream source; no external ramdisk fork.
run_logged init aarch64-linux-gnu-gcc -std=gnu11 -Wall -Wextra -Werror \
    -Wno-unused-parameter -Os -fno-stack-protector -fno-pie -no-pie \
    -fno-asynchronous-unwind-tables -fno-ident -nostdlib -static \
    -I"$LINUX/tools/include/nolibc" initramfs/init.c \
    -Wl,--build-id=none,--fix-cortex-a53-843419 \
    -o "$DIST/init" -lgcc
python3 -m scripts.validate elf "$DIST/init" > "$REPORTS/init-elf.json"
python3 -m scripts.make_initramfs --init "$DIST/init" --output "$DIST/initramfs.cpio.gz"
python3 -m scripts.validate initramfs "$DIST/initramfs.cpio.gz" > "$REPORTS/initramfs.json"

run_logged allnoconfig make -C "$LINUX" O="$KERNEL_OUT" allnoconfig
run_logged merge-config bash "$LINUX/scripts/kconfig/merge_config.sh" -m -O "$KERNEL_OUT" \
    "$KERNEL_OUT/.config" "$REPO/configs/on5xelte-stage0.config"
run_logged olddefconfig make -C "$LINUX" O="$KERNEL_OUT" olddefconfig
cp -- "$KERNEL_OUT/.config" "$DIST/kernel.config"
python3 -m scripts.validate config "$DIST/kernel.config" configs/on5xelte-stage0.config \
    --power-supply-kconfig "$LINUX/drivers/power/supply/Kconfig" \
    > "$REPORTS/config.json"

run_logged dtb make -C "$LINUX" O="$KERNEL_OUT" -j"$(nproc)" \
    exynos/exynos7570-on5xelte-stage0.dtb
install -m 644 "$KERNEL_OUT/arch/arm64/boot/dts/exynos/exynos7570-on5xelte-stage0.dtb" "$DIST/"
python3 -m scripts.validate dtb "$DIST/exynos7570-on5xelte-stage0.dtb" > "$REPORTS/dtb.json"
dtc -I dtb -O dts "$DIST/exynos7570-on5xelte-stage0.dtb" > "$DIST/exynos7570-on5xelte-stage0.dts"

# Standalone schema validation avoids building unrelated phone DTBs.
SCHEMA_ENV=${RUNNER_TEMP:?}/j5prime-dtschema
python3 -m venv "$SCHEMA_ENV"
SCHEMA_VERSION=$(python3 -c 'import json; print(json.load(open("sources.lock.json"))["dtschema_version"])')
run_logged schema-install "$SCHEMA_ENV/bin/pip" install "dtschema==$SCHEMA_VERSION"
"$SCHEMA_ENV/bin/dt-mk-schema" -j "$LINUX/Documentation/devicetree/bindings" \
    > "$WORK/processed-schema.json" 2> "$LOGS/schema-compile.log"
"$SCHEMA_ENV/bin/dt-validate" -s "$WORK/processed-schema.json" \
    "$DIST/exynos7570-on5xelte-stage0.dtb" > "$REPORTS/schema-validation.log" 2>&1
if [[ -s $REPORTS/schema-validation.log ]]; then
    cat -- "$REPORTS/schema-validation.log" >&2
    echo 'DT schema diagnostics are not silently accepted.' >&2
    exit 1
fi

# Only start the expensive kernel build after configuration and DT checks.
run_logged kernel make -C "$LINUX" O="$KERNEL_OUT" -j"$(nproc)" Image
install -m 644 "$KERNEL_OUT/arch/arm64/boot/Image" "$DIST/Image"
install -m 644 "$KERNEL_OUT/vmlinux" "$KERNEL_OUT/System.map" "$DIST/"
python3 -m scripts.validate image "$DIST/Image" > "$REPORTS/image.json"

cp -- sources.lock.json LICENSE "$DIST/"
cp -- "$REPORTS/"*.json "$DIST/"
cat > "$DIST/NOT-FLASHABLE.txt" <<'NOTICE'
Experimental upstream Linux stage0 BUILD COMPONENTS, not an Android BOOT image.
Mainline has NOT been boot-tested on this phone. The simplefb DT node is disabled.
Runtime uniLoader framebuffer handoff and full bundle checks are still required.
No storage, USB/ADB, charging or thermal management is provided.
No phone write is authorized by successful compilation or artifact download.
NOTICE
python3 - <<'PY'
import json
import os
from pathlib import Path
import subprocess

report = {
    'repository': os.environ['GITHUB_REPOSITORY'],
    'repository_commit': os.environ['GITHUB_SHA'],
    'run_id': os.environ['GITHUB_RUN_ID'],
    'run_url': f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    'sources': json.loads(Path('sources.lock.json').read_text()),
    'toolchain': subprocess.check_output(['aarch64-linux-gnu-gcc', '--version'], text=True).splitlines()[0],
    'source_date_epoch': int(os.environ['SOURCE_DATE_EPOCH']),
    'hardware_boot_tested': False,
    'flashable_android_image': False,
    'framebuffer_runtime_handoff_applied': False,
    'source_artifact': f"on5xelte-stage0-source-{os.environ['GITHUB_RUN_ID']}",
}
Path('dist/build-info.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
PY

# Make complete corresponding kernel + port sources available from this run,
# separately, so downloading small build components needn't download Linux.
cp -- "$WORK/linux-$VERSION.tar.xz" "$SOURCE_DIST/"
git archive --format=tar.gz HEAD > "$SOURCE_DIST/port-source.tar.gz"
cp -- "$DIST/kernel.config" "$DIST/build-info.json" sources.lock.json LICENSE "$SOURCE_DIST/"
cp -- "$LINUX/LICENSES/preferred/MIT" "$DIST/nolibc-MIT-license.txt"
for directory in "$DIST" "$SOURCE_DIST"; do
    (
        cd -- "$directory"
        find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%P\0' \
            | sort -z | xargs -0 sha256sum > SHA256SUMS
    )
done

cat >> "$GITHUB_STEP_SUMMARY" <<'SUMMARY'
## Upstream Linux stage0 components built and checked

- ARM64 Image, static diagnostic initramfs and rev04 board DTB
- Resolved Kconfig, Image load limits, DT properties and DT schemas checked
- Complete corresponding source supplied in the separate source artifact
- **Not boot-tested, not an Android BOOT image, no flashing authorization**
- Framebuffer placeholder is disabled; runtime uniLoader integration remains
SUMMARY
