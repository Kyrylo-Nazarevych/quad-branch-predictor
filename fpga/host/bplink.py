"""Serial link to the branch-predictor FPGA (protocol v1, see fpga/hdl/bp_hil_core.v).

Every packet is 2 bytes and gets exactly one 2-byte reply, in order:
    host -> board  H = 1 | seq[2:0] | op[1:0] | pc[7] | taken      D = 0 | pc[6:0]
    board -> host  H = 1 | seq[2:0] | tour gshare bimodal perceptron   D = 0 | cycles[6:0]   (BRANCH)
A header byte has bit 7 set and a data byte has it clear, so both sides can resynchronise.
"""
import threading
import time

import serial
from serial.tools import list_ports

OP_BRANCH, OP_RESET, OP_PING, OP_STATUS = 0, 1, 2, 3
VERSION = 1
FTDI_VID = 0x0403


def find_port():
    """The Nexys 4 DDR's UART is the FT2232HQ's second channel; pick the FTDI COM port."""
    ftdi = [p for p in list_ports.comports() if p.vid == FTDI_VID]
    if not ftdi:
        return None
    # Windows lists the two FT2232 channels with serial numbers ending in A (JTAG) and B (UART)
    for p in ftdi:
        if (p.serial_number or "").upper().endswith("B"):
            return p.device
    return ftdi[-1].device


def encode(seq, op, pc=0, taken=0):
    return bytes([0x80 | (seq & 7) << 4 | (op & 3) << 2 | (pc >> 7 & 1) << 1 | (taken & 1), pc & 0x7F])


class ProtocolError(Exception):
    pass


class Board:
    def __init__(self, port, baud=1_000_000, timeout=2.0, ser=None):
        self.ser = ser or serial.Serial(port, baud, timeout=timeout)   # ser: a stand-in port for tests
        try:
            self.ser.set_buffer_size(rx_size=1 << 20, tx_size=1 << 16)   # Windows only
        except (AttributeError, serial.SerialException):
            pass
        self.seq = 0
        self.timeout = timeout

    def close(self):
        self.ser.close()

    def _read_reply(self):
        h, d = self.ser.read(2) or b"", b""
        if len(h) == 2:
            h, d = h[0], h[1]
        else:
            raise ProtocolError("no reply from the board (is the bitstream loaded and the baud rate right?)")
        if not (h & 0x80) or (d & 0x80):
            raise ProtocolError(f"bad reply framing {h:02x} {d:02x}")
        return h, d

    def _command(self, op):
        seq = self.seq
        self.seq = (self.seq + 1) & 7
        self.ser.write(encode(seq, op))
        h, d = self._read_reply()
        if (h >> 4 & 7) != seq:
            raise ProtocolError(f"reply seq {h >> 4 & 7}, expected {seq}")
        return h, d

    def sync(self):
        """Drop anything in flight and resynchronise. A PING packet does it: its header byte restarts the board's
        parser even if a half packet is pending, and on a board in sync it is a normal packet, so it doesn't count as
        a framing error (the board's LD13 then only lights for real errors). Its reply is thrown away."""
        self.ser.reset_input_buffer()
        self.ser.write(encode(self.seq, OP_PING))
        self.seq = (self.seq + 1) & 7
        time.sleep(0.02)
        self.ser.reset_input_buffer()

    def ping(self):
        h, d = self._command(OP_PING)
        if d != 0x50:
            raise ProtocolError(f"unexpected PING reply {h:02x} {d:02x}")
        return h & 0x0F

    def reset(self):
        h, d = self._command(OP_RESET)
        if d != 0x52:
            raise ProtocolError(f"unexpected RESET reply {h:02x} {d:02x}")

    def status(self):
        return self._command(OP_STATUS)[1]

    def branch(self, pc, taken):
        """One branch, request and reply (single steps). Returns ((perc, bim, gsh, tour, cycles), tx, rx)."""
        seq = self.seq
        self.seq = (self.seq + 1) & 7
        tx = encode(seq, OP_BRANCH, pc, taken)
        self.ser.write(tx)
        h, d = self._read_reply()
        if (h >> 4 & 7) != seq:
            raise ProtocolError(f"reply seq {h >> 4 & 7}, expected {seq}")
        return (h & 1, h >> 1 & 1, h >> 2 & 1, h >> 3 & 1, d), tx, bytes([h, d])

    def run(self, branches, window=2048, chunk=64, progress=None, raw=None):
        """Replay [(pc8, taken), ...]; returns [(perc, bim, gsh, tour, cycles), ...].
        Writes run ahead of the replies by at most `window` packets; a reader thread drains the port.
        raw: a list that receives every reply as received, (header, data)."""
        n = len(branches)
        out = [None] * n
        first_seq = self.seq
        state = {"received": 0, "error": None}

        def reader():
            buf = bytearray()
            try:
                while state["received"] < n:
                    data = self.ser.read(max(2, min(self.ser.in_waiting, 1 << 16)))
                    if not data:
                        raise ProtocolError(f"timeout after {state['received']} of {n} replies")
                    buf += data
                    k = 0
                    while k + 1 < len(buf):
                        h, d = buf[k], buf[k + 1]
                        i = state["received"]
                        if not (h & 0x80) or (d & 0x80):
                            raise ProtocolError(f"bad reply framing at branch {i}: {h:02x} {d:02x}")
                        if (h >> 4 & 7) != (first_seq + i) & 7:
                            raise ProtocolError(f"branch {i}: reply seq {h >> 4 & 7}, expected {(first_seq + i) & 7}")
                        out[i] = (h & 1, h >> 1 & 1, h >> 2 & 1, h >> 3 & 1, d)
                        if raw is not None:
                            raw.append((h, d))
                        state["received"] = i + 1
                        k += 2
                    del buf[:k]
                    if progress:
                        progress(state["received"], n)
            except Exception as e:      # reported by the writer
                state["error"] = e

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        sent = 0
        while sent < n and state["error"] is None:
            if sent - state["received"] >= window:
                time.sleep(0.0005)
                continue
            m = min(chunk, n - sent, window - (sent - state["received"]))
            pkt = b"".join(encode(self.seq + j, OP_BRANCH, *branches[sent + j]) for j in range(m))
            self.ser.write(pkt)
            self.seq = (self.seq + m) & 7
            sent += m
        t.join()
        if state["error"]:
            raise state["error"]
        return out
