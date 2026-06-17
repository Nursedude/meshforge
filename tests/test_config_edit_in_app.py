"""In-app config editing — the In-Domain (MF018) alternative to nano/vi.

Pins `config_edit.edit_config_in_app` and the backend `editbox`: a config file
is edited INSIDE the TUI and written back via the right privilege path (atomic
user write vs sudo), with four DISTINCT honest outcomes (saved / read-fail /
write-fail / cancelled-or-unchanged) — never a silent "saved". Also pins that
the three "Edit Config" handlers no longer spawn a shell editor.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))

import config_edit  # noqa: E402


def _ctx():
    ctx = MagicMock()
    # report_action returns bool(ok), like the real TUIContext.
    ctx.report_action.side_effect = lambda ok, *a, **k: bool(ok)
    return ctx


def test_edit_saves_writable_file_atomically(tmp_path, monkeypatch):
    f = tmp_path / "config"
    f.write_text("old = 1\n")
    ctx = _ctx()
    ctx.dialog.editbox.return_value = "old = 2"  # user edited (strip is fine)
    written = {}
    monkeypatch.setattr("utils.paths.atomic_write_text",
                        lambda p, c: written.update(path=str(p), content=c))
    # A real tmp file is W_OK, so the atomic path is taken (no sudo).
    monkeypatch.setattr(config_edit.os, "access", lambda p, m: True)

    result = config_edit.edit_config_in_app(ctx, f, title="Edit X")

    assert result is True
    assert written["content"] == "old = 2\n"   # normalized trailing newline
    assert written["path"] == str(f)
    ok_arg = ctx.report_action.call_args[0][0]
    assert ok_arg is True


def test_edit_uses_sudo_for_unwritable_file(tmp_path, monkeypatch):
    f = tmp_path / "config"
    f.write_text("a\n")
    ctx = _ctx()
    ctx.dialog.editbox.return_value = "a\nb"
    sudo = {}
    monkeypatch.setattr(config_edit.os, "access", lambda p, m: False)  # not W_OK
    monkeypatch.setattr("utils.service_check._sudo_write",
                        lambda path, content, **k: (
                            sudo.update(path=path, content=content) or (True, "ok")))
    # atomic path must NOT be taken
    monkeypatch.setattr("utils.paths.atomic_write_text",
                        lambda p, c: pytest.fail("atomic used for unwritable file"))

    result = config_edit.edit_config_in_app(ctx, f)

    assert result is True
    assert sudo["content"] == "a\nb\n"
    assert sudo["path"] == str(f)


def test_cancel_returns_none_and_writes_nothing(tmp_path, monkeypatch):
    f = tmp_path / "config"
    f.write_text("x\n")
    ctx = _ctx()
    ctx.dialog.editbox.return_value = None  # operator cancelled
    monkeypatch.setattr("utils.paths.atomic_write_text",
                        lambda p, c: pytest.fail("wrote on cancel"))
    monkeypatch.setattr(config_edit.os, "access", lambda p, m: True)

    assert config_edit.edit_config_in_app(ctx, f) is None
    ctx.report_action.assert_not_called()


def test_no_change_returns_none_and_writes_nothing(tmp_path, monkeypatch):
    f = tmp_path / "config"
    f.write_text("same = 1\n")
    ctx = _ctx()
    ctx.dialog.editbox.return_value = "same = 1"  # identical modulo newline
    monkeypatch.setattr("utils.paths.atomic_write_text",
                        lambda p, c: pytest.fail("wrote on no-op edit"))
    monkeypatch.setattr(config_edit.os, "access", lambda p, m: True)

    assert config_edit.edit_config_in_app(ctx, f) is None


def test_unreadable_file_returns_false(tmp_path):
    ctx = _ctx()
    missing = tmp_path / "nope" / "config"  # parent dir absent -> read raises
    result = config_edit.edit_config_in_app(ctx, missing)
    assert result is False
    ctx.dialog.editbox.assert_not_called()  # never opened the editor


def test_write_failure_returns_false(tmp_path, monkeypatch):
    f = tmp_path / "config"
    f.write_text("a\n")
    ctx = _ctx()
    ctx.dialog.editbox.return_value = "a\nb"
    monkeypatch.setattr(config_edit.os, "access", lambda p, m: True)

    def _boom(p, c):
        raise OSError("disk full")
    monkeypatch.setattr("utils.paths.atomic_write_text", _boom)

    result = config_edit.edit_config_in_app(ctx, f)
    assert result is False
    assert ctx.report_action.call_args[0][0] is False  # honest failure dialog


# ----------------------------------------------------------- backend editbox


def test_backend_editbox_builds_args_and_returns_text():
    from backend import DialogBackend
    b = DialogBackend()
    captured = {}
    b._run = lambda args, **k: captured.update(args=args) or (0, "edited body")
    out = b.editbox("Edit cfg", "/etc/foo.conf")
    assert out == "edited body"
    assert "--editbox" in captured["args"]
    assert "/etc/foo.conf" in captured["args"]
    # NOT a shell editor spawn.
    assert "nano" not in captured["args"]


def test_backend_editbox_cancel_returns_none():
    from backend import DialogBackend
    b = DialogBackend()
    b._run = lambda args, **k: (1, "")  # cancel / ESC
    assert b.editbox("t", "/etc/foo.conf") is None


# ------------------------------------------- the 3 handlers no longer spawn nano

EDITOR_HANDLERS = [
    "meshtasticd_config.py",
    "rns_config.py",
    "_nomadnet_io_ops.py",
]


@pytest.mark.parametrize("fname", EDITOR_HANDLERS)
def test_config_handler_no_longer_spawns_shell_editor(fname):
    src = (SRC / "launcher_tui" / "handlers" / fname).read_text()
    import re
    # No subprocess spawn of nano/vim/vi/$editor.
    assert not re.search(
        r"subprocess\.(run|call|Popen)\([^)]*\b(nano|vim?|emacs)\b", src), \
        f"{fname} still spawns a shell editor"
    assert not re.search(
        r"subprocess\.(run|call|Popen)\(\s*\[\s*editor\b", src), \
        f"{fname} still spawns $editor"
    # And it routes through the in-app editor instead.
    assert "edit_config_in_app" in src, \
        f"{fname} does not use the in-app config editor"
