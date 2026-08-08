"""AREDN organ probes for the MeshForge watchdog.

Split out of ``watchdog_probes_env.py`` on 2026-07-20 when that file crossed the
MF025 1,500-line cap (the cap is a split trigger, never a number to raise).
Pure code motion — no behaviour change. ``watchdog_probes_env`` re-exports every
public name here, so existing import paths (including
``watchdog_probes_drift``'s back-compat re-exports) keep working.

Two probes, one organ, deliberately distinct owners:

- :func:`probe_aredn_source_dark` — the box made a STATEMENT (configured
  ``aredn_node_ips``, or declared ``organ_expectations.aredn``) and reality
  disagrees with it.
``probe_aredn_organ_undeclared`` (the "made NO statement at all" leg) was
removed 2026-08-08 by the signal-class yield audit: it was ``inert`` on all 8
boxes — 4 with no AREDN LAN, 3 where ``aredn_node_ips`` is configured and the
legs above own the box — so its only firing state was a bring-up box with an
AREDN LAN and no config, a state a human is present for. See
``.claude/audits/signal_class_yield_2026_08_08.md``; the structural-dark row
``aredn_configured_source_only`` in ``fleet_truth.py`` records the gap as
knowingly reopened rather than silently dropped.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import List, Optional

from utils.watchdog_probe_core import (
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)

# ─────────────────────────────────────────────────────────────────────
# Probe: AREDN local-source dark (Phase 0 AREDN organ, 2026-06-12)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_AREDN_SOURCE_DEBOUNCE_PATH = "/var/lib/meshforge/aredn_source_debounce.json"

# Diagnostics reasons that mean "configured, but the organ sees nothing".
# "unreachable" = AREDN node / LAN path dark; "not_configured" = the RUNNING
# service predates the config file (settings load at startup — restart loads).
_AREDN_DARK_REASONS = ("unreachable", "not_configured")


def _read_configured_aredn_ips_state(service_user):
    """Tri-state read of ``aredn_node_ips`` from the service user's
    map_settings.json: ``(ips, state)`` with state in
    ``ok`` | ``absent`` | ``unreadable``.

    The two-state version collapsed "the settings file does not exist" into the
    same ``None`` as "the settings file is corrupt/denied" — and the caller
    treats ``None`` as indeterminate and returns EARLY, before the declaration
    leg. So a box that DECLARES the AREDN organ and then loses its whole
    map_settings.json — the strongest form of the very wipe class the
    declaration exists to catch — stayed permanently silent
    (honest_failure_modes #1: a degraded state mapped to a valid-looking value).

    ``absent`` is a legitimate observation ("no configuration is expressed
    here"), NOT a failure to observe: the caller may act on it. ``unreadable``
    stays indeterminate — a file we cannot parse tells us nothing about intent.
    Sandboxed-root direct read, never escalate (the rns_version_drift lesson).
    """
    if not service_user:
        return None, "unreadable"
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
    except (KeyError, OSError):
        return None, "unreadable"
    path = os.path.join(home, ".config", "meshforge", "map_settings.json")
    if not os.path.exists(path):
        return [], "absent"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        raw = data.get("aredn_node_ips", [])
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return [], "ok"
        return [ip.strip() for ip in raw
                if isinstance(ip, str) and ip.strip()], "ok"
    except (KeyError, OSError, ValueError, TypeError):
        return None, "unreadable"


def _read_configured_aredn_ips(service_user) -> Optional[List[str]]:
    """Two-state wrapper kept for callers that only need the list.

    Returns the configured list (possibly empty = organ deliberately off, or
    no settings file) or None when the file exists but is unreadable.
    """
    ips, _state = _read_configured_aredn_ips_state(service_user)
    return ips


def _read_aredn_expectation(service_user):
    """Read the box's AREDN declaration from deployment.json
    ``organ_expectations.aredn`` (per-box overrides layer — the generic
    fleet_roles.yaml carries no instance values, MF014).

    Returns ``(declared, why)``: ``(True, <reason str>)`` when the box
    declares it should run the AREDN organ; ``(False, "")`` when the file is
    absent or carries no declaration (no expectation — benign); ``(None,
    "")`` when the file exists but is unreadable/unparseable (declaration
    unknown — indeterminate, never treated as "not declared")."""
    if not service_user:
        return None, ""
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
    except (KeyError, OSError):
        return None, ""
    path = os.path.join(home, ".config", "meshforge", "deployment.json")
    if not os.path.exists(path):
        return False, ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        organs = data.get("organ_expectations")
        if not isinstance(organs, dict) or "aredn" not in organs:
            return False, ""
        why = organs["aredn"]
        return True, why if isinstance(why, str) else ""
    except (OSError, ValueError, TypeError):
        return None, ""


def _fetch_local_status_json(status_url: str, timeout_s: float) -> Optional[dict]:
    """GET the local map status JSON. None on ANY failure (indeterminate)."""
    import urllib.request
    try:
        req = urllib.request.Request(
            status_url, headers={"User-Agent": "meshforge-watchdog"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def probe_aredn_source_dark(
    *,
    status_url: str = "http://127.0.0.1:5000/api/status",
    timeout_s: float = 3.0,
    configured_ips: Optional[List[str]] = None,
    service_user_fn=None,
    status_fetch_fn=None,
    expectation_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when a CONFIGURED local AREDN sysinfo source has gone dark —
    or when the box DECLARES the organ but carries no config at all.

    Phase 0 of the AREDN arc (2026-06-12): the box at the AREDN site was
    found with its local AREDN organ silently dormant — ``aredn_node_ips``
    never set, every "AREDN" node on the map coming from the worldmap
    fallback. Once the organ IS configured, two dark shapes must page
    instead of rotting silently (silence is the failure mode):

    - ``unreachable`` — none of the configured node IPs answered sysinfo
      (node down, LAN path broken, or the node's :8080 service dead).
    - ``not_configured`` reported by a RUNNING service while the settings
      file carries IPs — the service started before the config landed;
      a restart loads it. Without this leg, "I configured it" and "it
      runs" disagree forever and nobody is told.

    Reads intent from the service user's map_settings.json (sandboxed-root
    direct read) and observation from the local map's ``/api/status``
    ``source_diagnostics.aredn`` block (written fresh on every collect).

    Role-aware leg (2026-07-19, closes the aredn_configured_source_only
    structural-dark row): a box may DECLARE the AREDN organ in its
    deployment.json ``organ_expectations.aredn`` (per-box overrides layer;
    the generic fleet_roles.yaml carries no instance values). Declared +
    empty/absent ``aredn_node_ips`` = the config-wiped / never-configured
    site the configured-only probe was structurally blind to → fires
    degraded after the same debounce. Undeclared boxes keep the old INERT
    behavior; an unreadable deployment.json is indeterminate, never "not
    declared".

    Honest failure modes: unreadable settings → None (intent unknown, never
    alarm); empty/absent ``aredn_node_ips`` on an UNDECLARED box → None +
    streak reset (organ deliberately off — INERT by design); status endpoint
    unreachable/malformed or diagnostics block absent → None with the streak
    HELD (http_local owns the wedge; a one-tick status hiccup must not erase
    confirmed-dark progress); ``no_positions``, ``slow_sysinfo`` or
    ``yielded>0`` → healthy, streak reset (reachable-but-no-GPS, or
    reachable-but-slow on a constrained mips_24kc router, is an alive organ —
    slow ≠ dark, the 2026-06-16 moc5 lesson); unknown reason strings →
    indeterminate, held. 2-tick debounce rides out a single failed collect
    (node reboot, transient LAN blip). Severity ``degraded`` — the map keeps
    serving, the AREDN leg is blind.
    """
    user_fn = service_user_fn
    if user_fn is None:
        from utils.rns_tree_perms import _read_rnsd_user
        user_fn = _read_rnsd_user
    ips = configured_ips
    settings_state = "ok"
    if ips is None:
        ips, settings_state = _read_configured_aredn_ips_state(user_fn())
    if ips is None:
        note_disposition(
            "aredn_source_dark", "indeterminate",
            reason="map settings unreadable; intent unknown",
        )
        return None  # intent unreadable — indeterminate, never alarm
    sp = state_path or DEFAULT_AREDN_SOURCE_DEBOUNCE_PATH
    if not ips:
        # Role-aware leg: does this box DECLARE the AREDN organ? A declared
        # box with no config is the wipe class, not a deliberately-off organ.
        if expectation_fn is not None:
            declared, why = expectation_fn()
        else:
            declared, why = _read_aredn_expectation(user_fn())
        if declared is None:
            note_disposition(
                "aredn_source_dark", "indeterminate",
                reason="deployment.json unreadable; declaration unknown",
            )
            return None  # declaration unknown — never read as "not declared"
        if not declared:
            note_disposition(
                "aredn_source_dark", "inert",
                reason="aredn_node_ips not configured — organ deliberately off",
            )
            _save_parity_streak(sp, 0)
            return None  # undeclared + unconfigured — inert by design
        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "aredn_source_dark", "indeterminate",
                reason="declared-unconfigured seen; held by debounce",
            )
            return None
        return Signal(
            cls="aredn_source_dark",
            subject="declared-unconfigured",
            severity="degraded",
            detail=(
                "AREDN organ DECLARED for this box (deployment.json "
                "organ_expectations.aredn"
                + (f": {why}" if why else "")
                + ") but "
                + ("map_settings.json is ABSENT entirely"
                   if settings_state == "absent"
                   else "aredn_node_ips is empty/absent in map_settings.json")
                + " — the config-wiped / never-configured "
                "site the configured-only probe couldn't see. Fix: restore "
                "aredn_node_ips in ~/.config/meshforge/map_settings.json "
                "then restart meshforge-map, or remove the "
                "organ_expectations.aredn declaration if the site truly "
                "changed."
            ),
            extra={"declared_reason": why, "debounce_streak": streak,
                   "settings_state": settings_state},
        )

    fetch = status_fetch_fn or (
        lambda: _fetch_local_status_json(status_url, timeout_s)
    )
    status = fetch()
    if not isinstance(status, dict):
        note_disposition(
            "aredn_source_dark", "indeterminate",
            reason="local status endpoint unreadable; streak held",
        )
        return None  # status unreadable — hold streak; http_local owns the wedge
    diags = status.get("source_diagnostics")
    if not isinstance(diags, dict):
        note_disposition(
            "aredn_source_dark", "indeterminate",
            reason="source_diagnostics absent from status",
        )
        return None  # diagnostics surface absent — indeterminate, hold
    diag = diags.get("aredn")
    if not isinstance(diag, dict):
        note_disposition(
            "aredn_source_dark", "indeterminate",
            reason="no aredn diagnostics yet (no collect since start)",
        )
        return None  # no aredn entry yet (no collect since start) — hold

    yielded = diag.get("yielded")
    reason = diag.get("reason_if_zero")
    if isinstance(yielded, int) and yielded > 0:
        note_disposition("aredn_source_dark", "clean")
        _save_parity_streak(sp, 0)
        return None  # organ alive
    if reason in ("no_positions", "slow_sysinfo"):
        note_disposition("aredn_source_dark", "clean")
        _save_parity_streak(sp, 0)
        return None  # reachable (no GPS, or sysinfo slow) — alive, NOT dark
    if reason not in _AREDN_DARK_REASONS:
        note_disposition(
            "aredn_source_dark", "indeterminate",
            reason="unknown diagnostics reason string; held",
        )
        return None  # unknown/absent reason — indeterminate, hold

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        note_disposition(
            "aredn_source_dark", "indeterminate",
            reason="dark seen; held by 2-tick debounce",
        )
        return None  # dark seen, not yet confirmed across consecutive ticks

    if reason == "not_configured":
        hint = (
            "the RUNNING service reports not_configured while map_settings.json "
            "carries aredn_node_ips — it started before the config landed; fix: "
            "sudo systemctl restart meshforge-map.service"
        )
    else:
        hint = (
            "none of the configured AREDN node IPs answered sysinfo — check the "
            "node (web UI :8080), its power/PoE, and the LAN path from this box"
        )
    detail = (
        f"AREDN local source dark — configured IPs {ips} but the collector "
        f"reports '{reason}' (yielded 0), confirmed over {streak} consecutive "
        f"ticks. {hint}."
    )
    return Signal(
        cls="aredn_source_dark",
        subject=ips[0],
        severity="degraded",
        detail=detail,
        extra={
            "configured_ips": list(ips),
            "reason": reason,
            "attempted": diag.get("attempted"),
            "yielded": yielded,
            "debounce_streak": streak,
        },
    )
