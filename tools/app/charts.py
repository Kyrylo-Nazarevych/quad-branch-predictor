"""Plotly figures for the app. One colour per predictor, in a fixed order, everywhere."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

PREDICTORS = ("perceptron", "bimodal", "gshare", "tournament")
LABEL = {"perceptron": "Perceptron (704)", "bimodal": "Bimodal", "gshare": "gshare", "tournament": "Tournament"}
# Reference categorical palette, slots 1-4 (validated for adjacent pairs; scatter charts use one hue only)
COLOR = {"perceptron": "#2a78d6", "bimodal": "#eb6834", "gshare": "#1baf7a", "tournament": "#eda100"}
INK, INK2, RULE, MUTED = "#15181C", "#59616B", "#DADEE3", "#B9BFC7"
FONT = "IBM Plex Sans, system-ui, sans-serif"
MONO = "IBM Plex Mono, ui-monospace, monospace"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]   # blue ramp, light -> dark


def _base(fig: go.Figure, height: int, **kw) -> go.Figure:
    layout = dict(
        height=height, margin=dict(l=8, r=16, t=8, b=8), paper_bgcolor="white", plot_bgcolor="white",
        font=dict(family=FONT, size=13, color=INK), hoverlabel=dict(font=dict(family=FONT, size=12), bgcolor="white"),
        showlegend=False)
    layout.update(kw)
    fig.update_layout(**layout)
    fig.update_xaxes(gridcolor="#EEF0F2", zeroline=False, linecolor=RULE, tickfont=dict(color=INK2), title_font=dict(color=INK2, size=12))
    fig.update_yaxes(gridcolor="#EEF0F2", zeroline=False, linecolor=RULE, tickfont=dict(color=INK2), title_font=dict(color=INK2, size=12))
    return fig


def trace_strip(dyn: pd.DataFrame, start: int, width: int = 160) -> go.Figure:
    """Logic-analyser view: the real outcome of each branch, then a mark wherever a predictor was wrong."""
    seg = dyn.iloc[start:start + width]
    x = seg["index"].to_numpy()
    rows = ["Outcome", *[LABEL[k] for k in PREDICTORS]]
    fig = go.Figure()
    tk = seg["taken"].to_numpy().astype(bool)
    hover = [f"branch {i}<br>{ln}<br>{'taken' if t else 'not taken'}" for i, ln, t in zip(x, seg["line"].fillna("?"), tk)]
    fig.add_trace(go.Scatter(x=x[tk], y=["Outcome"] * tk.sum(), mode="markers", hovertext=np.array(hover)[tk], hoverinfo="text",
                             marker=dict(symbol="square", size=7, color=INK)))
    fig.add_trace(go.Scatter(x=x[~tk], y=["Outcome"] * (~tk).sum(), mode="markers", hovertext=np.array(hover)[~tk], hoverinfo="text",
                             marker=dict(symbol="square-open", size=7, color=INK2, line=dict(width=1.2))))
    for k in PREDICTORS:
        miss = (seg[k].to_numpy() != seg["taken"].to_numpy())
        fig.add_trace(go.Scatter(x=x[~miss], y=[LABEL[k]] * (~miss).sum(), mode="markers", hoverinfo="skip",
                                 marker=dict(symbol="circle", size=3, color=MUTED)))
        fig.add_trace(go.Scatter(x=x[miss], y=[LABEL[k]] * miss.sum(), mode="markers", hoverinfo="text",
                                 hovertext=[f"{LABEL[k]} wrong at branch {i}<br>{ln}" for i, ln in zip(x[miss], seg["line"].to_numpy()[miss])],
                                 marker=dict(symbol="line-ns", size=16, line=dict(width=3, color=COLOR[k]))))
    _base(fig, 230)
    fig.update_yaxes(categoryorder="array", categoryarray=rows[::-1], showgrid=False, tickfont=dict(color=INK, size=13))
    fig.update_xaxes(title_text="branch number (in execution order)", showgrid=False, tickformat="d",
                     range=[x.min() - 1 if len(x) else 0, x.max() + 1 if len(x) else 1])
    return fig


def accuracy_dots(stats: dict) -> go.Figure:
    """Accuracy per predictor against the majority-direction baseline."""
    b = 100 * stats["baseline"]["accuracy"]
    acc = {k: 100 * stats["predictors"][k]["accuracy"] for k in PREDICTORS}
    lo = min([b, *acc.values()])
    fig = go.Figure()
    for k in PREDICTORS:
        fig.add_trace(go.Scatter(
            x=[b, acc[k]], y=[LABEL[k]] * 2, mode="lines", line=dict(color=RULE, width=2), hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            x=[acc[k]], y=[LABEL[k]], mode="markers+text", text=[f"{acc[k]:.2f}%"], textposition="middle right", cliponaxis=False,
            textfont=dict(color=INK, size=13), marker=dict(size=14, color=COLOR[k], line=dict(width=2, color="white")),
            hovertemplate=f"{LABEL[k]}: %{{x:.2f}}%<br>{acc[k] - b:+.2f} points vs baseline<extra></extra>"))
    fig.add_vline(x=b, line=dict(color=INK2, width=1.5, dash="dash"))
    fig.add_annotation(x=b, y=1.0, yref="paper", yanchor="bottom", xanchor="left", showarrow=False,
                       text=f" always {stats['baseline']['direction']}: {b:.1f}%", font=dict(color=INK2, size=12))
    _base(fig, 250, margin=dict(l=8, r=70, t=28, b=8))
    fig.update_yaxes(categoryorder="array", categoryarray=[LABEL[k] for k in PREDICTORS][::-1], showgrid=False,
                     tickfont=dict(color=INK, size=13))
    fig.update_xaxes(title_text="branches predicted correctly (%)", range=[max(0, lo - 4), 100.5], ticksuffix="%")
    return fig


def learning_curve(lc: pd.DataFrame, window: int) -> go.Figure:
    fig = go.Figure()
    for k in PREDICTORS:
        fig.add_trace(go.Scatter(x=lc["branch"], y=100 * lc[k], mode="lines", name=LABEL[k],
                                 line=dict(color=COLOR[k], width=2),
                                 hovertemplate=f"{LABEL[k]}: %{{y:.1f}}%<extra></extra>"))
    _base(fig, 300, hovermode="x unified", showlegend=True,
          legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(size=12)),
          margin=dict(l=8, r=16, t=36, b=8))
    fig.update_yaxes(title_text=f"correct over the last {window} branches (%)", ticksuffix="%", range=[0, 102])
    fig.update_xaxes(title_text="branch number", tickformat=",d")
    return fig


def branch_scatter(pb: pd.DataFrame, predictor: str) -> go.Figure:
    """Chang et al. (bias) x Haungs et al. (transition rate), one dot per static branch."""
    acc = 100 * pb[f"acc_{predictor}"]
    size = 8 + 28 * np.sqrt(pb["count"] / pb["count"].max())
    fig = go.Figure(go.Scatter(
        x=pb["taken_rate"], y=pb["transition_rate"], mode="markers",
        marker=dict(size=size, color=acc, cmin=50, cmax=100,
                    colorscale=[[0, SEQ[6]], [0.5, SEQ[4]], [0.8, SEQ[2]], [1, SEQ[0]]],
                    line=dict(width=1.5, color="white"),
                    colorbar=dict(title=dict(text="correct", font=dict(size=12, color=INK2)), ticksuffix="%",
                                  thickness=10, len=0.8, tickfont=dict(color=INK2, size=11))),
        customdata=np.stack([pb["line"].fillna("?"), pb["function"].fillna("?"), pb["count"], acc,
                             pb[f"miss_{predictor}"]], axis=1),
        hovertemplate=("%{customdata[0]} in %{customdata[1]}<br>executed %{customdata[2]:,} times"
                       "<br>taken %{x:.0%}, changes direction %{y:.0%} of the time"
                       f"<br>{LABEL[predictor]} correct %{{customdata[3]:.1f}}% (%{{customdata[4]:,}} wrong)<extra></extra>")))
    worst = pb.sort_values(f"miss_{predictor}", ascending=False).head(3)
    for _, r in worst[worst[f"miss_{predictor}"] > 0].iterrows():
        fig.add_annotation(x=r["taken_rate"], y=r["transition_rate"], text=f"line {str(r['line']).split(':')[-1]}",
                           showarrow=True, arrowhead=0, arrowcolor=INK2, ax=28, ay=-22, font=dict(size=12, color=INK))
    _base(fig, 360)
    fig.update_xaxes(title_text="taken rate (how biased the branch is)", range=[-0.05, 1.05], tickformat=".0%")
    fig.update_yaxes(title_text="transition rate (how often it flips)", range=[-0.05, 1.05], tickformat=".0%")
    return fig


def win_matrix(agree: dict) -> go.Figure:
    """Row predictor right while column predictor wrong, as a share of all branches."""
    names = [LABEL[k] for k in PREDICTORS]
    z = [[None] * 4 for _ in range(4)]
    for key, v in agree.items():
        a, b = key.split("|")
        i, j = PREDICTORS.index(a), PREDICTORS.index(b)
        z[i][j], z[j][i] = 100 * v["only_a"], 100 * v["only_b"]
    zmax = max(x for row in z for x in row if x is not None)
    text = [[("" if x is None else f"{x:.1f}%") for x in row] for row in z]
    fig = go.Figure(go.Heatmap(
        z=z, x=names, y=names, text=text, texttemplate="%{text}", textfont=dict(size=13),
        colorscale=[[0, SEQ[0]], [1, SEQ[4]]], zmin=0, zmax=zmax, showscale=False, xgap=3, ygap=3,
        hovertemplate="%{y} right, %{x} wrong:<br>%{z:.1f}% of branches<extra></extra>"))
    _base(fig, 320, margin=dict(l=8, r=16, t=8, b=8))
    fig.update_xaxes(side="top", showgrid=False, tickfont=dict(color=INK), title_text="… is wrong")
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(color=INK), title_text="when this one is right …")
    return fig


def cost_scatter(stats: dict) -> go.Figure:
    """Accuracy against storage on the chip; area replaces storage when it is set in COST."""
    fig = go.Figure()
    use_area = all(stats["predictors"][k].get("area_um2") for k in PREDICTORS)
    for k in PREDICTORS:
        p = stats["predictors"][k]
        xv = p["area_um2"] if use_area else p["storage_bits"]
        fig.add_trace(go.Scatter(x=[xv], y=[100 * p["accuracy"]], mode="markers+text", text=[f"  {LABEL[k]}"],
                                 textposition="middle right", textfont=dict(size=12, color=INK), cliponaxis=False,
                                 marker=dict(size=14, color=COLOR[k], line=dict(width=2, color="white")),
                                 hovertemplate=f"{LABEL[k]}<br>%{{x:,}} {'µm²' if use_area else 'bits'}"
                                               f"<br>%{{y:.2f}}% correct<br>{p['clocks']} clocks per branch<extra></extra>"))
    xs = [stats["predictors"][k]["area_um2" if use_area else "storage_bits"] for k in PREDICTORS]
    ys = [100 * stats["predictors"][k]["accuracy"] for k in PREDICTORS]
    pad = max(0.5, (max(ys) - min(ys)) * 0.15)
    _base(fig, 300, margin=dict(l=8, r=16, t=16, b=8))
    fig.update_xaxes(title_text="cell area (µm², sky130)" if use_area else "storage on the chip (bits)",
                     range=[0, max(xs) * 1.45])
    fig.update_yaxes(title_text="correct (%)", ticksuffix="%", range=[min(ys) - pad, max(ys) + pad])
    return fig


def benchmark_bars(bench: pd.DataFrame) -> go.Figure:
    """Accuracy per program, one bar group per program, baseline as a tick."""
    fig = go.Figure()
    progs = bench["program"].tolist()
    for k in PREDICTORS:
        fig.add_trace(go.Bar(x=progs, y=100 * bench[k], name=LABEL[k], marker=dict(color=COLOR[k], line=dict(width=1.5, color="white")),
                             hovertemplate=f"%{{x}}<br>{LABEL[k]}: %{{y:.2f}}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=progs, y=100 * bench["baseline"], mode="markers", name="always the common direction",
                             marker=dict(symbol="line-ew", size=44, line=dict(width=2.5, color=INK)),
                             hovertemplate="%{x}<br>baseline: %{y:.1f}%<extra></extra>"))
    _base(fig, 360, barmode="group", bargap=0.25, showlegend=True,
          legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0), margin=dict(l=8, r=16, t=36, b=8))
    fig.update_yaxes(ticksuffix="%", range=[0, 102], title_text="branches predicted correctly")
    fig.update_xaxes(tickfont=dict(family=MONO, size=12, color=INK))
    return fig
