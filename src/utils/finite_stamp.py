"""The ONE finite-number rule for stamps read from remote JSON.

A value from a peer's spool / truth document is a real time (or count) only
if it is a finite real: ``bool`` is an ``int`` (``True`` = 1970-01-01T00:00:01),
NaN fails every comparison (a NaN age passes ``age < 0 or age > STALE`` —
fresh forever), ``inf`` and ``10**400`` (valid JSON) overflow the float maths.
Each is that box UNOBSERVABLE, never a raise and never "fresh".

Stdlib-only and its own module on purpose (spool-stamp seam, 2026-11-26): the
readers (box_truth_center.read_spool, fleet_truth, the collector's skew gate,
the watchdog's peer-cron leg) import it at module top with nothing that can
fail beneath it. It lived as ``fleet_truth._finite`` and was hand-copied into
read_spool; a third reader drifted (hfm #5). Byte-identical with MeshAnchor
(parity_check BYTE_IDENTICAL).
"""
from __future__ import annotations

import math
from typing import Any, Optional


def finite(v: Any) -> Optional[float]:
    """A finite float from remote JSON, else None (bool, NaN, inf, 10**400,
    str, None, containers)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None
