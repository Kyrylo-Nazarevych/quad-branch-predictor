# FPGA hardware-in-the-loop: the branch-predictor chip on the Nexys 4 DDR

The Tiny Tapeout chip RTL (`tt_um_kyrylo_nazarevych_bp`, four predictors) runs unchanged on the Artix-7. The PC streams
branches over the board's USB-UART, the FPGA drives the chip's pins exactly like `tb_team.v`, and replies with the four
predictions and the clocks the branch took. The result is the same `predictions.csv` as simulation.

Everything is scripted (Vivado 2024.2 batch mode, no GUI): `fpga.ps1` for the hardware, `host/bphost.py` for the board.

## Layout

| Path | What |
|---|---|
| `../rtl/` | The four chip files (shared with the ASIC flow), identical to `tt-branch-predictor` commit `21dad17`. Don't edit them for the FPGA. |
| `hdl/fpga_top.v` | Board top: 100 MHz → MMCM → 25 MHz, reset synchroniser, status LEDs |
| `hdl/bp_hil_core.v` | UART, FIFOs, packet parser, the chip handshake, replies (protocol below) |
| `hdl/board_ui.v` | 7-segment display (live accuracy, branch count), per-predictor and RGB LEDs, switches |
| `hdl/uart_rx.v`, `uart_tx.v`, `sync_fifo.v` | 8N1 UART at 1 Mbaud (25 clocks per bit), FIFOs |
| `hdl/bp_latch_mem_ff.v` | FPGA-only drop-in for the latch RAM: same module, ports and cycle behaviour, clocked storage. Used **instead of** `rtl/latch_memory.v` with `-Ram ff` |
| `sim/tb_hil.v` | UART-level testbench: plays the host, prints the same `R,` lines as `tb_team.v` |
| `sim/tb_team.v` | Pin-level reference testbench of the chip |
| `constraints/` | `nexys4ddr_bp.xdc` (used), Digilent's `Nexys-4-DDR-Master.xdc` (reference) |
| `scripts/` | `build.tcl` (synth → bitstream), `program.tcl` (JTAG), `timesim_netlist.tcl` |
| `host/` | `bplink.py` (protocol), `bphost.py` (CLI), `bpdash.py` + `dashboard.html` (live dashboard), `mockboard.py` (the board in Python, for tests and demos), `test_bplink.py` |
| `build/` | All outputs (not committed) |

## Commands (PowerShell, from the repository root)

```powershell
.\fpga\fpga.ps1 simall -Ram both          # every trace, both RAM builds: vs models and vs tb_team.v
.\fpga\fpga.ps1 sim -Trace verification\traces\ref704_committed_log [-Ram ff] [-Top]
.\fpga\fpga.ps1 build -Ram latch          # -> fpga\build\impl_latch\fpga_top_latch.bit (about 2.5 min)
.\fpga\fpga.ps1 build -Ram ff
.\fpga\fpga.ps1 timesim -Funcsim -Ram latch -Branches 100000   # post-synthesis netlist, whole reference trace (4 min)
.\fpga\fpga.ps1 program -Ram latch        # board on J6 (PROG/UART micro-USB), power on
fpga\.venv\Scripts\python.exe fpga\host\bphost.py ping
fpga\.venv\Scripts\python.exe fpga\host\bphost.py run verification\traces\sort_and_search_O1 --compare
fpga\.venv\Scripts\python.exe fpga\host\bphost.py runall      # all traces, compared with the models
```

`sim -Top` simulates the whole `fpga_top` (MMCM, reset, real 1 Mbaud); without it the core runs at 5 clocks per UART bit,
which is faster and used for the full matrix. Setup: `python -m venv fpga\.venv` then
`fpga\.venv\Scripts\python.exe -m pip install pyserial==3.5`.

## Live dashboard

```powershell
fpga\.venv\Scripts\python.exe fpga\host\bpdash.py            # the board on the FTDI COM port
fpga\.venv\Scripts\python.exe fpga\host\bpdash.py --mock     # no board: same protocol, predictions from the models
```

Open http://localhost:8765 (in VS Code: Simple Browser). One page, standard library server, live over Server-Sent
Events:

- **Run control**: Step (F10), +10, +100, Run (F5), Pause (F6), Reset chip, and a speed slider from 1 branch/s to
  20,000/s or "max" (about 35,000/s, the 1 Mbaud link). Paced speeds are exact (deadline-based); a single step is one
  request and reply, about 16 ms with the FTDI latency timer at its default (1 ms if you lower it).
- **Breakpoints**: click a line number in the C source (stops before that line's next branch), "stop when
  <predictor> mispredicts" (stops right after), and "stop if board ≠ models". The reference models run over the whole
  trace at load, so these stop on the exact branch even at full speed.
- **Views**: the C source with the next branch's line highlighted and each line's misses for a chosen predictor; the
  last branch (PC, function, line, outcome, the four predictions with ✓/✗ and whether the model agrees, clocks); live
  accuracy, MPKI and **board-vs-model mismatch counter** (0 on every trace so far); rolling or cumulative accuracy
  chart; clocks per branch (11 or 45); the **UART byte log** (every byte sent and received, decoded).
- **Program list** (pattern demos and workloads), **GCC level** list, and **Jump to**: the sections (functions) of
  the current program in the order it reaches them; picking one runs or replays to its first branch. A line under
  the toolbar says what to watch in the program. `programs/pattern_all_in_one.c` has every pattern as one function,
  so Jump to walks through them in one program; `programs/make_traces.py` compiles our programs through the app's
  pipeline (WSL on Windows) into `bp-tools/precomputed/`. New trace folders appear in the list by themselves.
- **Layout**: the page always fits the window, at any size or browser zoom (panels scroll inside, charts redraw at
  their new size, the toolbar wraps into more rows when narrow). The UART log runs oldest to newest and follows the
  newest rows while scrolled to the bottom, like Wireshark.

Checked on the board: sort_and_search_O1 to the end at max speed gives 84.48 / 84.07 / 82.83 / 83.48, 21.22 clocks,
0 mismatches; speeds 5 / 20 / 50 / 1,000 / 10,000 per second measured within 1%. Stepping is per branch; stepping per
instruction needs Spike's commit log (`spike.log`) in the trace folder, which the precomputed traces don't keep.

**Streamlit app** (`tools/app`): "FPGA board (Nexys 4 DDR)" is a third source next to the chip RTL and the Python
models. Only one program can hold the COM port: close the
dashboard before running the app on the board.

## Board display

The board shows the run by itself, so it reads well on a demo table without the PC screen:

| Control | 7-segment display |
|---|---|
| SW0 down | accuracy of one predictor, e.g. `PE  84.48`: `PE` perceptron, `bi` bimodal, `GS` gshare, `to` tournament |
| SW1 up | cycle through the four predictors every 2 s (otherwise SW3 SW2 pick one: 00 PE, 01 bi, 10 GS, 11 to) |
| SW0 up | branches replayed since the last chip reset, in decimal |

LD0–3 the last predictions, LD4 the last outcome, LD5–8 right/wrong per predictor on the last branch, RGB LD16
green/red for the selected predictor's last branch, RGB LD17 green when the chip is ready (red in reset), LD12 UART
traffic, LD13 framing error seen, LD14 chip ready, LD15 heartbeat. The counters clear on every chip reset (the host
resets before each run), so the display shows the same run as the host. The accuracy is computed on the FPGA
(48-bit divider, rounded to 2 decimals); `tb_hil.v` checks it and the count against the host's numbers on every trace.

## VS Code

`.vscode/tasks.json` (Terminal → Run Task; Ctrl+Shift+B = build and program the latch build):
simulate all traces, simulate one trace (picker), simulate the whole board design, build bitstream, program the
board, ping, replay every trace on the board, the dashboard (board or mock) and the Streamlit app.
`.vscode/launch.json` (Run and Debug, F5): the dashboard (board or mock) and the Streamlit app; each opens its page
in VS Code's Simple Browser when the server is up (`settings.json` maps `localhost:8765` and `localhost:8501` to the
Simple Browser, so a Ctrl+click on those links in a terminal opens there too).

## Protocol v1 (115200 is possible with `build -Baud 115200` and `--baud 115200`)

1 Mbaud, 8N1, no flow control. Every packet is 2 bytes and gets exactly one 2-byte reply, in order. A header byte has
bit 7 set and a data byte has it clear, so a lost byte can never turn a PC into a command; the board drops stray data
bytes, restarts on any header, and counts both as framing errors.

| Direction | Header byte | Data byte |
|---|---|---|
| host → board | `1 seq[2:0] op[1:0] pc[7] taken` | `0 pc[6:0]` |
| board → host, BRANCH | `1 seq[2:0] tour gshare bimodal perceptron` | `0 cycles[6:0]` |
| board → host, RESET | `1 seq 0000`, sent when the chip's RAM clear is done | `0x52` |
| board → host, PING | `1 seq version(1)` | `0x50` |
| board → host, STATUS | `1 seq 0000` | framing errors (saturates at 127) |

Ops: 0 BRANCH, 1 RESET, 2 PING, 3 STATUS. `cycles` = clocks from the chip sampling the strobe to `training_done`,
the same count as `tb_team.v`'s `cycles` column (11 to 45, average 25.6 on the reference trace).

Throughput: 2 bytes each way per branch = 20 µs, about 50,000 branches/s; the chip needs at most 45 clocks (1.8 µs).
The host streams with up to 2,048 packets in flight and a reader thread, so the USB round trip doesn't matter.
Set the COM port's latency timer to 1 ms (Device Manager → port → Advanced) for snappy single steps.

## Board

- Micro-USB **J6 (PROG)** carries JTAG and the UART. JP3 on USB power, power switch on. JTAG works in any JP1 mode.
- **Booting without a PC (demo):** `.\fpga\fpga.ps1 flash -Ram latch` writes the bitstream into the QSPI flash
  (S25FL128S; erase, program and verify, a few minutes; it replaces what was in the flash). With the **JP1 mode
  jumper on QSPI** the board then loads it at every power-on, or when PROG is pressed. `program` (JTAG) still
  overrides it until the next power cycle. After writing, the script tries a boot from the flash; with JP1 on JTAG
  that waits 3 minutes and reports "did not finish" (harmless: run `program` to load the FPGA again).
  `.\fpga\fpga.ps1 boot` reloads the FPGA from the flash over JTAG, the same as a power-on.
  Done 18 October 2026: the latch bitstream is in the flash (S25FL128S detected, erase/program/verify OK), JP1 on
  QSPI, and `boot` brings it up (DONE high after 2.6 s); the flash-loaded design replays sort_and_search_O1 IDENTICAL.
- LEDs: LD0–3 last predictions (perceptron, bimodal, gshare, tournament), LD4 last outcome, LD12 UART traffic,
  LD13 framing error seen, LD14 chip ready, LD15 heartbeat. CPU RESET (red) resets everything.

## Verified (18 October 2026, Vivado 2024.2.2 xsim)

- `simall -Ram both`: **34 of 34 runs pass**. 17 trace folders (`verification/traces`, 5, and
  `tools/precomputed`, 12) × 2 RAM builds. Each one is IDENTICAL to `ref_models.py` (`compare_predictions.py`),
  its `R,` lines (cycles included) are the same as `tb_team.v`'s, and the protocol checks pass (stray byte counted,
  PING, RESET, STATUS, seq numbers).
- `sim -Top` on the reference trace (full `fpga_top`, MMCM, 1 Mbaud): IDENTICAL, protocol PASS.
- Builds: latch RAM 1,253 LUTs, 695 FFs, 512 latches, setup slack 18.2 ns at 40 ns, hold met;
  flip-flop RAM 1,043 LUTs, 691 FFs, slack 29.6 ns.
- `timesim -Funcsim` (post-synthesis netlist of the latch build, full `fpga_top`, 1 Mbaud): reference trace IDENTICAL,
  cycles the same as `tb_team.v`. So Vivado's latch RAM synthesis is functionally right.
- `timesim` (post-route netlist with SDF delays) does not get out of reset in either build: the flip-flop that
  releases the chip's `rst_n` misses a stable 1 on its D input. The real board resets and runs correctly with the same
  bitstreams (below), so this is a timing-simulation setup problem, not the design. Not debugged further.

## Verified on the board (P2, 18 October 2026)

Nexys 4 DDR on COM10 (FT2232HQ channel B), 1 Mbaud, `bphost.py runall` after `fpga.ps1 program`:

- **Latch RAM build: 17 of 17 traces IDENTICAL** to `ref_models.py`, 0 framing errors.
- **Flip-flop RAM build: 17 of 17 traces IDENTICAL**, 0 framing errors.
- **All 34 board `predictions.csv` files are byte-identical to `tb_team.v`'s simulation output**, `cycles` column
  included (checked line by line against `build/sim/<ram>/<trace>/team/sim_out.txt`).
- Reference trace on the board: 81.98 / 66.88 / 86.47 / 88.83, avg_cycles 25.6.
- Speed: about 48,000 branches/s on the long traces (46,922 branches in 0.98 s), limited by the 1 Mbaud link; all
  17 traces take 12 s per build including the chip resets. Programming over JTAG takes 16 s.

So the latch RAM works on the Artix-7 at 25 MHz despite the non-clocked-latch warnings; the flip-flop build stays as
a cross-check.
- Latch build caveat: Vivado reports the 512 latches as non-clocked (TIMING-20) and their logic-driven enables as
  gated clocks (PDRC-153), so static timing doesn't cover the latch RAM. The board run on all traces is the real test;
  the flip-flop build is the fallback if the latch build disagrees.
