"""Fleet Architecture — reproduce this box to a lab-hardened preset (DRY-RUN).

Wraps the EXISTING convergence engine (provision_role.py + docs/fleet_presets.yaml
+ docs/fleet_roles.yaml) with a TUI: browse the lab-hardened catalog, see the
current box's role/overrides/drift, and PREVIEW what reproducing a preset would
do (the unit diff from provision_role.plan() + the gateway.json leg overlay).

DRY-RUN ONLY in this release — nothing is applied, written, or restarted. The
apply path is a deliberate follow-on. Pure logic lives in
``_fleet_provision_core.py`` so it is unit-testable without instantiating the TUI.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from handler_protocol import BaseHandler

from . import _fleet_provision_core as core

logger = logging.getLogger(__name__)


class FleetProvisionHandler(BaseHandler):
    """Dry-run TUI over the role/preset convergence engine."""

    handler_id = "fleet_provision"
    menu_section = "system"

    def menu_items(self) -> List[Tuple[str, str, Optional[str]]]:
        return [
            ("fleet_provision",
             "Fleet Architecture   Reproduce a box to a preset (dry-run)", None),
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
        lines += ["",
                  "DRY-RUN ONLY — nothing applied. Apply lands in a later release."]
        self.ctx.dialog.textbox(f"Preview: {preset_name}", "\n".join(lines))
