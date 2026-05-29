"""NOC fix-routing: a detected service state → the in-app action that fixes it.

This generalizes the mini-dudeai rule→action map (`mini_dudeai._fixes_for`) to the
service-status domain, so ANY monitoring view (Dashboard service status, Stack
Health, future widgets) can turn a DEGRADED service into a one-keystroke fix via
the shared remediation surface (`remediation.propose_remediation`).

The point — the operator never navigates to the fix. The fix comes to them where
they saw the problem. That is the In-Domain Principle extended one step: not just
"never quit the app to fix it," but "never even leave the status view to fix it."
See `.claude/foundations/in_domain_principle.md` and `remediation.py`.

The profile gate (`service_enabled_here`): a fix is offered ONLY for a service
this box is configured to run — the systemd unit exists AND is enabled-at-boot.
A disabled or absent unit means the service is intentionally off here (e.g. a
gateway-only box's map daemon, even when its deployment profile marks "maps"
available — the 'full' profile sets maps=True yet the unit is disabled). The
systemd enabled-state is the accurate per-box signal; the profile feature flag is
not. So gateway-only boxes are never nagged to "fix" intentionally-off services.

Conservative by design: only services this box OWNS, where a start/restart is a
genuine, safe local recovery. An unknown service yields no actions.
"""
from __future__ import annotations

from typing import List, Tuple

# Services this box may own where start/restart is a real, safe local fix.
# (rnsd can ALSO route to the guided RNS repair wizard — a richer follow-up;
#  for now we offer the safe start/restart, which recovers the common cases.)
_KNOWN_SERVICES = {
    "meshtasticd": "the Meshtastic radio daemon",
    "rnsd": "the Reticulum shared-instance daemon",
    "mosquitto": "the local MQTT broker",
    "meshforge-gateway": "the RNS<->Meshtastic gateway bridge",
    "meshforge": "the RNS<->Meshtastic gateway bridge",
    "meshforge-map": "the local map / federation server",
    "meshforge-maps": "the multi-source map extension",
}


def restart_action(service: str, label: str, description: str):
    """Shared builder: a remediation action that restarts a LOCAL service via
    the SSOT (``utils.service_check.restart_service``).

    Public — the one place "restart service X" actions are built. Used by
    ``service_fix_actions`` here and by mini-dudeai's rule→fix map, so the two
    don't carry duplicate copies.
    """
    from remediation import RemediationAction
    from utils.service_check import restart_service
    return RemediationAction(
        label=label, description=description,
        apply=lambda: restart_service(service), requires_admin=True,
    )


def _start_action(service: str, label: str, description: str):
    """A remediation action that starts a LOCAL service via the SSOT."""
    from remediation import RemediationAction
    from utils.service_check import start_service
    return RemediationAction(
        label=label, description=description,
        apply=lambda: start_service(service), requires_admin=True,
    )


def service_fix_actions(service_name: str, running: bool) -> List:
    """Return in-app actions to recover a degraded/stopped local service.

    Args:
        service_name: systemd unit / service name as shown in a status view.
        running: True if the service is up but unhealthy (e.g. active-but-
            unreachable) — a restart is the fix. False if it's stopped/failed —
            offer start first, then restart.

    Returns an empty list for services we don't own / can't safely auto-fix.
    Note: this is the "what COULD fix it" map; the profile gate
    (`service_enabled_here`) decides whether to actually OFFER it on this box.
    """
    desc = _KNOWN_SERVICES.get(service_name)
    if not desc:
        return []
    if running:
        return [restart_action(service_name, f"Restart {service_name}",
                               f"restart {desc}")]
    return [
        _start_action(service_name, f"Start {service_name}", f"start {desc}"),
        restart_action(service_name, f"Restart {service_name}", f"restart {desc}"),
    ]


def service_enabled_here(service_name: str) -> bool:
    """True iff this box is CONFIGURED to run the service.

    The gate for offering a fix: the systemd unit must exist AND be enabled-at-
    boot. A disabled or absent unit ⇒ the service is intentionally off on this
    box ⇒ we never nag to "fix" it. This is the accurate per-box signal — more
    accurate than the deployment-profile feature flag, which a gateway-only box
    on the 'full' profile reports as maps=True even though its map unit is
    disabled (the moc3 case).
    """
    from utils.service_check import is_service_unit_installed, check_systemd_service
    if not is_service_unit_installed(service_name):
        return False
    try:
        _running, enabled = check_systemd_service(service_name)
    except Exception:  # systemctl missing / odd environment — don't offer
        return False
    return bool(enabled)


def collect_degraded_services(service_names) -> List[Tuple[str, bool]]:
    """Check each service; return (name, running=False) for installed-but-down ones.

    Pure detection — the profile gate (`service_enabled_here`) is applied later by
    the chooser, so callers can pass a broad candidate list. Skips NOT_INSTALLED
    services (nothing to fix) and anything that errors.
    """
    from utils.service_check import check_service, ServiceState
    out: List[Tuple[str, bool]] = []
    for svc in service_names:
        try:
            st = check_service(svc)
        except Exception:
            continue
        if not st.available and st.state != ServiceState.NOT_INSTALLED:
            out.append((svc, False))
    return out


def _rns_repair_action(ctx):
    """Guided RNS repair wizard as a remediation action.

    rnsd's common failures — a wedged @rns shared instance, stale auth tokens,
    config drift (Issues #30/#37/#68/#69) — often need MORE than a restart. This
    routes to the RNS diagnostics handler's repair wizard (validate config, check
    deps, clear stale auth, restart, verify port). Returns None if the handler
    isn't available (so rnsd still gets start/restart).
    """
    from remediation import RemediationAction

    def _apply():
        registry = getattr(ctx, "registry", None)
        diag = registry.get_handler("rns_diagnostics") if registry else None
        if diag is None:
            return (False, "RNS diagnostics handler not available")
        # The wizard is interactive (own confirm + steps + wait); we just route
        # to it and report that it finished — its own dialogs carry the detail.
        diag._rns_repair_menu()
        return (True, "RNS repair wizard finished")

    return RemediationAction(
        label="Repair rnsd (guided wizard)",
        description="validate config, check deps, clear stale auth, restart, verify port",
        apply=_apply,
        requires_admin=True,
    )


def _actions_for(ctx, service_name: str, running: bool) -> List:
    """Full action list for a service: the pure start/restart map, plus the
    guided RNS repair wizard for rnsd (which needs ctx to reach the handler)."""
    actions = list(service_fix_actions(service_name, running))
    if service_name == "rnsd" and actions:
        repair = _rns_repair_action(ctx)
        if repair is not None:
            actions.append(repair)  # escalation: quick restart -> thorough repair
    return actions


def offer_service_fix(ctx, service_name: str, running: bool):
    """If a known + enabled-here service is degraded, offer its fix in-app.

    Returns the (ok, message) of the applied action, or None if no action exists,
    the service is intentionally off here, or the operator declined. Never raises.
    """
    from remediation import propose_remediation
    if not service_enabled_here(service_name):
        return None
    actions = _actions_for(ctx, service_name, running)
    if not actions:
        return None
    state_word = "is not healthy" if running else "is not running"
    return propose_remediation(
        ctx,
        f"Fix {service_name}",
        f"{service_name} {state_word}. You can fix it here — no need to hunt "
        f"for the right menu.",
        actions,
    )


def offer_service_fix_chooser(ctx, degraded) -> bool:
    """Offer an in-app fix chooser for degraded services this box is configured to run.

    Args:
        degraded: list of (service_name, running_bool).

    Gated by `service_enabled_here`, so intentionally-off services (disabled or
    absent units) are never offered — gateway-only boxes aren't nagged. Routes
    each pick to the remediation surface (the fix comes to the operator).

    Returns True if a chooser was shown (caller can skip its own wait-for-enter),
    False if nothing was fixable (caller should wait/return as usual).
    """
    fixable = [(n, r) for (n, r) in degraded
               if service_fix_actions(n, r) and service_enabled_here(n)]
    if not fixable:
        return False
    running_by_name = dict(fixable)
    while True:
        choices = [(n, f"Fix {n}") for (n, _) in fixable]
        choices.append(("__done__", "Done (leave as-is)"))
        sel = ctx.dialog.menu(
            "Fix a Degraded Service",
            "These services aren't healthy. Fix one now — no need to find the "
            "right menu:",
            choices,
        )
        if not sel or sel == "__done__":
            return True
        offer_service_fix(ctx, sel, running_by_name.get(sel, False))
