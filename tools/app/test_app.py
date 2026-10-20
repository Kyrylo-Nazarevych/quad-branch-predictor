"""Headless tests of the Streamlit app.   python3 -m pytest app/test_app.py -q

Needs the toolchain (riscv64-unknown-elf-gcc, Spike) and bptrace/ + examples/ in the repo root.
"""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).with_name("app.py"))


def start():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    return at


def press_run(at):
    next(b for b in at.button if b.label == "Compile and run").click().run()
    assert not at.exception, at.exception
    return at


def test_empty_state():
    at = start()
    assert at.title[0].value == "Branch predictor lab"


def test_example_sort_and_search():
    at = start()
    at.selectbox[0].set_value("sort_and_search").run()
    press_run(at)
    assert at.title[0].value == "sort_and_search.c"
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Conditional branches executed"] == "46,922"
    assert metrics["Most accurate"] == "84.5%"
    assert metrics["Mispredictions per 1,000 instructions"] == "41.6"
    assert any("4 branches cause 97%" in h.value for h in at.subheader), [h.value for h in at.subheader]


def test_pattern_example_defaults_to_O0():
    at = start()
    at.selectbox[0].set_value("pattern_alternating").run()
    assert at.selectbox[1].value == "O0"
    press_run(at)
    assert {m.label: m.value for m in at.metric}["Conditional branches executed"] == "4,001"


def _upload(at, name, text, tmp_path):
    # AppTest has no file-uploader driver: put the file in examples/ instead, then remove it
    ex = Path(__file__).resolve().parent.parent / "examples" / name
    ex.write_text(text)
    try:
        at = AppTest.from_file(APP, default_timeout=180)
        at.run()
        at.selectbox[0].set_value(Path(name).stem).run()
        press_run(at)
        return at
    finally:
        ex.unlink()


def test_compile_error_is_shown(tmp_path):
    at = _upload(at=None, name="zz_bad.c", text='int main(void){ printf("hi"); return 0; }\n', tmp_path=tmp_path)
    shown = " ".join(c.value for c in at.code)
    assert "printf" in shown and "no operating system" in shown
    assert "result" not in at.session_state


def test_hung_program_is_reported(tmp_path):
    at = _upload(at=None, name="zz_hang.c", text="volatile int x; int main(void){ for(;;) x++; }\n", tmp_path=tmp_path)
    shown = " ".join(m.value for m in at.markdown)
    assert "does main() return" in shown


def test_all_examples():
    at = start()
    at.selectbox[0].set_value("pattern_loop4").run()
    press_run(at)
    next(b for b in at.button if b.label == "Run all examples").click().run()
    assert not at.exception, at.exception
    bench = at.session_state["bench"]
    assert set(bench["program"]) >= {"sort_and_search", "pattern_alternating", "pattern_loop4", "pattern_xor"}
    row = bench.set_index("program").loc["sort_and_search"]
    assert round(100 * row["perceptron"], 2) == 84.48


def _no_toolchain(monkeypatch, tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pipeline
    monkeypatch.setattr(pipeline, "toolchain_status", lambda: (False, "riscv64-unknown-elf-gcc, spike not found."))
    monkeypatch.setattr(pipeline, "RUNS", tmp_path)        # no cached runs to fall back on


def test_example_runs_without_toolchain(monkeypatch, tmp_path):
    _no_toolchain(monkeypatch, tmp_path)
    at = start()
    at.selectbox[0].set_value("sort_and_search").run()
    press_run(at)
    assert {m.label: m.value for m in at.metric}["Conditional branches executed"] == "46,922"
    assert any("precomputed" in c.value for c in at.caption)


def test_board_backend_with_mock_board(monkeypatch, tmp_path):
    """The 'FPGA board' backend end to end, with fpga/host/mockboard.py standing in for the Nexys 4 DDR."""
    import pytest
    _no_toolchain(monkeypatch, tmp_path)
    import pipeline
    if not (pipeline.FPGA_HOST / "mockboard.py").exists():
        pytest.skip(f"no fpga/host at {pipeline.FPGA_HOST}")
    bl = pipeline._bplink()
    import mockboard
    monkeypatch.setattr(bl, "find_port", lambda: "mock")
    monkeypatch.setattr(bl.serial, "Serial", lambda *a, **k: type("Port", (), {"close": lambda self: None})())
    monkeypatch.setattr(bl.Board, "__init__", lambda self, port, baud=1_000_000, timeout=2.0, ser=None:
                        setattr(self, "ser", mockboard.MockSerial(link_baud=0)) or setattr(self, "seq", 0)
                        or setattr(self, "timeout", timeout))
    assert pipeline.board_status()[0]
    src = pipeline.EXAMPLES / "sort_and_search.c"
    r = pipeline.run_pipeline(src.name, src.read_bytes(), opt="O1", backend="board")
    assert r.backend == "board" and r.rtl_vs_models == 0
    assert len(r.preds) == 46922 and round(r.preds["cycles"].astype(float).mean(), 1) == 21.2


def test_new_program_without_toolchain_is_explained(monkeypatch, tmp_path):
    _no_toolchain(monkeypatch, tmp_path)
    at = _upload(at=None, name="zz_new.c", text="int main(void){ return 0; }\n", tmp_path=tmp_path)
    assert any("not found" in m.value for m in at.markdown)
    assert "result" not in at.session_state
