"""Fleet preset — reproduces today's ~/mini_dudeai.py wiring exactly.

What this preset configures:
  - WatchdogJsonSource → /var/lib/meshforge/watchdog.json → kind="signal_class"
  - FederationStatusSource → http://localhost:5000/api/status → 2 kinds:
      kind="federation_peer_unhealthy" (per peer in_backoff or unreachable)
      kind="source_error" (when the URL is unreachable — local map down)
    NOTE the URL is the box's OWN map, so this is a PER-VANTAGE reading: since
    2026-07-19 every MAP-RUNNING box wires it, not just the federator (see
    enable_federation below).
  - DigestStaleSource → ~/situation_digest.md → kind="digest_stale" if > 30m old
  - BootHealthSource → ~/mini_dudeai_clean_exit (+ state/assessment/power log)
      → kind="unexpected_reboot" on an unclean boot; planned reboots stay
      silent because the engine stamps the clean-exit marker on graceful stop
  - NtfyAction (fleet topic from env), FileAnnotateAction (digest annotations),
    ProposeEscalationAction, NoopAction.

The fleet ntfy topic is NOT hard-coded in source (MF014). Operator must set
MINI_DUDEAI_NTFY_TOPIC (or pass ntfy_topic= when calling build_engine).
"""
from __future__ import annotations

import os
from typing import Iterable

from ..actions import (
    FileAnnotateAction, NoopAction, NtfyAction, ProposeEscalationAction,
)
from ..engine import RuleEngine
from ..sources import (
    BootHealthSource, FileMtimeSource, HttpJsonSource, JsonFileSource,
)
from ..sources.base import Condition, Source

DEFAULT_FEDERATOR_URL = "http://localhost:5000/api/status"
DEFAULT_WATCHDOG_PATH = "/var/lib/meshforge/watchdog.json"
DEFAULT_DIGEST_STALE_THRESHOLD_S = 1800


def _watchdog_extractor(data):
    """Project watchdog.json signals[] to Condition-ready dicts.

    The on-disk shape (utils.watchdog_probes.signal_to_dict) serializes the
    Signal dataclass field `cls` to the JSON key **"class"** — NOT "cls". Read
    "class" (with a "cls" fallback for any legacy file). The condition kind
    becomes 'signal_class' so seed rules' match.kind: signal_class work directly;
    the class filter lives in cond.extras["class"] and is matched by rule.match.class.
    """
    out = []
    for sig in data.get("signals") or []:
        out.append({
            "subject": sig.get("subject", "unknown"),
            "detail": sig.get("detail", ""),
            "class": sig.get("class") or sig.get("cls") or "unknown",
            "severity": sig.get("severity", "info"),
            "issue_ref": sig.get("issue_ref"),
        })
    return out


#: Coverage dispositions that mean the class was NOT observed this tick.
#: `clean` and `inert` are positive observations; `active` means it fired.
#: Scoring `inert` would be wrong — a legitimately-absent organ is not a blind
#: one, and conflating them is how a coverage score would start lying.
_BLIND_DISPOSITIONS = frozenset({"indeterminate", "unknown"})


def _coverage_blind_extractor(data):
    """Project watchdog.json `coverage` → one Condition per BLIND detector.

    The companion to `_watchdog_extractor`, over the same file: that one asks
    *what is wrong*, this one asks *what could we not see*. They are disjoint
    questions and the second had no consumer at all — the watchdog has been
    emitting a complete per-class disposition map every tick and nothing has
    ever read it for blindness.

    Why this matters (2026-07-26, taxonomy T1): a probe that cannot observe
    returns nothing, and nothing is exactly what a healthy probe returns. Its
    silence is therefore indistinguishable from health at every consumer. The
    Pri-2 hold covers a blind probe that WAS signalling; this covers the
    disjoint case — blind and QUIET — e.g. `kernel_reboot_pending`, which has
    never fired and currently cannot read the modules dir.

    A missing/!dict `coverage` block yields NOTHING rather than a fabricated
    all-blind sweep: a legacy or torn writer is itself unobservable, and
    inventing 57 blind conditions from it would be the same defect one layer up
    (honest_failure_modes #1 — a degraded input must not become a confident
    claim). The block's own absence is already the watchdog's own problem to
    report.
    """
    coverage = (data or {}).get("coverage")
    if not isinstance(coverage, dict):
        return []
    out = []
    for cls, entry in sorted(coverage.items()):
        if not isinstance(entry, dict):
            continue
        disp = entry.get("disp")
        if disp not in _BLIND_DISPOSITIONS:
            continue
        reason = entry.get("reason") or "no reason reported"
        out.append({
            "subject": cls,
            "detail": f"detector cannot observe ({disp}): {reason}",
            "class": cls,
            "disp": disp,
        })
    return out


class FederationPeerSource(Source):
    """One Condition per UN-healthy peer pulled from /api/status.federation.peer_status.

    Only emits for peers that are in_backoff OR have last_error and !reachable.
    Healthy peers emit nothing. Federator unreachable → source_error Condition.
    """

    def __init__(self, url: str, timeout: float = 6.0,
                 name: str = "federation") -> None:
        self.url = url
        self.timeout = timeout
        self.name = name

    def collect(self) -> Iterable[Condition]:
        from .._util import fetch_json
        data, err = fetch_json(self.url, timeout=self.timeout)
        if err:
            yield Condition(
                kind="source_error",
                subject="federator",
                detail=f"/api/status unreachable: {err}",
                source=self.name,
            )
            return
        if not isinstance(data, dict):
            return
        peers = (data.get("federation") or {}).get("peer_status") or []
        for p in peers:
            name = p.get("peer_name") or p.get("hostname") or p.get("name") or "?"
            if p.get("in_backoff") or (p.get("last_error") and not p.get("reachable", True)):
                yield Condition(
                    kind="federation_peer_unhealthy",
                    subject=str(name),
                    detail=(f"in_backoff={p.get('in_backoff')} "
                            f"mult={p.get('backoff_multiplier')} "
                            f"last_err={p.get('last_error')!r}"),
                    source=self.name,
                    extras={
                        "backoff_multiplier": p.get("backoff_multiplier"),
                        "consecutive_failures": p.get("consecutive_failures"),
                    },
                )


def build_engine(
    home: str | None = None,
    rules_path: str | None = None,
    state_path: str | None = None,
    history_path: str | None = None,
    brief_path: str | None = None,
    digest_path: str | None = None,
    annotate_path: str | None = None,
    watchdog_path: str | None = None,
    federator_url: str | None = None,
    ntfy_topic: str | None = None,
    digest_stale_threshold_s: int = DEFAULT_DIGEST_STALE_THRESHOLD_S,
    enable_federation: bool | None = None,
    enable_digest: bool | None = None,
    enable_boot_health: bool | None = None,
    enable_watchdog: bool | None = None,
) -> RuleEngine:
    """Wire up the engine the way the fleet's primary node runs it today.

    All paths/URLs/topics are overridable for testing. Operator-runtime defaults
    pull from env / standard locations.

    enable_federation / enable_digest: which optional sources to wire. Both
    default ON (env MINI_DUDEAI_ENABLE_FEDERATION / _ENABLE_DIGEST = "0" to
    disable, or pass explicitly).

    enable_federation is gated on ONE fact: does THIS box run a local :5000 map?
    The source polls the box's OWN /api/status, so on any map-running box it is
    a free PER-VANTAGE reading of federation health — box A seeing peer C
    unhealthy while box B sees it fine is path evidence no single-vantage
    watcher can produce (structural-dark row 6, 2026-07-19; before that only
    the federator wired it). A box with NO local map MUST keep it "0": the
    source would emit a source_error every tick and pin src_errors, which is
    the declared-absent-vs-error confusion honest_failure_modes exists to stop.

    enable_digest is federator-only BY DESIGN and stays "0" everywhere else:
    situation_digest.md is a federator artifact, so watching its staleness on a
    box that never writes one is meaningless, not merely noisy.

    enable_watchdog: the box's own /var/lib/meshforge/watchdog.json feed.
    Defaults ON — it is every MeshForge box's local-health feed, and on those
    boxes turning it off would blind the source_error_watchdog dead-watchdog
    rule. Set "0" (env MINI_DUDEAI_ENABLE_WATCHDOG) ONLY on a box that runs no
    MeshForge watchdog at all (e.g. the MeshAnchor-only box): there the file is
    DECLARED absent, not a failure — leaving the source wired would page
    source_error_watchdog forever and pin src_errors=1 in every rollup
    (honest_failure_modes: declared-absent ≠ unobservable ≠ error).

    enable_boot_health: unexpected-reboot detection (BootHealthSource). Defaults
    ON for every box — gateways included; a hard reset is fleet-relevant
    everywhere (env MINI_DUDEAI_ENABLE_BOOT_HEALTH = "0" to disable). When on,
    the SAME clean_exit_path is passed to BOTH the source (reader) and the
    engine (writer-on-graceful-stop) — that pairing is the whole mechanism.
    """
    if enable_federation is None:
        enable_federation = os.environ.get("MINI_DUDEAI_ENABLE_FEDERATION", "1") != "0"
    if enable_digest is None:
        enable_digest = os.environ.get("MINI_DUDEAI_ENABLE_DIGEST", "1") != "0"
    if enable_boot_health is None:
        enable_boot_health = os.environ.get("MINI_DUDEAI_ENABLE_BOOT_HEALTH", "1") != "0"
    if enable_watchdog is None:
        enable_watchdog = os.environ.get("MINI_DUDEAI_ENABLE_WATCHDOG", "1") != "0"
    from .._util import resolve_home
    home = home or resolve_home()
    rules_path = rules_path or os.path.join(home, "mini_dudeai_rules.json")
    state_path = state_path or os.path.join(home, "mini_dudeai_state.json")
    history_path = history_path or os.path.join(home, "mini_dudeai_history.jsonl")
    # Every fleet box writes its own brief each tick (continuity: SSH to any box
    # and mini's current posture is there, fresh). Stays in $HOME — NOT the
    # fleet_sync-mirrored memory dir — so it's per-box, not clobbered by --delete.
    brief_path = brief_path or os.path.join(home, "mini_dudeai_brief.md")
    digest_path = digest_path or os.path.join(home, "situation_digest.md")
    annotate_path = annotate_path or os.path.join(home, "mini_dudeai_digest_annotations.md")
    watchdog_path = watchdog_path or DEFAULT_WATCHDOG_PATH
    federator_url = federator_url or os.environ.get(
        "MINI_DUDEAI_FEDERATOR_URL", DEFAULT_FEDERATOR_URL,
    )
    ntfy_topic = ntfy_topic or os.environ.get("MINI_DUDEAI_NTFY_TOPIC")
    if not ntfy_topic:
        raise ValueError(
            "fleet preset requires MINI_DUDEAI_NTFY_TOPIC env var or ntfy_topic= arg. "
            "Operator-specific topics live in ~/.config/meshforge/mini_dudeai.env, "
            "loaded by the systemd unit via EnvironmentFile= (MF014 keeps them out of repo)."
        )

    # The watchdog source is every MeshForge box's local-health feed — wired by
    # default; "0" only on a box that runs no MF watchdog (declared absent).
    sources: list[Source] = []
    if enable_watchdog:
        sources.append(JsonFileSource(
            path=watchdog_path,
            kind="signal_class",
            extractor=_watchdog_extractor,
            name="watchdog",
        ))
        # Second projection of the SAME file — no new source class, no new
        # daemon, no new probe, no new signal class. The coverage block has
        # been written every tick since fleet-truth Phase 0 and never read for
        # blindness; this is the reader, not new machinery.
        sources.append(JsonFileSource(
            path=watchdog_path,
            kind="detector_blind",
            extractor=_coverage_blind_extractor,
            name="watchdog_coverage",
        ))
    # Federation reads the box's OWN :5000 — wired on every MAP-RUNNING box for
    # a per-vantage view (row 6, 2026-07-19), "0" only where no local map exists.
    # Digest stays federator-only by design (see the docstring above).
    if enable_federation:
        sources.append(FederationPeerSource(url=federator_url))
    if enable_digest:
        sources.append(FileMtimeSource(
            path=digest_path,
            max_age_s=float(digest_stale_threshold_s),
            kind="digest_stale",
            subject="situation_digest.md",
            name="digest",
        ))
    # Unexpected-reboot detection. Per-box files in $HOME (like the brief —
    # NOT the fleet_sync-mirrored memory dir). clean_exit_path is computed
    # once here and fed to BOTH BootHealthSource and RuleEngine below: the
    # engine stamps it on graceful stop, the source reads it on the next
    # fresh boot. power_history.log may be absent on a box — the source
    # treats an unreadable power log as "no evidence", not an error.
    clean_exit_path = os.path.join(home, "mini_dudeai_clean_exit")
    if enable_boot_health:
        sources.append(BootHealthSource(
            state_path=state_path,
            clean_exit_path=clean_exit_path,
            assessment_path=os.path.join(home, "mini_dudeai_boot_assessment.json"),
            power_log_path=os.path.join(home, "power_history.log"),
            name="boot_health",
        ))
    actions = {
        "ntfy": NtfyAction(topic=ntfy_topic),
        "annotate_digest": FileAnnotateAction(path=annotate_path),
        "propose_escalation": ProposeEscalationAction(),
        "none": NoopAction(),
    }
    return RuleEngine(
        sources=sources,
        actions=actions,
        rules_path=rules_path,
        state_path=state_path,
        history_path=history_path,
        candidate_path=rules_path + ".candidate",
        brief_path=brief_path,
        clean_exit_path=clean_exit_path if enable_boot_health else None,
    )
