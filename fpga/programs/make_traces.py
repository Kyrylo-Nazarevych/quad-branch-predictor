#!/usr/bin/env python3
"""Make precomputed trace folders for our own programs, like tools/scripts/make_precomputed.sh, using the
Streamlit app's pipeline (so on Windows it compiles and runs Spike in WSL).

    fpga/.venv/Scripts/python.exe fpga/programs/make_traces.py [program.c ...]

For each program and -O0/-O1/-O2: bp-tools/precomputed/<name>_<opt>/ with the trace files, summary.json,
branches.csv, the .c, .dis, .elf and the PRECOMPUTED marker; the .c also goes to tools/examples/ so the app
lists it.
"""
import shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BP = HERE.parent.parent / "tools"
sys.path.insert(0, str(BP / "app"))
import pipeline as P  # noqa: E402

sources = [Path(a) for a in sys.argv[1:]] or sorted(HERE.glob("*.c"))
ok, why = P.toolchain_status()
if not ok:
    sys.exit(f"no toolchain: {why}")
for src in sources:
    shutil.copy2(src, P.EXAMPLES / src.name)
    for opt in ("O0", "O1", "O2"):
        out = P.PRECOMPUTED / f"{src.stem}_{opt}"
        shutil.rmtree(out, ignore_errors=True)
        s = P.build_trace(src, out, opt=opt, user_only=True)
        (out / "trace.bin").unlink(missing_ok=True)
        shutil.copy2(src, out / src.name)
        (out / "PRECOMPUTED").touch()
        print(f"{out.name}: {s['branches']} branches, {s['instructions']} instructions, spike_status {s['spike_status']}")
