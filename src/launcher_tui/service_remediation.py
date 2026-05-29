"""NOC fix-routing: a detected service state → the in-app action that fixes it.

This generalizes the mini-dudeai rule→action map (`mini_dudeai._fixes_for`) to the
service-status domain, so ANY monitoring view (Dashboard service status, Stack
Health, future widgets) can turn a DEGRADED service into a one-keystroke fix via
the shared remediation surface (`remediation.propose_remediation`).

The point — the operator never navigates to the fix. The fix comes to them where
they saw the problem. That is the In-Domain Principle extended one step: not just
"never quit the app to fix it," but "never even leave the status view to fix it."
See `.claude/foundations/in_domain_principle.md` and `remediation.py`.

Conservative by design: only services this box OWNS, where a start/restart is a
genuine, safe local recovery. An unknown service yields no actions — the caller
then shows it as informational rather than pretending to fix it.
"""
from __future__ import annotations

from typing import List

# Services this box owns where start/restart is a real, safe local fix.
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


def _restart_action(service: str, label: str, description: str):
    """A remediation action that restarts a LOCAL service via the SSOT."""
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
    """
    desc = _KNOWN_SERVICES.get(service_name)
    if not desc:
        return []
    if running:
        return [_restart_action(service_name, f"Restart {service_name}",
                                f"restart {desc}")]
    return [
        _start_action(service_name, f"Start {service_name}", f"start {desc}"),
        _restart_action(service_name, f"Restart {service_name}", f"restart {desc}"),
    ]


def offer_service_fix(ctx, service_name: str, running: bool):
    """If a known service is degraded, offer its fix via the shared surface.

    Returns the (ok, message) of the applied action, or None if no action exists
    or the operator declined. Never raises (the surface guards apply()).
    """
    from remediation import propose_remediation
    actions = service_fix_actions(service_name, running)
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
