"""Tests for scripts/parity_check.py's pure check_parity() — the (findings, overall)
contract that watchdog probe_parity_drift consumes. The drift-handling branch is
unit-tested in test_watchdog_probes.py via an injected check_fn; here we lock the
overall-status computation and the in-sync happy path against the real repos.
"""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_HAS_MESHANCHOR = os.path.isdir("/opt/meshanchor")


def _load():
    spec = importlib.util.spec_from_file_location(
        "parity_check", ROOT / "scripts" / "parity_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_missing_when_meshanchor_absent(tmp_path):
    pc = _load()
    findings, overall = pc.check_parity(str(ROOT), str(tmp_path / "nope"))
    assert overall == "repo_missing"
    assert findings and findings[0].status == "missing"


def test_main_repo_missing_exits_2(tmp_path):
    pc = _load()
    rc = pc.main(["--meshforge", str(ROOT), "--meshanchor", str(tmp_path / "nope")])
    assert rc == 2


@pytest.mark.skipif(not _HAS_MESHANCHOR, reason="/opt/meshanchor not present")
def test_in_sync_against_real_repos():
    pc = _load()
    findings, overall = pc.check_parity("/opt/meshforge", "/opt/meshanchor")
    assert overall == "in_sync", [f.label for f in findings if f.status != "ok"]
    assert findings and all(f.status == "ok" for f in findings)


@pytest.mark.skipif(not _HAS_MESHANCHOR, reason="/opt/meshanchor not present")
def test_main_in_sync_exits_0(capsys):
    pc = _load()
    rc = pc.main(["--meshforge", "/opt/meshforge", "--meshanchor", "/opt/meshanchor"])
    assert rc == 0
    assert "RESULT: in sync." in capsys.readouterr().out


# ── Calibration claim-gate shared-core guard (self-audit-qa-arc §1) ──

def test_extract_literal_constant_tuple():
    pc = _load()
    assert pc._extract_literal_constant('X = ("a", "b", "c")\nY = 5\n', "X") == \
        frozenset({"a", "b", "c"})


def test_extract_literal_constant_absent_or_nonliteral():
    pc = _load()
    assert pc._extract_literal_constant("X = 1", "MISSING") is None   # absent
    assert pc._extract_literal_constant("X = foo()", "X") is None      # non-literal
    assert pc._extract_literal_constant("X = 5", "X") is None          # not a collection
    assert pc._extract_literal_constant("def (", "X") is None          # unparseable
    assert pc._extract_literal_constant(None, "X") is None             # no text


def test_extract_detects_vocab_difference():
    """The set-difference the drift finding reports (mf-only / ma-only)."""
    pc = _load()
    a = pc._extract_literal_constant('S = ("x", "y")', "S")
    b = pc._extract_literal_constant('S = ("x", "z")', "S")
    assert a != b
    assert sorted(a - b) == ["y"] and sorted(b - a) == ["z"]


@pytest.mark.skipif(not _HAS_MESHANCHOR, reason="/opt/meshanchor not present")
def test_calibgate_section_wired_and_ok():
    """The claim-gate shared-core check is wired into check_parity and the three
    detection vocabularies agree between the real repos — the guard the operator
    asked for so a future MeshForge claim_gate change can't silently skip MA."""
    pc = _load()
    findings, _ = pc.check_parity("/opt/meshforge", "/opt/meshanchor")
    cg = [f for f in findings if f.section == "calibgate"]
    assert cg, "calibgate section missing — check not wired"
    assert all(f.status == "ok" for f in cg), \
        [f.label for f in cg if f.status != "ok"]
    names = {f.label.split("::")[-1].strip() for f in cg if "::" in f.label}
    assert {"STRONG_CLAIMS", "CALIBRATION_MARKERS", "EVIDENCE_PATTERNS"} <= names
