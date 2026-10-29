"""The ONE parser for every N-consecutive-ticks watchdog knob (hfm #5) lives
in ``watchdog_probe_core`` (re-exported by the ``watchdog_probes`` hub) — not
as a private symbol of a sibling probe module (review of bbe1601f, finding 6).
"""
import ast
import logging
from pathlib import Path

import pytest

from utils import watchdog_probe_core as core
from utils.watchdog_probes import env_confirm_ticks

_UTILS = Path(__file__).resolve().parent.parent / "src" / "utils"
_ENV = "MESHFORGE_TEST_CONFIRM_TICKS"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    core._rejected_knobs.clear()
    yield
    core._rejected_knobs.clear()


@pytest.mark.parametrize("raw, expected", [
    (None, 4), ("7", 7), ("1", 1), (" 5 ", 5),
    ("0", 4), ("-2", 4), ("x", 4), ("", 4), ("3.0", 4), ("1_000", 4),
    ("+3", 4), ("\u0663", 4)])
def test_parse(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv(_ENV, raw)
    assert env_confirm_ticks(_ENV, 4) == expected


def test_rejected_value_leaves_one_warning(monkeypatch, caplog):
    """hfm #9: an ignored knob must say so — once, not every tick."""
    monkeypatch.setenv(_ENV, "1_000")
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        env_confirm_ticks(_ENV, 4)
        env_confirm_ticks(_ENV, 4)
    hits = [r for r in caplog.records if _ENV in r.getMessage()]
    assert len(hits) == 1
    assert "IGNORED" in hits[0].getMessage() and "4 tick" in hits[0].getMessage()


def test_valid_and_absent_values_are_silent(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="watchdog"):
        env_confirm_ticks(_ENV, 4)
        monkeypatch.setenv(_ENV, "6")
        env_confirm_ticks(_ENV, 4)
    assert not [r for r in caplog.records if _ENV in r.getMessage()]


def _watchdog_functions():
    for path in sorted(_UTILS.glob("watchdog*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path.name, node


def test_defined_once_in_core():
    homes = [f for f, n in _watchdog_functions()
             if n.name.lstrip("_") == "env_confirm_ticks"]
    assert homes == ["watchdog_probe_core.py"]


def test_no_tick_knob_parses_the_environment_itself():
    """Any *ticks* helper that reads os.environ outside core is a second
    parser by another name — the drift the name-only check cannot see."""
    offenders = []
    for fname, node in _watchdog_functions():
        if "ticks" not in node.name or node.name == "env_confirm_ticks":
            continue
        src = ast.unparse(node)
        if "os.environ" in src or "getenv" in src:
            offenders.append(f"{fname}:{node.name}")
    assert offenders == []
