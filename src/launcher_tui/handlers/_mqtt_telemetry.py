"""
MQTT Telemetry & Statistics — extracted helpers for MQTTHandler.

Extracted from mqtt.py for file-size compliance (CLAUDE.md #6).
Each function takes the handler instance as first parameter to access
handler.ctx.dialog, handler._mqtt_subscriber, etc.
"""

import json
import logging
import shutil
import subprocess
import time
from typing import Optional, Dict, Any

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Telemetry poller for silent node detection & batch polling.
# TelemetryPoller is the class, get_telemetry_poller is the singleton accessor.
TelemetryPoller, get_telemetry_poller, _HAS_TELEMETRY_POLLER = safe_import(
    'utils.telemetry_poller', 'TelemetryPoller', 'get_telemetry_poller'
)

def show_mqtt_stats(handler):
    """Show MQTT statistics."""
    lines = ["MQTT STATISTICS", "=" * 40, ""]

    if handler._mqtt_subscriber:
        stats = handler._mqtt_subscriber.get_stats()

        lines.append("NODE COUNTS:")
        lines.append(f"  Total nodes:      {stats.get('node_count', 0)}")
        lines.append(f"  Online (15 min):  {stats.get('online_count', 0)}")
        lines.append(f"  With position:    {stats.get('with_position', 0)}")
        lines.append("")

        env_count = stats.get('nodes_with_env_metrics', 0)
        aq_count = stats.get('nodes_with_aq_metrics', 0)
        health_count = stats.get('nodes_with_health_metrics', 0)
        if env_count or aq_count or health_count:
            lines.append("SENSOR NODES:")
            if env_count:
                lines.append(f"  Environment:      {env_count}")
            if aq_count:
                lines.append(f"  Air Quality:      {aq_count}")
            if health_count:
                lines.append(f"  Health Metrics:   {health_count}")
            lines.append("")

        chutil_warn = stats.get('nodes_chutil_warning', 0)
        chutil_crit = stats.get('nodes_chutil_critical', 0)
        airutil_warn = stats.get('nodes_airutiltx_warning', 0)
        airutil_crit = stats.get('nodes_airutiltx_critical', 0)
        if chutil_warn or chutil_crit or airutil_warn or airutil_crit:
            lines.append("MESH HEALTH:")
            if chutil_warn:
                lines.append(f"  ChUtil >25%:      {chutil_warn} nodes")
            if chutil_crit:
                lines.append(f"  ChUtil >40%:      {chutil_crit} nodes [!]")
            if airutil_warn:
                lines.append(f"  AirUtil >7%:      {airutil_warn} nodes")
            if airutil_crit:
                lines.append(f"  AirUtil >10%:     {airutil_crit} nodes [!]")
            lines.append("")

        relay_discovered = stats.get('nodes_discovered_via_relay', 0)
        relay_merged = stats.get('relay_nodes_merged', 0)
        if relay_discovered or relay_merged:
            lines.append("RELAY DISCOVERY:")
            if relay_discovered:
                lines.append(f"  Via relay:        {relay_discovered}")
            if relay_merged:
                lines.append(f"  Merged nodes:     {relay_merged}")
            lines.append("")

        lines.append("TRAFFIC:")
        lines.append(f"  Messages recv:    {stats.get('messages_received', 0)}")
        lines.append(f"  Messages rejected:{stats.get('messages_rejected', 0)}")
        lines.append(f"  Reconnect tries:  {stats.get('reconnect_attempts', 0)}")

        if stats.get('connect_time'):
            lines.append("")
            lines.append(f"Connected since: {stats['connect_time']}")
    else:
        cache = load_mqtt_cache()
        if cache:
            lines.append(f"Cached nodes: {len(cache)}")
        else:
            lines.append("No data available.")
            lines.append("Start the MQTT subscriber to collect data.")

    handler.ctx.dialog.msgbox("MQTT Statistics", "\n".join(lines))

def export_mqtt_data(handler):
    """Export MQTT node data to file."""
    if not handler._mqtt_subscriber and not load_mqtt_cache():
        handler.ctx.dialog.msgbox("No Data", "No MQTT data to export.")
        return

    export_path = get_real_user_home() / ".local" / "share" / "meshforge" / "mqtt_export.json"

    try:
        if handler._mqtt_subscriber:
            nodes = handler._mqtt_subscriber.get_nodes()
            nodes_data = []
            for node in nodes:
                nodes_data.append({
                    'id': node.node_id,
                    'name': node.long_name or node.short_name or node.node_id,
                    'network': 'meshtastic',
                    'lat': node.latitude,
                    'lon': node.longitude,
                    'last_seen': node.get_age_string(),
                    'battery': node.battery_level,
                    'snr': node.snr,
                    'rssi': node.rssi,
                    'hardware': node.hardware_model,
                })
        else:
            nodes_data = load_mqtt_cache()

        export_path.parent.mkdir(parents=True, exist_ok=True)
        with open(export_path, 'w') as f:
            json.dump({'nodes': nodes_data, 'exported_at': time.time()}, f, indent=2)

        handler.ctx.dialog.msgbox(
            "Export Complete",
            f"MQTT data exported to:\n{export_path}\n\n"
            f"Nodes exported: {len(nodes_data)}"
        )
    except Exception as e:
        handler.ctx.dialog.msgbox("Export Error", f"Failed to export:\n{e}")

def show_mqtt_nodes(handler):
    """Show nodes discovered via MQTT."""
    nodes = []
    if handler._mqtt_subscriber:
        nodes = handler._mqtt_subscriber.get_nodes()

    if not nodes:
        cache_data = load_mqtt_cache()
        if cache_data:
            nodes = cache_data
        else:
            handler.ctx.dialog.msgbox(
                "No Nodes",
                "No MQTT nodes discovered yet.\n\n"
                "Start the subscriber and wait for network activity."
            )
            return

    if not nodes:
        handler.ctx.dialog.msgbox("No Nodes", "No nodes discovered yet.")
        return

    choices = []
    node_list = nodes[:50]
    for i, node in enumerate(node_list):
        if hasattr(node, 'long_name'):
            name = node.long_name or node.short_name or node.node_id
            last_seen = node.get_age_string()
            health_ind = ""
            if hasattr(node, 'heart_bpm') and node.heart_bpm:
                health_ind = " [H]"
        elif isinstance(node, dict):
            props = node.get('properties', node)
            name = props.get('name', props.get('id', f'Node {i}'))
            last_seen = props.get('last_seen', 'cached')
            health_ind = ""
        else:
            name = f'Node {i}'
            last_seen = 'unknown'
            health_ind = ""
        choices.append((str(i), f"{str(name)[:18]:<18}{health_ind} ({last_seen})"))

    if len(nodes) > 50:
        choices.append(("more", f"... and {len(nodes) - 50} more nodes"))

    def _handle(selected):
        if selected == "more":
            return
        try:
            idx = int(selected)
            if 0 <= idx < len(node_list):
                show_mqtt_node_details(handler, node_list[idx])
        except (ValueError, IndexError):
            pass

    handler.run_menu_loop(
        f"MQTT Nodes ({len(nodes)})",
        "Select a node for details, or Back to exit:",
        choices, default_handler=_handle,
    )

def show_mqtt_node_details(handler, node):
    """Show detailed information for an MQTT-discovered node."""
    lines = []

    if hasattr(node, 'node_id'):
        lines.append(f"NODE: {node.node_id}")
        lines.append("=" * 50)
        lines.append("")

        lines.append("IDENTITY:")
        lines.append("-" * 50)
        if node.long_name:
            lines.append(f"  Long Name:  {node.long_name}")
        if node.short_name:
            lines.append(f"  Short Name: {node.short_name}")
        if node.hardware_model:
            lines.append(f"  Hardware:   {node.hardware_model}")
        if node.role:
            lines.append(f"  Role:       {node.role}")
        lines.append(f"  Via MQTT:   Yes")
        lines.append(f"  Last Seen:  {node.get_age_string()}")
        lines.append("")

        has_health = (
            (hasattr(node, 'heart_bpm') and node.heart_bpm) or
            (hasattr(node, 'spo2') and node.spo2) or
            (hasattr(node, 'body_temperature') and node.body_temperature)
        )
        if has_health:
            lines.append("HEALTH METRICS:")
            lines.append("-" * 50)
            if hasattr(node, 'heart_bpm') and node.heart_bpm:
                lines.append(f"  Heart Rate: {node.heart_bpm} BPM")
            if hasattr(node, 'spo2') and node.spo2:
                lines.append(f"  SpO2:       {node.spo2}%")
            if hasattr(node, 'body_temperature') and node.body_temperature:
                lines.append(f"  Body Temp:  {node.body_temperature:.1f}C")
            lines.append("")

        has_device = node.battery_level or node.voltage
        has_channel = node.channel_utilization or node.air_util_tx
        if has_device or has_channel:
            lines.append("DEVICE TELEMETRY:")
            lines.append("-" * 50)
            if node.battery_level:
                lines.append(f"  Battery:    {node.battery_level}%")
            if node.voltage:
                lines.append(f"  Voltage:    {node.voltage:.2f}V")
            if node.channel_utilization:
                chutil = node.channel_utilization
                warn = " [!]" if chutil > 25 else ""
                lines.append(f"  ChUtil:     {chutil:.1f}%{warn}")
            if node.air_util_tx:
                airutil = node.air_util_tx
                warn = " [!]" if airutil > 7 else ""
                lines.append(f"  AirUtilTX:  {airutil:.1f}%{warn}")
            lines.append("")

        has_env = node.temperature or node.humidity or node.pressure
        if has_env:
            lines.append("ENVIRONMENT:")
            lines.append("-" * 50)
            if node.temperature:
                lines.append(f"  Temperature: {node.temperature:.1f}C")
            if node.humidity:
                lines.append(f"  Humidity:    {node.humidity:.0f}%")
            if node.pressure:
                lines.append(f"  Pressure:    {node.pressure:.0f} hPa")
            lines.append("")

        has_aq = node.pm25_standard or node.co2 or node.iaq
        if has_aq:
            lines.append("AIR QUALITY:")
            lines.append("-" * 50)
            if node.pm25_standard:
                lines.append(f"  PM2.5:      {node.pm25_standard} ug/m3")
            if node.pm10_standard:
                lines.append(f"  PM10:       {node.pm10_standard} ug/m3")
            if node.co2:
                lines.append(f"  CO2:        {node.co2} ppm")
            if node.iaq:
                lines.append(f"  IAQ Index:  {node.iaq}")
            lines.append("")

        if node.snr or node.rssi:
            lines.append("SIGNAL QUALITY:")
            lines.append("-" * 50)
            if node.snr:
                lines.append(f"  SNR:        {node.snr:.1f} dB")
            if node.rssi:
                lines.append(f"  RSSI:       {node.rssi} dBm")
            if node.hops_away is not None:
                lines.append(f"  Hops:       {node.hops_away}")
            lines.append("")

        if node.latitude and node.longitude:
            lines.append("POSITION:")
            lines.append("-" * 50)
            lines.append(f"  Latitude:   {node.latitude:.6f}")
            lines.append(f"  Longitude:  {node.longitude:.6f}")
            if node.altitude:
                lines.append(f"  Altitude:   {node.altitude}m")
            lines.append("")

        if hasattr(node, 'relay_node') and node.relay_node:
            lines.append("RELAY INFO:")
            lines.append("-" * 50)
            lines.append(f"  Relay Node: !...{node.relay_node:02x}")
            if hasattr(node, 'next_hop') and node.next_hop:
                lines.append(f"  Next Hop:   !...{node.next_hop:02x}")
            lines.append("")

    else:
        props = node.get('properties', node)
        lines.append(f"NODE: {props.get('id', 'Unknown')}")
        lines.append("=" * 50)
        lines.append(f"Name: {props.get('name', 'Unknown')}")
        lines.append(f"Last Seen: {props.get('last_seen', 'Unknown')}")

    handler.ctx.dialog.msgbox("Node Details", "\n".join(lines))

def load_mqtt_cache() -> list:
    """Load cached MQTT nodes from file."""
    cache_path = get_real_user_home() / ".local" / "share" / "meshforge" / "mqtt_nodes.json"
    try:
        if cache_path.exists():
            with open(cache_path) as f:
                data = json.load(f)
                if data.get('type') == 'FeatureCollection':
                    return data.get('features', [])
                return data.get('nodes', [])
    except Exception as e:
        logger.debug("Error loading MQTT cache: %s", e)
    return []

def detect_local_channel() -> Optional[str]:
    """Detect primary channel name from local meshtasticd."""
    try:
        cli = shutil.which('meshtastic') or 'meshtastic'
        result = subprocess.run(
            [cli, '--host', 'localhost', '--ch-index', '0', '--info'],
            capture_output=True, text=True, timeout=15
        )

        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'name' in line.lower():
                    parts = line.split(':')
                    if len(parts) >= 2:
                        name = parts[1].strip().strip('"\'')
                        if name and name.lower() not in ('none', ''):
                            logger.debug("Detected channel: %s", name)
                            return name
    except Exception as e:
        logger.debug("Could not detect channel: %s", e)

    return None

def configure_private_broker(handler, config: Dict[str, Any]):
    """Guided setup for a private MQTT broker."""
    # Import save_mqtt_config here to avoid circular dependency
    from .mqtt import save_mqtt_config

    broker = handler.ctx.dialog.inputbox(
        "Broker Address",
        "Enter your private MQTT broker hostname or IP:\n\n"
        "Examples:\n"
        "  gt.wildc.net\n"
        "  192.168.1.100\n"
        "  mqtt.local",
        init=config.get('broker', '')
    )
    if not broker:
        return

    port = handler.ctx.dialog.inputbox(
        "Broker Port",
        "Enter MQTT port:\n\n"
        "  1883 = Plain TCP\n"
        "  1884 = Alternative plain TCP\n"
        "  8883 = TLS encrypted",
        init=str(config.get('port', 1883))
    )
    if not port or not port.isdigit():
        return

    username = handler.ctx.dialog.inputbox(
        "Username", "MQTT username (blank for anonymous):",
        init=config.get('username', ''))

    password = handler.ctx.dialog.inputbox(
        "Password", "MQTT password (blank for none):", init='')

    root_topic = handler.ctx.dialog.inputbox(
        "Root Topic",
        "MQTT root topic -- controls which nodes you see:\n\n"
        "  msh           = ALL nodes (can be 5000+)\n"
        "  msh/US        = US region only\n"
        "  msh/US/2/e    = US encrypted channel\n"
        "  msh/HI        = Hawaii only (if broker supports)\n\n"
        "Your meshtasticd MQTT module must use the same root topic.",
        init=config.get('root_topic', 'msh/US/2/e')
    )
    if not root_topic:
        root_topic = "msh/US/2/e"

    channel = handler.ctx.dialog.inputbox(
        "Channel Name",
        "Meshtastic channel to subscribe to:\n\n"
        "  LongFast   = Default Meshtastic channel\n"
        "  HawaiiNet  = Regional channel\n"
        "  meshforge  = Private MeshForge channel\n\n"
        "Must match your radio's channel configuration.",
        init=config.get('channel', 'LongFast')
    )
    if not channel:
        channel = "LongFast"

    topic = f"{root_topic}/{channel}/#"
    use_tls = int(port) == 8883

    new_config = {
        'broker': broker,
        'port': int(port),
        'topic': topic,
        'root_topic': root_topic,
        'channel': channel,
        'username': username if username else None,
        'password': password if password else None,
        'use_tls': use_tls,
    }

    new_config['auto_start'] = config.get('auto_start', False)
    new_config['auto_start_telemetry'] = config.get('auto_start_telemetry', True)

    save_mqtt_config(new_config)
    handler.ctx.dialog.msgbox(
        "Private Broker Configured",
        f"Saved configuration:\n\n"
        f"  Broker:   {broker}:{port}\n"
        f"  Topic:    {topic}\n"
        f"  Channel:  {channel}\n"
        f"  Username: {username or '(anonymous)'}\n"
        f"  TLS:      {'Yes' if use_tls else 'No'}\n\n"
        f"Root topic '{root_topic}' determines node scope.\n"
        f"Restart MQTT subscriber to apply."
    )

def request_telemetry_menu(handler):
    """Request telemetry from silent Meshtastic 2.7+ nodes."""
    def _choices():
        items = [
            ("single", "Request from Node    Enter node ID manually"),
            ("silent", "Find Silent Nodes    Nodes with stale telemetry"),
            ("batch", "Poll Silent Nodes    Request from all silent"),
        ]
        if _HAS_TELEMETRY_POLLER:
            poller = get_telemetry_poller()
            stats = poller.get_stats()
            items.append(("stats", f"Poller Statistics    {stats.get('total_requests', 0)} requests"))
        return items

    dispatch = {
        "single": ("Request Single Telemetry", lambda: request_single_telemetry(handler)),
        "silent": ("Show Silent Nodes", lambda: show_silent_nodes(handler)),
        "batch": ("Batch Telemetry Request", lambda: batch_telemetry_request(handler)),
        "stats": ("Poller Statistics", lambda: show_poller_stats(handler)),
    }
    handler.run_menu_loop(
        "Telemetry Requests",
        "Request telemetry from silent Meshtastic 2.7+ nodes.\n\n"
        "These nodes don't broadcast telemetry by default to reduce\n"
        "mesh congestion. Use this to poll them explicitly.",
        _choices, dispatch,
    )

def request_single_telemetry(handler):
    """Request telemetry from a single node by ID."""
    node_id = handler.ctx.dialog.inputbox(
        "Request Telemetry",
        "Enter the Meshtastic node ID (e.g., !ba4bf9d0):",
        init="!"
    )

    if not node_id or node_id == "!":
        return

    if not node_id.startswith('!'):
        node_id = f"!{node_id}"

    handler.ctx.dialog.infobox("Requesting", f"Sending telemetry request to {node_id}...")

    if _HAS_TELEMETRY_POLLER:
        poller = get_telemetry_poller()
        success = poller.poll_node_now(node_id)

        if success:
            handler.ctx.dialog.msgbox(
                "Request Sent",
                f"Telemetry request sent to {node_id}.\n\n"
                "The node should respond within a few seconds.\n"
                "Check MQTT Nodes view for updated data."
            )
        else:
            handler.ctx.dialog.msgbox(
                "Request Failed",
                f"Failed to send telemetry request to {node_id}.\n\n"
                "Possible reasons:\n"
                "- meshtastic CLI not found\n"
                "- Rate limited (max 4 requests/minute)\n"
                "- meshtasticd not running"
            )
    else:
        fallback_telemetry_request(handler, node_id)

def fallback_telemetry_request(handler, node_id: str):
    """Fallback telemetry request using direct CLI call."""
    cli = shutil.which('meshtastic')
    if not cli:
        handler.ctx.dialog.msgbox(
            "CLI Not Found",
            "meshtastic CLI not found.\n\n"
            "Install it with: pipx install meshtastic"
        )
        return

    try:
        result = subprocess.run(
            [cli, '--host', 'localhost', '--request-telemetry', '--dest', node_id],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            handler.ctx.dialog.msgbox(
                "Request Sent",
                f"Telemetry request sent to {node_id}.\n\n"
                f"Output:\n{result.stdout[:500] if result.stdout else 'No output'}"
            )
        else:
            handler.ctx.dialog.msgbox(
                "Request Failed",
                f"Failed to request telemetry:\n{result.stderr[:500] if result.stderr else 'Unknown error'}"
            )
    except subprocess.TimeoutExpired:
        handler.ctx.dialog.msgbox("Timeout", "Telemetry request timed out after 30 seconds.")
    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Failed to request telemetry:\n{e}")

def show_silent_nodes(handler):
    """Show nodes with stale or missing telemetry."""
    if not handler._mqtt_subscriber:
        handler.ctx.dialog.msgbox(
            "MQTT Not Running",
            "Start the MQTT subscriber first to discover nodes."
        )
        return

    nodes = handler._mqtt_subscriber.get_nodes()
    if not nodes:
        handler.ctx.dialog.msgbox("No Nodes", "No nodes discovered yet.")
        return

    if not _HAS_TELEMETRY_POLLER:
        handler.ctx.dialog.msgbox("Module Not Found", "TelemetryPoller module not available.")
        return

    poller = TelemetryPoller()

    node_list = []
    for node in nodes:
        node_list.append({
            'id': node.node_id,
            'is_online': node.is_online(),
            'telemetry_timestamp': node.last_seen
        })

    silent = poller.identify_silent_nodes(node_list, telemetry_age_threshold=1800)

    if not silent:
        handler.ctx.dialog.msgbox(
            "No Silent Nodes",
            "All online nodes have recent telemetry.\n\nThreshold: 30 minutes"
        )
        return

    lines = ["SILENT NODES (>30 min without telemetry)", "=" * 50, ""]

    for node_id in silent[:20]:
        for node in nodes:
            if node.node_id == node_id:
                name = node.long_name or node.short_name or node_id
                age = node.get_age_string()
                lines.append(f"  {node_id}  {name[:15]:<15} ({age})")
                break
        else:
            lines.append(f"  {node_id}")

    if len(silent) > 20:
        lines.append(f"\n  ... and {len(silent) - 20} more")

    lines.append("")
    lines.append("Use 'Poll Silent Nodes' to request telemetry from all.")

    handler.ctx.dialog.msgbox("Silent Nodes", "\n".join(lines))

def batch_telemetry_request(handler):
    """Request telemetry from all silent nodes."""
    if not handler._mqtt_subscriber:
        handler.ctx.dialog.msgbox(
            "MQTT Not Running",
            "Start the MQTT subscriber first to discover nodes."
        )
        return

    if not handler.ctx.dialog.yesno(
        "Confirm Batch Request",
        "This will send telemetry requests to all silent nodes.\n\n"
        "Requests are rate-limited to 4/minute to avoid\n"
        "congesting the mesh.\n\nContinue?"
    ):
        return

    nodes = handler._mqtt_subscriber.get_nodes()
    if not nodes:
        handler.ctx.dialog.msgbox("No Nodes", "No nodes discovered yet.")
        return

    if not _HAS_TELEMETRY_POLLER:
        handler.ctx.dialog.msgbox("Module Not Found", "TelemetryPoller module not available.")
        return

    poller = get_telemetry_poller()

    node_list = []
    for node in nodes:
        node_list.append({
            'id': node.node_id,
            'is_online': node.is_online(),
            'telemetry_timestamp': node.last_seen
        })

    silent = poller.identify_silent_nodes(node_list, telemetry_age_threshold=1800)

    if not silent:
        handler.ctx.dialog.msgbox("No Silent Nodes", "No silent nodes to poll.")
        return

    handler.ctx.dialog.infobox("Polling", f"Sending requests to {min(5, len(silent))} nodes...")

    success_count = 0
    for node_id in silent[:5]:
        if poller.poll_node_now(node_id):
            success_count += 1
        time.sleep(0.5)

    handler.ctx.dialog.msgbox(
        "Batch Complete",
        f"Telemetry requests sent: {success_count}/{min(5, len(silent))}\n\n"
        f"Total silent nodes: {len(silent)}\n"
        f"Rate limit: 4 requests/minute\n\n"
        "Run again to poll more nodes."
    )

def show_poller_stats(handler):
    """Show telemetry poller statistics."""
    if not _HAS_TELEMETRY_POLLER:
        handler.ctx.dialog.msgbox("Module Not Found", "TelemetryPoller module not available.")
        return

    poller = get_telemetry_poller()
    stats = poller.get_stats()

    lines = [
        "TELEMETRY POLLER STATISTICS",
        "=" * 40,
        "",
        f"Total requests:      {stats.get('total_requests', 0)}",
        f"Successful:          {stats.get('successful_requests', 0)}",
        f"Failed:              {stats.get('failed_requests', 0)}",
        f"Rate limited:        {stats.get('rate_limited', 0)}",
        "",
        f"Nodes polled:        {stats.get('nodes_polled', 0)}",
    ]

    if stats.get('last_poll_cycle'):
        lines.append(f"Last poll cycle:     {stats['last_poll_cycle']}")

    handler.ctx.dialog.msgbox("Poller Statistics", "\n".join(lines))
