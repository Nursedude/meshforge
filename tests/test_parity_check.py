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
