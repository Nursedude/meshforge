"""Watchdog probes — environment & host-facing failure shapes.

Split out of ``watchdog_probes_drift.py`` 2026-07-14 (MF025 size cap). Holds the
router-scout, ntfy loopback/ack, kernel-reboot-pending, AREDN-source-dark, and
inherited-app-drift probes. Import via the ``utils.watchdog_probes`` hub, not
from here; watchdog_probes_drift also re-exports these for back-compat.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Dict, List, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)


# Probe: router scout degraded (2026-07-11 OpenWrt-router arc)
#
# A router-class fleet member (OpenWrt One, future MikroTik/AREDN boxes)
# runs the meshforge-scout agent (templates/openwrt/); the manager box's
# router_scout_pull.sh cron mirrors each tick to
# ~/.local/share/meshforge/router_scout/<device>_tick.json. This probe
# READS those mirrors (no ssh in the sandboxed watchdog). It is
# DEFENSE-IN-DEPTH behind the pull's own eval (which also FAILs
# cron_verdict on a stale/ok=false tick): its added value is surfacing
# the per-device verdict into the watchdog spine (/fleet + mini brief,
# device as subject), and covering ticks that reach the mirror by any
# path other than the verdict-wired pull. Fires on: fresh mirror + stale
# captured_at (agent cron dark on the router while the mirror keeps
# being re-copied), tick ok=false, or an unparseable mirror (the pull
# validates before writing, so garbage = writer/shape drift). Mirrors
# host_frozen's file-read pattern + 2-tick debounce. degraded only —
# every observed condition is REMOTE (tracer_peer_unreachable lesson).
# Alert-only (propose_escalation).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_ROUTER_SCOUT_DEBOUNCE_PATH = "/var/lib/meshforge/router_scout_debounce.json"
ROUTER_SCOUT_MIRROR_SUBDIR = ".local/share/meshforge/router_scout"
ROUTER_SCOUT_MIRROR_STALE_S = 5400   # 3 × the pull cron's 30-min cadence —
                                     # older = the pull stopped; skip the file
                                     # (cron_verdict_stale owns the dead cron)
ROUTER_SCOUT_TICK_STALE_S = 2700     # 3 × the agent's */15 router cadence —
                                     # a fresh mirror whose captured_at was
                                     # older than this AT THE LAST PULL
                                     # (mirror mtime) = agent dark on-router


def _read_router_scout_ticks(
    home, *, skipped: Optional[List[str]] = None,
) -> Optional[List[Tuple[str, str, float]]]:
    """Every mirrored tick as ``(filename, text, mtime)``, read as root
    in-process (no sudo — watchdog sandbox). None ONLY when the mirror dir
    is positively ABSENT or ``home`` is unset (→ INERT: the pull runs only
    on the manager box). A dir that EXISTS but can't be listed returns ``[]``
    with the dir appended to ``skipped``; an unreadable individual file is
    skipped with its name appended to ``skipped`` — unobservable ≠ absent."""
    if not home:
        return None
    d = os.path.join(str(home), ROUTER_SCOUT_MIRROR_SUBDIR)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith("_tick.json"))
    except (FileNotFoundError, NotADirectoryError):
        return None    # positive absence — legitimately inert
    except OSError:
        if skipped is not None:
            skipped.append(d)
        return []      # dir present but unlistable — unobservable, not absent
    out: List[Tuple[str, str, float]] = []
    for n in names:
        p = os.path.join(d, n)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            out.append((n, text, os.path.getmtime(p)))
        except OSError:
            # a vanished/unreadable single file — counted, never a silent
            # drop (that router would otherwise read clean-by-omission)
            if skipped is not None:
                skipped.append(n)
            continue
    return out


def probe_router_scout_degraded(
    *,
    operator: Optional[Tuple[int, str]] = None,
    ticks: Optional[List[Tuple[str, str, float]]] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    mirror_stale_s: float = ROUTER_SCOUT_MIRROR_STALE_S,
    tick_stale_s: float = ROUTER_SCOUT_TICK_STALE_S,
) -> Optional[Signal]:
    """Surface a degraded router-side meshforge-scout agent (2026-07-11).

    Reads the manager box's mirrored scout ticks (written by the
    verdict-wired ``router_scout_pull.sh``). Defense-in-depth: the pull's
    own eval also FAILs cron_verdict on these conditions — this probe's
    added value is the watchdog-spine surface (per-device subject into
    /fleet + mini) and coverage of mirrors landed by any path other than
    the pull. Fires ``degraded`` (never wedge — remote conditions) when,
    for any FRESH mirror:

      * the tick's ``captured_at`` was older than ``tick_stale_s`` at the
        mirror's mtime (the last pull — never measured against ``now``:
        time since the last pull is unobserved, and a reboot that eats a
        pull slot must not read as agent death) — the agent cron died on
        the router while the pull keeps re-copying the same old tick (a
        fresh mtime hides the corpse from mtime-only consumers);
      * the tick self-reports ``ok=false`` (its ``errors[]`` are the
        agent's own tri-state witnesses: tmpfs data_dir, unreadable
        /proc, dead radio TCP, ...);
      * the tick is unparseable (the pull validates JSON before its
        atomic write, so garbage here is writer/shape drift, not a torn
        read).

    Self-guards None: no mirror dir (not the manager box → INERT), and a
    STALE mirror file (mtime past ``mirror_stale_s``) is *skipped* — the
    pull cron stopped and ``cron_verdict_stale`` owns that alert; reading
    a frozen mirror as current would be the absence-of-evidence trap.
    2-tick debounce; observed-clean resets. Alert-only. Never raises into
    the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_ROUTER_SCOUT_DEBOUNCE_PATH

        skipped_files: List[str] = []
        if ticks is None:
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
            ticks = _read_router_scout_ticks(home, skipped=skipped_files)

        if not ticks:
            if ticks is None:
                # dir positively ABSENT — not the manager box → INERT
                note_disposition(
                    "router_scout_degraded", "inert",
                    reason="no scout mirror dir (manager-box-only organ)",
                )
            else:
                # dir EXISTS but yielded zero readable ticks (empty, or
                # every file/listing unreadable) — unobservable ≠ absent
                note_disposition(
                    "router_scout_degraded", "indeterminate",
                    reason="mirror dir present but no readable ticks",
                )
            _save_parity_streak(sp, 0)
            return None

        bad: List[Tuple[str, str]] = []     # (device-or-filename, why)
        for name, text, mtime in ticks:
            if mtime is not None and (now - mtime) > mirror_stale_s:
                note_disposition(
                    "router_scout_degraded", "indeterminate",
                    reason="mirror stale; cron_verdict_stale owns the dead-cron page",
                )
                continue    # dead pull cron — cron_verdict_stale's beat
            try:
                tick = json.loads(text)
            except (ValueError, TypeError):
                bad.append((name, "unparseable mirrored tick — "
                                  "writer/shape drift"))
                continue
            if not isinstance(tick, dict):
                bad.append((name, "mirrored tick is not an object"))
                continue
            device = str(tick.get("device") or name)
            cap = tick.get("captured_at")
            if isinstance(cap, bool) or not isinstance(cap, (int, float)):
                bad.append((device, "tick has no captured_at"))
                continue
            # Age at the LAST OBSERVATION (mirror mtime = when the pull
            # copied the router's tick), never at `now`: time since the
            # last pull is unobserved, not evidence of agent death — a
            # reboot that eats one pull slot must not read as "agent
            # dark" (2026-07-31 manager-box false positive). A genuinely
            # dead agent still fires: every later pull re-copies the
            # same tick, so this delta grows past the bar within one
            # pull cycle.
            observed_at = float(mtime) if mtime is not None else now
            age = observed_at - float(cap)
            if age > tick_stale_s:
                bad.append((device,
                            f"agent dark on the router — tick was "
                            f"already {int(age)}s old at the last pull "
                            f"(pull keeps copying a corpse)"))
                continue
            if tick.get("ok") is False:
                errs = tick.get("errors") or []
                first = str(errs[0]) if errs else "unspecified"
                bad.append((device,
                            f"agent self-report ok=false "
                            f"({len(errs)} witness(es): {first})"))

        if not bad:
            if skipped_files:
                # some routers' mirrors could not be read this tick — the
                # readable ones are clean, the rest are UNOBSERVABLE
                note_disposition(
                    "router_scout_degraded", "indeterminate",
                    reason="mirror file(s) unreadable — router(s) unobservable",
                )
            else:
                note_disposition("router_scout_degraded", "clean")
            _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "router_scout_degraded", "indeterminate",
                reason="degraded tick seen; held by 2-tick debounce",
            )
            return None

        devices = sorted({d for d, _ in bad})
        descs = [f"{d}: {why}" for d, why in sorted(bad)]
        return Signal(
            cls="router_scout_degraded",
            subject=devices[0] if len(devices) == 1 else "router-scout",
            severity="degraded",
            detail=("router scout degraded: " + "; ".join(descs)
                    + " — check the router's scout cron/conf "
                    "(/etc/meshforge/, templates/openwrt/README.md); the "
                    "pull channel itself is healthy or this would be "
                    "cron_verdict_stale instead."),
            issue_ref=None,
            extra={"routers": [{"device": d, "why": w}
                               for d, w in sorted(bad)],
                   "streak": streak},
        )
    except Exception:
        note_disposition(
            "router_scout_degraded", "indeterminate",
            reason="probe raised; observation failed",
        )
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: ntfy loopback (2026-06-18 — ntfy receipt-heartbeat Phase 2)
#
# The alerting spine's OWN liveness. A manager-box collector cron
# (scripts/fleet_ntfy_loopback.sh) publishes a nonce'd, MIN-priority heartbeat
# to the FLEET topic, then polls ntfy.sh's poll API to confirm the nonce LOOPS
# BACK within a window; on a miss it escalates via the Phase-1 EMAIL backbone
# (ntfy is the suspect channel, so it does NOT page back through ntfy) and
# writes a verdict file. This probe READS that file (read-only: probes never
# send network traffic) and surfaces a miss into mini's brief + /fleet — the
# "send ≠ receipt" lesson aimed at the spine itself (the 2026-06-14→17 dark
# incident). Catches ntfy.sh-down / fleet-topic-publish-broken / sender-no-op;
# the operator-phone-on-wrong-topic case is Phase 3's tap-to-ack job. Mirrors
# host_frozen's file-read pattern + 2-tick debounce. Alert-only
# (propose_escalation — the collector owns the email page).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_NTFY_LOOPBACK_DEBOUNCE_PATH = "/var/lib/meshforge/ntfy_loopback_debounce.json"
NTFY_LOOPBACK_STATE_STALE_S = 5400   # FLOOR for the stale window. The EFFECTIVE
                                     # window scales to 3× the collector's OWN
                                     # recorded cadence (interval_s in the verdict
                                     # file), so a cron cadence change (e.g.
                                     # */30 → q2hr) needs NO second edit here —
                                     # the cron is the single source of truth
                                     # (honest_failure_modes #5: one cadence, not
                                     # two drifting copies). This floor applies
                                     # only when interval_s is absent/insane.
                                     # Past the window the collector stopped →
                                     # cron_verdict_stale owns the dead-cron alert
                                     # (fleet_ntfy_loopback is verdict-wired).
NTFY_LOOPBACK_WEDGE_MISSES = 3       # this many consecutive misses → wedge
NTFY_LOOPBACK_DEGRADED_MISSES = 2    # FLOOR for "sustained". The EFFECTIVE value
                                     # scales to the collector's OWN recorded
                                     # escalate_after_misses, exactly like
                                     # interval_s above — the cron is the single
                                     # source of truth (honest_failure_modes #5).
                                     #
                                     # WHY THIS EXISTS (2026-08-08 yield audit):
                                     # this class fired 16× and every one of its
                                     # 12 dream proposals was rejected as "a
                                     # single transient publish miss that
                                     # self-cleared by the next run". The cause
                                     # was THREE consumers of one artifact each
                                     # picking their own threshold: the email
                                     # backbone escalated at >=2 (correct), while
                                     # the script's exit code AND this probe both
                                     # fired at >=1 — so one transient produced a
                                     # cron FAIL(1) *and* a degraded signal, and
                                     # cron_verdict_stale then latched onto the
                                     # FAIL. One event, two pages, neither real:
                                     # the operator's own rejection note from
                                     # 2026-06-25 called it "watcher-watching-
                                     # the-watcher". Note the debounce below is
                                     # NOT a second opinion — it counts 30s
                                     # WATCHDOG TICKS re-reading the SAME static
                                     # 2h-cadence file, so it confirmed nothing
                                     # and merely delayed the fire by 30s. Only
                                     # consecutive_misses counts independent
                                     # loopback RUNS, and each run already
                                     # retries its publish+poll cycle once.


def _read_ntfy_loopback_state(home) -> Tuple[Optional[str], Optional[float]]:
    """Read ``~/ntfy_loopback_state.json`` + its mtime as root, in-process (no
    sudo — watchdog sandbox). Returns ``(text, mtime)`` or ``(None, None)`` on
    absent/unreadable (→ INERT: the loopback monitor is manager-box-only)."""
    if not home:
        return None, None
    path = os.path.join(str(home), "ntfy_loopback_state.json")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text, os.path.getmtime(path)
    except (FileNotFoundError, IsADirectoryError):
        return None, None
    except OSError:
        # EXISTS but unreadable — the loopback verdict is UNOBSERVABLE, not
        # the positive "not the manager box" absence (worst-wins keeps this).
        note_disposition(
            "ntfy_loopback", "indeterminate",
            reason="loopback state unreadable — channel liveness unobservable",
        )
        return None, None


def probe_ntfy_loopback(
    *,
    operator: Optional[Tuple[int, str]] = None,
    state_text: Optional[str] = None,
    state_mtime: Optional[float] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    stale_after_s: float = NTFY_LOOPBACK_STATE_STALE_S,
    wedge_after_misses: int = NTFY_LOOPBACK_WEDGE_MISSES,
    degraded_after_misses: int = NTFY_LOOPBACK_DEGRADED_MISSES,
) -> Optional[Signal]:
    """Surface a failure of the ntfy alerting channel's OWN loopback — Phase 2 of
    the ntfy receipt-heartbeat arc (2026-06-18).

    Reads the manager box's ``~/ntfy_loopback_state.json`` (written by
    ``scripts/fleet_ntfy_loopback.sh``, which publishes a nonce'd min-priority
    heartbeat to the fleet topic and polls ntfy.sh to confirm it loops back).
    ``received == false`` means the channel is dark from this box's vantage —
    either the publish failed (sender-no-op / network) or it published but the
    heartbeat never came back (ntfy.sh down / the fleet topic's delivery
    broken). The collector OWNS the email page (it escalates via the Phase-1
    EMAIL backbone — ntfy is the suspect channel, so it does NOT page back
    through ntfy); this probe is VISIBILITY into mini's brief + /fleet
    (propose_escalation, no duplicate page — the fleet_box_unreachable model).

    Self-guards None: no verdict file (not the manager box → INERT — the monitor
    is manager-box-only), STALE file past ``stale_after_s`` (the collector
    stopped — reading a frozen verdict as current is the absence-of-evidence
    trap; ``cron_verdict_stale`` owns the dead-cron alert, fleet_ntfy_loopback is
    verdict-wired), unparseable JSON, a ``received`` that is not an explicit bool
    (torn/partial write — indeterminate, never read as a miss; honest_failure_
    modes #1/#2), or ``received == true``. 2-tick debounce rides a one-off
    transient miss. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_NTFY_LOOPBACK_DEBOUNCE_PATH

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
            state_text, state_mtime = _read_ntfy_loopback_state(home)

        if not state_text:
            note_disposition(
                "ntfy_loopback", "inert",
                reason="no loopback state file (manager-box-only organ)",
            )
            _save_parity_streak(sp, 0)      # no monitor here → INERT
            return None

        try:
            doc = json.loads(state_text)
        except (ValueError, TypeError):
            note_disposition(
                "ntfy_loopback", "indeterminate",
                reason="unparseable state file",
            )
            _save_parity_streak(sp, 0)      # garbage → don't false-fire
            return None
        if not isinstance(doc, dict):
            note_disposition(
                "ntfy_loopback", "indeterminate",
                reason="state file is not a JSON object",
            )
            _save_parity_streak(sp, 0)
            return None

        # The stale window scales with the collector's OWN recorded cadence
        # (interval_s) so changing the cron cadence (e.g. */30 → q2hr) needs no
        # second edit here — the cron is the single source of truth
        # (honest_failure_modes #5). stale_after_s is the FLOOR, applied when
        # interval_s is absent/insane; an absurd recorded value is clamped (#6).
        interval = doc.get("interval_s")
        if (isinstance(interval, (int, float)) and not isinstance(interval, bool)
                and 60 <= interval <= 21600):
            effective_stale = max(float(interval) * 3.0, float(stale_after_s))
        else:
            effective_stale = float(stale_after_s)

        # Stale file = the collector stopped; cron_verdict_stale owns that alert.
        if state_mtime is not None and (now - state_mtime) > effective_stale:
            note_disposition(
                "ntfy_loopback", "indeterminate",
                reason="state file stale; cron_verdict_stale owns the dead-cron page",
            )
            _save_parity_streak(sp, 0)
            return None

        received = doc.get("received")
        # Indeterminate (key missing / torn write / not a bool) → HOLD, never
        # read as a miss — absence of evidence is not a failure (#80 class).
        if not isinstance(received, bool):
            note_disposition(
                "ntfy_loopback", "indeterminate",
                reason="received missing/not a bool (torn write)",
            )
            _save_parity_streak(sp, 0)
            return None
        if received:
            note_disposition("ntfy_loopback", "clean")
            _save_parity_streak(sp, 0)      # looped back → healthy
            return None

        # received is False → a real miss.
        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "ntfy_loopback", "indeterminate",
                reason="loopback miss seen; held by 2-tick debounce",
            )
            return None

        published_ok = doc.get("published_ok")
        raw_misses = doc.get("consecutive_misses")
        if (not isinstance(raw_misses, int) or isinstance(raw_misses, bool)
                or raw_misses < 0):
            # UNKNOWN, never a healthy-looking 0: the old `except → misses = 0`
            # made an unreadable counter indistinguishable from a fresh miss,
            # and with the sustained gate below that would silently fail OPEN
            # (never fire). Absence of the count is not a count of zero.
            note_disposition(
                "ntfy_loopback", "indeterminate",
                reason=("miss observed but consecutive_misses is missing or "
                        "malformed — cannot tell a transient from a sustained "
                        "outage"),
            )
            return None
        misses = raw_misses

        # Sustained-miss gate, scaled to the collector's own escalate threshold
        # so the email backbone, the cron verdict, and this probe fire off ONE
        # number. Clamped (#6) — an absurd recorded value cannot disarm this.
        recorded = doc.get("escalate_after_misses")
        if (isinstance(recorded, int) and not isinstance(recorded, bool)
                and 1 <= recorded <= 10):
            degraded_at = recorded
        else:
            degraded_at = degraded_after_misses
        if misses < degraded_at:
            note_disposition(
                "ntfy_loopback", "indeterminate",
                reason=(f"single loopback miss ({misses}/{degraded_at}) — the "
                        f"collector retries in-run and this self-clears on the "
                        f"next cycle; awaiting a second consecutive miss"),
            )
            return None

        if published_ok is False:
            cause = ("could NOT publish to the fleet ntfy topic from this box "
                     "(sender no-op / network) — ntfy pages from here will not send")
        else:
            cause = ("published but the heartbeat did NOT loop back via ntfy.sh "
                     "(server or fleet-topic delivery broken) — pages may be lost")
        return Signal(
            cls="ntfy_loopback",
            subject="ntfy",
            severity="wedge" if misses >= wedge_after_misses else "degraded",
            detail=("ntfy alerting channel loopback FAILED: " + cause
                    + f" ({misses} consecutive miss(es)). The collector escalates "
                    "via the Phase-1 EMAIL backbone; this is visibility. "
                    "'send ≠ receipt' — the spine watching itself."),
            issue_ref=None,
            extra={"published_ok": (published_ok if isinstance(published_ok, bool)
                                    else None),
                   "consecutive_misses": misses, "streak": streak},
        )
    except Exception:
        note_disposition(
            "ntfy_loopback", "indeterminate",
            reason="probe raised; observation failed",
        )
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: ntfy ack stale (2026-06-18 — ntfy receipt-heartbeat Phase 3)
#
# The ONLY rung that confirms the operator's DEVICE. A manager-box cron
# (scripts/fleet_ntfy_ack.sh) sends a WEEKLY tap-to-ack page to the fleet topic
# with an ntfy action button; the tap makes the PHONE POST to a dedicated
# ack-topic (<fleet>-ack), which the cron polls. consecutive_unacked_pings grows
# each unacked week (reset on ack); the cron escalates via the Phase-1 EMAIL
# backbone at >=2 unacked, and this READ-ONLY probe surfaces it into mini's brief
# + /fleet. Catches exactly the 2026-06-14→17 incident (phone on a wrong/dead
# topic, app killed, notifications off) — what loopback (Phase 2, a different
# subscriber) structurally cannot. Mirrors the host_frozen file-read pattern +
# 2-tick debounce. Alert-only (propose_escalation — the cron owns the email).
# ─────────────────────────────────────────────────────────────────────

DEFAULT_NTFY_ACK_DEBOUNCE_PATH = "/var/lib/meshforge/ntfy_ack_debounce.json"
NTFY_ACK_STATE_STALE_S = 14400   # state file older than this → the cron stopped;
                                 # cron_verdict_stale owns the dead-cron alert
                                 # (fleet_ntfy_ack is verdict-wired). ~4× hourly.
NTFY_ACK_WEDGE_PINGS = 2         # this many consecutive unacked weeks → wedge


def _read_ntfy_ack_state(home) -> Tuple[Optional[str], Optional[float]]:
    """Read ``~/ntfy_ack_state.json`` + its mtime as root, in-process (no sudo —
    watchdog sandbox). Returns ``(text, mtime)`` or ``(None, None)`` on
    absent/unreadable (→ INERT: the ack monitor is manager-box-only)."""
    if not home:
        return None, None
    path = os.path.join(str(home), "ntfy_ack_state.json")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        return text, os.path.getmtime(path)
    except (FileNotFoundError, IsADirectoryError):
        return None, None
    except OSError:
        # EXISTS but unreadable — the ack state is UNOBSERVABLE, not the
        # positive "not the manager box" absence (worst-wins keeps this).
        note_disposition(
            "ntfy_ack_stale", "indeterminate",
            reason="ack state unreadable — operator receipt unobservable",
        )
        return None, None


def probe_ntfy_ack_stale(
    *,
    operator: Optional[Tuple[int, str]] = None,
    state_text: Optional[str] = None,
    state_mtime: Optional[float] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
    stale_after_s: float = NTFY_ACK_STATE_STALE_S,
    wedge_after_pings: int = NTFY_ACK_WEDGE_PINGS,
) -> Optional[Signal]:
    """Surface an UNCONFIRMED operator device — Phase 3 of the ntfy receipt-
    heartbeat arc (2026-06-18).

    Reads the manager box's ``~/ntfy_ack_state.json`` (written by
    ``scripts/fleet_ntfy_ack.sh``, which sends a weekly tap-to-ack page and polls
    the ack-topic the phone POSTs to on tap). ``consecutive_unacked_pings >= 1``
    means a weekly page went out with no tap-to-ack — the operator's phone may
    not be receiving fleet alerts (wrong/dead topic, app killed, notifications
    off), the exact 2026-06-14→17 failure that loopback (a different subscriber)
    cannot catch. The cron OWNS the email page (escalates at >=2 unacked weeks);
    this probe is VISIBILITY into mini's brief + /fleet (propose_escalation, no
    duplicate page — the fleet_box_unreachable model).

    Self-guards None: no state file (not the manager box → INERT), STALE file
    past ``stale_after_s`` (the cron stopped — cron_verdict_stale owns the
    dead-cron alert, fleet_ntfy_ack is verdict-wired), unparseable JSON, a
    ``consecutive_unacked_pings`` that is not an int (indeterminate → held, never
    invented; a JSON bool is rejected too), or ``<= 0`` (acked → healthy; also
    covers never-pinged, which keeps the counter at 0 so the first-week grace
    never false-alarms). 2-tick debounce. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_NTFY_ACK_DEBOUNCE_PATH

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
            state_text, state_mtime = _read_ntfy_ack_state(home)

        if not state_text:
            note_disposition(
                "ntfy_ack_stale", "inert",
                reason="no ack state file (manager-box-only organ)",
            )
            _save_parity_streak(sp, 0)      # no monitor here → INERT
            return None

        if state_mtime is not None and (now - state_mtime) > stale_after_s:
            note_disposition(
                "ntfy_ack_stale", "indeterminate",
                reason="state file stale; cron_verdict_stale owns the dead-cron page",
            )
            _save_parity_streak(sp, 0)      # cron stopped → cron_verdict_stale owns it
            return None

        try:
            doc = json.loads(state_text)
        except (ValueError, TypeError):
            note_disposition(
                "ntfy_ack_stale", "indeterminate",
                reason="unparseable state file",
            )
            _save_parity_streak(sp, 0)
            return None
        if not isinstance(doc, dict):
            note_disposition(
                "ntfy_ack_stale", "indeterminate",
                reason="state file is not a JSON object",
            )
            _save_parity_streak(sp, 0)
            return None

        unacked = doc.get("consecutive_unacked_pings")
        # Indeterminate (missing / not an int / a JSON bool) → HOLD, never invent.
        if not isinstance(unacked, int) or isinstance(unacked, bool):
            note_disposition(
                "ntfy_ack_stale", "indeterminate",
                reason="consecutive_unacked_pings missing/not an int",
            )
            _save_parity_streak(sp, 0)
            return None
        if unacked <= 0:
            if not doc.get("last_ping_ts"):
                # never pinged (last_ping_ts absent/0) — first-week grace
                note_disposition(
                    "ntfy_ack_stale", "inert",
                    reason="no weekly ping sent yet",
                )
            else:
                note_disposition("ntfy_ack_stale", "clean")
            _save_parity_streak(sp, 0)      # acked (or never pinged) → healthy
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "ntfy_ack_stale", "indeterminate",
                reason="unacked ping seen; held by 2-tick debounce",
            )
            return None

        # Name WHICH page went un-acked, and whether a fresh one has since gone
        # out. Without this the signal fires at the very moment the new weekly
        # page lands (the cron judges last week's page in the same run that
        # sends this week's), and reads as "the tap you just made failed" —
        # which is how 2026-09-03's four-taps-in-four-seconds happened while
        # every one of them had landed. Legibility is the instrument's job.
        unacked_ping_ts = doc.get("unacked_ping_ts")
        last_ping_ts = doc.get("last_ping_ts")
        which = ""
        if isinstance(unacked_ping_ts, (int, float)) and not isinstance(
                unacked_ping_ts, bool) and unacked_ping_ts > 0:
            which = (" The page sent "
                     + time.strftime("%m-%d %H:%M", time.localtime(unacked_ping_ts))
                     + " is the one that went un-acked.")
            if (isinstance(last_ping_ts, (int, float))
                    and not isinstance(last_ping_ts, bool)
                    and last_ping_ts > unacked_ping_ts):
                which += (" A fresh page went out "
                          + time.strftime("%m-%d %H:%M", time.localtime(last_ping_ts))
                          + " — a tap on THAT one shows 'Receipt confirmed' on "
                          "your phone at once and clears this within the hour.")
        return Signal(
            cls="ntfy_ack_stale",
            subject="ntfy",
            severity="wedge" if unacked >= wedge_after_pings else "degraded",
            detail=(f"weekly fleet-alert ack UNCONFIRMED — {unacked} consecutive "
                    "weekly page(s) with no tap-to-ack." + which + " Your phone "
                    "may not be receiving fleet pages (wrong/dead topic, app "
                    "killed, notifications off — the exact 06-14→17 failure). "
                    "Tap 'Confirm receipt' on the next weekly page, or check "
                    "your ntfy subscription. The cron escalates via the Phase-1 "
                    "EMAIL backbone; this is visibility."),
            issue_ref=None,
            extra={"consecutive_unacked_pings": unacked, "streak": streak,
                   "unacked_ping_ts": unacked_ping_ts if which else None},
        )
    except Exception:
        note_disposition(
            "ntfy_ack_stale", "indeterminate",
            reason="probe raised; observation failed",
        )
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: kernel reboot pending (2026-06-09 version-updates arc — the
# 6.12.75-straggler guard: moc/moc1/moc3/meshanchor-server silently ran
# an old kernel for days while a newer one sat installed; nothing paged)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_KERNEL_REBOOT_DEBOUNCE_PATH = "/var/lib/meshforge/kernel_reboot_debounce.json"

# "6.12.75+rpt-rpi-2712" → ("6.12.75", "+rpt-rpi-2712")
# "6.8.0-1057-raspi"     → ("6.8.0", "-1057-raspi")
_KERNEL_RELEASE_RE = re.compile(r"^(\d+(?:\.\d+)+)(.*)$")
# Ubuntu/Debian build segment immediately after the dotted core: "-1057-raspi"
# → build 1057, flavor remainder "-raspi". Trailing "(?:[-+.].*)?$" ensures the
# digits form a whole segment ("-1057raspi" does NOT split).
_KERNEL_BUILD_RE = re.compile(r"^-(\d+)((?:[-+.].*)?)$")


def _parse_kernel_release(release) -> Optional[Tuple[Tuple[int, ...], str]]:
    """Split a kernel release string into (comparable numeric tuple, flavor).

    The numeric tuple is the dotted core plus any Ubuntu-style ``-NNN`` build
    segments that follow it (``6.8.0-1057-raspi`` → ``(6, 8, 0, 1057)``); the
    flavor is everything left over (``-raspi`` / ``+rpt-rpi-2712``). RPi boxes
    install MULTIPLE flavors side by side (rpi-v8 AND rpi-2712), so ordering is
    only meaningful between identical flavors — callers must never compare
    across flavors. Returns None on anything unparseable (a module dir that
    fails to parse is SKIPPED by the caller, never treated as newer or older).
    """
    if not isinstance(release, str):
        return None
    m = _KERNEL_RELEASE_RE.match(release.strip())
    if m is None:
        return None
    try:
        nums = [int(x) for x in m.group(1).split(".")]
    except ValueError:
        return None
    rest = m.group(2)
    while True:
        bm = _KERNEL_BUILD_RE.match(rest)
        if bm is None:
            break
        nums.append(int(bm.group(1)))
        rest = bm.group(2)
    return tuple(nums), rest


def probe_kernel_reboot_pending(
    *,
    modules_dir: str = "/lib/modules",
    boot_dir: str = "/boot",
    reboot_required_path: str = "/var/run/reboot-required",
    running_release: Optional[str] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when this box is running an older kernel than it has installed.

    The 2026-06-09 version-updates arc found moc/moc1/moc3/meshanchor-server
    silently running kernel 6.12.75 while 6.18.x was installed or available —
    moc1 had 6.18.33 INSTALLED but not running for days, and nothing paged.
    Reboot pending = EITHER ``/var/run/reboot-required`` exists (Ubuntu boxes)
    OR a NEWER **same-flavor** kernel sits under ``/lib/modules`` than the one
    in ``os.uname().release``. Flavor discipline is load-bearing: RPi boxes
    install rpi-v8 AND rpi-2712 simultaneously, so the comparison runs ONLY
    against same-flavor entries (see ``_parse_kernel_release``).

    Read-only, no sudo (the watchdog's NoNewPrivileges sandbox forbids it).

    ⚠️ **World-readable is not the same as visible.** This docstring used to
    claim ``/lib/modules`` was readable "anyway"; that assumption was FALSE
    under the unit's own hardening. ``ProtectKernelModules=yes`` MASKS both
    ``/lib/modules`` and ``/usr/lib/modules`` — proven in the service sandbox
    2026-07-27 (``systemd-run --property=ProtectKernelModules=yes ls
    /lib/modules`` → 0 entries vs 36 unsandboxed), and neither ``ReadOnlyPaths``
    nor ``BindReadOnlyPaths`` restores them. Leg B therefore reported
    ``indeterminate`` on EVERY tick, and because indeterminate means "not
    observed", ``watchdog_tracker`` held the last-known verdict forever
    (``extra.unobserved_hold``): moc5 kept reporting ``degraded`` after the
    condition was cured, with a debounce streak of 347. A hold whose observer
    can never see again is an unfalsifiable assertion.

    Cure: fall back to ``/boot/vmlinuz-<release>``, which SURVIVES that sandbox
    and carries release strings identical to the modules dirs on both families
    (verified 2026-07-27 — Debian/rpt ``6.18.34+rpt-rpi-2712``, Ubuntu/raspi
    ``6.8.0-1060-raspi``; newest same-flavor entry agreed with ``/lib/modules``
    on both). Hardening is preserved — do NOT "fix" this by setting
    ``ProtectKernelModules=no`` on a root service.

    Honest failure modes: ``/lib/modules`` unreadable/empty, the running
    release unparseable, or no same-flavor sibling entries → that leg is
    indeterminate (never false-alarm) while the reboot-required-file leg still
    works independently; unparseable module dirs are skipped. Observed-healthy
    AND indeterminate paths both reset the debounce streak (mirrors
    ``probe_role_drift``). 2-tick debounce rides out a tick that lands mid-
    upgrade (dpkg unpacking the new modules tree). Severity ``degraded``: the
    box serves fine — it is running known-stale code until a reboot.
    """
    sp = state_path or DEFAULT_KERNEL_REBOOT_DEBOUNCE_PATH

    # Leg A — distro reboot-required flag (independent of kernel parsing).
    try:
        reboot_required = os.path.exists(reboot_required_path)
    except OSError:
        reboot_required = False

    # Leg B — newer same-flavor kernel installed under /lib/modules.
    if running_release is None:
        running_release = os.uname().release
    running_parsed = _parse_kernel_release(running_release)

    newest_installed: Optional[str] = None
    newer_found = False
    # Bound BEFORE the branch: an unparseable running release skips leg B
    # entirely, but the Signal below is still reachable via leg A (the
    # reboot-required flag), and it reads kernel_source.
    kernel_source: Optional[str] = None
    if running_parsed is not None:
        run_nums, run_flavor = running_parsed
        try:
            entries = [e.name for e in os.scandir(modules_dir) if e.is_dir()]
        except OSError:
            entries = []  # unreadable → try the boot fallback below
        if entries:
            kernel_source = "modules"
        else:
            # The modules dir is MASKED by ProtectKernelModules=yes, not merely
            # absent (see docstring). /boot survives that sandbox and carries
            # the same release strings, so this keeps leg B observable instead
            # of leaving it permanently indeterminate — which the tracker turns
            # into a held signal that can never clear.
            try:
                entries = [
                    n[len("vmlinuz-"):] for n in os.listdir(boot_dir)
                    if n.startswith("vmlinuz-") and n != "vmlinuz-"
                ]
            except OSError:
                entries = []  # both sources blind → leg indeterminate, no alarm
            if entries:
                kernel_source = "boot"
        best: Optional[Tuple[Tuple[int, ...], str]] = None
        for name in entries:
            parsed = _parse_kernel_release(name)
            if parsed is None:
                continue  # unparseable dir is skipped, not newer/older
            nums, flavor = parsed
            if flavor != run_flavor:
                continue  # different flavor (rpi-v8 vs rpi-2712) — never compare
            if best is None or nums > best[0]:
                best = (nums, name)
        if best is not None:
            newest_installed = best[1]
            newer_found = best[0] > run_nums

    if not reboot_required and not newer_found:
        # Observed-healthy (running == newest same-flavor, no flag) and the
        # indeterminate shapes (unreadable/empty modules dir, unparseable
        # release, no same-flavor sibling) all land here: reset, stay silent.
        if running_parsed is None:
            note_disposition(
                "kernel_reboot_pending", "indeterminate",
                reason="running kernel release unparseable",
            )
        elif newest_installed is None:
            note_disposition(
                "kernel_reboot_pending", "indeterminate",
                reason=("no same-flavor kernel entries readable under modules "
                        "dir or boot dir"),
            )
        else:
            note_disposition("kernel_reboot_pending", "clean")
        _save_parity_streak(sp, 0)
        return None

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        note_disposition(
            "kernel_reboot_pending", "indeterminate",
            reason="reboot-pending seen; held by 2-tick debounce",
        )
        return None  # pending seen, not yet confirmed across consecutive ticks

    reasons = []
    if newer_found and newest_installed:
        _where = boot_dir if kernel_source == "boot" else modules_dir
        reasons.append(
            f"running {running_release} but {newest_installed} is installed "
            f"under {_where}"
        )
    if reboot_required:
        reasons.append(f"{reboot_required_path} flag present")
    detail = (
        f"Kernel reboot pending — {'; '.join(reasons)} | confirmed over "
        f"{streak} consecutive ticks | the 2026-06-09 version-updates arc "
        f"found boxes silently running a stale kernel for days. Fix: schedule "
        f"a clean reboot (planned reboots through clean shutdown record "
        f"boot_health clean-exit)."
    )
    return Signal(
        cls="kernel_reboot_pending",
        subject=running_release,
        severity="degraded",
        detail=detail,
        extra={
            "running": running_release,
            "newest_installed": newest_installed,
            "kernel_source": kernel_source,  # "modules" | "boot" | None(blind)
            "reboot_required_file": reboot_required,
            "debounce_streak": streak,
        },
    )





# ─────────────────────────────────────────────────────────────────────
# AREDN organ probes — moved to watchdog_probes_aredn.py on 2026-07-20
# (MF025 file-size cap). Re-exported here so every existing import path
# keeps working; this module stays the documented entry point.
# ─────────────────────────────────────────────────────────────────────
from utils.watchdog_probes_aredn import (  # noqa: E402,F401
    DEFAULT_AREDN_SOURCE_DEBOUNCE_PATH,
    _AREDN_DARK_REASONS,
    _read_configured_aredn_ips,
    _read_configured_aredn_ips_state,
    _read_aredn_expectation,
    _fetch_local_status_json,
    probe_aredn_source_dark,
)



# ─────────────────────────────────────────────────────────────────────
# Probe: inherited-app drift (Action 5, 2026-06-21 upstream-app ownership)
# An INHERITED upstream app checkout (origin NOT ours) carrying an
# unversioned tracked-file code patch — a hand-edit that lives in no repo
# we control and is one `git pull` from silent deletion (the rescued
# .32 + dev-box bot patches were exactly this). Enforces policy §4.2.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_INHERITED_DRIFT_DEBOUNCE_PATH = (
    "/var/lib/meshforge/inherited_app_drift_debounce.json")

# A git checkout is OWNED (skip it — already version-controlled) when its
# origin URL names our GitHub org. Everything else with a remote is INHERITED
# upstream — the risk class this probe polices. Matched case-insensitively
# against the whole URL (covers https://github.com/Nursedude/… and
# git@github.com:Nursedude/…).
_OWNED_ORIGIN_MARKER = "nursedude"

# Top-level basenames never treated as an inherited fleet app even with a
# non-owned origin (PINS.md "Excluded from pinning"): standard tool installs
# and separately-governed forks. wireclaw-dudeclaw is the dude-claw FIRMWARE
# fork (its own FORK invariant — never commit on the dudeclaw branch, so it is
# intentionally dirty); nvm is a tool, not a fleet app. (wireclaw also lives
# nested under ~/src so the top-level scan misses it anyway — this is the
# belt-and-suspenders guard.)
_INHERITED_SCAN_EXCLUDE = frozenset({".nvm", "nvm", "wireclaw-dudeclaw"})

# Tracked-file modifications that are MACHINE-GENERATED dependency metadata,
# NOT a hand-edited "code patch" (policy §4.3 config≠code). npm/yarn/pip/cargo
# regenerate these on install — the MeshSense package*.json churn is exactly
# this and must NOT read as an unversioned source patch. Matched by exact
# basename or a ``*.lock`` suffix. Deliberately small + generic: ONE constant,
# no per-app allowlist that would drift (honest_failure_modes #5). A real
# hand-edited source file is never on this list, so it still fires.
_BENIGN_TRACKED_BASENAMES = frozenset({
    "package.json", "package-lock.json", "npm-shrinkwrap.json",
    "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    "Cargo.lock", "composer.lock", "Gemfile.lock",
})


def _read_git_origin_url(repo_path: str) -> Optional[str]:
    """Origin remote URL from ``<repo>/.git/config`` — read DIRECTLY, no git
    subprocess.

    Why not ``git remote get-url``? Root running git on a user-owned checkout
    hits the 'dubious ownership' refusal, and spawning a process for the
    owned-repo majority (every box has /opt/meshforge etc.) is wasteful. A flat
    read of the config + a tiny INI walk classifies owned-vs-inherited with no
    spawn at all. Returns the URL, or None when the file/section/key is absent
    or unreadable — the caller then SKIPS the repo (can't classify → never
    guess owned-vs-inherited; indeterminate is not 'inherited')."""
    cfg = os.path.join(repo_path, ".git", "config")
    try:
        with open(cfg, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    in_origin = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            # [remote "origin"] — tolerate whitespace variants.
            in_origin = (line.replace(" ", "").lower() == '[remote"origin"]')
            continue
        if in_origin:
            m = re.match(r"url\s*=\s*(\S+)", line)
            if m:
                return m.group(1)
    return None


def _git_tracked_modifications(
    repo_path: str, git_path: str = "git", timeout_s: float = 10.0,
) -> Optional[List[str]]:
    """Tracked-file paths with uncommitted modifications in ``repo_path``
    (``git status --porcelain --untracked-files=no --ignore-submodules=all``).

    UNTRACKED files are excluded by the flag (Raven's raven.conf, ucode's
    build/ artifacts → not a patch, the policy's config-not-code line).
    SUBMODULES are excluded too (``--ignore-submodules=all``): a vendored
    submodule's own working-tree churn is a dependency state, not a hand-edited
    source patch in the PARENT app — MeshSense's ``api/webbluetooth`` /
    ``api/meshtastic-js`` gitlinks show ``S.M.`` from npm/build activity and
    must NOT read as a MeshSense source edit (verified against moc5's real tree
    2026-06-21; the #78 synthetic-vs-real lesson — a clean-package.json fixture
    missed this). Returns ``None`` on ANY git error/timeout — a git failure must
    NEVER read as a clean tree (honest_failure_modes #1/#2). ``-c safe.directory``
    lets sandboxed root status a user-owned checkout without the dubious-
    ownership refusal; ``-c core.fileMode=false`` keeps a mere perms-bit diff
    from masquerading as a content edit."""
    try:
        proc = subprocess.run(
            [git_path,
             "-c", f"safe.directory={repo_path}",
             "-c", "core.fileMode=false",
             "-C", repo_path,
             "status", "--porcelain", "--untracked-files=no",
             "--ignore-submodules=all"],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None  # git refused/errored → indeterminate, not "clean"
    files: List[str] = []
    for line in proc.stdout.splitlines():
        # porcelain v1: "XY <path>" (or "XY <old> -> <new>" for renames/copies).
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        path = path.strip('"')
        if path:
            files.append(path)
    return files


def _real_code_patches(files: List[str]) -> List[str]:
    """From a tracked-modification list, drop machine-generated dependency
    manifests/lockfiles (benign churn) and keep the real code/config patches —
    the unversioned-source-edit defect (R1). A path is benign iff its basename
    is a known manifest or ends in ``.lock``."""
    out: List[str] = []
    for p in files:
        base = os.path.basename(p)
        if base in _BENIGN_TRACKED_BASENAMES or base.endswith(".lock"):
            continue
        out.append(p)
    return out


def _iter_inherited_checkouts(
    scan_roots,
    *,
    exclude=_INHERITED_SCAN_EXCLUDE,
    origin_fn=_read_git_origin_url,
    max_repos: int = 40,
    skipped_roots: Optional[List[str]] = None,
):
    """Yield ``(repo_path, origin_url)`` for each INHERITED (non-owned-origin)
    git checkout at the TOP LEVEL of any ``scan_roots`` dir.

    Top-level only (bounded; deep-nested checkouts like ~/src/wireclaw-dudeclaw
    are separately governed — PINS.md). Owned (Nursedude-origin) repos and the
    explicit excludes are skipped; a repo whose origin can't be read is skipped
    (can't classify → don't guess). Caps at ``max_repos`` (runaway guard).
    A root that EXISTS but can't be scanned is appended to ``skipped_roots``
    (unobservable ≠ absent — the caller must not read the sweep as complete);
    a positively-absent root is legitimate absence, not a skip."""
    seen = 0
    for root in scan_roots:
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except (FileNotFoundError, NotADirectoryError):
            continue   # root positively absent — legitimate absence
        except OSError:
            if skipped_roots is not None:
                skipped_roots.append(str(root))
            continue   # root unobservable — recorded, never read as scanned
        for entry in entries:
            if seen >= max_repos:
                return
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            if entry.name in exclude:
                continue
            if not os.path.isdir(os.path.join(entry.path, ".git")):
                continue
            url = origin_fn(entry.path)
            if not url:
                continue  # no readable origin → can't classify, skip
            if _OWNED_ORIGIN_MARKER in url.lower():
                continue  # owned fork — version-controlled already
            seen += 1
            yield entry.path, url


def _resolve_operator_home(service_user_fn=None) -> Optional[str]:
    """Resolve the operator's home dir (where inherited app checkouts live),
    root-context safe. Tries the live --user-bus operator first (the
    cron_verdict/synth_soak pattern — linger is on for the mini units), then the
    rnsd service user (the dominant drift-probe pattern). None if neither
    resolves to a non-root home."""
    # Primary: an operator uid with a live systemd --user bus.
    if service_user_fn is None:
        try:
            from utils.fleet_test_runner import _find_operator_user
            op = _find_operator_user()
            if op:
                import pwd
                return pwd.getpwuid(op[0]).pw_dir
        except Exception:
            pass
    # Fallback (or injected): the rnsd/service user.
    try:
        fn = service_user_fn
        if fn is None:
            from utils.rns_tree_perms import _read_rnsd_user
            fn = _read_rnsd_user
        user = fn()
        if user and user != "root":
            import pwd
            return pwd.getpwnam(user).pw_dir
    except Exception:
        pass
    return None


def probe_inherited_app_drift(
    *,
    scan_roots=None,
    service_user_fn=None,
    repos=None,
    git_path: str = "git",
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when an INHERITED (non-owned) upstream app checkout carries an
    unversioned tracked-file code patch — the R1 defect the upstream-app
    ownership policy (Action 5, 2026-06-21) exists to police.

    The operator's core concern: *"a local patch on an inherited checkout gets
    CLOBBERED on git pull + isn't version-controlled."* A hand-edited source
    file on a checkout we don't own (origin not Nursedude) exists in no repo we
    control and is one ``git pull`` from silent deletion (the rescued
    .32 + dev-box bot patches were exactly this). This makes that hygiene defect
    a continuously-monitored signal in /fleet + the mini rollup instead of a
    once-a-quarter manual git survey.

    Scope is deliberately **LOCAL problem-class detection**, NOT a comparison
    against ``fleet-overlays/PINS.md`` (the human pin ledger): the probe
    enforces the §4.2 invariant directly, PINS.md stays the record, and the two
    never have to agree on a shared constant (honest_failure_modes #5 —
    two-consumers-of-one-constant drift). PINS.md isn't even present on moc5
    (the box that has inherited apps), so reading it would couple to an artifact
    that isn't there.

    The floating-``main`` / pin-drift leg is intentionally **NOT** a local fire
    here. The fleet's chosen enforcement is *"record the pin, never auto-pull"*
    (PINS.md), NOT detached HEAD — so firing on "on a branch" would contradict
    the policy and false-page every intentionally-pinned moc5 app. Detecting
    "drifted off the recorded pin" needs the ledger SHA, which belongs to a
    future ledger/cross-box check, not this single-box local probe.

    What it inspects per inherited checkout (top level of the operator home +
    ``/opt``): ``git status --porcelain --untracked-files=no
    --ignore-submodules=all``. Untracked config/build artifacts (Raven's
    raven.conf, ucode's build/) and vendored-submodule churn (MeshSense's
    ``api/webbluetooth`` gitlink) are excluded by the flags, and machine-
    generated dependency manifests/lockfiles (the MeshSense npm package*.json
    churn) are filtered out — so only a real hand-edited code/config patch in
    the parent app fires.

    Honest failure modes — every error path stays SILENT and never reads as a
    clean tree:

    - no inherited checkouts on this box → None (INERT — moc1/2/3, the common
      case; absence of inherited apps is not a failure to report).
    - operator home / scan root unresolvable or unreadable → that root is
      skipped (indeterminate).
    - a repo whose ``.git/config`` origin can't be read → skipped (can't
      classify owned-vs-inherited; never guess).
    - ``git status`` errors/times out for a repo → that repo contributes
      nothing (a git failure must not read as clean) — others still evaluated.
    - 2-tick debounce rides out an operator mid-edit / a fleet-roll window where
      a patch is briefly uncommitted before being committed or reverted (the
      patches this targets are long-lived, so debounce only suppresses blips).

    Severity ``degraded`` — latent version-control hygiene debt, not an active
    outage; the seed routes it to a side-effect-free escalation (no ntfy page).
    Expected true-positives on today's fleet: the gated ``.32 ~/meshing-around``
    bot patch (rescued to fleet-overlays, fork-built, deploy gated post-06-24)
    and ``.32 ~/raphael-kit`` example edit — both real unversioned patches the
    policy wants surfaced. ``repos`` may be injected for tests as a list of
    ``(path, origin_url, modified_tracked_files)`` (the benign filter is applied
    to the injected files too)."""
    sp = state_path or DEFAULT_INHERITED_DRIFT_DEBOUNCE_PATH
    try:
        # 1. Build the {inherited checkout → real code patches} list.
        dirty: List[Tuple[str, str, List[str]]] = []
        scanned = 0   # inherited checkouts evaluated (disposition note only)
        skipped_roots: List[str] = []   # roots we could NOT scan this tick
        if repos is None:
            if scan_roots is None:
                home = _resolve_operator_home(service_user_fn)
                if home is None:
                    # can't resolve where operator checkouts live — that
                    # root goes UNSCANNED, not silently narrowed to /opt
                    skipped_roots.append("<operator home unresolvable>")
                scan_roots = [r for r in (home, "/opt") if r]
            for path, url in _iter_inherited_checkouts(
                    scan_roots, skipped_roots=skipped_roots):
                scanned += 1
                mods = _git_tracked_modifications(path, git_path)
                if mods is None:
                    note_disposition(
                        "inherited_app_drift", "indeterminate",
                        reason="git status failed on an inherited checkout",
                    )
                    continue  # git indeterminate for this repo — skip
                patches = _real_code_patches(mods)
                if patches:
                    dirty.append((path, url, patches))
        else:
            for path, url, files in repos:
                scanned += 1
                patches = _real_code_patches(list(files))
                if patches:
                    dirty.append((path, url, patches))

        if not dirty:
            if skipped_roots:
                # part of the promised sweep never happened — clean/inert
                # over a partial scan would be the docstring's own lie
                note_disposition(
                    "inherited_app_drift", "indeterminate",
                    reason="scan root(s) unobservable — inherited checkouts "
                           "partially unscanned",
                )
            elif scanned:
                note_disposition("inherited_app_drift", "clean")
            else:
                note_disposition(
                    "inherited_app_drift", "inert",
                    reason="no inherited checkouts on this box",
                )
            _save_parity_streak(sp, 0)
            return None  # no inherited patch anywhere → INERT / clean

        # 2. Debounce — ride out a transient mid-edit / mid-roll window.
        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "inherited_app_drift", "indeterminate",
                reason="inherited patch seen; held by 2-tick debounce",
            )
            return None

        # 3. Build the signal (one aggregate; subject stable for edge-tracking).
        dirty.sort(key=lambda r: r[0])
        names = [os.path.basename(p.rstrip("/")) for p, _u, _f in dirty]
        breakdown = {
            os.path.basename(p.rstrip("/")): files for p, _u, files in dirty
        }
        shown = "; ".join(
            f"{os.path.basename(p.rstrip('/'))} "
            f"({', '.join(files[:3])}"
            f"{' +%d more' % (len(files) - 3) if len(files) > 3 else ''})"
            for p, _u, files in dirty[:4]
        )
        if len(dirty) > 4:
            shown += f" (+{len(dirty) - 4} more app(s))"
        detail = (
            f"Unversioned code patch on {len(dirty)} INHERITED upstream "
            f"checkout(s): {shown} | confirmed over {streak} consecutive ticks "
            f"| these hand-edits exist in no repo we control and are one `git "
            f"pull` from silent deletion (upstream-app ownership policy §4.2). "
            f"Fix: rescue the patch into an owned fork or the tracked overlay "
            f"(Nursedude/fleet-overlays), or upstream it as a PR, then converge "
            f"the checkout clean and record the chosen pin in "
            f"fleet-overlays/PINS.md."
        )
        return Signal(
            cls="inherited_app_drift",
            subject="inherited-apps",
            severity="degraded",
            detail=detail,
            extra={
                "apps": names,
                "patches": breakdown,
                "debounce_streak": streak,
            },
        )
    except Exception:
        note_disposition(
            "inherited_app_drift", "indeterminate",
            reason="probe raised; observation failed",
        )
        return None
