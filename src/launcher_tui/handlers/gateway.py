"""
Gateway Handler — RNS-Meshtastic bridge configuration.

Converted from gateway_config_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

_GatewayConfig, _RoutingRule, _HAS_GATEWAY_CONFIG = safe_import(
    'gateway.config', 'GatewayConfig', 'RoutingRule'
)


class GatewayHandler(BaseHandler):
    """TUI handler for gateway bridge configuration."""

    handler_id = "gateway"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("gateway", "Gateway Bridge      RNS-Meshtastic-MeshCore", "gateway"),
        ]

    def execute(self, action):
        if action == "gateway":
            self._gateway_config_menu()

    def _gateway_config_menu(self):
        """Gateway bridge configuration menu."""
        if not _HAS_GATEWAY_CONFIG:
            self.ctx.dialog.msgbox(
                "Gateway Module Missing",
                "Gateway configuration module not found.\n\n"
                "Ensure src/gateway/config.py exists."
            )
            return

        try:
            config = _GatewayConfig.load()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Config Load Error",
                f"Could not load gateway configuration:\n\n"
                f"{type(e).__name__}: {e}\n\n"
                f"Starting with default configuration."
            )
            try:
                config = _GatewayConfig()
            except Exception as e:
                logger.debug("Default GatewayConfig creation failed: %s", e)
                self.ctx.dialog.msgbox(
                    "Gateway Error",
                    "Cannot create gateway configuration.\n"
                    "Check that gateway/config.py is valid."
                )
                return

        while True:
            status = "ENABLED" if config.enabled else "DISABLED"
            mode = config.bridge_mode

            choices = [
                ("status", f"Status              {status}"),
                ("mode", f"Bridge Mode         {mode}"),
                ("enable", "Enable Gateway" if not config.enabled else "Disable Gateway"),
            ]

            if config.bridge_mode == "mqtt_bridge":
                choices.append(("mqtt_bridge", "MQTT Bridge Settings"))
            else:
                choices.append(("meshtastic", "Meshtastic Settings"))

            choices.extend([
                ("rns", "RNS Settings"),
                ("routing", "Routing Rules"),
                ("telemetry", "Telemetry Settings"),
                ("templates", "Load Template"),
                ("validate", "Validate Config"),
                ("save", "Save Configuration"),
                ("back", "Back"),
            ])

            choice = self.ctx.dialog.menu(
                "Gateway Configuration",
                f"RNS <-> Meshtastic Bridge Setup\n\n"
                f"Config: ~/.config/meshforge/gateway.json",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "enable":
                config.enabled = not config.enabled
                self.ctx.dialog.msgbox(
                    "Gateway " + ("Enabled" if config.enabled else "Disabled"),
                    f"Gateway is now {'enabled' if config.enabled else 'disabled'}.\n\n"
                    "Save configuration to persist."
                )
                continue
            elif choice == "save":
                if config.save():
                    self.ctx.dialog.msgbox("Saved", "Gateway configuration saved.")
                else:
                    self.ctx.dialog.msgbox("Error", "Failed to save configuration.")
                continue

            dispatch = {
                "status": ("Gateway Status", self._show_gateway_status),
                "mode": ("Bridge Mode", self._set_bridge_mode),
                "meshtastic": ("Meshtastic Settings", self._config_meshtastic),
                "mqtt_bridge": ("MQTT Bridge Settings", self._config_mqtt_bridge),
                "rns": ("RNS Settings", self._config_rns),
                "routing": ("Routing Rules", self._config_routing),
                "telemetry": ("Telemetry Settings", self._config_telemetry),
                "templates": ("Load Template", self._load_template),
                "validate": ("Validate Config", self._validate_gateway_config),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(entry[0], entry[1], config)

    def _show_gateway_status(self, config):
        """Show detailed gateway status."""
        lines = [
            "GATEWAY CONFIGURATION STATUS",
            "=" * 40,
            "",
            f"Enabled:      {config.enabled}",
            f"Auto-start:   {config.auto_start}",
            f"Bridge Mode:  {config.bridge_mode}",
            "",
        ]

        if config.bridge_mode == "mqtt_bridge":
            lines.extend([
                "MQTT BRIDGE (zero interference):",
                f"  Broker:     {config.mqtt_bridge.broker}",
                f"  Port:       {config.mqtt_bridge.port}",
                f"  Region:     {config.mqtt_bridge.region}",
                f"  Channel:    {config.mqtt_bridge.channel}",
                f"  JSON mode:  {config.mqtt_bridge.json_enabled}",
                f"  TLS:        {config.mqtt_bridge.use_tls}",
                "",
            ])
        else:
            lines.extend([
                "MESHTASTIC (TCP - legacy):",
                f"  Host:       {config.meshtastic.host}",
                f"  Port:       {config.meshtastic.port}",
                f"  Channel:    {config.meshtastic.channel}",
                "",
            ])

        lines.extend([
            "RNS:",
            f"  Identity:   {config.rns.identity_name}",
            f"  Announce:   every {config.rns.announce_interval}s",
            "",
            "ROUTING:",
            f"  Default:    {config.default_route}",
            f"  Rules:      {len(config.routing_rules)}",
            "",
            "TELEMETRY:",
            f"  Position:   {config.telemetry.share_position}",
            f"  Battery:    {config.telemetry.share_battery}",
            f"  Interval:   {config.telemetry.update_interval}s",
        ])

        self.ctx.dialog.msgbox("Gateway Status", "\n".join(lines), width=50, height=25)

    def _set_bridge_mode(self, config):
        """Set the bridge operating mode."""
        choices = [
            ("mqtt_bridge", "MQTT Bridge (Recommended)  Zero interference"),
            ("message_bridge", "TCP Message Bridge         Legacy, blocks web client"),
            ("rns_transport", "RNS Transport              RNS over LoRa mesh"),
            ("mesh_bridge", "Mesh Bridge                Bridge two Meshtastic nets"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Bridge Mode",
            "Select how the gateway should operate:\n\n"
            "MQTT Bridge: Uses MQTT for receive, CLI for send.\n"
            "  Web client on :9443 works uninterrupted.\n"
            "  Requires: mosquitto + meshtasticd mqtt.enabled\n\n"
            "TCP Bridge: Legacy mode, holds TCP connection.\n"
            "  Blocks meshtasticd web client while running.\n\n"
            "RNS Transport: Use Meshtastic as RNS network layer\n"
            "Mesh Bridge: Connect two Meshtastic presets",
            choices
        )

        if choice and choice != "back":
            config.bridge_mode = choice
            self.ctx.dialog.msgbox(
                "Mode Set",
                f"Bridge mode set to: {choice}\n\n"
                "Save configuration to persist."
            )

    def _config_mqtt_bridge(self, config):
        """Configure MQTT bridge settings."""
        while True:
            mqtt = config.mqtt_bridge
            choices = [
                ("broker", f"Broker              {mqtt.broker}"),
                ("port", f"Port                {mqtt.port}"),
                ("region", f"Region              {mqtt.region}"),
                ("channel", f"Channel             {mqtt.channel}"),
                ("tls", f"TLS                 {mqtt.use_tls}"),
                ("auth", f"Auth                {'Set' if mqtt.username else 'None'}"),
                ("setup", "Run MQTT Setup Guide"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "MQTT Bridge Settings",
                "Configure MQTT transport for zero-interference bridging.\n\n"
                "Requires: mosquitto + meshtasticd mqtt.enabled=true",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "broker":
                val = self.ctx.dialog.inputbox(
                    "MQTT Broker",
                    "Enter MQTT broker address:",
                    init=mqtt.broker
                )
                if val:
                    mqtt.broker = val.strip()

            elif choice == "port":
                val = self.ctx.dialog.inputbox(
                    "MQTT Port",
                    "Enter MQTT broker port (1883 default, 8883 TLS):",
                    init=str(mqtt.port)
                )
                if val and val.isdigit():
                    mqtt.port = int(val)

            elif choice == "region":
                val = self.ctx.dialog.inputbox(
                    "LoRa Region",
                    "Enter LoRa region code (US, EU_868, etc.):",
                    init=mqtt.region
                )
                if val:
                    mqtt.region = val.strip()

            elif choice == "channel":
                val = self.ctx.dialog.inputbox(
                    "Meshtastic Channel",
                    "Enter Meshtastic channel name:",
                    init=mqtt.channel
                )
                if val:
                    mqtt.channel = val.strip()

            elif choice == "tls":
                mqtt.use_tls = not mqtt.use_tls
                if mqtt.use_tls and mqtt.port == 1883:
                    mqtt.port = 8883

            elif choice == "auth":
                user = self.ctx.dialog.inputbox(
                    "MQTT Username",
                    "Enter MQTT username (blank for none):",
                    init=mqtt.username
                )
                if user is not None:
                    mqtt.username = user.strip()
                    if mqtt.username:
                        pw = self.ctx.dialog.inputbox(
                            "MQTT Password",
                            "Enter MQTT password:",
                            init=""
                        )
                        if pw is not None:
                            mqtt.password = pw
                    else:
                        mqtt.password = ""

            elif choice == "setup":
                self.ctx.dialog.msgbox(
                    "MQTT Setup Guide",
                    "Step 1: Install mosquitto\n"
                    "  sudo apt install mosquitto mosquitto-clients\n\n"
                    "Step 2: Configure meshtasticd MQTT\n"
                    "  meshtastic --set mqtt.enabled true\n"
                    "  meshtastic --set mqtt.address 127.0.0.1\n"
                    "  meshtastic --set mqtt.json_enabled true\n\n"
                    "Step 3: Enable uplink on channel\n"
                    "  meshtastic --ch-index 0 --ch-set uplink_enabled true\n\n"
                    "Step 4: Downlink (only for bidirectional bridge)\n"
                    "  meshtastic --ch-index 0 --ch-set downlink_enabled true\n\n"
                    "Step 5: Verify\n"
                    "  mosquitto_sub -h localhost -t 'msh/#' -v\n\n"
                    "Or run the setup script:\n"
                    "  meshtasticd-mqtt-setup.sh --monitor  (read-only)\n"
                    "  meshtasticd-mqtt-setup.sh --bridge   (bidirectional)",
                    width=60, height=25
                )

    def _config_meshtastic(self, config):
        """Configure Meshtastic connection settings."""
        while True:
            choices = [
                ("host", f"Host                {config.meshtastic.host}"),
                ("port", f"Port                {config.meshtastic.port}"),
                ("channel", f"Channel             {config.meshtastic.channel}"),
                ("mqtt", f"Use MQTT            {config.meshtastic.use_mqtt}"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Meshtastic Settings",
                "Configure Meshtastic connection:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "host":
                host = self.ctx.dialog.inputbox(
                    "Meshtastic Host",
                    "Enter meshtasticd host (usually localhost):",
                    init=config.meshtastic.host
                )
                if host:
                    config.meshtastic.host = host.strip()

            elif choice == "port":
                port = self.ctx.dialog.inputbox(
                    "Meshtastic Port",
                    "Enter meshtasticd TCP port (default 4403):",
                    init=str(config.meshtastic.port)
                )
                if port and port.isdigit():
                    config.meshtastic.port = int(port)

            elif choice == "channel":
                channel = self.ctx.dialog.inputbox(
                    "Gateway Channel",
                    "Enter channel index for gateway messages (0-7):",
                    init=str(config.meshtastic.channel)
                )
                if channel and channel.isdigit():
                    ch = int(channel)
                    if 0 <= ch <= 7:
                        config.meshtastic.channel = ch

            elif choice == "mqtt":
                config.meshtastic.use_mqtt = not config.meshtastic.use_mqtt

    def _config_rns(self, config):
        """Configure RNS settings."""
        while True:
            choices = [
                ("identity", f"Identity Name       {config.rns.identity_name}"),
                ("announce", f"Announce Interval   {config.rns.announce_interval}s"),
                ("config_dir", f"Config Directory    {config.rns.config_dir or 'default'}"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "RNS Settings",
                "Configure Reticulum Network Stack:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "identity":
                name = self.ctx.dialog.inputbox(
                    "RNS Identity Name",
                    "Enter identity name for this gateway:",
                    init=config.rns.identity_name
                )
                if name:
                    config.rns.identity_name = name.strip()

            elif choice == "announce":
                interval = self.ctx.dialog.inputbox(
                    "Announce Interval",
                    "Enter announce interval in seconds (60-3600):",
                    init=str(config.rns.announce_interval)
                )
                if interval and interval.isdigit():
                    val = int(interval)
                    if 60 <= val <= 3600:
                        config.rns.announce_interval = val

            elif choice == "config_dir":
                dir_path = self.ctx.dialog.inputbox(
                    "RNS Config Directory",
                    "Enter RNS config directory (blank for default ~/.reticulum):",
                    init=config.rns.config_dir
                )
                if dir_path is not None:
                    config.rns.config_dir = dir_path.strip()

    def _config_routing(self, config):
        """Configure routing rules."""
        while True:
            choices = [
                ("default", f"Default Route       {config.default_route}"),
                ("add", "Add Rule"),
            ]

            for i, rule in enumerate(config.routing_rules):
                status = "ON" if rule.enabled else "OFF"
                choices.append((f"rule_{i}", f"{rule.name:<18} [{status}] {rule.direction}"))

            choices.append(("clear", "Clear All Rules"))
            choices.append(("defaults", "Load Default Rules"))
            choices.append(("back", "Back"))

            choice = self.ctx.dialog.menu(
                f"Routing Rules ({len(config.routing_rules)})",
                "Configure message routing between networks:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "clear":
                if self.ctx.dialog.yesno("Clear Rules", "Remove all routing rules?"):
                    config.routing_rules = []
                continue
            elif choice == "defaults":
                if self.ctx.dialog.yesno("Load Defaults", "Replace rules with defaults?"):
                    config.routing_rules = config.get_default_rules()
                continue
            elif choice.startswith("rule_"):
                idx = int(choice.split("_")[1])
                self.ctx.safe_call(f"Edit Rule {idx}", self._edit_routing_rule, config, idx)
                continue

            dispatch = {
                "default": ("Default Route", self._set_default_route),
                "add": ("Add Routing Rule", self._add_routing_rule),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(entry[0], entry[1], config)

    def _set_default_route(self, config):
        """Set the default routing direction."""
        choices = [
            ("bidirectional", "Bidirectional       Both ways"),
            ("mesh_to_rns", "Mesh to RNS         One way: Meshtastic -> RNS"),
            ("rns_to_mesh", "RNS to Mesh         One way: RNS -> Meshtastic"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Default Route",
            "Select default routing direction:",
            choices
        )

        if choice and choice != "back":
            config.default_route = choice

    def _add_routing_rule(self, config):
        """Add a new routing rule."""
        name = self.ctx.dialog.inputbox(
            "Rule Name",
            "Enter a unique name for this rule:",
            init=""
        )
        if not name:
            return

        if any(r.name == name for r in config.routing_rules):
            self.ctx.dialog.msgbox("Error", f"Rule '{name}' already exists.")
            return

        rule = _RoutingRule(name=name.strip())
        config.routing_rules.append(rule)
        self.ctx.dialog.msgbox("Rule Added", f"Rule '{name}' added.\n\nEdit it to configure.")

    def _edit_routing_rule(self, config, idx: int):
        """Edit a routing rule."""
        if idx >= len(config.routing_rules):
            return

        rule = config.routing_rules[idx]

        while True:
            choices = [
                ("enabled", f"Enabled             {rule.enabled}"),
                ("direction", f"Direction           {rule.direction}"),
                ("source", f"Source Filter       {rule.source_filter or '(any)'}"),
                ("dest", f"Dest Filter         {rule.dest_filter or '(any)'}"),
                ("message", f"Message Filter      {rule.message_filter or '(any)'}"),
                ("priority", f"Priority            {rule.priority}"),
                ("delete", "Delete Rule"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                f"Edit Rule: {rule.name}",
                "Configure routing rule:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "enabled":
                rule.enabled = not rule.enabled
            elif choice == "direction":
                self._set_rule_direction(rule)
            elif choice == "source":
                val = self.ctx.dialog.inputbox(
                    "Source Filter",
                    "Regex pattern for source address (blank for any):",
                    init=rule.source_filter
                )
                if val is not None:
                    rule.source_filter = val.strip()
            elif choice == "dest":
                val = self.ctx.dialog.inputbox(
                    "Destination Filter",
                    "Regex pattern for destination (blank for any):",
                    init=rule.dest_filter
                )
                if val is not None:
                    rule.dest_filter = val.strip()
            elif choice == "message":
                val = self.ctx.dialog.inputbox(
                    "Message Filter",
                    "Regex pattern for message content (blank for any):",
                    init=rule.message_filter
                )
                if val is not None:
                    rule.message_filter = val.strip()
            elif choice == "priority":
                val = self.ctx.dialog.inputbox(
                    "Priority",
                    "Rule priority (higher = evaluated first):",
                    init=str(rule.priority)
                )
                if val and val.lstrip('-').isdigit():
                    rule.priority = int(val)
            elif choice == "delete":
                if self.ctx.dialog.yesno("Delete Rule", f"Delete rule '{rule.name}'?"):
                    config.routing_rules.pop(idx)
                    break

    def _set_rule_direction(self, rule):
        """Set routing rule direction."""
        choices = [
            ("bidirectional", "Bidirectional"),
            ("mesh_to_rns", "Mesh to RNS only"),
            ("rns_to_mesh", "RNS to Mesh only"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu("Direction", "Select routing direction:", choices)
        if choice and choice != "back":
            rule.direction = choice

    def _config_telemetry(self, config):
        """Configure telemetry settings."""
        while True:
            choices = [
                ("position", f"Share Position      {config.telemetry.share_position}"),
                ("battery", f"Share Battery       {config.telemetry.share_battery}"),
                ("environment", f"Share Environment   {config.telemetry.share_environment}"),
                ("precision", f"Position Precision  {config.telemetry.position_precision} decimals"),
                ("interval", f"Update Interval     {config.telemetry.update_interval}s"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Telemetry Settings",
                "Configure what data is shared between networks:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "position":
                config.telemetry.share_position = not config.telemetry.share_position
            elif choice == "battery":
                config.telemetry.share_battery = not config.telemetry.share_battery
            elif choice == "environment":
                config.telemetry.share_environment = not config.telemetry.share_environment
            elif choice == "precision":
                val = self.ctx.dialog.inputbox(
                    "Position Precision",
                    "Decimal places for lat/lon (1-7):",
                    init=str(config.telemetry.position_precision)
                )
                if val and val.isdigit():
                    p = int(val)
                    if 1 <= p <= 7:
                        config.telemetry.position_precision = p
            elif choice == "interval":
                val = self.ctx.dialog.inputbox(
                    "Update Interval",
                    "Telemetry update interval in seconds (30-3600):",
                    init=str(config.telemetry.update_interval)
                )
                if val and val.isdigit():
                    i = int(val)
                    if 30 <= i <= 3600:
                        config.telemetry.update_interval = i

    def _load_template(self, config):
        """Load a configuration template."""
        templates = _GatewayConfig.get_available_templates()

        choices = []
        for name, desc in templates.items():
            short_desc = desc[:40] + "..." if len(desc) > 40 else desc
            choices.append((name, short_desc))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "Load Template",
            "Select a pre-configured template:\n\n"
            "This will replace current settings.",
            choices
        )

        if choice and choice != "back":
            if self.ctx.dialog.yesno(
                "Confirm",
                f"Load template '{choice}'?\n\n"
                f"{templates[choice]}\n\n"
                "This will replace current configuration."
            ):
                new_config = _GatewayConfig.from_template(choice)
                if new_config:
                    config.enabled = new_config.enabled
                    config.auto_start = new_config.auto_start
                    config.bridge_mode = new_config.bridge_mode
                    config.meshtastic = new_config.meshtastic
                    config.rns = new_config.rns
                    config.mqtt_bridge = new_config.mqtt_bridge
                    config.rns_transport = new_config.rns_transport
                    config.mesh_bridge = new_config.mesh_bridge
                    config.routing_rules = new_config.routing_rules
                    config.default_route = new_config.default_route
                    config.telemetry = new_config.telemetry
                    config.log_level = new_config.log_level
                    config.log_messages = new_config.log_messages

                    self.ctx.dialog.msgbox(
                        "Template Loaded",
                        f"Loaded template: {choice}\n\n"
                        "Save configuration to persist."
                    )
                else:
                    self.ctx.dialog.msgbox("Error", "Failed to load template.")

    def _validate_gateway_config(self, config):
        """Validate the gateway configuration."""
        is_valid, errors = config.validate()

        if not errors:
            self.ctx.dialog.msgbox(
                "Validation Passed",
                "Configuration is valid!\n\n"
                "No errors or warnings found."
            )
            return

        lines = ["VALIDATION RESULTS", "=" * 40, ""]

        error_count = sum(1 for e in errors if e.severity == "error")
        warn_count = sum(1 for e in errors if e.severity == "warning")
        info_count = sum(1 for e in errors if e.severity == "info")

        lines.append(f"Errors:   {error_count}")
        lines.append(f"Warnings: {warn_count}")
        lines.append(f"Info:     {info_count}")
        lines.append("")

        for err in errors:
            icon = {"error": "X", "warning": "!", "info": "i"}.get(err.severity, "?")
            lines.append(f"[{icon}] {err.field}:")
            lines.append(f"    {err.message}")
            lines.append("")

        status = "VALID" if is_valid else "INVALID"
        lines.insert(2, f"Status: {status}")

        self.ctx.dialog.msgbox("Validation Results", "\n".join(lines), width=60, height=25)
