"""In-app apt install — the SHARED implementation for TUI callers.

The In-Domain Principle (MF018) says the fix comes to the operator: when the
TUI needs a system package (``tmux`` for the NomadNet unit, ``dialog`` for
the in-app config editor — whiptail has no editbox), it offers the install
and runs it here instead of printing a shell command. Formerly a private
method of ``_nomadnet_service_ops.py``; lifted 2026-10-30 when the editor
needed the same act (a fixed mechanism with copies drifts). ⚠️ Older copies
still exist (service_menu.py x2, radio_menu.py, main.py's no-dialog-layer
bootstrap, utils/emoji.py) with their own timeouts — migrating them is
queued, not done.

Contract: ``apt_install(package) -> (ok, output)``; never raises on a
failed install (timeout, missing apt, non-zero exit all return False with
the reason). A name that apt would not read as ONE package raises
ValueError — the caller passes a constant, so that is a programming error.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Tuple

#: Debian package-name grammar (policy 5.6.1): lowercase alnum, then
#: [a-z0-9+.-]; at least two characters; never a leading '-' (an option).
_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.\-]+$")


#: Same room the other in-app installers get (pip_install 600 s): a timeout
#: SIGKILLs sudo mid-dpkg, leaving the lock held or dpkg "interrupted".
DEFAULT_TIMEOUT_S = 600


def _apt(args, timeout):
    argv = ["apt-get"] + args
    if os.geteuid() != 0:
        argv = ["sudo"] + argv
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"apt-get {' '.join(args)} timed out after {timeout}s"
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"apt-get {' '.join(args)} failed: {e}"
    return proc.returncode == 0, ((proc.stdout or "") + (proc.stderr or "")).strip()


def apt_install(package: str, timeout: int = DEFAULT_TIMEOUT_S) -> Tuple[bool, str]:
    """Install one apt package; returns (ok, combined_output).

    Root runs apt-get directly; a non-root TUI goes through ``sudo`` (the
    Admin-mode privilege separation in CLAUDE.md), which prompts on the
    operator's tty when a password is needed. "Unable to locate package" on
    the first try means a stale/empty package index (fresh image), so the
    index is refreshed ONCE and the install retried.
    """
    if not isinstance(package, str) or not _PACKAGE_RE.match(package) \
            or ".." in package:
        raise ValueError(f"not a single apt package name: {package!r}")
    ok, out = _apt(["install", "-y", package], timeout)
    if ok or "Unable to locate package" not in out:
        return ok, out
    up_ok, up_out = _apt(["update"], timeout)
    if not up_ok:
        return False, f"{out}\n\napt-get update also failed:\n{up_out}"
    return _apt(["install", "-y", package], timeout)
