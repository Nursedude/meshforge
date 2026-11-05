"""Run the honest_status shell harnesses under pytest.

WHY THIS FILE EXISTS (2026-07-28): ``test_honest_status_preserve.sh`` was a
dead test for weeks. Commit 49c0b703 pointed the gate's interpreter at
``$REPO/venv/bin/python`` — an ABSOLUTE path that routes around the harness's
PATH stub — so its fake pytest was never invoked and 4 of its 5 assertions
could not pass. Nobody noticed, because nothing ran it: pytest collects
``test_*.py``, and a ``.sh`` file is invisible to it.

A test that cannot fail and a test that nobody runs are the same artifact.
This wrapper makes the shell harnesses part of the suite, so the next time one
of them stops working, CI says so.

The harness list is DERIVED, not hand-maintained (2026-07-31). It used to be a
hardcoded allowlist, which reintroduced the very defect one layer up: a new
``test_*.sh`` was dead until someone remembered to add it here, and nothing
failed if they didn't. Found by adding ``test_pytest_verdict.sh`` — the sweep
also turned up ``test_weekly_updates_digest.sh``, an existing harness that had
never been collected (it passes; it was dead, not broken). Globbing the
directory means the naming convention alone is enough to be run.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
# Every tests/test_*.sh is a harness by naming convention — no allowlist to
# forget to update.
_HARNESSES = sorted(
    os.path.basename(p) for p in glob.glob(os.path.join(_HERE, "test_*.sh"))
)


#: Each harness's wall-clock budget — the subprocess timeout AND the pytest
#: per-test timeout below (one constant: the CLI cap must never undercut it).
_HARNESS_BUDGET_S = 300


@pytest.mark.timeout(_HARNESS_BUDGET_S)   # beats CI's --timeout=30 (marker > CLI)
@pytest.mark.parametrize("script", _HARNESSES)
def test_shell_harness_passes(script):
    path = os.path.join(_HERE, script)
    assert os.path.isfile(path), f"{script} missing"
    proc = subprocess.run(
        ["bash", path], capture_output=True, text=True, timeout=_HARNESS_BUDGET_S,
        cwd=_HERE,
    )
    # The harnesses print PASS:/FAIL: per assertion and exit non-zero on any
    # failure. Surface their own output — a bare exit code would make a
    # regression here as opaque as the silence this file exists to end.
    assert proc.returncode == 0, (
        f"{script} failed (exit {proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert "ALL PASS" in proc.stdout, f"{script}: no ALL PASS line\n{proc.stdout}"


# 2026-11-05 (deferred `ci-per-test-timeout-vs-shell-harness`): CI runs
# `pytest --timeout=30 --timeout-method=thread`, which OVERRODE this test's
# own 300 s subprocess budget. test_honest_status_boxes.sh measures ~36 s on
# the dev Pi (2/2) and ~25-35 s on CI, so 5 of 10 CI runs on 11-04/05 died
# "suite crashed before reporting" (the thread method kills the whole run).
# The per-test marker must equal the subprocess budget — ONE constant.
def test_the_harness_runner_carries_its_own_budget_as_its_timeout():
    marks = [m for m in getattr(test_shell_harness_passes, "pytestmark", [])
             if m.name == "timeout"]
    assert marks and marks[0].args[0] == _HARNESS_BUDGET_S


def test_the_marker_beats_a_lower_cli_cap():
    # Behaviour, not wiring: under --timeout=1 a harness that runs ~7 s must
    # still pass, i.e. pytest-timeout honours the marker over the CLI flag.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", os.path.abspath(__file__), "-q",
         "-p", "no:cacheprovider", "--timeout=1", "--timeout-method=thread",
         "-k", "test_shell_harness_passes and test_exit_code_mask_guard"],
        capture_output=True, text=True, timeout=_HARNESS_BUDGET_S, cwd=_HERE)
    assert proc.returncode == 0 and "1 passed" in proc.stdout, proc.stdout[-1500:]
