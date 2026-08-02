"""tracemalloc heap-attribution probe — env-gated, observation-only.

Purpose (2026-08-02, gateway memory-growth diagnosis): the gateway bridge
process grows ~100MB -> 360-400MB over 2-4 days on the gateway boxes. A code
audit named several uncapped accumulators, but which one dominates the live
heap is unknowable from reading — this probe converts that BELIEVED ranking
into a VERIFIED attribution by snapshotting the real process's allocations.

Design:
- INERT unless the MESHFORGE_TRACEMALLOC env var is set to a positive int
  (the traceback frame depth, clamped 1..25). Unset/empty/"0" = no tracing,
  no thread, zero cost. This is a diagnostic you arm per-box via a systemd
  drop-in, not a permanent fleet resident:

      # /etc/systemd/system/meshforge-gateway.service.d/50-tracemalloc.conf
      [Service]
      Environment=MESHFORGE_TRACEMALLOC=8

- On start: tracemalloc.start(nframes), then a daemon thread takes a
  BASELINE snapshot after ~60s (post-init steady state, so startup
  allocations don't drown the growth signal) and thereafter writes a report
  every 15 min to ~/.local/share/meshforge/tracemalloc_report.txt with:
  traced current/peak, top allocations grouped by line, and — the payload —
  top GROWTH since baseline. Atomic writes; each report fully replaces the
  last (the interesting state is "now vs baseline", not history).

- Failure honesty: a failed tick logs ERROR with the exception (the swallow
  leaves a witness) and the loop continues; the probe must never take the
  gateway down. The report itself carries its own generation timestamp, so a
  wedged probe is visible as a stale file, never as silently-frozen "fresh"
  numbers.

Cost warning: tracemalloc overhead is proportional to live allocation count
(tens of MB + CPU on a busy gateway) and the retained baseline snapshot adds
more. Run it on a box with headroom (moc, 3.8GB), not on the 905MB boxes.
"""

from __future__ import annotations

import os
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Optional

from utils.logging_config import get_logger
from utils.paths import atomic_write_text, get_real_user_home

logger = get_logger("utils.tracemalloc_probe")

ENV_VAR = "MESHFORGE_TRACEMALLOC"
DEFAULT_REPORT_PATH = (
    get_real_user_home() / ".local" / "share" / "meshforge" / "tracemalloc_report.txt"
)
BASELINE_DELAY_S = 60.0
REPORT_INTERVAL_S = 900.0
TOP_N = 30
MAX_FRAMES = 25

_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


def _parse_env(raw: Optional[str]) -> int:
    """Return the frame depth from the env value, or 0 for 'stay inert'.

    Unset/empty/"0" -> 0 (inert). A non-integer value is a config mistake:
    warn loudly but arm anyway at a sane depth — the operator who set the
    var wanted tracing, and silently doing nothing would be the classic
    degraded-state-reads-as-valid failure.
    """
    if raw is None or raw.strip() == "" or raw.strip() == "0":
        return 0
    try:
        depth = int(raw.strip())
    except ValueError:
        logger.warning(
            "%s=%r is not an integer — arming tracemalloc at default depth 8",
            ENV_VAR, raw,
        )
        return 8
    if depth <= 0:
        return 0
    return min(depth, MAX_FRAMES)


def _snapshot() -> tracemalloc.Snapshot:
    snap = tracemalloc.take_snapshot()
    return snap.filter_traces((
        tracemalloc.Filter(False, tracemalloc.__file__),
        tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
        tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
    ))


def _fmt_mb(nbytes: int) -> str:
    return f"{nbytes / (1024 * 1024):.2f}MB"


def _render_report(
    current: tracemalloc.Snapshot,
    baseline: Optional[tracemalloc.Snapshot],
    baseline_ts: Optional[float],
) -> str:
    traced_now, traced_peak = tracemalloc.get_traced_memory()
    lines = [
        f"# tracemalloc_report generated_ts={time.time():.0f} "
        f"({time.strftime('%Y-%m-%d %H:%M:%S')})",
        f"traced_current={_fmt_mb(traced_now)} traced_peak={_fmt_mb(traced_peak)}",
        f"baseline_ts={baseline_ts:.0f}" if baseline_ts else "baseline=NOT_YET_TAKEN",
        "",
        f"## top {TOP_N} allocations by current size (lineno)",
    ]
    for stat in current.statistics("lineno")[:TOP_N]:
        lines.append(f"{_fmt_mb(stat.size):>12}  count={stat.count:<8} {stat.traceback}")

    if baseline is not None:
        lines += ["", f"## top {TOP_N} GROWTH since baseline (lineno)"]
        for stat in current.compare_to(baseline, "lineno")[:TOP_N]:
            if stat.size_diff <= 0:
                continue
            lines.append(
                f"{_fmt_mb(stat.size_diff):>12} (+{stat.count_diff})  {stat.traceback}"
            )
        lines += ["", f"## top 10 GROWTH since baseline (full traceback)"]
        for stat in current.compare_to(baseline, "traceback")[:10]:
            if stat.size_diff <= 0:
                continue
            lines.append(f"{_fmt_mb(stat.size_diff):>12} (+{stat.count_diff})")
            for frame_line in stat.traceback.format():
                lines.append(f"    {frame_line.strip()}")
    return "\n".join(lines) + "\n"


def _probe_loop(report_path: Path) -> None:
    baseline: Optional[tracemalloc.Snapshot] = None
    baseline_ts: Optional[float] = None
    # First wake takes the baseline and writes a liveness report; after
    # that, one report per interval.
    wait_s = BASELINE_DELAY_S
    while not _stop_event.wait(wait_s):
        wait_s = REPORT_INTERVAL_S
        try:
            snap = _snapshot()
            if baseline is None:
                baseline = snap
                baseline_ts = time.time()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(report_path, _render_report(snap, baseline, baseline_ts))
        except Exception as e:  # never take the gateway down; leave a witness
            logger.error("tracemalloc probe tick failed: %s", e, exc_info=True)


def maybe_start_from_env(report_path: Optional[Path] = None) -> bool:
    """Arm the probe if MESHFORGE_TRACEMALLOC requests it. Returns True if armed.

    Call as early as possible in the process (before the bridge is built) so
    construction-time allocations are traced. Idempotent: a second call while
    the probe thread is alive is a no-op returning True.
    """
    global _thread
    depth = _parse_env(os.environ.get(ENV_VAR))
    if depth == 0:
        return False
    if _thread is not None and _thread.is_alive():
        return True
    if not tracemalloc.is_tracing():
        tracemalloc.start(depth)
    path = report_path or DEFAULT_REPORT_PATH
    logger.warning(
        "tracemalloc probe ARMED: depth=%d interval=%.0fs report=%s "
        "(diagnostic — adds memory+CPU overhead, disarm the drop-in when done)",
        depth, REPORT_INTERVAL_S, path,
    )
    _stop_event.clear()
    _thread = threading.Thread(target=_probe_loop, args=(path,),
                               name="tracemalloc-probe", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    """Stop the probe thread (tests / clean shutdown)."""
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5.0)
