"""Watchdog probes — TIMER-triggered user-unit failure shapes.

Closes the last uncovered corner of the user-unit blindness class. The two
existing detectors between them cover everything EXCEPT this one:

- ``probe_service_inactive`` — structurally blind to user units entirely
  (root/system-context ``systemctl`` cannot see them).
- ``probe_nomadnet_crashloop`` — one named unit, LIVE restart loop only.
- ``probe_user_unit_inactive`` — enrolled **always-on** ``.service`` daemons
  (``default.target.wants`` + ``invocation:*`` markers). Its docstring is
  explicit that **timers are out of scope**: a timer carries no invocation
  marker, and a oneshot service is *supposed* to be inactive between firings,
  so "not running" says nothing about its health.

That left a real hole, found the hard way on 2026-07-19: kiai's
``meshforge-tracer.timer`` was enabled and firing every 10 minutes, and its
oneshot service exited 2 (``no peers in lab_peers — nothing to do``) on
**every single firing since 2026-07-12** — a week of silent failure that no
probe could see. The unit is not in ``default.target.wants`` (the *timer* is
in ``timers.target.wants``), it is correctly inactive between firings, and it
never crashloops. Every existing leg reads it as healthy.

Part of the ``watchdog_probes`` split — import via the ``utils.watchdog_probes``
hub, not from here. Split into its own module (2026-07-19) because
``watchdog_probes_service`` sits at the MF025 1,500-line ratchet.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional

from utils.watchdog_probe_core import Signal, note_disposition

# Reused rather than duplicated: this helper is already the proven bus-free
# way for root to read a USER unit's journal (``USER_UNIT=`` field selector,
# no sudo, works under the watchdog's NoNewPrivileges sandbox). Its name says
# "restart" for its first caller, but it is a generic
# (unit, pattern) -> timestamps reader, and it already returns the honest
# tri-state None on an unobservable journal.
from utils.watchdog_probes_service import (
    _journal_user_unit_restart_ts as _journal_user_unit_ts,
)

logger = logging.getLogger("watchdog")

DEFAULT_USER_TIMER_FAILING_STATE = \
    "/var/lib/meshforge/user_timer_failing_debounce.json"

# Window we read the journal over. Must comfortably span several firings of a
# typical timer so "every recent firing failed" is a real judgement and not a
# single sample.
USER_TIMER_FAILING_LOOKBACK = "3h"
# Consecutive-ish failures required inside the window. 2 keeps a one-off blip
# (a transient RNS wedge during a deploy) from paging, while a genuinely
# broken timer job clears it within two cadences.
USER_TIMER_FAILING_MIN_FAILURES = 2
# The newest failure must be at least this fresh. Without it, a job fixed an
# hour ago would keep paging off its own history until the window rolled — the
# same post-fix false-page trap nomadnet_crashloop's recency gate exists for.
USER_TIMER_FAILING_RECENCY_S = 3600.0

_FAIL_PATTERN = "Failed with result"
_OK_PATTERN = "Finished "


def _load_streak(state_path: str) -> int:
    """Consecutive-tick streak; any error → 0 (favour silence over a false page)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_streak(state_path: str, streak: int) -> None:
    """Persist the debounce streak (atomic rename, never raises).

    A persistent write failure would pin the streak at 1 forever, so the probe
    would silently NEVER fire during a real outage. The swallow therefore
    leaves a witness in the watchdog journal (honest_failure_modes #9).
    """
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError as exc:
        logger.warning(
            "user_timer_unit_failing: could not persist debounce streak to %s "
            "(%s) — the probe may not advance past its debounce floor; "
            "check %s is writable.",
            state_path, exc, os.path.dirname(state_path) or state_path,
        )


def _enabled_user_timers(user_home: str) -> Optional[Dict[str, str]]:
    """Map ``timer unit name -> triggered service unit name`` for the timers
    the operator has enabled (symlinks in
    ``~/.config/systemd/user/timers.target.wants/``).

    The triggered service is ``Unit=`` from the ``[Timer]`` section when the
    author set one, else systemd's default of the same stem with a
    ``.service`` suffix.

    Returns ``None`` when the wants directory exists but cannot be read — that
    is *unobservable*, which the caller must not collapse into "no timers"
    (honest_failure_modes #1). An absent directory is a real, observed empty
    enrollment and returns ``{}``.
    """
    wants = os.path.join(user_home, ".config", "systemd", "user",
                         "timers.target.wants")
    if not os.path.isdir(wants):
        return {}
    try:
        names = [n for n in os.listdir(wants) if n.endswith(".timer")]
    except OSError:
        return None

    out: Dict[str, str] = {}
    for timer in names:
        service = timer[: -len(".timer")] + ".service"
        # Best-effort Unit= override. A unreadable timer file falls back to
        # the stem default rather than dropping the timer from the map — the
        # conservative choice is to keep WATCHING it under its likely name.
        try:
            with open(os.path.join(wants, timer), "r", encoding="utf-8",
                      errors="replace") as fh:
                body = fh.read()
            m = re.search(r"(?mi)^\s*Unit\s*=\s*(\S+)\s*$", body)
            if m and m.group(1).endswith(".service"):
                service = m.group(1)
        except OSError:
            pass
        out[timer] = service
    return out


def probe_user_timer_unit_failing(
    *,
    operator=None,
    user_home: Optional[str] = None,
    lookback: str = USER_TIMER_FAILING_LOOKBACK,
    min_failures: int = USER_TIMER_FAILING_MIN_FAILURES,
    recency_s: float = USER_TIMER_FAILING_RECENCY_S,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    journalctl_path: str = "journalctl",
    ts_fn=None,
    now: Optional[float] = None,
) -> Optional[Signal]:
    """Fire when an enabled USER **timer's** job keeps failing every firing.

    The 2026-07-12→19 kiai class: ``meshforge-tracer.timer`` fired on cadence
    for a week while its oneshot service exited 2 every time, and nothing
    noticed — the unit is inactive between firings *by design*, so
    ``user_unit_inactive`` cannot judge it, and it never crashloops, so
    ``nomadnet_crashloop`` cannot either.

    Judgement per timer, from the root-readable ``USER_UNIT=`` journal:
    at least ``min_failures`` ``Failed with result`` events inside
    ``lookback``, the newest of them fresher than ``recency_s``, and **no
    successful run since that newest failure**. The last clause is what makes
    this an outcome detector rather than an error counter: a job that fails
    twice and then succeeds is a blip, not an outage, and stays silent.

    Honest self-guards — every one of these returns None rather than a
    healthy-looking answer:

    - no resolvable operator, or no timers enrolled → INERT;
    - timers wants-dir unreadable → indeterminate (never "no timers");
    - journal unobservable for a unit (``journalctl`` missing/timeout/rc≠0,1)
      → that unit is skipped as indeterminate and, if NOTHING was observable,
      the whole tick is indeterminate and the streak is held, never reset —
      a journalctl wedge must not read as "all timers healthy";
    - newest failure older than ``recency_s`` (already remediated) → INERT.

    2-tick debounce rides out a tick that lands mid-firing. Never raises into
    the tick.
    """
    try:
        import time
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_USER_TIMER_FAILING_STATE

        if user_home is None:
            if operator is None:
                try:
                    from utils.fleet_test_runner import _find_operator_user
                    operator = _find_operator_user()
                except Exception:
                    operator = None
            if operator is None:
                note_disposition("user_timer_unit_failing", "inert",
                                 reason="no resolvable operator user")
                return None
            uid, name = operator
            import pwd as _pwd
            try:
                user_home = _pwd.getpwuid(uid).pw_dir
            except KeyError:
                user_home = f"/home/{name}"

        timers = _enabled_user_timers(user_home)
        if timers is None:
            note_disposition("user_timer_unit_failing", "indeterminate",
                             reason="user timers.target.wants unreadable")
            return None
        if not timers:
            _save_streak(sp, 0)
            note_disposition("user_timer_unit_failing", "inert",
                             reason="no user timers enrolled")
            return None

        if ts_fn is None:
            def ts_fn(unit, pattern):
                return _journal_user_unit_ts(
                    unit, pattern, lookback, journalctl_path=journalctl_path)

        failing: List[dict] = []
        observed_any = False
        for timer, service in sorted(timers.items()):
            fails = ts_fn(service, _FAIL_PATTERN)
            oks = ts_fn(service, _OK_PATTERN)
            if fails is None or oks is None:
                # Unobservable for THIS unit — say nothing about it.
                continue
            observed_any = True
            if len(fails) < min_failures:
                continue
            newest_fail = max(fails)
            if (now - newest_fail) > recency_s:
                continue                      # already fixed / stale history
            if oks and max(oks) > newest_fail:
                continue                      # recovered after the failures
            failing.append({
                "timer": timer,
                "service": service,
                "failures": len(fails),
                "newest_age_s": round(now - newest_fail, 1),
            })

        if not observed_any:
            # Journal was unreadable for every enrolled timer. Hold the streak
            # — resetting here would let a journalctl wedge quietly clear a
            # real, ongoing outage.
            note_disposition("user_timer_unit_failing", "indeterminate",
                             reason="journal unobservable for all user timers")
            return None

        if not failing:
            _save_streak(sp, 0)
            note_disposition("user_timer_unit_failing", "clean")
            return None

        streak = min(_load_streak(sp) + 1, debounce_ticks)
        _save_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("user_timer_unit_failing", "indeterminate",
                             reason="failing-timer candidate under debounce")
            return None

        failing.sort(key=lambda d: d["service"])
        subj = (failing[0]["service"] if len(failing) == 1
                else f"{len(failing)} units")
        listed = ", ".join(
            f"{d['service']} ({d['failures']}x in {lookback})" for d in failing
        )
        return Signal(
            cls="user_timer_unit_failing",
            subject=subj,
            severity="degraded",
            detail=(
                f"timer-triggered user job(s) failing every firing: {listed}. "
                f"No successful run since the newest failure. These are "
                f"invisible to every other user-unit leg — a oneshot is "
                f"inactive between firings by design (user_unit_inactive "
                f"can't judge it) and it never crashloops "
                f"(nomadnet_crashloop can't either). Check: "
                f"`systemctl --user status <unit>` and "
                f"`journalctl --user -u <unit> -n 50`; a job whose inputs went "
                f"missing (config/peers file absent) exits nonzero every "
                f"cadence forever without paging anyone. "
                f"(The 2026-07-12→19 kiai lab_peers class: a week of silence.)"
            ),
            extra={"failing": failing, "lookback": lookback,
                   "min_failures": min_failures, "streak": streak},
        )
    except Exception:
        note_disposition("user_timer_unit_failing", "indeterminate",
                         reason="probe raised unexpectedly; unobservable this tick")
        return None
