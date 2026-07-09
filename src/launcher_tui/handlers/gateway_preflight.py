"""Gateway Pre-Flight handler — validate bridge readiness before launch.

Checks the cross-protocol bridge setup end-to-end:
  1. LXMF Python package importable (not just RNS)
  2. meshtasticd reachable on configured host/port
  3. rnsd running with shared instance reachable
  4. At least one Meshtastic channel has uplinkEnabled + downlinkEnabled
  5. gateway.json mqtt_channel matches an uplinked HAT channel name
  6. gateway_identity file exists, derive LXMF source hash
  7. NomadNet identity matches gateway's default_lxmf_destination (if logfile present)

Shows colored PASS/FAIL per check with copy-paste fix commands.
Surfaces the gateway's own LXMF hash so users know where to send from NomadNet.
"""

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from handler_protocol import BaseHandler
from utils.paths import get_real_user_home
from utils.safe_import import safe_import
from utils.service_check import check_service, check_port, get_rns_shared_instance_info

logger = logging.getLogger(__name__)

# ANSI colors — match styling used elsewhere in the TUI
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_OK = f"{_GREEN}✓{_RESET}"
_FAIL = f"{_RED}✗{_RESET}"
_WARN = f"{_YELLOW}⚠{_RESET}"


class GatewayPreflightHandler(BaseHandler):
    """Validate cross-protocol bridge readiness (Meshtastic ↔ RNS/NomadNet)."""

    handler_id = "gateway_preflight"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("check", "Gateway Pre-Flight  Validate bridge readiness", None),
            ("export", "Export Config       Snapshot current state as template", None),
        ]

    def execute(self, action):
        dispatch = {
            "check": ("Gateway Pre-Flight", self._run_preflight),
            "export": ("Export Config as Template", self._run_export),
        }
        entry = dispatch.get(action)
        if entry:
            self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # Main flow
    # ------------------------------------------------------------------

    def collect_results(self) -> Tuple[List[Tuple[str, str, Optional[str]]],
                                       List[Tuple[str, str, Optional[str]]]]:
        """Run every pre-flight check; return (core_results, template_results),
        each a list of (status, message, fix-hint) tuples.

        SSOT check inventory — shared by this handler's menu run AND the Gateway
        Wizard's step 5. Add new checks HERE so both consumers get them; a
        hand-copied call list in a second consumer is exactly the drift that let
        the wizard silently omit the template-drift check (2026-07-09 review)."""
        results: List[Tuple[str, str, Optional[str]]] = []
        results.append(self._check_lxmf())
        results.append(self._check_meshtasticd())
        results.append(self._check_rnsd())
        channel_result, uplinked_names = self._check_channel_uplink()
        results.append(channel_result)
        results.append(self._check_gateway_config_channel(uplinked_names))
        results.append(self._check_gateway_identity())
        results.append(self._check_nomadnet_identity_match())
        return results, self._run_template_drift()

    def _run_preflight(self):
        from backend import clear_screen
        clear_screen()

        print(f"\n{_BOLD}{_CYAN}Gateway Bridge Pre-Flight Check{_RESET}\n")
        print(f"{_CYAN}{'─' * 60}{_RESET}\n")

        results, template_results = self.collect_results()

        for status, msg, fix in results:
            print(f"  {status}  {msg}")
            if fix:
                print(f"      {_CYAN}Fix:{_RESET} {fix}")
            print()

        # Template drift — if a known-good template is present, compare.
        if template_results:
            print(f"\n{_BOLD}{_CYAN}Template Drift Check{_RESET}")
            print(f"{_CYAN}{'─' * 60}{_RESET}\n")
            for status, msg, fix in template_results:
                print(f"  {status}  {msg}")
                if fix:
                    print(f"      {_CYAN}Fix:{_RESET} {fix}")
            results.extend(template_results)

        fails = sum(1 for s, _, _ in results if s == _FAIL)
        warns = sum(1 for s, _, _ in results if s == _WARN)
        print(f"\n{_CYAN}{'─' * 60}{_RESET}")
        if fails == 0 and warns == 0:
            print(f"{_GREEN}{_BOLD}  All checks passed — bridge ready to launch.{_RESET}")
        elif fails == 0:
            print(f"{_YELLOW}  {warns} warning(s) — bridge should work, review hints above.{_RESET}")
        else:
            print(
                f"{_RED}  {fails} failure(s), {warns} warning(s) — "
                f"fix failures before launching bridge.{_RESET}"
            )

        try:
            self.ctx.wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()

    def _run_template_drift(self) -> List[Tuple[str, str, Optional[str]]]:
        """Judge live state against the built-in templates (best match wins).

        The fleet deliberately runs multiple radio profiles (LONG_FAST/slot20
        and SHORT_TURBO/slot8 legs joined by a cross-preset bridge), so each
        box is judged against whichever template it matches best — a single
        default template false-failed one leg or the other (2026-07-09)."""
        from handlers import _gateway_preflight_template as tmpl_mod
        templates = tmpl_mod.load_templates()
        if not templates:
            # No templates installed: return BEFORE touching the radio — the
            # capture's `meshtastic --info` is a PhoneAPI query (#17 class)
            # and must not run when there is nothing to judge against.
            return []
        info_text = tmpl_mod.run_meshtastic_info()
        live = tmpl_mod.capture_live_state(info_text)
        results, chosen, total = tmpl_mod.check_template_drift_best(live, templates)
        if not results:
            return []
        if total > 1:
            results.insert(0, (_OK, f"judged against template '{chosen}' "
                                    f"(best match of {total})", None))
        return results

    def _run_export(self):
        """Snapshot current live state as a JSON template for the fleet."""
        from backend import clear_screen
        from handlers import _gateway_preflight_template as tmpl_mod
        clear_screen()
        print(f"\n{_BOLD}{_CYAN}Export Current Config as Template{_RESET}\n")
        info_text = tmpl_mod.run_meshtastic_info()
        if info_text is None:
            print(f"  {_WARN}  meshtastic --info failed; export will omit radio section")
        live = tmpl_mod.capture_live_state(info_text)
        try:
            target = tmpl_mod.export_current_as_template(live)
            print(f"  {_OK}  Wrote template to {_BOLD}{target}{_RESET}")
            print(f"\n  Review, rename, and copy into "
                  f"src/gateway/templates/preflight/ to add it to the built-in set.")
        except (OSError, PermissionError) as e:
            print(f"  {_FAIL}  Export failed: {e}")
        try:
            self.ctx.wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()

    # ------------------------------------------------------------------
    # Individual checks (each returns (status, message, fix_hint))
    # ------------------------------------------------------------------

    def _check_lxmf(self) -> Tuple[str, str, Optional[str]]:
        rns_mod, has_rns = safe_import("RNS")
        lxmf_mod, has_lxmf = safe_import("LXMF")
        if has_rns and has_lxmf:
            rns_ver = getattr(rns_mod, "__version__", "?")
            lxmf_ver = getattr(lxmf_mod, "__version__", "?")
            return (_OK, f"RNS ({rns_ver}) and LXMF ({lxmf_ver}) importable", None)
        missing = []
        if not has_rns:
            missing.append("rns")
        if not has_lxmf:
            missing.append("lxmf")
        pkgs = " ".join(missing)
        return (
            _FAIL,
            f"Python package(s) not installed: {pkgs}",
            f"pip3 install --user --break-system-packages {pkgs}",
        )

    def _check_meshtasticd(self) -> Tuple[str, str, Optional[str]]:
        if check_port(4403, "127.0.0.1", timeout=2.0):
            return (_OK, "meshtasticd reachable on 127.0.0.1:4403", None)
        status = check_service("meshtasticd")
        return (_FAIL, f"meshtasticd not reachable: {status.message}", status.fix_hint)

    def _check_rnsd(self) -> Tuple[str, str, Optional[str]]:
        rns_status = check_service("rnsd")
        if not rns_status.available:
            return (_FAIL, f"rnsd: {rns_status.message}", rns_status.fix_hint)
        info = get_rns_shared_instance_info()
        if info.get("available"):
            detail = info.get("detail", "unknown")
            return (_OK, f"rnsd running, shared instance reachable ({detail})", None)
        return (
            _WARN,
            "rnsd running but shared instance not reachable",
            "sudo systemctl restart rnsd",
        )

    def _check_channel_uplink(self) -> Tuple[Tuple[str, str, Optional[str]], List[str]]:
        """Return (status, list_of_uplinked_channel_names)."""
        info = self._run_meshtastic_info()
        if info is None:
            result = (
                _WARN,
                "could not query meshtastic --info (skipping channel check)",
                None,
            )
            return result, []
        # Parse channel lines like:
        # Index 2: SECONDARY psk=secret { ... "name": "meshforge", "uplinkEnabled": true, "downlinkEnabled": true, ... }
        uplinked: List[str] = []
        for m in re.finditer(
            r'Index (\d+):.*?"name":\s*"([^"]*)".*?"uplinkEnabled":\s*(true|false).*?"downlinkEnabled":\s*(true|false)',
            info,
        ):
            idx, name, uplink, downlink = m.groups()
            if uplink == "true" and downlink == "true":
                # Channels with empty names default to their preset name in MQTT topics
                uplinked.append(name or f"(primary, index {idx})")
        if uplinked:
            return (_OK, f"MQTT uplink enabled on: {', '.join(uplinked)}", None), uplinked
        return (
            _FAIL,
            "no Meshtastic channel has MQTT uplink enabled (gateway will see no RX)",
            "meshtastic --host 127.0.0.1 --ch-index N --ch-set uplink_enabled true "
            "--ch-set downlink_enabled true",
        ), []

    def _check_gateway_config_channel(
        self, uplinked_names: List[str]
    ) -> Tuple[str, str, Optional[str]]:
        cfg_path = get_real_user_home() / ".config" / "meshforge" / "gateway.json"
        try:
            cfg = json.loads(cfg_path.read_text())
        except FileNotFoundError:
            return (
                _WARN,
                f"gateway.json not found at {cfg_path}",
                "run the gateway once to generate defaults",
            )
        except (OSError, json.JSONDecodeError) as e:
            return (_FAIL, f"gateway.json unreadable: {e}", None)
        cfg_channel = cfg.get("mqtt_bridge", {}).get("channel") or cfg.get(
            "meshtastic", {}
        ).get("mqtt_channel")
        if not cfg_channel:
            return (_WARN, "gateway.json has no mqtt_channel set", None)
        if not uplinked_names:
            return (
                _WARN,
                f"gateway.json mqtt_channel='{cfg_channel}' — cannot verify (no uplink data)",
                None,
            )
        if cfg_channel in uplinked_names:
            return (_OK, f"gateway.json mqtt_channel='{cfg_channel}' matches uplinked channel", None)
        return (
            _FAIL,
            f"gateway.json mqtt_channel='{cfg_channel}' but uplinked channel(s) are: "
            f"{', '.join(uplinked_names)}",
            f"update mqtt_channel / mqtt_bridge.channel in {cfg_path} to match",
        )

    def _check_gateway_identity(self) -> Tuple[str, str, Optional[str]]:
        id_path = get_real_user_home() / ".config" / "meshforge" / "gateway_identity"
        if not id_path.exists():
            return (
                _WARN,
                f"gateway_identity not found at {id_path}",
                "start the gateway once to create an identity",
            )
        rns_mod, has_rns = safe_import("RNS")
        if not has_rns:
            return (_WARN, f"gateway_identity present at {id_path} (RNS not importable, can't derive hash)", None)
        try:
            identity = rns_mod.Identity.from_file(str(id_path))
            dest_hash = rns_mod.Destination.hash(identity, "lxmf", "delivery").hex()
            return (
                _OK,
                f"gateway LXMF hash: {_BOLD}{dest_hash}{_RESET} "
                f"(send from NomadNet to this address to test TX)",
                None,
            )
        except (OSError, ValueError, AttributeError) as e:
            return (_FAIL, f"could not derive gateway hash: {e}", None)

    def _check_nomadnet_identity_match(self) -> Tuple[str, str, Optional[str]]:
        cfg_path = get_real_user_home() / ".config" / "meshforge" / "gateway.json"
        try:
            cfg = json.loads(cfg_path.read_text())
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return (_WARN, "gateway.json not readable — skipping NomadNet identity check", None)
        raw = cfg.get("rns", {}).get("default_lxmf_destination")
        # default_lxmf_destination may be one hash or a LIST of recipients
        # (multi-recipient broadcast fan-out, Issue #39/#47). MEMBERSHIP is the
        # contract: NomadNet must be one of the recipients, not the only one —
        # exact-equality false-FAILed every multi-recipient box while the
        # template-drift leg passed the same fact (2026-07-09 field test).
        if isinstance(raw, str):
            dests = [raw]
        elif isinstance(raw, list):
            dests = [d for d in raw if isinstance(d, str) and d]
        else:
            dests = []
        if not dests:
            return (
                _WARN,
                "gateway.json has no default_lxmf_destination "
                "(broadcasts won't route to NomadNet)",
                "set rns.default_lxmf_destination to NomadNet's LXMF hash",
            )
        shown = dests[0][:12] + ("…" if len(dests[0]) > 12 else "") \
            + (f" (+{len(dests) - 1} more)" if len(dests) > 1 else "")
        nomadnet_log = get_real_user_home() / ".nomadnetwork" / "logfile"
        if not nomadnet_log.exists():
            return (
                _WARN,
                f"default_lxmf_destination={shown} (NomadNet logfile missing, "
                f"cannot verify)",
                None,
            )
        # Find the most recent "LXMF Router ready to receive on: <hash>" line
        try:
            text = nomadnet_log.read_text(errors="ignore")
        except OSError as e:
            return (_WARN, f"cannot read NomadNet logfile: {e}", None)
        matches = re.findall(r"LXMF Router ready to receive on: <([0-9a-f]+)>", text)
        if not matches:
            return (
                _WARN,
                f"default_lxmf_destination={shown} "
                f"(no NomadNet 'LXMF Router ready' line found yet)",
                "start NomadNet once with: nomadnet --rnsconfig /etc/reticulum --daemon",
            )
        nomadnet_hash = matches[-1]  # most recent
        if nomadnet_hash in dests:
            if len(dests) == 1:
                return (_OK, f"default_lxmf_destination matches NomadNet identity ({nomadnet_hash})", None)
            return (_OK, f"NomadNet identity {nomadnet_hash} is one of "
                         f"{len(dests)} default_lxmf_destination recipients", None)
        return (
            _FAIL,
            f"NomadNet is on {nomadnet_hash} but it is not among the "
            f"{len(dests)} default_lxmf_destination recipient(s) ({shown})",
            f"add {nomadnet_hash} to rns.default_lxmf_destination in {cfg_path}",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_meshtastic_info(self) -> Optional[str]:
        """Run `meshtastic --host 127.0.0.1 --info` and return stdout, or None on failure."""
        try:
            result = subprocess.run(
                ["meshtastic", "--host", "127.0.0.1", "--info"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                logger.debug("meshtastic --info exit=%s stderr=%s", result.returncode, result.stderr)
                return None
            return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("meshtastic --info failed: %s", e)
            return None
