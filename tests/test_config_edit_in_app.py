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


def test_backend_editbox_builds_args_and_returns_text(monkeypatch):
    import backend as be
    from backend import DialogBackend
    # Pin the ambient tool set: editbox now depends on whether dialog is
    # installed, and this box's answer must not decide the test.
    monkeypatch.setattr(be.shutil, "which", lambda n: f"/usr/bin/{n}")
    b = DialogBackend()
    captured = {}
    b._run = lambda args, **k: captured.update(args=args) or (0, "edited body")
    out = b.editbox("Edit cfg", "/etc/foo.conf")
    assert out == "edited body"
    assert "--editbox" in captured["args"]
    assert "/etc/foo.conf" in captured["args"]
    # NOT a shell editor spawn.
    assert "nano" not in captured["args"]


def test_backend_editbox_cancel_returns_none(monkeypatch):
    import backend as be
    from backend import DialogBackend
    monkeypatch.setattr(be.shutil, "which", lambda n: f"/usr/bin/{n}")
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


# ------------------------------- whiptail has NO --editbox (2026-10-30 finding)
# Since 832e7e83 (06-16) the backend preferred whiptail and passed it
# --editbox; whiptail exits 1 "--editbox: unknown option", editbox() read
# that as CANCEL and edit_config_in_app returned None — "Edit Config" did
# nothing, silently, on every whiptail box. The tests above replace _run, so
# none of them ever met the binary that refuses the flag.


def _backend(kind, which):
    """A backend of KIND on a box whose PATH holds exactly WHICH — returns
    (backend, backend-module, fake-which) so the test can pin the ambient
    tool set with monkeypatch."""
    import backend as be
    from backend import DialogBackend
    b = DialogBackend()
    b.backend = kind
    fake_which = lambda name: (f"/usr/bin/{name}" if name in which else None)
    return b, be, fake_which


def test_editbox_on_whiptail_uses_dialog_when_installed(monkeypatch):
    b, be, w = _backend("whiptail", {"whiptail", "dialog"})
    monkeypatch.setattr(be.shutil, "which", w)
    seen = {}
    b._run = lambda args, **k: seen.update(args=args, **k) or (0, "x = 1")
    assert b.editbox("t", "/etc/foo.conf") == "x = 1"
    assert seen["tool"] == "dialog"          # NOT whiptail


def test_editbox_on_whiptail_without_dialog_raises_unavailable(monkeypatch):
    from backend import EditorUnavailable, DialogError
    b, be, w = _backend("whiptail", {"whiptail"})
    monkeypatch.setattr(be.shutil, "which", w)
    b._run = lambda *a, **k: pytest.fail("must not run a tool that cannot edit")
    with pytest.raises(EditorUnavailable) as ei:
        b.editbox("t", "/etc/foo.conf")
    assert issubclass(EditorUnavailable, DialogError)
    assert "dialog" in str(ei.value)


def test_editbox_on_dialog_backend_uses_dialog(monkeypatch):
    b, be, w = _backend("dialog", {"dialog"})
    monkeypatch.setattr(be.shutil, "which", w)
    seen = {}
    b._run = lambda args, **k: seen.update(**k) or (0, "y")
    b.editbox("t", "/etc/foo.conf")
    assert seen["tool"] == "dialog"


def test_editbox_keeps_leading_indentation(monkeypatch):
    # _run strips output; for a menu tag that is right, for a YAML body it
    # eats the first line's indentation.
    b, be, w = _backend("dialog", {"dialog"})
    monkeypatch.setattr(be.shutil, "which", w)
    seen = {}
    b._run = lambda args, **k: seen.update(**k) or (0, "  indented: 1\n")
    b.editbox("t", "/etc/foo.yaml")
    assert seen.get("strip") is False


def test_edit_reports_unavailable_editor_honestly(tmp_path, monkeypatch):
    from backend import EditorUnavailable
    f = tmp_path / "config"
    f.write_text("a\n")
    ctx = _ctx()
    ctx.dialog.editbox.side_effect = EditorUnavailable("whiptail has no editbox")
    ctx.dialog.yesno.return_value = False      # operator declines the install
    result = config_edit.edit_config_in_app(ctx, f)
    assert result is False                     # a FAILURE, never "cancelled"
    assert ctx.dialog.msgbox.called            # and it is SAID, not silent
    assert f.read_text() == "a\n"


def test_edit_never_raises_on_a_dead_dialog(tmp_path):
    from backend import DialogError
    f = tmp_path / "config"
    f.write_text("a\n")
    ctx = _ctx()
    ctx.dialog.editbox.side_effect = DialogError("editbox died")
    assert config_edit.edit_config_in_app(ctx, f) is False


def test_real_whiptail_refuses_editbox_flag():
    # The external fact the fix rests on, measured, not remembered. If a
    # future whiptail grows --editbox this fails and says so.
    import shutil, subprocess
    if not shutil.which("whiptail"):
        pytest.skip("whiptail not installed")
    r = subprocess.run(["whiptail", "--editbox", "/dev/null", "5", "20"],
                       stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=10)
    assert r.returncode != 0 and "unknown option" in r.stderr


def test_installer_installs_dialog_for_the_in_app_editor():
    # The in-app editor needs `dialog` (whiptail has no editbox); a fresh
    # install must carry it, or "Edit Config" refuses on every new box.
    import re
    sh = (SRC.parent / "scripts" / "install_noc.sh").read_text()
    m = re.search(r"if mf_apt_install \\\n(.*?);\s*then", sh, re.S)
    assert m, "base mf_apt_install block not found in install_noc.sh"
    from backend import EDITOR_PACKAGE
    assert re.search(rf"\b{re.escape(EDITOR_PACKAGE)}\b", m.group(1)), \
        f"install_noc.sh base apt list lacks backend.EDITOR_PACKAGE ({EDITOR_PACKAGE!r})"


# ---- MF018: the fix is OFFERED in-app, never a shell instruction (10-30)


def test_unavailable_editor_offers_in_app_install_declined(tmp_path, monkeypatch):
    from backend import EditorUnavailable
    f = tmp_path / "config"; f.write_text("a\n")
    ctx = _ctx()
    ctx.dialog.editbox.side_effect = EditorUnavailable("whiptail has no editbox")
    ctx.dialog.yesno.return_value = False
    called = []
    monkeypatch.setattr("utils.apt_packages.apt_install",
                        lambda *a, **k: called.append(a) or (True, ""))
    assert config_edit.edit_config_in_app(ctx, f) is False
    assert ctx.dialog.yesno.called          # the offer was made
    assert not called                        # and declining installs nothing
    assert f.read_text() == "a\n"


def test_unavailable_editor_install_then_edit(tmp_path, monkeypatch):
    from backend import EditorUnavailable
    f = tmp_path / "config"; f.write_text("a\n")
    ctx = _ctx()
    ctx.dialog.editbox.side_effect = [EditorUnavailable("no editbox"), "a\nb"]
    ctx.dialog.yesno.return_value = True
    called = []
    monkeypatch.setattr("utils.apt_packages.apt_install",
                        lambda pkg, **k: called.append(pkg) or (True, "ok"))
    monkeypatch.setattr(config_edit.os, "access", lambda p, m: True)
    written = {}
    monkeypatch.setattr("utils.paths.atomic_write_text",
                        lambda p, c: written.update(content=c))
    assert config_edit.edit_config_in_app(ctx, f) is True
    assert called == ["dialog"]
    assert written["content"] == "a\nb\n"


def test_unavailable_editor_failed_install_reports_output(tmp_path, monkeypatch):
    from backend import EditorUnavailable
    f = tmp_path / "config"; f.write_text("a\n")
    ctx = _ctx()
    ctx.dialog.editbox.side_effect = EditorUnavailable("no editbox")
    ctx.dialog.yesno.return_value = True
    monkeypatch.setattr("utils.apt_packages.apt_install",
                        lambda pkg, **k: (False, "E: Could not get lock"))
    assert config_edit.edit_config_in_app(ctx, f) is False
    shown = " ".join(str(c) for c in ctx.dialog.msgbox.call_args_list)
    assert "Could not get lock" in shown


# ---- non-author review 10-30 (round 1) on the editbox fix


def test_dialog_own_error_255_with_text_is_a_failure_not_cancel(monkeypatch):
    # dialog reports ITS errors (window too big, can't open file) as 255,
    # the same code as ESC — but with text on stderr. ESC leaves it empty.
    from backend import DialogError
    b, be, w = _backend("dialog", {"dialog"})
    monkeypatch.setattr(be.shutil, "which", w)
    b._run = lambda args, **k: (255, "Error: Can't make new window at (0,0)")
    with pytest.raises(DialogError) as ei:
        b.editbox("t", "/etc/foo.conf")
    assert "new window" in str(ei.value)


def test_esc_255_without_text_is_still_cancel(monkeypatch):
    b, be, w = _backend("dialog", {"dialog"})
    monkeypatch.setattr(be.shutil, "which", w)
    b._run = lambda args, **k: (255, "")
    assert b.editbox("t", "/etc/foo.conf") is None


def test_editbox_height_fits_a_short_terminal(monkeypatch):
    import os as _os
    b, be, w = _backend("dialog", {"dialog"})
    monkeypatch.setattr(be.shutil, "which", w)
    monkeypatch.setattr(be.os, "get_terminal_size",
                        lambda *a: _os.terminal_size((80, 18)))
    seen = {}
    b._run = lambda args, **k: seen.update(args=args) or (0, "x")
    b.editbox("t", "/etc/foo.conf")
    i = seen["args"].index("--editbox")
    assert int(seen["args"][i + 2]) <= 16      # rows - 2


def test_editbox_title_tells_the_keys(monkeypatch):
    # On a whiptail-themed TUI the editor is dialog: Enter adds a newline,
    # Tab reaches OK, Esc discards. Say so where the operator looks.
    b, be, w = _backend("whiptail", {"whiptail", "dialog"})
    monkeypatch.setattr(be.shutil, "which", w)
    seen = {}
    b._run = lambda args, **k: seen.update(args=args) or (0, "x")
    b.editbox("Edit cfg", "/etc/foo.conf")
    title = seen["args"][seen["args"].index("--title") + 1]
    assert "Tab" in title and "Esc" in title


def test_unexpected_editor_exception_is_logged(tmp_path, caplog):
    f = tmp_path / "config"; f.write_text("a\n")
    ctx = _ctx()
    ctx.dialog.editbox.side_effect = AttributeError("renamed attr")
    with caplog.at_level("ERROR"):
        assert config_edit.edit_config_in_app(ctx, f) is False
    assert "renamed attr" in caplog.text        # a witness, not just a msgbox


# ---- non-author review 10-30 (round 2)


def test_offer_yesno_dying_still_never_raises(tmp_path):
    from backend import DialogError, EditorUnavailable
    f = tmp_path / "config"; f.write_text("a\n")
    ctx = _ctx()
    ctx.dialog.editbox.side_effect = EditorUnavailable("no editbox")
    ctx.dialog.yesno.side_effect = DialogError("yesno died")
    assert config_edit.edit_config_in_app(ctx, f) is False
    assert f.read_text() == "a\n"


def test_editbox_killed_by_signal_is_failure_not_cancel(monkeypatch):
    from backend import DialogError
    b, be, w = _backend("dialog", {"dialog"})
    monkeypatch.setattr(be.shutil, "which", w)
    b._run = lambda args, **k: (-15, "")       # SIGTERM
    with pytest.raises(DialogError):
        b.editbox("t", "/etc/foo.conf")


def test_run_reads_the_capture_as_utf8_explicitly(monkeypatch):
    # Python's C-locale UTF-8 mode covers sudo's C locale, but a latin-1
    # locale would still decode an edited '°'/emoji body wrongly or raise.
    # Pin the explicit encoding rather than the ambient locale.
    import builtins
    import backend as be
    from backend import DialogBackend
    from types import SimpleNamespace
    b = DialogBackend(); b.backend = "dialog"
    monkeypatch.setattr(be.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(be, "clear_screen", lambda: None)
    reads = []
    real_open = builtins.open
    def spy(f, mode="r", *a, **k):
        if mode == "r":
            reads.append(k)
        return real_open(f, mode, *a, **k)
    monkeypatch.setattr(builtins, "open", spy)
    b._run(["--editbox", "/x", "10", "40"], tool="dialog", strip=False)
    assert reads and reads[-1].get("encoding") == "utf-8"
    assert reads[-1].get("errors") == "replace"
