#!/usr/bin/env python3
"""Compare the chip RTL's predictions with the reference models, branch by branch.

    python3 tools/compare_predictions.py traces/sort_and_search_O1 sim_out.txt
    python3 tools/compare_predictions.py traces/sort_and_search_O1 predictions.csv --bidx 5

The RTL output can be the raw simulator log (lines starting with "R,") or predictions.csv
(columns index,pc8,taken,perceptron,bimodal,gshare,tournament[,cycles]).
Prints accuracy from both sides and the first differences per predictor. Exit code 0 = identical.
"""
import argparse, csv, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models"))
import ref_models as R  # noqa: E402

COLS = ["index", "pc8", "taken", *R.PREDICTORS, "cycles"]


def read_rtl(path):
    rows = []
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip()]
    if any(l.startswith("R,") for l in lines):
        lines = [l[2:] for l in lines if l.startswith("R,")]
    if lines and lines[0].split(",")[0].strip().lower() == "index":
        header = [c.strip().lower() for c in lines[0].split(",")]
        lines = lines[1:]
    else:
        header = COLS
    for l in lines:
        v = dict(zip(header, (x.strip() for x in l.split(","))))
        rows.append(v)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace_dir", help="folder with tb_input.hex")
    ap.add_argument("rtl_output", help="sim_out.txt (R, lines) or predictions.csv")
    ap.add_argument("--bidx", type=int, default=6, help="table index bits the RTL was built with (default 6)")
    ap.add_argument("--ghist", type=int, default=8, help="gshare history bits the RTL was built with (default 8)")
    ap.add_argument("--show", type=int, default=5, help="differences to print per predictor")
    a = ap.parse_args()

    trace = [(int(p, 16), int(t)) for p, t in (l.split() for l in open(os.path.join(a.trace_dir, "tb_input.hex")))]
    rtl = read_rtl(a.rtl_output)
    if len(rtl) != len(trace):
        print(f"ROW COUNT: RTL has {len(rtl)} rows, trace has {len(trace)} branches "
              "(is N_BRANCHES or the testbench array too small?)")
    n = min(len(rtl), len(trace))
    # pc8 may be printed in decimal or hex: pick the base that matches the trace
    base = 10
    for b in (10, 16):
        try:
            if all(int(rtl[i]["pc8"], b) == trace[i][0] for i in range(min(n, 200))):
                base = b
                break
        except ValueError:
            continue
    for i in range(n):   # same trace on both sides?
        if int(rtl[i]["taken"]) != trace[i][1] or int(rtl[i]["pc8"], base) != trace[i][0]:
            print(f"TRACE MISMATCH at row {i}: RTL pc8={rtl[i]['pc8']} taken={rtl[i]['taken']}, "
                  f"trace pc8={trace[i][0]} taken={trace[i][1]}. Wrong trace_init.vh?")
            return 2
    model = R.run(trace[:n], bidx=a.bidx, ghist=a.ghist)
    total = 0
    print(f"{n} branches, BIDX={a.bidx} GHIST={a.ghist}")
    print(f"{'predictor':12s} {'RTL':>8s} {'model':>8s} {'differ':>7s}")
    for k in R.PREDICTORS:
        r = [int(rtl[i][k]) for i in range(n)]
        m = model[k]
        diff = [i for i in range(n) if r[i] != m[i]]
        total += len(diff)
        acc = lambda p: 100 * sum(p[i] == trace[i][1] for i in range(n)) / n
        print(f"{k:12s} {acc(r):7.2f}% {acc(m):7.2f}% {len(diff):7d}")
        for i in diff[:a.show]:
            print(f"    branch {i}: pc8=0x{trace[i][0]:02x} taken={trace[i][1]} RTL={r[i]} model={m[i]}")
    cyc = [float(x["cycles"]) for x in rtl[:n] if x.get("cycles") not in (None, "", "nan")]
    if cyc:
        print(f"perceptron clocks per branch: mean {sum(cyc) / len(cyc):.1f}, max {max(cyc):.0f}")
    print("IDENTICAL" if total == 0 and len(rtl) == len(trace) else f"{total} DIFFERENCES")
    return 0 if total == 0 and len(rtl) == len(trace) else 1


if __name__ == "__main__":
    sys.exit(main())
