# SPDX-License-Identifier: Apache-2.0
#
# Trace-driven test for tt_um_kyrylo_nazarevych_bp.
# Replays the 704 reference trace (1,271 conditional branches of reference.c) through the pins,
# using the same handshake as tb/tb_team.v, and checks the mispredictions of all four predictors.
# Works with the DUT as toplevel (Makefile here) and with the Tiny Tapeout tb.v wrapper (RTL and gate level).

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

# The trace as 3-hex-digit words {taken, pc[7:0]}; it repeats one loop body, so it is stored compactly.
_A = "14c19415807019415817019415817019405817019405807019405817019405817019405817019405807019405817019405817019405817019405807019405817019405817019405817019405807019405817019405817019405817009419c0b41b41780901b40781901b41780901b41781901b40780901b41781901b40780901b41781901b40780901b41781901b41780901b40781901b41780901b41781901b40780901b41781901b40780901b41781901b40780901b41781900b40"
_C = "0" + _A
_HEX = _A + ("e801" + _C + "e811" + _C) * 4 + "e801" + _C + "e811" + "004c"
TRACE = [int(_HEX[i:i + 3], 16) for i in range(0, len(_HEX), 3)]

# Reference results (perceptron 81.98%, bimodal 66.88%, gshare 86.47%, tournament 88.83%).
EXPECTED_MISSES = {"perceptron": 229, "bimodal": 421, "gshare": 172, "tournament": 142}
UO_BIT = {"perceptron": 1, "bimodal": 4, "gshare": 5, "tournament": 6}
UIO_OE = 0x7C  # uio[2..6] outputs, uio[0], uio[1] and uio[7] inputs


def bit(signal, i):
    s = str(signal.value)
    return s[len(s) - 1 - i] == "1"


async def wait_for(dut, i, limit, what):
    for _ in range(limit):
        if bit(dut.uo_out, i):
            return
        await FallingEdge(dut.clk)
    assert False, f"timed out waiting for {what} (uo[{i}])"


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 100, unit="ns").start())  # 10 MHz
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    # The 704 core first clears its 64-byte latch RAM (about 128 clocks); uo[3] pulses when it is done.
    await FallingEdge(dut.clk)
    await wait_for(dut, 3, 400, "memory reset done")


@cocotb.test()
async def test_reference_trace(dut):
    await reset(dut)
    assert dut.uio_oe.value.is_resolvable, "uio_oe has X or Z bits"
    assert int(dut.uio_oe.value) == UIO_OE, f"uio_oe = {int(dut.uio_oe.value):#04x}, expected {UIO_OE:#04x}"

    misses = dict.fromkeys(EXPECTED_MISSES, 0)
    for word in TRACE:
        taken, pc = word >> 8, word & 0xFF
        await FallingEdge(dut.clk)
        dut.ui_in.value = pc
        dut.uio_in.value = (taken << 1) | 1     # new branch strobe + real outcome
        await FallingEdge(dut.clk)
        dut.uio_in.value = taken << 1
        await wait_for(dut, 0, 100, "perceptron prediction ready")
        assert dut.uo_out.value.is_resolvable, f"uo_out has X or Z bits: {dut.uo_out.value}"
        out = int(dut.uo_out.value)
        for name, b in UO_BIT.items():
            misses[name] += ((out >> b) & 1) != taken
        await wait_for(dut, 2, 100, "training done")

    n = len(TRACE)
    dut._log.info("branches=%d " % n + " ".join(f"{k}={100.0 * (n - v) / n:.2f}%" for k, v in misses.items()))
    assert misses == EXPECTED_MISSES, f"mispredictions {misses}, expected {EXPECTED_MISSES}"
