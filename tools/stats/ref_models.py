"""Reference models of the four predictors on the tt_um_bp_team chip.

Written from the hardware's rules, then checked against every accuracy the reference RTL
publishes (704 reference log, sort_and_search -O1, alternating, loop of 4, XOR):
    python3 stats/ref_models.py --selftest
They are the app's fallback when the chip RTL (Icarus + hw_reference/) is not available,
and a cross-check when it is.

Rules (as in the hardware):
  * Input per branch: pc8 = PC[7:0] and the real outcome. Tables index with PC[7:2] (64 entries).
  * 2-bit counters start at 2 ("weakly taken"); predict taken when counter >= 2.
  * gshare: index = PC[7:2] XOR fold(GHR), GHR = 8 bits folded to 6 by XORing GHR[7:6] onto GHR[1:0].
  * Tournament chooser: 2-bit, starts at 1 ("weakly bimodal"); >= 2 picks gshare. Trained only
    when bimodal and gshare disagree, towards the one that was right.
  * 704 perceptron: 8 perceptrons (index PC[4:2]) x (bias + 7 weights), 8-bit wrapping weights,
    7-bit global history, inputs 0/1 for the prediction and +-1 for training, threshold 15,
    10-bit wrapping sum, predict taken when sum >= 0.
  * Every prediction is made before that branch trains anything.
"""
from __future__ import annotations

PREDICTORS = ("perceptron", "bimodal", "gshare", "tournament")


def _wrap(v: int, bits: int) -> int:
    m = 1 << bits
    v &= m - 1
    return v - m if v >= m >> 1 else v


def _sat(c: int, taken: int) -> int:
    return min(c + 1, 3) if taken else max(c - 1, 0)


class Bimodal:
    def __init__(self, bidx: int = 6):
        self.mask = (1 << bidx) - 1
        self.t = [2] * (1 << bidx)

    def idx(self, pc8):
        return (pc8 >> 2) & self.mask

    def predict(self, pc8):
        return int(self.t[self.idx(pc8)] >= 2)

    def update(self, pc8, taken):
        i = self.idx(pc8)
        self.t[i] = _sat(self.t[i], taken)


class Gshare:
    def __init__(self, bidx: int = 6, ghist: int = 8):
        self.bidx, self.ghist = bidx, ghist
        self.mask = (1 << bidx) - 1
        self.t = [2] * (1 << bidx)
        self.ghr = 0

    def folded(self):
        g, out = self.ghr, 0
        while g:
            out ^= g & self.mask
            g >>= self.bidx
        return out

    def idx(self, pc8):
        return ((pc8 >> 2) & self.mask) ^ self.folded()

    def predict(self, pc8):
        return int(self.t[self.idx(pc8)] >= 2)

    def update(self, pc8, taken):
        i = self.idx(pc8)
        self.t[i] = _sat(self.t[i], taken)
        self.ghr = ((self.ghr << 1) | taken) & ((1 << self.ghist) - 1)


class Tournament:
    """Chooser over its own bimodal and gshare tables (the chip shares them; results are equal)."""

    def __init__(self, bidx: int = 6, ghist: int = 8):
        self.b, self.g = Bimodal(bidx), Gshare(bidx, ghist)
        self.mask = (1 << bidx) - 1
        self.c = [1] * (1 << bidx)

    def predict(self, pc8):
        return self.g.predict(pc8) if self.c[(pc8 >> 2) & self.mask] >= 2 else self.b.predict(pc8)

    def update(self, pc8, taken):
        pb, pg = self.b.predict(pc8), self.g.predict(pc8)
        if pb != pg:
            i = (pc8 >> 2) & self.mask
            self.c[i] = _sat(self.c[i], pg == taken)
        self.b.update(pc8, taken)
        self.g.update(pc8, taken)


class Perceptron704:
    HIST, THETA, WBITS, YBITS, N = 7, 15, 8, 10, 8

    def __init__(self):
        self.w = [[0] * (self.HIST + 1) for _ in range(self.N)]
        self.h = [0] * self.HIST          # h[0] = most recent outcome

    def _y(self, pc8):
        w = self.w[(pc8 >> 2) % self.N]
        y = w[0]
        for i in range(self.HIST):
            y = _wrap(y + w[i + 1] * self.h[i], self.YBITS)
        return y

    def predict(self, pc8):
        return int(self._y(pc8) >= 0)

    def update(self, pc8, taken):
        y = self._y(pc8)
        if int(y >= 0) != taken or abs(y) <= self.THETA:
            w = self.w[(pc8 >> 2) % self.N]
            t = 1 if taken else -1
            w[0] = _wrap(w[0] + t, self.WBITS)
            for i in range(self.HIST):
                w[i + 1] = _wrap(w[i + 1] + t * (1 if self.h[i] else -1), self.WBITS)
        self.h = [taken] + self.h[:-1]


def make_all(bidx: int = 6, ghist: int = 8):
    return {"perceptron": Perceptron704(), "bimodal": Bimodal(bidx),
            "gshare": Gshare(bidx, ghist), "tournament": Tournament(bidx, ghist)}


def run(trace, bidx: int = 6, ghist: int = 8):
    """trace: iterable of (pc8, taken). Returns {predictor: [prediction per branch]}."""
    models = make_all(bidx, ghist)
    out = {k: [] for k in models}
    for pc8, tk in trace:
        for k, m in models.items():
            out[k].append(m.predict(pc8))
        for m in models.values():
            m.update(pc8, tk)
    return out


def _selftest(kit, t704):
    """Compare with the accuracies the reference RTL produces."""
    import os, re, subprocess, sys, tempfile
    published = {   # trace -> (branch filter, {predictor: accuracy %})
        "704 reference log": (None, dict(perceptron=81.98, bimodal=66.88, gshare=86.47, tournament=88.83)),
        "sort_and_search -O1": (None, dict(perceptron=84.48, bimodal=84.07, gshare=82.83, tournament=83.48)),
        "pattern_alternating -O0": ("pattern_alternating.c:7", dict(perceptron=99.9, bimodal=50.0, gshare=99.9, tournament=99.9)),
        "pattern_loop4 -O0": ("pattern_loop4.c:7", dict(perceptron=99.8, bimodal=80.0, gshare=100.0, tournament=99.9)),
        "pattern_xor -O0": ("pattern_xor.c:17", dict(perceptron=51.0, bimodal=50.1, gshare=49.8, tournament=51.2)),
    }
    traces = {}
    rx = re.compile(r"core\s+\d+:\s+\d+\s+0x([0-9a-f]+)\s+\(0x([0-9a-f]+)\)")
    seq = [(int(m[1], 16), int(m[2], 16)) for m in map(rx.match, open(os.path.join(t704, "func_sim/spike_log.txt"))) if m]
    traces["704 reference log"] = [(pc, int(seq[i + 1][0] != pc + 4), None)
                                   for i, (pc, ins) in enumerate(seq[:-1]) if ins & 0x7F == 0x63]
    tmp = tempfile.mkdtemp()
    for name in ("sort_and_search -O1", "pattern_alternating -O0", "pattern_loop4 -O0", "pattern_xor -O0"):
        prog, opt = name.split()
        out = os.path.join(tmp, prog)
        subprocess.run([sys.executable, os.path.join(kit, "bptrace/bptrace.py"), os.path.join(kit, "examples", prog + ".c"),
                        "-o", out, "--opt", opt[1:], "--user-only"], check=True, capture_output=True)
        lines = dict(l.split(",")[0:7:6] for l in open(os.path.join(out, "branches.csv")).read().splitlines()[1:])
        traces[name] = [(int(p, 16), int(t), lines[p]) for p, t in (l.split() for l in open(os.path.join(out, "trace.txt")))]
    ok = True
    for name, (only, want) in published.items():
        tr = traces[name]
        preds = run([(pc & 0xFF, tk) for pc, tk, _ in tr])
        got = {}
        for k in PREDICTORS:
            sel = [(p, tk) for p, (_, tk, line) in zip(preds[k], tr) if only is None or line == only]
            got[k] = 100 * sum(p == tk for p, tk in sel) / len(sel)
        digits = 2 if only is None else 1
        # within half a printed digit: 99.85% (3 misses of 2,000) prints as 99.8 or 99.9 depending on rounding
        match = all(abs(got[k] - want[k]) <= 0.5 * 10 ** -digits + 1e-9 for k in PREDICTORS)
        ok &= match
        print(f"{'PASS' if match else 'FAIL'}  {name:26s} " +
              "  ".join(f"{k} {got[k]:.{digits}f} (kit {want[k]})" for k in PREDICTORS))
    return ok


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--kit", default=".", help="folder with bptrace/ and examples/")
    ap.add_argument("--t704", default="tt_branch_pred", help="clone of the 704 repo")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if _selftest(a.kit, a.t704) else 1)
