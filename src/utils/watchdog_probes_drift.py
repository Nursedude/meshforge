"""Watchdog probes — declared-state vs live-state drift failure shapes.

Foundation perms drift, MeshForge<->MeshAnchor parity drift, RNS fork-pin
version drift, role drift, MQTT root drift (#77), cron verdict stale (#78),
kernel reboot pending (2026-06-09 version-updates arc).
Part of the ``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _journal_newest_match,
    _read_deployment_declaration,
    _resolve_main_pid,
)

# ─────────────────────────────────────────────────────────────────────
# Probe: permission-foundation drift (mf.4 / Issue #73 perms class)
# ─────────────────────────────────────────────────────────────────────


def probe_foundation_drift(
    *,
    perms=None,
) -> Optional[Signal]:
    """Surface a born-correct permission-foundation drift in the RNS config tree.

    The foundation SSOT (utils.fleet_foundation + the shared utils.rns_tree_perms)
    declares that a non-root rnsd must own/be-able-to-write its ``/etc/reticulum``
    tree (configdir ``root:<rnsd_user> 1775``, logfile/storage ``<rnsd_user>``). A
    re-provision that recreates the tree ``root:root`` while rnsd runs non-root is
    the recurrence path (moc1/moc2/moc, 2026-06-01) — every ``RNS.log()`` write then
    fails, which self-deadlocked the daemon pre-fork-mf.4 and loses all logs after.
    The fleet caught moc this way *manually* on the first audit; this probe makes it
    a continuously-monitored signal that flows to /fleet + the mini deep-rollup, so a
    drifted box self-surfaces instead of waiting for a hand-run audit.

    Scope: this checks the **RNS-tree perms** leg only — it derives the rnsd user
    from rnsd's own systemd unit (``probe_rns_tree_perms``), so it is correct no
    matter which user the watchdog runs as (it runs as root, where
    ``get_real_username`` would mislead). The **data-roots** leg of the foundation
    (operator-user-owned ``~/.config`` etc.) depends on the operator identity and is
    owned by the explicit, operator-run ``scripts/fleet_foundation.py audit`` /
    provisioner, not this root-context probe.

    Severity is ``degraded`` (not ``wedge``): a drifted box typically still serves —
    it is one logfile rotation from the wedge — and the fix is perms-only with no
    restart. Returns None when the foundation is clean, when rnsd runs as root
    (root writes anything), when the perms weren't probed (indeterminate — never
    guess), or when the foundation modules can't be imported.
    """
    try:
        from utils.rns_tree_perms import logfile_perms_drift, probe_rns_tree_perms
    except Exception:
        return None  # foundation tooling absent — indeterminate, don't false-alarm
    if perms is None:
        try:
            perms = probe_rns_tree_perms()
        except Exception:
            return None
    reason = logfile_perms_drift(perms)
    if not reason:
        return None
    detail = (
        f"{reason} | born-correct permission foundation drifted (mf.4/#73 perms "
        f"class). Fix (perms-only, no restart): "
        f"sudo python3 scripts/fleet_foundation.py apply"
    )
    return Signal(
        cls="foundation_perms_drift",
        subject="rnsd",
        severity="degraded",
        detail=detail,
        issue_ref=73,
        extra={
            "rnsd_user": perms.rnsd_user,
            "configdir_owner": perms.configdir_owner,
            "configdir_mode": perms.configdir_mode,
            "logfile_owner": perms.logfile_owner,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: MeshForge <-> MeshAnchor parity drift (lead-repo port debt)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_PARITY_DEBOUNCE_PATH = "/var/lib/meshforge/parity_debounce.json"


def _load_parity_streak(state_path: str) -> int:
    """Read the consecutive-drift streak counter. Best-effort: any error → 0.

    A missing/unreadable/garbage state means 'no confirmed streak yet', which
    suppresses a first-seen drift — exactly the conservative direction the
    debounce wants (favour silence on uncertainty, not a false page).
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_parity_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def probe_parity_drift(
    *,
    meshforge_root: str = "/opt/meshforge",
    meshanchor_root: str = "/opt/meshanchor",
    check_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Surface MeshForge<->MeshAnchor RNS-reliability parity drift.

    The two sister NOCs share the fleet's RNS substrate; reliability-critical files
    (the RNS-init chokepoint, the bridge contract, the rns_tree_perms SSOT, the
    fork-pin, lint MF009/MF019, the wedge probes) must stay in lockstep —
    ``scripts/parity_check.py`` is the lead-repo gate. This makes that audit a
    continuously-monitored signal so a divergence (someone edits one repo and
    forgets to port) self-surfaces in /fleet + the mini deep-rollup instead of
    rotting until the next manual run.

    Only meaningful where BOTH repos are present (e.g. the box holding the
    MeshAnchor clone). Returns None when ``meshanchor_root`` isn't a directory (a
    MeshForge-only fleet box — not applicable), when the parity tool can't be
    loaded, when everything's in sync, or when the result is merely ``missing`` (a
    tracked file absent — indeterminate / possible mid-deploy window, don't
    false-alarm). Fires ``degraded`` only on definite content ``drift`` — nothing is
    failing at runtime; the fix is to port the flagged change (MeshForge leads).

    **Debounce**: a drift must persist for ``debounce_ticks`` *consecutive* ticks
    before firing (default 2). The two repos sync seconds apart during a fleet
    roll, so a single tick can catch one repo mid-update and see a transient
    divergence that self-heals before the next tick — the 2026-06-01
    ``rns_tree_perms.py`` SSOT-port race did exactly this. A consecutive-drift
    streak (persisted to ``state_path``, default
    ``/var/lib/meshforge/parity_debounce.json``) rides out those in-flight blips
    while still surfacing a genuine forgotten port within one extra tick. Any
    non-drift result (in_sync / missing / tool error) resets the streak.
    """
    if not os.path.isdir(meshanchor_root):
        return None  # both repos required; MeshForge-only box → not applicable
    if state_path is None:
        state_path = DEFAULT_PARITY_DEBOUNCE_PATH
    if check_fn is None:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "parity_check",
                os.path.join(meshforge_root, "scripts", "parity_check.py"),
            )
            import sys
            mod = importlib.util.module_from_spec(spec)
            # Register before exec: on py3.12+ @dataclass resolves field types via
            # sys.modules[cls.__module__].__dict__ → AttributeError if absent. This
            # silently killed parity_drift + role_drift on the 3.13 fleet (found
            # 2026-06-08 inducing a live role_drift on moc1).
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            check_fn = mod.check_parity
        except Exception:
            return None  # parity tool unavailable → indeterminate, don't alarm
    try:
        findings, overall = check_fn(meshforge_root, meshanchor_root)
    except Exception:
        # Indeterminate — don't let a tool error count toward the streak.
        _save_parity_streak(state_path, 0)
        return None
    if overall != "drift":
        _save_parity_streak(state_path, 0)  # in_sync / missing → streak broken
        return None

    streak = _load_parity_streak(state_path) + 1
    _save_parity_streak(state_path, streak)
    if streak < debounce_ticks:
        return None  # drift seen, but not yet confirmed across consecutive ticks

    drifted = [f for f in findings if getattr(f, "status", None) == "drift"]
    items = ", ".join(f.label for f in drifted) or "?"
    detail = (
        f"MeshForge<->MeshAnchor parity drift ({len(drifted)} item(s)): {items} | "
        f"confirmed over {streak} consecutive ticks | RNS-reliability files must "
        f"match (MeshForge is the lead repo). Port the change, then verify: "
        f"python3 scripts/parity_check.py"
    )
    return Signal(
        cls="parity_drift",
        subject="meshforge<->meshanchor",
        severity="degraded",
        detail=detail,
        extra={"drift_items": [f.label for f in drifted], "debounce_streak": streak},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS/LXMF fork-pin version drift (RNS T2-isolate arc)
# ─────────────────────────────────────────────────────────────────────


def _read_pkg_versions_for_user(user, pkgs):
    """Read installed versions of ``pkgs`` from the service user's site-packages —
    read-only, no privilege change. Returns ``{pkg: version}`` for those found, or
    None if the user's site dir can't be located/read.

    Why not just ``importlib.metadata.version()``? That reads the *current*
    interpreter's env — the watchdog runs as root, whose env may carry a different
    rns (verified live: root had 1.1.1 while the wh6gxz service env had 1.2.5+mf.4).
    And we can't switch user: the watchdog unit sets NoNewPrivileges + RestrictSUIDSGID,
    which block sudo AND runuser (both need setuid). But ProtectHome=no, so root can
    READ ``/home/<user>/.local/...`` directly and point importlib.metadata at it.
    """
    if not user or user == "root":
        return None
    try:
        import importlib.metadata as _im
        home = Path(f"/home/{user}")
        site_dirs = [str(p) for p in sorted(home.glob(".local/lib/python3*/site-packages"))
                     if p.is_dir()]
        if not site_dirs:
            return None
        found = {}
        for dist in _im.distributions(path=site_dirs):
            try:
                name = (dist.metadata["Name"] or "").lower()
            except Exception:
                continue
            if name in pkgs:
                found[name] = dist.version
        return found
    except Exception:
        return None


def probe_rns_version_drift(
    *,
    rnsd_user=None,
    pins=None,
    installed=None,
) -> Optional[Signal]:
    """Surface a box running rns/lxmf off the pinned MeshForge-fork version.

    The fleet pins rns/lxmf on the ``+mf.N`` marker (``requirements/rns.txt``,
    gated by ``scripts/rns_version_check.py``) — upstream withdrew public support, so
    a bump is a *reviewed* decision, never an automatic pip-latest. This makes the
    check a continuously-monitored signal so a box that missed a fork roll (e.g. moc3
    was stock 1.2.5 before the mf.4 roll) self-surfaces in /fleet + the mini rollup.

    The pin (``pins``) is env-independent (just reads requirements/rns.txt). The
    INSTALLED versions are read from the rnsd **service user's** site-packages
    (see ``_read_pkg_versions_for_user`` for why root's own env / sudo / runuser all
    fail here). Fires ``degraded`` only on a concrete mismatch (installed != pinned
    for a package we can actually see). Returns None when compliant, when the pin or
    the user env can't be read (indeterminate — never false-alarm), or when a package
    isn't visible in the user site (possible venv install elsewhere — don't guess).
    """
    if rnsd_user is None and installed is None:
        try:
            from utils.rns_tree_perms import _read_rnsd_user
            rnsd_user = _read_rnsd_user()
        except Exception:
            rnsd_user = None

    if pins is None:
        try:
            import importlib.util
            script = str(Path(__file__).resolve().parents[2] / "scripts" / "rns_version_check.py")
            spec = importlib.util.spec_from_file_location("rns_version_check", script)
            import sys
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod  # register before exec (py3.12+ @dataclass eval; see probe_parity_drift)
            spec.loader.exec_module(mod)
            pins = mod.pinned_versions()
        except Exception:
            return None
    if not pins:
        return None  # no pin parseable (sub-arc A not applied) → indeterminate

    if installed is None:
        installed = _read_pkg_versions_for_user(rnsd_user, set(pins))
    if not installed:
        return None  # couldn't read the service env → indeterminate, no false alarm

    drift = []
    for pkg, want in pins.items():
        have = installed.get(pkg)
        if have is None:
            continue  # not visible in the user site (venv elsewhere?) — don't guess
        if have != want:
            drift.append(f"{pkg} installed={have} pinned={want}")
    if not drift:
        return None

    detail = (
        f"rns/lxmf off the pinned MeshForge-fork version ({'; '.join(drift)}). "
        f"Upstream withdrew support so the pin is deliberate — converge with a "
        f"REVIEWED bump: pip install --force-reinstall -r requirements/rns.txt, "
        f"then verify rnsd."
    )
    return Signal(
        cls="rns_version_drift",
        subject="rns/lxmf",
        severity="degraded",
        detail=detail,
        extra={"rnsd_user": rnsd_user, "drift": drift},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: declared-role drift (fleet_roles.yaml + overrides vs live units)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_ROLE_DRIFT_DEBOUNCE_PATH = "/var/lib/meshforge/role_drift_debounce.json"

# plan() verbs that mean "the box would change under converge" = real drift.
_ROLE_DRIFT_VERBS = ("enable", "disable", "mask")


def _plan_role_actions(role: str, overrides: dict, meshforge_root: str):
    """Default plan_fn: importlib-load ``scripts/provision_role.py`` (the
    converge SSOT) and return its ``plan()`` actions for this box's effective
    declaration — base role flattened through ``inherits`` plus the box's
    documented ``service_overrides``.

    Returns ``None`` when the tooling/catalog can't be loaded (indeterminate);
    raises ``KeyError`` for a role missing from the catalog (a REAL mismatch
    the caller counts as drift — e.g. deployment.json names a role the box's
    fleet_roles.yaml doesn't carry yet).
    """
    try:
        import importlib.util
        script = os.path.join(meshforge_root, "scripts", "provision_role.py")
        spec = importlib.util.spec_from_file_location("provision_role", script)
        import sys
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # register before exec (py3.12+ @dataclass eval; see probe_parity_drift)
        spec.loader.exec_module(mod)
        catalog = mod.load_roles(mod.DEFAULT_ROLES_FILE)
    except Exception:
        return None  # tool/catalog unavailable → indeterminate, don't alarm
    role_def = mod.resolve_role(catalog, role)  # KeyError → unknown role
    return mod.plan(role_def, overrides)


def probe_role_drift(
    *,
    meshforge_root: str = "/opt/meshforge",
    deployment: Optional[Tuple[Optional[str], dict]] = None,
    plan_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Surface a box whose live systemd unit state diverges from its declared role.

    The fleet's role model (``docs/fleet_roles.yaml`` + per-box
    ``deployment.json`` ``role``/``service_overrides``) is converged only when an
    operator runs ``provision_role.py --apply`` — between runs, nothing alerted on
    divergence. The 2026-06-03 architecture audit hit exactly this legibility gap
    (moc2 read as "full-gateway" while deliberately not bridging) — see
    ``.claude/research/fleet_architecture_2026_06_03.md`` §7-B. This probe makes
    the converge SSOT's own dry-run plan a continuously-monitored signal.

    Drift = any plan action whose verb is enable/disable/mask (the box would
    change under converge) or a BLOCKING warning (required unit not installed; a
    waiver missing its required ``reason``). **Documented overrides are honored**
    — ``plan()`` reports them as non-blocking advisories, which do NOT fire (the
    moc2 lesson: a declared, reasoned exception is not drift). A role missing
    from the catalog counts as drift (mismatched declaration).

    Returns ``None`` when the box declares no role (not applicable), when the
    tool/catalog can't be loaded (indeterminate — never false-alarm), or while a
    divergence hasn't yet persisted ``debounce_ticks`` consecutive ticks — role
    catalog (git) and unit state (converge/restarts) deploy independently, so a
    single tick can catch a fleet-roll window (same rationale as
    ``probe_parity_drift``). Severity ``degraded``: latent legibility debt, not
    an active failure.
    """
    if state_path is None:
        state_path = DEFAULT_ROLE_DRIFT_DEBOUNCE_PATH
    if deployment is None:
        try:
            from utils.rns_tree_perms import _read_rnsd_user
            service_user = _read_rnsd_user()
        except Exception:
            service_user = None
        deployment = _read_deployment_declaration(service_user)
    role, overrides = deployment
    if not role:
        _save_parity_streak(state_path, 0)
        return None  # box not role-declared (or unreadable) → not applicable

    if plan_fn is None:
        def plan_fn(r, ov):
            return _plan_role_actions(r, ov, meshforge_root)

    unknown_role = False
    try:
        actions = plan_fn(role, overrides)
    except KeyError:
        unknown_role = True
        actions = []
    except Exception:
        _save_parity_streak(state_path, 0)
        return None  # tool error → indeterminate, don't count toward streak
    if actions is None and not unknown_role:
        _save_parity_streak(state_path, 0)
        return None

    if unknown_role:
        items = [f"role '{role}' not in the fleet_roles.yaml catalog"]
    else:
        items = []
        for a in actions:
            verb = getattr(a, "verb", "")
            if verb in _ROLE_DRIFT_VERBS or (
                verb == "warn" and getattr(a, "required", False)
            ):
                items.append(
                    f"{getattr(a, 'item', '?')}: "
                    f"{getattr(a, 'current', '?')} -> {getattr(a, 'desired', '?')}"
                )
    if not items:
        _save_parity_streak(state_path, 0)
        return None

    streak = _load_parity_streak(state_path) + 1
    _save_parity_streak(state_path, streak)
    if streak < debounce_ticks:
        return None  # divergence seen, not yet confirmed across consecutive ticks

    shown = "; ".join(items[:4]) + (f" (+{len(items) - 4} more)" if len(items) > 4 else "")
    detail = (
        f"live unit state diverges from declared role '{role}' "
        f"({len(items)} item(s)): {shown} | confirmed over {streak} consecutive "
        f"ticks | documented service_overrides are honored (not drift). Review: "
        f"python3 scripts/provision_role.py (dry-run); converge with sudo "
        f"python3 scripts/provision_role.py --apply, or correct the declared role."
    )
    return Signal(
        cls="role_drift",
        subject=role,
        severity="degraded",
        detail=detail,
        extra={"items": items, "debounce_streak": streak},
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: MQTT root-topic drift (Issue #77 — the msh/US split guard)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_MQTT_ROOT_DEBOUNCE_PATH = "/var/lib/meshforge/mqtt_root_debounce.json"

# Extract the effective root prefix from a meshtasticd json-uplink journal
# line: "JSON publish message to msh/2/json/LongFast/!32962f10, 163 bytes:..."
# → "msh". A region-form root (mqtt.root="msh/US") yields "msh/US" — exactly
# the pre-unification moc2 drift shape this probe exists to catch.
_MQTT_PUBLISH_TOPIC_RE = re.compile(
    r"JSON publish message to (\S+?)/2/json/"
)

# The GatewayConfig dataclass default (gateway/config.py MQTTBridgeConfig).
# Used when gateway.json omits the key — that IS the consumer's effective
# value, so comparing against it is honest, not a guess.
_GATEWAY_DEFAULT_ROOT_TOPIC = "msh"


def _read_declared_root_topic(service_user) -> Optional[str]:
    """Read ``mqtt_bridge.root_topic`` from the service user's gateway.json.

    The watchdog runs as sandboxed root — home is derived from the service
    user and READ directly, never escalate (the rns_version_drift lesson).
    Returns the configured value, the GatewayConfig default when the key is
    absent (that is the consumer's effective root), or None when the file is
    unreadable/unparseable (indeterminate → caller stays silent).
    """
    if not service_user:
        return None
    try:
        import pwd
        home = pwd.getpwnam(service_user).pw_dir
        path = os.path.join(home, ".config", "meshforge", "gateway.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        mqtt = data.get("mqtt_bridge")
        if not isinstance(mqtt, dict):
            return _GATEWAY_DEFAULT_ROOT_TOPIC
        root = mqtt.get("root_topic", _GATEWAY_DEFAULT_ROOT_TOPIC)
        if isinstance(root, str) and root:
            return root.strip().strip("/")
        return _GATEWAY_DEFAULT_ROOT_TOPIC
    except (KeyError, OSError, ValueError, TypeError):
        return None


def probe_mqtt_root_drift(
    *,
    unit: str = "meshtasticd.service",
    lookback: str = "6h",
    journalctl_path: str = "journalctl",
    systemctl_path: str = "systemctl",
    main_pid: Optional[int] = None,
    newest_line_fn=None,
    declared_root: Optional[str] = None,
    service_user_fn=None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when the radio's MQTT publish root drifts from the declared root.

    Issue #77 (2026-06-06 fleet unification): the fleet standardized on an
    explicit ``mqtt.root msh`` after the msh/US topic-root split silently
    fractured the uplink namespace (moc2 flipped 06-06 23:41). The remaining
    hole: a zero-config radio join (or a factory-reset radio) reintroduces a
    divergent root and every consumer pinned to the declared root partially
    or fully misses its feed — the dark-feed class again, but with the CAUSE
    visible hours before ``channel_feed_dark`` can prove the symptom.

    Local invariant, no fleet consensus needed (brokers are per-box islands):
    the root prefix the radio actually publishes under — observable in
    meshtasticd's json-uplink journal lines, ``JSON publish message to
    <root>[/<region>]/2/json/<channel>/!<id>`` — must equal the box's own
    declared consumer root (``gateway.json mqtt_bridge.root_topic``; an
    absent key means the GatewayConfig default ``msh``, which is the
    effective value, so the comparison stays honest).

    Observation is journal-only: never queries the radio (a per-tick CLI
    ``--get mqtt.root`` would open a TCP connection against the PhoneAPI —
    the #17 contention class this probe family exists to police).

    Self-guards (returns None):
    - meshtasticd inactive (``service_inactive`` owns that)
    - no json publish lines in the lookback (unobservable ≠ drift — the
      RX-only collector case, same gate as ``channel_feed_dark``)
    - gateway.json unreadable / service user unresolvable (indeterminate)
    - drift seen but not yet ``debounce_ticks`` consecutive ticks (rides out
      an operator mid-rotation: radio flipped before config, or vice versa)

    Recovery: ``meshtastic --host localhost --set mqtt.root <declared>``
    (or fix root_topic in gateway.json if the radio is the intended truth),
    then verify the next journal publish line carries the declared root.
    """
    pid = main_pid if main_pid is not None else _resolve_main_pid(
        unit, systemctl_path=systemctl_path
    )
    if pid is None:
        return None

    if newest_line_fn is None:
        def newest_line_fn(pattern: str) -> Optional[str]:
            return _journal_newest_match(
                unit, pattern, lookback, journalctl_path=journalctl_path
            )

    line = newest_line_fn("JSON publish message to ")
    if line is None:
        return None  # no json uplink at all — unobservable, not drift
    m = _MQTT_PUBLISH_TOPIC_RE.search(line)
    if m is None:
        return None  # publish line shape changed — indeterminate, not drift
    observed = m.group(1).strip("/")

    if declared_root is not None:
        declared = declared_root.strip().strip("/")
    else:
        user_fn = service_user_fn
        if user_fn is None:
            from utils.rns_tree_perms import _read_rnsd_user
            user_fn = _read_rnsd_user
        declared = _read_declared_root_topic(user_fn())
    if not declared:
        return None  # declared side indeterminate — never alarm on a guess

    sp = state_path or DEFAULT_MQTT_ROOT_DEBOUNCE_PATH
    if observed == declared:
        _save_parity_streak(sp, 0)
        return None

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        return None  # drift seen, not yet confirmed across consecutive ticks

    detail = (
        f"Radio publishes MQTT under root '{observed}' but this box's "
        f"declared consumer root is '{declared}' "
        f"(gateway.json mqtt_bridge.root_topic) — confirmed over {streak} "
        f"consecutive ticks. Consumers subscribed under '{declared}' are "
        f"partially or fully deaf to this radio's uplink (the msh/US split, "
        f"Issue #77; a zero-config radio join is the usual cause). Fix: "
        f"meshtastic --host localhost --set mqtt.root '{declared}' (or "
        f"correct root_topic in gateway.json if the radio is the intended "
        f"truth), then verify the next 'JSON publish message to' journal "
        f"line carries '{declared}'."
    )
    return Signal(
        cls="mqtt_root_drift",
        subject="meshtasticd",
        severity="degraded",
        detail=detail,
        issue_ref=77,
        extra={
            "observed_root": observed,
            "declared_root": declared,
            "streak": streak,
            "lookback": lookback,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Cron-verdict coverage (Issue #78) — a cron WIRED to cron_verdict.sh that
# reported FAIL/CONCERN or went silent past its schedule cadence. Cross-
# references the crontab so a stale ORPHAN verdict (a one-off verdict for a
# cron that no longer exists, e.g. the diag24h_watchdog line) never fires.
# Inert until crons are wired — the regime is opt-in.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_CRON_VERDICT_DEBOUNCE_PATH = "/var/lib/meshforge/cron_verdict_debounce.json"
CRON_VERDICT_STALE_FLOOR_S = 2 * 3600.0      # don't flag faster than this (anti-flap)
CRON_VERDICT_CADENCE_MULT = 3.0              # stale if age > MULT × expected interval
_CRON_VERDICT_FALLBACK_MAX_S = 26 * 3600.0   # unparseable schedule → panel's 26h
_CRON_WIRED_RE = re.compile(r'cron_verdict\.sh\s+(\S+)')


def _cron_max_interval(schedule: str) -> float:
    """Coarse expected-max gap (seconds) for a 5-field cron schedule or
    ``@keyword``. Intentionally approximate — catch gross silence, not exact
    scheduling. Unparseable → the panel's 26h fallback. ``@reboot`` → inf
    (only runs at boot, never stale-checkable)."""
    if not isinstance(schedule, str):
        return _CRON_VERDICT_FALLBACK_MAX_S
    s = schedule.strip()
    kw = {
        "@hourly": 3600.0, "@daily": 86400.0, "@midnight": 86400.0,
        "@weekly": 604800.0, "@monthly": 2592000.0,
        "@yearly": 31536000.0, "@annually": 31536000.0,
        "@reboot": float("inf"),
    }
    if s in kw:
        return kw[s]
    fields = s.split()
    if len(fields) < 5:
        return _CRON_VERDICT_FALLBACK_MAX_S
    minute, hour, dom, mon, dow = fields[:5]
    mm = re.match(r'^\*/(\d+)$', minute)
    if mm:
        try:
            return max(60.0, int(mm.group(1)) * 60.0)
        except ValueError:
            return _CRON_VERDICT_FALLBACK_MAX_S
    if minute == "*":
        return 60.0
    # specific minute from here → at most hourly granularity
    if hour == "*":
        return 3600.0
    hm = re.match(r'^\*/(\d+)$', hour)
    if hm:
        try:
            return max(3600.0, int(hm.group(1)) * 3600.0)
        except ValueError:
            return _CRON_VERDICT_FALLBACK_MAX_S
    # specific minute + specific hour
    if dow != "*":
        return 604800.0    # weekly
    if dom != "*" or mon != "*":
        return 2592000.0   # monthly-ish
    return 86400.0         # daily


def _read_operator_crontab_spool(name: Optional[str]) -> Optional[str]:
    """Read the operator's crontab from the spool — as root, in-process, no
    sudo (the watchdog's NoNewPrivileges sandbox forbids privilege change).
    Debian path first, then RHEL-style. None on missing/unreadable."""
    if not name or name == "root":
        return None
    for path in (f"/var/spool/cron/crontabs/{name}", f"/var/spool/cron/{name}"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except (FileNotFoundError, IsADirectoryError):
            continue
        except OSError:
            return None
    return None


def _read_operator_verdicts_log(home: Optional[str]) -> Optional[str]:
    """Read ``~/cron_verdicts.log`` as root, in-process. None on absent/unreadable."""
    if not home:
        return None
    try:
        with open(os.path.join(str(home), "cron_verdicts.log"),
                  "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (FileNotFoundError, IsADirectoryError):
        return None
    except OSError:
        return None


def probe_cron_verdict_stale(
    *,
    operator: Optional[Tuple[int, str]] = None,
    crontab_text: Optional[str] = None,
    verdicts_text: Optional[str] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when a cron WIRED to cron_verdict.sh reported FAIL/CONCERN or went
    silent past its schedule cadence — "silence is the failure mode" for the
    cron-verdict regime (Issue #78).

    Reads the operator's crontab (spool) + ``~/cron_verdicts.log`` directly as
    root (no sudo — watchdog sandbox). Only WIRED crons (a ``cron_verdict.sh
    <name>`` in the crontab line) are judged, so a stale ORPHAN verdict for a
    cron that no longer exists never false-alarms. INERT (None) on any box with
    no wired crons — the regime is opt-in. 2-tick debounce rides a mid-run
    window where a fresh run hasn't recorded yet. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_CRON_VERDICT_DEBOUNCE_PATH

        # 1. Resolve operator (root-safe) — only when nothing is injected.
        if operator is None and crontab_text is None and verdicts_text is None:
            try:
                from utils.fleet_test_runner import _find_operator_user
                operator = _find_operator_user()
            except Exception:
                operator = None
        op_name = operator[1] if operator else None

        # 2. Wired crontab → {name: schedule}. No wired cron → inert.
        if crontab_text is None:
            crontab_text = _read_operator_crontab_spool(op_name)
        wired: Dict[str, str] = {}
        if crontab_text:
            try:
                from utils.fleet_snapshot import _parse_crontab
                for job in _parse_crontab(crontab_text):
                    m = _CRON_WIRED_RE.search(job.get("command", ""))
                    if m:
                        wired[m.group(1)] = job.get("schedule", "")
            except Exception:
                wired = {}
        if not wired:
            _save_parity_streak(sp, 0)   # nothing to watch — clear + inert
            return None

        # 3. Verdict log → latest verdict per name.
        if verdicts_text is None and operator is not None:
            home = None
            try:
                import pwd
                home = pwd.getpwuid(operator[0]).pw_dir
            except (KeyError, OSError):
                home = None
            verdicts_text = _read_operator_verdicts_log(home)
        latest: Dict[str, dict] = {}
        if verdicts_text:
            try:
                from utils.fleet_snapshot import _parse_cron_verdicts
                for v in _parse_cron_verdicts(verdicts_text, now):
                    latest[v["name"]] = v
            except Exception:
                latest = {}

        # 4. Cross-reference — judge ONLY wired crons (orphans ignored).
        failed: List[str] = []
        stale: List[str] = []
        for name, schedule in sorted(wired.items()):
            v = latest.get(name)
            if v is not None and v.get("status", "").upper().startswith(
                    ("FAIL", "CONCERN")):
                failed.append(f"{name}({v.get('status')})")
                continue
            max_age = _cron_max_interval(schedule)
            if max_age == float("inf"):
                continue   # @reboot — not stale-checkable
            threshold = max(CRON_VERDICT_STALE_FLOOR_S,
                            CRON_VERDICT_CADENCE_MULT * max_age)
            if v is None:
                stale.append(f"{name}(never)")
            elif float(v.get("age_s", 0.0)) > threshold:
                stale.append(f"{name}({int(float(v['age_s']) // 3600)}h)")

        if not failed and not stale:
            _save_parity_streak(sp, 0)
            return None

        # 5. Debounce — first sighting silent, fire on the 2nd consecutive tick.
        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            return None

        bits = []
        if failed:
            bits.append(f"{len(failed)} failing: " + ", ".join(failed[:5]))
        if stale:
            bits.append(f"{len(stale)} silent: " + ", ".join(stale[:5]))
        return Signal(
            cls="cron_verdict_stale",
            subject="cron",
            severity="degraded",
            detail=("Wired cron(s) unhealthy — " + "; ".join(bits)
                    + " (fix the job or re-run + re-verify; silence is the "
                    "failure mode)"),
            issue_ref=78,
            extra={"failed": failed, "stale": stale, "streak": streak,
                   "wired_count": len(wired)},
        )
    except Exception:
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

    Read-only, sandbox-safe (no sudo — the watchdog's NoNewPrivileges sandbox
    forbids it; ``/lib/modules`` and the flag file are world-readable anyway).

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
    if running_parsed is not None:
        run_nums, run_flavor = running_parsed
        try:
            entries = [e.name for e in os.scandir(modules_dir) if e.is_dir()]
        except OSError:
            entries = []  # unreadable → leg indeterminate, never an alarm
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
        _save_parity_streak(sp, 0)
        return None

    streak = _load_parity_streak(sp) + 1
    _save_parity_streak(sp, streak)
    if streak < debounce_ticks:
        return None  # pending seen, not yet confirmed across consecutive ticks

    reasons = []
    if newer_found and newest_installed:
        reasons.append(
            f"running {running_release} but {newest_installed} is installed "
            f"under {modules_dir}"
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
            "reboot_required_file": reboot_required,
            "debounce_streak": streak,
        },
    )


