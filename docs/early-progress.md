# Guarded early progress — not a hardware acceptance test

The normal simplefb publication is a device initcall, after IRQ/time init.
Early failures can therefore leave only firmware artwork. This branch adds
small memory-only progress cells, without weakening the original gate or
programming DECON, DSIM, display clocks, power, storage or USB registers.

## Safety and bounds

Before any pixel write the existing early gate must identify/bounds-check
the board/FDT, measure stable live window-0 metadata, validate the complete
backing/page envelope against actual kernel/RAM/reservations/FDT/initrd,
reserve it, and confirm no drift. The actual kernel placement contract
remains `0x40000000`; the disabled static DT simplefb is not an alternative.
Every checkpoint revalidates board/FDT before hard-coded MMIO, then checks
two fresh DECON snapshots against the saved surface. Drift disables further
progress and late publication; an existing reservation remains reserved.

Only one 16x16 visible cell is written at a time. Cells occupy columns
0–111, rows 0–15, independent of the firmware logo location. Supported formats
remain ARGB/ABGR/XRGB/XBGR8888 and RGB565. Mapping/range/phase/format errors
refuse writes. The renderer maps bounded framebuffer memory as device IO,
emits little-endian pixels and orders writes before unmapping; it does not
assume a cached linear alias or allocate memory/use a timer. Host tests
enforce the physical surface, full reservation, exact per-row write span,
guard bytes, all formats and failure/drift paths. MMU/cache/fabric traps,
firmware ownership and panel operation still require separate hardware
evidence; the live check and writes cannot be atomic with firmware.

## Meaning of numbered cells

Green cells with white borders/digits record a **visited code path**:

| Cell | What was reached — no stronger claim |
| --- | --- |
| 1 | Full early FDT/kernel/memory/backing gate and reservation completed |
| 2 | `paging_init()` returned |
| 3 | About to enter `init_IRQ()` |
| 4 | About to enter `time_init()` |
| 5 | `time_init()` returned, including a possible rejected timer provider |
| 6 | `console_init()` returned; framebuffer console may still be unavailable |
| Red 7 | MCT `clk_prepare_enable()` returned nonzero and timer init was refused |

Red 7 does not identify the parent/gate failure mechanism. Cell 5 is **not**
proof of a working timer, and 6 is not proof of a visible console. Later
simplefb/fbcon or firmware can overwrite cells. The ordinary userspace
marker/advancing heartbeat remains a separate, stronger observation.

**No cells is ambiguous:** rejected firmware packaging/entry, wrong actual
placement/FDT, an earlier Linux stop, a rejected scanout or invisible memory
writes can all produce that observation. Do not use logo-only output to
diagnose one of them. CI cannot establish a complete hardware repair.

## MCT consumer regression

`scripts/check_mct.py` runs only in GitHub CI, before kernel compilation.
It extracts the actual resource and caller functions from the hash-pinned
upstream input and the fully patched source. GCC and Clang + ASan/UBSan
reproduce the old unchecked enable return, then exercise nonzero errors
and success. Failed enable must propagate unchanged, release two acquired
clocks/unmap/reset the timer base, and never proceed into MCT interrupt,
counter or event initialization. Successful policy is unchanged. Clocks,
DT, mapping, interrupt and timer subsystems are explicit host stand-ins.

The original source, exact source hashes and regression report are retained
in the recipient-encrypted bundle. New progress symbols are required in
the actual Image/vmlinux alongside the verified built-in diagnostic `/init`.

## No implicit phone permission

Build/download/source checks do not authorize a phone transfer/write/reboot.
This stage0 still has no USB/ADB, charging, thermal policy or automatic
recovery. Any separately authorized native observation is short (15–30 s)
followed by immediate manual TWRP return, never an unattended wait. Never
read/write S-Boot/BL or write RECOVERY/other partitions. Preserve distinct
original and fresh rollback images and all historical failed evidence.
