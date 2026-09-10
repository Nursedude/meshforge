"""Per-probe isolation for the watchdog runner (2026-09-09, pass-3 finding 1).

``watchdog_runner.run_all_probes`` calls ~60 probes in sequence with NO
``try:`` between them. Any one probe raising reached the single tick-level
handler in ``main``, which set ``signals = []`` and ``coverage = None`` —
every class blanked for the tick, every held signal re-held, and the
watchdog's whole vocabulary silent because ONE observer had a bug. Three
live skins reached it the day this was written (an unimported helper on the
#69 inverted tier, an un-caught ``http.client.IncompleteRead``, an AF_UNIX
``socket()`` outside its try). The blast radius of a probe bug must be that
probe's classes, never the tick.

Design: every ``probe_*`` name the runner imports is re-bound to a wrapper
at import time (``install_probe_isolation(globals())`` in the runner)
rather than wrapping each of the ~70 call sites. The call sites keep their
literal ``probe_x(...)`` syntax, which is what
``tests/test_honesty_invariants.py`` walks by AST to prove every
SIGNAL_CLASSES member is reachable. A raised exception is logged WITH its
traceback, every class the probe owns is noted ``indeterminate`` (a witness
``build_coverage`` and mini's blind extractor can see — never a silent
``unknown``, never a clean), the probe's empty return shape is substituted,
and the remaining probes still run. The tick-level handler in ``main``
stays as the last resort.

Ownership is the probe's NAME minus ``probe_`` when that is a signal class,
with the seven historical exceptions pinned in ``PROBE_OWNS_OVERRIDES``.
The closed consumer for that table is
``tests/test_watchdog_runner_isolation.py``, which re-derives each probe's
classes from the string literals in its body and fails when the two
disagree (honest_failure_modes #7).

Lives in its own module because ``watchdog_runner.py`` sits at the MF025
1,500-line cap.
"""
from __future__ import annotations

import functools
import logging
from typing import Callable, Dict, Tuple

from utils.watchdog_probe_core import SIGNAL_CLASSES, note_disposition

logger = logging.getLogger("watchdog")

PROBE_OWNS_OVERRIDES: Dict[str, Tuple[str, ...]] = {
    "probe_rns_shared_instance_responsive": (
        "rns_shared_instance_unresponsive", "rns_instance_name_mismatch"),
    "probe_rns_rpc_responsive": ("rns_rpc_unresponsive",),
    "probe_lxmf_process_wedge": ("main_thread_wedge",),
    "probe_http_local": ("http_local_unresponsive",),
    "probe_foundation_drift": ("foundation_perms_drift",),
    "probe_rns_env_coherence": ("rns_stray_env_drift",),
    "probe_history_write_failure": ("history_write_stalled",),
}

#: Probes that return ``List[Signal]`` (0..N) — the runner ``extend``s
#: them, so their isolated empty value is ``[]``, not None. Adding a new
#: list-returning probe MUST add it here (and to the coverage test's tuple).
LIST_RETURNING_PROBES: Tuple[str, ...] = (
    "probe_lxmf_process_wedge",
    "probe_tracer_peer_unreachable",
    "probe_memory_cap_engaged",
)

#: Per-probe raise counter (process lifetime) — the witness for a probe
#: that owns no class in the table above, and a cheap thing to read in a
#: debugger. Reset only by restart, like the RPC streaks.
probe_raise_counts: Dict[str, int] = {}


def probe_owned_classes(name: str) -> Tuple[str, ...]:
    """Signal classes a probe (by function name) is responsible for."""
    if name in PROBE_OWNS_OVERRIDES:
        return PROBE_OWNS_OVERRIDES[name]
    stem = name[len("probe_"):] if name.startswith("probe_") else name
    return (stem,) if stem in SIGNAL_CLASSES else ()


def isolated(fn: Callable, *, name: str, owns: Tuple[str, ...], empty):
    """Wrap one probe so a raise becomes a witnessed, class-scoped miss.

    ``name`` is the probe AS WIRED in the runner (its binding), not
    ``fn.__name__`` — a test double or a re-bound callable must still be
    reported under the name the operator greps for.
    """
    @functools.wraps(fn)
    def _guarded(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the whole point
            logger.error(
                "watchdog: probe %s raised %s: %s — its class(es) %s read "
                "indeterminate this tick; other probes continue",
                name, type(exc).__name__, exc,
                list(owns) or "<unmapped>", exc_info=True,
            )
            reason = f"probe raised {type(exc).__name__}: {exc}"[:240]
            for cls in owns:
                note_disposition(cls, "indeterminate", reason=reason)
            probe_raise_counts[name] = probe_raise_counts.get(name, 0) + 1
            return [] if isinstance(empty, list) else empty
    return _guarded


def install_probe_isolation(namespace: dict) -> int:
    """Re-bind every ``probe_*`` callable in ``namespace`` to its isolated
    form. Returns how many were wrapped. Idempotent per name (a wrapped
    probe carries ``__wrapped__``)."""
    wrapped = 0
    for name, fn in list(namespace.items()):
        if not (name.startswith("probe_") and callable(fn)):
            continue
        if getattr(fn, "__wrapped__", None) is not None:
            continue
        namespace[name] = isolated(
            fn, name=name, owns=probe_owned_classes(name),
            empty=[] if name in LIST_RETURNING_PROBES else None)
        wrapped += 1
    return wrapped
