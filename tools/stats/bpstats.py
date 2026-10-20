"""bpstats: statistics for one run, accuracy, MPKI, per-branch tables and the cost comparison.

Input: a run folder with trace.txt, predictions.csv, branches.csv, summary.json.
Output: stats.json plus DataFrames the app draws from.

    python3 stats/bpstats.py runs/<key>          # writes runs/<key>/stats.json and prints a summary

Joins are by row index, never by pc8: row i of predictions.csv is row i of trace.txt, which
carries the full PC. Two different branches can share PC[7:0] once the code is over 256 bytes.
"""
from __future__ import annotations

import json, sys
from pathlib import Path

import numpy as np
import pandas as pd

PREDICTORS = ("perceptron", "bimodal", "gshare", "tournament")
LABEL = {"perceptron": "Perceptron (704)", "bimodal": "Bimodal", "gshare": "gshare", "tournament": "Tournament"}

# Cost of each predictor on the chip. Storage follows from the design; clocks for the perceptron
# come from predictions.csv when the RTL ran, else from the 704 reference trace (25.6 clocks).
# Area from per-module synthesis with LibreLane (µm², sky130, logic only).
COST = {
    "perceptron": {"storage_bits": 8 * 8 * 8, "clocks": 25.6, "area_um2": 16879,
                   "what": "8 perceptrons x (bias + 7 weights) x 8 bit, latch RAM"},
    "bimodal":    {"storage_bits": 64 * 2, "clocks": 1, "area_um2": 7936, "what": "64 x 2-bit counters"},
    "gshare":     {"storage_bits": 64 * 2 + 8, "clocks": 1, "area_um2": 7948, "what": "64 x 2-bit + 8-bit history"},
    "tournament": {"storage_bits": 64 * 2 * 3 + 8, "clocks": 1, "area_um2": 22948,
                   "what": "chooser 64 x 2-bit + bimodal + gshare"},
}


def load(run_dir) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (dynamic branches with full PC and source line, static branches table, summary)."""
    run_dir = Path(run_dir)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8", errors="replace"))
    tr = pd.read_csv(run_dir / "trace.txt", sep=" ", header=None, names=["pc", "taken"], dtype={"pc": str})
    pr = pd.read_csv(run_dir / "predictions.csv")
    if len(pr) != len(tr):
        raise ValueError(f"predictions.csv has {len(pr)} rows, trace.txt {len(tr)}")
    if not (pr["taken"].to_numpy() == tr["taken"].to_numpy()).all():
        raise ValueError("predictions.csv and trace.txt disagree on outcomes: not the same trace")
    br = pd.read_csv(run_dir / "branches.csv", dtype={"pc": str})
    dyn = pr.copy()
    dyn["pc"] = tr["pc"].str.lower()
    br["pc"] = br["pc"].str.lower()
    dyn = dyn.merge(br[["pc", "function", "line", "mnemonic"]], on="pc", how="left")
    return dyn, br, summary


def per_branch(dyn: pd.DataFrame) -> pd.DataFrame:
    """One row per static branch: executions, bias, transition rate, accuracy per predictor."""
    g = dyn.groupby("pc", sort=False)
    out = pd.DataFrame({
        "function": g["function"].first(), "line": g["line"].first(), "mnemonic": g["mnemonic"].first(),
        "count": g.size(), "taken_rate": g["taken"].mean(),
    })
    trans = dyn.groupby("pc", sort=False)["taken"].apply(lambda s: (s.diff().abs() == 1).sum() / max(len(s) - 1, 1))
    out["transition_rate"] = trans
    for k in PREDICTORS:
        ok = dyn[k] == dyn["taken"]
        out[f"acc_{k}"] = ok.groupby(dyn["pc"], sort=False).mean()
        out[f"miss_{k}"] = (~ok).groupby(dyn["pc"], sort=False).sum()
    out["bias"] = np.maximum(out["taken_rate"], 1 - out["taken_rate"])
    out["line_no"] = pd.to_numeric(out["line"].astype(str).str.rsplit(":", n=1).str[-1], errors="coerce")
    return out.reset_index().sort_values("count", ascending=False, ignore_index=True)


def hard_branches(pb: pd.DataFrame, predictor: str, total_instr: int,
                  min_exec=100, min_miss=10, max_acc=0.99) -> pd.DataFrame:
    """Lin & Tarsa (IISWC 2019) criteria, scaled from 30M instructions down to small programs."""
    h = pb[(pb["count"] >= min_exec) & (pb[f"miss_{predictor}"] >= min_miss) & (pb[f"acc_{predictor}"] < max_acc)]
    h = h.sort_values(f"miss_{predictor}", ascending=False).copy()
    total_miss = pb[f"miss_{predictor}"].sum()
    h["share_of_misses"] = h[f"miss_{predictor}"] / max(total_miss, 1)
    return h


def learning_curve(dyn: pd.DataFrame, window=200, max_points=1500) -> pd.DataFrame:
    """Rolling accuracy over the last `window` branches, thinned to at most max_points rows."""
    lc = pd.DataFrame({"branch": dyn["index"]})
    for k in PREDICTORS:
        lc[k] = (dyn[k] == dyn["taken"]).astype(float).rolling(window, min_periods=1).mean()
    step = max(1, len(lc) // max_points)
    return lc.iloc[::step]


def busiest_window(dyn: pd.DataFrame, width: int, step: int | None = None) -> int:
    """Start of the window where the four predictors disagree most (earliest on a tie), snapped to `step`."""
    step = step or max(1, width // 4)
    p = dyn[list(PREDICTORS)].to_numpy()
    split = (p.min(axis=1) != p.max(axis=1)).astype(int)
    if len(split) <= width or split.sum() == 0:
        return 0
    c = np.concatenate([[0], np.cumsum(split)])
    starts = np.arange(0, len(split) - width + 1, step)
    return int(starts[np.argmax(c[starts + width] - c[starts])])


def agreement(dyn: pd.DataFrame) -> dict:
    """For each pair: share of branches both right, only A right, only B right, both wrong."""
    ok = {k: (dyn[k] == dyn["taken"]).to_numpy() for k in PREDICTORS}
    n = len(dyn)
    out = {}
    for i, a in enumerate(PREDICTORS):
        for b in PREDICTORS[i + 1:]:
            out[f"{a}|{b}"] = {"both_right": float((ok[a] & ok[b]).sum() / n), "only_a": float((ok[a] & ~ok[b]).sum() / n),
                               "only_b": float((~ok[a] & ok[b]).sum() / n), "both_wrong": float((~ok[a] & ~ok[b]).sum() / n)}
    return out


def compute(run_dir, window=200) -> dict:
    dyn, _, summary = load(run_dir)
    pb = per_branch(dyn)
    n = len(dyn)
    instr = int(summary.get("instructions") or 0)
    taken = int(dyn["taken"].sum())
    baseline_dir = "taken" if taken * 2 >= n else "not taken"
    baseline = max(taken, n - taken) / n
    pred = {}
    cyc = pd.to_numeric(dyn.get("cycles"), errors="coerce")
    for k in PREDICTORS:
        miss = int((dyn[k] != dyn["taken"]).sum())
        cost = dict(COST[k])
        if k == "perceptron" and cyc.notna().any():
            cost["clocks"], cost["clocks_source"] = round(float(cyc.mean()), 1), "measured in RTL"
        else:
            cost["clocks_source"] = "1 clock (table)" if k != "perceptron" else "704 reference trace"
        pred[k] = {"accuracy": (n - miss) / n, "mispredictions": miss,
                   "mpki": 1000 * miss / instr if instr else None,
                   "vs_baseline": (n - miss) / n - baseline, **cost}
    best = max(PREDICTORS, key=lambda k: pred[k]["accuracy"])
    hard = hard_branches(pb, best, instr)
    stats = {
        "program": summary.get("program"), "opt": summary.get("opt"), "march": summary.get("march"),
        "instructions": instr, "branches": n, "static_branches": int(len(pb)),
        "truncated": bool(summary.get("truncated")), "taken_rate": taken / n,
        "baseline": {"direction": baseline_dir, "accuracy": baseline},
        "best": best, "predictors": pred,
        "agreement": agreement(dyn),
        "hard_branches": [{"pc": r["pc"], "line": r["line"], "function": r["function"], "count": int(r["count"]),
                           "taken_rate": float(r["taken_rate"]), "transition_rate": float(r["transition_rate"]),
                           **{f"acc_{k}": float(r[f"acc_{k}"]) for k in PREDICTORS},
                           "share_of_misses": float(r["share_of_misses"])} for _, r in hard.iterrows()],
        "per_branch": json.loads(pb.drop(columns=["line_no"]).to_json(orient="records")),
    }
    return stats


def save(run_dir, stats=None) -> Path:
    stats = stats or compute(run_dir)
    p = Path(run_dir) / "stats.json"
    p.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return p


if __name__ == "__main__":
    d = sys.argv[1]
    s = compute(d)
    save(d, s)
    print(f"{s['program']} {s['opt']}: {s['branches']} branches, baseline {s['baseline']['direction']} "
          f"{100 * s['baseline']['accuracy']:.2f}%")
    for k in PREDICTORS:
        p = s["predictors"][k]
        mpki = f"{p['mpki']:.1f}" if p["mpki"] is not None else "n/a"
        print(f"  {k:11s} {100 * p['accuracy']:6.2f}%  MPKI {mpki}")
