"""Boundary timing helper — observability for cross-process calls.

A "boundary" is anywhere MeshAnchor talks to something *outside* its own
Python process: rnsd shared-instance RPC, meshtasticd TCP/HTTP, MeshCore
TCP, MQTT publish/subscribe, systemctl shell-outs, etc.

When one of those calls stalls, the symptom upstream is "the daemon
hung." Without per-boundary timings, we waste hours bisecting which
external system actually went sideways. This module is the shared floor:
every boundary call is wrapped, every slow call logs a forensic, every
boundary's p50/p95/p99 is queryable for status surfaces.

Usage:

    # Context manager — best when wrapping a block
    from utils.boundary_timing import timed_boundary
    with timed_boundary("rnsd.has_path", target=hash_short):
        has = RNS.Transport.has_path(dest_hash)

    # Call wrapper — best for a single call
    from utils.boundary_timing import call_boundary
    result = call_boundary(
        "rnsd.handle_outbound",
        router.handle_outbound, lxm,
        target=hash_short,
    )

    # Status / diagnostics
    from utils.boundary_timing import get_boundary_stats
    stats = get_boundary_stats()  # all boundaries
    stats = get_boundary_stats("rnsd.has_path")  # one boundary

Behavior:
- Sub-threshold calls log at DEBUG.
- Slow calls (>= threshold_s) log at WARNING with the full label.
- Exceptions log at WARNING with elapsed time, then re-raise.
- Counters and a ring buffer of recent samples are kept per label;
  ``get_boundary_stats`` exposes count / slow_count / error_count and
  p50/p95/p99 over the ring buffer.

Design rules (see ``.claude/plans/boundary_observability_charter.md``):
- Logger only — no Prometheus / OTel deps.
- Don't suppress exceptions — they propagate untouched.
- Default threshold 2.0s, override per call site only when documented.
- Don't wrap in-process functions; only daemon/socket/RPC boundaries.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Any, Callable, Deque, Dict, Iterator, Optional


DEFAULT_THRESHOLD_S: float = 2.0
"""Default WARN threshold for any boundary call. See charter for tuning rules."""

_RING_SIZE: int = 1000
"""Per-label ring buffer of recent sample durations for percentile reporting."""

logger = logging.getLogger("boundary")


_lock = threading.Lock()
_counters: Dict[str, Dict[str, int]] = defaultdict(
    lambda: {"count": 0, "slow_count": 0, "error_count": 0}
)
_samples: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=_RING_SIZE))


def _format_label(label: str, target: Optional[str]) -> str:
    if target:
        return f"{label}[{target}]"
    return label


@contextmanager
def timed_boundary(
    label: str,
    *,
    target: Optional[str] = None,
    threshold_s: float = DEFAULT_THRESHOLD_S,
) -> Iterator[None]:
    """Time a boundary call.

    ``label`` should be ``<system>.<operation>`` (e.g. ``rnsd.has_path``,
    ``meshtasticd.send_text_direct``). ``target`` adds a hash/id suffix
    for fan-out correlation, e.g. the destination hash prefix when several
    calls fire in quick succession against different targets.

    Exceptions inside the body propagate untouched. The duration up to
    the exception is still recorded as an error sample.
    """
    full_label = _format_label(label, target)
    t0 = time.monotonic()
    completed = False
    try:
        yield
        completed = True
    finally:
        elapsed = time.monotonic() - t0
        with _lock:
            counters = _counters[full_label]
            counters["count"] += 1
            if not completed:
                counters["error_count"] += 1
            elif elapsed >= threshold_s:
                counters["slow_count"] += 1
            _samples[full_label].append(elapsed)
        if not completed:
            logger.warning("rpc[%s] raised after %.3fs", full_label, elapsed)
        elif elapsed >= threshold_s:
            logger.warning(
                "rpc[%s] slow: %.3fs (>=%.1fs threshold)",
                full_label, elapsed, threshold_s,
            )
        else:
            logger.debug("rpc[%s] ok %.3fs", full_label, elapsed)


def call_boundary(
    label: str,
    fn: Callable[..., Any],
    *args: Any,
    target: Optional[str] = None,
    threshold_s: float = DEFAULT_THRESHOLD_S,
    **kwargs: Any,
) -> Any:
    """Call wrapper variant of :func:`timed_boundary`.

    Equivalent to::

        with timed_boundary(label, target=target, threshold_s=threshold_s):
            return fn(*args, **kwargs)

    Convenient when you only need to time one call and don't want a
    nested ``with`` block. Returns whatever ``fn`` returns.
    """
    with timed_boundary(label, target=target, threshold_s=threshold_s):
        return fn(*args, **kwargs)


def _percentile(sorted_samples: list, fraction: float) -> float:
    if not sorted_samples:
        return 0.0
    # Nearest-rank: index = ceil(fraction * n) - 1, clamped to [0, n-1]
    n = len(sorted_samples)
    idx = max(0, min(n - 1, int(fraction * n)))
    return sorted_samples[idx]


def _stats_dict(counters: Dict[str, int], samples_snapshot: list) -> Dict[str, Any]:
    sorted_samples = sorted(samples_snapshot)
    return {
        "count": counters["count"],
        "slow_count": counters["slow_count"],
        "error_count": counters["error_count"],
        "p50_s": _percentile(sorted_samples, 0.50),
        "p95_s": _percentile(sorted_samples, 0.95),
        "p99_s": _percentile(sorted_samples, 0.99),
        "samples": len(sorted_samples),
    }


def get_boundary_stats(label: Optional[str] = None) -> Dict[str, Any]:
    """Return per-boundary stats.

    With no argument, returns ``{label: stats_dict}`` for every boundary
    that has been observed in this process. With a ``label``, returns
    just that boundary's stats. Returns an empty dict / zero-stats dict
    when nothing has been recorded yet.
    """
    with _lock:
        if label is not None:
            counters = dict(_counters.get(label, {"count": 0, "slow_count": 0, "error_count": 0}))
            samples_snapshot = list(_samples.get(label, ()))
            return _stats_dict(counters, samples_snapshot)
        return {
            k: _stats_dict(dict(_counters[k]), list(_samples[k]))
            for k in _counters
        }


def reset_boundary_stats(label: Optional[str] = None) -> None:
    """Clear recorded counters and samples.

    With no argument, clears everything (use only for tests). With a
    ``label``, clears only that boundary.
    """
    with _lock:
        if label is None:
            _counters.clear()
            _samples.clear()
        else:
            _counters.pop(label, None)
            _samples.pop(label, None)
