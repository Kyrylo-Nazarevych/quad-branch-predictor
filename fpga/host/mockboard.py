"""A stand-in for the board: a pyserial-like object that speaks protocol v1 and predicts with ref_models.py.

    board = bplink.Board(None, ser=MockSerial())
Used by test_bplink.py and by the dashboard's --mock mode (demo or development without the Nexys 4 DDR).
Cycles follow the chip: 45 when the perceptron trains, 11 when it doesn't (the only two values on the board).
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "verification", "models"))
import ref_models as R  # noqa: E402

OP_BRANCH, OP_RESET, OP_PING = 0, 1, 2
VERSION = 1


class MockSerial:
    """Byte-level model of bp_hil_core: parser with resync, one reply per packet."""

    def __init__(self, timeout=2.0, link_baud=1_000_000):
        self.timeout = timeout
        self.byte_time = 10 / link_baud if link_baud else 0   # 0: infinitely fast link
        self.rx = bytearray()
        self.cv = threading.Condition()
        self.hdr = None
        self.ferr = 0
        self.models = R.make_all()
        self.port = "mock"

    def _reply(self, h, d):
        with self.cv:
            self.rx += bytes([h, d])
            self.cv.notify_all()

    def _packet(self, h, d):
        seq, op, pc, tk = h >> 4 & 7, h >> 2 & 3, (h >> 1 & 1) << 7 | d, h & 1
        if op == OP_BRANCH:
            p = [self.models[k].predict(pc) for k in R.PREDICTORS]
            perc = self.models["perceptron"]
            y = perc._y(pc)
            cycles = 45 if (int(y >= 0) != tk or abs(y) <= perc.THETA) else 11
            for k in R.PREDICTORS:
                self.models[k].update(pc, tk)
            self._reply(0x80 | seq << 4 | p[3] << 3 | p[2] << 2 | p[1] << 1 | p[0], cycles)
        elif op == OP_RESET:
            self.models = R.make_all()
            self._reply(0x80 | seq << 4, 0x52)
        elif op == OP_PING:
            self._reply(0x80 | seq << 4 | VERSION, 0x50)
        else:
            self._reply(0x80 | seq << 4, min(self.ferr, 127))

    def write(self, data):
        if self.byte_time:
            time.sleep(len(data) * self.byte_time)
        for b in data:
            if b & 0x80:
                if self.hdr is not None:
                    self.ferr += 1
                self.hdr = b
            elif self.hdr is not None:
                self._packet(self.hdr, b)
                self.hdr = None
            else:
                self.ferr += 1
        return len(data)

    @property
    def in_waiting(self):
        with self.cv:
            return len(self.rx)

    def read(self, n):
        with self.cv:
            self.cv.wait_for(lambda: len(self.rx) >= n, timeout=self.timeout)
            out = bytes(self.rx[:n])
            del self.rx[:n]
            return out

    def reset_input_buffer(self):
        with self.cv:
            self.rx.clear()

    def close(self):
        pass
