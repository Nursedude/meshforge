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
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)


# Cron-verdict coverage (Issue #78) — a cron WIRED to cron_verdict.sh that
# reported FAIL/CONCERN or went silent past its schedule cadence. Cross-
# references the crontab so a stale ORPHAN verdict (a one-off verdict for a
# cron that no longer exists, e.g. the diag24h_watchdog line) never fires.
# Inert until crons are wired — the regime is opt-in.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_CRON_VERDICT_DEBOUNCE_PATH = "/var/lib/meshforge/cron_verdict_debounce.json"
CRON_VERDICT_STALE_FLOOR_S = 2 * 3600.0      # don't flag faster than this (anti-flap)
CRON_VERDICT_CADENCE_MULT = 3.0              # stale if age > MULT × expected interval
_CRON_VERDICT_FALLBACK_MAX_S = 26 * 3600.0   # unparseable schedule → panel's 26h
# Wired-cron extraction is owned by fleet_snapshot._verdict_names_in_command
# (one regex, one extractor — honest_failure_modes #5; imported in the probe
# below so this probe and the fleet-snapshot orphan filter can never drift).


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
    for path in (f"/var/spool/cron/crontabs/{name}", f"/var/spool/cron/{name}"):
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
        for name, schedule in sorted(wired.items()):
            v = latest.get(name)
            if v is not None and v.get("status", "").upper().startswith(
                    ("FAIL", "CONCERN")):
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
            else:
                note_disposition("cron_verdict_stale", "clean")
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
            down.append((parts[0].strip(), down_since, alert_count))

        if not down:
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

        descs: List[str] = []
        max_down_min = 0
        sustained = False
        for name, ds, ac in sorted(down):
            if ds and now >= ds:
                mins = int((now - ds) // 60)
                max_down_min = max(max_down_min, mins)
                if (now - ds) > wedge_after_s:
                    sustained = True
                descs.append(f"{name} (~{mins}m, page #{ac})" if ac
                             else f"{name} (~{mins}m)")
            else:
                descs.append(name)
        return Signal(
            cls="fleet_box_unreachable",
            subject="fleet",
            severity="wedge" if sustained else "degraded",
            detail=("Fleet box(es) the offline-monitor confirms DOWN: "
                    + ", ".join(descs)
                    + " — surfaced here so a dark box can't sit silent (Leg D); "
                    "ntfy is re-paging on a cadence. Check the box."),
            issue_ref=None,
            extra={"down": [d[0] for d in sorted(down)],
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

        bad: List[Tuple[str, str, str]] = []   # (name, verdict, raw)
        for t in targets:
            if not isinstance(t, dict):
                continue
            verdict = str(t.get("verdict") or "").upper()
            if not verdict or verdict == "OK":
                continue
            name = str(t.get("name") or t.get("host") or "?")
            raw = str(t.get("raw") or "")
            bad.append((name, verdict, raw))

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

        wedge = any(v in _HOST_FROZEN_WEDGE_VERDICTS for _, v, _ in bad)
        descs = [f"{n}: {v}" + (f" [{r}]" if r else "") for n, v, r in sorted(bad)]
        names = sorted({n for n, _, _ in bad})
        return Signal(
            cls="host_frozen",
            subject=names[0] if len(names) == 1 else "claw-witness",
            severity="wedge" if wedge else "degraded",
            detail=("dude-claw out-of-band witness: "
                    + "; ".join(descs)
                    + " — HOST_FROZEN = kernel alive but userspace wedged "
                    "(the self-petted HW watchdog can't catch this); UNREACHABLE "
                    "= host/path down; UNKNOWN = claw witness itself unreachable "
                    "(lost visibility). Alert-only; check the box."),
            issue_ref=None,
            extra={"targets": [{"name": n, "verdict": v} for n, v, _ in sorted(bad)],
                   "streak": streak},
        )
    except Exception:
        note_disposition(
            "host_frozen", "indeterminate",
            reason="probe raised; observation failed",
        )
        return None


# ─────────────────────────────────────────────────────────────────────
