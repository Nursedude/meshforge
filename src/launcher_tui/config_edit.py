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
    False — read or write failed (honest failure report shown),
    None  — cancelled or no change (nothing written, no report).
Never raises.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


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

    edited = ctx.dialog.editbox(box_title, str(p))
    if edited is None:
        return None  # operator cancelled — no change

    # The editbox round-trips through the dialog backend, which strips trailing
    # whitespace; a config file should end with exactly one newline.
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
