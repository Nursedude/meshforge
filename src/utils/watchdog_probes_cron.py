"""Watchdog probes — the cron-verdict regime (Issue #78).

Split out of ``watchdog_probes_liveness.py`` 2026-09-14, the same way
``watchdog_probes_claw_uplink`` and ``watchdog_probes_claw_watch`` were before
it: that file sat at exactly the 1,500-line MF025 cap, so the CONCERN/FAIL band
could not be added to it without raising a limit that only ever shrinks.

What lives here is one coherent unit — the crontab/verdict-log cross-reference
and the cadence math behind ``cron_verdict_stale`` — and it now sits beside its
own twin, ``watchdog_probes_peer_cron``, which routes a spooled peer's failure
into the same class.

Import via the ``utils.watchdog_probes`` hub, not from here.
``watchdog_probes_liveness`` re-exports every name below for back-compat, so no
caller had to move with the file.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime as _datetime
from typing import Dict, List, Optional, Tuple

from utils.watchdog_probe_core import (
    CRON_SPOOL_PATHS as _CRON_SPOOL_PATHS,
    CRON_VERDICT_ORPHAN_ACKNOWLEDGED,
    Signal,
    classify_orphan_verdicts,
    cron_verdict_band,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)

_GLOBAL_NOTE_DISPOSITION = note_disposition  # probe_cron_verdict_stale shadows it


# Cron-verdict coverage (Issue #78) — a cron WIRED to cron_verdict.sh that
# reported FAIL/CONCERN or went silent past its schedule cadence. Cross-
# references the crontab so a stale ORPHAN verdict (a one-off verdict for a
# cron that no longer exists, e.g. the diag24h_watchdog line) never fires.
# Inert until crons are wired — the regime is opt-in.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_CRON_VERDICT_DEBOUNCE_PATH = "/var/lib/meshforge/cron_verdict_debounce.json"
CRON_VERDICT_STALE_FLOOR_S = 2 * 3600.0      # don't flag faster than this (anti-flap)
CRON_VERDICT_CADENCE_MULT = 3.0              # stale if age > MULT × expected interval
# A FAIL on a FAST cron must be seen TWICE before it counts (2026-07-26,
# signal-yield pass R1). Confirmation is measured in CRON RUNS, not watchdog
# ticks: the old `debounce_ticks=2` bounded ~60 s while the thing it had to
# ride out is one cron CYCLE, so a single transient failure held the signal
# for the whole gap until the next run — 50 fires, 30% of ALL fleet
# escalation volume, from a debounce in the wrong unit.
#
# Gated by cadence because the cost of waiting is one full cycle, and that
# is only acceptable when the cycle is short. Grounded in the live fleet's
# two real failures: `fleet_hosts_drift` (47 * * * *, hourly) failed once and
# self-healed on the next run — pure noise, now suppressed; `local_brain_eval`
# (25 3 * * 0, WEEKLY) is persistently failing — it fires on sight, because
# waiting a week to report a broken weekly job is absurd. @reboot resolves to
# inf and therefore also fires immediately (there is no next run to wait for).
CRON_VERDICT_CONFIRM_MAX_CADENCE_S = 3600.0
_CRON_VERDICT_FALLBACK_MAX_S = 26 * 3600.0   # unparseable schedule → panel's 26h
# Wired-cron extraction is owned by fleet_snapshot._verdict_names_in_command
# (one regex, one extractor — honest_failure_modes #5; imported in the probe
# below so this probe and the fleet-snapshot orphan filter can never drift).


def _prior_verdict_statuses(text: str) -> Dict[str, str]:
    """name -> the SECOND-newest verdict status for that cron.

    ``_parse_cron_verdicts`` collapses to the newest entry per name by
    contract, so the previous run is invisible through it. Confirmation for
    the fast-cron gate is counted in cron RUNS, which needs exactly one step
    of history — parsed here rather than by widening the shared helper, whose
    latest-per-name contract other consumers depend on.

    The log is append-only chronological, so the entry a name displaces IS
    its previous run. Garbage/short lines are skipped exactly as the shared
    parser skips them — including its ISO-timestamp check on the first token
    (a garbage line must not seed a prior FAIL and falsely confirm a first
    failure; 2026-07-26 review A6).
    """
    newest: Dict[str, str] = {}
    prior: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        try:
            _datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        name, status = parts[1], parts[2]
        if name in newest:
            prior[name] = newest[name]
        newest[name] = status
    return prior


def _cron_max_interval(schedule: str) -> float:
    """Coarse expected-max gap (seconds) for a 5-field cron schedule or
    ``@keyword``. Intentionally approximate — catch gross silence, not exact
    scheduling. Unparseable → the panel's 26h fallback. ``@reboot`` → inf
    (only runs at boot, never stale-checkable)."""
    if not isinstance(schedule, str):
        return _CRON_VERDICT_FALLBACK_MAX_S
    s = schedule.strip()
    kw = {
        "@hourly": 3600.0, "@daily": 86400.0, "@midnight": 86400.0,
        "@weekly": 604800.0, "@monthly": 2592000.0,
        "@yearly": 31536000.0, "@annually": 31536000.0,
        "@reboot": float("inf"),
    }
    if s in kw:
        return kw[s]
    fields = s.split()
    if len(fields) < 5:
        return _CRON_VERDICT_FALLBACK_MAX_S
    minute, hour, dom, mon, dow = fields[:5]
    mm = re.match(r'^\*/(\d+)$', minute)
    if mm:
        try:
            return max(60.0, int(mm.group(1)) * 60.0)
        except ValueError:
            return _CRON_VERDICT_FALLBACK_MAX_S
    if minute == "*":
        return 60.0
    # specific minute from here → at most hourly granularity
    if hour == "*":
        return 3600.0
    hm = re.match(r'^\*/(\d+)$', hour)
    if hm:
        try:
            return max(3600.0, int(hm.group(1)) * 3600.0)
        except ValueError:
            return _CRON_VERDICT_FALLBACK_MAX_S
    # specific minute + specific hour
    if dow != "*":
        return 604800.0    # weekly
    if dom != "*" or mon != "*":
        return 2592000.0   # monthly-ish
    return 86400.0         # daily


def _read_operator_crontab_spool(name: Optional[str]) -> Optional[str]:
    """Read the operator's crontab from the spool — as root, in-process, no
    sudo (the watchdog's NoNewPrivileges sandbox forbids privilege change).
    Debian path first, then RHEL-style. None on missing/unreadable."""
    if not name or name == "root":
        return None
    for path in (p.format(name) for p in _CRON_SPOOL_PATHS):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except (FileNotFoundError, IsADirectoryError):
            continue
        except OSError:
            # The spool EXISTS but can't be read — the wired set is
            # UNOBSERVABLE, not positively absent; the probe's later inert
            # note must not read as a clean "no wired crons" (fail-dark;
            # worst-wins keeps this note).
            note_disposition(
                "cron_verdict_stale", "indeterminate",
                reason="crontab spool unreadable — wired set unobservable",
            )
            return None
    return None


def _read_operator_verdicts_log(home: Optional[str]) -> Optional[str]:
    """Read ``~/cron_verdicts.log`` as root, in-process. None on absent/unreadable."""
    if not home:
        return None
    try:
        with open(os.path.join(str(home), "cron_verdicts.log"),
                  "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (FileNotFoundError, IsADirectoryError):
        return None
    except OSError:
        return None


def probe_cron_verdict_stale(
    *,
    operator: Optional[Tuple[int, str]] = None,
    crontab_text: Optional[str] = None,
    verdicts_text: Optional[str] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    disposition_sink: Optional[list] = None,
) -> Optional[Signal]:
    """Fire when a cron WIRED to cron_verdict.sh reported FAIL/CONCERN or went
    silent past its schedule cadence — "silence is the failure mode" for the
    cron-verdict regime (Issue #78).

    Reads the operator's crontab (spool) + ``~/cron_verdicts.log`` directly as
    root (no sudo — watchdog sandbox). Only WIRED crons (a ``cron_verdict.sh
    <name>`` in the crontab line) are judged, so a stale ORPHAN verdict for a
    cron that no longer exists never false-alarms. INERT (None) on any box with
    no wired crons — the regime is opt-in. 2-tick debounce rides a mid-run
    window where a fresh run hasn't recorded yet. Never raises into the tick.
    """
    # A LIST not a bool (None means BOTH inert and clean); shadowed not renamed
    # so MF027 sees it. Why: fleet_truth_collector.judge_spooled_schedules.
    if disposition_sink is None:
        note_disposition = _GLOBAL_NOTE_DISPOSITION
    else:
        def note_disposition(c, d, **k):   # noqa: F811 - deliberate shadow
            disposition_sink.append((c, d, k.get('reason')))
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_CRON_VERDICT_DEBOUNCE_PATH

        # 1. Resolve operator (root-safe) — only when nothing is injected.
        if operator is None and crontab_text is None and verdicts_text is None:
            try:
                from utils.fleet_test_runner import _find_operator_user
                operator = _find_operator_user()
            except Exception:
                operator = None
        op_name = operator[1] if operator else None

        # 2. Wired crontab → {name: schedule}. No wired cron → inert.
        if crontab_text is None:
            crontab_text = _read_operator_crontab_spool(op_name)
        wired: Dict[str, str] = {}
        if crontab_text:
            try:
                from utils.fleet_snapshot import (
                    _parse_crontab, _verdict_names_in_command)
                for job in _parse_crontab(crontab_text):
                    for name in _verdict_names_in_command(
                            job.get("command", "")):
                        wired[name] = job.get("schedule", "")
            except Exception:
                note_disposition(
                    "cron_verdict_stale", "indeterminate",
                    reason="crontab parse failed; wired set unknown",
                )
                wired = {}
        if not wired:
            note_disposition(
                "cron_verdict_stale", "inert",
                reason="no crons wired to cron_verdict.sh on this box",
            )
            _save_parity_streak(sp, 0)   # nothing to watch — clear + inert
            return None

        # 3. Verdict log → latest verdict per name.
        if verdicts_text is None and operator is not None:
            home = None
            try:
                import pwd
                home = pwd.getpwuid(operator[0]).pw_dir
            except (KeyError, OSError):
                home = None
            verdicts_text = _read_operator_verdicts_log(home)
        latest: Dict[str, dict] = {}
        previous: Dict[str, str] = _prior_verdict_statuses(verdicts_text or "")
        if verdicts_text:
            try:
                from utils.fleet_snapshot import _parse_cron_verdicts
                for v in _parse_cron_verdicts(verdicts_text, now):
                    latest[v["name"]] = v
            except Exception:
                note_disposition(
                    "cron_verdict_stale", "indeterminate",
                    reason="verdict log parse failed",
                )
                latest = {}
        # verdicts_text still None here = the log could NOT be read (home
        # unresolvable or OSError — a POSITIVE empty read is ""). With wired
        # crons present their verdicts are UNOBSERVABLE — never let this
        # tick reach the clean note (fail-dark; worst-wins protects).
        if verdicts_text is None:
            note_disposition(
                "cron_verdict_stale", "indeterminate",
                reason="verdict log unreadable — cron verdicts unobservable",
            )

        # 4. Cross-reference. HEALTH is judged for wired crons only: an orphan
        # is usually a parked cron's fossil, and judging it false-alarms forever
        # (#78). But an unwired name still writing FRESH verdicts is no fossil —
        # it is a live emitter nothing judges, reported as a WIRING gap (not as
        # its health). Same fresh/stale rule fleet_snapshot._read_cron_verdicts
        # already uses, on `stale` from the SHARED parser: one rule, two
        # consumers (honest_failure_modes #5). Why: see tests/test_cron_verdict_orphan.py
        failed: List[str] = []
        stale: List[str] = []
        reboot_unjudged: List[str] = []
        unconfirmed: List[str] = []
        unwired_failing, acknowledged = classify_orphan_verdicts(latest, wired)
        for name, schedule in sorted(wired.items()):
            v = latest.get(name)
            if v is not None and v.get("status", "").upper().startswith(
                    ("FAIL", "CONCERN")):
                # FAIL/CONCERN leg ONLY — the silence leg below is untouched.
                # It already gates on MULT x cadence, and post-2026-07-10 a
                # silent(never) page is REAL (#78's log-truncation defect was
                # fixed in d0254dae), so it must keep firing on sight.
                cadence = _cron_max_interval(schedule)
                if cadence <= CRON_VERDICT_CONFIRM_MAX_CADENCE_S:
                    prev = previous.get(name)
                    if prev is None or not prev.upper().startswith(
                            ("FAIL", "CONCERN")):
                        # First failure on a fast cron. The next run lands
                        # within the cadence and will either confirm it or
                        # clear it, so waiting costs at most one cycle.
                        # UNLESS that next run never came: the unconfirmed
                        # FAIL itself aging past the stale threshold means
                        # the cron died after failing — the silence leg owns
                        # it, not "unconfirmed" forever (2026-07-26, A2).
                        threshold = max(CRON_VERDICT_STALE_FLOOR_S,
                                        CRON_VERDICT_CADENCE_MULT * cadence)
                        if float(v.get("age_s", 0.0)) > threshold:
                            stale.append(
                                f"{name}({int(float(v['age_s']) // 3600)}h)")
                            continue
                        unconfirmed.append(f"{name}({v.get('status')})")
                        continue
                failed.append(f"{name}({v.get('status')})")
                continue
            max_age = _cron_max_interval(schedule)
            if max_age == float("inf"):
                # @reboot — not stale-checkable. WITH a verdict it can read
                # clean; with NO verdict it is unjudgeable, not clean.
                if v is None:
                    reboot_unjudged.append(name)
                continue
            threshold = max(CRON_VERDICT_STALE_FLOOR_S,
                            CRON_VERDICT_CADENCE_MULT * max_age)
            if v is None:
                stale.append(f"{name}(never)")
            elif float(v.get("age_s", 0.0)) > threshold:
                stale.append(f"{name}({int(float(v['age_s']) // 3600)}h)")

        if not failed and not stale and not unwired_failing:
            if reboot_unjudged:
                # PARTIAL coverage, not whole-class blindness (2026-08-13
                # Pri-1 review). This used to note `indeterminate` for the
                # whole class, which discarded the true negatives on every
                # cron that WAS judged — the opposite wrong collapse from the
                # user_timer probe's partial→clean. The honest claim is
                # `clean` about the judged subset with the shortfall in the
                # structured coverage field; mini's blind extractor seeds a
                # partial-blind condition from judged < enrolled, so the
                # @reboot crons' unobservability stays visible (the 07-19
                # concern that made this indeterminate) without painting the
                # judged crons' health as blindness. With NOTHING judged the
                # subset is empty and `clean` would be an affirmative claim
                # over no observation at all — that case keeps the 07-19
                # indeterminate.
                judged = len(wired) - len(reboot_unjudged)
                names = ", ".join(sorted(reboot_unjudged)[:3])
                if judged > 0:
                    note_disposition(
                        "cron_verdict_stale", "clean",
                        reason=("@reboot cron(s) with no verdict observed: "
                                + names),
                        coverage={"judged": judged, "enrolled": len(wired)})
                else:
                    note_disposition(
                        "cron_verdict_stale", "indeterminate",
                        reason=("@reboot cron(s) with no verdict observed: "
                                + names))
            elif unconfirmed:
                # A failure WAS observed; it is simply not confirmed yet. This
                # is emphatically NOT clean — mapping "seen once, awaiting the
                # next run" onto healthy is the exact defect class this whole
                # spine exists to prevent (honest_failure_modes #1/#2). The
                # signal is withheld; the observation is not.
                note_disposition(
                    "cron_verdict_stale", "indeterminate",
                    reason=("unconfirmed first failure on fast cron(s), "
                            "awaiting next run: " + ", ".join(unconfirmed)),
                )
            elif acknowledged:
                # Name WHOSE detector owns each: an unreadable acknowledgement
                # is just a mute button.
                note_disposition(
                    "cron_verdict_stale", "clean",
                    reason="unwired verdict(s) acknowledged elsewhere: " + "; ".join(
                        "%s -> %s" % (n, CRON_VERDICT_ORPHAN_ACKNOWLEDGED[n])
                        for n in acknowledged))
            else:
                note_disposition("cron_verdict_stale", "clean")
            # Do NOT clear the streak on an unconfirmed failure — the tick
            # produced no verdict either way, so prior state must hold.
            if not unconfirmed:
                _save_parity_streak(sp, 0)
            return None

        # 5. Debounce — first sighting silent, fire on the 2nd consecutive tick.
        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "cron_verdict_stale", "indeterminate",
                reason="unhealthy cron seen; held by 2-tick debounce",
            )
            return None

        bits = []
        if failed:
            bits.append(f"{len(failed)} failing: " + ", ".join(failed[:5]))
        if unwired_failing:
            bits.append(f"{len(unwired_failing)} failing UNWIRED (nothing else "
                        "would have told you): " + ", ".join(unwired_failing[:5]))
        if stale:
            bits.append(f"{len(stale)} silent: " + ", ".join(stale[:5]))
        # CONCERN vs FAIL: which band the LOUDNESS rules key off. Carried in
        # `extra` rather than in `severity` because severity is read by /fleet
        # and the worst-wins rollups, and a CONCERN finding is still a real
        # degraded observation there — it is only the PAGE it should not earn.
        band = cron_verdict_band(failed, stale, unwired_failing)
        return Signal(
            cls="cron_verdict_stale",
            subject="cron",
            severity="degraded",
            # Lead and tail follow the FINDING. Saying "Wired cron(s)
            # unhealthy — 5 writing verdicts nothing judges" contradicts
            # itself: those are precisely the UNwired ones, and "fix the job"
            # is wrong advice when the job is fine and the wiring is missing.
            # Caught by reading the live signal on 2026-09-06 — a misread
            # instrument is a bug report against the instrument.
            detail=("Cron(s) unhealthy — " + "; ".join(bits)
                    + " (fix the job or re-run + re-verify; silence is the "
                    "failure mode)"),
            issue_ref=78,
            extra={"failed": failed, "stale": stale,
                   "unwired_failing": unwired_failing,
                   "acknowledged": acknowledged, "streak": streak,
                   "wired_count": len(wired), "verdict_band": band},
        )
    except Exception:
        note_disposition(
            "cron_verdict_stale", "indeterminate",
            reason="probe raised; observation failed",
        )
        return None
