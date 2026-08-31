#!/usr/bin/env python3
"""Capture the F2 post-flash ``direct=`` snapshot — exactly once, over a window
comparable to the pre-flash baseline.

WHY THIS EXISTS. The claws' ``direct=`` counters live only in
``claw_last_tick*.json``, which the ``*/5`` metrics cron OVERWRITES every tick.
So the F2 before/after measurement has a window that can be lost in silence —
by nobody being at a terminal at the right hour, or by any claw rebooting and
resetting its counters to zero. Losing it costs another ~21 h to re-earn, and
nothing in the fleet would have said so.

Runs on the claw-brain box from the operator crontab, wired to the cron-verdict
regime so every run leaves a line whether or not it captured anything:

    */30 * * * * PYTHONPATH=/opt/meshforge/src python3 /opt/meshforge/scripts/claw_direct_snapshot.py --notify >"$HOME/.local/state/meshforge/cron_out/claw_direct_snapshot.out" 2>&1; /opt/meshforge/scripts/cron_verdict.sh claw_direct_snapshot $?

``--notify`` pages the fleet ntfy channel on a TRANSITION into captured /
window_lost / unobservable -- never on ``waiting`` (it persists ~21 h). The
page carries the F2 delta in its body, so the headline arrives on the phone
rather than only in the verdict log.

HONESTY CONTRACT — five outcomes, never collapsed into one another
(honest_failure_modes #1/#2: a degraded read must not land inside the healthy
domain, and "unobservable" is a different claim from "not yet"):

    waiting           a claw has not reached its baseline window yet.   exit 0
    captured          every claw met its window; snapshot written ONCE. exit 0
    already_captured  the snapshot exists; idempotent no-op.            exit 0
    window_lost       a claw REBOOTED: counters reset, window restarts. exit 1
    unobservable      a tick or the baseline is missing/stale/garbled.  exit 1

``waiting`` is exit 0 deliberately — it is a legitimate state that persists for
~21 h and must not page for a day. ``window_lost`` and ``unobservable`` are
LOUD, because each means the measurement will not happen on its own.

Identity comes from each tick's own ``device`` field, never from the filename.
The host->claw mapping is counterintuitive by measurement (moc2 holds claw-01's
DEFAULT tick basename while claw-03 is the board attached to it), and guessing
it is precisely the error the flash cycle's step 0 exists to prevent.

Re-capturing is deliberate, not a flag: move the existing snapshot aside first.
A ``--force`` that clobbers the only copy of an irreplaceable 21 h window is the
defect this script exists to prevent.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai._util import atomic_write_json  # noqa: E402
from mini_dudeai.claw_telemetry import (  # noqa: E402
    CLAW_TICK_BASENAME,
    SECONDARY_TICK_GLOB,
)
from mini_dudeai.rollup import CLAW_STALE_S  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402

#: Fixed name, NOT timestamped: a fixed name is what makes "exactly once"
#: enforceable by O_EXCL. The capture instant is recorded inside as taken_utc.
SNAPSHOT_BASENAME = "claw_direct_snapshot_post_dudeclaw20.json"
BASELINE_GLOB = "claw_direct_snapshot_pre_dudeclaw20_*.json"
STATE_BASENAME = "claw_direct_snapshot_state.json"

#: Outcomes, in the order they are checked. Order matters: blindness is decided
#: BEFORE readiness, so a snapshot is never assembled from a partial fleet.
OUT_UNOBSERVABLE = "unobservable"
OUT_WINDOW_LOST = "window_lost"
OUT_ALREADY = "already_captured"
OUT_WAITING = "waiting"
OUT_CAPTURED = "captured"

_LOUD = (OUT_UNOBSERVABLE, OUT_WINDOW_LOST)

#: Outcomes worth a page, with (priority, tags). `waiting` never pages — it
#: persists ~21 h — and `already_captured` never pages because the thing it
#: reports already paged once. Paging is keyed to the TRANSITION, not the run,
#: so a state that persists for a day does not send 48 notifications.
_NOTIFY = {
    OUT_CAPTURED: ("default", "chart_with_upwards_trend"),
    OUT_WINDOW_LOST: ("high", "warning"),
    OUT_UNOBSERVABLE: ("high", "warning"),
}


def _state_path(home: str) -> str:
    return os.path.join(home, ".local", "state", "meshforge", STATE_BASENAME)


def notify(outcome: str, lines: List[str]) -> Optional[str]:
    """Page the fleet ntfy channel through the SSOT helper.

    Returns a witness line to print, or None when nothing was sent. Best-effort
    by contract: a paging failure never changes the outcome, because the outcome
    is the truth and the page is only its delivery. But every swallow leaves a
    line in the cron output (honest_failure_modes #9) — a page that silently
    failed to send is exactly the silence this whole script exists to prevent.

    ⚠️ fleet_ntfy_push.sh is best-effort and always exits 0, so a successful
    call here is NOT proof of delivery. The device leg is proven only by the
    operator seeing the notification (harness_map: tap-to-ack is the only
    device-leg proof).
    """
    spec = _NOTIFY.get(outcome)
    if spec is None:
        return None
    priority, tags = spec
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fleet_ntfy_push.sh")
    if not os.path.exists(helper):
        return "NOT PAGED: %s missing — this run is unannounced" % helper
    body = "\n".join(lines)[:1400]
    try:
        subprocess.run([helper, "claw F2 window: %s" % outcome, priority,
                        tags, body], timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return "NOT PAGED: %s — this run is unannounced" % exc
    return "paged ntfy (%s/%s) — delivery unproven until seen" % (priority, tags)


def load_state(home: str) -> Dict[str, Any]:
    """Watermarks for reboot detection. A garbled state file is NOT fatal — it
    only costs us reboot detection until it is rewritten — but it must never be
    silently read as "no reboot ever happened", so it is reported by the caller.
    """
    try:
        with open(_state_path(home), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        # No "first_run" marker: it would be written back into the state file
        # and read true forever after, which is a field that lies from run 2 on.
        return {"claws": {}, "state_readable": True}
    except (OSError, ValueError) as exc:
        return {"claws": {}, "state_readable": False, "state_error": str(exc)}
    if not isinstance(data, dict) or not isinstance(data.get("claws"), dict):
        return {"claws": {}, "state_readable": False,
                "state_error": "state file is not the expected shape"}
    data["state_readable"] = True
    return data


def load_baseline(home: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (baseline, error). The per-claw window targets come FROM the
    baseline — there is no default. Inventing one would let a short window pass
    as comparable, which is the whole defect class this measurement is about.
    """
    hits = sorted(glob.glob(os.path.join(home, BASELINE_GLOB)))
    if not hits:
        return None, "no pre-flash baseline found (%s)" % BASELINE_GLOB
    if len(hits) > 1:
        return None, ("%d baselines match %s — ambiguous, refusing to guess: %s"
                      % (len(hits), BASELINE_GLOB,
                         ", ".join(os.path.basename(h) for h in hits)))
    try:
        with open(hits[0], "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "baseline unreadable (%s): %s" % (hits[0], exc)
    claws = data.get("claws")
    if not isinstance(claws, dict) or not claws:
        return None, "baseline %s carries no claws block" % hits[0]
    data["_path"] = hits[0]
    return data, None


def read_ticks(home: str, now: float) -> Tuple[Dict[str, Any], List[str]]:
    """Read every claw tick on this box, keyed by the tick's OWN device field.

    Returns (ticks, problems). A stale or garbled tick becomes a problem, never
    an absent claw — absence of evidence is not evidence of absence (#2).
    """
    paths = [os.path.join(home, CLAW_TICK_BASENAME)]
    paths += sorted(glob.glob(os.path.join(home, SECONDARY_TICK_GLOB)))

    ticks: Dict[str, Any] = {}
    problems: List[str] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                tick = json.load(fh)
        except FileNotFoundError:
            if os.path.basename(path) == CLAW_TICK_BASENAME:
                continue  # a box may legitimately host only secondaries
            problems.append("%s vanished mid-read" % os.path.basename(path))
            continue
        except (OSError, ValueError) as exc:
            problems.append("%s unreadable: %s" % (os.path.basename(path), exc))
            continue

        device = tick.get("device") if isinstance(tick, dict) else None
        if not device:
            problems.append("%s carries no device field — cannot attribute it"
                            % os.path.basename(path))
            continue

        age = now - (tick.get("captured_at") or 0)
        if age > CLAW_STALE_S:
            problems.append("%s tick is STALE (%.0fs > %.0fs) — the capture cron "
                            "may be dead; not treating it as current"
                            % (device, age, CLAW_STALE_S))
            continue
        if age < -CLAW_STALE_S:
            problems.append("%s tick is stamped in the FUTURE (%.0fs) — clock "
                            "skew; refusing to trust it" % (device, -age))
            continue
        if not tick.get("ok") or not (tick.get("device_info") or {}).get("uptime_s"):
            problems.append("%s did not answer at last capture (ok=%r) — its "
                            "counters are unknown, not zero"
                            % (device, tick.get("ok")))
            continue
        ticks[device] = tick
    return ticks, problems


def detect_reboots(ticks: Dict[str, Any],
                   state: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """A claw whose uptime went DOWN rebooted and reset its counters.

    uptime_s is device-side monotonic, so a decrease is a reboot regardless of
    what this host's wall clock did — deliberately not derived from timestamps,
    which are forgeable on an RTC-less fleet (honest_failure_modes #6).
    """
    prior = state.get("claws") or {}
    rebooted: List[str] = []
    fresh: Dict[str, Any] = {}
    for device, tick in sorted(ticks.items()):
        uptime = (tick.get("device_info") or {}).get("uptime_s") or 0
        seen = (prior.get(device) or {}).get("uptime_s")
        if isinstance(seen, (int, float)) and uptime < seen:
            rebooted.append("%s rebooted (uptime %ss < %ss seen before) — its "
                            "direct= counters reset to zero" % (device, uptime, seen))
        fresh[device] = {"uptime_s": uptime,
                         "version": (tick.get("device_info") or {}).get("version")}
    return rebooted, fresh


def readiness(ticks: Dict[str, Any],
              baseline: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Split claws into (ready, waiting-with-remaining). Each claw is measured
    against ITS OWN baseline window, so a claw is only ever compared to itself.
    """
    ready: List[str] = []
    waiting: List[str] = []
    for device, base in sorted((baseline.get("claws") or {}).items()):
        tick = ticks.get(device)
        if tick is None:
            # Unreachable in practice: missing ticks are raised as blindness
            # before readiness is consulted. Kept as a fail-closed guard so a
            # future caller cannot reach the snapshot with a claw missing.
            waiting.append("%s: no current tick" % device)
            continue
        target = base.get("accumulation_window_s")
        uptime = (tick.get("device_info") or {}).get("uptime_s") or 0
        if not isinstance(target, (int, float)) or target <= 0:
            waiting.append("%s: baseline carries no usable window target" % device)
            continue
        if uptime >= target:
            ready.append(device)
        else:
            waiting.append("%s: %.1fh of %.1fh (%.1fh to go)"
                           % (device, uptime / 3600.0, target / 3600.0,
                              (target - uptime) / 3600.0))
    return ready, waiting


def _direct_true(block: Any) -> set:
    if not isinstance(block, dict):
        return set()
    return {node for node, val in block.items()
            if isinstance(val, dict) and val.get("direct") is True}


def _heard(watched: Any, node: str) -> Optional[bool]:
    """Did this claw hear `node` in this window? None = cannot tell.

    Tri-state on purpose. "Not heard" and "heard but no longer direct" are
    different claims about F2, and an unknown must not be rounded to either.
    """
    entry = (watched or {}).get(node) if isinstance(watched, dict) else None
    if not isinstance(entry, dict):
        return None
    if entry.get("never") is True:
        return False
    pkts = entry.get("pkts")
    if entry.get("never") is False and isinstance(pkts, (int, float)):
        return pkts > 0
    return None


def build_delta(baseline: Dict[str, Any],
                claws: Dict[str, Any]) -> Dict[str, Any]:
    """Which watched nodes stopped earning direct= — F2's whole question.

    ⚠️ A node can leave the direct-true set for THREE unrelated reasons, and
    only one of them is about F2:

      * F2 refused to call it direct       -> the signal
      * the claw never heard it this window -> says NOTHING about F2
      * it left the watch list entirely     -> not comparable at all

    Collapsing those into one "lost_direct" bucket would put a claim on the
    operator's phone that the data does not support — the degraded-value-
    inside-the-healthy-domain class this whole script exists to guard (#1/#2).
    So they are separate fields, and only `lost_direct_heard` is evidence.

    Reported as node ids, never a bare count: the three claws hear different
    traffic on different segments, so even the signal bucket is evidence, not
    proof, and the reader needs to see WHICH nodes moved.
    """
    delta: Dict[str, Any] = {}
    for device, cur in sorted(claws.items()):
        base = (baseline.get("claws") or {}).get(device) or {}
        was = _direct_true(base.get("direct"))
        now = _direct_true(cur.get("direct"))
        cur_watched = cur.get("watched") or {}
        base_watch = set((base.get("watched") or {}).keys())
        cur_watch = set(cur_watched.keys())

        signal, unheard, unknown, dropped = [], [], [], []
        for node in sorted(was - now):
            if node not in cur_watch:
                dropped.append(node)
                continue
            state = _heard(cur_watched, node)
            if state is True:
                signal.append(node)
            elif state is False:
                unheard.append(node)
            else:
                unknown.append(node)

        delta[device] = {
            "version_before": base.get("version"),
            "version_after": cur.get("version"),
            # The F2 signal: still watched, still heard, no longer direct.
            "lost_direct_heard": signal,
            # Not evidence — the node went quiet, or we cannot tell.
            "lost_direct_unheard": unheard,
            "lost_direct_unknown": unknown,
            # Not comparable — the watch list itself moved.
            "dropped_from_watch": dropped,
            "kept_direct": sorted(was & now),
            "gained_direct": sorted(now - was),
            "watch_set_changed": sorted(base_watch) != sorted(cur_watch),
            "watch_added": sorted(cur_watch - base_watch),
            "watch_removed": sorted(base_watch - cur_watch),
            "window_before_s": base.get("accumulation_window_s"),
            "window_after_s": cur.get("accumulation_window_s"),
        }
    return delta


def build_snapshot(ticks: Dict[str, Any], baseline: Dict[str, Any],
                   now: float) -> Dict[str, Any]:
    claws: Dict[str, Any] = {}
    for device, tick in sorted(ticks.items()):
        lora = tick.get("lora") or {}
        info = tick.get("device_info") or {}
        claws[device] = {
            "accumulation_window_s": info.get("uptime_s"),
            "uptime_s": info.get("uptime_s"),
            "version": info.get("version"),
            "direct": lora.get("direct") or {},
            "watched": lora.get("watched") or {},
            "heard_pkts": lora.get("heard_pkts"),
            "last_hops": lora.get("last_hops"),
            "stats_truncated": lora.get("stats_truncated"),
        }
    return {
        "claws": claws,
        "purpose": ("F2 after-flash capture — (0,0) no longer counts as DIRECT "
                    "in +dudeclaw.20; compare against the pre-flash baseline"),
        "taken_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "baseline_path": baseline.get("_path"),
        "warning": ("claw counters RESET AT BOOT. Every claw here met or "
                    "exceeded its own baseline window; claws hear different "
                    "traffic on different segments, so a drop is evidence, "
                    "not proof."),
        "delta_vs_baseline": build_delta(baseline, claws),
    }


def write_once(path: str, payload: Dict[str, Any]) -> bool:
    """Reserve atomically. Returns False if a snapshot already exists.

    O_CREAT|O_EXCL, never check-then-write: the check-then-write race is how a
    second run silently destroys an irreplaceable window.
    """
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return True


def render_delta(delta: Dict[str, Any]) -> List[str]:
    """Lead with the F2 signal; keep the non-evidence buckets visible and
    clearly labelled rather than folded into a healthier-looking summary (#5).
    """
    lines = []
    for device, d in sorted(delta.items()):
        sig = d.get("lost_direct_heard") or []
        lines.append("  %-14s %s -> %s | kept %d, gained %d, F2-LOST %d%s"
                     % (device, d.get("version_before"), d.get("version_after"),
                        len(d.get("kept_direct") or []),
                        len(d.get("gained_direct") or []),
                        len(sig), (" " + ",".join(sig)) if sig else ""))
        for field, label in (("lost_direct_unheard", "not heard this window"),
                             ("lost_direct_unknown", "heard-state unknown"),
                             ("dropped_from_watch", "left the watch list")):
            vals = d.get(field) or []
            if vals:
                lines.append("      not evidence (%s): %s"
                             % (label, ",".join(vals)))
        if d.get("watch_set_changed"):
            lines.append("      WATCH SET MOVED since baseline (+%s / -%s) — "
                         "this claw is no longer a clean comparison"
                         % (",".join(d.get("watch_added") or []) or "none",
                            ",".join(d.get("watch_removed") or []) or "none"))
    return lines


def maybe_notify(home: str, outcome: str, lines: List[str],
                 enabled: bool) -> Optional[str]:
    """Page on a TRANSITION into a notifiable outcome, never on every run.

    `waiting` clears the marker, so a later slide back into `unobservable` is a
    NEW episode and pages again — a second blindness after a recovery is not
    the same event as the first, and swallowing it would be the very
    absence-read-as-continuity the checklist warns about (#2).
    """
    if not enabled:
        return None
    state = load_state(home)
    last = state.get("last_notified_outcome")

    if outcome == OUT_WAITING:
        new_marker = None
    elif outcome == OUT_ALREADY:
        new_marker = last  # carry it; the capture already paged
    else:
        new_marker = outcome

    witness = None
    if outcome in _NOTIFY and outcome != last:
        witness = notify(outcome, lines)

    if new_marker != last:
        state["last_notified_outcome"] = new_marker
        try:
            os.makedirs(os.path.dirname(_state_path(home)), exist_ok=True)
            atomic_write_json(_state_path(home), state)
        except OSError as exc:
            return ((witness or "") +
                    " | could not persist the paging marker: %s "
                    "(a repeat page is possible)" % exc).strip(" |")
    return witness


def run(home: str, now: float) -> Tuple[str, List[str]]:
    """Return (outcome, report lines). Pure enough to test: everything that
    varies is passed in.
    """
    out_path = os.path.join(home, SNAPSHOT_BASENAME)
    lines: List[str] = []

    baseline, base_err = load_baseline(home)
    if baseline is None:
        return OUT_UNOBSERVABLE, ["baseline: %s" % base_err]

    state = load_state(home)
    if not state.get("state_readable"):
        lines.append("state file unusable (%s) — reboot detection is blind this "
                     "run, and that is a gap, not a clean bill"
                     % state.get("state_error"))

    ticks, problems = read_ticks(home, now)

    # A baselined claw with no tick AT ALL is blindness, not "not yet". Without
    # this, a box that cannot see the claws would report `waiting` forever —
    # a failed observation wearing the costume of a legitimate pending state.
    for device in sorted((baseline.get("claws") or {}).keys()):
        if device in ticks:
            continue
        if any(device in p for p in problems):
            continue  # already explained (stale / garbled / did-not-answer)
        problems.append("%s: no tick file on this box at all — this box cannot "
                        "observe that claw" % device)

    rebooted, fresh = detect_reboots(ticks, state)

    # Persist watermarks BEFORE deciding, so a reboot is reported exactly once:
    # the next run compares against the new, lower uptime and sees plain growth.
    state["claws"] = fresh
    state["last_run_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    try:
        os.makedirs(os.path.dirname(_state_path(home)), exist_ok=True)
        atomic_write_json(_state_path(home), state)
    except OSError as exc:
        lines.append("could not persist watermarks: %s — a reboot may be "
                     "reported twice or not at all" % exc)

    if os.path.exists(out_path):
        lines.append("snapshot already exists: %s" % out_path)
        lines.append("re-capturing is deliberate: move that file aside first.")
        return OUT_ALREADY, lines

    if rebooted:
        lines.extend(rebooted)
        lines.append("the comparable window has RESTARTED for the claw(s) above; "
                     "the measurement now needs another full baseline window.")
        return OUT_WINDOW_LOST, lines

    if problems:
        lines.extend(problems)
        lines.append("refusing to snapshot a partial fleet — a snapshot missing "
                     "a claw would read as that claw having no direct links.")
        return OUT_UNOBSERVABLE, lines

    ready, waiting = readiness(ticks, baseline)
    if waiting:
        lines.append("waiting for a comparable window (%d of %d claws ready):"
                     % (len(ready), len(ready) + len(waiting)))
        lines.extend("  " + w for w in waiting)
        return OUT_WAITING, lines

    payload = build_snapshot(ticks, baseline, now)
    if not write_once(out_path, payload):
        lines.append("another run captured it first: %s" % out_path)
        return OUT_ALREADY, lines

    try:
        atomic_write_json(os.path.join(home, ".claw_direct_snapshot_post_latest"),
                          {"path": out_path, "taken_utc": payload["taken_utc"]})
    except OSError as exc:
        lines.append("snapshot written, but the pointer file failed: %s" % exc)

    lines.append("captured %s (%d claws, all at or past their baseline window)"
                 % (out_path, len(payload["claws"])))
    lines.append("F2 delta vs baseline %s:" % payload.get("baseline_path"))
    lines.extend(render_delta(payload["delta_vs_baseline"]))
    lines.append("F2-LOST = was direct on .19, STILL watched and STILL heard, "
                 "and not direct on .20 — F2's candidate forgeries. The other "
                 "buckets are NOT evidence: a node that went quiet, or left the "
                 "watch list, says nothing about F2. Claws hear different "
                 "segments; even F2-LOST is evidence, not proof.")
    return OUT_CAPTURED, lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--home", default=None,
                        help="operator home to read/write (default: real user home)")
    parser.add_argument("--now", type=float, default=None,
                        help="epoch override (tests)")
    parser.add_argument("--notify", action="store_true",
                        help="page the fleet ntfy channel on a transition into "
                             "captured / window_lost / unobservable")
    args = parser.parse_args(argv)

    home = args.home or str(get_real_user_home())
    now = args.now if args.now is not None else time.time()

    outcome, lines = run(home, now)
    witness = maybe_notify(home, outcome, lines, args.notify)

    print("claw_direct_snapshot: %s" % outcome)
    for line in lines:
        print(line)
    if witness:
        print(witness)
    return 1 if outcome in _LOUD else 0


if __name__ == "__main__":
    sys.exit(main())
