# Branch predictor lab (Streamlit app)

Upload a C program, compile it for RISC-V, run it on Spike, replay its branches through the chip,
and see accuracy per predictor, per branch and per source line.

## Run it

From the `tools/` folder (the one with `bptrace/` and `examples/`):

```bash
pip install -r app/requirements.txt
export SPIKE=$HOME/riscv-tools/bin/spike      # or have spike on PATH
streamlit run app/app.py                       # opens http://localhost:8501
python3 -m pytest app/test_app.py -q           # 6 headless tests, about 10 s
```

Use the patched `bptrace.py` (it caps runaway programs and counts instructions correctly).
The app also works with the original, minus the instruction cap.

## Files

| File | What it does |
|---|---|
| `app/app.py` | The page: sidebar, key numbers, five tabs, downloads |
| `app/pipeline.py` | Calls each stage, caches runs in `runs/<hash>/`, turns failures into readable errors |
| `app/charts.py` | Plotly figures, one fixed colour per predictor |
| `stats/bpstats.py` | Accuracy, MPKI, baseline, per-branch classes, hard list, learning curve, agreement, cost |
| `stats/ref_models.py` | Python reference models of all four predictors |
| `.streamlit/config.toml` | Theme, 1 MB upload limit, minimal toolbar |

## How each stage is called

| Stage | Preferred | Assumption | Fallback |
|---|---|---|---|
| Trace | `bptrace.build_trace(src, out, opt=, march=, user_only=, pc_bits=, timeout_s=)` returning the `summary.json` dict | Planned signature | `bptrace/bptrace.py` command line |
| Predictions | `sim/run_sim.py` with `hw_reference/` and Icarus | Either a function `run_sim(trace_dir)` returning a DataFrame, or the command line `python3 sim/run_sim.py <trace_dir>` writing `<trace_dir>/predictions.csv` with the `predictions.csv` columns | `stats/ref_models.py` |
| Statistics | `stats/bpstats.py` (this app ships it) | Joins predictions to source lines by **row index** through `trace.txt`, never by `pc8` | — |

When the chip RTL is available the app uses it and also runs the reference models, then reports how many
predictions differ.

The reference models were checked against every accuracy the reference RTL produces and match all of them:
704 reference log (81.98 / 66.88 / 86.47 / 88.83), `sort_and_search -O1` (84.48 / 84.07 / 82.83 / 83.48),
the three pattern branches, and the gshare history table (GHIST 4 / 6 / 8). Run
`python3 stats/ref_models.py --selftest --kit . --t704 <path to tt_branch_pred>`.

## Limits

- Programs must finish within 2,000,000 instructions (patched bptrace). Traces over 100,000 branches are cut,
  and the page says so.
- Runs are cached by file content and options, so a repeated demo run is instant. Delete `runs/` to start over.
- "Try other table sizes" runs the reference models only; the chip itself has 64 entries and 8 history bits.
- Clocks per branch for the perceptron come from the `cycles` column when the RTL ran; otherwise the 704
  reference value (25.6). Area per predictor comes from per-module synthesis (`COST` in `stats/bpstats.py`).
