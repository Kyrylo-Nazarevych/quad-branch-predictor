"""Branch predictor lab: upload a C program, see which of its branches each predictor on the chip gets right.

    streamlit run app/app.py

Stages it calls (see pipeline.py): bptrace (compile + Spike + trace), the chip RTL in Icarus
(or the Python reference models), then stats/bpstats.py.
"""
from __future__ import annotations

import html, io, json, re, sys, zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import charts as C  # noqa: E402
import pipeline as P  # noqa: E402
import bpstats as S  # noqa: E402  (stats/bpstats.py, put on sys.path by pipeline)

st.set_page_config(page_title="Branch predictor lab", page_icon="⎇", layout="wide")

st.html("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
html, body, p, li, label, h1, h2, h3, h4, input, textarea, button, td, th,
[data-testid="stMarkdownContainer"], [data-testid="stMetricValue"], [data-testid="stCaptionContainer"] {
  font-family: 'IBM Plex Sans', system-ui, sans-serif; }
[data-testid="stIconMaterial"], .material-symbols-rounded { font-family: 'Material Symbols Rounded' !important; }
code, pre, .mono { font-family: 'IBM Plex Mono', ui-monospace, monospace !important; }
h1 { font-weight: 600 !important; letter-spacing: -0.01em; font-size: 2.1rem !important; }
h2, h3 { font-weight: 600 !important; }
[data-testid="stMetricValue"] { font-weight: 500; font-size: 1.9rem; }
[data-testid="stMetricLabel"] p { color: #59616B; font-size: 0.9rem; }
[data-testid="stMetric"] { border-top: 2px solid #15181C; padding-top: 0.5rem; }
.lede { color: #59616B; font-size: 1.05rem; max-width: 72ch; margin: -0.4rem 0 1.2rem; }
.srctab { border-collapse: collapse; width: 100%; font-size: 13px; }
.srctab td { padding: 1px 10px; vertical-align: top; white-space: pre; }
.srctab td.n { color: #9AA1AA; text-align: right; width: 3em; font-family: 'IBM Plex Mono', monospace; }
.srctab td.c { font-family: 'IBM Plex Mono', monospace; color: #15181C; }
.srctab tr.b td.c { background: #F1F4F8; }
.srctab td.s { font-family: 'IBM Plex Sans', sans-serif; color: #59616B; white-space: nowrap; width: 15.5em; font-size: 12px; }
.bar { display: inline-block; height: 9px; vertical-align: middle; margin-right: 6px; border-radius: 0 3px 3px 0; }
.legend-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin: 0 4px 0 12px; vertical-align: -1px; }
</style>
""")

PRED = P.PREDICTORS
LABEL = C.LABEL
PASTE_TEMPLATE = """/* My program: a loop with a branch that is taken every third time. */
volatile int sink;

int main(void) {
    for (int i = 0; i < 3000; i++) {
        if (i % 3 == 0) sink = i;   /* T, N, N, T, N, N, ... */
        else            sink = -i;
    }
    return 0;
}
"""
WHERE = {"rtl": "the chip RTL in Icarus Verilog", "board": "the chip on the FPGA board (Nexys 4 DDR)",
         "models": "the Python reference models"}
ex = P.examples()

# ------------------------------------------------------------------ sidebar: the program and how to run it
with st.sidebar:
    st.subheader("Program")
    source = st.segmented_control("Source", ["Example", "Your C file", "Paste C code"], default="Example",
                                  label_visibility="collapsed")
    code, name = None, None
    if source == "Paste C code":
        pname = st.text_input("Program name", value="my_program", help="Saved under this name once it compiles, "
                              "so it appears in the Example list. Start it with pattern_ to default to -O0.")
        ptext = st.text_area("C code", height=300, value=PASTE_TEMPLATE, label_visibility="collapsed")
        st.caption("Bare metal: no `printf`, no `malloc`, no files. Use static arrays and `volatile` variables, "
                   "return a value from `main`, and stay under 2 million instructions.")
        tc_ok, tc_why = P.toolchain_status()
        (st.caption if tc_ok else st.warning)(f"Compiler and Spike: {tc_why}.")
        if ptext.strip():
            code, name = ptext.encode(), (re.sub(r"[^A-Za-z0-9_]", "_", pname.strip()) or "my_program") + ".c"
        opt_default = "O0" if (name or "").startswith("pattern_") else "O1"
    elif source == "Your C file":
        up = st.file_uploader("C file", type=["c"], label_visibility="collapsed")
        st.caption("Bare metal: no `printf`, no `malloc`, no files. Use static arrays, "
                   "return a value from `main`, and stay under 2 million instructions.")
        if up is not None:
            code, name = up.getvalue(), up.name
        opt_default = "O1"
    else:
        if not ex:
            st.error(f"No examples found in {P.EXAMPLES}")
            st.stop()
        pick = st.selectbox("Example program", list(ex), label_visibility="collapsed",
                            format_func=lambda k: f"{k}  (yours)" if P.is_mine(ex[k]) else k)
        code, name = ex[pick].read_bytes(), ex[pick].name
        opt_default = "O0" if pick.startswith("pattern_") else "O1"   # plan decision 10
    opts = ["O0", "O1", "O2", "O3", "Os"]
    opt = st.selectbox("Compiler optimisation", opts, index=opts.index(opt_default), key=f"opt_{name}",
                       help="Pattern programs use -O0 so GCC keeps every branch as written.")
    user_only = st.checkbox("Only branches in this file", value=True,
                            help="Drops branches in the start-up code, mini libc and libgcc (division, float).")

    st.subheader("Predictions from")
    rtl_ok, rtl_why = P.rtl_status()
    board_ok, board_why = P.board_status()
    BACKENDS = {"Chip RTL (Icarus Verilog)": "rtl", "FPGA board (Nexys 4 DDR)": "board", "Python reference models": "models"}
    backend_label = st.radio("Backend", list(BACKENDS), index=0 if rtl_ok else 1 if board_ok else 2,
                             label_visibility="collapsed")
    backend = BACKENDS[backend_label]
    if not rtl_ok:
        st.caption(f"Chip RTL unavailable: {rtl_why}. The reference models reproduce the reference RTL's "
                   "RTL results exactly.")
    st.caption(f"FPGA board: {board_why}." + (" The same chip RTL, replayed over USB-UART at 1 Mbaud."
                                               if board_ok else ""))
    bidx, ghist = 6, 8
    with st.expander("Try other table sizes", expanded=False):
        st.caption("Runs on the reference models only; the chip is built with 64 entries and 8 history bits.")
        bidx = st.select_slider("Entries per table", options=[4, 5, 6], value=6, format_func=lambda b: f"{1 << b}")
        ghist = st.select_slider("gshare history bits", options=[4, 5, 6, 7, 8], value=8)
    if (bidx, ghist) != (6, 8):
        backend = "models"

    go = st.button("Compile and run", type="primary", width="stretch", disabled=code is None)


@st.cache_data(show_spinner=False, max_entries=64)
def cached_stats(run_dir: str, stamp: float) -> dict:
    s = S.compute(run_dir)
    S.save(run_dir, s)
    return s


def run_now(name, code, opt, user_only, backend, bidx, ghist):
    with st.status(f"Running {name}", expanded=True) as box:
        try:
            box.write(f"Compiling with riscv64-unknown-elf-gcc -{opt} -march=rv32im and running it on Spike")
            r = P.run_pipeline(name, code, opt=opt, user_only=user_only, backend=backend, bidx=bidx, ghist=ghist)
            where = WHERE[r.backend]
            box.write(f"Replayed {len(r.trace):,} branches through {where}")
            box.write("Computing accuracy, MPKI and branch classes")
            stats = cached_stats(str(r.out), (r.out / "predictions.csv").stat().st_mtime)
            box.update(label=f"{name}: done in {sum(r.timings.values()):.1f} s", state="complete", expanded=False)
            return r, stats
        except P.CompileError as e:
            box.update(label="The program didn't compile", state="error", expanded=True)
            st.code(str(e), language="text")
        except P.SpikeError as e:
            box.update(label="The program didn't finish on Spike", state="error", expanded=True)
            st.write(str(e))
        except P.SimError as e:
            box.update(label="The FPGA board run failed" if backend == "board" else "The chip simulation failed",
                       state="error", expanded=True)
            st.code(str(e), language="text")
        except P.ToolchainMissing as e:
            box.update(label="This machine can't compile programs", state="error", expanded=True)
            st.write(str(e))
    return None, None


if go:
    r, s = run_now(name, code, opt, user_only, backend, bidx, ghist)
    if r is not None:
        st.session_state["result"] = (r, s, dict(bidx=bidx, ghist=ghist, opt=opt, user_only=user_only))
        if source in ("Paste C code", "Your C file"):     # it compiled: keep it in the example list
            saved = P.save_program(name, code)
            st.toast(f"Saved as {saved}: it is in the Example list now.")

# ------------------------------------------------------------------ empty state
if "result" not in st.session_state:
    st.title("Branch predictor lab")
    st.markdown('<p class="lede">Four branch predictors share one Tiny Tapeout chip: the 704 perceptron, '
                'bimodal, gshare and a tournament of the two. Pick an example or upload a C program, and '
                'see which of its branches each one gets right.</p>', unsafe_allow_html=True)
    st.markdown("**What happens when you press Compile and run**")
    st.markdown(
        "1. GCC compiles the program for a 32-bit RISC-V core (`rv32im`), with debug info.\n"
        "2. Spike, the reference RISC-V simulator, runs it and logs every instruction.\n"
        "3. Every conditional branch and its real outcome is extracted, with its source line.\n"
        "4. The branches are replayed through the chip, which predicts each one before learning the outcome.\n"
        "5. You get accuracy, mispredictions per thousand instructions, and the branches that are hard to predict.")
    st.stop()

run, stats, params = st.session_state["result"]
dyn, _, summary = S.load(run.out)
pb = S.per_branch(dyn)
best = stats["best"]

# ------------------------------------------------------------------ header and key numbers
st.title(run.source_name)
facts = (f"-{params['opt']}, {summary.get('march')}, "
         f"{'branches in this file only' if params['user_only'] else 'all branches'}")
src_txt = WHERE[run.backend]
st.markdown(f'<p class="lede">Compiled with {html.escape(facts)}. Predictions from {src_txt}'
            + (f", {1 << params['bidx']} entries per table and {params['ghist']} history bits" if (params['bidx'], params['ghist']) != (6, 8) else "")
            + ".</p>", unsafe_allow_html=True)
if run.truncated:
    st.warning(f"The program executed {summary.get('branches_total', 0):,} branches; only the first "
               f"{stats['branches']:,} were replayed to keep the simulation under a minute.")
if run.rtl_vs_models is not None:
    who = "The FPGA board" if run.backend == "board" else "Chip RTL"
    (st.success if run.rtl_vs_models == 0 else st.error)(
        f"{who} and reference models differ on {run.rtl_vs_models} predictions.")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Conditional branches executed", f"{stats['branches']:,}",
          help=f"{stats['static_branches']} different branches in the code, {stats['instructions']:,} instructions in total")
k2.metric("Most accurate", f"{100 * stats['predictors'][best]['accuracy']:.1f}%",
          delta=f"{100 * stats['predictors'][best]['vs_baseline']:+.1f} points vs baseline", delta_color="normal",
          help=f"{LABEL[best]}. Baseline: always guess {stats['baseline']['direction']}, "
               f"{100 * stats['baseline']['accuracy']:.1f}% right")
k2.caption(LABEL[best])
mp = stats["predictors"][best]["mpki"]
k3.metric("Mispredictions per 1,000 instructions", f"{mp:.1f}" if mp is not None else "n/a", help=f"MPKI of {LABEL[best]}")
pc = stats["predictors"]["perceptron"]
k4.metric("Clocks per branch", f"1 vs {pc['clocks']:g}",
          help=f"Tables answer in 1 clock; the perceptron's latch RAM reads one byte per clock ({pc['clocks_source']}).")
k4.caption("tables vs perceptron")

tab_over, tab_br, tab_src, tab_cmp, tab_all = st.tabs(
    ["Overview", "Hard branches", "Source code", "Predictors compared", "All examples"], key="tab", on_change="rerun")

# ------------------------------------------------------------------ overview
with tab_over:
    st.subheader("Every branch, as the chip saw it")
    st.caption("Top row: what the program really did, filled for taken and hollow for not taken. Below it, a "
               "coloured mark wherever a predictor guessed wrong. Hover a mark for its source line.")
    n = len(dyn)
    width = min(120, n)
    if n > width:
        start = st.slider("Window starts at branch", 0, n - width, S.busiest_window(dyn, width),
                          step=max(1, width // 4), key=f"win_{run.key}",
                          help="Opens where the predictors disagree most; drag to see the start of the run.")
    else:
        start = 0
    st.plotly_chart(C.trace_strip(dyn, start, width), width="stretch", config={"displayModeBar": False})

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Accuracy")
        st.plotly_chart(C.accuracy_dots(stats), width="stretch", config={"displayModeBar": False})
    with right:
        st.subheader("In numbers")
        tbl = pd.DataFrame([{
            "Predictor": LABEL[k], "Correct": 100 * stats["predictors"][k]["accuracy"],
            "vs baseline": 100 * stats["predictors"][k]["vs_baseline"],
            "Wrong": stats["predictors"][k]["mispredictions"], "MPKI": stats["predictors"][k]["mpki"]} for k in PRED])
        st.dataframe(tbl, hide_index=True, width="stretch", column_config={
            "Correct": st.column_config.NumberColumn(format="%.2f%%"),
            "vs baseline": st.column_config.NumberColumn("vs baseline", format="%+.2f pts",
                                                         help=f"Baseline: always {stats['baseline']['direction']}, "
                                                              f"{100 * stats['baseline']['accuracy']:.2f}%"),
            "Wrong": st.column_config.NumberColumn(format="%d"),
            "MPKI": st.column_config.NumberColumn(format="%.1f")})
        st.caption(f"Baseline: always guess {stats['baseline']['direction']}, right "
                   f"{100 * stats['baseline']['accuracy']:.1f}% of the time. A predictor is only useful above it.")

    st.subheader("Learning curve")
    window = 200
    span = st.segmented_control("Show", ["First 2,000 branches", "Whole run"], default="First 2,000 branches",
                                key="lc_span", label_visibility="collapsed") or "First 2,000 branches"
    part = dyn.iloc[:2000] if span.startswith("First") else dyn
    st.caption("Accuracy over the last 200 branches. The first branches show how fast each predictor learns.")
    st.plotly_chart(C.learning_curve(S.learning_curve(part, window), window), width="stretch",
                    config={"displayModeBar": False})

# ------------------------------------------------------------------ hard branches
with tab_br:
    who = st.segmented_control("Predictor", PRED, default=best, format_func=lambda k: LABEL[k], key="who_br") or best
    hard = S.hard_branches(pb, who, stats["instructions"])
    total_miss = int(pb[f"miss_{who}"].sum())
    if len(hard):
        st.subheader(f"{len(hard)} branches cause {100 * hard['share_of_misses'].sum():.0f}% of {LABEL[who]}'s "
                     f"{total_miss:,} mispredictions")
    else:
        st.subheader(f"No hard branches for {LABEL[who]}")
    st.caption("Hard means executed at least 100 times, wrong at least 10 times and right less than 99% of the time "
               "(Lin and Tarsa, IISWC 2019, scaled down to small programs).")
    if len(hard):
        view = hard[["line", "function", "count", "taken_rate", "transition_rate", f"acc_{who}", f"miss_{who}", "share_of_misses"]]
        st.dataframe(view, hide_index=True, width="stretch", column_config={
            "line": st.column_config.TextColumn("Source line"), "function": "Function",
            "count": st.column_config.NumberColumn("Executed", format="%d"),
            "taken_rate": st.column_config.NumberColumn("Taken", format="percent"),
            "transition_rate": st.column_config.NumberColumn("Flips", format="percent",
                                                             help="Share of executions where the direction changed"),
            f"acc_{who}": st.column_config.NumberColumn("Correct", format="percent"),
            f"miss_{who}": st.column_config.NumberColumn("Wrong", format="%d"),
            "share_of_misses": st.column_config.ProgressColumn("Share of all misses", format="percent", min_value=0, max_value=1)})
    st.subheader("Why a branch is hard")
    st.caption("Each dot is one branch in the code, sized by how often it ran. Branches in the corners are easy: "
               "almost always one way (left or right edge) or a regular flip. Branches in the middle depend on "
               "the data, and no predictor can learn them from history.")
    st.plotly_chart(C.branch_scatter(pb, who), width="stretch", config={"displayModeBar": False})
    with st.expander("All branches"):
        st.dataframe(pb[["pc", "line", "function", "mnemonic", "count", "taken_rate", "transition_rate",
                         *[f"acc_{k}" for k in PRED]]], hide_index=True, width="stretch",
                     column_config={**{f"acc_{k}": st.column_config.NumberColumn(LABEL[k], format="percent") for k in PRED},
                                    "taken_rate": st.column_config.NumberColumn("Taken", format="percent"),
                                    "transition_rate": st.column_config.NumberColumn("Flips", format="percent"),
                                    "count": st.column_config.NumberColumn("Executed", format="%d")})

# ------------------------------------------------------------------ source with per-line results
with tab_src:
    who_s = st.segmented_control("Predictor", PRED, default=best, format_func=lambda k: LABEL[k], key="who_src") or best
    mine = pb[pb["line"].astype(str).str.rsplit(":", n=1).str[0] == run.source_name]
    per_line = mine.groupby("line_no").agg(count=("count", "sum"), miss=(f"miss_{who_s}", "sum"))
    maxmiss = max(1, int(per_line["miss"].max()) if len(per_line) else 1)
    st.caption(f"Lines with a conditional branch are shaded. The bar is {LABEL[who_s]}'s mispredictions on that line.")
    rows = []
    for i, text in enumerate(run.source_text.splitlines(), start=1):
        cell = ""
        cls = ""
        if i in per_line.index:
            c, m = int(per_line.loc[i, "count"]), int(per_line.loc[i, "miss"])
            w = 3 + 70 * m / maxmiss
            cls = ' class="b"'
            cell = (f'<span class="bar" style="width:{w:.0f}px;background:{C.COLOR[who_s]}"></span>'
                    f'{m:,} of {c:,} wrong')
        rows.append(f'<tr{cls}><td class="n">{i}</td><td class="s">{cell}</td><td class="c">{html.escape(text) or " "}</td></tr>')
    st.html(f'<div style="overflow-x:auto"><table class="srctab">{"".join(rows)}</table></div>')

# ------------------------------------------------------------------ predictors compared
with tab_cmp:
    a, b = st.columns(2, gap="large")
    with a:
        st.subheader("Where one is right and another wrong")
        ag = stats["agreement"]
        bg = ag["bimodal|gshare"]
        st.caption(f"Each cell: share of all branches where the row's predictor is right and the column's is wrong. "
                   f"A tournament can only gain where exactly one of its two predictors is right: for bimodal and "
                   f"gshare that is {100 * (bg['only_a'] + bg['only_b']):.1f}% of branches. Both are wrong on "
                   f"{100 * bg['both_wrong']:.1f}%, which no chooser can fix.")
        st.plotly_chart(C.win_matrix(ag), width="stretch", config={"displayModeBar": False})
    with b:
        st.subheader("What each predictor costs")
        st.caption("Storage on the chip against accuracy on this program. Area replaces storage once the "
                   "GDS run reports it per module.")
        st.plotly_chart(C.cost_scatter(stats), width="stretch", config={"displayModeBar": False})
        st.dataframe(pd.DataFrame([{"Predictor": LABEL[k], "Storage (bits)": stats["predictors"][k]["storage_bits"],
                                    "Clocks per branch": stats["predictors"][k]["clocks"],
                                    "What it stores": stats["predictors"][k]["what"]} for k in PRED]),
                     hide_index=True, width="stretch")

# ------------------------------------------------------------------ all examples
with tab_all:
    st.subheader("Every example program")
    st.caption("Pattern programs run at -O0 and the rest at -O1, each from a fresh chip. Use this chart for the "
               "results slide.")
    if st.button("Run all examples", key="run_all"):
        rows, branch_rows = [], []
        prog = st.progress(0.0)
        for i, (nm, path) in enumerate(ex.items()):
            o = "O0" if nm.startswith("pattern_") else "O1"
            try:
                rr = P.run_pipeline(path.name, path.read_bytes(), opt=o, backend=backend, bidx=bidx, ghist=ghist)
                ss = cached_stats(str(rr.out), (rr.out / "predictions.csv").stat().st_mtime)
                rows.append({"program": nm, "opt": f"-{o}", "branches": ss["branches"],
                             "baseline": ss["baseline"]["accuracy"],
                             **{k: ss["predictors"][k]["accuracy"] for k in PRED}})
                if nm.startswith("pattern_"):
                    code_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    for b in ss["per_branch"]:
                        ln = int(str(b["line"]).rsplit(":", 1)[-1]) if str(b["line"]).rsplit(":", 1)[-1].isdigit() else 0
                        branch_rows.append({"program": nm, "line": ln,
                                            "code": code_lines[ln - 1].strip() if 0 < ln <= len(code_lines) else "?",
                                            "executed": b["count"], "taken": b["taken_rate"], "flips": b["transition_rate"],
                                            **{k: 100 * b[f"acc_{k}"] for k in PRED}})
            except Exception as e:   # one failing example must not hide the others
                st.warning(f"{nm}: {e}")
            prog.progress((i + 1) / len(ex))
        prog.empty()
        st.session_state["bench"] = pd.DataFrame(rows)
        st.session_state["bench_branches"] = pd.DataFrame(branch_rows)
    if "bench" in st.session_state and len(st.session_state["bench"]):
        bench = st.session_state["bench"]
        st.plotly_chart(C.benchmark_bars(bench), width="stretch")
        st.dataframe(bench, hide_index=True, width="stretch", column_config={
            "baseline": st.column_config.NumberColumn("Baseline", format="percent"),
            "branches": st.column_config.NumberColumn("Branches", format="%d"),
            **{k: st.column_config.NumberColumn(LABEL[k], format="percent") for k in PRED}})
        bb = st.session_state.get("bench_branches")
        if bb is not None and len(bb):
            st.subheader("Pattern programs, branch by branch")
            st.caption("Whole-program accuracy mixes the pattern with its loop branch. This table gives each branch on "
                       "its own: the pattern rows are the ones the slides talk about.")
            st.dataframe(bb, hide_index=True, width="stretch", column_config={
                "line": st.column_config.NumberColumn("Line", format="%d"),
                "code": st.column_config.TextColumn("Source", width="medium"),
                "executed": st.column_config.NumberColumn("Executed", format="%d"),
                "taken": st.column_config.NumberColumn("Taken", format="percent"),
                "flips": st.column_config.NumberColumn("Flips", format="percent"),
                **{k: st.column_config.NumberColumn(LABEL[k], format="%.1f%%") for k in PRED}})

# ------------------------------------------------------------------ downloads
with st.sidebar:
    st.subheader("Download")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(run.out.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(run.out))
    st.download_button("All files for this run (.zip)", buf.getvalue(), file_name=f"{Path(run.source_name).stem}-run.zip",
                       mime="application/zip", width="stretch")
    st.download_button("predictions.csv", (run.out / "predictions.csv").read_bytes(), file_name="predictions.csv",
                       mime="text/csv", width="stretch")
    st.download_button("stats.json", json.dumps(stats, indent=2), file_name="stats.json", mime="application/json",
                       width="stretch")
    for note in run.notes:
        st.caption(note)
