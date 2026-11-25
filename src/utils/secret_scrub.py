"""Never echo a secret on screen from a child's output or error (operator
2026-11-24: "one posture line" — the argv residual is SAID, and nothing shown
on screen ever carries the password).

ONE implementation for every caller, the TUI handlers and utils alike
(hfm #5): the meshtastic CLI echoes ``Set mqtt.password to <value>``,
``str(TimeoutExpired)`` embeds the whole argv, ``meshtastic --get mqtt``
prints ``password: <value>``.
"""
from __future__ import annotations

import re
from typing import Optional

#: a "password: value" / "password = value" line in a tool's own output —
#: the value is hidden even when the caller does not know it (a radio's
#: stored mqtt.password read back with --get)
_PASSWORD_LINE = re.compile(r"(?im)^(\s*[\w.]*password\s*[:=]\s*)(\S.*)$")


def scrub_secret(text: Optional[str], *secrets: Optional[str]) -> str:
    """``text`` with every non-empty known secret replaced by ``<password>``.
    A plain substring replace: a short password may garble unrelated text —
    the safe way to fail (review r1 nit, recorded)."""
    out = text or ""
    for sec in secrets:
        if sec:
            out = out.replace(sec, "<password>")
    return out


def redact_password_lines(text: Optional[str]) -> str:
    """``text`` with the value of any ``password: …`` / ``password = …`` line
    hidden — for output whose secret the caller never saw."""
    return _PASSWORD_LINE.sub(r"\1<hidden>", text or "")
