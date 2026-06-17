"""Tests for the MF018 In-Domain Principle ratchet in scripts/lint.py.

Pins the rule that backs foundations/in_domain_principle.md: TUI code must
let the user fix things in-app, never punt to a shell. The check is a
per-file ratchet — each handler has a frozen baseline count of pre-existing
shell-escapes; the build fails only when a file EXCEEDS its baseline. The
baseline can only shrink (closing a gap), never grow.
"""
import os
import sys
from pathlib import Path

import importlib.util

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)

REPO_ROOT = Path(__file__).parent.parent


def _write(tmp_path, body):
    """Write a fake handler under a src/launcher_tui tree rooted at tmp_path."""
    d = tmp_path / "src" / "launcher_tui" / "handlers"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "_fake.py"
    p.write_text(body)
    return str(p)


def test_repo_is_green_at_baseline():
    """The live repo must pass MF018 — the frozen baseline matches reality."""
    files = lint.get_all_python_files(str(REPO_ROOT / lint.MF018_SCAN_DIR))
    issues = lint.check_in_domain_escapes(files, repo_root=str(REPO_ROOT))
    assert issues == [], f"MF018 backlog drifted from baseline: {[(i.file, i.message) for i in issues]}"


def test_new_escape_in_clean_file_fails(tmp_path):
    p = _write(tmp_path, 'def f(ctx):\n    ctx.msg("Try manually: sudo systemctl restart rnsd")\n')
    issues = lint.check_in_domain_escapes([p], repo_root=str(tmp_path))
    assert len(issues) == 1
    assert issues[0].code == "MF018"


def test_editor_spawn_fails(tmp_path):
    p = _write(tmp_path, "import subprocess\ndef f(path):\n    subprocess.run(['nano', path])\n")
    issues = lint.check_in_domain_escapes([p], repo_root=str(tmp_path))
    assert len(issues) == 1 and issues[0].code == "MF018"


def test_run_with_sudo_fails(tmp_path):
    p = _write(tmp_path, 'def f(ctx):\n    ctx.msg("Run MeshForge with sudo to install.")\n')
    issues = lint.check_in_domain_escapes([p], repo_root=str(tmp_path))
    assert len(issues) == 1 and issues[0].code == "MF018"


def test_in_domain_ok_marker_suppresses(tmp_path):
    p = _write(tmp_path, 'def f(ctx):\n    ctx.msg("Try manually: foo")  # in-domain-ok: cross-app paste\n')
    issues = lint.check_in_domain_escapes([p], repo_root=str(tmp_path))
    assert issues == []


def test_clean_file_passes(tmp_path):
    p = _write(tmp_path, 'def f(ctx):\n    service_check.restart_service("rnsd")\n')
    issues = lint.check_in_domain_escapes([p], repo_root=str(tmp_path))
    assert issues == []


def test_baseline_retired_to_zero():
    """The In-Domain arc retired the backlog: the baseline is now EMPTY, so
    every TUI file has an implicit baseline of 0 and any new bare shell-escape
    fails the build. This is the terminal state of the ratchet — it only ever
    shrank (frozen-2026-05-29 total was 79). Re-adding a nonzero entry to grant
    a file headroom is a regression; close the escape in-app or mark a genuine
    exception instead (foundations/in_domain_principle.md)."""
    assert lint.MF018_BASELINE == {}, (
        "MF018 backlog must stay retired — no file may carry shell-escape "
        f"headroom; got {lint.MF018_BASELINE}"
    )


def test_files_outside_tui_scope_ignored(tmp_path):
    d = tmp_path / "src" / "utils"
    d.mkdir(parents=True)
    p = d / "x.py"
    p.write_text('print("Try manually: sudo apt install foo")\n')
    issues = lint.check_in_domain_escapes([str(p)], repo_root=str(tmp_path))
    assert issues == []  # MF018 governs the TUI surface only
