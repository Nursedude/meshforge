"""Watchdog probes — mini-dudeai self-observation failure shapes (#79).

History write stalled, rules seed drift, memory index oversize.
Part of the ``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from utils.watchdog_probe_core import (
    Signal,
    _read_deployment_declaration,
    note_disposition,
)

# ─────────────────────────────────────────────────────────────────────
# Probes: mini-dudeai self-health (audit 2026-06-09)
#
# mini-dudeai is the per-box observation loop. These three probes watch
# the loop's OWN integrity from outside it — the watchdog sees mini's
# files (state/history/rules) the same root-context, in-process way the
# other drift probes read the rnsd-user env. All self-guard None when
# mini isn't running on this box (no files / loop not ticking), so a box
# that doesn't run mini never false-alarms.
# ─────────────────────────────────────────────────────────────────────

# Canonical mini file names (mini_dudeai/presets/meshforge_fleet.py).
_MINI_STATE_NAME = "mini_dudeai_state.json"
_MINI_HISTORY_NAME = "mini_dudeai_history.jsonl"
_MINI_RULES_NAME = "mini_dudeai_rules.json"


def _seed_rules_path(meshforge_root: str, seed_name: str) -> str:
    """Path to a role seed's rules file (``configs/mini_dudeai_rules.<seed>.json``).

    The layout is OWNED by ``mini_dudeai.candidate.seed_rules_path``; this probe
    keeps a local copy because it must import cleanly even where the mini_dudeai
    package isn't installed (its mini import above is guarded). A test pins the
    two byte-identical so they can never drift — honest_failure_modes #5: share
    by import where layering allows, test-pin where it doesn't."""
    return os.path.join(
        meshforge_root, "configs", f"mini_dudeai_rules.{seed_name}.json")

# A mini tick is 30s; treat the loop as ALIVE only when state.json's
# last_tick is within this window (≈4 ticks of slack). A stale state means
# the daemon is stopped — not a write-failure to surface here.
_MINI_LOOP_FRESH_S = 150.0

DEFAULT_HISTORY_STALL_STATE_PATH = "/var/lib/meshforge/mini_history_stall.json"


def _resolve_mini_home() -> Optional[str]:
    """Resolve the mini operator's home dir, root-context safe.

    mini runs as a systemd --user unit owned by the operator, so the
    watchdog (sandboxed root) must derive the home from the operator UID
    and read it directly — never escalate (the rns_version_drift /
    cron_verdict lesson). None when no operator user is resolvable.
    """
    try:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
    except Exception:
        op = None
    if not op:
        return None
    try:
        import pwd
        return pwd.getpwuid(op[0]).pw_dir
    except (KeyError, OSError):
        return None


def _load_history_stall_state(state_path: str) -> dict:
    """Read the prior (fires, history mtime, streak) baseline. Any error → empty."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_history_stall_state(state_path: str, *, fires: int,
                              hist_mtime: float, streak: int) -> None:
    """Persist the baseline (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"fires": int(fires), "hist_mtime": float(hist_mtime),
                       "streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def probe_history_write_failure(
    *,
    mini_home: Optional[str] = None,
    state_doc: Optional[dict] = None,
    history_mtime: Optional[float] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Surface a PERSISTENT mini history-write failure (Issue #79).

    The engine swallows-and-prints a ``history append failed`` (HistoryWriter
    never raises into the tick) — so a perms/disk problem that kills ONLY the
    history file (state.json keeps writing fine) is invisible to the fleet. This
    probe makes it a signal: the loop is ALIVE (state.json ``last_tick_ts`` is
    recent) and the engine has FIRED more rules since we last looked (cumulative
    ``rules[*].fire_count`` advanced — fires are exactly what should append to
    history), yet the history file's mtime did NOT advance. A quiet box (no new
    fires) never touches history, so it can't false-alarm — the cumulative-fire
    delta is the honest distinguisher, not a bare mtime check.

    Self-guards None: mini not active (no state file / stale ``last_tick_ts``),
    no new fires since the baseline (nothing should have been appended), or the
    home can't be resolved. 2-tick debounce rides a single-tick read race.
    Severity ``degraded``: the loop still works; history (the warm-context feed)
    is the casualty.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_HISTORY_STALL_STATE_PATH

        if state_doc is None or history_mtime is None:
            home = mini_home or _resolve_mini_home()
            if not home:
                note_disposition("history_write_stalled", "indeterminate",
                                 reason="operator home unresolvable")
                return None
            if state_doc is None:
                try:
                    with open(os.path.join(home, _MINI_STATE_NAME),
                              "r", encoding="utf-8") as fh:
                        doc = json.load(fh)
                except FileNotFoundError:
                    # Positively-observed absence → mini not active here.
                    note_disposition("history_write_stalled", "inert",
                                     reason="mini state absent; mini not active here")
                    return None
                except (OSError, ValueError):
                    # N4 (2026-07-18): a PRESENT but unreadable/corrupt state
                    # file on a box actively running mini is unobservable,
                    # not absent — inert here would read "not active" on
                    # exactly the broken box.
                    note_disposition("history_write_stalled", "indeterminate",
                                     reason="mini state file unreadable or corrupt")
                    return None
                state_doc = doc if isinstance(doc, dict) else {}
            if history_mtime is None:
                try:
                    history_mtime = os.stat(
                        os.path.join(home, _MINI_HISTORY_NAME)).st_mtime
                except OSError:
                    history_mtime = 0.0  # absent — fires>0 below makes that a failure

        last_tick = float(state_doc.get("last_tick_ts", 0.0) or 0.0)
        if last_tick <= 0.0 or (now - last_tick) > _MINI_LOOP_FRESH_S:
            note_disposition("history_write_stalled", "indeterminate",
                             reason="mini loop not ticking; write path unobservable")
            return None  # loop not ticking → not a write-failure (daemon stopped)

        # Activity baseline: prefer the engine's history_appends_total counter
        # (advances on EVERY successful history append, edge_up AND edge_down).
        # The old fires-only sum was blind to edge_down-only windows: history
        # appends were failing but per-rule fire_count (edge_up-only) never
        # advanced, so the stall went undetected. Fallback keeps older state
        # files working until their first post-upgrade tick.
        appends_total = state_doc.get("history_appends_total")
        if isinstance(appends_total, (int, float)) and appends_total >= 0:
            fires = int(appends_total)
        else:
            rules = state_doc.get("rules") or {}
            fires = 0
            if isinstance(rules, dict):
                for rs in rules.values():
                    if isinstance(rs, dict):
                        fires += int(rs.get("fire_count", 0) or 0)

        prior = _load_history_stall_state(sp)
        prior_fires = int(prior.get("fires", -1))
        prior_mtime = float(prior.get("hist_mtime", 0.0) or 0.0)
        streak = int(prior.get("streak", 0) or 0)

        # First sighting (no baseline): record + stay silent.
        if prior_fires < 0:
            _save_history_stall_state(sp, fires=fires, hist_mtime=history_mtime,
                                      streak=0)
            note_disposition("history_write_stalled", "indeterminate",
                             reason="no baseline yet; first sighting recorded")
            return None

        # honest_failure_modes #6 — st_mtime is a WALL-CLOCK value; on this
        # RTC-less fleet a backward NTP/fake-hwclock step at boot can drop a
        # freshly-WRITTEN file's mtime BELOW the persisted baseline even though it
        # WAS written this interval. A real write only moves mtime forward, so an
        # EXISTING file (mtime > 0) whose mtime regressed means the CLOCK moved,
        # not that the write stalled. Re-baseline + reset the streak rather than
        # manufacture a stall from a clock artifact (the engine carries the same
        # backward-step guard for fire/grace deltas). The absent-file sentinel
        # (0.0, set above on OSError) is excluded so a genuinely missing/unreadable
        # history file still trips the stall below.
        if 0.0 < history_mtime < prior_mtime:
            _save_history_stall_state(sp, fires=fires, hist_mtime=history_mtime,
                                      streak=0)
            note_disposition("history_write_stalled", "indeterminate",
                             reason="history mtime regressed (clock step); re-baselined")
            return None

        fires_advanced = fires > prior_fires
        history_advanced = history_mtime > prior_mtime
        stalled = fires_advanced and not history_advanced

        if not stalled:
            _save_history_stall_state(sp, fires=fires, hist_mtime=history_mtime,
                                      streak=0)
            note_disposition("history_write_stalled", "clean")
            return None

        streak += 1
        # Persist the new fires baseline but KEEP the frozen mtime so a
        # continuing stall keeps accumulating the streak.
        _save_history_stall_state(sp, fires=fires, hist_mtime=history_mtime,
                                  streak=streak)
        if streak < debounce_ticks:
            note_disposition("history_write_stalled", "indeterminate",
                             reason="stall candidate held by debounce")
            return None

        delta = fires - prior_fires
        return Signal(
            cls="history_write_stalled",
            subject="mini-dudeai",
            severity="degraded",
            detail=(
                f"mini loop is alive (last_tick {int(now - last_tick)}s ago) and "
                f"fired {delta}+ more rule(s) but {_MINI_HISTORY_NAME} stopped "
                f"accumulating (mtime frozen) over {streak} consecutive ticks — a "
                f"swallowed history-write failure. Check the file's perms/owner + "
                f"free disk on the mini host; the loop keeps ticking, the "
                f"warm-context feed is the casualty."
            ),
            issue_ref=79,
            extra={"fires": fires, "delta": delta, "streak": streak,
                   "history_mtime": history_mtime},
        )
    except Exception:
        note_disposition("history_write_stalled", "indeterminate",
                         reason="unexpected probe error")
        return None


# Seed-content provenance (Issue #80 residual): hasher + stamp key come from
# the MERGE WRITER (mini_dudeai.candidate) so writer and reader can never hash
# the same rule differently. No fallback hasher — a duplicated implementation
# is the two-constants drift trap — so when the mini package is absent the
# content-drift leg self-guards off and the ID leg below still works.
try:
    from mini_dudeai.candidate import (
        PROVENANCE_KEY as _MINI_PROVENANCE_KEY,
        rule_body_sha as _mini_rule_body_sha,
    )
except Exception:  # mini package absent in some contexts
    _MINI_PROVENANCE_KEY = None
    _mini_rule_body_sha = None


# Map a deployment.json role → the mini-dudeai role seed it should track.
# Only confident mappings are listed; an unmapped role is AMBIGUOUS (no
# dedicated mini seed) and self-guards None rather than guess a seed.
_ROLE_TO_MINI_SEED = {
    "primary": "federator",       # the :5000 federator/manager box
    "full-gateway": "fleet_gateway",
    "gateway-only": "fleet_gateway",
    # collector (moc5) and cloud-publisher (moc1) both `inherits: full-gateway`
    # in fleet_roles.yaml and run mini (install_noc enrolls the user units on
    # every fleet box) — leaving them unmapped made this probe permanently
    # inert on exactly the boxes most likely to be forgotten in a re-seed.
    "collector": "fleet_gateway",
    "cloud-publisher": "fleet_gateway",
}


def probe_rules_seed_drift(
    *,
    meshforge_root: str = "/opt/meshforge",
    mini_home: Optional[str] = None,
    role: Optional[str] = None,
    live_ids: Optional[set] = None,
    seed_ids: Optional[set] = None,
    live_rules: Optional[list] = None,
    seed_rules: Optional[list] = None,
) -> Optional[Signal]:
    """Surface a live ``mini_dudeai_rules.json`` that fell behind its role seed (Issue #79 + #80 content leg).

    The fleet ships a per-role mini seed (``configs/mini_dudeai_rules.<role>.json``)
    that is the canonical rule set for the box's role; the live file is seeded from
    it then evolves per-box (candidate promotions, MF014 box-local rules). When the
    seed gains a NEW rule (a seed bump for a new failure class) but the live file
    wasn't re-seeded, the box silently misses that rule. This makes the gap a signal.

    Two drift legs, one signal:

    - **missing** (ID leg, #79): the seed carries rule ids the live file lacks.
    - **stale** (content leg, #80 residual): a live rule whose body is an
      UNMODIFIED copy of an OLDER seed body — its ``seed_provenance.seed_sha``
      stamp (written by ``mini_dudeai.candidate.merge_seed_rules``) equals the
      live body hash, but the current seed body hashes differently, i.e. the
      seed TUNED the rule and the box never got the bump. A live rule whose
      body differs from its stamp was box-tuned (legitimate, exempt) and an
      unstamped rule is indeterminate (pre-provenance — exempt; unobservable
      ≠ drift, stamps ratchet in via the merge helper).

    Extra live-only rules are LEGITIMATE (box-local additions) and never fire —
    this is a one-directional "live behind seed" check. Self-guards None: box
    declares no role, the role has no confident mini-seed mapping (ambiguous —
    don't guess), either file is unreadable, or there's no gap. The content leg
    additionally self-guards off when the mini package (the shared hasher) is
    unimportable — no duplicated fallback hasher.
    """
    try:
        if (live_ids is None and live_rules is None) or \
                (seed_ids is None and seed_rules is None):
            # Resolve role (deployment.json) → seed name.
            if role is None:
                try:
                    from utils.rns_tree_perms import _read_rnsd_user
                    service_user = _read_rnsd_user()
                except Exception:
                    service_user = None
                role, _ov = _read_deployment_declaration(service_user)
            if not role:
                note_disposition("rules_seed_drift", "indeterminate",
                                 reason="role unresolvable (deployment.json absent/unreadable)")
                return None  # no declared role → not applicable
            seed_name = _ROLE_TO_MINI_SEED.get(role)
            if not seed_name:
                note_disposition("rules_seed_drift", "inert",
                                 reason="role has no mapped mini seed; not applicable")
                return None  # role has no dedicated mini seed → ambiguous, no guess

            if seed_ids is None and seed_rules is None:
                seed_path = _seed_rules_path(meshforge_root, seed_name)
                try:
                    with open(seed_path, "r", encoding="utf-8") as fh:
                        seed_doc = json.load(fh)
                except (OSError, ValueError):
                    note_disposition("rules_seed_drift", "indeterminate",
                                     reason="role seed file unreadable")
                    return None  # seed unreadable → indeterminate
                seed_rules = [r for r in (seed_doc.get("rules") or [])
                              if isinstance(r, dict) and r.get("id")]

            if live_ids is None and live_rules is None:
                home = mini_home or _resolve_mini_home()
                if not home:
                    note_disposition("rules_seed_drift", "indeterminate",
                                     reason="operator home unresolvable")
                    return None
                try:
                    with open(os.path.join(home, _MINI_RULES_NAME),
                              "r", encoding="utf-8") as fh:
                        live_doc = json.load(fh)
                except FileNotFoundError:
                    # Positively-observed absence → mini not seeded here.
                    note_disposition("rules_seed_drift", "inert",
                                     reason="live rules absent; mini not seeded here")
                    return None
                except (OSError, ValueError):
                    # N4 (2026-07-18): a PRESENT but unreadable/corrupt rules
                    # file is unobservable, not "not seeded" — corrupt rules
                    # on an active mini box must not read as benign absence.
                    note_disposition("rules_seed_drift", "indeterminate",
                                     reason="live rules file unreadable or corrupt")
                    return None
                live_rules = [r for r in (live_doc.get("rules") or [])
                              if isinstance(r, dict) and r.get("id")]

        seed_by_id = {r["id"]: r for r in (seed_rules or [])
                      if isinstance(r, dict) and r.get("id")}
        live_by_id = {r["id"]: r for r in (live_rules or [])
                      if isinstance(r, dict) and r.get("id")}
        if seed_ids is None:
            seed_ids = set(seed_by_id)
        if live_ids is None:
            live_ids = set(live_by_id)

        missing = sorted(seed_ids - live_ids)

        # Content leg — needs full rule bodies AND the shared hasher.
        stale: list = []
        if seed_by_id and live_by_id and _mini_rule_body_sha is not None:
            for rid in sorted(seed_ids & live_ids):
                if rid not in seed_by_id or rid not in live_by_id:
                    continue  # ids injected wider than the rules provided
                live_rule = live_by_id[rid]
                live_sha = _mini_rule_body_sha(live_rule)
                if live_sha == _mini_rule_body_sha(seed_by_id[rid]):
                    continue  # in sync with the current seed
                prov = live_rule.get(_MINI_PROVENANCE_KEY)
                stamp_sha = prov.get("seed_sha") if isinstance(prov, dict) \
                    else None
                if stamp_sha == live_sha:
                    stale.append(rid)  # untouched copy of an older seed body
                # else: box-tuned or unstamped → exempt

        if not missing and not stale:
            if _mini_rule_body_sha is None:
                note_disposition("rules_seed_drift", "indeterminate",
                                 reason="ID leg clean; content leg off (mini pkg absent)")
            else:
                note_disposition("rules_seed_drift", "clean")
            return None  # live is at-or-ahead of the seed → no drift

        parts = []
        if missing:
            shown = ", ".join(missing[:5]) + (
                f" (+{len(missing) - 5} more)" if len(missing) > 5 else "")
            parts.append(
                f"MISSING {len(missing)} rule(s) the role seed carries: {shown}")
        if stale:
            shown = ", ".join(stale[:5]) + (
                f" (+{len(stale) - 5} more)" if len(stale) > 5 else "")
            parts.append(
                f"STALE {len(stale)} seed-owned rule(s) — the seed tuned the "
                f"body but this box still runs the old copy: {shown}")
        return Signal(
            cls="rules_seed_drift",
            subject="mini-dudeai",
            severity="degraded",
            detail=(
                f"live mini rules behind the role seed — {'; '.join(parts)}. "
                f"Review + merge from configs/mini_dudeai_rules.<role>.json via "
                f"mini_dudeai.candidate.merge_seed_rules (box-local extras and "
                f"box-TUNED rules are kept; this is a one-way behind-seed "
                f"check)."
            ),
            issue_ref=79,
            extra={"missing": missing, "stale": stale, "role": role},
        )
    except Exception:
        note_disposition("rules_seed_drift", "indeterminate",
                         reason="unexpected probe error")
        return None


# The operator memory index path (relative to the operator home) + its
# context-load limit. MEMORY.md silently partial-loads when it exceeds this,
# so a bump is a latent legibility failure. The limit derives from the
# WRITER-side guard (mini_dudeai.memory_apply) so the writer's warning and
# this fleet probe can never judge the same file against different numbers —
# they used to (24,000 vs 24,576), leaving a band where the writer warned on
# every append while the fleet stayed silent.
_MEMORY_INDEX_REL = os.path.join(
    ".claude", "projects", "-opt-meshforge", "memory", "MEMORY.md")
try:
    from mini_dudeai.memory_apply import (
        MEMORY_INDEX_SOFT_LIMIT_BYTES as MEMORY_INDEX_LIMIT_BYTES,
    )
except Exception:  # mini package absent in some contexts — keep the same number
    MEMORY_INDEX_LIMIT_BYTES = 24_000


def probe_memory_index_oversize(
    *,
    operator_home: Optional[str] = None,
    size_bytes: Optional[int] = None,
    limit_bytes: int = MEMORY_INDEX_LIMIT_BYTES,
) -> Optional[Signal]:
    """Surface the operator memory index (MEMORY.md) over its load limit (Issue #79).

    The persistent memory store's hot index (``MEMORY.md``) is loaded into every
    session's context; over the ~24 KB harness cap it silently partial-loads, so
    later index lines never reach the assistant. The store itself warns at write
    time, but nothing on the FLEET surface flags a box whose index has crept over.
    This makes it a continuously-monitored signal so the remedy (demote older /
    shipped entries to ``MEMORY_ARCHIVE.md``) gets prompted, not deferred.

    Read-only ``stat`` of the index in the operator home, root-context safe.
    Self-guards None: the index file isn't present on this box (not the
    memory-holding host), or the home can't be resolved. Fires ``degraded`` only
    when the size strictly exceeds the limit.
    """
    try:
        if size_bytes is None:
            home = operator_home or _resolve_mini_home()
            if not home:
                note_disposition("memory_index_oversize", "indeterminate",
                                 reason="operator home unresolvable")
                return None
            path = os.path.join(str(home), _MEMORY_INDEX_REL)
            try:
                size_bytes = os.stat(path).st_size
            except OSError:
                note_disposition("memory_index_oversize", "inert",
                                 reason="MEMORY.md absent; not the memory-holding box")
                return None  # absent / unreadable → not this box, no alarm
        if size_bytes <= limit_bytes:
            note_disposition("memory_index_oversize", "clean")
            return None

        over = size_bytes - limit_bytes
        return Signal(
            cls="memory_index_oversize",
            subject="MEMORY.md",
            severity="degraded",
            detail=(
                f"operator memory index MEMORY.md is {size_bytes} bytes — "
                f"{over} over the ~{limit_bytes // 1024} KB context-load limit, so "
                f"it silently partial-loads (later index lines never reach the "
                f"session). Demote older/shipped entries to MEMORY_ARCHIVE.md "
                f"(keep index lines to one tight hook)."
            ),
            issue_ref=79,
            extra={"size_bytes": size_bytes, "limit_bytes": limit_bytes,
                   "over_bytes": over},
        )
    except Exception:
        note_disposition("memory_index_oversize", "indeterminate",
                         reason="unexpected probe error")
        return None


# Calibration ledger (the calibration spine, 2026-06-15). Read+fold via the
# OWNING module so the probe and the ledger can never compute the fold
# differently (honest_failure_modes #5 — share by import, never duplicate). When
# mini_dudeai isn't importable the leg self-guards off rather than carrying a
# duplicated fold; no two-constants drift trap.
try:
    from mini_dudeai import calibration_ledger as _calib
except Exception:  # mini package absent in some contexts
    _calib = None

_CALIB_LEDGER_NAME = "calibration_ledger.jsonl"

# Recency window for calibration_drift (2026-06-19, self-audit-qa-arc §1): a broke
# claim older than this no longer fires the alert — so the signal can CLEAR once
# I've demonstrably stopped miscalibrating, instead of ONE old broke pinning it
# 'degraded' forever (which would never soak low-false-positive, the documented
# bar for graduating it to an ntfy page, and erodes into ignored noise). The
# lifetime held/broke RATIO stays visible in the warm-brief ledger block
# regardless — this alert reflects "am I CURRENTLY miscalibrating," the brief
# keeps the full history. Env-overridable; default 7 days.
try:
    CALIB_DRIFT_RECENT_WINDOW_S = float(
        os.environ.get("MESHFORGE_CALIB_DRIFT_RECENT_DAYS", "7")) * 86400.0
except (TypeError, ValueError):
    CALIB_DRIFT_RECENT_WINDOW_S = 7 * 86400.0


def probe_calibration_drift(
    *,
    ledger_path: Optional[str] = None,
    events: Optional[list] = None,
    now_ts: Optional[float] = None,
    recent_window_s: Optional[float] = None,
) -> Optional[Signal]:
    """Surface VERIFIED completion claims that did NOT hold on re-derivation.

    The calibration spine records every VERIFIED "done/green" claim and a later
    re-derivation flips it held/broke against external ground truth (a full
    ``honest_status`` verdict on the head it was claimed on). A BROKE claim — I
    called a head verified and a later full run on that head FAILED — is my own
    miscalibration. This probe routes it to the fleet surface so the operator's
    concern ("you say 100%% and we do it N more times, the math is wrong") stops
    being a private impression and becomes a tracked, shrinkable number.

    Read-only fold of the ledger via its owning module (no duplicated fold).
    Self-guards None: mini_dudeai unimportable, no ledger on this box (every box
    but the dev/manager one), unreadable ledger, zero broke claims (the math
    held), or all broke claims aged out of the recency window (recovery
    demonstrated — the lifetime ratio stays in the warm-brief ledger block).
    Severity ``degraded`` — a trust signal, not a service outage; the seed
    routes it side-effect-free (NO ntfy page) until the re-derivation soaks
    low-false-positive, exactly as the operator chose."""
    try:
        if _calib is None:
            note_disposition("calibration_drift", "inert",
                             reason="mini_dudeai unimportable; ledger fold unavailable")
            return None
        if events is None:
            if ledger_path is None:
                home = _resolve_mini_home()
                if not home:
                    note_disposition("calibration_drift", "indeterminate",
                                     reason="operator home unresolvable")
                    return None
                ledger_path = os.path.join(home, _CALIB_LEDGER_NAME)
            if not os.path.exists(ledger_path):
                note_disposition("calibration_drift", "inert",
                                 reason="no calibration ledger; not the dev/manager box")
                return None  # not the dev/manager box — nothing to judge
            events = _calib.load_events(ledger_path)
            # N3 (2026-07-18): load_events maps ANY OSError/garbled content
            # to [] (its never-raises contract) — so an unreadable or
            # wholly-corrupt ledger would fold to "every claim held" and
            # note CLEAN. A non-empty file that yielded zero events was NOT
            # positively observed; only a genuinely empty (0-byte) or
            # absent ledger keeps its benign disposition.
            if not events:
                try:
                    ledger_size = os.path.getsize(ledger_path)
                except OSError:
                    note_disposition(
                        "calibration_drift", "indeterminate",
                        reason="ledger size unreadable — ledger unobservable")
                    return None
                if ledger_size > 0:
                    note_disposition(
                        "calibration_drift", "indeterminate",
                        reason="ledger non-empty but yielded zero events — "
                               "unreadable/garbled")
                    return None
        state = _calib.fold(events)
        broke = state.get("broke", [])
        if not broke:
            note_disposition("calibration_drift", "clean")
            return None  # every re-checked claim held → no calibration drift

        # RECENCY (self-audit-qa-arc §1): fire only on RECENT drift so the alert
        # can CLEAR once recovery is demonstrated, instead of one old broke pinning
        # it 'degraded' forever. Anchor on the CLAIM ts (when I made the claim that
        # turned out wrong). honest_failure_modes #6: ts is wall-clock, but the
        # ledger lives on the dev/manager box (NTP-up during the interactive
        # sessions that make claims) so it's far less boot-churn-exposed than a
        # fleet probe — and a negative age (clock stepped back) OR a missing ts is
        # treated as RECENT (fire), never a false-clear (the safe direction).
        now = time.time() if now_ts is None else now_ts
        window = (CALIB_DRIFT_RECENT_WINDOW_S if recent_window_s is None
                  else recent_window_s)

        def _is_recent(rec):
            ts = rec.get("ts")
            if not isinstance(ts, (int, float)):
                return True  # malformed/synthetic ts → conservative: fire
            return (now - ts) <= window  # negative age (clock back) → recent

        recent = [r for r in broke if _is_recent(r)]
        if not recent:
            note_disposition("calibration_drift", "clean")
            return None  # all breaks aged out → recovery demonstrated; the
            #              lifetime ratio stays in the warm-brief ledger block
        ratio = state.get("ratio")
        ratio_disp = (f"{round(100 * ratio)}% held"
                      if isinstance(ratio, (int, float)) else "n/a")
        examples = "; ".join(
            (r.get("claim_text") or "")[:50] for r in recent[:2])
        n_recent = len(recent)
        return Signal(
            cls="calibration_drift",
            subject="claude-claims",
            severity="degraded",
            detail=(
                f"{n_recent} recent VERIFIED completion claim(s) did NOT hold on "
                f"re-derivation ({state['n_held']} held / {ratio_disp} lifetime) "
                f"— a 'done/verified' claim drifted from ground truth. e.g.: "
                f"{examples}. See .claude/rules/calibrated_claims.md; treat "
                f"completion claims with extra scrutiny."
            ),
            extra={"n_broke": state["n_broke"], "n_recent": n_recent,
                   "n_held": state["n_held"],
                   "n_total": state.get("n_total", 0), "ratio": ratio},
        )
    except Exception:
        note_disposition("calibration_drift", "indeterminate",
                         reason="ledger unreadable or unexpected probe error")
        return None


