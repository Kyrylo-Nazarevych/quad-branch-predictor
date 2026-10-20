#!/usr/bin/env python3
"""Accuracy of the table predictors for each table size (BIDX) and gshare history length (GHIST).

    python3 tools/sweep_params.py traces/*          # all traces in the hand-off
    python3 tools/sweep_params.py traces/sort_and_search_O1 --bidx 5 6 --ghist 6 8

Runs on the reference models (seconds), so you can pick parameters before touching the RTL
and see what the area fallback BIDX=5 costs. The perceptron does not depend on
these parameters and is listed once per trace for comparison.

Pattern traces are also scored on their pattern branch alone (the line in FOCUS below), which is
how the pattern table is defined.
"""
import argparse, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models"))
import ref_models as R  # noqa: E402

FOCUS = {"pattern_alternating": "pattern_alternating.c:7", "pattern_xor": "pattern_xor.c:17"}


def load(d):
    tr = [(int(p, 16), int(t)) for p, t in (l.split() for l in open(os.path.join(d, "trace.txt")))]
    lines = {}
    bc = os.path.join(d, "branches.csv")
    if os.path.exists(bc):
        for row in open(bc).read().splitlines()[1:]:
            c = row.split(",")
            lines[int(c[0], 16)] = c[6]
    return tr, lines


def acc(pred, tr, keep=None):
    sel = [(p, t) for p, (pc, t) in zip(pred, tr) if keep is None or keep(pc)]
    return 100 * sum(p == t for p, t in sel) / len(sel) if sel else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--bidx", type=int, nargs="+", default=[4, 5, 6])
    ap.add_argument("--ghist", type=int, nargs="+", default=[4, 6, 8])
    a = ap.parse_args()
    for d in a.traces:
        if not os.path.exists(os.path.join(d, "trace.txt")):
            continue
        tr, lines = load(d)
        name = os.path.basename(os.path.normpath(d))
        focus = next((v for k, v in FOCUS.items() if name.startswith(k)), None)
        keep = (lambda pc: lines.get(pc) == focus) if focus else None
        base = R.run([(pc & 0xFF, t) for pc, t in tr])
        print(f"\n{name}: {len(tr)} branches" + (f"  (second number: branch at {focus} only)" if focus else ""))
        fmt = lambda p: f"{acc(p, tr):6.2f}" + (f" / {acc(p, tr, keep):6.2f}" if focus else "")
        print(f"  perceptron (any setting)      {fmt(base['perceptron'])}")
        print(f"  {'BIDX':>4s} {'entries':>7s} {'GHIST':>5s}   {'bimodal':>{15 if focus else 7}s}  "
              f"{'gshare':>{15 if focus else 7}s}  {'tournament':>{15 if focus else 10}s}  storage bits")
        for b in a.bidx:
            for g in a.ghist:
                p = R.run([(pc & 0xFF, t) for pc, t in tr], bidx=b, ghist=g)
                bits = 3 * 2 * (1 << b) + g
                print(f"  {b:4d} {1 << b:7d} {g:5d}   {fmt(p['bimodal'])}  {fmt(p['gshare'])}  {fmt(p['tournament'])}  {bits:6d}")


if __name__ == "__main__":
    main()
