"""Fleet power posture — the DECLARED "a box is off on purpose" SSOT.

Born 2026-09-01 from the field-eComm + duty-cycle design pass
(.claude/plans/field_ecomm_and_dutycycle_fleet.md, Decision 3). Hurricane
Lala replayed against today's instruments: power off eight boxes and the
offline monitor pages DOWN x8 hourly, every fleet cron goes CONCERN,
cron_verdict_stale fires all storm, every surviving watchdog reports its
peers unreachable — all TRUE statements about an EXPECTED condition, which
is exactly the noise that made "the watchdog died" indistinguishable from
"the fleet is fine and dark on purpose". The creed gains a fourth state:
unobservable != dark != resolved != DORMANT.

THE FILE (operator values, never committed — MF014; manager SSOT, mirrored
to every box before a storm like the naming registry):

    ~/.config/meshforge/fleet_posture.json
    {
      "posture": "storm-2026-09",           # free name for the declaration
      "declared_at": "2026-09-05T00:00:00Z",
      "declared_by": "operator",
      "boxes": {
        "moc4": {"state": "dormant", "since": "...Z", "until": "...Z",
                 "reason": "tier-2 shed for the storm"},
        "kit":  {"state": "detached", "since": "...Z", "until": "...Z"}
      }
    }

FOUR PER-BOX STATES, each a different claim:
  active    — watched exactly as today (the default for any box not listed)
  shed      — box UP, services deliberately reduced (reachability watched)
  dormant   — box OFF on purpose: pages NOTHING, never reads OK, always a
              witness line; a dormant box that ANSWERS is posture DRIFT
  detached  — reachable only by its own push (the field kit): no organ may
              target it; its evidence arrives when it rejoins

THE RULES THIS MODULE ENFORCES (the design's attack list, compiled):
  * ``until`` is MANDATORY per box and capped at MAX_DORMANCY_S (14 d,
    renewable). A declaration with no end is how a box that died in the
    storm stays "dormant" in November (the known_benign class).
  * Past ``until`` a box is ACTIVE again — the honest default — with a note.
  * Expiry is wall-clock on RTC-less Pis whose clocks ran 8 days stale last
    time, so a consumer whose own clock is unconfirmed passes
    ``clock_confident=False`` and the posture is HELD past expiry with the
    note "expiry unverifiable: clock unconfirmed" (hfm #2) — never silently
    un-dormanted, never silently extended without saying so.
  * Absent file = ``undeclared`` = today's behaviour, byte for byte. An
    UNREADABLE or INVALID file is a loud, separate status: consumers fall
    back to watching everything (paging is the safe default) and say why.
  * ``validate()`` refuses a posture that leaves zero bridge-capable boxes
    active when the caller tells it which boxes bridge — a fleet that
    cannot deliver a message must not be declarable by accident.

Consumers (each must read posture or fail a test — see
tests/test_fleet_posture.py::TestClosedConsumers): scripts/fleet_offline_check.sh
(batch 1, 2026-09-01); fleet_truth cell, the manager fleet crons,
honest_status SHA leg, peer-facing watchdog probes, claw RF watch lists
(queued batches, in the design's priority order).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

POSTURE_BASENAME = "fleet_posture.json"
POSTURE_ENV = "MESHFORGE_FLEET_POSTURE"

STATE_ACTIVE = "active"
STATE_SHED = "shed"
STATE_DORMANT = "dormant"
STATE_DETACHED = "detached"
STATES = (STATE_ACTIVE, STATE_SHED, STATE_DORMANT, STATE_DETACHED)

#: States under which a box is NOT expected to answer and must page nothing.
SILENT_STATES = (STATE_DORMANT, STATE_DETACHED)

#: Hard cap on one declaration. Renewable by re-declaring; never silently.
MAX_DORMANCY_S = 14 * 86400

#: Doc statuses (tri-state-plus): the reader never collapses these.
DECLARED = "declared"
UNDECLARED = "undeclared"      # no file — today's behaviour
UNREADABLE = "unreadable"      # file present, cannot be read
INVALID = "invalid"            # file read, failed validation

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})$")


def posture_path(home: Optional[str] = None) -> str:
    """Resolve the posture file. ``MESHFORGE_FLEET_POSTURE`` wins (tests,
    drills); else ``<home>/.config/meshforge/fleet_posture.json``. ``home``
    defaults to the REAL user's home (sudo-safe, MF001); a watchdog running
    as sandboxed root must pass the service user's home explicitly (the
    rns_version_drift lesson: get_real_user_home() says /root there)."""
    env = os.environ.get(POSTURE_ENV)
    if env:
        return env
    if home is None:
        from utils.paths import get_real_user_home
        home = str(get_real_user_home())
    return os.path.join(home, ".config", "meshforge", POSTURE_BASENAME)


def parse_ts(value) -> Optional[float]:
    """ISO-8601 (with zone) or epoch seconds → epoch float; None if not."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _ISO_RE.match(value.strip()):
        s = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


def fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class BoxPosture:
    name: str
    state: str                       # one of STATES (effective, after expiry)
    declared_state: str              # what the file says
    since: Optional[float] = None
    until: Optional[float] = None
    reason: str = ""
    services: Optional[List[str]] = None   # shed: the reduced expected-active set
    expired: bool = False
    held: bool = False               # past until but HELD (clock unconfirmed)
    note: str = ""                   # the sentence a consumer prints beside it

    @property
    def silent(self) -> bool:
        """True when this box must page nothing (dormant/detached in effect)."""
        return self.state in SILENT_STATES


@dataclass
class Posture:
    status: str                      # DECLARED | UNDECLARED | UNREADABLE | INVALID
    path: str
    name: str = ""
    declared_at: Optional[float] = None
    declared_by: str = ""
    boxes: Dict[str, BoxPosture] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    detail: str = ""                 # for UNREADABLE/INVALID: why

    def box(self, name: str) -> BoxPosture:
        """Effective posture for ``name`` — ACTIVE for any box not declared
        (and for every box when the file is absent/unreadable/invalid: the
        safe default is to WATCH)."""
        bp = self.boxes.get(name)
        if bp is None:
            return BoxPosture(name=name, state=STATE_ACTIVE,
                              declared_state=STATE_ACTIVE)
        return bp

    def silent_boxes(self) -> List[str]:
        return sorted(n for n, b in self.boxes.items() if b.silent)


def validate(doc, now: Optional[float] = None,
             bridge_boxes: Optional[Iterable[str]] = None,
             skip_past: bool = False) -> List[str]:
    """Return the list of reasons this document must be REFUSED (empty =
    valid). ``bridge_boxes``: the boxes whose role hosts a bridge /
    transport / propagation node — a posture that silences ALL of them is
    a mesh that cannot deliver, refused unless the caller forces.
    ``skip_past``: readers pass True — an expired window is a valid document
    whose effect has ended, not a refusal (declare-time keeps it False)."""
    now = time.time() if now is None else now
    errs: List[str] = []
    if not isinstance(doc, dict):
        return ["document is not a JSON object"]
    boxes = doc.get("boxes")
    if boxes is None:
        return ["no `boxes` key — a posture that declares nothing is not a posture"]
    if not isinstance(boxes, dict):
        return ["`boxes` must be an object keyed by box name"]
    declared_at = parse_ts(doc.get("declared_at"))
    if doc.get("declared_at") is not None and declared_at is None:
        errs.append("`declared_at` is not an ISO-8601 timestamp with zone")
    silenced: List[str] = []
    for name, entry in boxes.items():
        if not isinstance(name, str) or not name.strip():
            errs.append(f"box name {name!r} is not a non-empty string")
            continue
        if not isinstance(entry, dict):
            errs.append(f"{name}: entry must be an object")
            continue
        state = entry.get("state")
        if state not in STATES:
            errs.append(f"{name}: state {state!r} not in {list(STATES)}")
            continue
        if state == STATE_ACTIVE:
            continue                       # explicit active needs no window
        until = parse_ts(entry.get("until"))
        if until is None:
            errs.append(f"{name}: `until` is MANDATORY for state {state} "
                        f"(ISO-8601 with zone or epoch) — an open-ended "
                        f"declaration is how a dead box becomes furniture")
            continue
        since = parse_ts(entry.get("since"))
        anchor = since if since is not None else (declared_at if declared_at is not None else now)
        # 2026-09-01 (caught by the collector test): anchoring on `now` for
        # an entry with neither `since` nor `declared_at` made a reader that
        # passed a synthetic clock refuse a valid file. The CLI always writes
        # `since`; a hand-written entry without one is capped against the
        # reader's real clock only when that clock is real.
        if until - anchor > MAX_DORMANCY_S and (since is not None or declared_at is not None or not skip_past):
            errs.append(f"{name}: window {fmt_ts(anchor)} -> {fmt_ts(until)} "
                        f"exceeds the {MAX_DORMANCY_S // 86400}-day cap — "
                        f"re-declare to renew, never silently extend")
        if until <= now and not skip_past:
            errs.append(f"{name}: `until` {fmt_ts(until)} is already in the past")
        svcs = entry.get("services")
        if svcs is not None and not (isinstance(svcs, list)
                                     and all(isinstance(s, str) for s in svcs)):
            errs.append(f"{name}: `services` must be a list of unit names")
        if state in SILENT_STATES or (state == STATE_SHED and svcs == []):
            silenced.append(name)
    if bridge_boxes is not None:
        bridges = {b for b in bridge_boxes if isinstance(b, str)}
        if bridges and bridges.issubset(set(silenced)):
            errs.append("posture silences EVERY bridge-capable box "
                        f"({', '.join(sorted(bridges))}) — a fleet that cannot "
                        "deliver a message; pass force to declare it anyway")
    return errs


def _effective(name: str, entry: dict, now: float, clock_confident: bool) -> BoxPosture:
    state = entry.get("state", STATE_ACTIVE)
    until = parse_ts(entry.get("until"))
    since = parse_ts(entry.get("since"))
    bp = BoxPosture(name=name, state=state, declared_state=state, since=since,
                    until=until, reason=str(entry.get("reason") or ""),
                    services=entry.get("services") if isinstance(entry.get("services"), list) else None)
    if state == STATE_ACTIVE or until is None:
        bp.note = f"declared {state}"
        return bp
    if now >= until:
        if clock_confident:
            bp.state = STATE_ACTIVE
            bp.expired = True
            bp.note = (f"declared {state} until {fmt_ts(until)} — EXPIRED, "
                       f"watching again (re-declare to renew)")
        else:
            bp.held = True
            bp.note = (f"declared {state} until {fmt_ts(until)} — expiry "
                       f"unverifiable: this reader's clock is unconfirmed; "
                       f"posture HELD, not silently lifted")
        return bp
    bp.note = f"declared {state} until {fmt_ts(until)}"
    if bp.reason:
        bp.note += f" ({bp.reason})"
    return bp


def read_posture(path: Optional[str] = None, *, now: Optional[float] = None,
                 clock_confident: bool = True, home: Optional[str] = None) -> Posture:
    """Read the posture file into a tri-state-plus result. NEVER raises.

    UNDECLARED (no file) is a positive observation: nothing is declared,
    every box is active. UNREADABLE and INVALID are loud: consumers treat
    every box as active (watch everything — paging is the safe default)
    and print ``detail`` so a broken declaration is found, not absorbed."""
    now = time.time() if now is None else now
    path = path or posture_path(home)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return Posture(status=UNDECLARED, path=path)
    except OSError as exc:
        return Posture(status=UNREADABLE, path=path, detail=f"{type(exc).__name__}: {exc}")
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return Posture(status=INVALID, path=path, detail=f"not JSON: {exc}",
                       errors=[f"not JSON: {exc}"])
    # Validate WITHOUT the past-`until` rule: an expired window is a valid
    # document whose effect has ended, not a refusal.
    errs = validate(doc, now=now, skip_past=True)
    if errs:
        return Posture(status=INVALID, path=path, errors=errs,
                       detail="; ".join(errs)[:300])
    p = Posture(status=DECLARED, path=path, name=str(doc.get("posture") or ""),
                declared_at=parse_ts(doc.get("declared_at")),
                declared_by=str(doc.get("declared_by") or ""))
    for name, entry in doc["boxes"].items():
        p.boxes[name] = _effective(name, entry, now, clock_confident)
    return p


# --------------------------------------------------------------------------- #
# Writing (the CLI's half). Atomic, backed up, validated first.
# --------------------------------------------------------------------------- #
def load_doc(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {"posture": "", "declared_by": "operator", "boxes": {}}
    if not isinstance(doc, dict):
        raise ValueError("posture file is not a JSON object")
    doc.setdefault("boxes", {})
    return doc


def write_doc(path: str, doc: dict) -> str:
    """Atomic write with a timestamped backup of any prior file. Returns the
    backup path ('' if none)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    backup = ""
    if os.path.exists(path):
        backup = f"{path}.bak-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        os.replace(path, backup)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return backup


def parse_until(spec: str, now: Optional[float] = None) -> Optional[float]:
    """'+36h' / '+3d' / '+90m' relative, or an absolute ISO/epoch."""
    now = time.time() if now is None else now
    s = (spec or "").strip()
    m = re.match(r"^\+(\d+)([mhd])$", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return now + n * {"m": 60, "h": 3600, "d": 86400}[unit]
    return parse_ts(s)


def declare(doc: dict, name: str, state: str, until: Optional[float],
            reason: str = "", services: Optional[List[str]] = None,
            now: Optional[float] = None) -> dict:
    """Return a NEW doc with ``name`` declared. Validation is the caller's
    job (so `--force` can be a deliberate, recorded override)."""
    now = time.time() if now is None else now
    new = json.loads(json.dumps(doc))
    entry = {"state": state, "since": fmt_ts(now)}
    if until is not None:
        entry["until"] = fmt_ts(until)
    if reason:
        entry["reason"] = reason
    if services is not None:
        entry["services"] = list(services)
    new["boxes"][name] = entry
    new["declared_at"] = fmt_ts(now)
    new.setdefault("declared_by", "operator")
    return new


def clear(doc: dict, name: str) -> Tuple[dict, bool]:
    new = json.loads(json.dumps(doc))
    existed = name in new.get("boxes", {})
    new["boxes"].pop(name, None)
    return new, existed
