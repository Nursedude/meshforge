"""NomadNet inline config menu — toggles + setters for common settings.

Historically the TUI only offered *view* and *edit* (launch $EDITOR) for
the NomadNet config file. The settings operators actually change most
often — ``enable_node``, ``enable_client``, ``announce_at_start``,
``display_name``, ``node_name`` — are one-line booleans or strings. This
mixin exposes them as inline menu items so routine config changes don't
require an editor round-trip.

Complements ``_configure_propagation_node`` in ``nomadnet.py`` (the
existing propnode editor), which is called from the config menu here
rather than reimplemented.

Config path: ``<real_user_home>/.nomadnetwork/config``. We preserve the
file's formatting (indentation, comments, blank lines) by doing
line-oriented edits, not config-parser round-trips.
"""

import logging
import re
from pathlib import Path
from typing import List, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


# Section assignments for unknown keys. Lines up with NomadNet's own
# config layout (see NomadNet/nomadnet/defaults/nomadnetconfig in upstream).
_DEFAULT_SECTIONS = {
    "enable_node": "[node]",
    "enable_client": "[client]",
    "announce_at_start": "[node]",
    "announce_interval": "[node]",
    "display_name": "[node]",
    "node_name": "[node]",
    "user_interface": "[textui]",
    "propagation_node": "[client]",
}


class NomadNetConfigOpsMixin:
    """Inline config editing for NomadNet's default identity."""

    # ------------------------------------------------------------------
    # Menu dispatcher
    # ------------------------------------------------------------------

    def _config_menu(self):
        """Top-level NomadNet configuration submenu."""
        while True:
            cfg = self._default_config_path()
            current = self._read_key_values(cfg) if cfg.exists() else {}

            def _val(key: str, fallback: str = "(unset)") -> str:
                v = current.get(key)
                return v if v is not None else fallback

            subtitle_lines = [f"Config: {cfg}"]
            if not cfg.exists():
                subtitle_lines.append("(config not generated yet)")
            else:
                subtitle_lines.append(
                    f"enable_node={_val('enable_node')}  "
                    f"enable_client={_val('enable_client')}  "
                    f"announce={_val('announce_at_start')}"
                )
            subtitle = "\n".join(subtitle_lines)

            choices = [
                ("view", "View full config"),
                ("edit", "Edit in $EDITOR"),
                ("propnode", "Propagation node"
                 + self._annotation(current.get('propagation_node'))),
                ("announce", "Toggle announce_at_start"
                 + self._annotation(current.get('announce_at_start'))),
                ("enable_node", "Toggle enable_node"
                 + self._annotation(current.get('enable_node'))),
                ("enable_client", "Toggle enable_client"
                 + self._annotation(current.get('enable_client'))),
                ("display_name", "Set display_name"
                 + self._annotation(current.get('display_name'))),
                ("node_name", "Set node_name"
                 + self._annotation(current.get('node_name'))),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "NomadNet Configuration", subtitle, choices,
            )
            if choice is None or choice == "back":
                return

            if choice == "view":
                self.ctx.safe_call("View Config", self._view_nomadnet_config)
                continue
            if choice == "edit":
                self.ctx.safe_call("Edit Config", self._edit_nomadnet_config)
                continue
            if choice == "propnode":
                self.ctx.safe_call(
                    "Propagation Node", self._configure_propagation_node,
                )
                continue
            if choice in ("announce", "enable_node", "enable_client"):
                key = ("announce_at_start"
                       if choice == "announce" else choice)
                self.ctx.safe_call(
                    f"Toggle {key}",
                    lambda k=key: self._toggle_config_bool(k),
                )
                continue
            if choice in ("display_name", "node_name"):
                self.ctx.safe_call(
                    f"Set {choice}",
                    lambda k=choice: self._prompt_set_string(k),
                )
                continue

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _default_config_path(self) -> Path:
        """Resolve the default-identity config path."""
        return get_real_user_home() / ".nomadnetwork" / "config"

    def _annotation(self, value: Optional[str]) -> str:
        """Short inline annotation for menu choices: ``  [yes]`` / ``  [unset]``."""
        if value is None:
            return "  [unset]"
        # Truncate long strings (display_name etc.) for menu sanity
        shown = value if len(value) <= 24 else value[:21] + "..."
        return f"  [{shown}]"

    def _read_key_values(self, cfg: Path) -> dict:
        """Parse simple ``key = value`` lines into a dict.

        Uncommented lines only; first occurrence wins. Cross-section
        collisions are unusual in NomadNet configs so we don't namespace.
        """
        result: dict = {}
        try:
            text = cfg.read_text()
        except (OSError, PermissionError) as e:
            logger.debug("Cannot read NomadNet config %s: %s", cfg, e)
            return result
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                continue
            if "=" not in stripped:
                continue
            key, _, val = stripped.partition("=")
            key = key.strip()
            val = val.strip()
            if key and key not in result:
                result[key] = val
        return result

    # ------------------------------------------------------------------
    # Toggles / setters
    # ------------------------------------------------------------------

    def _toggle_config_bool(self, key: str) -> None:
        """Flip a ``<key> = yes|no`` line, creating it if absent."""
        cfg = self._default_config_path()
        if not cfg.exists():
            self.ctx.dialog.msgbox(
                "No Config",
                f"Config doesn't exist yet:\n\n  {cfg}\n\n"
                f"Start the Default Identity once to generate it.",
            )
            return

        current = self._read_key_values(cfg).get(key)
        new_value = "no" if (current or "").lower() == "yes" else "yes"

        if not self.ctx.dialog.yesno(
            f"Toggle {key}",
            f"Change {key} from '{current or '(unset)'}' to '{new_value}'?\n\n"
            f"(NomadNet must restart to pick up the change.)",
        ):
            return

        ok, err = self._write_config_value(cfg, key, new_value)
        if ok:
            self.ctx.dialog.msgbox(
                f"{key} = {new_value}",
                "Config updated. Restart NomadNet for the change to take effect:\n"
                "  Service Control > Restart",
            )
        else:
            self.ctx.dialog.msgbox("Write failed", err)

    def _prompt_set_string(self, key: str) -> None:
        """Prompt for a string value and write it to the config."""
        cfg = self._default_config_path()
        if not cfg.exists():
            self.ctx.dialog.msgbox(
                "No Config",
                f"Config doesn't exist yet:\n\n  {cfg}\n\n"
                f"Start the Default Identity once to generate it.",
            )
            return

        current = self._read_key_values(cfg).get(key, "")
        value = self.ctx.dialog.inputbox(
            f"Set {key}",
            f"Enter a value for {key}. Leave empty to clear.",
            current,
        )
        if value is None:
            return  # cancelled
        value = value.strip()

        # Sanity — no quotes required, no newlines
        if "\n" in value or "\r" in value:
            self.ctx.dialog.msgbox(
                "Invalid value",
                f"'{key}' cannot contain newlines.",
            )
            return

        if value == "":
            ok, err = self._remove_config_key(cfg, key)
        else:
            ok, err = self._write_config_value(cfg, key, value)

        if ok:
            self.ctx.dialog.msgbox(
                f"{key} updated",
                f"{key} = {value or '(removed)'}\n\n"
                f"Restart NomadNet for the change to take effect:\n"
                f"  Service Control > Restart",
            )
        else:
            self.ctx.dialog.msgbox("Write failed", err)

    # ------------------------------------------------------------------
    # File edits (line-preserving)
    # ------------------------------------------------------------------

    def _write_config_value(
        self, cfg: Path, key: str, value: str,
    ) -> tuple:
        """Write ``<key> = <value>`` preserving the file's existing shape.

        If the key already exists (uncommented), replace it in place with
        the same indentation. Otherwise append under the expected section
        (``_DEFAULT_SECTIONS``), creating the section if it's absent.
        Returns ``(ok, error_message)``.
        """
        try:
            lines = cfg.read_text().splitlines()
        except (OSError, PermissionError) as e:
            return False, f"Read failed: {e}"

        key_re = re.compile(
            r'^(?P<indent>\s*)' + re.escape(key) + r'\s*=.*$'
        )
        out: List[str] = []
        replaced = False
        for line in lines:
            m = key_re.match(line)
            if m and not replaced:
                out.append(f"{m.group('indent')}{key} = {value}")
                replaced = True
            else:
                out.append(line)

        if not replaced:
            target_section = _DEFAULT_SECTIONS.get(key, "[client]")
            out = self._append_under_section(out, target_section, key, value)

        try:
            cfg.write_text("\n".join(out) + "\n")
        except (OSError, PermissionError) as e:
            return False, f"Write failed: {e}"
        return True, ""

    def _remove_config_key(self, cfg: Path, key: str) -> tuple:
        """Remove any uncommented line matching ``<key> = ...``."""
        try:
            lines = cfg.read_text().splitlines()
        except (OSError, PermissionError) as e:
            return False, f"Read failed: {e}"

        key_re = re.compile(r'^\s*' + re.escape(key) + r'\s*=.*$')
        out = [line for line in lines if not key_re.match(line)]

        try:
            cfg.write_text("\n".join(out) + "\n")
        except (OSError, PermissionError) as e:
            return False, f"Write failed: {e}"
        return True, ""

    # ------------------------------------------------------------------
    # Propagation node (LXMF store-and-forward) — moved from nomadnet.py
    # to keep that file under the 1,500-line cap (Issue #6 / Issue #45).
    # ------------------------------------------------------------------

    def _configure_propagation_node(self):
        """Configure the LXMF propagation node for store-and-forward messaging.

        Writes/updates the propagation_node setting in the NomadNet config
        file under the [client] section. This tells LXMF where to sync
        messages for offline destinations.

        Expects ``self._get_nomadnet_config_path()`` from
        ``NomadNetInstallUtilsMixin``.
        """
        config_path = self._get_nomadnet_config_path()
        if not config_path or not config_path.exists():
            self.ctx.dialog.msgbox(
                "No Config",
                "NomadNet config not found.\n\n"
                "Launch NomadNet once first to generate it,\n"
                "then set the propagation node.",
            )
            return

        # Read current value if set
        current_value = ""
        try:
            content = config_path.read_text()
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("propagation_node"):
                    parts = stripped.split("=", 1)
                    if len(parts) == 2:
                        current_value = parts[1].strip()
                        break
        except (OSError, PermissionError) as e:
            logger.warning("Cannot read NomadNet config: %s", e)

        prompt = (
            "Enter the LXMF propagation node hash (32 hex characters).\n\n"
            "This enables store-and-forward messaging for offline nodes.\n"
            "You can find propagation nodes via 'rnstatus' or NomadNet's\n"
            "network browser.\n\n"
            "Leave empty to clear the current setting."
        )

        result = self.ctx.dialog.inputbox(
            "Propagation Node", prompt, current_value
        )
        if result is None:
            return

        node_hash = result.strip()

        # Validate if non-empty
        if node_hash:
            if len(node_hash) != 32:
                self.ctx.dialog.msgbox(
                    "Invalid Hash",
                    f"Expected 32 hex characters, got {len(node_hash)}.\n\n"
                    f"Input: {node_hash}",
                )
                return
            try:
                bytes.fromhex(node_hash)
            except ValueError:
                self.ctx.dialog.msgbox(
                    "Invalid Hex",
                    f"Not valid hexadecimal:\n  {node_hash}",
                )
                return

        # Update NomadNet config
        try:
            content = config_path.read_text()
            lines = content.splitlines()
            found = False
            new_lines = []

            for line in lines:
                if line.strip().startswith("propagation_node"):
                    if node_hash:
                        new_lines.append(
                            f"  propagation_node = {node_hash}"
                        )
                    # else: drop the line to clear the setting
                    found = True
                else:
                    new_lines.append(line)

            # If not found and we have a value, add under [client]
            if not found and node_hash:
                final_lines = []
                added = False
                for line in new_lines:
                    final_lines.append(line)
                    if line.strip() == "[client]" and not added:
                        final_lines.append(
                            f"  propagation_node = {node_hash}"
                        )
                        added = True
                if not added:
                    # No [client] section — append one
                    final_lines.append("")
                    final_lines.append("[client]")
                    final_lines.append(
                        f"  propagation_node = {node_hash}"
                    )
                new_lines = final_lines

            config_path.write_text("\n".join(new_lines) + "\n")

            if node_hash:
                self.ctx.dialog.msgbox(
                    "Propagation Node Set",
                    f"Propagation node configured:\n  {node_hash}\n\n"
                    "Restart NomadNet for the change to take effect.",
                )
            else:
                self.ctx.dialog.msgbox(
                    "Propagation Node Cleared",
                    "Propagation node setting removed.\n\n"
                    "Restart NomadNet for the change to take effect.",
                )
        except (OSError, PermissionError) as e:
            self.ctx.dialog.msgbox(
                "Error", f"Failed to update config:\n{e}",
            )

    def _append_under_section(
        self, lines: List[str], section: str, key: str, value: str,
    ) -> List[str]:
        """Insert ``  key = value`` as the last entry inside ``[section]``.

        Scans forward from the section header; appends right before the
        next section boundary (or EOF). Creates the section at EOF if
        it's missing. Keeps blank-line footer conventions intact.
        """
        section_re = re.compile(r'^\s*\[(?P<name>[^\]]+)\]\s*$')
        target_name = section.strip("[]")

        # Locate the section
        section_idx = -1
        for i, line in enumerate(lines):
            m = section_re.match(line)
            if m and m.group("name").strip() == target_name:
                section_idx = i
                break

        if section_idx < 0:
            # Append a new section + key at EOF
            tail = [""] if lines and lines[-1] != "" else []
            return lines + tail + [f"[{target_name}]",
                                   f"  {key} = {value}"]

        # Find end of this section (next section header or EOF)
        insert_at = len(lines)
        for j in range(section_idx + 1, len(lines)):
            if section_re.match(lines[j]):
                insert_at = j
                break

        # Walk back past trailing blank lines inside the section
        while (insert_at - 1 > section_idx
               and lines[insert_at - 1].strip() == ""):
            insert_at -= 1

        new_lines = lines[:insert_at] + [f"  {key} = {value}"] + lines[insert_at:]
        return new_lines
