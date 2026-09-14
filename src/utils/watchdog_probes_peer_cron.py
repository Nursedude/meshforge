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
* A peer whose cell is ``dark`` is NOT reported as failing. ``dark`` means the
  confirmation gate has not confirmed yet — waiting one cron cycle is the
  designed behaviour, not a finding.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from utils.watchdog_probe_core import Signal, note_disposition

#: Class this probe contributes to. Deliberately an EXISTING member of
#: SIGNAL_CLASSES — see "WHAT THIS IS *NOT*" above.
_CLS = "cron_verdict_stale"


def probe_peer_cron_verdict_stale(
    *,
    now: Optional[float] = None,
    spool_dir=None,
) -> List[Signal]:
    """Emit ``cron_verdict_stale`` for every spooled peer judged ``failed``."""
    now = time.time() if now is None else now
    try:
        from utils.fleet_truth_collector import (
            SPOOL_STALE_S, judge_spooled_schedules,
        )
        d = _operator_spool_dir() if spool_dir is None else spool_dir
        files = sorted(p for p in d.glob("*.json")
                       if not p.name.startswith("cron_debounce."))
    except Exception as exc:  # noqa: BLE001 - unobservable, never "healthy"
        note_disposition(
            _CLS, "indeterminate",
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
                _CLS, "indeterminate",
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
        try:
            doc: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = float(doc.get("fetched_at") or 0.0)
        except Exception:  # noqa: BLE001 - a corpse is unobservable, not clean
            blind.append(f"{alias}(unreadable)")
            continue

        age = now - fetched_at
        if fetched_at <= 0 or age < 0 or age > SPOOL_STALE_S:
            # A future stamp is as broken as an old one (#74's clock class).
            blind.append(f"{alias}({int(max(age, 0)) // 60}m stale)")
            continue

        try:
            cell = judge_spooled_schedules(alias, doc.get("schedules"), now=now)
        except Exception as exc:  # noqa: BLE001
            blind.append(f"{alias}(judge failed: {type(exc).__name__})")
            continue

        if cell is None:
            # Nothing spooled to say anything about — not blindness.
            continue
        judged += 1
        state = cell.get("state")
        if state == "failed":
            signals.append(Signal(
                cls=_CLS,
                subject=alias,
                severity="degraded",
                detail=(f"{alias}: {cell.get('reason') or 'cron verdict unhealthy'} "
                        f"— judged from the ssh truth spool because this box runs "
                        f"no watchdog of its own"),
                extra={"peer": alias, "via": "truth_spool"},
            ))
        elif state == "dark":
            # Unconfirmed / unobservable on the PEER's side. Its own reason is
            # the witness; surface it as partial coverage, never as health.
            blind.append(f"{alias}({cell.get('reason') or 'dark'})")

    if blind:
        note_disposition(
            _CLS, "indeterminate",
            reason="peer cron verdicts unobservable: " + ", ".join(blind),
            coverage={"judged": judged, "enrolled": judged + len(blind)})
    elif judged:
        # Positive witness that the leg RAN and saw peers. Without this an
        # all-healthy fleet and a silently dead peer leg emit the same thing
        # (nothing) — the "absence rendered as health" class. `clean` never
        # overrides a worse note from the local probe (worst-wins), so this
        # only ever ADDS the coverage counts.
        note_disposition(
            _CLS, "clean",
            reason="judged " + str(judged) + " spooled peer(s); none failing",
            coverage={"judged": judged, "enrolled": judged})
    return signals


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
