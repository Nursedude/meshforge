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
"""
from __future__ import annotations

import os
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_HARNESSES = [
    "test_honest_status_preserve.sh",   # suite-failure log preservation
    "test_honest_status_boxes.sh",      # fleet box-list derivation
]


@pytest.mark.parametrize("script", _HARNESSES)
def test_shell_harness_passes(script):
    path = os.path.join(_HERE, script)
    assert os.path.isfile(path), f"{script} missing"
    proc = subprocess.run(
        ["bash", path], capture_output=True, text=True, timeout=300,
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
