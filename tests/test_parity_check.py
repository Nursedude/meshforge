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
    # ROOT, not /opt/meshforge: run from a worktree or branch, the literal
    # compared MAIN and read in_sync over the change under test (2026-10-20).
    findings, overall = pc.check_parity(str(ROOT), "/opt/meshanchor")
    assert overall == "in_sync", [f.label for f in findings if f.status != "ok"]
    assert findings and all(f.status == "ok" for f in findings)


@pytest.mark.skipif(not _HAS_MESHANCHOR, reason="/opt/meshanchor not present")
def test_main_in_sync_exits_0(capsys):
    pc = _load()
    rc = pc.main(["--meshforge", str(ROOT), "--meshanchor", "/opt/meshanchor"])
    assert rc == 0
    assert "RESULT: in sync." in capsys.readouterr().out


def _twin_repos(tmp_path, ma_has_dep):
    for side in ("mf", "ma"):
        d = tmp_path / side / "src" / "utils"
        d.mkdir(parents=True)
        (d / "twinned.py").write_text("from utils.registry import ROWS\n")
        if side == "mf" or ma_has_dep:
            (d / "registry.py").write_text("ROWS = []\n")
    return str(tmp_path / "mf"), str(tmp_path / "ma")


def test_twinned_file_importing_a_module_the_twin_lacks_is_missing(tmp_path, monkeypatch):
    """2026-10-20 (non-author review): the twinned fleet_truth.py gained an
    import of a NEW module only MeshForge had. Byte-identical copies, and a
    byte-for-byte port would crash the twin on import — parity must say so."""
    pc = _load()
    monkeypatch.setattr(pc, "BYTE_IDENTICAL", ("src/utils/twinned.py",))
    mf, ma = _twin_repos(tmp_path, ma_has_dep=False)
    findings, overall = pc.check_parity(mf, ma)
    hits = [f for f in findings if f.section == "twinimport"]
    assert [(f.label, f.status) for f in hits] == [
        ("src/utils/twinned.py -> utils.registry.ROWS", "missing")]
    assert overall == "missing"


def test_twinned_import_present_in_both_repos_is_ok(tmp_path, monkeypatch):
    pc = _load()
    monkeypatch.setattr(pc, "BYTE_IDENTICAL", ("src/utils/twinned.py",))
    mf, ma = _twin_repos(tmp_path, ma_has_dep=True)
    findings, _ = pc.check_parity(mf, ma)
    assert not [f for f in findings if f.section == "twinimport" and f.status != "ok"]


def _twin_with(tmp_path, twinned_src, mf_files, ma_files):
    """Two repos sharing src/utils/twinned.py; each side gets its own files
    ({repo-relative path: text})."""
    roots = []
    for side, files in (("mf", mf_files), ("ma", ma_files)):
        root = tmp_path / side
        for rel, text in {"src/utils/twinned.py": twinned_src, **files}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        roots.append(str(root))
    return roots


def _twinimport(pc, monkeypatch, roots):
    monkeypatch.setattr(pc, "BYTE_IDENTICAL", ("src/utils/twinned.py",))
    findings, overall = pc.check_parity(*roots)
    # Only this tier: the temp repos carry none of the other tiers' files,
    # so `overall` reads missing whatever this tier says.
    return [(f.label, f.status) for f in findings
            if f.section == "twinimport" and f.status != "ok"]


def test_from_package_import_submodule_is_checked(tmp_path, monkeypatch):
    """2026-10-20 review round 2: `from utils import mod` was skipped (one
    dotted part), so the exact shape this tier exists for read clean."""
    pc = _load()
    roots = _twin_with(tmp_path, "from utils import registry\n",
                       {"src/utils/registry.py": "ROWS = []\n"}, {})
    assert _twinimport(pc, monkeypatch, roots) == [
        ("src/utils/twinned.py -> utils.registry", "missing")]


def test_nested_package_module_resolves(tmp_path, monkeypatch):
    """`mini_dudeai.presets.x` was mapped to src/mini_dudeai/presets.py —
    absent in BOTH repos, so correct code read missing."""
    pc = _load()
    files = {"src/mini_dudeai/__init__.py": "",
             "src/mini_dudeai/presets/__init__.py": "",
             "src/mini_dudeai/presets/fleet.py": "RULES = []\n"}
    roots = _twin_with(tmp_path, "from mini_dudeai.presets.fleet import RULES\n",
                       files, files)
    assert _twinimport(pc, monkeypatch, roots) == []


def test_imported_name_the_twin_module_lacks_is_missing(tmp_path, monkeypatch):
    """The module existing is not the import working: the twin's copy must
    BIND the name (PEER_CLOCK_SKEW_DARK_S read OK while MA had none)."""
    pc = _load()
    roots = _twin_with(tmp_path, "from utils.collector import NEW_CONST\n",
                       {"src/utils/collector.py": "NEW_CONST = 1\n"},
                       {"src/utils/collector.py": "OLD = 1\n"})
    assert _twinimport(pc, monkeypatch, roots) == [
        ("src/utils/twinned.py -> utils.collector.NEW_CONST", "missing")]


def test_guarded_optional_import_is_not_a_dependency(tmp_path, monkeypatch):
    """A try/except-ImportError import degrades by design on the twin."""
    pc = _load()
    src = ("try:\n    from utils.mf_only import X\n"
           "except ImportError:\n    X = None\n")
    roots = _twin_with(tmp_path, src, {"src/utils/mf_only.py": "X = 1\n"}, {})
    assert _twinimport(pc, monkeypatch, roots) == []


def test_unparseable_side_says_its_imports_went_unchecked(tmp_path, monkeypatch):
    pc = _load()
    roots = _twin_with(tmp_path, "import os\n", {}, {})
    (tmp_path / "ma" / "src/utils/twinned.py").write_text("def broken(:\n")
    assert ("src/utils/twinned.py (ma)", "drift") in _twinimport(pc, monkeypatch, roots)


def test_resolving_edges_print_as_one_count(tmp_path, monkeypatch, capsys):
    """26 '[OK] x -> y' lines buried the one MISSING that mattered."""
    pc = _load()
    roots = _twin_with(tmp_path, "from utils.registry import ROWS\n",
                       {"src/utils/registry.py": "ROWS = []\n"},
                       {"src/utils/registry.py": "ROWS = []\n"})
    monkeypatch.setattr(pc, "BYTE_IDENTICAL", ("src/utils/twinned.py",))
    pc.main(["--meshforge", roots[0], "--meshanchor", roots[1]])
    out = capsys.readouterr().out
    assert "-> utils.registry" not in out
    assert "1 import edge(s) resolve in both repos" in out


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
