#!/usr/bin/env python3
"""Replay branch traces through the chip on the Nexys 4 DDR.

    python fpga/host/bphost.py ports
    python fpga/host/bphost.py ping  [--port COM5]
    python fpga/host/bphost.py run   <trace dir> [--port COM5] [--out predictions.csv] [--compare]
    python fpga/host/bphost.py runall [--port COM5]           every trace folder, compare with the models

`run` resets the chip, streams the trace and writes predictions.csv with the same columns as the simulation
(index,pc8,taken,perceptron,bimodal,gshare,tournament,cycles), cycles = clocks from strobe to training_done.
"""
import argparse
import csv
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bplink  # noqa: E402

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
COMPARE = os.path.join(REPO, "verification", "tools", "compare_predictions.py")
TRACE_ROOTS = [os.path.join(REPO, "verification", "traces"),
               os.path.join(REPO, "tools", "precomputed")]
COLS = ["index", "pc8", "taken", "perceptron", "bimodal", "gshare", "tournament", "cycles"]


def load_trace(trace_dir):
    with open(os.path.join(trace_dir, "tb_input.hex")) as f:
        return [(int(p, 16), int(t)) for p, t in (line.split() for line in f if line.strip())]


def open_board(a):
    port = a.port or bplink.find_port()
    if not port:
        sys.exit("no FTDI COM port found: is the board plugged into J6 and switched on? (or pass --port)")
    board = bplink.Board(port, a.baud)
    board.sync()
    return port, board


def run_one(board, trace_dir, out_csv, compare):
    trace = load_trace(trace_dir)
    board.reset()
    ferr0 = board.status()                      # framing errors so far
    t0 = time.perf_counter()
    res = board.run(trace)
    secs = time.perf_counter() - t0
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for i, ((pc, tk), (pe, bi, gs, to, cy)) in enumerate(zip(trace, res)):
            w.writerow([i, f"{pc:02x}", tk, pe, bi, gs, to, cy])
    n = len(trace)
    acc = [100 * sum(r[k] == t[1] for r, t in zip(res, trace)) / n for k in range(4)]
    cyc = sum(r[4] for r in res) / n
    ferr = board.status() - ferr0
    print(f"{os.path.basename(trace_dir)}: {n} branches in {secs:.2f} s ({n / secs:,.0f}/s)  "
          f"perceptron {acc[0]:.2f}  bimodal {acc[1]:.2f}  gshare {acc[2]:.2f}  tournament {acc[3]:.2f}  "
          f"avg_cycles {cyc:.1f}  framing_errors {ferr}")
    if compare:
        r = subprocess.run([sys.executable, "-I", COMPARE, trace_dir, out_csv], capture_output=True, text=True)
        print(r.stdout.rstrip().splitlines()[-1] if r.stdout else r.stderr)
        return r.returncode == 0 and ferr == 0
    return ferr == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["ports", "ping", "run", "runall"])
    ap.add_argument("trace", nargs="?", help="trace folder with tb_input.hex (run)")
    ap.add_argument("--port", help="COM port (default: the FTDI port)")
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--out", help="predictions.csv to write (run; default: <trace>/fpga_predictions.csv)")
    ap.add_argument("--outdir", default=os.path.join(REPO, "fpga", "build", "board"), help="runall output folder")
    ap.add_argument("--compare", action="store_true", help="run compare_predictions.py on the result")
    a = ap.parse_args()

    if a.cmd == "ports":
        for p in bplink.list_ports.comports():
            print(f"{p.device:8s} vid={p.vid and hex(p.vid)} pid={p.pid and hex(p.pid)} {p.description} [{p.location}]")
        print("FTDI UART guess:", bplink.find_port())
        return 0

    port, board = open_board(a)
    try:
        ver = board.ping()
        print(f"{port}: board protocol v{ver}")
        if ver != bplink.VERSION:
            print(f"warning: host speaks v{bplink.VERSION}")
        if a.cmd == "ping":
            print("framing errors since reset:", board.status())
            return 0
        if a.cmd == "run":
            if not a.trace:
                sys.exit("run needs a trace folder")
            return 0 if run_one(board, a.trace, a.out or os.path.join(a.trace, "fpga_predictions.csv"), a.compare) else 1
        # runall
        ok = True
        for root in TRACE_ROOTS:
            for name in sorted(os.listdir(root)):
                d = os.path.join(root, name)
                if os.path.isfile(os.path.join(d, "tb_input.hex")):
                    out = os.path.join(a.outdir, os.path.basename(root), name, "predictions.csv")
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    ok &= run_one(board, d, out, True)
        print("ALL IDENTICAL" if ok else "SOME RUNS DIFFER")
        return 0 if ok else 1
    finally:
        board.close()


if __name__ == "__main__":
    sys.exit(main())
