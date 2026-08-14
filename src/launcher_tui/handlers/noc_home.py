"""NOC Home — the operator's landing: transports + health + one-touch fixes.

The monitor→diagnose→fix loop in ONE screen (TUI workflow arc, 2026-05-29).
Top of the view is the TRANSPORTS row — Meshtastic / MeshCore / RNS — with RNS
on its OWN line and a one-touch repair, because the 2026-05-29 dependency
research found RNS upstream support-risk HIGH (the maintainer withdrew public
support); see the register's "RNS Dependency Risk" note. When only the RNS leg
is down, the view says so explicitly ("still routing on the other transport(s)")
so an RNS wedge reads as DEGRADED, not DOWN — surfacing the transport-agnostic
hedge the gateway already has.

Presentation-layer only: every action routes to an EXISTING handler/view via the
registry, or to the shared `service_remediation` surface. No behavior is
reimplemented here. Probes respect the enabled-at-boot gate, so a box that
intentionally doesn't run a transport (e.g. rnsd disabled) shows it as "off",
not a red alarm.
"""
import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


def _dot(state: str) -> str:
    """Plain-text status marker for a transport/service state.

    Plain text on purpose: this panel renders INSIDE the whiptail menu
    body, and whiptail never interprets ANSI color escapes. The previous
    ANSI-colored version was print()ed to the terminal and then erased by
    the backend's clear_screen before the menu appeared — the operator
    never saw the panel at all (2026-08-14 audit W12).
    """
    return {
        "up": "[ UP ]",
        "degraded": "[DEGR]",
        "down": "[DOWN]",
        "off": "[ -- ]",
    }.get(state, "[ ?? ]")


class NocHomeHandler(BaseHandler):
    """The NOC operator's landing screen."""

    handler_id = "noc_home"
    menu_section = "main"

    def menu_items(self):
        return [
            ("n", "NOC Home            Transports, health, one-touch fixes", None),
        ]

    def execute(self, action):
        if action == "n":
            self.ctx.safe_call("NOC Home", self._noc_home)

    # ------------------------------------------------------------ probes
    def _svc_state(self, svc: str):
        """(state, label) for a core service, gated by enabled-at-boot so an
        intentionally-disabled service reads 'off (disabled)', not red."""
        from service_remediation import service_enabled_here
        from utils.service_check import check_service
        try:
            if not service_enabled_here(svc):
                return ("off", "off (disabled)")
            return ("up", "running") if check_service(svc).available \
                else ("down", "stopped")
        except Exception:  # a probe must never crash the landing
            return ("off", "unknown")

    def _probe_rns(self):
        """('up'|'degraded'|'down'|'off', running_bool).

        degraded = rnsd is running but the shared instance isn't answering (the
        wedge class — Issues #68/#69). off = rnsd not enabled on this box.
        """
        from service_remediation import service_enabled_here
        from utils.service_check import check_service, check_rns_shared_instance
        from utils.paths import ReticulumPaths
        try:
            if not service_enabled_here("rnsd"):
                return ("off", False)
            if not check_service("rnsd").available:
                return ("down", False)
            try:
                ok = check_rns_shared_instance(
                    ReticulumPaths.get_configured_instance_name()
                )
            except Exception:
                ok = True  # can't determine the shared instance; trust the daemon
            return ("up", True) if ok else ("degraded", True)
        except Exception:
            return ("off", False)

    def _probe_meshcore(self) -> str:
        """'on'|'off' from the gateway config. MeshCore is a companion radio, not
        a systemd unit, so we report configured-enablement, not link health."""
        try:
            from gateway.config import GatewayConfig
            mc = getattr(GatewayConfig.load(), "meshcore", None)
            return "on" if (mc and getattr(mc, "enabled", False)) else "off"
        except Exception:
            return "off"

    # ------------------------------------------------------------ view
    def _noc_home(self):
        from service_remediation import (
            collect_degraded_services, offer_service_fix_chooser, offer_service_fix,
        )
        while True:
            mesh_state, _mesh_lbl = self._svc_state("meshtasticd")
            mc_state = self._probe_meshcore()
            rns_state, rns_running = self._probe_rns()

            # The panel goes INTO the menu body (whiptail clears the screen
            # before drawing, so anything print()ed here is never seen).
            panel = ["TRANSPORTS"]
            panel.append(f"  {_dot(mesh_state)} Meshtastic   {mesh_state.upper()}")
            mc_label = "ON (config)" if mc_state == "on" else "off / not configured"
            panel.append(f"  {_dot('up' if mc_state == 'on' else 'off')} MeshCore     {mc_label}")
            rns_tail = "   -> Repair below" if rns_state in ("degraded", "down") else ""
            panel.append(f"  {_dot(rns_state)} RNS / rnsd   {rns_state.upper()}{rns_tail}")
            # Degrade-not-down: RNS leg down but another transport still carrying.
            if rns_state in ("degraded", "down") and (mesh_state == "up" or mc_state == "on"):
                panel.append("       bridge still routing on the other "
                             "transport(s) — RNS leg only")
            panel.append("")

            panel.append("HEALTH")
            for svc in ("meshtasticd", "rnsd", "meshforge-gateway"):
                state, label = self._svc_state(svc)
                panel.append(f"  {_dot(state)} {svc:<18} {label}")

            degraded = collect_degraded_services(
                ["meshtasticd", "rnsd", "meshforge-gateway", "meshforge-map"]
            )
            fixable_non_rns = [(n, r) for (n, r) in degraded if n != "rnsd"]

            choices = []
            if rns_state in ("degraded", "down"):
                choices.append(("rns_repair",
                                "-> Repair RNS       start / restart / guided wizard"))
            if fixable_non_rns:
                choices.append(("fix", "Fix a degraded service"))
            choices += [
                ("status", "Service Status      detail view"),
                ("stack", "Stack Health        full local probes"),
                ("diag", "Diagnostics         system health check"),
                ("msg", "Messages            send / receive"),
                ("more", "All Sections        full menu"),
                ("back", "Back"),
            ]

            sel = self.ctx.dialog.menu(
                "NOC Home",
                "\n".join(panel),
                choices,
            )
            if not sel or sel in ("back", "more"):
                return
            if sel == "rns_repair":
                self.ctx.safe_call(
                    "Repair RNS",
                    lambda: offer_service_fix(self.ctx, "rnsd", rns_running),
                )
            elif sel == "fix":
                self.ctx.safe_call(
                    "Fix services",
                    lambda: offer_service_fix_chooser(self.ctx, fixable_non_rns),
                )
            elif sel == "status":
                self._route("dashboard", "status")
            elif sel == "stack":
                self._route("dashboard", "stack_health")
            elif sel == "diag":
                self._route("system", "diagnose")
            elif sel == "msg":
                self._route("mesh_networks", "messaging")

    def _route(self, section: str, tag: str):
        """Route to an existing handler view via the registry (no reimplement)."""
        reg = getattr(self.ctx, "registry", None)
        if reg is None:
            self.ctx.dialog.msgbox("Unavailable", "Handler registry not available.")
            return
        self.ctx.safe_call(f"{section}/{tag}", lambda: reg.dispatch(section, tag))
