"""UI-launch hold policy — a launch must not change WHICH services run.

2026-10-31 (moc, BIC-2 kill run): the operator stopped meshtasticd, opened the
TUI, and the launcher's ``ServiceOrchestrator.startup()`` started it again —
no prompt, no line. ``startup(hold_operator_stops=True)`` asks
:func:`hold_decision` per service; a held service is recorded, not started,
and is not a failure.

BYTE-IDENTICAL in MeshForge and MeshAnchor (``scripts/parity_check.py``).
Each twin's ``utils.service_check.unit_boot_history`` produces the verdicts
(it builds systemctl argv its own way); the POLICY over them lives here once.
"""

import logging
from typing import Callable, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

# Policy over the closed boot-history vocabulary — every verdict is in exactly
# ONE set (pinned by a test that walks BOOT_HISTORY_VERDICTS,
# honest_failure_modes #7). Anything else is held, loudly.
UI_HOLD_VERDICTS = frozenset({'stopped_this_boot', 'stopping', 'unknown'})
UI_START_VERDICTS = frozenset({'active', 'not_started_this_boot', 'failed',
                               'absent'})

#: A first ``systemctl show`` that fails gets ONE slower retry before the unit
#: is held as unobservable (boot-time systemd can be slow to answer).
BOOT_HISTORY_RETRY_TIMEOUT = 15

History = Callable[..., Tuple[str, Optional[str]]]


def hold_decision(
    service_name: str,
    systemd_name: str,
    dependencies: Iterable[str],
    held: Dict[str, str],
    waiting: Dict[str, str],
    history: History,
) -> bool:
    """True when a UI launch must NOT touch ``service_name``.

    Records the reason in ``held`` (name -> why; the operator gets a literal
    start command) or ``waiting`` (name -> the held service it waits on; no
    start command — starting it alone is not the fix).

    * stopped this boot / stopping / unobservable -> held
    * depends on a held service: running -> left exactly as it is (True, not
      recorded, not failed); not running -> waiting
    * never started this boot / failed / absent / active -> False (start path)
    """
    verdict, since = history(systemd_name)
    if verdict == 'unknown':
        verdict, since = history(systemd_name,
                                 timeout=BOOT_HISTORY_RETRY_TIMEOUT)
    held_dep = next((d for d in dependencies if d in held or d in waiting),
                    None)
    if held_dep:
        if verdict == 'active':
            logger.info("%s running; its dependency %s is held - left as it is",
                        service_name, held_dep)
        else:
            waiting[service_name] = held_dep
            logger.warning("Not starting %s (%s): waits on held %s",
                           service_name, verdict, held_dep)
        return True
    if verdict == 'stopped_this_boot':
        held[service_name] = (f"inactive since {since or 'an unknown time'} "
                              f"this boot (stopped, or exited cleanly)")
    elif verdict == 'stopping':
        held[service_name] = "stop in progress"
    elif verdict == 'unknown':
        held[service_name] = ("boot history unobservable - cannot tell it was "
                              "not a stop")
    elif verdict not in UI_START_VERDICTS:
        held[service_name] = f"unhandled boot-history verdict {verdict!r}"
    if service_name in held:
        logger.warning("Holding %s: %s", service_name, held[service_name])
        return True
    return False


def hold_summary(held: Dict[str, str], waiting: Dict[str, str]) -> int:
    """Count what a UI launch held; log it. Non-zero = startup must NOT log
    "all started" nor emit ``all_ready`` (a hold is not a failure either)."""
    n = len(held) + len(waiting)
    if n:
        logger.warning("═══ Startup done with %d held (not started) ═══", n)
    return n
