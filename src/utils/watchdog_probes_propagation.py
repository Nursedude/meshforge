"""Watchdog probe — LXMF propagation store-and-forward soak (slice 3).

Split out of ``watchdog_probes_gateway_flow.py`` 2026-07-23 (MF025 size cap —
the never-published escalation leg pushed it past 1,500 lines). One-way
dependency: this module imports the shared soak helpers FROM gateway_flow;
nothing here is imported back by it. Import via the ``utils.watchdog_probes``
hub; ``watchdog_probes_gateway`` re-exports for back-compat.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from utils.watchdog_probe_core import Signal, note_disposition
from utils.watchdog_probes_gateway_flow import (
    DEFAULT_PROPAGATION_SOAK_DEBOUNCE_PATH,
    DEFAULT_PROPAGATION_SOAK_INDET_PATH,
    _PROPAGATION_SOAK_STALE_AFTER_S,
    _load_synth_streak,
    _newest_synth_file,
    _resolve_propagation_soak_dir,
    _save_synth_streak,
)

logger = logging.getLogger("watchdog")

# -----------------------------------------------------------------------
# Probe: propagation soak degraded / silent (2026-07-21, slice 3)
# -----------------------------------------------------------------------


def _worst_propagation_round(round_results) -> Optional[str]:
    """Compact ``round N failed at <stage>: <reason>`` for the first failure.

    Tolerates any shape - a summary helper must never crash the probe that
    calls it.
    """
    if not isinstance(round_results, list):
        return None
    fallback = None
    for r in round_results:
        if not isinstance(r, dict):
            continue
        if r.get("ok") is False:
            line = (f"round {r.get('seq')} failed at "
                    f"{r.get('stage') or '?'}: {r.get('reason') or 'unknown'}")
            # Prefer the DEFINITIVE failure over an indeterminate stall (F7);
            # lockstep with worst_round() in lab/lxmf_propagation_soak.py.
            if r.get("stage") not in ("send", "pull_link"):
                return line
            if fallback is None:
                fallback = line
    return fallback


def _bump_prop_indet_streak(state_path: str, newest_name: str) -> int:
    """Advance the consecutive-indeterminate ENVELOPE streak.

    Keyed by envelope filename: a new indeterminate envelope increments once;
    re-reading the same envelope on later ticks returns the stored count
    unchanged. Best-effort I/O — an unwritable path returns the increment for
    THIS tick (so the leg degrades to slower detection, never to silence) and
    leaves a log witness.
    """
    last, streak = None, 0
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        last = st.get("last_file")
        streak = max(int(st.get("streak", 0)), 0)
    except (OSError, ValueError, TypeError):
        pass
    if newest_name == last:
        return streak
    streak += 1
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"last_file": newest_name, "streak": streak},
                      fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError as e:
        logger.warning("propagation indeterminate-streak state unwritable "
                       "(%s): %s", state_path, e)
    return streak


def _reset_prop_indet_streak(state_path: str) -> None:
    try:
        os.remove(state_path)
    except OSError:
        pass


def probe_propagation_soak_degraded(
    *,
    state_dir: Optional[str] = None,
    now: Optional[float] = None,
    stale_after_s: float = _PROPAGATION_SOAK_STALE_AFTER_S,
    debounce_path: Optional[str] = None,
    debounce_ticks: int = 2,
    home: Optional[str] = None,
    configured_fn=None,
    indeterminate_after: int = 6,
    indet_state_path: Optional[str] = None,
    never_published_after_s: float = 3 * 3600.0,
) -> Optional[Signal]:
    """The configured propagation node stopped STORING AND FORWARDING.

    The gap this closes: ``probe_lxmf_propagation_node_dark`` watches whether
    the configured node ANNOUNCES. A node that announces perfectly while
    silently dropping every stored message reads clean on that probe forever -
    announce-liveness standing in as a proxy for the property we actually care
    about. In a small lab, traffic to an offline peer essentially never happens
    organically, so the realistic failure is that the organ is quietly useless
    for months while every gate stays green.

    ``meshforge-propagation-soak.timer`` -> ``lab_propagation_soak_fire.sh``
    manufactures that traffic hourly: a receiver that has never announced (so
    no direct path can exist) is sent a PROPAGATED message, then comes up and
    pulls it back. This probe consumes the resulting envelope.

    Two legs, ``degraded`` only - offline-peer delivery is impaired while LIVE
    delivery is unaffected, and ``gateway_delivery_degraded`` /
    ``delivery_confirmation_stall`` own the hard-failure surface:

      - SILENCE: newest ``prop-*.json`` older than ``stale_after_s`` (~2.5x the
        hourly cadence) - the exerciser stopped. For a fixed-cadence generator
        silence IS the failure, exactly as for the synth soak.
      - ENVELOPE: newest result has ``pass_envelope`` false - a message was
        accepted by the node and never came back, or was never accepted.

    Honest-failure self-guards (favour silence on uncertainty):
      - operator unresolvable -> indeterminate (NOT "box doesn't run it": on a
        soak-running box that early-boot window must not read as inert).
      - state dir absent -> INERT (this box doesn't run the drill - the common
        case, including every box with no ``propagation_node`` configured,
        since the fire script publishes nothing there).
      - no ``prop-*.json`` yet -> indeterminate, streak HELD (freshly installed).
      - newest unreadable/garbage -> a candidate, but ridden out by the debounce
        so a torn mid-write file cannot page.
      - ``pass_envelope`` absent on a parseable fresh file -> indeterminate; a
        shape regression must never read as healthy.
      - only an explicit healthy+fresh observation resets the streak.
    """
    import time as _time
    now = _time.time() if now is None else now

    # Intent gate FIRST (review F3): the drill only means something on a box
    # that has ADOPTED a propagation node. Gating on the state dir alone was
    # false in both directions — the fire script's old unconditional mkdir
    # created the dir on unconfigured boxes (permanent indeterminate), and a
    # box de-adopted later kept a dir of aging envelopes (false silence page).
    if configured_fn is None:
        def configured_fn():
            from utils.paths import get_real_user_home
            from utils.watchdog_probes_gateway import (
                _read_configured_propagation_node)
            h = home or str(get_real_user_home())
            return _read_configured_propagation_node(h)
    try:
        cfg_val, cfg_state = configured_fn()
    except Exception:                      # noqa: BLE001 - never crash a probe
        cfg_val, cfg_state = None, "unreadable"
    if cfg_state == "absent" or (cfg_state == "ok"
                                 and not str(cfg_val or "").strip()):
        note_disposition(
            "propagation_soak_degraded", "inert",
            reason="no propagation node adopted - nothing to exercise")
        return None
    if cfg_state != "ok":
        note_disposition("propagation_soak_degraded", "indeterminate",
                         reason="gateway.json unreadable - intent unknown")
        return None

    sdir = state_dir or _resolve_propagation_soak_dir()
    if not sdir:
        note_disposition("propagation_soak_degraded", "indeterminate",
                         reason="operator unresolvable - state dir unknown")
        return None
    if not os.path.isdir(sdir):
        note_disposition(
            "propagation_soak_degraded", "inert",
            reason="propagation-soak state dir absent - box doesn't run the drill")
        return None

    sp = debounce_path or DEFAULT_PROPAGATION_SOAK_DEBOUNCE_PATH

    newest = _newest_synth_file(sdir, prefix="prop-")
    if newest is None:
        # Day-one-broken guard (07-23 audit): a drill that has NEVER published
        # an envelope (typo'd hash -> rc 4, broken python env from install
        # day) held indeterminate forever — an absorbing state with no
        # escalation, while the fire script exits 0 and its FAIL verdict is
        # unwired. Anchor the first no-result sighting and escalate once the
        # silence exceeds ~3 drill cadences. State rides the indet file
        # (best-effort: unwritable -> this stays a held-indeterminate, the
        # pre-fix behavior, with a log witness from the bump helper's twin).
        ipath = indet_state_path or DEFAULT_PROPAGATION_SOAK_INDET_PATH
        since = None
        try:
            with open(ipath, "r", encoding="utf-8") as fh:
                since = json.load(fh).get("no_result_since")
        except (OSError, ValueError, TypeError, AttributeError):
            since = None
        if not isinstance(since, (int, float)) or since > now:
            # first sighting (or a clock-backward stamp — re-anchor, never a
            # negative age): record and hold.
            try:
                parent = os.path.dirname(ipath)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                tmp = ipath + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"no_result_since": now}, fh,
                              separators=(",", ":"))
                os.replace(tmp, ipath)
            except OSError:
                pass
            note_disposition("propagation_soak_degraded", "indeterminate",
                             reason="no drill result file yet / listing race - held")
            return None
        silent_for = now - since
        if silent_for <= never_published_after_s:
            note_disposition("propagation_soak_degraded", "indeterminate",
                             reason="no drill result file yet / listing race - held")
            return None
        return Signal(
            cls="propagation_soak_degraded",
            subject="propagation-drill",
            severity="degraded",
            detail=(
                f"LXMF store-and-forward drill has NEVER published a result "
                f"envelope in {silent_for / 3600.0:.1f}h despite its state "
                f"dir existing — a day-one-broken drill (typo'd node hash, "
                f"broken python env) is indistinguishable from a healthy "
                f"not-yet-fired one only briefly; this long is broken. Run "
                f"the fire script by hand and check its fire.log: "
                f"systemctl --user status meshforge-propagation-soak.timer"),
            issue_ref=None,
            extra={"state_dir": sdir,
                   "no_result_for_s": round(silent_for, 1),
                   "leg": "never_published"},
        )

    newest_path, newest_mtime = newest
    age = now - newest_mtime
    extra: dict = {"newest": os.path.basename(newest_path), "age_s": round(age, 1)}

    candidate_detail: Optional[str] = None
    definitively_healthy = False
    held_reason: Optional[str] = None

    if age > stale_after_s:
        candidate_detail = (
            f"LXMF store-and-forward drill went DARK: newest result is "
            f"{age / 3600.0:.1f}h old (cadence ~1h) - the exerciser stopped "
            f"producing output, so nothing is proving the propagation node "
            f"still stores and forwards. Check "
            f"meshforge-propagation-soak.timer (systemd --user; needs linger) "
            f"and its fire.log."
        )
    else:
        try:
            with open(newest_path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            candidate_detail = (
                f"propagation drill newest result unreadable "
                f"({os.path.basename(newest_path)}) - the run wrote no "
                f"parseable envelope."
            )
        elif doc.get("pass_envelope") is True:
            definitively_healthy = True
            _reset_prop_indet_streak(
                indet_state_path or DEFAULT_PROPAGATION_SOAK_INDET_PATH)
        elif "pass_envelope" in doc and doc.get("pass_envelope") is None:
            # An explicit-null envelope is an honest "couldn't test" — but a
            # RUN of them is an absorbing state (review F2): a node refusing
            # every upload, a box that can never finish a stamp, or a dead
            # pull path all read indeterminate forever while every dashboard
            # stays green, and the SILENCE leg never trips because envelopes
            # keep publishing. Escalate once the drill has produced
            # ``indeterminate_after`` consecutive unproven fires.
            env_streak = _bump_prop_indet_streak(
                indet_state_path or DEFAULT_PROPAGATION_SOAK_INDET_PATH,
                os.path.basename(newest_path))
            extra["indeterminate_envelopes"] = env_streak
            # Under the escalation streak this tick stays indeterminate, but
            # with ITS OWN reason: an explicitly-null envelope is an unproven
            # round, not a missing key (the shape-bug case below).
            held_reason = (f"pass_envelope null — unproven round, streak "
                           f"{env_streak}/{indeterminate_after}")
            if env_streak >= indeterminate_after:
                worst = _worst_propagation_round(doc.get("round_results"))
                candidate_detail = (
                    f"LXMF store-and-forward drill INDETERMINATE for "
                    f"{env_streak} consecutive fires (~{env_streak}h) - the "
                    f"drill is running but has not PROVEN store-and-forward "
                    f"in any of them"
                    + (f". Latest: {worst}" if worst else "")
                    + ". Chronic send-stalls (slow stamp / overloaded box), "
                    "a node refusing uploads, or a dead pull path all look "
                    "like this. Check the fire.log round reasons, the box's "
                    "load, and lxmd on the node's host."
                )
        elif doc.get("pass_envelope") is False:
            _reset_prop_indet_streak(
                indet_state_path or DEFAULT_PROPAGATION_SOAK_INDET_PATH)
            total_ok = doc.get("total_ok")
            total = doc.get("total_samples")
            node = doc.get("propagation_node")
            worst = _worst_propagation_round(doc.get("round_results"))
            extra.update({
                "total_ok": total_ok, "total_samples": total,
                "propagation_node": node, "worst_round": worst,
            })
            candidate_detail = (
                f"LXMF store-and-forward FAILED against the configured "
                f"propagation node {str(node)[:16]}: {total_ok}/{total} "
                f"round(s) completed"
                + (f". {worst}" if worst else "")
                + ". Messages to an OFFLINE peer are not being stored and "
                "returned - live delivery is unaffected, but the propagation "
                "organ is not doing its job. Check lxmd on its host "
                "(`lxmd --status`: messagestore + 'served to clients'), then "
                "RNS paths to the node."
            )
        # else: pass_envelope KEY ABSENT (shape bug) -> indeterminate (held
        # below); the fire-script verdict FAIL owns paging that regression.

    if candidate_detail is not None:
        streak = _load_synth_streak(sp) + 1
        _save_synth_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition("propagation_soak_degraded", "indeterminate",
                             reason="degraded candidate held by debounce")
            return None
        return Signal(
            cls="propagation_soak_degraded",
            subject="lxmf-propagation",
            severity="degraded",
            detail=candidate_detail,
            extra=extra,
        )

    if definitively_healthy:
        _save_synth_streak(sp, 0)
        note_disposition("propagation_soak_degraded", "clean")
    else:
        note_disposition(
            "propagation_soak_degraded", "indeterminate",
            reason=held_reason or "pass_envelope absent on parseable envelope")
    return None
