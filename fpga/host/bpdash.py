#!/usr/bin/env python3
"""Live dashboard for the branch-predictor chip on the FPGA: step, run, pause, speed, breakpoints, UART log.

    python fpga/host/bpdash.py                 # the board on the FTDI COM port
    python fpga/host/bpdash.py --mock          # no board: mockboard.MockSerial (same protocol, Python models)
    then open http://127.0.0.1:8765

One worker thread owns the serial port and runs the commands the page sends. The reference models run over the whole
trace when it is loaded, so the board is checked branch by branch (the mismatch counter) and breakpoints on a source
line or on a predictor's misprediction stop exactly on that branch, even at full speed.
Standard library only (plus pyserial through bplink); the page gets state snapshots over Server-Sent Events.
"""
import argparse
import collections
import csv
import glob
import json
import os
import queue
import re
import shutil
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import bplink  # noqa: E402
import mockboard  # noqa: E402
R = mockboard.R
PRED = list(R.PREDICTORS)                  # perceptron, bimodal, gshare, tournament
BP_TOOLS = os.path.join(REPO, "tools")
MY_TRACES = os.path.join(BP_TOOLS, "my_traces")     # programs compiled in the dashboard's Compiler view
TRACE_ROOTS = [os.path.join(REPO, "verification", "traces"), os.path.join(BP_TOOLS, "precomputed"), MY_TRACES]
_P = None


def pipeline():
    """The app pipeline (tools/app/pipeline.py): compiles C and runs Spike, in WSL on Windows."""
    global _P
    if _P is None:
        sys.path.insert(0, os.path.join(BP_TOOLS, "app"))
        import pipeline as P
        _P = P
    return _P
ROLL = 200                                 # rolling-accuracy window
SERIES_POINTS = 400


# ------------------------------------------------------------------ traces

# Programs whose C comment doesn't say what to watch
DESCRIPTIONS = {
    "sort_and_search": "Bubble sort of 200 numbers, then 300 binary searches. Lines 10-11 (the sort's loop and compare) "
                       "are 84% of all branches; the compare on line 11 depends on the data and causes most misses. "
                       "Set a breakpoint on line 20 to jump to the binary search.",
    "ref704_committed_log": "The 704 design's own reference program, from its committed Spike log (no C source here). "
                            "The published result: perceptron 81.98%.",
    # Measured on the chip RTL: the C header's prediction was wrong
    "pattern_aliasing": "24 branches that always go the same way. The perceptron and bimodal get all of them right. "
                        "gshare doesn't: two branches that want opposite directions hash to the same counter on every "
                        "iteration, so gshare gets one of them wrong every time. The perceptron shares 8 perceptrons "
                        "among 24 branches and still tells them apart: the fixed history before each branch is a "
                        "fingerprint of where it is in the loop.",
    "pattern_correlated": "Line 18 repeats line 16's random outcome: bimodal still sees a coin flip (~50%), gshare and "
                          "the perceptron find line 16's outcome in their history (~100%). Only at -O0: at -O1 and -O2 "
                          "GCC merges the two tests, so the second branch disappears.",
}


def _describe(path):
    """What the program shows (for the program picker): DESCRIPTIONS, else the first /* ... */ comment of its C file."""
    prog = re.sub(r"_(O[0-3s])$", "", os.path.basename(path))
    if prog in DESCRIPTIONS:
        return DESCRIPTIONS[prog]
    srcs = glob.glob(os.path.join(path, "*.c"))
    if not srcs:
        return ""
    text = open(srcs[0], encoding="utf-8", errors="replace").read()
    m = re.search(r"/\*(.*?)\*/", text, re.S)
    return " ".join(m.group(1).split()) if m else ""


def list_traces():
    """Every trace folder, newest root last, with its program name, optimisation level and description.
    A folder name that appears in more than one root is listed once (the one with the C source wins)."""
    seen, out = {}, []
    for root in TRACE_ROOTS:
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            if not os.path.isfile(os.path.join(d, "tb_input.hex")):
                continue
            name = os.path.basename(d)
            m = re.match(r"(.+)_(O[0-3s])$", name)
            t = {"key": f"{os.path.basename(root)}/{name}", "path": d,
                 "has_source": bool(glob.glob(os.path.join(d, "*.c"))), "mine": root == MY_TRACES,
                 "program": m.group(1) if m else name, "opt": m.group(2) if m else "", "desc": _describe(d)}
            if name in seen:
                if t["has_source"] and not out[seen[name]]["has_source"]:
                    out[seen[name]] = t
                continue
            seen[name] = len(out)
            out.append(t)
    return out


class Trace:
    def __init__(self, key, path):
        self.key, self.path = key, path
        with open(os.path.join(path, "tb_input.hex")) as f:
            self.br = [(int(p, 16), int(t)) for p, t in (l.split() for l in f if l.strip())]
        self.n = len(self.br)
        self.pc = None
        if os.path.isfile(os.path.join(path, "trace.txt")):
            with open(os.path.join(path, "trace.txt")) as f:
                self.pc = [int(l.split()[0], 16) for l in f if l.strip()]
        self.static = {}                    # full pc -> function, file:line, mnemonic
        if os.path.isfile(os.path.join(path, "branches.csv")):
            with open(os.path.join(path, "branches.csv"), newline="") as f:
                for r in csv.DictReader(f):
                    self.static[int(r["pc"], 16)] = r
        srcs = glob.glob(os.path.join(path, "*.c"))
        self.source_name = os.path.basename(srcs[0]) if srcs else None
        self.source = open(srcs[0], encoding="utf-8", errors="replace").read().splitlines() if srcs else []
        self.instructions = None
        if os.path.isfile(os.path.join(path, "summary.json")):
            self.instructions = json.load(open(os.path.join(path, "summary.json"))).get("instructions")
        # source line of every dynamic branch (0 = unknown or outside the source file)
        self.line = [0] * self.n
        if self.pc and self.source_name:
            for i, p in enumerate(self.pc[:self.n]):
                s = self.static.get(p)
                if s and s["line"].rsplit(":", 1)[0] == self.source_name and s["line"].rsplit(":", 1)[-1].isdigit():
                    self.line[i] = int(s["line"].rsplit(":", 1)[1])
        # sections for "Jump to": the functions, in the order the program first reaches them
        self.sections = []
        index = {}
        for i in range(self.n):
            s = self.static.get(self.pc[i]) if self.pc else None
            fn = s["function"] if s else None
            if not fn:
                continue
            if fn not in index:
                index[fn] = len(self.sections)
                self.sections.append({"function": fn, "first": i, "line": self.line[i], "count": 0})
            self.sections[index[fn]]["count"] += 1
        t0 = time.time()
        m = R.run(self.br)
        self.model = [tuple(m[k][i] for k in PRED) for i in range(self.n)]
        self.model_secs = time.time() - t0

    def info(self, i):
        if i < 0 or i >= self.n:
            return None
        pc8, tk = self.br[i]
        d = {"i": i, "pc8": pc8, "taken": tk, "line": self.line[i], "model": self.model[i]}
        if self.pc:
            d["pc"] = f"{self.pc[i]:08x}"
            s = self.static.get(self.pc[i])
            if s:
                d.update(function=s["function"], where=s["line"], mnemonic=s["mnemonic"])
        return d


# ------------------------------------------------------------------ run state and the worker

class Dash:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.cmds = queue.Queue()
        self.version = 0
        self.board = None
        self.conn = {"kind": None, "port": None, "version": None}
        self.trace = None
        self.mode = "paused"
        self.speed = 20                    # branches per second, 0 = as fast as the link goes
        self.breaks = {"lines": [], "mispredict": None, "mismatch": True}
        self.msg, self.err = "", ""
        self.log = collections.deque(maxlen=400)
        self.log_seq = 0                           # numbers every log entry, so the page can append new ones
        self.tx_bytes = self.rx_bytes = 0
        self._resume = False
        self._pace = (None, 0, 0)                  # (speed, start time, start index) of the paced run
        self.comp = {"state": "idle", "name": "", "log": [], "results": []}   # the Compiler view's job
        self.toolchain = {"ok": None, "why": "checking for the RISC-V compiler and Spike..."}
        self._clear()

    def _clear(self):
        n = self.trace.n if self.trace else 0
        self.i = 0
        self.res = [None] * n
        self.correct = dict.fromkeys(PRED, 0)
        self.mism = dict.fromkeys(PRED, 0)
        self.first_mismatch = None
        self.cyc_hist = collections.Counter()
        self.cyc_sum = 0
        self.lines = {}                    # line -> [count, miss per predictor...]
        self.roll = collections.deque()
        self.roll_sum = [0, 0, 0, 0]
        self.series = []
        self.stride = max(1, n // SERIES_POINTS)
        self.run_secs = 0.0
        self.run_count = 0
        self.taken_n = 0
        self.static = {}                   # branch address -> [count, taken, flips, last outcome, miss per predictor...]
        self.agree = {f"{a}|{b}": [0, 0, 0, 0] for i, a in enumerate(PRED) for b in PRED[i + 1:]}
                                           # both right, only first right, only second right, both wrong

    # --- bookkeeping, called with the lock held
    def _account(self, j, r):
        tr = self.trace
        tk = tr.br[j][1]
        self.res[j] = r
        ok = [int(r[k] == tk) for k in range(4)]
        for k, name in enumerate(PRED):
            self.correct[name] += ok[k]
            if r[k] != tr.model[j][k]:
                self.mism[name] += 1
                if self.first_mismatch is None:
                    self.first_mismatch = j
        self.cyc_hist[r[4]] += 1
        self.cyc_sum += r[4]
        ln = tr.line[j]
        if ln:
            row = self.lines.setdefault(ln, [0, 0, 0, 0, 0])
            row[0] += 1
            for k in range(4):
                row[1 + k] += 1 - ok[k]
        self.taken_n += tk
        key = tr.pc[j] if tr.pc else tr.br[j][0]
        st = self.static.setdefault(key, [0, 0, 0, -1, 0, 0, 0, 0])
        st[0] += 1
        st[1] += tk
        st[2] += int(st[3] >= 0 and st[3] != tk)
        st[3] = tk
        for k in range(4):
            st[4 + k] += 1 - ok[k]
        for pair, cell in self.agree.items():
            a, b = pair.split("|")
            oa, ob = ok[PRED.index(a)], ok[PRED.index(b)]
            cell[0 if oa and ob else 1 if oa else 2 if ob else 3] += 1
        self.roll.append(ok)
        for k in range(4):
            self.roll_sum[k] += ok[k]
        if len(self.roll) > ROLL:
            old = self.roll.popleft()
            for k in range(4):
                self.roll_sum[k] -= old[k]
        self.i = j + 1
        if self.i % self.stride == 0 or self.i == tr.n:
            self._point()

    def _point(self):
        n = self.i
        if n == 0:
            return
        self.series.append([n] + [round(100 * self.correct[k] / n, 2) for k in PRED]
                           + [round(100 * s / len(self.roll), 2) for s in self.roll_sum])

    def _log(self, kind, i, tx, rx, text):
        self.log_seq += 1
        self.log.append({"seq": self.log_seq, "t": round(time.time(), 3), "kind": kind, "i": i, "tx": tx.hex(" "),
                         "rx": rx.hex(" "), "text": text})
        self.tx_bytes += len(tx)
        self.rx_bytes += len(rx)

    def _bump(self):
        self.version += 1
        self.changed.notify_all()

    # --- snapshot for the page
    def snapshot(self):
        tr = self.trace
        n = self.i
        snap = {"v": self.version, "mode": self.mode, "speed": self.speed, "msg": self.msg, "err": self.err,
                "compile": self.comp, "toolchain": self.toolchain,
                "conn": self.conn, "breaks": self.breaks, "i": n, "tx_bytes": self.tx_bytes, "rx_bytes": self.rx_bytes,
                "rate": round(self.run_count / self.run_secs) if self.run_secs > 0.05 else None,
                "log": list(self.log)[-150:]}
        if tr:
            snap["trace"] = {"key": tr.key, "n": tr.n, "source_name": tr.source_name, "instructions": tr.instructions}
            snap["correct"] = self.correct
            snap["acc"] = {k: round(100 * self.correct[k] / n, 2) if n else None for k in PRED}
            ins = tr.instructions * n / tr.n if tr.instructions and tr.n else None
            snap["mpki"] = {k: round(1000 * (n - self.correct[k]) / ins, 2) if ins else None for k in PRED}
            snap["mismatch"] = self.mism
            snap["mismatch_total"] = sum(self.mism.values())
            snap["first_mismatch"] = self.first_mismatch
            snap["cycles"] = {"hist": {str(c): v for c, v in sorted(self.cyc_hist.items())},
                              "avg": round(self.cyc_sum / n, 2) if n else None}
            snap["lines"] = self.lines
            snap["baseline"] = ({"acc": round(100 * max(self.taken_n, n - self.taken_n) / n, 2),
                                 "dir": "taken" if self.taken_n * 2 >= n else "not taken"} if n else None)
            snap["agree"] = self.agree
            rows = []
            for key, st in sorted(self.static.items(), key=lambda kv: -kv[1][0])[:60]:
                info = tr.static.get(key, {}) if tr.pc else {}
                rows.append({"pc": f"{key:08x}" if tr.pc else f"{key:02x}", "where": info.get("line", ""),
                             "function": info.get("function", ""), "count": st[0],
                             "taken": round(st[1] / st[0], 4), "flips": round(st[2] / max(1, st[0] - 1), 4),
                             "miss": st[4:8]})
            snap["static"] = rows
            snap["series"] = self.series + ([[n] + snap_point(self)] if n and (not self.series or self.series[-1][0] != n) else [])
            last = tr.info(n - 1)
            if last is not None:
                last["board"] = self.res[n - 1]
            snap["last"] = last
            snap["next"] = tr.info(n)
        return snap

    # --- commands (run on the worker thread)
    def connect(self, mock=None, port=None):
        if self.board:
            self.board.close()
            self.board = None
        mock = self.args.mock if mock is None else mock
        if mock:
            self.board = bplink.Board(None, ser=mockboard.MockSerial())
            port = "mock"
        else:
            port = port or self.args.port or bplink.find_port()
            if not port:
                raise RuntimeError("no FTDI COM port: is the board plugged into J6 and switched on?")
            self.board = bplink.Board(port, self.args.baud)
        self.board.sync()
        ver = self.board.ping()
        with self.lock:
            self.conn = {"kind": "mock" if mock else "board", "port": port, "version": ver}
            seq = (self.board.seq - 1) & 7
            self._log("PING", None, bplink.encode(seq, bplink.OP_PING), bytes([0x80 | seq << 4 | ver, 0x50]),
                      f"{port}: protocol v{ver}")

    def load(self, key):
        traces = list_traces()                   # by key, or by folder name (duplicates are listed once)
        t = (next((t for t in traces if t["key"] == key), None)
             or next((t for t in traces if os.path.basename(t["path"]) == key.split("/")[-1]), None))
        if t is None:
            raise RuntimeError(f"unknown trace {key}")
        tr = Trace(t["key"], t["path"])
        with self.lock:
            self.trace = tr
            self.mode = "paused"
            self.breaks["lines"] = []
        self.reset()

    def reset(self):
        if self.board:
            self.board.reset()
        with self.lock:
            self._clear()
            self.mode = "paused"
            self.msg = "chip reset" if self.board else "loaded (not connected)"
            if self.board:
                seq = (self.board.seq - 1) & 7
                self._log("RESET", None, bplink.encode(seq, bplink.OP_RESET), bytes([0x80 | seq << 4, 0x52]),
                          "chip reset, RAM cleared")

    def _next_stop(self, start, end, resume, breaks=True):
        """First index in [start, end) to stop before (line breakpoint) or after (model misprediction)."""
        tr = self.trace
        if not breaks:
            return end, None
        lines = set(self.breaks["lines"])
        k = PRED.index(self.breaks["mispredict"]) if self.breaks["mispredict"] in PRED else None
        for j in range(start, end):
            if lines and tr.line[j] in lines and not (resume and j == start):
                return j, f"breakpoint: line {tr.line[j]}"
            if k is not None and tr.model[j][k] != tr.br[j][1]:
                return j + 1, f"{PRED[k]} mispredicted branch {j}"
        return end, None

    def execute(self, count, resume=False, breaks=True):
        """Run up to `count` branches through the board, honouring breakpoints. Returns why it stopped, or None."""
        tr = self.trace
        start = self.i
        end, why = self._next_stop(start, min(tr.n, start + count), resume, breaks)
        if end <= start:
            return why
        b = self.board
        t0 = time.perf_counter()
        if end - start == 1:
            pc, tk = tr.br[start]
            r, tx, rx = b.branch(pc, tk)
            recs = [(start, r, tx, rx)]
        else:
            seq0 = b.seq
            raw = []
            res = b.run(tr.br[start:end], raw=raw)
            recs = []
            for off in range(max(0, len(res) - 24), len(res)):     # keep only the last bytes for the log
                j = start + off
                recs.append((j, res[off], bplink.encode(seq0 + off, bplink.OP_BRANCH, *tr.br[j]), bytes(raw[off])))
            full = res
        dt = time.perf_counter() - t0
        with self.lock:
            if end - start == 1:
                self._account(start, recs[0][1])
            else:
                for off, r in enumerate(full):
                    self._account(start + off, r)
                self.tx_bytes += 2 * (end - start - len(recs))
                self.rx_bytes += 2 * (end - start - len(recs))
            for j, r, tx, rx in recs:
                pc8, tk = tr.br[j]
                bad = "  BOARD != MODEL" if r[:4] != tr.model[j] else ""
                self._log("BRANCH", j, tx, rx, f"pc8 {pc8:02x} {'T' if tk else 'N'} -> perc {r[0]} bim {r[1]} "
                          f"gsh {r[2]} tour {r[3]}, {r[4]} clk{bad}")
            self.run_secs += dt
            self.run_count += end - start
        if breaks and self.first_mismatch is not None and self.breaks["mismatch"] and self.first_mismatch >= start:
            return f"board differs from the models at branch {self.first_mismatch}"
        if self.i >= tr.n:
            return "end of trace"
        return why

    def goto(self, target):
        """Jump to branch `target`. The chip cannot run backwards, but it is deterministic: going back resets it and
        replays the first `target` branches at full speed, which leaves it in exactly the state it had there."""
        tr = self.trace
        target = max(0, min(tr.n, int(target)))
        t0 = time.perf_counter()
        back = target < self.i
        if back:
            self.reset()
        if target > self.i:
            self.execute(target - self.i, breaks=False)
        with self.lock:
            self.mode = "paused"
            secs = time.perf_counter() - t0
            self.msg = (f"back to branch {target}: chip reset and {target:,} branches replayed in {secs:.2f} s" if back
                        else f"jumped to branch {target}")
            self._log("GOTO", target, b"", b"", self.msg)

    # --- Compiler view: runs on its own thread, so the board keeps working meanwhile
    def check_toolchain(self):
        try:
            ok, why = pipeline().toolchain_status()
        except Exception as e:
            ok, why = False, f"cannot load the app pipeline: {e}"
        with self.lock:
            self.toolchain = {"ok": ok, "why": why}
            self._bump()

    def compile_program(self, name, code, opts, user_only):
        def say(text, kind="info"):
            with self.lock:
                self.comp["log"].append({"t": round(time.time(), 3), "kind": kind, "text": text})
                self._bump()

        with self.lock:
            self.comp = {"state": "running", "name": name, "log": [], "results": []}
            self._bump()
        built = []
        try:
            P = pipeline()
            stem = P.save_program(name + ".c", code.encode("utf-8"))
            src = P.MY_PROGRAMS / f"{stem}.c"
            say(f"saved as my_programs/{stem}.c" + (f" (renamed: an example is called {name})" if stem != name else ""))
            with self.lock:
                self.comp["name"] = stem
            for opt in opts:
                say(f"-{opt}: compiling with riscv64-unknown-elf-gcc, then running on Spike"
                    + (" (in WSL)" if os.name == "nt" else ""))
                out = os.path.join(MY_TRACES, f"{stem}_{opt}")
                tmp = out + ".building"
                shutil.rmtree(tmp, ignore_errors=True)
                t0 = time.time()
                try:
                    s = P.build_trace(src, P.Path(tmp), opt=opt, user_only=user_only)
                except P.CompileError as e:
                    say(str(e), "error")
                    say("compile error: fix the code and press Compile again", "error")
                    shutil.rmtree(tmp, ignore_errors=True)
                    break                       # the same error at every level
                except P.SpikeError as e:
                    say(f"-{opt}: {e}", "error")
                    shutil.rmtree(tmp, ignore_errors=True)
                    continue
                if not s.get("branches"):
                    say(f"-{opt}: it ran, but executed no conditional branches"
                        + (" in this file" if user_only else ""), "error")
                    shutil.rmtree(tmp, ignore_errors=True)
                    continue
                for junk in ("trace.bin", "spike.log"):
                    try:
                        os.remove(os.path.join(tmp, junk))
                    except OSError:
                        pass
                shutil.copy2(src, os.path.join(tmp, f"{stem}.c"))
                open(os.path.join(tmp, "PRECOMPUTED"), "w").close()
                shutil.rmtree(out, ignore_errors=True)
                os.replace(tmp, out)
                r = {"opt": opt, "branches": s["branches"], "instructions": s.get("instructions"),
                     "static": s.get("static_branches"), "result": s.get("main_result"),
                     "secs": round(time.time() - t0, 1), "key": f"my_traces/{stem}_{opt}"}
                built.append(r)
                with self.lock:
                    self.comp["results"].append(r)
                say(f"-{opt}: {r['branches']:,} branches ({r['static']} in the code), {r['instructions']:,} instructions, "
                    f"main() returned {r['result']}, {r['secs']} s", "ok")
            if built:
                pick = next((r for r in built if r["opt"] == ("O0" if stem.startswith("pattern_") else "O1")), built[0])
                say(f"done: {stem} is in the Program list (Your programs); loading it at -{pick['opt']}", "ok")
                self.cmds.put({"cmd": "load", "trace": pick["key"]})
        except Exception as e:
            say(f"{type(e).__name__}: {e}", "error")
            traceback.print_exc()
        with self.lock:
            self.comp["state"] = "ok" if built else "error"
            self._bump()

    def my_programs(self):
        P = pipeline()
        out = []
        for f in sorted(P.MY_PROGRAMS.glob("*.c")):
            opts = sorted(os.path.basename(d).rsplit("_", 1)[1]
                          for d in glob.glob(os.path.join(MY_TRACES, f"{f.stem}_O*")) if not d.endswith(".building"))
            out.append({"name": f.stem, "opts": opts, "mtime": int(f.stat().st_mtime)})
        return out

    def worker(self):
        while True:
            try:
                if self.mode == "running":
                    try:
                        cmd = self.cmds.get_nowait()
                    except queue.Empty:
                        cmd = None
                else:
                    cmd = self.cmds.get()
                if cmd:
                    self.handle(cmd)
                elif self.mode == "running":
                    self.tick()
            except Exception as e:
                with self.lock:
                    self.mode = "paused"
                    self.err = f"{type(e).__name__}: {e}"
                    self._bump()
                traceback.print_exc()

    def tick(self):
        if not self.board or not self.trace:
            self.mode = "paused"
            return
        now = time.perf_counter()
        if self.speed != self._pace[0]:                 # (re)start the clock when the run or the speed starts
            self._pace = (self.speed, now, self.i)
        speed, t0, i0 = self._pace
        if speed == 0:
            count, left = 4096, 0
        else:
            due = i0 + int(speed * (now - t0)) + 1 - self.i     # branches owed by now
            count = min(due, max(1, speed // 10))
            left = (self.i - i0) / speed - (now - t0) if due <= 0 else 0   # until the next one is due
        if count > 0:
            why = self.execute(count, resume=self._resume)
            self._resume = False
            with self.lock:
                if why:
                    self.mode = "paused"
                    self.msg = why
                self._bump()
        if left > 0 and self.mode == "running":
            try:                                   # wake early for a command
                cmd = self.cmds.get(timeout=left)
                self.handle(cmd)
            except queue.Empty:
                pass

    def handle(self, c):
        op = c.get("cmd")
        self.err = ""
        if op == "connect":
            self.connect(c.get("mock"), c.get("port"))
            if self.trace:
                self.reset()
        elif op == "load":
            self.load(c["trace"])
        elif op == "reset":
            self.reset()
        elif op in ("step", "run"):
            if not self.board:
                raise RuntimeError("not connected")
            if not self.trace:
                raise RuntimeError("no trace loaded")
            if op == "step":
                why = self.execute(int(c.get("n", 1)), resume=True)
                with self.lock:
                    self.mode = "paused"
                    self.msg = why or f"stepped to branch {self.i}"
            else:
                with self.lock:
                    self.mode = "running"
                    self.msg = "running"
                self._resume = True
                self._pace = (None, 0, 0)
        elif op in ("back", "goto"):
            if not self.board or not self.trace:
                raise RuntimeError("not connected" if not self.board else "no trace loaded")
            self.goto(self.i - int(c.get("n", 1)) if op == "back" else c["target"])
        elif op == "pause":
            with self.lock:
                self.mode = "paused"
                self.msg = f"paused at branch {self.i}"
        elif op == "speed":
            with self.lock:
                self.speed = max(0, int(c["value"]))
        elif op == "breaks":
            with self.lock:
                for k in ("lines", "mispredict", "mismatch"):
                    if k in c:
                        self.breaks[k] = c[k]
        with self.lock:
            self._bump()


def snap_point(d):
    n = d.i
    return [round(100 * d.correct[k] / n, 2) for k in PRED] + [round(100 * s / len(d.roll), 2) for s in d.roll_sum]


# ------------------------------------------------------------------ HTTP

def make_handler(dash):
    page = os.path.join(HERE, "dashboard.html")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                b = open(page, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            elif self.path == "/api/traces":
                self._json([{k: t[k] for k in ("key", "has_source", "program", "opt", "desc", "mine")} for t in list_traces()])
            elif self.path == "/api/myprograms":
                self._json(dash.my_programs())
            elif self.path.startswith("/api/myprogram?name="):
                name = re.sub(r"[^A-Za-z0-9_]", "", self.path.split("=", 1)[1])
                f = pipeline().MY_PROGRAMS / f"{name}.c"
                self._json({"name": name, "code": f.read_text(encoding="utf-8", errors="replace")} if f.exists() else {})
            elif self.path == "/api/source":
                tr = dash.trace
                self._json({"name": tr.source_name, "lines": tr.source, "key": tr.key, "sections": tr.sections,
                            "branch_lines": sorted(set(l for l in tr.line if l))} if tr else {})
            elif self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                seen = -1
                try:
                    while True:
                        with dash.lock:
                            dash.changed.wait_for(lambda: dash.version != seen, timeout=1.0)
                            seen = dash.version
                            snap = dash.snapshot()
                        self.wfile.write(b"data: " + json.dumps(snap).encode() + b"\n\n")
                        self.wfile.flush()
                        time.sleep(0.1)                         # at most 10 updates per second
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path not in ("/api/cmd", "/api/compile"):
                self.send_error(404)
                return
            n = int(self.headers.get("Content-Length", 0))
            c = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/compile":
                name = re.sub(r"[^A-Za-z0-9_]", "_", (c.get("name") or "").strip()) or "my_program"
                opts = [o for o in ("O0", "O1", "O2") if o in (c.get("opts") or [])] or ["O0"]
                if dash.comp.get("state") == "running":
                    self._json({"ok": False, "why": "a compile is already running"}, 409)
                elif not dash.toolchain.get("ok"):
                    self._json({"ok": False, "why": dash.toolchain.get("why")}, 409)
                else:
                    threading.Thread(target=dash.compile_program, daemon=True,
                                     args=(name, c.get("code") or "", opts, bool(c.get("user_only", True)))).start()
                    self._json({"ok": True})
                return
            if c.get("cmd") == "pause":                         # pause and speed act at once, even mid-run
                with dash.lock:
                    dash.mode = "paused"
                    dash.msg = f"paused at branch {dash.i}"
                    dash._bump()
            elif c.get("cmd") == "speed":
                with dash.lock:
                    dash.speed = max(0, int(c["value"]))
                    dash._bump()
            else:
                dash.cmds.put(c)
            self._json({"ok": True})

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true", help="no board: simulate it with the reference models")
    ap.add_argument("--port", help="COM port (default: the FTDI port)")
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--trace", default="precomputed/sort_and_search_O1", help="trace to load at start")
    ap.add_argument("--http", type=int, default=8765)
    a = ap.parse_args()
    dash = Dash(a)
    threading.Thread(target=dash.worker, daemon=True).start()
    threading.Thread(target=dash.check_toolchain, daemon=True).start()
    dash.cmds.put({"cmd": "connect"})
    dash.cmds.put({"cmd": "load", "trace": a.trace})
    handler = make_handler(dash)
    # Listen on both loopback addresses: browsers try ::1 first for "localhost", and on Windows a refused IPv6
    # connect costs hundreds of milliseconds per request, which made single steps slow.
    try:
        srv6 = Server6(("::1", a.http), handler)
        srv6.daemon_threads = True
        threading.Thread(target=srv6.serve_forever, daemon=True).start()
    except OSError as e:
        print("IPv6 loopback not available:", e)
    srv = ThreadingHTTPServer(("127.0.0.1", a.http), handler)
    srv.daemon_threads = True
    print(f"dashboard on http://localhost:{a.http}  ({'mock board' if a.mock else 'board'})", flush=True)
    srv.serve_forever()


class Server6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


if __name__ == "__main__":
    main()
