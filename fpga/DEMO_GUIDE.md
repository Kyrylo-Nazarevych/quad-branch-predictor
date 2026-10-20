# Branch predictor on the FPGA: demo guide

What the hardware-in-the-loop setup is, what it can run, what every screen and every LED shows, and what an audience
sees during the demo. For build and test commands in more depth, see [README.md](README.md).

## 1. The idea in one paragraph

Our Tiny Tapeout chip holds **four branch predictors** behind one set of pins: the 704 **perceptron**, a
**bimodal** table, **gshare**, and a **tournament** that picks between bimodal and gshare. The exact chip RTL that was
hardened for the tape-out runs on a **Nexys 4 DDR** FPGA board. A PC replays the branches of a real C program (compiled
for RISC-V and run on the Spike simulator) into the board over USB, one branch at a time: "this branch at this
address, and this is what it really did". For every branch the chip answers with **four predictions**, made before it
learns the real outcome, and the number of clocks it took. The PC shows it all live, and checks every answer against
bit-exact Python models of the four predictors. On every trace replayed on the board so far, it has never disagreed with them.

```
 C program ──► RISC-V GCC ──► Spike ──► branch trace ──► PC app ══ USB, 1 Mbaud ══► FPGA: the chip RTL
 (yours or     (in WSL)     (runs it)   (PC + outcome    (dashboard,                  4 predictions + clocks
  an example)                            per branch)      Streamlit, CLI) ◄═════════  per branch
                                                                │
                                                                └─► live charts, statistics, board ≠ model check
```

## 2. What is in the box

| Part | What it is | Where |
|---|---|---|
| FPGA design | The chip RTL, unchanged from `tt-branch-predictor` commit `21dad17`, inside a wrapper with a 25 MHz clock, a UART link and the board display | `rtl/`, `fpga/hdl/` |
| Bitstream in the board's flash | The latch-RAM build (the chip exactly as taped out). The board loads it by itself at power-on | written with `fpga.ps1 flash` |
| **Live dashboard** | Web page on `http://localhost:8765`: step, run, rewind, breakpoints, C source, UART bytes, statistics | `fpga/host/bpdash.py` + `dashboard.html` |
| **Streamlit app** | Upload or paste a C program, compile it, run it on the board (or the RTL simulator, or the models), full statistics and charts | `tools/app/` |
| Command-line tool | Replay one trace or all of them and write `predictions.csv` | `fpga/host/bphost.py` |
| Programs and traces | 15 C programs, each traced at `-O0`, `-O1` and `-O2` (45 traces) plus the 704 reference log | `bp-tools/precomputed/`, `bp-tools/examples/` |
| RISC-V toolchain | GCC 13.2 and Spike in WSL Ubuntu 24.04; the app compiles new programs through it | `~/riscv-tools` in WSL |
| VS Code integration | Tasks and launch configurations for everything above | `.vscode/` |

## 3. The programs it can run

Every program can be run at three GCC optimisation levels. A pattern program opens at `-O0` (where GCC keeps every
branch as written); a workload opens at `-O1`. You can also paste or upload your own C program in the Streamlit app;
once it compiles it joins the list.

Numbers below are whole-program accuracy at the default level (% of branches predicted correctly). The **baseline**
always guesses the program's more common direction: a predictor is only useful above it. These come from the
reference models, which the board matches exactly.

| Program | GCC | Branches | Baseline | Perceptron | Bimodal | gshare | Tournament | What to watch |
|---|---|---|---|---|---|---|---|---|
| **Pattern demos** | | | | | | | | |
| `alternating` | -O0 | 4,001 | 75.0 | 99.80 | 74.98 | 99.93 | 99.90 | T, N, T, N: bimodal gets exactly half of that branch; anything with history learns it |
| `loop4` | -O0 | 6,001 | 83.3 | 99.78 | 83.32 | 99.95 | 99.93 | The exit of a 4-step loop: bimodal misses every exit, history predictors learn it |
| `loop12` | -O0 | 7,001 | 92.8 | 92.83 | 92.84 | 92.84 | 92.84 | The exit of a 12-step loop is too far back for 7–8 history bits: nobody learns it |
| `correlated` | -O0 | 18,001 | 66.5 | 91.57 | 66.85 | 83.86 | 87.53 | Line 18 repeats line 16's coin flip: bimodal ~50% on it, gshare and perceptron ~100% |
| `xor` | -O0 | 12,001 | 63.2 | 63.01 | 62.43 | 61.47 | 62.52 | XOR of two coin flips: no linear perceptron can learn it; gshare would need 6 history bits, the chip has 8 |
| `random` | -O0 | 8,001 | 75.5 | 74.70 | 75.07 | 73.80 | 74.73 | A fair coin: everyone ~50% on it. The honest lower bound |
| `biased` | -O0 | 8,001 | 55.1 | 94.86 | 94.36 | 93.85 | 94.39 | Random but 90% one way: the bias is learnable, history adds nothing |
| `aliasing` | -O0 | 7,501 | 52.0 | 99.91 | 99.83 | 95.76 | 99.83 | 24 fixed branches: gshare loses one to a counter collision every time; the perceptron tells them apart |
| `all in one` | -O0 | 32,807 | 70.3 | 86.53 | 75.37 | 84.72 | 85.25 | All patterns as functions in one program: use **Jump to** to move between them |
| **Workloads** | | | | | | | | |
| `sort and search` | -O1 | 46,922 | 75.2 | 84.48 | 84.07 | 82.83 | 83.48 | Bubble sort then binary search. Line 11 (the compare) is data-dependent and causes most misses |
| `quicksort` | -O1 | 6,721 | 64.8 | 79.18 | 80.75 | 78.96 | 81.03 | Same hard compare as bubble sort, executed far fewer times |
| `nqueens` | -O1 | 37,972 | 75.5 | 88.95 | 88.05 | 84.02 | 88.11 | Backtracking recursion: early exits whose pattern depends on the board |
| `strsearch` | -O1 | 6,575 | 60.4 | 90.60 | 88.90 | 81.66 | 88.20 | Text search: a data-dependent early exit, and a rare "found it" branch |
| `matmul` | -O1 | 5,811 | 91.1 | 91.50 | 91.62 | 91.58 | 91.62 | Perfectly regular loops: only the loop exits are missed |
| `interpreter` | -O1 | 5,205 | 100.0 | 99.94 | 99.92 | 99.94 | 99.92 | A bytecode interpreter. At -O1 nearly every branch goes the same way; try -O0 (16,015 branches) to see the decoder chain |
| `ref704 committed log` | – | 1,271 | 64.9 | 81.98 | 66.88 | 86.47 | 88.83 | The 704 design's own reference program (no C source); the published 81.98% |

Two things in the table are slides on their own. The pattern programs give each predictor's limits as hard numbers
(bimodal *exactly* 50% on alternation, 80% on a loop of 4). And on real code the four end up within a few points of
each other, mostly because of one or two data-dependent branches that nobody can predict.

## 4. The live dashboard

Start it, then open `http://localhost:8765` (in VS Code: Run and Debug → "Dashboard: board", which opens it inside
VS Code):

```bash
fpga/.venv/Scripts/python.exe fpga/host/bpdash.py
```

`--mock` runs it without the board: a Python stand-in answers with the same protocol, the same predictions and the
same clock counts. The page always fits the window, at any size or browser zoom; each panel scrolls on its own.

### Toolbar

| Control | What it does |
|---|---|
| Connection badge | `COM10 · protocol v1` (green) when the board answered; `mock board` (orange) in mock mode |
| **Board / Mock** | Switch between the real FPGA and the Python stand-in |
| **Program** | Every program, grouped into pattern demos and workloads |
| **GCC** | `-O0`, `-O1`, `-O2`: the same program compiled differently (different code, different branches) |
| **Jump to** | The sections (C functions) of the current program, in the order it reaches them, with their first line and branch count. Picking one jumps there |
| **Debugger / Statistics / Compiler** | Switch between the three views below |
| **Reset chip** | Clears the chip's tables (all predictors forget everything) and starts the trace from branch 0 |
| **−100, −10, ◀ Back** | Go back. The chip can't run backwards, but it is deterministic: going back resets it and replays the earlier branches at full speed (under a second even for the longest trace), which leaves it in exactly the state it had there |
| **Step ▶, +10, +100** | Send 1, 10 or 100 branches |
| **Run / Pause** | Run continuously at the chosen speed |
| **Speed** | 1 to 20,000 branches per second, or *max*, about 35,000 per second, limited by the 1 Mbaud USB link. Paced speeds are exact |
| **stop if board ≠ models** | Pause the moment the board disagrees with the Python models (it never has) |
| **stop when … mispredicts** | Pause right after the chosen predictor gets a branch wrong: a quick way to find the interesting branches |
| Progress bar | Click anywhere to jump there, forwards or backwards |
| Second line | What just happened (for example "back to branch 4001: chip reset and 4,001 branches replayed in 0.32 s") and what to watch in this program |

Keyboard: **F10** step, **Shift+F10** back one, **F5** run or pause, **F6** pause.

The four predictors have the same colours everywhere, taken from Windows Task Manager: **perceptron blue**,
**bimodal purple**, **gshare green**, **tournament brown**.

### Debugger view

- **Source code** (left, large). The C program with line numbers. Lines that hold a conditional branch are shaded;
  the line of the **next** branch is highlighted yellow with a ▶. Click a line number to set a **breakpoint** (red
  dot): running stops just before that line's next branch. Next to each branch line, a bar and a count show how often
  the chosen predictor got it wrong (for example `5693/19624` on the bubble-sort compare).
- **UART bytes** (left, bottom). Every message on the wire, Wireshark style: time, kind (BRANCH, RESET, PING, GOTO),
  branch number, the 2 bytes sent, the 2 bytes received, and their meaning ("pc8 a4 T → perc 1 bim 0 gsh 1 tour 1,
  45 clk"). New rows appear at the bottom; the list follows them while you are scrolled to the bottom and stays put
  when you scroll up to read. A disagreement with the models would show in red.
- **Last branch** (right, top). Branch number, full address and the 8 address bits the chip sees, source line,
  function and instruction (`beq`, `bge`, …), the real outcome, and the clocks the chip took: **11** when the
  perceptron only predicted, **45** when it also trained (its weights changed). Below, four tiles: each predictor's
  guess (T or N) with ✓ or ✗, and "model agrees" (or a red outline and "MODEL SAYS …" if the board ever differed).
- **Accuracy so far** (right). Per predictor: a bar, % correct, mispredictions per 1,000 instructions (MPKI, the
  standard measure in branch-prediction research) and how many answers differed from the model. Under it, the big
  **0**: "differences between the board and the Python models (N branches × 4 checked)".
- **Rolling accuracy** (right, bottom). A Task Manager style graph of each predictor's accuracy over the last 200
  branches (or, switched, the cumulative accuracy), over the whole trace. You see the predictors warm up, and you see
  each section of a program as a step. Under it, the clocks per branch: the average and the split between 11 and 45.

### Compiler view

The third button in the header (**Debugger | Statistics | Compiler**) turns the dashboard into a small IDE for new
programs, so you never need the Streamlit app or a terminal to add one:

- **C source**: an editor with line numbers (Tab indents, **Ctrl+Enter** compiles). **New** starts from a template;
  **Copy current program** puts the program open in the debugger into the editor, to change it; **Open .c file…**
  loads one from disk. The draft is kept in the browser, so a reload doesn't lose it.
- **Name**, the **GCC levels** to build (-O0, -O1, -O2) and **only branches in this file**, then **Compile**.
- **Output**: what happens, live. The program is saved to `tools/my_programs/`, then for each level GCC compiles
  it and Spike runs it, in WSL, in the background (about a second per level). Compile errors appear in red with
  GCC's message; a program that doesn't finish, or runs no branch, is reported too.
- **Traces made**: one row per level with the branches executed, the branches in the code, the instructions, what
  `main()` returned, and an **Open** button.
- **Your programs**: everything in `my_programs/`, including programs saved by the Streamlit app, with **Edit** and
  **Open in debugger**.

When a compile succeeds, the program appears at the top of the **Program** list under **Your programs** and opens in
the debugger (at -O0 if its name starts with `pattern_`, otherwise -O1). From then on it behaves like every other
program: step, run, Jump to its functions, statistics, all on the board.

### Statistics view

- **Accuracy**: four large cards (% correct, points above or below the baseline, number wrong), a table with MPKI,
  the baseline row ("always not taken: 75.21%"), and the board-vs-model counter.
- **Rolling accuracy per predictor**: four Task Manager graphs side by side, one per predictor in its colour, with the
  current value.
- **Cumulative accuracy**: all four on one graph, plus the clock statistics.
- **Branches in the code**: every branch in the program with its line, function, how often it ran, how often it was
  taken, how often it **flips** direction, and each predictor's accuracy on it. Sort it by any predictor's misses to
  find the hard branches.
- **Where predictors agree**: for every pair, the share of branches both got right, only one got right, or both got
  wrong. The note under it explains the tournament: it can only gain where exactly one of bimodal and gshare is right.

## 5. The Streamlit app

The analysis app: it can use the board as the source and compile programs on this PC.

```bash
fpga/.venv/Scripts/python.exe -m streamlit run tools/app/app.py
```

It opens on `http://localhost:8501`. Close the dashboard first: only one program can use the board's port.

| Sidebar | |
|---|---|
| **Example / Your C file / Paste C code** | Pick one of the programs, upload a `.c` file, or paste code into a box (a starter program is filled in). A program that compiles is saved and appears in the example list marked "(yours)" |
| Compiler optimisation | `-O0` to `-Os` |
| Only branches in this file | Leave out branches in the start-up code and the tiny C library |
| **Predictions from** | *FPGA board (Nexys 4 DDR)*, *Chip RTL (Icarus Verilog)* (not installed on this PC), or *Python reference models*. The caption says whether the board was found, or that another program holds its port |
| Try other table sizes | Models only: what if the tables had 16, 32 or 64 entries, or gshare 4–8 history bits |
| **Compile and run** | Compiles in WSL (1–3 s), runs on Spike, replays on the chosen source, computes the statistics |

The page then shows the key numbers (branches, the most accurate predictor and its margin over the baseline, MPKI,
clocks per branch, and "The FPGA board and reference models differ on 0 predictions"), and five tabs:
**Overview** (every branch in a window as a strip with each predictor's misses marked, accuracy against the baseline,
learning curve), **Hard branches** (the branches that cause most misses, why: taken rate against flip rate),
**Source code** (each line's misses next to the C), **Predictors compared** (where one is right and another wrong,
cost against accuracy) and **All examples** (run the whole set and compare). It can download the run as a zip,
`predictions.csv` and `stats.json`.

**Dashboard or Streamlit?** The dashboard shows the run *as it happens*, branch by branch, with the hardware in the
loop. The app analyses a *finished* run with the full statistics, and is where new C code comes in. A natural
demo uses the app for the results and the dashboard for the live part.

## 6. Scripts and commands

| Command | What it does |
|---|---|
| `.\fpga\fpga.ps1 simall -Ram both` | Every trace through the FPGA design in Vivado's simulator, both RAM builds, compared with the models and with the chip testbench (34 runs, about 15 min) |
| `.\fpga\fpga.ps1 sim -Trace <folder> [-Top]` | One trace; `-Top` simulates the whole board design including the clock and 1 Mbaud |
| `.\fpga\fpga.ps1 build -Ram latch` (or `ff`) | Synthesis, place and route, bitstream (about 2.5 min) |
| `.\fpga\fpga.ps1 program -Ram latch` | Load the bitstream over USB (16 s; lost at power-off) |
| `.\fpga\fpga.ps1 flash -Ram latch` | Write it into the board's flash so it loads at power-on (about 1 min) |
| `.\fpga\fpga.ps1 boot` | Make the FPGA reload itself from the flash, as at power-on |
| `.\fpga\fpga.ps1 timesim -Funcsim` | Simulate the netlist Vivado made (checks the latch RAM after synthesis) |
| `bphost.py ping` | Is the board there, which protocol version |
| `bphost.py run <trace> --compare` | Replay one trace (about 48,000 branches/s) and write `predictions.csv`, compared with the models |
| `bphost.py runall` | Every trace, each compared with the models (about 12 s) |
| `bpdash.py [--mock]` | The live dashboard |
| `test_bplink.py` | Tests the PC side against the mock board, no hardware needed |
| `programs/make_traces.py` | Compile our own programs (`fpga/programs/*.c`) through WSL into precomputed traces |
| `tools/scripts/setup_toolchain.sh`, `make_precomputed.sh` | Install GCC and Spike; build every example's traces |

In VS Code, **Terminal → Run Task** offers all of these (simulate, build, program, write to flash, ping, replay, the
dashboard, the app); **Ctrl+Shift+B** builds and programs the board.

## 7. The FPGA board

### What runs on it

Inside the FPGA: the 100 MHz board clock is turned into 25 MHz; the chip RTL runs on it exactly as on the real chip;
a small wrapper receives 2-byte packets from the PC (branch, reset, ping, status), drives the chip's pins as the chip's
own testbench does (PC and outcome, a one-clock strobe, wait for the prediction, wait for training to finish), and
sends 2 bytes back: the four predictions and the clock count. A header byte has its top bit set and a data byte
doesn't, so a lost byte can never be read as a command; the board resynchronises by itself.

The chip needs 11 clocks (0.44 µs) to predict and up to 45 (1.8 µs) including training; the USB link needs 20 µs per
branch each way, so the link sets the speed, not the chip.

### At power-on

With the **JP1** jumper on **QSPI**, the FPGA loads the design from its flash within about a second of switching on
(the board's DONE LED lights). The chip resets and clears its memory in a few microseconds. Then:

- LD17 (RGB) is **green**: the chip is ready. LD14 is on.
- LD15 **blinks** about once a second: the design is alive.
- The display shows **`PE   --.--`**: perceptron selected, no branches yet.
- Everything else is off until the first branch arrives.

Pressing **PROG** reloads the design from the flash; switching the board off and on does the same.

### The LEDs (our design)

| LED | Meaning |
|---|---|
| **LD0** | Perceptron's prediction for the last branch (on = predicted taken) |
| **LD1** | Bimodal's prediction |
| **LD2** | gshare's prediction |
| **LD3** | Tournament's prediction |
| **LD4** | What the last branch really did (on = taken) |
| **LD5** | Perceptron was **right** on the last branch (on = right, off = wrong) |
| **LD6** | Bimodal was right |
| **LD7** | gshare was right |
| **LD8** | Tournament was right |
| LD9–LD11 | Not used (off) |
| **LD12** | UART traffic: lights while branches are coming in (stretched to about a tenth of a second so a single step is visible) |
| **LD13** | A framing error happened since power-on or CPU RESET (a garbled or misplaced byte on the link). Should stay off |
| **LD14** | Chip ready (off only during a reset) |
| **LD15** | Heartbeat: blinks about once a second while the design runs |
| **LD16** (RGB) | The selected predictor (SW2–SW3) on the last branch: **green** right, **red** wrong. Off until the first branch. Dimmed on purpose: these LEDs are very bright |
| **LD17** (RGB) | **Green** the chip is ready, **red** the chip is in reset |

The board also has its own LEDs that our design doesn't control: the power LED, **DONE** (on once the FPGA is
configured), and two UART LEDs next to the USB connector that flicker with the serial traffic (the board's manual
calls them LD19 receive and LD20 transmit).

Reading LD0–LD8 while stepping: LD4 is the truth; each of LD0–LD3 is a guess; LD5–LD8 say which guesses matched.
On `alternating`, step slowly: the loop branch and the alternating `if` take turns, and bimodal is wrong on every
other `if`, so about every fourth step LD6 goes off (and LD16 turns red with bimodal selected), while LD5, LD7 and
LD8 stay on once the others have learned the pattern.

### The switches

| Switch | Down | Up |
|---|---|---|
| **SW0** | Display shows a predictor's **accuracy** | Display shows the **number of branches** replayed since the last chip reset |
| **SW1** | The predictor is picked by SW3–SW2 | The display **cycles** through the four predictors, 2 s each |
| **SW3, SW2** | Predictor for the display and LD16: `00` perceptron (**PE**), `01` bimodal (**bi**), `10` gshare (**GS**), `11` tournament (**to**) | |
| SW4–SW15 | Not used | |

### The 7-segment display

- **Accuracy mode** (SW0 down): `PE   84.48` reads "perceptron, 84.48% of branches predicted correctly so far". The
  two letters on the left name the predictor (PE, bi, GS, to); the number is computed on the FPGA itself from its own
  counters, rounded to two decimals the same way the PC does, so it always equals the PC's number for the same run.
  Before the first branch it shows `--.--`; a perfect run shows `100.00`.
- **Count mode** (SW0 up): `46922`, the branches since the last chip reset, without leading zeros.

The counters clear whenever the chip is reset: power-on, the CPU RESET button, or the PC's reset (every new run, the
**Reset chip** button, choosing a program, going back). So the display always describes the same run as the PC.

### The buttons and jumpers

| Item | What it does |
|---|---|
| **CPU RESET** (red button) | Resets the whole design: the chip, the link, the counters. The PC side notices: the next answers would not match its models. Press **Reset chip** in the dashboard afterwards |
| **PROG** | Reloads the FPGA from the flash (with JP1 on QSPI) |
| **JP1** | Programming mode. On **QSPI** the board loads our design from flash at power-on; on JTAG it waits for the PC to load it |
| **JP3** | Power source: USB. The single micro-USB cable on **J6 (PROG)** powers the board, programs it and carries the UART |
| Power switch | On/off |

## 8. What the audience experiences

A suggested 5–7 minute live part, with what people see at each step.

1. **The board on the table.** It boots by itself: green LD17, LD15 blinking, `PE   --.--` on the display.
   "This is our chip, the same Verilog as the tape-out, running on an FPGA."
2. **The dashboard on the projector**, connected (`COM10 · protocol v1`, green). Program **alternating**, GCC -O0.
   **Step** a few times. On screen, the yellow line moves through the C loop; the four tiles show T/N with ✓ and ✗;
   bimodal gets a ✗ on every other pass of the `if` (every fourth branch). On the board at the same moment, LD6 goes
   off with each bimodal miss, LD16 blinks red and green if bimodal is selected, and LD12 flashes with each message. The UART log shows the two
   bytes going out and the two coming back.
3. **Run** at 20 branches/s, then **max**. The rolling graph shows bimodal flat at 50% on that branch while the others
   climb to 100%; the board's display races to `PE   99.80` (flip SW1 up to cycle through all four).
4. **all in one** and **Jump to**: loop4 (bimodal misses every 4th), loop12 (everyone misses the exit), correlated
   (bimodal at a coin flip, gshare and the perceptron learn it), random (everyone 50%), biased (everyone 90%). The
   rolling graphs step from section to section. Jumping back is instant: "the chip can't run backwards, so we reset
   it and replay; it is deterministic".
5. **A real program: sort and search.** Set "stop when Perceptron mispredicts" and press Run a few times: the first
   stops are the setup loop's exit (line 25) and the sort's loop (line 10), and from then on almost always line 11,
   the bubble-sort compare (5,693 of the perceptron's 7,284 misses). The branch table in **Statistics** shows why: taken 49%, flipping direction 35% of the
   time, decided by the data, not by any pattern. A breakpoint on line 20 jumps to the binary search: another 50/50
   branch.
6. **Statistics view.** Accuracy cards against the baseline ("always not taken" would already score 75.2%), the
   per-predictor Task Manager graphs, the agreement matrix ("the tournament can only win where exactly one of its two
   is right").
7. **Your own code.** Switch to **Compiler**, write a loop (or ask someone in the audience for a branch pattern),
   press **Compile**: the output shows GCC and Spike running, three traces appear, and the program opens in the
   debugger, ready to step on the board. (The Streamlit app's **Paste C code** does the same with its full charts.)
8. **The closing line**: the counter under the accuracy table. Every prediction of every branch, checked on real
   hardware against bit-exact models: **0 differences**, on every trace the board has replayed.

## 9. If something looks wrong

| Symptom | Cause and fix |
|---|---|
| Dashboard or app: "COM10 … Access is denied" / "in use by another program" | Only one program can use the board. Close the other (dashboard, app, or a `bphost` command) |
| Badge says "not connected", or no COM port found | Board off or not on J6, or the USB cable is power-only. Check `bphost.py ports` |
| Board is dark after power-on | JP1 not on QSPI (the design isn't loaded): set JP1 to QSPI and press PROG, or run `fpga.ps1 program` |
| Display stuck at `--.--` while the PC runs | SW0 is down and no branch has arrived: check the PC is connected to the board, not the mock |
| LD17 red | The chip is in reset: press **Reset chip** in the dashboard, or CPU RESET on the board |
| LD13 on | A garbled byte on the link since power-on. Runs still check every answer; power-cycle or press CPU RESET to clear it |
| A paste in the app won't compile | The error text is shown. Bare metal: no `printf`, no `malloc`; use static arrays and return from `main` |
| Single steps feel slow | The FTDI USB driver waits up to 16 ms per reply by default; set its latency timer to 1 ms (Device Manager → the COM port → Advanced) |

## 10. Status of this guide (18 October 2026)

Checked on the board: all 17 original trace folders (34 runs, both RAM builds) byte-identical to simulation; the
dashboard's stepping, speeds, breakpoints and board-vs-model check; the display showing `PE  84.48`; the flash boot.
Checked with the mock board only: the newest dashboard (one-screen layout, Jump to, rewind, statistics view, the
Compiler view with real compiles through WSL) and the change that keeps LD13 off when a program connects (the
board's port was in use). The 30 new traces were replayed
through the chip RTL with 0 differences; they, and `all in one`, have not been replayed on the board yet.
