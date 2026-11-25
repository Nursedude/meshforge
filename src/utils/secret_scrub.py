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
#: the same field in JSON (``meshtastic --info`` prints module preferences
#: as JSON: ``"password": "<value>"`` — review r2: the line form misses it)
_PASSWORD_JSON = re.compile(
    r"""(?i)(["'][\w.]*(?:password|passphrase|wifi_?psk)["']\s*:\s*)(["'])"""
    r"""(?:(?!\2)[^\\]|\\.)*\2""")
# JSON or a dict repr (r1); wifiPsk is a password in all but name (r2). A
# COMPLETE value only: a cut-off chunk is never fed here — the one caller that
# could produce one (a timed-out Radio Info) withholds partial output instead
# (r3: an end-of-text match let a crafted node name blank the rest of the
# screen, and a cut after a backslash still leaked a prefix)
#: a config KEY whose value is a secret (display and logs mask it)
_SECRET_KEY = re.compile(
    r"(?i)password|passphrase|passwd|secret|psk|private_?key|admin_?key|api_?key|token")


def is_secret_key(key: object) -> bool:
    """True for a settings key whose VALUE must never be shown or logged."""
    return bool(_SECRET_KEY.search(str(key)))


def scrub_secret(text: Optional[str], *secrets: Optional[str],
                 label: str = "<password>") -> str:
    """``text`` with every non-empty known secret replaced by ``label``.
    A plain substring replace: a short password may garble unrelated text —
    the safe way to fail (review r1 nit, recorded)."""
    out = text or ""
    # only non-empty STRINGS (a corrupt backup's int/list "url" crashed the
    # error path that called this — Fable 11-24); LONGEST first, so a secret
    # that is a prefix of another never leaves the longer one's tail
    for sec in sorted((s for s in secrets if isinstance(s, str) and s), key=len, reverse=True):
        out = out.replace(sec, label)
    return out


def redact_password_lines(text: Optional[str]) -> str:
    """``text`` with the value of any ``password: …`` / ``password = …`` line
    hidden — for output whose secret the caller never saw."""
    out = _PASSWORD_JSON.sub(r"\1\2<hidden>\2", text or "")
    return _PASSWORD_LINE.sub(r"\1<hidden>", out)
