"""Watchdog probes — TIMER-triggered user-unit failure shapes.

Narrows the user-unit blindness class. The window is **cadence-aware since
2026-08-13**: each timer's lookback and recency gate are sized from its own
schedule (``utils.timer_cadence``, which asks ``systemd-analyze``), so a daily
or weekly job is judged over enough of its own firings instead of against a
flat 3h window it could never fill.

⚠️ RESIDUAL, NARROWED, NOT GONE — it moved from the window to the JOURNAL.
The old residual (2026-07-21 review, W4) was that a timer with cadence ≳90 min
could fail every firing inside a 3h window and never trip ``min_failures=2``;
that is closed. What remains is that a cadence-sized window can outrun the
journal that has to fill it: measured 2026-08-13, fleet journal horizons run
28h–102h (and ~10 min on meshanchor-server, whose map service was writing a
full traceback per client disconnect). A daily timer needs ~60h. So the probe
now judges a timer ONLY when it actually witnessed ``min_failures`` firing
OUTCOMES in the window, and reports the rest as unjudged with the count — an
honest "I could not see enough" instead of the affirmative ``clean`` that
counting un-witnessed units used to produce. Where a journal is too short for
a slow timer, ``cron_verdict`` (Issue #78) is still the durable owner.

The existing detectors cover everything else:

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

from utils.timer_cadence import timer_cadences
from utils.user_units import enabled_user_timers, timer_wants_dirs
from utils.watchdog_probe_core import (
    Signal,
    _journal_user_unit_has_lines,
    note_disposition,
)

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

# FLOOR for the window we read the journal over, and the whole window for a
# timer whose cadence cannot be determined. Must comfortably span several
# firings of a fast timer so "every recent firing failed" is a real judgement
# and not a single sample.
USER_TIMER_FAILING_LOOKBACK = "3h"
USER_TIMER_FAILING_LOOKBACK_S = 3 * 3600.0
# A timer's own window is this many of its cadences. 2.5 guarantees room for
# 2–3 firings, so ``min_failures=2`` is reachable for ANY cadence — which is
# precisely what the flat 3h floor could not do for a daily job (it allowed
# zero). Sized from the schedule, never from the state dir a single manual run
# creates forever (the synth_soak proxy defect, 2026-08-12).
USER_TIMER_FAILING_LOOKBACK_CADENCE_MULT = 2.5
# Ceiling, so a monthly/yearly timer cannot ask journalctl for a window no
# journal on this fleet could hold. Measured 2026-08-13: query cost is FLAT in
# window width (~10 ms at 3h and at 30d — the ``USER_UNIT=`` field match is
# indexed), so this bounds absurdity, not cost. Beyond it the outcome gate
# below reports the timer unjudged rather than clean.
USER_TIMER_FAILING_MAX_LOOKBACK_S = 30 * 86400.0
# Consecutive-ish failures required inside the window. 2 keeps a one-off blip
# (a transient RNS wedge during a deploy) from paging, while a genuinely
# broken timer job clears it within two cadences.
USER_TIMER_FAILING_MIN_FAILURES = 2
# The newest failure must be at least this fresh. Without it, a job fixed an
# hour ago would keep paging off its own history until the window rolled — the
# same post-fix false-page trap nomadnet_crashloop's recency gate exists for.
# FLOOR only; like the lookback it scales with cadence, because "fresh" for a
# daily job is not one hour. Un-scaled, this gate ALONE re-created the old
# blindness: meshanchor-map-restart.timer's daily job had last fired 19h
# earlier, so even a correctly widened window would have discarded its newest
# failure as stale history.
USER_TIMER_FAILING_RECENCY_S = 3600.0
# 1.5 cadences: the failure must be on (or one jittered firing after) the most
# recent scheduled run. Deliberately under the 2.5 lookback — the window is for
# COUNTING failures, this gate is for asserting the job is still broken NOW.
USER_TIMER_FAILING_RECENCY_CADENCE_MULT = 1.5

_FAIL_PATTERN = "Failed with result"
_OK_PATTERN = "Finished "


_LOOKBACK_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def _lookback_seconds(spec: str) -> float:
    """The flat ``lookback`` string in seconds — the FLOOR the cadence math
    raises from.

    Deliberately tiny: this parses only the ``<int><s|m|h|d>`` form this
    module's own constant and its callers use, and anything else falls back to
    the module default rather than inventing a number. A wrong floor here
    would silently resize every timer's window, so the failure mode is "the
    documented default", never "whatever the regex happened to match".
    """
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", spec or "")
    if not m:
        return USER_TIMER_FAILING_LOOKBACK_S
    return int(m.group(1)) * _LOOKBACK_UNITS[m.group(2)]


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


# Promoted to utils.user_units (2026-08-09) so provision_role + the soak probes
# read enrollment through ONE implementation (hfm #5).
#
# ⚠️ This widened what THIS probe can see, and that was a bug fix, not a side
# effect: enablement is a symlink under ANY ``*.target.wants``, so a timer
# enabled into e.g. ``default.target.wants`` used to be invisible here — its
# oneshot could have failed on every firing forever, which is precisely the
# 2026-07-19 kiai hole this probe exists to close, one directory over. Found on
# meshanchor-server: ``meshanchor-map-restart.timer`` declares
# ``WantedBy=timers.target`` but is linked from ``default.target.wants``.
# Fleet effect measured the same day: exactly one newly-visible timer, on one
# box, and its job was already succeeding — coverage gained, no alarm uncorked.
_enabled_user_timers = enabled_user_timers


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
    coverage_fn=None,
    cadences: Optional[Dict[str, Optional[float]]] = None,
    now: Optional[float] = None,
) -> Optional[Signal]:
    """Fire when an enabled USER **timer's** job keeps failing every firing.

    The 2026-07-12→19 kiai class: ``meshforge-tracer.timer`` fired on cadence
    for a week while its oneshot service exited 2 every time, and nothing
    noticed — the unit is inactive between firings *by design*, so
    ``user_unit_inactive`` cannot judge it, and it never crashloops, so
    ``nomadnet_crashloop`` cannot either.

    Judgement per timer, from the root-readable ``USER_UNIT=`` journal:
    at least ``min_failures`` ``Failed with result`` events inside that
    timer's own window, the newest of them fresher than its own recency gate,
    and **no successful run since that newest failure**. The last clause is
    what makes this an outcome detector rather than an error counter: a job
    that fails twice and then succeeds is a blip, not an outage, and stays
    silent.

    **Each timer gets its OWN window** (2026-08-13), sized
    ``LOOKBACK_CADENCE_MULT × cadence`` with the flat ``lookback`` as a floor
    and ``MAX_LOOKBACK_S`` as a ceiling; ``recency_s`` scales the same way.
    ``cadences`` maps timer unit name → seconds between firings (``None`` for
    a timer with no derivable schedule, which keeps the flat floor — exactly
    the pre-2026-08-13 behaviour, so a box without ``systemd-analyze`` loses
    the new coverage rather than gaining a wrong window). Injectable because a
    test must not depend on the ambient box's systemd.

    Honest self-guards — every one of these returns None rather than a
    healthy-looking answer:

    - no resolvable operator, or no timers enrolled → INERT;
    - timers wants-dir unreadable → indeterminate (never "no timers");
    - journal unobservable for a unit (``journalctl`` missing/timeout/rc≠0,1)
      → that unit is skipped as indeterminate and, if NOTHING was observable,
      the whole tick is indeterminate and the streak is held, never reset —
      a journalctl wedge must not read as "all timers healthy";
    - **fewer than ``min_failures`` firing OUTCOMES witnessed in the window**
      → that timer is unjudged, and says so. A window is a REQUEST; the
      journal decides what it returns. Claiming "no failing job" about a
      daily timer whose journal only reaches back 10 minutes is the
      affirmative-clean-over-nothing defect that produced this leg
      (calibrated_claims: a label may claim only what its evidence covers);
    - newest failure older than the recency gate (already remediated) → INERT.

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
                             reason="user-unit enrollment dirs unreadable")
            return None
        if not timers:
            _save_streak(sp, 0)
            note_disposition("user_timer_unit_failing", "inert",
                             reason="no user timers enrolled")
            return None

        if ts_fn is None:
            def ts_fn(unit, pattern, window=lookback):
                return _journal_user_unit_ts(
                    unit, pattern, window, journalctl_path=journalctl_path)
        if coverage_fn is None:
            def coverage_fn(unit, window=lookback):
                return _journal_user_unit_has_lines(
                    unit, window, journalctl_path=journalctl_path)

        base_lookback_s = _lookback_seconds(lookback)
        if cadences is None:
            dirs = timer_wants_dirs(user_home)
            # None here is unobservable, but enabled_user_timers already
            # returned above in that case; an empty list just means no
            # cadence is derivable and every timer keeps the flat floor.
            cadences = timer_cadences(dirs or [])

        failing: List[dict] = []
        unjudged: List[str] = []
        observed_any = False
        observed_count = 0
        for timer, service in sorted(timers.items()):
            cadence = cadences.get(timer)
            # A window is a REQUEST, sized from the schedule. What the journal
            # actually returns is checked below — never assumed from this.
            if cadence and cadence > 0:
                window_s = min(
                    USER_TIMER_FAILING_MAX_LOOKBACK_S,
                    max(base_lookback_s,
                        USER_TIMER_FAILING_LOOKBACK_CADENCE_MULT * cadence))
                unit_recency_s = max(
                    recency_s,
                    USER_TIMER_FAILING_RECENCY_CADENCE_MULT * cadence)
            else:
                window_s = base_lookback_s
                unit_recency_s = recency_s
            window = (lookback if window_s == base_lookback_s
                      else f"{int(window_s)}s")

            fails = ts_fn(service, _FAIL_PATTERN, window)
            oks = ts_fn(service, _OK_PATTERN, window)
            if fails is None or oks is None:
                # Unobservable for THIS unit — say nothing about it.
                continue

            # THE COVERAGE GATE. `fails` and `oks` are the only firing
            # outcomes systemd records, so their count IS how many firings
            # this window actually witnessed. Fewer than the failure
            # threshold and no verdict is reachable: "0 failures" would be a
            # statement about an empty observation, which is how a DAILY
            # timer got folded into an affirmative `clean` on
            # meshanchor-server (2026-08-13) — and, once windows are
            # cadence-sized, how a journal too short to fill one would do it
            # again on every slow timer at once.
            outcomes = len(fails) + len(oks)
            if outcomes < min_failures:
                why = f"{outcomes} firing outcome(s) in {window}"
                if outcomes == 0 and coverage_fn(service, window) is not True:
                    # Cheap only because we are already in the rare path:
                    # separates a DEAD channel from a live one that simply
                    # has not completed enough firings yet.
                    why = f"no journal lines at all in {window}"
                unjudged.append(f"{service} ({why})")
                continue

            observed_any = True
            observed_count += 1
            if len(fails) < min_failures:
                continue
            newest_fail = max(fails)
            if (now - newest_fail) > unit_recency_s:
                continue                      # already fixed / stale history
            if oks and max(oks) > newest_fail:
                continue                      # recovered after the failures
            failing.append({
                "timer": timer,
                "service": service,
                "failures": len(fails),
                "newest_age_s": round(now - newest_fail, 1),
                "window": window,
                "cadence_s": round(cadence, 1) if cadence else None,
            })

        if not observed_any:
            # Nothing was judgeable: the journal was unreadable for every
            # enrolled timer, or it could not cover any of their cadences.
            # Hold the streak — resetting here would let a journalctl wedge,
            # or a journal too short for the fleet's slow timers, quietly
            # clear a real ongoing outage.
            note_disposition(
                "user_timer_unit_failing", "indeterminate",
                reason=("no user timer judgeable: "
                        + (", ".join(sorted(unjudged)[:3])
                           if unjudged
                           else "journal unobservable for all user timers")))
            return None

        if not failing:
            _save_streak(sp, 0)
            # Say how many were actually JUDGED, not how many are enrolled,
            # and NAME the ones that were not — a label may claim only what
            # its evidence covers (2026-08-13). Without the names this reads
            # as a tidy fraction and the operator cannot tell a box that is
            # fine from a box whose journal is too short to say so.
            note_disposition(
                "user_timer_unit_failing", "clean",
                reason=(f"{observed_count} of {len(timers)} enrolled timer(s) "
                        f"judged; no failing job. Unjudged: "
                        + "; ".join(sorted(unjudged)[:3]))
                if unjudged else None)
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
            f"{d['service']} ({d['failures']}x in {d['window']})"
            for d in failing
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
                   "min_failures": min_failures, "streak": streak,
                   "unjudged": unjudged},
        )
    except Exception:
        note_disposition("user_timer_unit_failing", "indeterminate",
                         reason="probe raised unexpectedly; unobservable this tick")
        return None
