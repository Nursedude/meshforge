"""probe_peer_cron_verdict_stale — give spooled, watchdog-less boxes a voice.

WHY THIS EXISTS (2026-09-13)
----------------------------
A box whose declared role runs no watchdog (``field-node``: lehua) writes cron
verdicts that NOTHING on the fleet judged into a signal. The ssh truth spool
carried them to the manager and ``fleet_truth_collector.judge_spooled_schedules``
rendered a ``/fleet`` CELL — but a cell is a surface, not a signal: it reaches a
human who opens the page, and nobody else. Measured: lehua failed its hourly
``fleet_hosts_drift`` 26 consecutive times (2026-09-11 -> 09-14) and the only
thing that knew was a web page nobody had open. (The spool ALSO summarised away
the field the confirmation gate needs, so the cell read ``dark`` rather than
``failed`` the whole time — fixed the same day in ``fleet_truth_spool``; that
bug is why this gap went unnoticed for so long.)

WHAT THIS IS *NOT*
------------------
NOT a new signal class. ``cron_verdict_stale`` already exists, already carries
the fleet's chosen severity for this subject (``propose_escalation`` — degraded,
not an outage), and its seeded rule already matches ``subject_glob: "*"``. This
probe only routes a peer's failure INTO that existing class with
``subject=<alias>``, exactly as ``probe_fleet_box_unreachable`` and
``probe_tracer_peer_unreachable`` already emit peer-subject signals from the
manager. Peer boxes become first-class in the spine instead of second-class.
Loudness stays one line in the ruleset, and applies to local and peer alike.

HONEST-FAILURE-MODE CONTRACT
----------------------------
* The judging is ``judge_spooled_schedules`` — the SAME predicate the collector
  calls, which is itself the box's own probe run on spooled text. One
  implementation, three callers; a second copy here would be free to drift
  (honest_failure_modes #5).
* No spool directory / no spool files = this is not the manager. Return nothing
  and note NOTHING: absence of a spool is not a claim about anyone's crons, and
  the LOCAL ``probe_cron_verdict_stale`` owns this class's disposition here.
* A spool file that is STALE or unreadable is UNOBSERVABLE, never healthy. It
  is reported as ``indeterminate`` WITH ``coverage`` so a consumer can see the
  class is partially blind rather than read a tidy verdict over a peer nobody
  could see (honest_failure_modes #2, #9). When signals are also emitted the
  runner renders that as ``active`` + ``partial``, which is the truthful pair.
* A FRESH spool with no usable ``schedules`` section (the judge returns
  None) is UNOBSERVABLE too — never "nothing to say". The spool writer
  stamps every row, so a failed ssh read, a tripwire withhold or a crashed
  reader all look fresh; treating None as silence let the local leg's
  ``clean`` clear a paged peer (P1, 2026-11-27). The reason names the
  spool's own cause (``transport``, then ``section_errors.schedules``).
* A peer whose cell is ``dark`` is NOT reported as failing. ``dark`` means the
  confirmation gate has not confirmed yet — waiting one cron cycle is the
  designed behaviour, not a finding.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional

from utils.finite_stamp import finite  # the ONE finite-stamp rule (11-26)

from utils.watchdog_probe_core import (
    CRON_VERDICT_BAND_FAIL, Signal, note_disposition, note_subject_observed,
)

#: The subject the LOCAL leg (`probe_cron_verdict_stale`) emits and observes.
#: Same literal as its emit; a spool alias equal to it is refused above.
LOCAL_CRON_SUBJECT = "cron"

# This probe contributes to the EXISTING class "cron_verdict_stale" — see
# "WHAT THIS IS *NOT*" above.
#
# ⚠️ That name is written as a LITERAL at every emission and note_disposition
# site below, never via a module constant. `TestOwnershipTableAgreesWithProbe
# Bodies` reads probe SOURCE for class literals and compares it to
# `PROBE_OWNS_OVERRIDES`; routing through a constant makes the body look like
# it owns nothing and the table look like it lies. The first version of this
# file did exactly that and the gate caught it. Keeping the string greppable
# IS the contract.


def probe_peer_cron_verdict_stale(
    *,
    now: Optional[float] = None,
    spool_dir=None,
) -> List[Signal]:
    """Emit ``cron_verdict_stale`` for every spooled peer judged ``failed``."""
    now = finite(time.time() if now is None else now)
    if now is None:
        # A NaN clock makes every age NaN, which passes both staleness bounds
        # -> every peer judged fresh (r2 review, the read_spool rule).
        note_disposition(
            "cron_verdict_stale", "indeterminate",
            reason="peer cron leg: this box's clock is not a finite time",
            coverage={"judged": 0, "enrolled": len(_declared_targets())})
        return []
    try:
        from utils.fleet_truth_collector import (
            SPOOL_SCHEMA, SPOOL_STALE_S, judge_spooled_schedules,
            spooled_peer_now,
        )
        # read_spool's alias rule — the /fleet cell refuses these stems too.
        from utils.box_truth_center import _ALIAS_RE
        d = _operator_spool_dir() if spool_dir is None else spool_dir
        files = sorted(p for p in d.glob("*.json")
                       if not p.name.startswith("cron_debounce."))
    except Exception as exc:  # noqa: BLE001 - unobservable, never "healthy"
        note_disposition(
            "cron_verdict_stale", "indeterminate",
            reason=f"peer cron leg could not read the truth spool: {exc}")
        return []

    if not files:
        # No spool files. Two very different situations, and collapsing them
        # is the defect this module exists to end: on a box that declares NO
        # spool targets this is absence-by-design (say nothing — the local
        # probe owns the class here); on the MANAGER, which declares targets,
        # it means the spool cron is dead and every peer just went invisible.
        # "peer leg dead" must never render identically to "all peers healthy".
        targets = _declared_targets()
        if targets:
            note_disposition(
                "cron_verdict_stale", "indeterminate",
                reason=("truth spool declares " + str(len(targets)) + " peer(s) "
                        "but wrote no spool file — the spool cron is not "
                        "running; peer cron verdicts are UNOBSERVABLE, not "
                        "healthy"),
                coverage={"judged": 0, "enrolled": len(targets)})
        return []

    signals: List[Signal] = []
    blind: List[str] = []
    judged = 0
    for path in files:
        alias = path.stem
        if not _ALIAS_RE.match(alias):
            # e.g. `.lehua.json` / `le hua.json`: judged (and observed) here
            # while read_spool refuses the alias -- two readers, two answers.
            blind.append(f"{alias[:40]!r}(not a valid spool alias)")
            continue
        if alias == LOCAL_CRON_SUBJECT:
            # The local leg emits and observes subject `cron`; a peer spooled
            # under that alias would clear (or raise) the LOCAL verdict on a
            # peer's behalf. Witnessed as blind, never judged (review (b),
            # 2026-10-06).
            blind.append(f"{alias}(alias collides with the local subject "
                         f"{LOCAL_CRON_SUBJECT!r}; rename the spool target)")
            continue
        try:
            doc: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corpse is unobservable, not clean
            blind.append(f"{alias}(unreadable)")
            continue
        if not isinstance(doc, dict):
            blind.append(f"{alias}(unreadable: not a JSON object)")
            continue
        # The same two gates read_spool applies (one schema constant, F1):
        # a doc about ANOTHER box must never be judged as this alias's crons.
        # Peer-supplied values are capped: this reason rides watchdog state.
        if doc.get("schema") != SPOOL_SCHEMA:
            blind.append(f"{alias}(spool schema {repr(doc.get('schema'))[:40]}, "
                         f"expected {SPOOL_SCHEMA!r})")
            continue
        if doc.get("alias") != alias:
            blind.append(f"{alias}(spool file describes "
                         f"{repr(doc.get('alias'))[:40]})")
            continue
        fetched_at = finite(doc.get("fetched_at"))
        if fetched_at is None:
            # NaN passed every bound below (each NaN comparison is False) and
            # was judged fresh forever from a stale schedule (11-26 review).
            blind.append(f"{alias}(spool stamp not a finite time)")
            continue

        age = now - fetched_at
        if fetched_at <= 0 or age < 0 or age > SPOOL_STALE_S:
            # A future stamp is as broken as an old one (#74's clock class) —
            # and says so: "0m stale" read as fresh (r1 review).
            if age < 0:
                blind.append(f"{alias}(stamp {math.ceil(-age)}s in the "
                             f"future — clock fault)")
            else:
                blind.append(f"{alias}({int(age) // 60}m stale)")
            continue

        try:
            cell = judge_spooled_schedules(
                alias, doc.get("schedules"), now=now,
                peer_now=spooled_peer_now(doc), fetched_at=fetched_at)
        except Exception as exc:  # noqa: BLE001
            blind.append(f"{alias}(judge failed: {type(exc).__name__})")
            continue

        if cell is None:
            # No usable `schedules` section behind a FRESH stamp: the spool
            # writer stamps every row, so a FAILED ssh read (all sections
            # None), a tripwire withhold, or a crashed reader all land here.
            # That is the channel failing, not the peer's crons being fine.
            # `continue` here used to note nothing -> the local leg's `clean`
            # won the class -> a paged FAILED peer cron CLEARED off a reading
            # nobody took (P1, 11-27 Fable; hfm #2). Blind, held, and named
            # by the REAL cause the spool carries (r1 nit 1).
            blind.append(f"{alias}({_no_section_cause(doc)})")
            continue
        state = cell.get("state")
        if state not in ("failed", "healthy", "dark", "inert"):
            # Closed enum, closed consumer (hfm #7): a state this leg does not
            # know was neither observed nor flagged — blind, named.
            blind.append(f"{alias}(unknown judge state {state!r})")
            continue
        if state != "dark":
            # A dark cell is BLIND below, not judged: counting it both ways
            # read one peer as enrolled=2 (11-26 seam review).
            judged += 1
        if state == "failed":
            signals.append(Signal(
                cls="cron_verdict_stale",
                subject=alias,
                severity="degraded",
                detail=(f"{alias}: {cell.get('reason') or 'cron verdict unhealthy'} "
                        f"— judged from the ssh truth spool because this box runs "
                        f"no watchdog of its own"),
                extra={"peer": alias, "via": "truth_spool",
                       # Same loudness vocabulary as the local leg: a peer's
                       # CONCERN is as quiet as ours, its FAIL as loud. An
                       # absent band means the judge could not say, and an
                       # unknown band is the LOUD one — never the quiet one.
                       "verdict_band": (cell.get("band")
                                        or CRON_VERDICT_BAND_FAIL)},
            ))
        elif state in ("healthy", "inert"):
            # Positively observed — a sibling peer's blindness must not hold
            # THIS peer's recovered subject (2026-10-06, the cron hold).
            # `inert` too: the peer's crontab was READ and nothing in it can
            # fail, so a failed->inert peer (crons unwired) releases its old
            # subject instead of holding it (11-26 r3 review).
            note_subject_observed("cron_verdict_stale", alias)
        elif state == "dark":
            # Unconfirmed / unobservable on the PEER's side. Its own reason is
            # the witness; surface it as partial coverage, never as health.
            blind.append(f"{alias}({cell.get('reason') or 'dark'})")

    if blind:
        note_disposition(
            "cron_verdict_stale", "indeterminate",
            reason="peer cron verdicts unobservable: " + ", ".join(blind),
            coverage={"judged": judged, "enrolled": judged + len(blind)})
    elif judged:
        # Positive witness that the leg RAN and saw peers. Without this an
        # all-healthy fleet and a silently dead peer leg emit the same thing
        # (nothing) — the "absence rendered as health" class. `clean` never
        # overrides a worse note from the local probe (worst-wins), so this
        # only ever ADDS the coverage counts.
        note_disposition(
            "cron_verdict_stale", "clean",
            reason="judged " + str(judged) + " spooled peer(s); none failing",
            coverage={"judged": judged, "enrolled": judged})
    return signals


def _no_section_cause(doc: Dict[str, Any]) -> str:
    """Why a fresh spool carries no cron section, from what the spool says.

    Order: the transport (a failed read names its fix — grant, identity,
    unreachable), then the export's own ``section_errors.schedules`` (the
    tripwire withhold, a reader crash), then the bare fact. Peer-supplied
    text, so capped: this reason rides watchdog state."""
    transport = doc.get("transport")
    if isinstance(transport, dict) and transport.get("outcome") != "ok":
        why = (transport.get("reason") or transport.get("outcome")
               or "outcome unrecorded")
        return f"no cron section — read failed: {str(why)[:160]}"
    meta = doc.get("box_truth")
    errs = meta.get("section_errors") if isinstance(meta, dict) else None
    err = errs.get("schedules") if isinstance(errs, dict) else None
    if err:
        return f"no cron section — {str(err)[:160]}"
    return "no cron section in a fresh spool, and no reason recorded"


def _declared_targets() -> List[str]:
    """Aliases opted in to the ssh truth spool, or [] if none/unreadable.

    Same file `fleet_truth_spool` reads. Unreadable -> [] deliberately: this
    is used only to decide whether an EMPTY spool is a finding, and guessing
    "yes" off an unreadable file would invent an outage.
    """
    try:
        home = _operator_home()
        if home is None:
            return []
        path = home / ".config" / "meshforge" / "truth_spool_targets"
        out: List[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
        return out
    except Exception:  # noqa: BLE001 - absent/unreadable -> no claim
        return []


# ── Operator-home resolution (root-service safe) ─────────────────────────
# ⚠️ This probe runs inside meshforge-watchdog, which runs as ROOT with no
# SUDO_USER, where `get_real_user_home()` resolves to /root. The spool is
# written by an OPERATOR cron into the operator's ~/.local/state, and
# `truth_spool_dir()` is only correct for its other caller — the collector,
# which runs inside meshforge-map as the operator user.
#
# Measured before shipping, by running this probe under `sudo env -u
# SUDO_USER`: it found 4 peers as the operator and ZERO as root, saying
# nothing either way. A silently-inert peer leg is precisely the failure this
# module exists to end, so resolve the operator explicitly — the same
# `_find_operator_user()` the LOCAL cron probe already uses for exactly this
# reason (calibrated_claims #7: verify the consumer-of-record, not the wiring).


def _operator_home():
    """The operator's home, resolved root-safely. None if unresolvable."""
    import os
    from pathlib import Path
    try:
        from utils.fleet_test_runner import _find_operator_user
        operator = _find_operator_user()
    except Exception:  # noqa: BLE001
        operator = None
    if operator:
        try:
            import pwd
            return Path(pwd.getpwuid(operator[0]).pw_dir)
        except (KeyError, OSError):
            pass
    if os.geteuid() != 0:
        try:
            from utils.paths import get_real_user_home
            return get_real_user_home()
        except Exception:  # noqa: BLE001
            return None
    return None


def _operator_spool_dir():
    """Where the operator's spool cron actually writes. Raises on failure so
    the caller reports `indeterminate` — never an empty, healthy-looking read."""
    import os
    from pathlib import Path
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "meshforge" / "truth_spool"
    home = _operator_home()
    if home is None:
        raise RuntimeError(
            "could not resolve the operator user, so the truth spool's "
            "location is unknown (running as root with no operator?)")
    return home / ".local" / "state" / "meshforge" / "truth_spool"
