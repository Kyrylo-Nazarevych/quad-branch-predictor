# Verification data and tools

Reference branch traces, the predictions the chip must produce for them, bit-exact Python models of the four
predictors, and tools to compare and explore.

| Path | What |
|---|---|
| `traces/<name>/` | One trace per program: `trace_init.vh` (Verilog `include`), `trace.mem` (`$readmemh`), `tb_input.hex` (`pc8 taken` per line), `trace.txt` (full PC), `branches.csv` and the C source, `summary.json` |
| `expected/<name>/predictions.csv` | What each predictor must predict for every branch (`index,pc8,taken,perceptron,bimodal,gshare,tournament,cycles`) |
| `models/ref_models.py` | The four predictors in Python, with the table size (`BIDX`) and history length (`GHIST`) as parameters |
| `tools/compare_predictions.py` | Diff a simulator's or the board's output against the models; prints `IDENTICAL` or the first differing branches |
| `tools/sweep_params.py` | Accuracy for every table size and history length, in a few seconds |

## The traces

| Trace | Branches | Perceptron | Bimodal | gshare | Tournament | What it tests |
|---|---|---|---|---|---|---|
| `ref704_committed_log` | 1,271 | 81.98 | 66.88 | 86.47 | 88.83 | The 704 design's own reference program |
| `sort_and_search_O1` | 46,922 | 84.48 | 84.07 | 82.83 | 83.48 | Bubble sort and binary search |
| `pattern_alternating_O0` | 4,001 | 99.80 | 74.98 | 99.93 | 99.90 | Line 7 goes T, N, T, N: bimodal gets exactly 50% on it |
| `pattern_loop4_O0` | 6,001 | 99.78 | 83.32 | 99.95 | 99.93 | Inner loop of 4: bimodal misses every exit (80%) |
| `pattern_xor_O0` | 12,001 | 63.01 | 62.43 | 61.47 | 62.52 | Line 17 is the XOR of lines 15 and 16: no predictor learns it with 8 history bits |

More traces (15 programs × `-O0/-O1/-O2`) are in `../tools/precomputed/`.

## Use

Simulate the chip on a trace and compare (Icarus):

```bash
cp traces/sort_and_search_O1/trace_init.vh ../fpga/sim/
cd ../fpga/sim && iverilog -g2012 -o tbteam tb_team.v ../../rtl/*.v && vvp -n tbteam > sim_out.txt
python3 ../../verification/tools/compare_predictions.py ../../verification/traces/sort_and_search_O1 sim_out.txt
```

The tool reads the `R,` lines from the simulator output and exits with 0 when every prediction matches. If you
build the RTL with other parameters, pass the same ones: `--bidx 5 --ghist 6`.

Try parameters on the models before changing the RTL:

```bash
python3 tools/sweep_params.py traces/*
python3 tools/sweep_params.py traces/sort_and_search_O1 --bidx 5 6 --ghist 6 7 8
```

## Rules the RTL and the models share

1. Inputs per branch: `pc8 = PC[7:0]` and the real outcome. Tables index with `PC[7:2]`, the perceptron with `PC[4:2]`.
2. Every 2-bit counter resets to 2 (weakly taken) and predicts taken at 2 or 3.
3. gshare index = `PC[7:2] XOR (GHR[5:0] ^ {4'b0, GHR[7:6]})`; the newest outcome shifts into bit 0.
4. The chooser resets to 1 (weakly bimodal), picks gshare at 2 or 3, and trains only when bimodal and gshare
   disagree, towards whichever was right.
5. All predictions are captured **before** any table or history is updated for that branch.
6. Perceptron: 8 × (bias + 7 weights), 8-bit wrapping weights, 10-bit wrapping sum, inputs 0/1 to predict and ±1 to
   train, threshold 15.
