"""Pins for the lies the truth sweep's level-two walk found (2026-09-22).

Each rendered a health verdict from NOTHING observed: the diagnostic engine
called an empty window "healthy" (and every screen printed "Rules Loaded: 0"
because the key was never written), and the status report scored "65/100
(fair)" from the health scorer's category defaults with zero nodes and zero
services reporting. The sweep reads those screens only through its porous
text classifier, which is not gated at level two — so the source behaviour
is pinned here, where it can fail.
"""
from types import SimpleNamespace
from unittest.mock import patch

import utils.report_generator as rg
from utils.diagnostic_engine import Category, DiagnosticEngine, Severity


def test_empty_window_is_unknown_not_healthy():
    eng = DiagnosticEngine(persist_history=False)
    summary = eng.get_health_summary()
    assert summary["overall_health"] == "unknown"
    assert summary["symptoms_last_hour"] == 0


def test_rules_loaded_is_the_real_count():
    eng = DiagnosticEngine(persist_history=False)
    stats = eng.get_health_summary()["stats"]
    assert stats["rules_loaded"] == len(eng._rules) > 0


def test_a_reported_symptom_still_yields_a_verdict():
    # Control: the unknown branch is ONLY for silence.
    eng = DiagnosticEngine(persist_history=False)
    eng.report_symptom("minor thing", Category.CONNECTIVITY, Severity.WARNING, source="t")
    assert eng.get_health_summary()["overall_health"] == "healthy"
    eng.report_symptom("bad thing", Category.CONNECTIVITY, Severity.CRITICAL, source="t")
    assert eng.get_health_summary()["overall_health"] == "critical"


def _scorer(nodes, services):
    snap = SimpleNamespace(overall_score=65.0, status="fair", node_count=nodes,
                           service_count=services,
                           category_scores={"connectivity": 50.0, "reliability": 100.0})
    return SimpleNamespace(get_snapshot=lambda: snap, get_trend=lambda: "stable")


def _health_section(scorer):
    gen = rg.ReportGenerator()
    with patch.object(rg, "_HAS_HEALTH_SCORE", True), \
            patch.object(rg, "_get_health_scorer", lambda: scorer):
        gen._add_health_section()
    return gen._sections[-1].content


def test_report_scores_nothing_as_unknown():
    text = _health_section(_scorer(0, 0))
    assert "UNKNOWN" in text and "/100" not in text


def test_report_scores_when_something_reports():
    text = _health_section(_scorer(3, 0))
    assert "**Overall Score: 65/100** (fair)" in text
    assert "Nodes reporting: 3" in text


# --- RNS Fix Ownership: a verdict over what was actually checked ----------

def _ownership_screen(tmp_path, monkeypatch, etc_exists: bool, storage_raises: bool):
    import contextlib
    import io
    import sys
    from pathlib import Path
    here = Path(__file__).resolve().parent
    for p in (here, here.parent / "src" / "launcher_tui"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from handler_test_utils import make_handler_context
    import handlers.rns_interfaces as ri

    etc = tmp_path / "etc_reticulum"
    if etc_exists:
        etc.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(ri.ReticulumPaths, "ETC_BASE", etc)
    monkeypatch.setattr(ri, "get_real_user_home", lambda: home)
    monkeypatch.setattr(ri, "clear_screen", lambda: None)

    def storage():
        if storage_raises:
            raise PermissionError("planted storage failure")
    monkeypatch.setattr(ri.ReticulumPaths, "_fix_storage_file_permissions", staticmethod(storage))

    h = ri.RNSInterfacesHandler()
    h.set_context(make_handler_context())
    h.ctx.wait_for_enter = lambda *a, **k: None
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        h._fix_rns_ownership()
    return out.getvalue()


def test_ownership_nothing_checked_is_not_all_correct(tmp_path, monkeypatch):
    text = _ownership_screen(tmp_path, monkeypatch, etc_exists=False, storage_raises=False)
    assert "Nothing checked" in text and "correct ownership" not in text


def test_ownership_storage_failure_is_counted(tmp_path, monkeypatch):
    # Fable review finding 4: FAIL: storage/ was followed by "All 1 checked
    # path(s) have correct ownership" — the failure was never counted.
    text = _ownership_screen(tmp_path, monkeypatch, etc_exists=True, storage_raises=True)
    assert "FAIL: storage/" in text
    assert "Issues found: 1" in text
    assert "correct ownership" not in text


def test_ownership_clean_check_says_what_it_checked(tmp_path, monkeypatch):
    text = _ownership_screen(tmp_path, monkeypatch, etc_exists=True, storage_raises=False)
    assert "All 1 checked path(s) have correct ownership" in text


def test_sniffer_start_reports_whether_hooks_installed():
    # start() returned True with RNS absent, so the TUI's honest
    # "Capture Started (No RNS)" branch could never run (Fable review,
    # finding 3). Capture mode is still enabled — it attaches later.
    from unittest.mock import patch
    import monitoring.rns_sniffer as rs
    with patch.object(rs, "_HAS_RNS", False):
        sn = rs.RNSSniffer()
        assert sn.start() is False
        assert sn._running is True
        sn.stop()
