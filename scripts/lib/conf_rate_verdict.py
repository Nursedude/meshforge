#!/usr/bin/env python3
"""ONE verdict for honest_status.sh's "live conf_rate<=1.0" leg (2026-11-07).

Both sources of a box's confirmation_rate — the map's live
``/api/gateway/delivery`` body and the gateway-published
``delivery_snapshot.json`` (a gateway-only box serves no map) — are judged
HERE, by one function. Round 2 of the review found the two inline snippets
this replaces disagreeing on the same value (a string rate FAILED from the
snapshot and PASSED from the API) and the API one fail-OPEN on anything it
could not parse; one judge is the seam that ends that class.

Usage::

    printf '%s' "$body" | conf_rate_verdict.py api
    printf '%s' "$body" | HS_SNAP_NOW=<box epoch> conf_rate_verdict.py snapshot

Prints exactly one token and always exits 0 (the gate reads the token):

    OK:<rate>     judged, within bound       → checked
    NONE          rate present and null      → checked (no terminal events yet)
    VIOL:<value>  > 1.0, non-finite, or not a number → the leg FAILS
    BLIND:<code>  could not testify          → the leg is UNKNOWN for this box

A crash inside this program also prints a BLIND token — it never prints
nothing, and never OK.
"""
from __future__ import annotations

import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.environ.get("HONEST_SNAP_SRC") or os.path.join(_HERE, "..", "..", "src")


def judge_rate(r) -> str:
    """The ONE rate judgement. Exact for ints (no float overflow)."""
    if r is None:
        return "NONE"
    if isinstance(r, bool) or not isinstance(r, (int, float)):
        return "VIOL:shape=" + type(r).__name__
    if isinstance(r, int):
        # Exact int compare; never float() it (r3 F11: -10**400 overflowed).
        if r > 1:
            return "VIOL:" + _cap(str(r))
        return "OK:%.3f" % r if -10**6 < r else "OK:" + _cap(str(r))
    if not math.isfinite(r) or r > 1.0:
        # repr, not %.3f: 1.0000001 must not print as "1.000" beside "VIOL".
        return "VIOL:" + _cap(repr(r))
    return "OK:%.3f" % r


def _cap(text: str, n: int = 40) -> str:
    """One-line det: a 401-digit value must not swamp the leg (r3 F12)."""
    return text if len(text) <= n else text[:n] + "..."


def verdict_api(text: str) -> str:
    try:
        d = json.loads(text)
    except ValueError:
        return "BLIND:api-unparseable"
    if not isinstance(d, dict):
        return "BLIND:api-not-object"
    if "error" in d:
        # The map's own error envelope (HTTP 500 with JSON) is not a rate.
        return "BLIND:api-error"
    if "confirmation_rate" not in d:
        # Key MISSING is blindness; key present-and-null is NONE (no events).
        return "BLIND:api-no-rate-key"
    return judge_rate(d["confirmation_rate"])


def verdict_snapshot(text: str, now_raw: str) -> str:
    try:
        now = float(now_raw)
    except (TypeError, ValueError):
        return "BLIND:snapshot-no-box-clock"
    if not math.isfinite(now):
        return "BLIND:snapshot-no-box-clock"
    try:
        w = json.loads(text)
    except ValueError:
        return "BLIND:snapshot-unreadable"
    sys.path.insert(0, _SRC)
    try:
        from utils.watchdog_probes_gateway import judge_gateway_state
    except Exception as e:  # noqa: BLE001 — named in the token, never swallowed
        return "BLIND:snapshot-reader-unimportable(%s)" % type(e).__name__
    payload, code, _age = judge_gateway_state(w, now=now, key="snapshot")
    if payload is None:
        return "BLIND:snapshot-" + str(code)
    if "confirmation_rate" not in payload:
        return "BLIND:snapshot-no-rate-key"
    return judge_rate(payload["confirmation_rate"])


def main(argv) -> str:
    mode = argv[1] if len(argv) > 1 else ""
    text = sys.stdin.read()
    if mode == "api":
        return verdict_api(text)
    if mode == "snapshot":
        return verdict_snapshot(text, os.environ.get("HS_SNAP_NOW", ""))
    return "BLIND:verdict-bad-mode"


if __name__ == "__main__":
    try:
        tok = main(sys.argv)
    except Exception as e:  # noqa: BLE001 — a crash is BLIND with its cause
        tok = "BLIND:verdict-crashed(%s: %s)" % (type(e).__name__, str(e)[:80])
    print(tok)
