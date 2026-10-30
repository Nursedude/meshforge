"""In-app config-file editing — the In-Domain alternative to spawning nano/vi.

The In-Domain Principle (MF018, foundations/in_domain_principle.md) forbids
ejecting the operator to a shell editor to fix MeshForge's own config. This is
the shared backend for every "Edit Config" handler: it reads a config file,
edits it INSIDE the TUI (the whiptail/dialog editbox), and writes the result
back — a privileged write (sudo) for a root-owned system file, an atomic user
write otherwise. It does NOT own the post-edit step (restart, reload, divergence
check, chown); each handler keeps its own.

Honest-failure-modes (the reason this returns a tri-state): an unreadable file,
a failed write, and a cancelled/unchanged edit are DISTINCT outcomes the caller
can see — never collapsed into a silent "saved". Returns:
    True  — edited and saved (success report shown),
    False — nothing usable happened and the operator was TOLD why: the file
            could not be read or written, OR the editor could not open (no
            editor installed and the install declined/failed, or the dialog
            died). False does NOT imply a write was attempted.
    None  — cancelled or no change (nothing written, no report).
Never raises.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _say_editor_failed(ctx, box_title, p, e, prefix=""):
    """Report an editor failure; return False. A DialogError is the expected
    failure; anything else is a defect and is logged with its traceback
    (honest_failure_modes #9: every swallow gets a witness)."""
    from backend import DialogError
    if isinstance(e, DialogError):
        ctx.dialog.msgbox(box_title, f"{prefix}Could not open the in-app "
                                     f"editor for:\n{p}\n\n{e}")
    else:
        logger.error("in-app editor failed unexpectedly for %s", p,
                     exc_info=(type(e), e, e.__traceback__))
        ctx.dialog.msgbox(box_title, f"{prefix}The in-app editor failed for:"
                                     f"\n{p}\n\n{type(e).__name__}: {e}")
    return False


def _open_editor(ctx, box_title, p):
    """editbox text, None (cancelled), or False (could not open — SAID).

    whiptail has no editbox (EditorUnavailable). That used to read as
    CANCEL, so "Edit Config" silently did nothing on every whiptail-only box
    (06-16..10-30). Now the missing editor is OFFERED in-app (MF018: the fix
    comes to the operator) and the edit retried ONCE after the install.
    Never raises.
    """
    from backend import EDITOR_PACKAGE, EditorUnavailable
    try:
        return ctx.dialog.editbox(box_title, str(p))
    except EditorUnavailable as e:
        gap = e
    except Exception as e:
        return _say_editor_failed(ctx, box_title, p, e)
    try:
        accepted = ctx.dialog.yesno(
            "In-App Editor Not Installed",
            f"{gap}\n\nInstall '{EDITOR_PACKAGE}' now (apt, needs admin) "
            f"and open the editor?")
    except Exception as e:
        return _say_editor_failed(ctx, box_title, p, e)
    if not accepted:
        ctx.dialog.msgbox(box_title, f"Not edited: {p}\n\n{gap}")
        return False
    from utils.apt_packages import apt_install
    ok, out = apt_install(EDITOR_PACKAGE)
    if not ok:
        ctx.dialog.msgbox(
            "Install Failed",
            f"Could not install '{EDITOR_PACKAGE}', so {p} was not "
            f"edited:\n\n{out[-1500:]}")
        return False
    try:
        return ctx.dialog.editbox(box_title, str(p))
    except Exception as e:
        return _say_editor_failed(ctx, box_title, p, e,
                                  prefix=f"'{EDITOR_PACKAGE}' installed, but: ")


def edit_config_in_app(ctx, path, *, title: Optional[str] = None) -> Optional[bool]:
    """Edit a config file in-app and persist it. See module docstring for the
    tri-state return contract."""
    p = Path(path)
    box_title = title or f"Edit {p.name}"

    try:
        original = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        ctx.dialog.msgbox(
            box_title, f"Cannot read the config file:\n{p}\n\n{e}")
        return False

    edited = _open_editor(ctx, box_title, p)
    if edited is False:
        return False
    if edited is None:
        return None  # operator cancelled — no change

    # The editbox returns the body unstripped (leading indent matters), but
    # dialog may drop or add the final newline; end with exactly one.
    edited = edited.rstrip("\n") + "\n"
    if edited.rstrip("\n") == original.rstrip("\n"):
        return None  # nothing changed — don't write, don't prompt downstream

    # Write back: a system file we cannot write directly goes through sudo; a
    # user-owned file gets an atomic replace. os.W_OK is the accurate per-file
    # signal — root sees W_OK on /etc, a non-root viewer does not (-> sudo).
    if os.access(str(p), os.W_OK):
        try:
            from utils.paths import atomic_write_text
            atomic_write_text(p, edited)
            ok, msg = True, f"Saved {p}"
        except OSError as e:
            ok, msg = False, f"Could not write {p}:\n{e}"
    else:
        from utils.service_check import _sudo_write
        ok, msg = _sudo_write(str(p), edited)

    ctx.report_action(
        ok,
        box_title, f"Saved changes to:\n{p}",
        "Save Failed", msg,
    )
    return ok
