# tools: from a C program to branch statistics

Compile C for RISC-V, run it on Spike, extract the conditional branches, and analyse how the four predictors do on
them. Run every command below from this `tools/` folder.

## 1. Get it into WSL (Windows) or a Linux machine

Clone the repository **inside WSL**, in your Linux home folder. Working from `/mnt/c/...` is much slower and Windows
drops the "executable" flags.

```bash
git clone https://github.com/Kyrylo-Nazarevych/quad-branch-predictor.git ~/quad-branch-predictor
cd ~/quad-branch-predictor/tools
```

## 2. Install the toolchain (once, about 15 minutes)

```bash
bash scripts/setup_toolchain.sh
source ~/.bashrc
```

It asks for your password (for `apt`), installs the RISC-V compiler, Icarus Verilog and the build tools,
then builds Spike from source into `~/riscv-tools` (the slow part: about 11 minutes on 2 cores).
It warns if your Ubuntu is not 24.04; reference numbers come from a 24.04 machine (other versions ship another GCC).

## 3. Check it works

```bash
bash test.sh
```

Takes about 2 minutes the first time. It ends with `ALL PASSED` when:
the toolchain reproduces the 704 numbers (0.819827 and 0.863887), the trace tool gives 46,922 branches for the example,
the predictor models match every reference result, and the app's 8 tests pass.

## 4. Run the app

```bash
bash run.sh
```

Then open **http://localhost:8501** in your Windows browser (WSL forwards it). Stop with `Ctrl+C`.
The first start sets up a Python environment in `.venv` (about a minute); later starts are instant.

Pick an example and press **Compile and run**, or switch to **Your C file** and upload one.
Programs must be bare metal: no `printf`, no `malloc`, return a value from `main`, under 2 million instructions.

## What is in the folder

| Folder | What it is |
|---|---|
| `bptrace/` | Trace tool: C file → RISC-V ELF → Spike → branch trace files. Patched version (see `docs/bptrace.patch`) |
| `bptrace/runtime/` | Start-up code, linker script and mini C library the programs are linked with |
| `examples/` | The C programs: pattern demos and workloads |
| `app/` | Streamlit app (`app.py`), stage runner (`pipeline.py`), charts, tests, its own README |
| `stats/` | `bpstats.py` (accuracy, MPKI, hard branches, …) and `ref_models.py` (Python models of the four predictors) |
| `scripts/` | `setup_toolchain.sh` (install), `check_toolchain.sh` (self-test), `make_precomputed.sh` |
| `precomputed/` | Example traces at -O0/-O1/-O2, so the examples run even without the toolchain. Rebuild with `scripts/make_precomputed.sh` |
| `docs/` | `bptrace.patch`: what was changed in the original trace tool and why |
| `run.sh`, `test.sh` | Start the app; run every check |

Created when you use it: `.venv/` (Python packages), `runs/` (cached app runs; delete to start over),
`tt_branch_pred/` (the 704 repo, cloned by the tests).

## The chip RTL in the app

The app's "Chip RTL (Icarus Verilog)" source looks for `hw_reference/` and `sim/run_sim.py` here. Without them it
uses the Python models (identical results) or the FPGA board. The RTL itself is in `../rtl/`, and
`../verification/` has the scripts to simulate and compare it.

## Troubleshooting

| You see | Do this |
|---|---|
| `Note: riscv64-unknown-elf-gcc spike dtc not found` | Only the examples work until you run `bash scripts/setup_toolchain.sh` and `source ~/.bashrc` |
| `python3 -m venv` fails | `sudo apt install -y python3-venv`, then `rm -rf .venv` and run again |
| `bash\r: No such file` or `$'\r': command not found` | The files were checked out or edited on Windows. Clone again inside WSL as in step 1 |
| The browser can't reach localhost:8501 | Check `run.sh` is still running; try `http://127.0.0.1:8501`; or start with `PORT=8502 bash run.sh` |
| Port already in use | Another app is running: `PORT=8502 bash run.sh` |
| The app shows an old result after you changed code | Delete `runs/` and press Compile and run again |
