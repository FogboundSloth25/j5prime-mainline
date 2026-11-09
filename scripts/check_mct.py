#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""CI-only production MCT consumer/caller regression with explicit stand-ins."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

FUNCTIONS = ("exynos4_timer_resources", "mct_init_dt")
ERRORS = (-5, -11, -22, -12, 1, 0)
ROOT = Path(__file__).resolve().parents[1]

def select_functions(source: str) -> str:
    if len(source.encode()) > 1024 * 1024:
        raise ValueError("MCT source exceeds bound")
    selected = []
    for name in FUNCTIONS:
        starts = list(re.finditer(r"(?m)^static int __init " + name + r"\(", source))
        if len(starts) != 1:
            raise ValueError("missing/duplicate production MCT function: " + name)
        start = starts[0].start()
        end = source.find("\n}\n", start)
        if end < 0 or end + 3 - start > 16384:
            raise ValueError("unterminated/oversized production MCT function")
        value = source[start:end + 3]
        if value.count("{") != value.count("}"):
            raise ValueError("unbalanced production MCT function")
        selected.append(value)
    return "\n".join(selected)

def check_sources(patched: str, upstream: str) -> dict:
    results = {}
    with tempfile.TemporaryDirectory(prefix="mct-consumer-", dir=os.environ["RUNNER_TEMP"]) as directory:
        top = Path(directory)
        include = top / "include"
        for name in ("init.h", "types.h", "platform_data/simplefb.h"):
            path = include / "linux" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "mct-kernel-mock.h"\n')
        for compiler in ("gcc", "clang"):
            results[compiler] = {}
            for kind, source in (("patched", patched), ("upstream", upstream)):
                (include / "selected-mct-production.c").write_text(select_functions(source))
                binary = top / (compiler + "-" + kind)
                compiled = subprocess.run([compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-sign-compare", "-g", "-O1", "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer", "-fno-pie", "-no-pie", "-I", str(include),
                    "-I", str(ROOT / "tests/c"), str(ROOT / "tests/c/mct-stage0.c"),
                    "-o", str(binary)], capture_output=True, text=True)
                if compiled.returncode:
                    raise ValueError("MCT production stand-in compilation failed:\n" + compiled.stdout + compiled.stderr)
                probes = []
                for error in ERRORS if kind == "patched" else (-5,):
                    result = subprocess.run([str(binary), str(error)], capture_output=True, text=True)
                    if result.returncode or result.stderr:
                        raise ValueError("MCT stand-in failure/sanitizer diagnostics:\n" + result.stdout + result.stderr)
                    value = json.loads(result.stdout)
                    if value["enables"] != 1 or value["maps"] != 1:
                        raise ValueError("production MCT resource path was not exercised")
                    if kind == "upstream":
                        if (value["result"] != 0 or any(value[k] != 1 for k in ("interrupts", "counters", "events")) or
                            value["unmaps"] or value["puts"] or value["markers"] or value["reg_base_cleared"]):
                            raise ValueError("pinned upstream unchecked-enable defect was not reproduced")
                    elif error:
                        if (value["result"] != error or any(value[k] for k in ("interrupts", "counters", "events")) or
                            value["unmaps"] != 1 or value["puts"] != 2 or value["markers"] != 1 or not value["reg_base_cleared"]):
                            raise ValueError("MCT failure did not propagate/stop/unwind before timer use")
                    elif (value["result"] != 0 or any(value[k] != 1 for k in ("interrupts", "counters", "events")) or
                          value["unmaps"] or value["puts"] or value["markers"] or value["reg_base_cleared"]):
                        raise ValueError("successful MCT policy changed")
                    probes.append(value)
                results[compiler][kind] = probes
    return {"production_functions": list(FUNCTIONS), "results": results,
            "patched_source_sha256": hashlib.sha256(patched.encode()).hexdigest(),
            "upstream_source_sha256": hashlib.sha256(upstream.encode()).hexdigest(),
            "unchecked_upstream_clock_enable_defect_reproduced": True,
            "patched_MCT_error_propagation_and_no_timer_use_verified": True,
            "GCC_Clang_ASan_UBSan": True,
            "mocked_clocks_DT_mapping_interrupts_and_timer_subsystems": True,
            "hardware_boot_tested": False}

def main() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Production MCT source regression is CI-only.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patched", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check_sources(args.patched.read_text(), args.upstream.read_text()), indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
