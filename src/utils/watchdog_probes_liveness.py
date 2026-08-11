"""Watchdog probes — cron/fleet/host liveness & staleness failure shapes.

Split out of ``watchdog_probes_drift.py`` 2026-07-14 (that file had drifted to
2,598 lines vs the 1,500-line MF025 cap). Holds the cron-verdict-stale (#78),
fleet-box-unreachable, and host-frozen probes — the "is the box/cron alive"
family, distinct from the declared-vs-live *drift* probes that stay in
watchdog_probes_drift. Import via the ``utils.watchdog_probes`` hub, not from
here; watchdog_probes_drift also re-exports these for back-compat.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime as _datetime
from typing import Dict, List, Optional, Tuple

from utils import claw_battery
from utils.watchdog_probe_core import (
    CRON_SPOOL_PATHS as _CRON_SPOOL_PATHS,
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)

logger = logging.getLogger(__name__)


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

        # 4. Cross-reference — judge ONLY wired crons (orphans ignored).
        failed: List[str] = []
        stale: List[str] = []
        reboot_unjudged: List[str] = []
        unconfirmed: List[str] = []
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

        if not failed and not stale:
            if reboot_unjudged:
                note_disposition(
                    "cron_verdict_stale", "indeterminate",
                    reason="@reboot cron(s) with no verdict observed",
                )
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
        if stale:
            bits.append(f"{len(stale)} silent: " + ", ".join(stale[:5]))
        return Signal(
            cls="cron_verdict_stale",
            subject="cron",
            severity="degraded",
            detail=("Wired cron(s) unhealthy — " + "; ".join(bits)
                    + " (fix the job or re-run + re-verify; silence is the "
                    "failure mode)"),
            issue_ref=78,
            extra={"failed": failed, "stale": stale, "streak": streak,
                   "wired_count": len(wired)},
        )
    except Exception:
        note_disposition(
            "cron_verdict_stale", "indeterminate",
            reason="probe raised; observation failed",
        )
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: fleet box unreachable (2026-06-17, Leg D) — surface a fleet box
# the offline-monitor (fleet_offline_check.sh on the manager box) has confirmed
# DOWN into mini's brief + /fleet, so a dark box can't sit silent in a
# side-channel logfile (the .32 33h-dark lesson). The monitor's OWN death
# is covered by cron_verdict_stale (fleet_offline_check is verdict-wired).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_FLEET_UNREACHABLE_DEBOUNCE_PATH = (
    "/var/lib/meshforge/fleet_unreachable_debounce.json")
FLEET_STATE_STALE_S = 1800          # state file older than this → not current
FLEET_UNREACHABLE_WEDGE_S = 1800    # a box down longer than this → wedge severity


def _read_operator_fleet_state(home) -> Tuple[Optional[str], Optional[float]]:
    """Read ``~/fleet_offline_state.tsv`` + its mtime as root, in-process (no
    sudo — watchdog sandbox). Returns ``(text, mtime)`` or ``(None, None)`` on
    absent/unreadable (→ INERT: the monitor is manager-box-only)."""
    if not home:
        return None, None
    path = os.path.join(str(home), "fleet_offline_state.tsv")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text, os.path.getmtime(path)
    except (FileNotFoundError, IsADirectoryError):
        return None, None
    except OSError:
        # The file EXISTS but can't be read — UNOBSERVABLE, not the positive
        # "not the manager box" absence; the probe's later inert note must
        # not read as legitimate absence (worst-wins keeps this note).
        note_disposition(
            "fleet_box_unreachable", "indeterminate",
            reason="offline-monitor state unreadable/mid-rewrite",
        )
        return None, None


def probe_fleet_box_unreachable(
    *,
    operator: Optional[Tuple[int, str]] = None,
    state_text: Optional[str] = None,
    state_mtime: Optional[float] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    stale_after_s: float = FLEET_STATE_STALE_S,
    wedge_after_s: float = FLEET_UNREACHABLE_WEDGE_S,
) -> Optional[Signal]:
    """Surface fleet boxes the offline-monitor has confirmed DOWN, into the spine
    the operator actually watches (mini warm brief + /fleet) — Leg D, 2026-06-17.

    Reads the manager box's ``~/fleet_offline_state.tsv`` (written by the hardened
    ``fleet_offline_check.sh``) directly as root. A row with ``alerted==1`` is a
    box unreachable past the monitor's 3-fail (~15 min) threshold that is already
    being re-paged; this probe makes it VISIBLE in the brief/panel so it can't sit
    silent in a side-channel logfile (the ".32 dark 33h, found by manually poking
    it" gap). The monitor owns the ntfy page; this probe is visibility, not a
    second page — its seed rule is ``propose_escalation`` (no duplicate ntfy).

    Self-guards None: no state file (not the manager box → INERT — the monitor is
    manager-box-only), or the file is STALE past ``stale_after_s`` (the monitor
    itself stopped — reporting frozen down-rows as current would be the
    absence-of-evidence trap; ``cron_verdict_stale`` owns the dead-cron alert,
    since ``fleet_offline_check`` is verdict-wired). 2-tick debounce. Back-compat
    with the pre-Leg-D 3-field state rows. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_FLEET_UNREACHABLE_DEBOUNCE_PATH

        if state_text is None:
            if operator is None:
                try:
                    from utils.fleet_test_runner import _find_operator_user
                    operator = _find_operator_user()
                except Exception:
                    operator = None
            home = None
            if operator is not None:
                try:
                    import pwd
                    home = pwd.getpwuid(operator[0]).pw_dir
                except (KeyError, OSError):
                    home = None
            state_text, state_mtime = _read_operator_fleet_state(home)

        if not state_text:
            if state_text is None:
                # positively ABSENT file — not the manager box → INERT
                note_disposition(
                    "fleet_box_unreachable", "inert",
                    reason="no offline-monitor state file "
                           "(manager-box-only organ)",
                )
            else:
                # zero-byte read — the writer's mid-rewrite window; an
                # EMPTY file is not a positive "no monitor here"
                note_disposition(
                    "fleet_box_unreachable", "indeterminate",
                    reason="offline-monitor state unreadable/mid-rewrite",
                )
            _save_parity_streak(sp, 0)
            return None

        # Stale file = the monitor stopped updating; do NOT read frozen rows as
        # current (cron_verdict_stale owns the dead-monitor alert).
        if state_mtime is not None and (now - state_mtime) > stale_after_s:
            note_disposition(
                "fleet_box_unreachable", "indeterminate",
                reason="state file stale; cron_verdict_stale owns the dead-cron page",
            )
            _save_parity_streak(sp, 0)
            return None

        down: List[Tuple[str, float, int]] = []
        unobs: List[Tuple[str, float, int]] = []
        for line in state_text.splitlines():
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[0].strip():
                continue
            try:
                alerted = int(parts[2])
            except ValueError:
                continue
            if alerted != 1:
                continue
            try:
                down_since = float(parts[3]) if len(parts) > 3 and parts[3] else 0.0
            except ValueError:
                down_since = 0.0
            try:
                alert_count = int(parts[5]) if len(parts) > 5 and parts[5] else 0
            except ValueError:
                alert_count = 0
            # Field 7 is the monitor's VERDICT (script note 6): "down" = the box
            # is implicated, "unobservable" = only the path to it was observed
            # broken. Pre-2026-08-11 rows have no field 7 and meant "down".
            # Anything unrecognised is treated as unobservable — an unknown
            # verdict must not be laundered into the stronger claim.
            verdict = (parts[6].strip().lower()
                       if len(parts) > 6 and parts[6].strip() else "down")
            (down if verdict == "down" else unobs).append(
                (parts[0].strip(), down_since, alert_count))

        if not down and not unobs:
            note_disposition("fleet_box_unreachable", "clean")
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "fleet_box_unreachable", "indeterminate",
                reason="down box seen; held by 2-tick debounce",
            )
            return None

        max_down_min = 0
        sustained = False

        def _describe(rows: List[Tuple[str, float, int]],
                      escalates: bool) -> List[str]:
            nonlocal max_down_min, sustained
            out: List[str] = []
            for name, ds, ac in sorted(rows):
                if ds and now >= ds:
                    mins = int((now - ds) // 60)
                    max_down_min = max(max_down_min, mins)
                    if escalates and (now - ds) > wedge_after_s:
                        sustained = True
                    out.append(f"{name} (~{mins}m, page #{ac})" if ac
                               else f"{name} (~{mins}m)")
                else:
                    out.append(name)
            return out

        # Only a CONFIRMED-down box may drive the wedge severity. "This box has
        # been wedged for hours" is an assertion about the box, and on the
        # unobservable verdict we do not have one — the path is what broke.
        # Paging is unaffected: the shell monitor pages on its own cadence
        # either way, so honesty here costs no notification.
        parts_out: List[str] = []
        if down:
            parts_out.append("the offline-monitor confirms DOWN: "
                             + ", ".join(_describe(down, escalates=True)))
        if unobs:
            parts_out.append(
                "UNOBSERVABLE — only path down, box state UNKNOWN (not observed "
                "down): " + ", ".join(_describe(unobs, escalates=False)))
        return Signal(
            cls="fleet_box_unreachable",
            subject="fleet",
            severity="wedge" if sustained else "degraded",
            detail=("Fleet box(es) " + "; ".join(parts_out)
                    + " — surfaced here so a dark box can't sit silent (Leg D); "
                    "ntfy is re-paging on a cadence. Check the box."),
            issue_ref=None,
            extra={"down": [d[0] for d in sorted(down)],
                   "unobservable": [d[0] for d in sorted(unobs)],
                   "max_down_min": max_down_min, "streak": streak},
        )
    except Exception:
        note_disposition(
            "fleet_box_unreachable", "indeterminate",
            reason="probe raised; observation failed",
        )
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: host frozen (2026-06-17 Leg C — the dude-claw out-of-band witness)
#
# An ESP32 (dude-claw) on the watched box's OWN subnet runs a host_probe tool
# over NATS; an out-of-band collector cron on the claw's brain box polls it and
# writes a verdict file. This probe READS that file (no NATS in the sandboxed
# watchdog) and surfaces HOST_FROZEN / UNREACHABLE / (sustained) UNKNOWN into
# mini's brief + /fleet — exactly the swap-thrash freeze class the box's own
# self-petted HW watchdog can't catch. Mirrors fleet_box_unreachable's
# file-read pattern + 2-tick debounce. Alert-only (propose_escalation).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_HOST_FROZEN_DEBOUNCE_PATH = "/var/lib/meshforge/host_frozen_debounce.json"
HOST_PROBE_STATE_STALE_S = 900   # verdict file older than this → the collector
                                 # stopped; cron_verdict_stale owns the dead-cron
                                 # alert (host_probe_check is verdict-wired)

# verdicts that mean "the target is in trouble" (→ wedge) vs degraded visibility
_HOST_FROZEN_WEDGE_VERDICTS = ("HOST_FROZEN", "UNREACHABLE")


def _read_host_probe_verdict(home) -> Tuple[Optional[str], Optional[float]]:
    """Read ``~/host_probe_state.json`` + its mtime as root, in-process (no sudo
    — watchdog sandbox). Returns ``(text, mtime)`` or ``(None, None)`` on
    absent/unreadable (→ INERT: the collector runs only on the claw's brain box)."""
    if not home:
        return None, None
    path = os.path.join(str(home), "host_probe_state.json")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text, os.path.getmtime(path)
    except (FileNotFoundError, IsADirectoryError):
        return None, None
    except OSError:
        # EXISTS but unreadable — the witness verdict is UNOBSERVABLE, not
        # the positive "not the brain box" absence (worst-wins keeps this).
        note_disposition(
            "host_frozen", "indeterminate",
            reason="host-probe state unreadable — witness unobservable",
        )
        return None, None


def probe_host_frozen(
    *,
    operator: Optional[Tuple[int, str]] = None,
    state_text: Optional[str] = None,
    state_mtime: Optional[float] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    stale_after_s: float = HOST_PROBE_STATE_STALE_S,
) -> Optional[Signal]:
    """Surface a dude-claw out-of-band witness verdict (Leg C, 2026-06-17).

    Reads the brain box's ``~/host_probe_state.json`` (written by the
    out-of-band ``host_probe_check`` collector that polls the claw's
    ``host_probe`` tool over NATS). The claw sits on the watched box's own
    subnet, so it tells HOST_FROZEN (the IP stack answers but the app port
    serves no banner = kernel alive / userspace swap-wedged — the .32 class the
    box's self-petted HW watchdog can't catch) from UNREACHABLE (no TCP answer
    = host/path/SoC down). A sustained UNKNOWN (the claw witness itself couldn't
    be reached) surfaces as *degraded* — lost visibility is NOT "healthy"
    (honest_failure_modes #2), not silently swallowed.

    Self-guards None: no verdict file (not the brain box → INERT), STALE file
    (the collector stopped — cron_verdict_stale owns the dead-cron alert,
    host_probe_check is verdict-wired; reading a frozen verdict as current would
    be the absence-of-evidence trap), unparseable JSON (don't false-fire), or
    every target OK. 2-tick debounce. Alert-only (seed rule is
    propose_escalation — no ntfy). Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_HOST_FROZEN_DEBOUNCE_PATH

        if state_text is None:
            if operator is None:
                try:
                    from utils.fleet_test_runner import _find_operator_user
                    operator = _find_operator_user()
                except Exception:
                    operator = None
            home = None
            if operator is not None:
                try:
                    import pwd
                    home = pwd.getpwuid(operator[0]).pw_dir
                except (KeyError, OSError):
                    home = None
            state_text, state_mtime = _read_host_probe_verdict(home)

        if not state_text:
            note_disposition(
                "host_frozen", "inert",
                reason="no host-probe verdict file (brain-box-only organ)",
            )
            _save_parity_streak(sp, 0)      # no collector here → INERT
            return None

        # Stale file = the collector stopped; do NOT read a frozen verdict as
        # current (cron_verdict_stale owns the dead-collector alert).
        if state_mtime is not None and (now - state_mtime) > stale_after_s:
            note_disposition(
                "host_frozen", "indeterminate",
                reason="state file stale; cron_verdict_stale owns the dead-cron page",
            )
            _save_parity_streak(sp, 0)
            return None

        try:
            doc = json.loads(state_text)
            targets = doc.get("targets") or []
        except (ValueError, TypeError, AttributeError):
            note_disposition(
                "host_frozen", "indeterminate",
                reason="unparseable verdict file",
            )
            _save_parity_streak(sp, 0)      # garbage → don't false-fire
            return None

        # (name, verdict, raw, witness_device, failover)
        bad: List[Tuple[str, str, str, str, bool]] = []
        for t in targets:
            if not isinstance(t, dict):
                continue
            verdict = str(t.get("verdict") or "").upper()
            if not verdict or verdict == "OK":
                continue
            name = str(t.get("name") or t.get("host") or "?")
            raw = str(t.get("raw") or "")
            wdev = str(t.get("witness_device") or "")
            failover = bool(t.get("failover"))
            bad.append((name, verdict, raw, wdev, failover))

        if not bad:
            note_disposition("host_frozen", "clean")
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "host_frozen", "indeterminate",
                reason="bad verdict seen; held by 2-tick debounce",
            )
            return None

        wedge = any(v in _HOST_FROZEN_WEDGE_VERDICTS for _, v, _, _, _ in bad)

        def _desc(n, v, r, wdev, fo):
            tag = ""
            if wdev:
                tag = f" via {wdev}" + (" (failover)" if fo else "")
            return f"{n}: {v}{tag}" + (f" [{r}]" if r else "")
        descs = [_desc(*b) for b in sorted(bad)]
        names = sorted({n for n, _, _, _, _ in bad})
        return Signal(
            cls="host_frozen",
            subject=names[0] if len(names) == 1 else "claw-witness",
            severity="wedge" if wedge else "degraded",
            detail=("dude-claw out-of-band witness: "
                    + "; ".join(descs)
                    + " — HOST_FROZEN = kernel alive but userspace wedged "
                    "(the self-petted HW watchdog can't catch this); UNREACHABLE "
                    "= host/path down; UNKNOWN = claw witness itself unreachable "
                    "(lost visibility, incl. a failover claw with no route). "
                    "Alert-only; check the box."),
            issue_ref=None,
            extra={"targets": [{"name": n, "verdict": v, "witness_device": wdev,
                                "failover": fo}
                               for n, v, _, wdev, fo in sorted(bad)],
                   "streak": streak},
        )
    except Exception:
        note_disposition(
            "host_frozen", "indeterminate",
            reason="probe raised; observation failed",
        )
        return None


# ─────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────
# dude-claw EDGE HARDWARE: is the node answering, and is its pack dying?
#
# Born 2026-07-19 (structural-dark row 7). dudeclaw-02 — a battery-powered
# claw, the fleet's out-of-band LoRa/RF eyes — drained to 2.41 V and went dark
# for 17.4 h. The spine DID notice, but the only thing it could say was
# "cron_verdict_stale: claw02_metrics FAIL — fix the job", because the capture
# cron's exit code was the sole downstream witness. A dead radio node was
# laundered into an infrastructure-noise signal, in the one channel known to
# flap benignly. Meanwhile the `battery_v lt 3.5` sensor spec that would have
# caught it was bound to dudeclaw-01, which lives on USB at 4.06 V forever.
#
# These probes give the claw its OWN vocabulary, per device, from the tick
# files the capture cron already writes — no second NATS poll (one poller, one
# set of thresholds; honest_failure_modes #5) and no subprocess (MF021).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_CLAW_DARK_DEBOUNCE_PATH = "/var/lib/meshforge/claw_dark_debounce.json"
DEFAULT_CLAW_BATTERY_DEBOUNCE_PATH = "/var/lib/meshforge/claw_battery_debounce.json"
#: Capture cadence is */5; tolerate three misses before calling the FILE stale.
CLAW_TICK_STALE_S = 1200.0
#: LiPo working floor. Below this a single-cell pack is in its knee and the
#: node has hours, not days — the warning must land while it can still be acted
#: on. Physical constant of the chemistry, not an operator value (MF014).
#: IMPORTED, not redeclared: battery_soak judges the SAME pack against the same
#: chemistry, and two independent hardcodes of one physical constant drift
#: (honest_failure_modes #5). utils.claw_battery is the SSOT.
CLAW_BATTERY_FLOOR_V = claw_battery.FLOOR_V
_CLAW_TICK_GLOB = "claw_last_tick*.json"
#: A pack projected to reach cutoff within this many hours is worth saying out
#: loud even while it is still above the floor — the early warning the old
#: level-only threshold structurally could not give (a claw at 3.9 V losing
#: 100 mV/h is 5 h from dark, and read as perfectly healthy).
CLAW_BATTERY_EARLY_WARN_HR = 6.0
#: Where a battery soak declares its intent. A device named here is discharging
#: BECAUSE WE ASKED IT TO; the decline is the experiment, not a fault.
CLAW_SOAK_CONFIG_REL = os.path.join(".config", "meshforge", "battery_soak.json")
#: Series-append io_error witness — logged ONCE per series path per process
#: (honest_failure_modes #9): a silently frozen series serves a stale trend
#: as current. The live tick voltage is still valid, so the probe continues.
_SERIES_APPEND_IO_ERROR_WARNED: set = set()


def _soak_armed_devices(home: Optional[str]) -> dict:
    """``{device: cutoff_v}`` for devices whose discharge is deliberate.

    Intent is read ONLY from an explicit config NAMING a device. Unreadable /
    absent / malformed / unnamed → empty, i.e. NOTHING is treated as expected:
    a silence-manufacturing switch must fail toward paging, never away from it.
    (battery_soak defaults an unnamed device to dudeclaw-01; this deliberately
    does NOT — inferring which pack someone meant to discharge is exactly the
    guess that must not silence a page.)

    The soak's own ``cutoff_v`` rides along so the probe judges that pack
    against the SAME end-of-discharge the experiment is measuring to — one
    artifact, one constant (honest_failure_modes #5).
    """
    if not home:
        return {}
    try:
        with open(os.path.join(home, CLAW_SOAK_CONFIG_REL)) as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            return {}
        dev = cfg.get("claw_device")
        if not dev:
            return {}
        bat = cfg.get("battery")
        cut = bat.get("cutoff_v") if isinstance(bat, dict) else None
        if not isinstance(cut, (int, float)) or isinstance(cut, bool):
            cut = claw_battery.CUTOFF_V
        return {str(dev): float(cut)}
    except (OSError, ValueError, AttributeError):
        return {}


def _armed_cutoff(armed, device: str) -> float:
    """The cutoff to judge ``device`` against. Accepts either the mapping the
    config yields or a plain set of names (injected by callers/tests)."""
    if isinstance(armed, dict):
        cut = armed.get(device)
        if isinstance(cut, (int, float)) and not isinstance(cut, bool):
            return float(cut)
    return claw_battery.CUTOFF_V


def _claw_battery_view(tick: dict, *, state_dir: str, now: float,
                       soak_armed: bool,
                       cutoff_v: float = claw_battery.CUTOFF_V,
                       home: Optional[str] = None) -> dict:
    """Classify ONE claw's pack, recording this capture into its series first.

    The series is what turns a bare voltage into intelligence: the watchdog
    only ever sees the newest tick, so without a rolling record there is no
    trend to fit and — the case that matters most — no way to know what a
    pack was doing in the hours before its claw went dark.

    A soak-armed claw ALSO has the soak's own record, which is longer and
    denser (227 samples / 37.5 h on moc2 against this probe's 5 / 20 min), so
    both are merged — same pack, one history, deduped by timestamp. Reading the
    shorter of two records of one artifact is what made a claw that had plainly
    shed 570 mV report "trend unknown" (2026-07-24).
    """
    device = str(tick.get("device"))
    path = claw_battery.series_path(state_dir, device)
    bat = tick.get("battery")
    volts = bat.get("volts") if isinstance(bat, dict) else None
    ts = tick.get("captured_at")
    have_live = isinstance(volts, (int, float)) and not isinstance(volts, bool)
    live_ts = ts if (isinstance(ts, (int, float))
                     and not isinstance(ts, bool)) else now
    if have_live:
        rc = claw_battery.append_sample(path, live_ts, volts)
        if rc == "io_error" and path not in _SERIES_APPEND_IO_ERROR_WARNED:
            # Once per path per process: a frozen series serves a stale trend
            # as current (honest_failure_modes #9). The live tick voltage is
            # still valid, so the probe continues on it.
            _SERIES_APPEND_IO_ERROR_WARNED.add(path)
            logger.error(
                "claw battery series append failing at %s — trend will "
                "freeze; continuing on the live tick voltage", path)
    series = claw_battery.read_series(path)
    if soak_armed:
        # Same pack, one history. classify() sorts + dedups by timestamp, so an
        # overlapping reading from both sources counts once.
        series = series + claw_battery.read_soak_series(home, device)
    if have_live and series:
        # Frozen series (append failing): the stored curve stopped following
        # the pack. Judging trend/intent off it would let a stale declining
        # fit keep suppressing a REAL drain via soak "expected" — classify
        # the live reading alone, say the trend is unobservable, and never
        # let the frozen curve grant the suppression (2026-07-26 review C3).
        newest = max((float(r["ts"]) for r in series
                      if isinstance(r.get("ts"), (int, float))
                      and not isinstance(r.get("ts"), bool)), default=None)
        if newest is not None and (live_ts - newest) > CLAW_TICK_STALE_S:
            view = claw_battery.classify(
                [{"ts": live_ts, "volts": float(volts)}],
                soak_armed=soak_armed, cutoff_v=cutoff_v)
            view["expected"] = False
            view["series_stale"] = True
            view["summary"] += (
                " — series stale (newest stored reading "
                f"{(live_ts - newest) / 3600.0:.1f} h behind the live tick) — "
                "trend unobservable (append failing?)")
            view["device"] = device
            return view
    if have_live and not series:
        # Series unwritable (read-only /var/lib, full disk): fall back to the
        # single live reading so a level warning still works. The trend stays
        # None — degraded observability, not a fabricated flat pack.
        series = [{"ts": live_ts, "volts": volts}]
    view = claw_battery.classify(series, soak_armed=soak_armed, cutoff_v=cutoff_v)
    view["device"] = device
    return view


def _operator_home() -> Optional[str]:
    """Resolve the operator's home; the claw ticks live there, and the
    watchdog runs as root with sudo blocked (never escalate — read directly)."""
    try:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
    except Exception:
        return None
    if op is None:
        return None
    try:
        import pwd
        return pwd.getpwuid(op[0]).pw_dir
    except (KeyError, OSError):
        return None


def _read_claw_ticks(home: Optional[str], now: float) -> Tuple[List[dict], int]:
    """``(fresh_ticks, files_seen)`` for every claw tick file in ``home``.

    A file older than CLAW_TICK_STALE_S is DROPPED from the fresh list but
    still counted in ``files_seen``: a frozen tick must never be read as a
    current reading (absence of evidence is not evidence of absence), and
    cron_verdict_stale already owns the dead-capture-cron page. Unparseable
    files are likewise counted-but-dropped — a torn mid-write must not read as
    "no claws here"."""
    import glob as _glob
    if not home:
        return [], 0
    ticks: List[dict] = []
    paths = sorted(_glob.glob(os.path.join(home, _CLAW_TICK_GLOB)))
    for p in paths:
        try:
            mtime = os.path.getmtime(p)
            if (now - mtime) > CLAW_TICK_STALE_S:
                continue
            with open(p) as f:
                doc = json.load(f)
            if isinstance(doc, dict) and doc.get("device"):
                ticks.append(doc)
        except (OSError, ValueError):
            continue
    return ticks, len(paths)


def _tick_reachable(tick: dict) -> Optional[bool]:
    """Tri-state reachability for one tick.

    Prefers the explicit ``reachable`` field (written since 2026-07-19). Falls
    back to "did device_info parse" for ticks written by an older capture — and
    returns None when neither is decidable, so an unknown never reads as dark.
    """
    if isinstance(tick.get("reachable"), bool):
        return tick["reachable"]
    if "device_info" in tick:
        return tick.get("device_info") is not None
    return None


def probe_claw_device_dark(
    *,
    home: Optional[str] = None,
    ticks: Optional[List[dict]] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    soak_devices: Optional[set] = None,
) -> Optional[Signal]:
    """A claw edge node stopped answering while its capture kept running.

    THE distinction this exists to make: the capture cron is alive and writing
    fresh ticks, and those fresh ticks say the DEVICE did not reply. That is a
    hardware/RF/power fact about a node, and it is said in those words —
    not as a failing cron.

    SECOND distinction (2026-07-24): *why* it went quiet. The probe now reads
    the pack's rolling series, which outlives the device, and says whether it
    went dark with a flat battery or a healthy one — the difference between
    "the pack ran out" and "the node crashed", which the operator otherwise has
    to go and physically check. A claw that goes dark at 4.05 V is NOT a
    battery problem and must not be sent down that path.

    When the dark device is the ARMED battery soak and its pack had reached the
    knee/cutoff, the silence is the experiment ENDING, not a fault: it resolves
    to a clean disposition naming the last reading, and battery_soak's own
    verdict owns the result. Strictly gated — intent must be declared in a
    config naming that device, AND its last measured pack must actually have
    been low. A soak-armed claw that dies with a healthy pack still pages.

    Self-guards None: no tick files (no claw on this box → INERT), every tick
    stale or unparseable (indeterminate — cron_verdict_stale owns the dead-cron
    page), reachability undecidable, or every claw answering. 2-tick debounce
    rides out a single missed poll. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_CLAW_DARK_DEBOUNCE_PATH
        home_dir = home     # bound for BOTH paths: injected ticks reach the
                            # soak-series read below too, and an unbound name
                            # there would vanish into the probe's own except
        if ticks is None:
            home_dir = home or _operator_home()
            ticks, seen = _read_claw_ticks(home_dir, now)
            if soak_devices is None:
                soak_devices = _soak_armed_devices(home_dir)
        else:
            seen = len(ticks)
        armed = soak_devices or {}

        if not seen:
            note_disposition("claw_device_dark", "inert",
                             reason="no claw tick files (no claw edge node here)")
            _save_parity_streak(sp, 0)
            return None
        if not ticks:
            note_disposition(
                "claw_device_dark", "indeterminate",
                reason="claw tick file(s) stale/unparseable; cron_verdict_stale "
                       "owns the dead-capture page")
            _save_parity_streak(sp, 0)
            return None

        dark = [t for t in ticks if _tick_reachable(t) is False]
        undecidable = [t for t in ticks if _tick_reachable(t) is None]

        if not dark:
            reason = ("all claw devices answering"
                      if not undecidable
                      else "no claw reported dark; "
                           f"{len(undecidable)} tick(s) predate the reachable field")
            note_disposition("claw_device_dark", "clean", reason=reason)
            _save_parity_streak(sp, 0)
            return None

        # WHY did it go quiet? The pack series outlives the device, so the last
        # readings before the silence are still here to be read.
        state_dir = os.path.dirname(sp) or "."
        power_ctx = {}
        end_of_discharge = []
        for t in dark:
            dev = str(t.get("device"))
            hist = claw_battery.read_series(
                claw_battery.series_path(state_dir, dev))
            if dev in armed:
                hist = hist + claw_battery.read_soak_series(home_dir, dev)
            view = claw_battery.classify(
                hist, soak_armed=dev in armed,
                cutoff_v=_armed_cutoff(armed, dev))
            power_ctx[dev] = view
            if dev in armed and view["state"] in (claw_battery.CRITICAL,
                                                  claw_battery.KNEE):
                end_of_discharge.append((dev, view))
        # Only when EVERY dark claw is an expected end-of-discharge. One real
        # death alongside a finished soak still pages — the suppression may
        # never absorb a claw it wasn't asked about.
        if end_of_discharge and len(end_of_discharge) == len(dark):
            note_disposition(
                "claw_device_dark", "clean",
                reason=("expected end-of-discharge: "
                        + "; ".join(f"{d} last read {v['summary']}"
                                    for d, v in end_of_discharge)
                        + " — this device is the ARMED battery soak and its pack "
                          "reached the knee; battery_soak owns the verdict"))
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("claw_device_dark", "indeterminate",
                             reason=f"dark candidate, debounce {streak}/{debounce_ticks}")
            return None

        names = sorted(str(t.get("device")) for t in dark)
        detail_bits = []
        for t in sorted(dark, key=lambda d: str(d.get("device"))):
            dev = str(t.get("device"))
            err = "; ".join(f"{k}={v}" for k, v in (t.get("errors") or {}).items())
            detail_bits.append(f"{dev}" + (f" ({err[:110]})" if err else ""))
        # Point the operator at the RIGHT thing: a node that went quiet with a
        # flat pack is a battery; one that went quiet at 4.05 V is not, and
        # sending them down the battery path wastes the trip.
        verdicts = []
        for dev in names:
            view = power_ctx.get(dev) or {}
            st = view.get("state")
            if st in (claw_battery.CRITICAL, claw_battery.KNEE):
                verdicts.append(f"{dev}: pack was {view['summary']} — the "
                                f"BATTERY ran out; charge/swap and it returns")
            elif st in (claw_battery.FLOAT, claw_battery.CHARGING,
                        claw_battery.DISCHARGING):
                verdicts.append(f"{dev}: pack was {view['summary']} — NOT a flat "
                                f"battery; look at wifi, the USB feed, or the "
                                f"node itself")
            else:
                verdicts.append(f"{dev}: pack state unknown (no readings held) "
                                f"— cannot say whether power was the cause")
        return Signal(
            cls="claw_device_dark",
            subject=names[0] if len(names) == 1 else f"{len(names)} claws",
            severity="degraded",
            detail=(
                f"claw edge node not answering: {', '.join(detail_bits)}. The "
                f"capture cron IS running (tick fresh) — the DEVICE is silent. "
                f"Last known power state — {'; '.join(verdicts)}. This is the "
                f"hardware fact behind what would otherwise surface only as a "
                f"failing claw metrics cron."
            ),
            extra={"devices": names, "claws_seen": seen,
                   "power": {d: {"state": v.get("state"),
                                 "volts": v.get("volts"),
                                 "slope_v_per_hr": v.get("slope_v_per_hr")}
                             for d, v in power_ctx.items()}},
        )
    except Exception:
        note_disposition("claw_device_dark", "indeterminate",
                         reason="probe raised unexpectedly; unobservable this tick")
        return None


def probe_claw_battery_low(
    *,
    home: Optional[str] = None,
    ticks: Optional[List[dict]] = None,
    now: Optional[float] = None,
    floor_v: float = CLAW_BATTERY_FLOOR_V,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    soak_devices: Optional[set] = None,
) -> Optional[Signal]:
    """A claw's pack is losing charge nobody asked it to lose.

    The warning that was missing on 2026-07-10: dudeclaw-02 spent ~38 h under
    3.5 V before it died, and nothing said so. Fires only on a CONCRETE
    voltage from a REACHABLE device.

    LEVEL + TREND + INTENT (2026-07-24). The original probe compared one
    instantaneous voltage to one threshold, which cannot tell apart a pack we
    are deliberately running to cutoff from one that is failing — so the fleet's
    armed battery soak paged as degraded for the whole experiment, training the
    operator to ignore the one class that also carries a real death. Now:

    - a soak-armed device that is DECLINING is ``expected``: reported in the
      disposition with its projection, never paged. Intent is read only from a
      config that names the device, and only suppresses an actual decline — a
      soak-armed pack that is charging or flat is still described as such, and
      any pack on a box with no soak config still pages exactly as before.
    - a pack projected to hit cutoff within CLAW_BATTERY_EARLY_WARN_HR pages
      even while it is still above the floor. This is new reach: a claw at
      3.9 V shedding 100 mV/h is 5 h from dark and used to read as healthy
      until it had already fallen into the knee.
    - every page now carries slope + a LABELLED linear projection, so "charge
      or swap the pack" comes with how long there is to do it.

    ``soak_devices`` is injectable; when ticks are injected too (tests) the
    on-disk config is NOT consulted, so a test can never be steered by the real
    box's soak state. Production (ticks=None) reads the operator's config.

    Self-guards None: no tick files (INERT), stale/unparseable ticks, a device
    with no battery gauge or a capture that predates battery collection
    (unknown is NOT "charged" — it is simply not a low-battery claim), an
    unreachable device (claw_device_dark owns that), or every pack healthy.
    2-tick debounce. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_CLAW_BATTERY_DEBOUNCE_PATH
        home_dir = home
        if ticks is None:
            home_dir = home or _operator_home()
            ticks, seen = _read_claw_ticks(home_dir, now)
            if soak_devices is None:
                soak_devices = _soak_armed_devices(home_dir)
        else:
            seen = len(ticks)
        armed = soak_devices or {}

        if not seen:
            note_disposition("claw_battery_low", "inert",
                             reason="no claw tick files (no claw edge node here)")
            _save_parity_streak(sp, 0)
            return None
        if not ticks:
            note_disposition("claw_battery_low", "indeterminate",
                             reason="claw tick file(s) stale/unparseable")
            _save_parity_streak(sp, 0)
            return None

        state_dir = os.path.dirname(sp) or "."
        alarming, expected, measured = [], [], 0
        for t in ticks:
            if _tick_reachable(t) is False:
                continue                     # dark → claw_device_dark's call
            bat = t.get("battery")
            volts = bat.get("volts") if isinstance(bat, dict) else None
            if not isinstance(volts, (int, float)) or isinstance(volts, bool):
                continue                     # no gauge / pre-battery capture
            measured += 1
            dev = str(t.get("device"))
            view = _claw_battery_view(
                t, state_dir=state_dir, now=now, home=home_dir,
                soak_armed=dev in armed, cutoff_v=_armed_cutoff(armed, dev))
            if view["expected"]:
                expected.append(view)
                continue
            hrs = view["hours_to_cutoff"]
            if (volts < floor_v
                    or (hrs is not None and hrs <= CLAW_BATTERY_EARLY_WARN_HR)):
                alarming.append(view)

        if not measured:
            note_disposition(
                "claw_battery_low", "indeterminate",
                reason="no claw reported a battery voltage (no gauge, or the "
                       "capture predates battery collection) — unknown is not charged")
            _save_parity_streak(sp, 0)
            return None
        if not alarming:
            # An expected decline is NOT silence: it is stated, with its
            # projection, so a soak that is running away from its plan is still
            # visible to anyone reading dispositions.
            note = "; ".join(f"{v['device']} {v['summary']}" for v in expected)
            note_disposition(
                "claw_battery_low", "clean",
                reason=(f"{measured} claw pack(s) healthy or expected"
                        + (f" — {note}" if note else "")))
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("claw_battery_low", "indeterminate",
                             reason=f"low candidate, debounce {streak}/{debounce_ticks}")
            return None

        alarming.sort(key=lambda v: v["volts"])
        worst = alarming[0]
        listed = "; ".join(f"{v['device']}: {v['summary']}" for v in alarming)
        if worst["volts"] < floor_v:
            lead = (f"claw pack below {floor_v} V — {worst['device']} at "
                    f"{worst['volts']:.2f} V is in the knee")
        else:
            lead = (f"claw pack draining fast — {worst['device']} is above the "
                    f"{floor_v} V floor but projected to reach cutoff within "
                    f"{CLAW_BATTERY_EARLY_WARN_HR:.0f} h")
        expected_note = ("" if not expected else
                         " (not counted: " +
                         ", ".join(f"{v['device']} is the armed soak" for v in expected)
                         + ")")
        return Signal(
            cls="claw_battery_low",
            subject=worst["device"],
            severity="degraded",
            detail=(
                f"{lead}. {listed}. Hours, not days, before the node drops off "
                f"and its RF/witness coverage goes with it — charge or swap the "
                f"pack.{expected_note}"
            ),
            extra={"low": [{"device": v["device"], "volts": v["volts"],
                            "state": v["state"],
                            "slope_v_per_hr": v["slope_v_per_hr"],
                            "hours_to_cutoff": v["hours_to_cutoff"]}
                           for v in alarming],
                   "expected": [{"device": v["device"], "volts": v["volts"],
                                 "state": v["state"],
                                 "hours_to_cutoff": v["hours_to_cutoff"]}
                                for v in expected],
                   "floor_v": floor_v, "measured": measured},
        )
    except Exception:
        note_disposition("claw_battery_low", "indeterminate",
                         reason="probe raised unexpectedly; unobservable this tick")
        return None


# ─────────────────────────────────────────────────────────────────────
# claw LoRa EARS: the fleet's only over-the-air witness (row 9, 2026-07-19)
#
# Every other mesh-RF check in this fleet is a box talking about itself. The
# gateway's own self-report can read healthy — RNS round-trip canary green,
# service active, queue draining — while nothing actually leaves the antenna
# (deaf radio, wrong region/preset, dead PA, unplugged coax). That is the
# `mesh_rf_ota_leg_unwatched` blind spot.
#
# The claws answer `lora_stats` from a SEPARATE radio on SEPARATE silicon:
# "I heard N packets, the last one <age> seconds ago." That is independent
# physical-layer evidence no box can fabricate about itself. Silence across a
# claw that normally hears constantly means the CHANNEL went quiet — the one
# observation that distinguishes "we stopped transmitting" from "we think we
# transmitted".
#
# ⚠️ THRESHOLD IS PROVISIONAL. The operator staged this pending a heard-rate
# soak across overnight lulls, and that soak data did not exist until this
# capture shipped. So this probe ESCALATES ONLY — it must not page until the
# quiet-hours floor is measured, exactly the way calibration_drift soaked 34
# days before promotion. A pager on an unmeasured threshold is the "worked
# once" trap wearing an RF hat.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_CLAW_RF_DEBOUNCE_PATH = "/var/lib/meshforge/claw_rf_debounce.json"
#: Provisional quiet-window. Raise/lower only from measured heard-rate data.
CLAW_RF_SILENT_S = 1800.0


def probe_claw_rf_silent(
    *,
    home: Optional[str] = None,
    ticks: Optional[List[dict]] = None,
    now: Optional[float] = None,
    silent_after_s: float = CLAW_RF_SILENT_S,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """No LoRa traffic heard over the air by ANY claw for the quiet window.

    Fires only when EVERY claw reporting a LoRa reading is silent: one deaf
    claw is that claw's problem, but all of them going quiet at once is the
    channel. Requires a REACHABLE claw with a parsed reading — an unreachable
    device is claw_device_dark's call, and a claw whose firmware has no
    lora_stats is indeterminate, never "the air is quiet".

    Self-guards None: no tick files (INERT), stale/unparseable ticks, no claw
    reporting a LoRa reading, or any claw hearing traffic inside the window.
    2-tick debounce. Escalate-only until the heard-rate soak lands (see the
    module comment). Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_CLAW_RF_DEBOUNCE_PATH
        if ticks is None:
            ticks, seen = _read_claw_ticks(home or _operator_home(), now)
        else:
            seen = len(ticks)

        if not seen:
            note_disposition("claw_rf_silent", "inert",
                             reason="no claw tick files (no claw edge node here)")
            _save_parity_streak(sp, 0)
            return None
        if not ticks:
            note_disposition("claw_rf_silent", "indeterminate",
                             reason="claw tick file(s) stale/unparseable")
            _save_parity_streak(sp, 0)
            return None

        ages = []
        for t in ticks:
            if _tick_reachable(t) is False:
                continue                      # dark → claw_device_dark's call
            lora = t.get("lora")
            age = lora.get("heard_age_s") if isinstance(lora, dict) else None
            if isinstance(age, (int, float)) and not isinstance(age, bool):
                ages.append((str(t.get("device")), float(age), lora))

        if not ages:
            note_disposition(
                "claw_rf_silent", "indeterminate",
                reason="no reachable claw reported a LoRa reading (no ears, or "
                       "a capture predating lora_stats) — unknown is not silence")
            _save_parity_streak(sp, 0)
            return None

        hearing = [a for a in ages if a[1] <= silent_after_s]
        if hearing:
            youngest = min(a[1] for a in hearing)
            note_disposition(
                "claw_rf_silent", "clean",
                reason=f"{len(hearing)}/{len(ages)} claw(s) hearing traffic; "
                       f"newest packet {youngest:.0f}s old")
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("claw_rf_silent", "indeterminate",
                             reason=f"RF-silent candidate, debounce {streak}/{debounce_ticks}")
            return None

        ages.sort(key=lambda a: a[1])
        listed = ", ".join(f"{d} {age:.0f}s" for d, age, _ in ages)
        return Signal(
            cls="claw_rf_silent",
            subject=ages[0][0] if len(ages) == 1 else f"{len(ages)} claws",
            severity="degraded",
            detail=(
                f"no LoRa traffic heard over the air for >{silent_after_s:.0f}s "
                f"by any claw ({listed}). This is an INDEPENDENT receiver, not a "
                f"box talking about itself: the mesh-RF leg looks silent even if "
                f"gateway self-reports and the RNS canary read green. Check the "
                f"radio/antenna/region-preset before trusting any box's own "
                f"'sent' count. NOTE: the quiet-window threshold is provisional "
                f"pending the heard-rate soak — a genuine overnight lull can "
                f"reach this, which is why this escalates and does not page."
            ),
            extra={"claws": [{"device": d, "heard_age_s": a,
                              "heard_pkts": (l or {}).get("heard_pkts"),
                              "crc_err": (l or {}).get("crc_err")}
                             for d, a, l in ages],
                   "silent_after_s": silent_after_s,
                   "threshold_provisional": True},
        )
    except Exception:
        note_disposition("claw_rf_silent", "indeterminate",
                         reason="probe raised unexpectedly; unobservable this tick")
        return None
