"""Hard-timeout wrapper for gateway-hot-path RNS RPC calls.

`utils.boundary_timing.call_boundary()` records p50/p95/p99 of every
wrapped call but only **logs slow calls — it never aborts them**. If
rnsd's RPC listener wedges after init (`unix_wait_for_peer`), an
unwrapped `RNS.Transport.has_path()` / `LXMRouter.handle_outbound()`
call blocks the gateway thread indefinitely with no exception, no
log past the slow-warning threshold, and no recovery short of
operator-driven `systemctl restart`.

This module composes `call_boundary`'s metrics ring buffer with the
field-validated `utils.rns_init.bounded_block` watchdog pattern. Every
wrapped call now has:

- Same p50/p95/p99 sample stream (no metrics regression).
- A hard timeout. When exceeded, the watchdog thread:
  1. Fires the `on_wedge` hook — default publishes a `WedgeEvent` to
     `utils.wedge_events` and bumps a per-label counter that
     operators can scrape via Prometheus.
  2. Calls `os._exit(2)` so systemd (Restart=on-failure on the
     gateway unit) restarts the process. The kernel `connect()` in
     `unix_wait_for_peer` is uninterruptible from userland, so process
     abort is the only escape — same conclusion as the lab tracer
     reached in `utils.rns_init.bounded_block`.

Test-mode escape hatch: pass `exit_on_wedge=False` so the watchdog
publishes the event without killing the test runner. Tests that
exercise the full path (including the `on_wedge` action chain) can
also have the wrapped fn raise `WedgeTimeout` synchronously to
simulate a wedge without timing on a real timer.

Env-var per-label overrides:

    MESHFORGE_BOUNDED_RPC_TIMEOUT_<UPPERCASE_LABEL_WITH_UNDERSCORES>=N

For example, `MESHFORGE_BOUNDED_RPC_TIMEOUT_RNSD_HANDLE_OUTBOUND=30`
bumps the `rnsd.handle_outbound` budget from its compiled-in default
to 30 seconds. Debug knob for operators; not a normal config surface.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from typing import Any, Callable, Dict, Optional

from utils import wedge_events
from utils.boundary_timing import timed_boundary
from utils.boundary_timing import DEFAULT_THRESHOLD_S


logger = logging.getLogger(__name__)


class WedgeTimeout(RuntimeError):
    """Raised when a `bounded_call` exceeded its timeout budget.

    In production this is unreachable on the calling thread — the
    watchdog runs `os._exit(2)` before any exception can propagate
    out of the wrapped call. Tests that want to assert on the
    on-wedge action chain pass `exit_on_wedge=False`, and the
    wrapped fn raises `WedgeTimeout` synchronously to simulate a
    wedge without timer flakiness.

    The error string contains the word "timeout" so
    `gateway.message_queue.RetryPolicy.should_retry` classifies it
    as retriable — wedged messages survive process restart via the
    persistent queue.
    """


# Per-label wedge counters, exposed to Prometheus + operator CLI.
# Bumped by the default `on_wedge` hook. Module-level + lock so a
# concurrent set of wedged calls (rare but possible — moc2 had two
# SYN-SENT sockets) doesn't lose counts.
_wedge_counters: Dict[str, int] = defaultdict(int)
_counter_lock = threading.Lock()


def get_wedge_counters() -> Dict[str, int]:
    """Snapshot of per-label wedge counts since process start.

    Returns a fresh dict — caller may mutate without affecting
    internal state. Sum of values is the total number of wedge
    events observed by this process.
    """
    with _counter_lock:
        return dict(_wedge_counters)


def _reset_counters_for_tests() -> None:
    """Test-only: zero out counters between cases."""
    with _counter_lock:
        _wedge_counters.clear()


def _env_override_timeout(label: str) -> Optional[float]:
    """Read MESHFORGE_BOUNDED_RPC_TIMEOUT_<LABEL> from env, if set.

    Returns the float value or None if unset / malformed. Label
    casing is normalized: dots become underscores, hyphens too,
    then uppercased. So `rnsd.handle_outbound` →
    `MESHFORGE_BOUNDED_RPC_TIMEOUT_RNSD_HANDLE_OUTBOUND`.
    """
    key = (
        "MESHFORGE_BOUNDED_RPC_TIMEOUT_"
        + label.replace(".", "_").replace("-", "_").upper()
    )
    raw = os.environ.get(key)
    if not raw:
        return None
    try:
        v = float(raw)
        if v > 0:
            return v
    except (TypeError, ValueError):
        logger.warning(
            "bounded_rpc: ignoring malformed %s=%r — expected positive float",
            key, raw,
        )
    return None


def default_on_wedge(label: str, target: Optional[str], timeout_s: float) -> None:
    """Default hook: bump the wedge counter, publish a WedgeEvent.

    Does NOT call `os._exit` itself — `bounded_call` does that after
    the hook returns (so tests can override the hook without losing
    the publish-then-exit ordering, and so callers can chain extra
    actions like circuit-breaker `trip_open` ahead of the abort).

    Public so callers in `rns_bridge.py` can build a composite hook:

        def _on_wedge(label, target, timeout_s):
            if target:
                self._circuit_breaker.trip_open(target, "wedge")
            bounded_rpc.default_on_wedge(label, target, timeout_s)
    """
    full = label if not target else f"{label}[{target}]"
    with _counter_lock:
        _wedge_counters[label] += 1
    try:
        wedge_events.publish(wedge_events.WedgeEvent(
            ts=time.time(),
            source="gateway.rns",
            label=label,
            target=target,
            timeout_s=timeout_s,
            note="",
        ))
    except Exception:
        # Publishing must never crash the watchdog; the abort is the
        # important outcome and stdout/journalctl still get the loud log.
        logger.exception("bounded_rpc: wedge_events.publish failed for %s", full)


WedgeHook = Callable[[str, Optional[str], float], None]


def bounded_call(
    label: str,
    fn: Callable[..., Any],
    *args: Any,
    target: Optional[str] = None,
    timeout_s: float,
    threshold_s: float = DEFAULT_THRESHOLD_S,
    on_wedge: Optional[WedgeHook] = None,
    exit_on_wedge: bool = True,
    **kwargs: Any,
) -> Any:
    """Call `fn(*args, **kwargs)` under a hard timeout, with metrics.

    A strict superset of `utils.boundary_timing.call_boundary`:

    - Records the same p50/p95/p99 sample stream via `timed_boundary`,
      so existing operator dashboards reading `get_boundary_stats()`
      keep working without modification.
    - Arms a watchdog thread that, on timeout exceeded:
      a. invokes `on_wedge(label, target, timeout_s)` (default: bump
         counter + publish WedgeEvent); exceptions inside the hook are
         logged but don't block abort.
      b. if `exit_on_wedge` is True, calls `os._exit(2)`. Default-true
         because in production the only escape from a wedged kernel
         `connect()` is process abort + systemd restart.

    Test-mode escape: `exit_on_wedge=False` makes the watchdog publish
    and return without killing the process. Tests assert on the
    counter/wedge_events side effects.

    Env-var per-label timeout override (`MESHFORGE_BOUNDED_RPC_TIMEOUT_*`)
    takes precedence over the `timeout_s` argument when set. Lets
    operators tune one call site without redeploying.
    """
    if on_wedge is None:
        on_wedge = default_on_wedge

    effective_timeout = _env_override_timeout(label) or timeout_s
    done = threading.Event()
    # If the watchdog fires, set this so the function's eventual return
    # (in tests where exit_on_wedge=False) can be ignored. Production
    # path never reads this because `_exit` already terminated.
    fired: Dict[str, bool] = {"wedge": False}

    def _watchdog() -> None:
        if not done.wait(timeout=effective_timeout):
            fired["wedge"] = True
            full = label if not target else f"{label}[{target}]"
            logger.error(
                "rpc[%s] WEDGED at %.1fs — aborting "
                "(see project_rnsd_rpc_listener_wedge.md)",
                full, effective_timeout,
            )
            try:
                on_wedge(label, target, effective_timeout)
            except Exception:
                logger.exception(
                    "bounded_rpc: on_wedge hook raised for %s", full
                )
            if exit_on_wedge:
                os._exit(2)

    watchdog = threading.Thread(
        target=_watchdog, daemon=True, name=f"bounded-{label}",
    )
    watchdog.start()
    try:
        with timed_boundary(label, target=target, threshold_s=threshold_s):
            return fn(*args, **kwargs)
    except WedgeTimeout:
        # Test-mode synthetic wedge: the wrapped fn raised WedgeTimeout
        # directly. Run the on_wedge action chain (matches the
        # watchdog's behavior) and re-raise so tests can assert on it.
        # The watchdog hasn't fired yet (done is still unset), so we
        # invoke the hook ourselves. exit_on_wedge is honored: tests
        # set it False; real callers using exit_on_wedge=True won't
        # see this branch in production because real RNS wedges hang
        # rather than raise.
        try:
            on_wedge(label, target, effective_timeout)
        except Exception:
            logger.exception(
                "bounded_rpc: on_wedge hook raised for %s (synthetic)",
                label if not target else f"{label}[{target}]",
            )
        if exit_on_wedge:
            os._exit(2)
        raise
    finally:
        done.set()
