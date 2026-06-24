"""Fleet Architecture — reproduce this box to a lab-hardened preset.

Wraps the EXISTING convergence engine (provision_role.py + docs/fleet_presets.yaml
+ docs/fleet_roles.yaml) with a TUI: browse the lab-hardened catalog, see the
current box's role/overrides/drift, PREVIEW what reproducing a preset would do
(the unit diff from provision_role.plan() + the gateway.json leg overlay), and
— in admin mode, after an explicit confirm — APPLY the preset's ROLE to this box.

Apply scope (v1): the ROLE axis only (which systemd units run), via the existing
``provision_role`` apply surface — write_role (merge) + apply_action. The
BRIDGE-LEG axis (gateway.json) is NOT auto-applied: a generic preset omits
box-specific values (LXMF hash, channel index — MF014); legs are surfaced as a
guided ``configure_gateway.sh`` next step. Local box only (no SSH/remote apply).

Pure logic lives in ``_fleet_provision_core.py`` so it is unit-testable without
instantiating the TUI.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

from handler_protocol import BaseHandler

from . import _fleet_provision_core as core

logger = logging.getLogger(__name__)


class FleetProvisionHandler(BaseHandler):
    """Preview + guarded-apply TUI over the role/preset convergence engine."""

    handler_id = "fleet_provision"
    menu_section = "system"

    def menu_items(self) -> List[Tuple[str, str, Optional[str]]]:
        return [
            ("fleet_provision",
             "Fleet Architecture   Reproduce a box to a preset (preview + apply)",
             None),
        ]

    def execute(self, action: str) -> None:
        if action == "fleet_provision":
            self.ctx.safe_call("Fleet Architecture", self._main_menu)

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    def _load(self):
        """Return (mod, presets_doc) or (None, None) with an in-pane note."""
        mod = core.load_provision_role()
        if mod is None:
            self.ctx.dialog.msgbox(
                "Fleet Architecture",
                "Could not load scripts/provision_role.py — the convergence "
                "engine is unavailable. Is this a full MeshForge checkout?")
            return None, None
        try:
            doc = core.load_presets(core.presets_path())
        except (OSError, ValueError) as e:
            self.ctx.dialog.msgbox(
                "Fleet Architecture",
                f"Could not load docs/fleet_presets.yaml: {e}")
            return None, None
        return mod, doc

    # ------------------------------------------------------------------
    # menus
    # ------------------------------------------------------------------
    def _main_menu(self) -> None:
        mod, doc = self._load()
        if mod is None:
            return
        while True:
            choice = self.ctx.dialog.menu(
                "Fleet Architecture",
                "Reproduce this box to a lab-hardened configuration "
                "(DRY-RUN — nothing is applied).",
                [
                    ("current", "Current box     role, overrides, live drift"),
                    ("catalog", "Browse presets  the lab-hardened catalog"),
                    ("back", "Back"),
                ],
            )
            if choice in (None, "back"):
                return
            if choice == "current":
                self._show_current(mod)
            elif choice == "catalog":
                self._catalog_menu(mod, doc)

    def _show_current(self, mod) -> None:
        info = core.current_box(mod)
        lines = [f"Declared role : {info['role'] or '(none set)'}"]
        ov = info["overrides"] or {}
        if ov:
            lines += ["", "service_overrides:"]
            for unit, spec in ov.items():
                lines.append(
                    f"  {unit}: {spec.get('state', '?')}  "
                    f"({spec.get('reason', 'no reason')})")
        lines.append("")
        drift = info["drift"]
        if drift is None:
            lines.append("Drift: UNKNOWN (role unset or catalog unavailable).")
        elif not drift:
            lines.append("Drift: none — box matches its declared role.")
        else:
            lines.append(f"Drift: {len(drift)} unit(s) would change under converge:")
            for a in drift:
                lines.append(f"  {a.verb:7} {a.item}   ({a.current} -> {a.desired})")
        self.ctx.dialog.textbox("Current box", "\n".join(lines))

    def _catalog_menu(self, mod, doc) -> None:
        presets = doc["presets"]
        while True:
            choices = [
                (name, f"{p.get('shorthand', '-'):22} {p.get('title', name)}")
                for name, p in presets.items()
            ]
            choices.append(("back", "Back"))
            choice = self.ctx.dialog.menu(
                "Lab-hardened presets",
                "Pick a preset to preview what reproducing it would do.",
                choices,
            )
            if choice in (None, "back"):
                return
            self._preview(mod, choice, doc)

    def _preview(self, mod, preset_name: str, doc) -> None:
        p = doc["presets"][preset_name]
        lines = [
            p.get("title", preset_name),
            f"shorthand : {p.get('shorthand', '-')}",
            f"role      : {p['role']}",
            f"board     : {p.get('board_tier', '?')}",
            f"maturity  : {p.get('maturity', '?')}",
            "",
            (p.get("use_when", "") or "").strip(),
            "",
        ]
        try:
            overrides = core.current_box(mod)["overrides"]
            prev = core.preview_preset(mod, preset_name, doc, overrides)
        except Exception as e:
            lines.append(f"[could not compute dry-run: {e}]")
            self.ctx.dialog.textbox(f"Preview: {preset_name}", "\n".join(lines))
            return
        lines.append("Role converge (DRY-RUN) — units that would change:")
        if not prev["actions"]:
            lines.append("  (none — this box already matches the preset's role)")
        else:
            for a in prev["actions"]:
                lines.append(f"  {a.verb:7} {a.item}   ({a.current} -> {a.desired})")
        lines.append("")
        overlay = prev["gateway_overlay"]
        if overlay:
            lines.append("gateway.json overlay (bridge legs):")
            for k, v in overlay.items():
                lines.append(f"  {k} = {v}")
        else:
            lines.append("gateway.json overlay: (none — non-bridge preset)")
        if os.geteuid() == 0:
            lines += ["",
                      "This is a PREVIEW. Choose 'Apply' to converge this box's "
                      "ROLE (you confirm the exact changes first). Bridge legs "
                      "are applied separately via configure_gateway.sh."]
        else:
            lines += ["",
                      "PREVIEW only — applying changes systemd units + the box "
                      "role, which needs admin mode (relaunch with sudo)."]
        self.ctx.dialog.textbox(f"Preview: {preset_name}", "\n".join(lines))

        # Apply is offered only in admin mode; viewer mode just returns to the
        # catalog (the preview text above already explains why).
        if os.geteuid() == 0:
            choice = self.ctx.dialog.menu(
                f"Preview: {preset_name}",
                "Preview shown above. Apply converges this box's ROLE only.",
                [("apply", "Apply this preset to THIS box (converge role)"),
                 ("back", "Back to catalog")],
            )
            if choice == "apply":
                self._apply_flow(mod, preset_name, doc)

    # ------------------------------------------------------------------
    # apply (guarded: admin-gated, confirm-after-dry-run, honest report)
    # ------------------------------------------------------------------
    def _apply_flow(self, mod, preset_name: str, doc) -> None:
        """Converge THIS box's ROLE to a preset, after an explicit confirm.

        Re-derives the exact change set, shows it, requires a yes (default no),
        applies via the core, then reports the REAL per-action result — a failed
        apply never shows a success title (honest-signal contract, #74-#77).
        """
        if os.geteuid() != 0:
            self.ctx.dialog.msgbox(
                "Apply Preset",
                "Applying a preset changes systemd units and the box role — it "
                "requires admin mode.\n\nRelaunch with:\n"
                "  sudo python3 src/launcher_tui/main.py")
            return

        overrides = core.current_box(mod)["overrides"]
        try:
            prev = core.preview_preset(mod, preset_name, doc, overrides)
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Apply Preset", f"Could not compute the change set: {e}")
            return

        role = prev["role"]
        actions = prev["actions"]
        overlay = prev["gateway_overlay"]

        lines = [f"Apply preset '{preset_name}' to THIS box?", "",
                 f"  role -> {role}"]
        if actions:
            lines.append(f"  converge {len(actions)} unit(s) "
                         "(enable=start, disable=stop, mask):")
            for a in actions:
                lines.append(f"    {a.verb:7} {a.item}  "
                             f"({a.current} -> {a.desired})")
        else:
            lines.append("  units already match this role — no unit changes")
        if overlay:
            lines += ["",
                      "Bridge legs (gateway.json) are NOT auto-applied (they need",
                      "box-specific values). After this, wire them with:",
                      f"  {core.CONFIGURE_GATEWAY_CMD}",
                      "to set: " + ", ".join(f"{k}={v}" for k, v in overlay.items())]
        lines += ["",
                  "Reverting = re-apply the box's prior preset. Continue?"]

        if not self.ctx.dialog.yesno("Apply Preset — confirm", "\n".join(lines)):
            self.ctx.dialog.msgbox("Apply Preset", "Cancelled — nothing changed.")
            return

        res = core.apply_preset(mod, preset_name, doc, overrides)
        out = [
            "role set: {0}  ({1})".format(
                res["role"],
                "ok" if res["role_written"]
                else "FAILED: " + str(res["role_err"])),
            "",
        ]
        if not res["results"]:
            out.append("units: no changes (already converged)")
        else:
            for r in res["results"]:
                out.append(
                    f"  {r['verb']:7} {r['item']}: "
                    f"{'OK' if r['ok'] else 'FAILED'}"
                    f"{('  ' + r['result']) if r['result'] else ''}")
        if overlay and res["ok"]:
            out += ["", f"Next: {core.CONFIGURE_GATEWAY_CMD}  (wire bridge legs)"]
        body = "\n".join(out)

        if res["ok"]:
            self.ctx.report_action(True, f"Preset Applied: {preset_name}", body)
        else:
            nfail = len(res["failures"]) + (0 if res["role_written"] else 1)
            self.ctx.report_action(
                False, "", "", "Preset Apply — incomplete",
                body + f"\n\n{nfail} step(s) failed — the box may be in a "
                "partial state. Fix the cause and re-apply, or converge via "
                "'sudo python3 scripts/provision_role.py --apply'.")
