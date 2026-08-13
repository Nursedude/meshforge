"""Watchdog probes — LXMF propagation-node capability legs.

The unadopted-capability probe (shape C, 2026-07-20) and its shape-A
companion (the CONFIGURED node went dark). Split out of
``watchdog_probes_gateway`` 2026-07-31 (MF025 size cap — the delivery
snapshot-file fallback pushed it over 1,500 lines); that module re-exports
this entire surface, so import via the ``utils.watchdog_probes`` hub or
``utils.watchdog_probes_gateway`` as before — the split is API-preserving.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from utils.watchdog_probe_core import (
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)

# ─────────────────────────────────────────────────────────────────────
# The shape-C leg (LXMF propagation available but unadopted) lived here
# 2026-07-20 .. 2026-08-08. Removed by the signal-class yield audit: it was
# INERT on all 8 boxes — inert without a gateway, and inert BY DESIGN on the
# two that adopted a node — so it was a one-time adoption nudge running in a
# 30s loop. The shape-A leg below (the configured node went quiet) stays: it
# watches a live dependency, which is the half that earns a tick.
#
# Found by the optional-organ sweep: of 50 signal classes, exactly ONE
# watched for an available-but-UNADOPTED capability. Everything else waits
# to be told. This is the second, and it was hiding in plain sight — the
# gateway PARSES LXMF_PROPAGATION announces off the RNS network and files
# them in its node cache, while `gateway.json rns.propagation_node` sits
# empty. Measured 2026-07-20: 14-15 propagation nodes heard within 6 h on
# both gateway boxes, zero configured.
#
# What it costs: LXMF to an OFFLINE peer simply fails today. A propagation
# node stores and forwards it until the peer returns — the delivery-layer
# analogue of what AREDN buys on the transport layer, and exactly the
# property emergency comms needs.
#
# Evidence is config-free and NOT the journal (fleet boxes run
# Storage=volatile, so journal absence proves nothing): the gateway's own
# operator-owned node cache, atomically written, carries service_type +
# last_seen per node.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_LXMF_PROPAGATION_DEBOUNCE_PATH = (
    "/var/lib/meshforge/lxmf_propagation_unused_debounce.json")

#: A propagation node counts as AVAILABLE only if heard this recently. Nodes
#: announce periodically; something last heard days ago is not a capability
#: we can claim is present right now.
_PROPAGATION_FRESH_S = 6 * 3600.0

#: The cache is written by the gateway process. Older than this and we are
#: reading a corpse: the cache cannot testify about the present, so the probe
#: holds rather than claiming availability from stale bytes.
_PROPAGATION_CACHE_FRESH_S = 3 * 3600.0

#: Wall-clock is forgeable on RTC-less Pis (honest_failure_modes #6). A
#: last_seen this far in the future is a clock artifact, not an observation.
_PROPAGATION_FUTURE_SLOP_S = 900.0


def _operator_home() -> Optional[str]:
    """The operator's home dir, or None. Root-safe read; never escalate."""
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


def _read_configured_propagation_node(home: str):
    """``(value, state)`` where state is ok | absent | unreadable.

    ABSENT gateway.json means this box runs no gateway organ — an observation
    the caller may act on (INERT). UNREADABLE means we could not determine
    intent — indeterminate. Collapsing those two was the row-5 defect; it is
    not repeated here.
    """
    path = os.path.join(home, ".config", "meshforge", "gateway.json")
    if not os.path.exists(path):
        return None, "absent"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rns = data.get("rns")
        if not isinstance(rns, dict):
            return "", "ok"
        val = rns.get("propagation_node")
        return (val if isinstance(val, str) else ""), "ok"
    except (OSError, ValueError, TypeError):
        return None, "unreadable"


def _read_fresh_propagation_nodes(home: str, now: float):
    """``(candidates, state)`` — propagation nodes heard within the freshness
    window, newest first. state is ok | absent | stale | unreadable.

    ``absent`` = no node cache, i.e. no gateway organ ever ran here (INERT).
    ``stale`` = the cache exists but the gateway stopped updating it, so it
    cannot speak for the present (HOLD, never fire).
    """
    path = os.path.join(home, ".cache", "meshforge", "rns_nodes.json")
    if not os.path.exists(path):
        return [], "absent"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return [], "unreadable"     # incl. a torn read — indeterminate, hold

    import datetime as _dt

    def _epoch(val):
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return _dt.datetime.fromisoformat(val).timestamp()
            except ValueError:
                return None
        return None

    saved = _epoch(data.get("saved_at"))
    if saved is None or (now - saved) > _PROPAGATION_CACHE_FRESH_S:
        return [], "stale"

    out = []
    for n in data.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        if "PROPAGATION" not in str(n.get("service_type") or "").upper():
            continue
        ts = _epoch(n.get("last_seen"))
        if ts is None:
            continue
        age = now - ts
        if age < -_PROPAGATION_FUTURE_SLOP_S:
            continue                 # forged/skewed future stamp — not evidence
        if age > _PROPAGATION_FRESH_S:
            continue
        # Key order matters: the production writer (UnifiedNode.to_dict via
        # node_tracker's web cache) emits "id" and "name" — the other keys are
        # tolerated legacy/twin shapes. "name" was MISSING here until the
        # 2026-07-21 review (W3): the enrichment read display_name/long_name
        # only, keys the writer never emits, so the page's nearest-node name
        # was always empty and the fixtures pinned a shape production never
        # produces (see the writer-derived-shape test).
        out.append((max(age, 0.0),
                    str(n.get("id") or n.get("node_id") or "?"),
                    str(n.get("name") or n.get("display_name")
                        or n.get("long_name") or "")))
    out.sort(key=lambda r: r[0])
    return out, "ok"


# ─────────────────────────────────────────────────────────────────────
# Probe: the CONFIGURED LXMF propagation node stopped answering
# (2026-07-20 — the shape-A leg that must ship WITH adoption).
#
# Without this leg, adopting a node would trade a watched gap for an
# UNWATCHED dependency — offline-peer delivery silently depending on a node
# nobody checks. That is why the propagation-leg plan forbids splitting
# adoption from this probe, and why this leg survived the 2026-08-08 yield
# audit that removed its shape-C sibling: this one watches something LIVE.
#
# Evidence is the durable, operator-owned node cache —
# NOT the journal (fleet boxes run Storage=volatile, so an absence of log
# lines proves nothing). A propagation node re-announces periodically
# (360 min in our own lxmd template, and the upstream default), so the
# cache's `last_seen` for the configured hash is a passive, config-free
# liveness record that survives a gateway restart.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_LXMF_PROPAGATION_DARK_DEBOUNCE_PATH = (
    "/var/lib/meshforge/lxmf_propagation_node_dark_debounce.json")

#: Announce period of a propagation node — our lxmd template's
#: ``announce_interval = 360`` (minutes), which is also the upstream default.
_PROPAGATION_ANNOUNCE_INTERVAL_S = 360 * 60.0

#: A configured node counts as NOT ANSWERING only after several missed
#: announce periods. Passive announce observation cannot distinguish "down"
#: from "announces less often than we assumed" any faster than a few periods,
#: and a stranger's interval is not ours to know — so the window is
#: deliberately generous. This is a ``degraded`` capability signal, not an
#: outage page; being late and right beats being fast and wrong.
_PROPAGATION_DARK_AFTER_S = 3 * _PROPAGATION_ANNOUNCE_INTERVAL_S


def _normalize_rns_hash(val) -> str:
    """Lowercase hex of an RNS destination hash, ``rns_`` prefix stripped."""
    s = str(val or "").strip().lower()
    if s.startswith("rns_"):
        s = s[4:]
    return s


def _read_propagation_liveness(home: str, now: float):
    """``(ages_by_hash, freshest_any, state)`` from the RNS node cache.

    ``ages_by_hash`` maps every propagation node's hash — indexed under BOTH
    its full form and its 16-char short form, since the cache carries both —
    to the age in seconds of its newest announce. ``freshest_any`` is the age
    of the freshest propagation announce from ANY node, or ``None``.

    That second value is the honesty guard: it is positive proof that this box
    can currently hear the propagation announce class at all. Without it, an
    RNS-wide transport failure would read as "the configured node died" —
    exactly the "degraded state mapped to a confident claim" defect this
    fleet keeps paying for. State is ok | absent | stale | unreadable.
    """
    path = os.path.join(home, ".cache", "meshforge", "rns_nodes.json")
    if not os.path.exists(path):
        return {}, None, "absent"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return {}, None, "unreadable"    # incl. a torn read — indeterminate

    import datetime as _dt

    def _epoch(val):
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return _dt.datetime.fromisoformat(val).timestamp()
            except ValueError:
                return None
        return None

    saved = _epoch(data.get("saved_at"))
    if saved is None or (now - saved) > _PROPAGATION_CACHE_FRESH_S:
        return {}, None, "stale"

    ages: dict = {}
    freshest = None
    for n in data.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        if "PROPAGATION" not in str(n.get("service_type") or "").upper():
            continue
        ts = _epoch(n.get("last_seen"))
        if ts is None:
            continue
        age = now - ts
        # A stamp in the future is a clock artifact, not an observation
        # (honest_failure_modes #6). Discard it — never let it read as fresh,
        # which is the direction that would hide a dead node.
        if age < -_PROPAGATION_FUTURE_SLOP_S:
            continue
        age = max(age, 0.0)
        for key in (_normalize_rns_hash(n.get("rns_hash")),
                    _normalize_rns_hash(n.get("id"))):
            if key and (key not in ages or age < ages[key]):
                ages[key] = age
        if freshest is None or age < freshest:
            freshest = age
    return ages, freshest, "ok"


def probe_lxmf_propagation_node_dark(
    *,
    home: Optional[str] = None,
    now: Optional[float] = None,
    configured_fn=None,
    liveness_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """The propagation node this gateway is CONFIGURED to use has gone quiet.

    Adoption makes a node load-bearing for offline-peer delivery; this is the
    leg that keeps that dependency watched. (Its shape-C sibling, which
    watched for an available-but-unadopted node, was removed 2026-08-08 —
    see the module header.)

    Two fault legs, reported distinctly because they need different fixes:
      - **STALE** — the node is in the cache but its newest announce is older
        than several announce periods: it answered once and stopped.
      - **UNHEARD** — the configured hash is not in the cache at all: most
        likely a wrong/typo'd hash, which is precisely the failure adoption
        itself can introduce.

    Honest failure modes, every one of which prefers silence:
      - no operator resolvable → indeterminate.
      - gateway.json ABSENT → INERT (no gateway organ here); UNREADABLE →
        indeterminate, never read as "unconfigured".
      - ``propagation_node`` EMPTY → INERT. Slice 1 owns that gap; one fault
        keeps one owner, and this probe must never double-report it.
      - node cache ABSENT → INERT; UNREADABLE or STALE → indeterminate with
        the streak HELD, because stale bytes cannot testify about the present.
      - **no propagation announce from ANY node within the window** →
        indeterminate, streak HELD. This box's ability to hear the class is
        unproven, so silence about our node is unobservable, not dark. An
        RNS-wide wedge has its own owners (``rns_rpc_unresponsive`` et al.)
        and must not be relabelled as a propagation-node death here.
      - 2-tick debounce so one torn cache read cannot page.

    ``degraded``, escalation-only by seed policy: store-and-forward stopping
    degrades delivery to OFFLINE peers only — live delivery is unaffected —
    and the generous window means a fire is already hours old and not urgent.
    """
    import time as _time
    now = _time.time() if now is None else now
    sp = state_path or DEFAULT_LXMF_PROPAGATION_DARK_DEBOUNCE_PATH

    if home is None:
        home = _operator_home()
    if not home:
        note_disposition("lxmf_propagation_node_dark", "indeterminate",
                         reason="operator unresolvable — cannot read either side")
        return None

    if configured_fn is not None:
        configured, cfg_state = configured_fn()
    else:
        configured, cfg_state = _read_configured_propagation_node(home)
    if cfg_state == "absent":
        note_disposition("lxmf_propagation_node_dark", "inert",
                         reason="no gateway.json — box runs no gateway organ")
        _save_parity_streak(sp, 0)
        return None
    if cfg_state != "ok":
        note_disposition("lxmf_propagation_node_dark", "indeterminate",
                         reason="gateway.json unreadable — intent unknown")
        return None
    if not configured:
        note_disposition(
            "lxmf_propagation_node_dark", "inert",
            reason="no propagation_node configured — nothing adopted to watch "
                   "(the available-but-unadopted gap is knowingly unwatched "
                   "since the 2026-08-08 yield audit removed that probe)")
        _save_parity_streak(sp, 0)
        return None

    if liveness_fn is not None:
        ages, freshest_any, cache_state = liveness_fn()
    else:
        ages, freshest_any, cache_state = _read_propagation_liveness(home, now)
    if cache_state == "absent":
        note_disposition("lxmf_propagation_node_dark", "inert",
                         reason="no RNS node cache — gateway never ran here")
        _save_parity_streak(sp, 0)
        return None
    if cache_state in ("unreadable", "stale"):
        note_disposition(
            "lxmf_propagation_node_dark", "indeterminate",
            reason=f"node cache {cache_state} — cannot speak for the present; streak held")
        return None

    # Can this box hear the propagation announce class AT ALL right now? If
    # not, our node's silence is unobservable — hold, never claim it is dark.
    if freshest_any is None or freshest_any > _PROPAGATION_DARK_AFTER_S:
        note_disposition(
            "lxmf_propagation_node_dark", "indeterminate",
            reason=("no propagation announce heard from ANY node within the window — "
                    "this box's ability to hear the class is unproven; streak held"))
        return None

    want = _normalize_rns_hash(configured)
    age = ages.get(want)
    if age is None and len(want) > 16:
        age = ages.get(want[:16])

    if age is not None and age <= _PROPAGATION_DARK_AFTER_S:
        # The window belongs in the CLEAN reason, not only in the fire text.
        # 2026-08-12: "configured node answered 178 min ago" was read — by me,
        # out loud, mid-session — as evidence gone stale, and it prompted a
        # request to make this probe do a live check. It is 3h into an 18h
        # window that is deliberately 3 announce periods wide (see
        # _PROPAGATION_DARK_AFTER_S). A disposition that states its evidence
        # without its budget invites exactly that misread, and the cure is one
        # f-string, not an architecture change: the ACTIVE leg already exists
        # as propagation_soak_degraded, which proves store-and-forward rather
        # than mere reachability, and duplicating it here would give one fault
        # two owners and put RNS traffic on a 30s loop.
        note_disposition(
            "lxmf_propagation_node_dark", "clean",
            reason=(f"configured node answered {age / 60:.0f} min ago "
                    f"(window {_PROPAGATION_DARK_AFTER_S / 3600:.0f}h; "
                    f"store-and-forward is propagation_soak_degraded's leg)"))
        _save_parity_streak(sp, 0)
        return None

    leg = "stale" if age is not None else "unheard"

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        note_disposition("lxmf_propagation_node_dark", "indeterminate",
                         reason=f"configured node {leg}; held by debounce")
        return None

    window_h = _PROPAGATION_DARK_AFTER_S / 3600.0
    if leg == "stale":
        what = (f"last announced {age / 3600:.1f}h ago (window {window_h:.0f}h) — "
                "it answered before and stopped")
        fix = ("Check the node's host: `systemctl is-active lxmd` and its "
               "NRestarts, then that its rnsd is healthy. ")
    else:
        what = (f"has NEVER been heard on this box (window {window_h:.0f}h) — "
                "most likely a wrong or truncated hash in gateway.json, which "
                "is the failure adoption itself introduces")
        fix = ("Re-check the configured hash against the node's own identity "
               "(32 hex chars, no truncation). ")
    return Signal(
        cls="lxmf_propagation_node_dark",
        subject=want[:16] or "propagation-node",
        severity="degraded",
        detail=(
            f"The CONFIGURED LXMF propagation node {want[:16]} {what}, while "
            f"{len(set(ages.values()))} other propagation announce(s) reached "
            f"this box (freshest {freshest_any / 60:.0f} min ago) — so the box "
            "can hear the class and this node specifically is not answering. "
            "Store-and-forward to OFFLINE peers is degraded: LXMF to a peer "
            "that is down right now fails outright again, as it did before "
            "adoption. Live delivery is unaffected. " + fix +
            "If the node is genuinely gone, either point rns.propagation_node "
            "at a replacement or clear it — clearing leaves the gap "
            "knowingly unwatched, which is a choice, not a regression."
        ),
        extra={
            "leg": leg,
            "configured": want,
            "age_h": None if age is None else round(age / 3600.0, 2),
            "window_h": window_h,
            "freshest_any_min": round(freshest_any / 60.0, 1),
            "propagation_nodes_heard": len(set(ages.values())),
            "debounce_streak": streak,
        },
    )

