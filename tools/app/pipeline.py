"""Stage runner for the app: C file -> trace -> predictions -> statistics.

Every stage is a plain function, so the app, scripts and tests call the same code.
Where a teammate's piece is missing, the runner says so and falls back:

  stage                      preferred (kit)                               fallback
  2-4 trace                  bptrace.build_trace()                         bptrace/bptrace.py command line
  5   predictions            chip RTL in Icarus via sim/run_sim.py         stats/ref_models.py (Python)
  6   statistics             stats/bpstats.py                              (always present: shipped with the app)

Paths come from the environment, with defaults that match this folder's layout:
  BP_ROOT   repo root with bptrace/, examples/, sim/, hw_reference/, stats/   (default: parent of app/)
  SPIKE     Spike binary (default: spike on PATH, then ~/riscv-tools/bin/spike)
  BP_RUNS   where run folders go (default: <BP_ROOT>/runs)
"""
from __future__ import annotations

import hashlib, importlib, json, os, re, shutil, subprocess, sys, time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ROOT = Path(os.environ.get("BP_ROOT", Path(__file__).resolve().parent.parent))
RUNS = Path(os.environ.get("BP_RUNS", ROOT / "runs"))
EXAMPLES = ROOT / "examples"
for p in (ROOT / "stats", ROOT / "bptrace", ROOT / "sim"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ref_models  # noqa: E402  (stats/ref_models.py)

PREDICTORS = ref_models.PREDICTORS
PRED_COLUMNS = ["index", "pc8", "taken", *PREDICTORS, "cycles"]


class CompileError(Exception):
    """gcc failed. str() is the compiler's error text, ready to show."""


class SpikeError(Exception):
    """Spike did not finish the program (timeout or crash)."""


class SimError(Exception):
    """The RTL simulation failed."""


class ToolchainMissing(Exception):
    """No RISC-V compiler or Spike here, and no precomputed trace for this program."""


PRECOMPUTED = ROOT / "precomputed"


def _wsl_path(p: Path) -> str:
    """C:\\Users\\x\\y -> /mnt/c/Users/x/y (WSL's default mount of Windows drives)."""
    p = Path(p).resolve()
    return f"/mnt/{p.drive[0].lower()}{p.as_posix()[2:]}" if p.drive else p.as_posix()


_WSL_OK = None


def wsl_toolchain() -> bool:
    """On Windows without a native RISC-V toolchain: is it installed in WSL (scripts/setup_toolchain.sh)?"""
    global _WSL_OK
    if _WSL_OK is None:
        _WSL_OK = False
        if os.name == "nt" and shutil.which("wsl"):
            try:
                r = subprocess.run(["wsl", "-e", "bash", "-lc",
                                    'command -v riscv64-unknown-elf-gcc >/dev/null && test -x "$HOME/riscv-tools/bin/spike"'
                                    " && command -v dtc >/dev/null && command -v python3 >/dev/null"],
                                   capture_output=True, timeout=60)
                _WSL_OK = r.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
    return _WSL_OK


def toolchain_status() -> tuple[bool, str]:
    """Can this machine compile and run programs? (gcc, Spike and dtc, which Spike calls at run time)."""
    if os.name == "nt" and not shutil.which("riscv64-unknown-elf-gcc") and wsl_toolchain():
        return True, "toolchain found in WSL"
    missing = []
    if not shutil.which("riscv64-unknown-elf-gcc"):
        missing.append("riscv64-unknown-elf-gcc")
    spike = os.environ.get("SPIKE") or shutil.which("spike") or str(Path.home() / "riscv-tools/bin/spike")
    if not (Path(spike).is_file() and os.access(spike, os.X_OK)):
        missing.append("spike")
    if not shutil.which("dtc"):
        missing.append("dtc")
    if missing:
        return False, (f"{', '.join(missing)} not found. Install the toolchain with "
                       "bash scripts/setup_toolchain.sh, or pick one of the examples, which have precomputed traces.")
    return True, "toolchain found"


def precomputed_for(name: str, code: bytes, opt: str, user_only: bool) -> Path | None:
    """A trace made earlier on a machine with the toolchain, for exactly this source and these options.

    Folders are precomputed/<stem>_<opt>[_all]/ holding the trace files, summary.json, the source
    and a PRECOMPUTED marker. The source must match byte for byte, so an edited file never
    picks up an old trace.
    """
    d = PRECOMPUTED / f"{Path(name).stem}_{opt.lstrip('-')}{'' if user_only else '_all'}"
    src = d / Path(name).name
    if (d / "PRECOMPUTED").exists() and src.exists() and src.read_bytes() == code:
        return d
    return None


def _spike_env():
    env = dict(os.environ)
    if "SPIKE" not in env:
        found = shutil.which("spike") or str(Path.home() / "riscv-tools/bin/spike")
        env["SPIKE"] = found
    env["PATH"] = str(Path(env["SPIKE"]).parent) + os.pathsep + env.get("PATH", "")
    return env


def source_key(code: bytes, **options) -> str:
    """Same program + same options = same run folder, so repeated demo runs are instant."""
    h = hashlib.sha256(code)
    h.update(json.dumps(options, sort_keys=True).encode())
    return h.hexdigest()[:16]


# ------------------------------------------------------------------ stage 2-4: trace

BARE_METAL_HINTS = {
    "printf": "There is no operating system: printf, puts and other stdio calls don't exist. Return the result from main() instead.",
    "malloc": "There is no heap: use static arrays instead of malloc/free.",
    "free": "There is no heap: use static arrays instead of malloc/free.",
    "rand": "There is no C library: write your own random generator (see examples/pattern_xor.c).",
}


def _compile_message(stderr: str) -> str:
    keep = [l for l in stderr.splitlines()
            if not l.startswith("+ ") and not l.startswith("Traceback") and not l.startswith("  File")
            and "CalledProcessError" not in l and "subprocess" not in l and l.strip()]
    keep = [re.sub(r"/\S*/(src|runs)/[0-9a-f]+/", "", l) for l in keep]   # hide run-folder paths
    msg = "\n".join(keep[:25]) or stderr[-2000:]
    hints = [h for name, h in BARE_METAL_HINTS.items() if re.search(rf"undefined reference to `{name}'", stderr)]
    return msg + ("\n\n" + "\n".join(dict.fromkeys(hints)) if hints else "")


def build_trace(src: Path, out: Path, opt="O1", march="rv32im", user_only=True,
                pc_bits="7:0", max_instructions=2_000_000, timeout_s=60) -> dict:
    """Compile, run on Spike and extract the branch trace into `out`. Returns summary.json as a dict."""
    out.mkdir(parents=True, exist_ok=True)
    try:   # a Python API for bptrace, when it exists
        bt = importlib.import_module("bptrace")
        if hasattr(bt, "build_trace"):
            return bt.build_trace(str(src), str(out), opt=opt, march=march, user_only=user_only,
                                  pc_bits=pc_bits, timeout_s=timeout_s)
    except ImportError:
        pass
    script = ROOT / "bptrace" / "bptrace.py"
    if not script.exists():
        raise FileNotFoundError(f"bptrace not found at {script}")
    cmd = [sys.executable, str(script), str(src), "-o", str(out), "--opt", opt.lstrip("-"),
           "--march", march, "--pc-bits", pc_bits, "--timeout", str(timeout_s)]
    if user_only:
        cmd.append("--user-only")
    if "--max-instructions" in script.read_text(encoding="utf-8", errors="replace"):          # the patched bptrace caps runaway programs
        cmd += ["--max-instructions", str(max_instructions)]
    if os.name == "nt" and not shutil.which("riscv64-unknown-elf-gcc") and wsl_toolchain():
        r = _run_in_wsl(cmd, src, out, timeout_s)
    else:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=_spike_env(), timeout=timeout_s + 60)
    elf = out / (src.stem + ".elf")
    if r.returncode != 0 and not elf.exists():
        raise CompileError(_compile_message(r.stderr))
    if r.returncode != 0:
        raise SpikeError(r.stderr[-1500:])
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8", errors="replace"))
    if summary.get("spike_status") not in (0, None) or summary.get("spike_status") == "timeout":
        raise SpikeError(f"Spike stopped with status {summary['spike_status']}: "
                         "does main() return? Programs must finish within "
                         f"{max_instructions:,} instructions.")
    if summary.get("spike_status") is None:
        raise SpikeError(f"Spike was stopped after {timeout_s} s: does main() return?")
    return summary


def _run_in_wsl(cmd, src: Path, out: Path, timeout_s: int):
    """Run the bptrace command line inside WSL. It works in a Linux temporary folder (Spike's log is about 50 bytes
    per instruction, slow to write onto C:), then copies everything except spike.log back into `out`.
    bptrace and the C file are copied there too: bptrace splits addr2line's "file:line" at the first space, so a
    source path with a space in it (like ".../Claude Folder/...") would lose every branch to the --user-only filter."""
    import shlex
    _python, bptrace_py, source, _o, _out, *options = cmd       # [python, bptrace.py, source, "-o", out, options...]
    q = lambda p: shlex.quote(_wsl_path(Path(p)))
    src_name = shlex.quote(Path(source).name)
    script = ('tmp=$(mktemp -d); export SPIKE="$HOME/riscv-tools/bin/spike" PATH="$HOME/riscv-tools/bin:$PATH"; '
              f'cp -r {q(Path(bptrace_py).parent)} "$tmp/bptrace"; mkdir "$tmp/src" "$tmp/out"; '
              f'cp {q(source)} "$tmp/src/"; cd "$tmp"; '
              f'python3 bptrace/bptrace.py src/{src_name} -o "$tmp/out" ' + " ".join(shlex.quote(a) for a in options) +
              f'; rc=$?; rm -f "$tmp/out/spike.log"; cp -r "$tmp/out"/. {q(out)}/; rm -rf "$tmp"; exit $rc')
    return subprocess.run(["wsl", "-e", "bash", "-lc", script], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout_s + 90)


def truncate_trace(out: Path, max_branches: int) -> bool:
    """Keep the first max_branches branches in every trace file (simulation time grows linearly)."""
    lines = (out / "trace.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= max_branches:
        return False
    for name in ("trace.txt", "tb_input.hex", "trace.mem"):
        p = out / name
        if p.exists():
            p.write_text("\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[:max_branches]) + "\n", encoding="utf-8")
    vh = out / "trace_init.vh"
    if vh.exists():
        keep, n = [], 0
        for l in vh.read_text(encoding="utf-8", errors="replace").splitlines():
            if l.startswith("N_BRANCHES"):
                l = f"N_BRANCHES = {max_branches};"
            elif l.startswith("trace["):
                n += 1
                if n > max_branches:
                    continue
            keep.append(l)
        vh.write_text("\n".join(keep) + "\n", encoding="utf-8")
    s = json.loads((out / "summary.json").read_text(encoding="utf-8", errors="replace"))
    s.update(truncated=True, branches_total=s["branches"], branches=max_branches)
    (out / "summary.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    return True


def load_trace(out: Path) -> pd.DataFrame:
    """trace.txt -> DataFrame(index, pc, pc8, taken). Row i here is row i of predictions.csv."""
    if (out / "trace.txt").stat().st_size == 0:
        return pd.DataFrame(columns=["index", "pc", "pc8", "taken"])
    df = pd.read_csv(out / "trace.txt", sep=" ", header=None, names=["pc", "taken"], dtype={"pc": str})
    df["pc"] = df["pc"].map(lambda s: int(s, 16))
    df.insert(0, "index", range(len(df)))
    df.insert(2, "pc8", df["pc"] & 0xFF)
    return df


# ------------------------------------------------------------------ stage 5: predictions

def rtl_status() -> tuple[bool, str]:
    """Can we replay through the chip RTL? (needs Icarus, sim/run_sim.py and hw_reference/)."""
    if not shutil.which("iverilog"):
        return False, "Icarus Verilog (iverilog) is not installed"
    if not (ROOT / "sim" / "run_sim.py").exists():
        return False, "sim/run_sim.py is not in the repo yet"
    if not (ROOT / "hw_reference").is_dir():
        return False, "hw_reference/ (the chip RTL) is not in the repo yet"
    return True, "chip RTL in Icarus Verilog"


def _normalise(df: pd.DataFrame, n: int) -> pd.DataFrame:
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in PRED_COLUMNS if c not in df.columns and c != "cycles"]
    if missing:
        raise SimError(f"predictions.csv lacks columns {missing}")
    if "cycles" not in df.columns:
        df["cycles"] = pd.NA
    if len(df) != n:
        raise SimError(f"predictions.csv has {len(df)} rows for {n} branches")
    return df[PRED_COLUMNS]


def simulate_rtl(out: Path, n: int, timeout_s=600) -> pd.DataFrame:
    """Replay the trace through the chip RTL. Tries run_sim.run_sim(dir) first, then the CLI."""
    script = ROOT / "sim" / "run_sim.py"
    try:
        # import only a module that defines run_sim(): importing a plain script would run it
        rs = importlib.import_module("run_sim") if "def run_sim(" in script.read_text(encoding="utf-8", errors="replace") else None
        if rs is not None and hasattr(rs, "run_sim"):
            res = rs.run_sim(str(out))
            if isinstance(res, pd.DataFrame):
                return _normalise(res.copy(), n)
    except Exception as e:                         # fall through to the command line
        print("run_sim import path failed:", e, file=sys.stderr)
    r = subprocess.run([sys.executable, str(ROOT / "sim" / "run_sim.py"), str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s, cwd=ROOT)
    if r.returncode != 0 or not (out / "predictions.csv").exists():
        raise SimError((r.stderr or r.stdout)[-1500:])
    return _normalise(pd.read_csv(out / "predictions.csv"), n)


#   The FPGA board: the same chip RTL on the Nexys 4 DDR, fed over USB-UART (fpga/host/bplink.py).
FPGA_HOST = Path(os.environ.get("BP_FPGA_HOST", ROOT.parent.parent / "fpga" / "host"))


def _bplink():
    if str(FPGA_HOST) not in sys.path:
        sys.path.insert(0, str(FPGA_HOST))
    return importlib.import_module("bplink")


def board_status() -> tuple[bool, str]:
    """Is the FPGA board reachable? (bplink.py, pyserial and an FTDI COM port)."""
    if not (FPGA_HOST / "bplink.py").exists():
        return False, f"fpga/host/bplink.py not found (set BP_FPGA_HOST; looked in {FPGA_HOST})"
    try:
        bl = _bplink()
    except ImportError as e:
        return False, f"cannot load bplink ({e}); pip install pyserial"
    port = os.environ.get("BP_FPGA_PORT") or bl.find_port()
    if not port:
        return False, "no FTDI COM port: plug the Nexys 4 DDR into J6 (PROG) and switch it on"
    try:                                       # only one program can hold the port (the dashboard, for example)
        bl.serial.Serial(port).close()
    except Exception:
        return False, f"{port} is in use by another program (close the FPGA dashboard)"
    return True, f"Nexys 4 DDR on {port}"


def simulate_board(out: Path, n: int) -> pd.DataFrame:
    """Replay tb_input.hex on the FPGA: reset the chip, stream every branch, read back predictions and clocks."""
    bl = _bplink()
    port = os.environ.get("BP_FPGA_PORT") or bl.find_port()
    if not port:
        raise SimError("no FTDI COM port for the FPGA board")
    lines = (out / "tb_input.hex").read_text(encoding="utf-8").split("\n")
    trace = [(int(p, 16), int(t)) for p, t in (l.split() for l in lines if l.strip())]
    try:
        board = bl.Board(port)
    except Exception as e:                       # port busy (dashboard open?) or gone
        raise SimError(f"cannot open {port}: {e}")
    try:
        board.sync()
        if board.ping() != bl.VERSION:
            raise SimError("the board speaks another protocol version: load the matching bitstream")
        board.reset()
        ferr = board.status()
        res = board.run(trace)
        if board.status() != ferr:
            raise SimError("UART framing errors during the run")
    except bl.ProtocolError as e:
        raise SimError(f"board on {port}: {e}")
    finally:
        board.close()
    df = pd.DataFrame(res, columns=[*PREDICTORS, "cycles"])
    df.insert(0, "taken", [t for _, t in trace])
    df.insert(0, "pc8", [p for p, _ in trace])
    df.insert(0, "index", range(len(trace)))
    return _normalise(df, n)


def simulate_models(trace: pd.DataFrame, bidx=6, ghist=8) -> pd.DataFrame:
    preds = ref_models.run(zip(trace["pc8"].tolist(), trace["taken"].tolist()), bidx=bidx, ghist=ghist)
    df = trace[["index", "pc8", "taken"]].copy()
    for k in PREDICTORS:
        df[k] = preds[k]
    df["cycles"] = pd.NA
    return df


@dataclass
class Run:
    key: str
    out: Path
    source_name: str
    source_text: str
    summary: dict
    trace: pd.DataFrame
    preds: pd.DataFrame
    backend: str                     # "rtl", "board" or "models"
    timings: dict = field(default_factory=dict)
    rtl_vs_models: int | None = None  # differing predictions, when both ran
    truncated: bool = False
    notes: list = field(default_factory=list)


def run_pipeline(source_name: str, code: bytes, *, opt="O1", user_only=True, backend="auto",
                 bidx=6, ghist=8, max_branches=100_000, max_instructions=2_000_000,
                 cross_check=True, reuse=True) -> Run:
    """The whole chain for one C file. backend: 'auto' (RTL if available), 'rtl', 'board' (FPGA) or 'models'."""
    rtl_ok, why = rtl_status()
    if backend == "auto":
        backend = "rtl" if rtl_ok and (bidx, ghist) == (6, 8) else "models"
    if backend == "rtl" and not rtl_ok:
        raise SimError(why)
    if backend == "board":
        board_ok, board_why = board_status()
        if not board_ok:
            raise SimError(board_why)
        if (bidx, ghist) != (6, 8):
            raise SimError("the chip on the board has 64 entries and 8 history bits")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(source_name).name)
    if not name.endswith(".c"):
        name += ".c"
    key = source_key(code, opt=opt, user_only=user_only, name=name, max_branches=max_branches)
    out = RUNS / key
    src = out / "src" / name
    t = {}
    notes = []
    done = out / ".trace_ok"                                  # written only after a successful build
    if not (reuse and done.exists()):
        shutil.rmtree(out, ignore_errors=True)
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(code)
        t0 = time.time()
        tc_ok, tc_why = toolchain_status()
        pre = precomputed_for(name, code, opt, user_only)
        if not tc_ok and pre is None:
            shutil.rmtree(out, ignore_errors=True)
            raise ToolchainMissing(tc_why)
        if not tc_ok:
            for f in pre.iterdir():
                if f.is_file() and f.suffix != ".c":
                    shutil.copy2(f, out / f.name)
        else:
            try:
                build_trace(src, out, opt=opt, user_only=user_only, max_instructions=max_instructions)
            finally:
                (out / "spike.log").unlink(missing_ok=True)      # ~53 bytes per instruction
        truncate_trace(out, max_branches)
        done.touch()
        t["trace"] = time.time() - t0
    if (out / "PRECOMPUTED").exists():
        notes.append("The trace was loaded from precomputed files: the RISC-V compiler or Spike is not "
                     "installed on this machine.")
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8", errors="replace"))
    trace = load_trace(out)
    if trace.empty:
        raise SpikeError("The program ran but executed no conditional branches"
                         + (" in your file (untick 'Only branches in this file' to include start-up code)." if user_only else "."))

    t0 = time.time()
    model_preds = simulate_models(trace, bidx, ghist)
    t["models"] = time.time() - t0
    diff = None
    if backend in ("rtl", "board"):
        t0 = time.time()
        preds = simulate_rtl(out, len(trace)) if backend == "rtl" else simulate_board(out, len(trace))
        t[backend] = time.time() - t0
        if cross_check:
            diff = int(sum((preds[k].astype(int) != model_preds[k]).sum() for k in PREDICTORS))
    else:
        preds = model_preds
        if not rtl_ok:
            notes.append(f"Predictions come from the Python reference models: {why}.")
    preds.to_csv(out / "predictions.csv", index=False)
    return Run(key=key, out=out, source_name=name, source_text=code.decode(errors="replace"),
               summary=summary, trace=trace, preds=preds, backend=backend, timings=t,
               rtl_vs_models=diff, truncated=bool(summary.get("truncated")), notes=notes)


MY_PROGRAMS = ROOT / "my_programs"        # C files pasted or uploaded in the app that compiled


def examples() -> dict[str, Path]:
    ex = {p.stem: p for p in sorted(EXAMPLES.glob("*.c"))}
    for p in sorted(MY_PROGRAMS.glob("*.c")):
        ex.setdefault(p.stem, p)
    return ex


def is_mine(path: Path) -> bool:
    return Path(path).parent == MY_PROGRAMS


def save_program(name: str, code: bytes) -> str:
    """Keep a C file that compiled, so it shows up in the example list. Returns the name it was saved under."""
    stem = re.sub(r"[^A-Za-z0-9_]", "_", Path(name).stem) or "my_program"
    if (EXAMPLES / f"{stem}.c").exists():
        stem += "_mine"
    MY_PROGRAMS.mkdir(exist_ok=True)
    (MY_PROGRAMS / f"{stem}.c").write_bytes(code)
    return stem
