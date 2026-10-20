#!/usr/bin/env python3
"""Host-side test without a board, against mockboard.MockSerial (protocol v1, predictions from ref_models.py).

    python fpga/host/test_bplink.py [trace dir]
Checks framing, seq numbers, windowed streaming, single steps and that the predictions come back in order.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import bplink  # noqa: E402
from mockboard import MockSerial, R  # noqa: E402


def main():
    trace_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "verification", "traces", "ref704_committed_log")
    with open(os.path.join(trace_dir, "tb_input.hex")) as f:
        trace = [(int(p, 16), int(t)) for p, t in (l.split() for l in f if l.strip())]
    fake = MockSerial(link_baud=0)
    board = bplink.Board(None, ser=fake)
    fake.write(b"\x15")                         # stray byte, as in tb_hil.v
    board.sync()
    assert board.ping() == bplink.VERSION
    board.reset()
    f0 = board.status()
    half = len(trace) // 2
    raw = []
    res = board.run(trace[:half], window=300, chunk=37, raw=raw)
    assert len(raw) == half
    for pc, tk in trace[half:half + 10]:        # single steps in the middle of a stream
        res.append(board.branch(pc, tk)[0])
    res += board.run(trace[half + 10:])
    assert board.status() == f0, "framing errors during the run"
    want = R.run(trace)
    for j, k in enumerate(R.PREDICTORS):
        assert [r[j] for r in res] == want[k], f"{k} differs"
    print(f"PASS: {len(trace)} branches, framing errors before the run {f0} (the stray byte; sync itself is clean), "
          f"avg cycles {sum(r[4] for r in res) / len(res):.1f}")


if __name__ == "__main__":
    main()
