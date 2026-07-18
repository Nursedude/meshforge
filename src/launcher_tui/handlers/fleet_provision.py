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

import yaml

from handler_protocol import BaseHandler

from . import _fleet_provision_core as core

logger = logging.getLogger(__name__)

# Units whose stop/disable deserves an explicit red-flag line in the
# confirm dialog — the box's bridge, its recovery backstop, and the RNS
# substrate. Not a denylist (the catalog stays authoritative); a warning
# the operator cannot miss.
HIGH_CONSEQUENCE_UNITS = ("meshforge-gateway", "meshforge-watchdog", "rnsd")


def _fmt_action(a, indent: str = "  ") -> str:
    """ONE renderer for plan actions — preview, confirm and report must
    show the same facts (detail carries the waiver reason / #69 note)."""
    line = f"{indent}{a.verb:7} {a.item}   ({a.current} -> {a.desired})"
    detail = getattr(a, "detail", "")
    return f"{line}  [{detail}]" if detail else line


def _warning_lines(warnings, indent: str = "  ") -> List[str]:
    """Render plan warnings; required ones are BLOCKING (CLI exit-1 class)."""
    out = []
    for w in warnings or []:
        tag = "BLOCKING" if getattr(w, "required", False) else "advisory"
        detail = getattr(w, "detail", "") or w.desired
        out.append(f"{indent}⚠ {tag}: {w.item} — {detail}")
    return out


class FleetProvisionHandler(BaseHandler):
    """Preview + guarded-apply TUI over the role/preset convergence engine."""

    handler_id = "fleet_provision"
    menu_section = "system"

    def menu_items(self) -> List[Tuple[str, str, Optional[str]]]:
        return [
            ("fleet_provision",
             "Fleet Architecture   Reproduce a box to a preset (preview + apply)",
             None),
            ("fleet_membership",
             "Fleet Membership     Declare standalone, or fleet + host list",
             None),
        ]

    def execute(self, action: str) -> None:
        if action == "fleet_provision":
            self.ctx.safe_call("Fleet Architecture", self._main_menu)
        elif action == "fleet_membership":
            self.ctx.safe_call("Fleet Membership", self._membership_menu)

    # ------------------------------------------------------------------
    # fleet membership (first-run declaration)
    # ------------------------------------------------------------------
    def _membership_menu(self) -> None:
        from . import _fleet_membership_core as fm

        while True:
            st = fm.membership_state()
            if st["mode"] == "fleet":
                head = (f"Mode: FLEET — {len(st['hosts'])} box(es): "
                        f"{', '.join(st['hosts'][:6])}"
                        + (" …" if len(st["hosts"]) > 6 else ""))
            else:
                head = ("Mode: STANDALONE — no fleet host list. Rollup and "
                        "fleet tools cover this box only.")
                if st["disabled_exists"]:
                    head += "\n(a disabled host list exists — Edit restores it)"
            choices = [("edit", "Edit host list   ssh aliases, comma/space separated"),
                       ("probe", "Probe hosts      ssh-check each declared box")]
            if st["mode"] == "fleet":
                choices.append(("standalone",
                                "Go standalone    set host list aside (reversible)"))
            choices.append(("back", "Back"))
            sel = self.ctx.dialog.menu(
                "Fleet Membership", head + f"\nFile: {st['path']}", choices)
            if sel in (None, "back"):
                return
            if sel == "edit":
                self._membership_edit(fm, st)
            elif sel == "probe":
                self._membership_probe(fm, st)
            elif sel == "standalone":
                self._membership_standalone(fm, st)

    def _membership_edit(self, fm, st) -> None:
        init = ", ".join(st["hosts"])
        if not st["hosts"] and st["disabled_exists"]:
            init = ", ".join(
                fm.membership_state(st["path"] + fm.DISABLED_SUFFIX)["hosts"])
        text = self.ctx.dialog.inputbox(
            "Fleet Membership",
            "Boxes this one should see (ssh aliases/hostnames). Empty = "
            "standalone.", init=init)
        if text is None:
            return
        try:
            hosts = fm.parse_host_input(text)
        except ValueError as e:
            self.ctx.dialog.msgbox("Fleet Membership", f"Rejected: {e}")
            return
        if not hosts:
            self.ctx.dialog.msgbox(
                "Fleet Membership",
                "Empty list — use 'Go standalone' to declare standalone "
                "explicitly; nothing was changed.")
            return
        results = fm.probe_hosts(hosts)
        down = [h for h, s in results if s != "ok"]
        summary = "\n".join(f"  {'[ OK ]' if s == 'ok' else '[DOWN]'} {h}"
                            for h, s in results)
        warn = ("\n\nUnreachable boxes stay in the list (maybe not racked "
                "yet) — fleet panes will honestly show them unreachable."
                if down else "")
        if not self.ctx.dialog.yesno(
                "Fleet Membership",
                f"Write {len(hosts)} host(s)?\n\n{summary}{warn}"):
            return
        fm.write_fleet_hosts(st["path"], hosts)
        self.ctx.dialog.msgbox(
            "Fleet Membership",
            f"Wrote {st['path']} ({len(hosts)} hosts). Fleet panes pick "
            "this up on next run — verify via Dashboard → Fleet Posture.")

    def _membership_probe(self, fm, st) -> None:
        if not st["hosts"]:
            self.ctx.dialog.msgbox(
                "Fleet Membership", "No hosts declared — nothing to probe.")
            return
        results = fm.probe_hosts(st["hosts"])
        lines = [f"  {'[ OK ]' if s == 'ok' else '[DOWN]'} {h}"
                 for h, s in results]
        ok = sum(1 for _, s in results if s == "ok")
        self.ctx.dialog.msgbox(
            "Fleet Membership",
            f"{ok}/{len(results)} reachable over ssh:\n\n" + "\n".join(lines)
            + "\n\n[DOWN] = ssh BatchMode failed: box down, no key, or "
              "wrong alias. Keys/aliases live in ~/.ssh/config.")

    def _membership_standalone(self, fm, st) -> None:
        if not self.ctx.dialog.yesno(
                "Fleet Membership",
                f"Set the host list aside?\n\n{st['path']}\n→ "
                f"{st['path']}{fm.DISABLED_SUFFIX}\n\nReversible: 'Edit host "
                "list' offers the saved list back.",
                default_no=True):
            return
        moved = fm.declare_standalone(st["path"])
        self.ctx.dialog.msgbox(
            "Fleet Membership",
            "Already standalone — no host list present." if moved is None
            else f"Standalone declared. List preserved at:\n{moved}")

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
        except (OSError, ValueError, yaml.YAMLError) as e:
            # yaml.YAMLError is NOT a ValueError — without it here, a
            # malformed catalog edit skipped this actionable message and
            # fell to the generic error pane.
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
                if isinstance(spec, dict):
                    lines.append(
                        f"  {unit}: {spec.get('state', '?')}  "
                        f"({spec.get('reason', 'no reason')})")
                else:
                    # the engine tolerates this shape (renders it as a
                    # hidden-drift warn); the pane must not crash on it
                    lines.append(f"  {unit}: {spec!r}  "
                                 f"(MALFORMED — expected {{state, reason}})")
        lines.append("")
        drift = info["drift"]
        blocking = core.required_warnings(info.get("warnings"))
        if drift is None:
            lines.append("Drift: UNKNOWN (role unset or catalog unavailable).")
        elif not drift and not blocking:
            # same predicate probe_role_drift pages on — the TUI and the
            # watchdog must never disagree about the same plan
            lines.append("Drift: none — box matches its declared role.")
        else:
            if drift:
                lines.append(f"Drift: {len(drift)} unit(s) would change "
                             f"under converge:")
                lines += [_fmt_action(a) for a in drift]
            if blocking:
                lines.append(f"Blocking: {len(blocking)} required "
                             f"warning(s) — converge cannot fully succeed:")
                lines += _warning_lines(blocking)
        advisories = [w for w in (info.get("warnings") or [])
                      if not getattr(w, "required", False)]
        if advisories:
            lines.append("")
            lines += _warning_lines(advisories)
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
            overrides = mod.read_overrides()
            prev = core.preview_preset(mod, preset_name, doc, overrides)
        except Exception as e:
            lines.append(f"[could not compute dry-run: {e}]")
            self.ctx.dialog.textbox(f"Preview: {preset_name}", "\n".join(lines))
            return
        blocking = core.required_warnings(prev["warnings"])
        lines.append("Role converge (DRY-RUN) — units that would change:")
        if not prev["actions"] and not blocking:
            lines.append("  (none — this box already matches the preset's role)")
        else:
            lines += [_fmt_action(a) for a in prev["actions"]]
        if prev["warnings"]:
            lines += _warning_lines(prev["warnings"])
        if prev.get("foundation"):
            lines.append("  foundation converge (perms, mf.4 class):")
            lines += [_fmt_action(a, indent="    ")
                      for a in prev["foundation"]]
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

        overrides = mod.read_overrides()
        prior_role = mod.read_role()
        try:
            prev = core.preview_preset(mod, preset_name, doc, overrides)
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Apply Preset", f"Could not compute the change set: {e}")
            return

        role = prev["role"]
        actions = prev["actions"]
        blocking = core.required_warnings(prev["warnings"])
        overlay = prev["gateway_overlay"]

        lines = [f"Apply preset '{preset_name}' to THIS box?", "",
                 f"  role: {prior_role or '(none set)'} -> {role}"]
        if actions:
            lines.append(f"  converge {len(actions)} unit(s) "
                         "(enable=start, disable=stop, mask):")
            lines += [_fmt_action(a, indent="    ") for a in actions]
        else:
            lines.append("  units already match this role — no unit changes")
        risky = [a.item for a in actions
                 if a.verb in ("disable", "mask")
                 and any(a.item.startswith(u) for u in
                         HIGH_CONSEQUENCE_UNITS)]
        if risky:
            lines += ["",
                      f"  ⚠⚠ THIS STOPS {', '.join(risky)} — the box's "
                      f"bridge/backstop. If a soak or drill is running, "
                      f"this resets it. Be sure."]
        if prev["warnings"]:
            lines += [""] + _warning_lines(prev["warnings"])
            if blocking:
                lines.append("  (BLOCKING warnings mean the converge CANNOT "
                             "fully succeed — the CLI would exit 1)")
        if any(a.verb == "mask" for a in actions):
            lines += ["",
                      "  NOTE: mask is NOT undone by re-applying a preset —",
                      "  reverting a mask needs 'systemctl unmask <unit>'."]
        if overlay:
            lines += ["",
                      "Bridge legs (gateway.json) are NOT auto-applied (they need",
                      "box-specific values). After this, wire them with:",
                      f"  {core.CONFIGURE_GATEWAY_CMD}",
                      "to set: " + ", ".join(f"{k}={v}" for k, v in overlay.items())]
        lines += ["",
                  f"Reverting the role = re-apply the prior preset "
                  f"(current role: {prior_role or 'none'}). Continue?"]

        if not self.ctx.dialog.yesno("Apply Preset — confirm",
                                     "\n".join(lines), default_no=True):
            self.ctx.dialog.msgbox("Apply Preset", "Cancelled — nothing changed.")
            return

        logger.info("fleet_provision apply: preset=%s role=%s->%s "
                    "actions=%d blocking_warnings=%d",
                    preset_name, prior_role, role, len(actions),
                    len(blocking))
        res = core.apply_preset(mod, preset_name, doc, overrides,
                                expected_actions=actions)

        if res.get("aborted"):
            logger.warning("fleet_provision apply ABORTED: %s",
                           res["aborted"])
            self.ctx.report_action(
                False, "", "", "Preset Apply — aborted (nothing changed)",
                f"{res['aborted']}\n\nThe box was NOT modified. Re-open the "
                f"preview to see the current plan.")
            return

        out = [
            "role set: {0}  ({1}; was: {2})".format(
                res["role"],
                "ok" if res["role_written"]
                else "FAILED: " + str(res["role_err"]),
                res.get("prior_role") or "none"),
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
        if res.get("blocking_warnings"):
            out.append("")
            out.append("BLOCKING warnings (converge incomplete — the CLI "
                       "would exit 1):")
            for w in res["blocking_warnings"]:
                out.append(f"  ⚠ {w['item']}: {w['detail']}")
        if overlay and res["ok"]:
            out += ["", f"Next: {core.CONFIGURE_GATEWAY_CMD}  (wire bridge legs)"]
        body = "\n".join(out)

        logger.info("fleet_provision apply result: preset=%s ok=%s "
                    "failed=%d blocking=%d role_written=%s",
                    preset_name, res["ok"], len(res["failures"]),
                    len(res.get("blocking_warnings") or []),
                    res["role_written"])
        if res["ok"]:
            self.ctx.report_action(True, f"Preset Applied: {preset_name}", body)
        else:
            nfail = (len(res["failures"])
                     + len(res.get("blocking_warnings") or [])
                     + (0 if res["role_written"] else 1))
            self.ctx.report_action(
                False, "", "", "Preset Apply — incomplete",
                body + f"\n\n{nfail} step(s) failed/blocking — the box may "
                "be in a partial state. Fix the cause and re-apply, or "
                "converge via 'sudo python3 scripts/provision_role.py "
                "--apply'.")
