"""
Test Gateway RX — synthetic MQTT probe + end-to-end bridge verification.

Publishes a UUID-tagged probe to the same MQTT topic shape that the gateway
subscribes to, then watches the gateway log and the NomadNet conversation
directory for the UUID. Reports PASS (end-to-end), PARTIAL (gateway parsed
but NomadNet did not store), or FAIL (probe never reached the gateway).

Operators use this to answer "is my bridge actually working?" without having
to cook up `mosquitto_pub` commands by hand. See Issue #35 in
persistent_issues.md for why messages land under the gateway's own LXMF hash
rather than the receiver's.
"""

import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path

from handler_protocol import BaseHandler
from utils.paths import get_real_user_home
from utils.service_check import check_service, check_rns_shared_instance

logger = logging.getLogger(__name__)

try:
    from gateway.config import GatewayConfig as _GatewayConfig
    _HAS_GATEWAY_CONFIG = True
except ImportError:
    _GatewayConfig = None
    _HAS_GATEWAY_CONFIG = False

_WATCH_SECONDS = 10
_POLL_INTERVAL = 0.5


class TestGatewayRxHandler(BaseHandler):
    """TUI handler: inject an MQTT probe and verify end-to-end delivery."""

    handler_id = "test_gateway_rx"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("test_gateway_rx", "Test Gateway RX     MQTT probe -> RNS -> NomadNet", "gateway"),
        ]

    def execute(self, action):
        if action == "test_gateway_rx":
            self.ctx.safe_call("Test Gateway RX", self._run_rx_test)

    def _run_rx_test(self):
        if not _HAS_GATEWAY_CONFIG:
            self.ctx.dialog.msgbox(
                "Gateway Module Missing",
                "Gateway configuration module not found.\n\n"
                "Expected src/gateway/config.py.",
            )
            return

        mosq = check_service('mosquitto')
        if not mosq.available:
            self.ctx.dialog.msgbox(
                "Mosquitto Not Available",
                f"mosquitto broker is not running.\n\n{mosq.fix_hint or ''}",
            )
            return

        try:
            config = _GatewayConfig.load()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Config Load Error",
                f"Could not load gateway configuration:\n\n{type(e).__name__}: {e}",
            )
            return

        if not config.enabled:
            if not self.ctx.dialog.yesno(
                "Gateway Disabled",
                "Gateway is currently disabled in config.\n\n"
                "The probe can still reach mosquitto, but nothing will bridge it.\n"
                "Continue anyway?",
            ):
                return

        if config.bridge_mode != "mqtt_bridge":
            if not self.ctx.dialog.yesno(
                "Non-MQTT Bridge Mode",
                f"Gateway bridge_mode is '{config.bridge_mode}', not 'mqtt_bridge'.\n\n"
                "This probe targets the MQTT subscription path. In other modes\n"
                "the probe will be ignored. Continue anyway?",
            ):
                return

        rns_dest = (config.rns.default_lxmf_destination or "").strip()
        if not rns_dest:
            self.ctx.dialog.msgbox(
                "Missing LXMF Destination",
                "rns.default_lxmf_destination is empty.\n\n"
                "Set it to your NomadNet identity hash in gateway.json before\n"
                "testing; broadcasts need a default LXMF peer to route to.",
            )
            return

        rns = check_rns_shared_instance()
        if not rns:
            if not self.ctx.dialog.yesno(
                "rnsd Not Reachable",
                "check_rns_shared_instance() reports rnsd is not running or its\n"
                "shared instance is unreachable. The gateway can still log the\n"
                "probe but LXMF delivery will fail.\n\nContinue anyway?",
            ):
                return

        probe_uuid = uuid.uuid4().hex[:12]
        probe_text = f"meshforge-rx-probe-{probe_uuid}"
        topic = self._build_topic(config)
        payload = self._build_payload(probe_text, config)

        ok, stderr = self._publish_probe(config, topic, payload)
        if not ok:
            self.ctx.dialog.msgbox(
                "mosquitto_pub Failed",
                f"Could not publish probe to {topic}.\n\n"
                f"stderr:\n{stderr or '(none)'}\n\n"
                "Check MQTT broker reachability and credentials.",
            )
            return

        start = time.monotonic()
        deadline = start + _WATCH_SECONDS
        log_hit = False
        conv_hit = False
        conv_path = None
        home = get_real_user_home()
        log_dir = home / ".config" / "meshforge" / "logs"
        nomadnet_dir = home / ".nomadnetwork" / "storage" / "conversations"

        tick = threading.Event()
        while time.monotonic() < deadline:
            if not log_hit and self._probe_in_logs(log_dir, probe_text):
                log_hit = True
            if not conv_hit:
                conv_path = self._probe_in_conversations(nomadnet_dir, probe_text)
                conv_hit = conv_path is not None
            if log_hit and conv_hit:
                break
            tick.wait(_POLL_INTERVAL)

        elapsed = time.monotonic() - start
        self._render_result(
            log_hit=log_hit,
            conv_hit=conv_hit,
            conv_path=conv_path,
            topic=topic,
            probe_text=probe_text,
            elapsed=elapsed,
        )

    @staticmethod
    def _build_topic(config) -> str:
        """Build the publish topic matching `mqtt_bridge_handler._on_connect`."""
        root = config.mqtt_bridge.root_topic.strip() or "msh"
        region = config.mqtt_bridge.region.strip()
        channel = config.mqtt_bridge.channel.strip() or "LongFast"
        synthetic_node = "!feedface"
        if region:
            return f"{root}/{region}/2/json/{channel}/{synthetic_node}"
        return f"{root}/2/json/{channel}/{synthetic_node}"

    @staticmethod
    def _build_payload(text: str, config) -> str:
        """Build the JSON payload the bridge's `_handle_json_message` expects."""
        channel_idx = getattr(config.meshtastic, "channel", 2)
        try:
            channel_idx = int(channel_idx)
        except (TypeError, ValueError):
            channel_idx = 2
        escaped = text.replace('\\', '\\\\').replace('"', '\\"')
        return (
            '{'
            f'"payload":{{"text":"{escaped}"}},'
            '"sender":"!feedface",'
            '"type":"text",'
            f'"channel":{channel_idx},'
            '"to":4294967295,'
            '"from":4277009102,'
            f'"id":{int(time.time()) & 0x7fffffff}'
            '}'
        )

    @staticmethod
    def _publish_probe(config, topic: str, payload: str):
        cmd = [
            "mosquitto_pub",
            "-h", config.mqtt_bridge.broker or "localhost",
            "-p", str(config.mqtt_bridge.port or 1883),
            "-t", topic,
            "-m", payload,
        ]
        if config.mqtt_bridge.username:
            cmd += ["-u", config.mqtt_bridge.username]
            if config.mqtt_bridge.password:
                cmd += ["-P", config.mqtt_bridge.password]
        if config.mqtt_bridge.use_tls:
            cmd += ["--capath", "/etc/ssl/certs"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )
        except FileNotFoundError:
            return False, "mosquitto_pub not installed (apt install mosquitto-clients)"
        except subprocess.TimeoutExpired:
            return False, "mosquitto_pub timed out after 10s"
        if result.returncode != 0:
            return False, (result.stderr or "").strip()
        return True, ""

    @staticmethod
    def _probe_in_logs(log_dir: Path, needle: str) -> bool:
        if not log_dir.is_dir():
            return False
        logs = sorted(log_dir.glob("meshforge_*.log"), reverse=True)[:2]
        for log_path in logs:
            try:
                with log_path.open("r", errors="replace") as fh:
                    for line in fh:
                        if needle in line:
                            return True
            except OSError as e:
                logger.debug("RX probe: could not read %s: %s", log_path, e)
        return False

    @staticmethod
    def _probe_in_conversations(conv_dir: Path, needle: str):
        if not conv_dir.is_dir():
            return None
        needle_bytes = needle.encode("utf-8")
        cutoff = time.time() - 60
        for child in conv_dir.iterdir():
            if not child.is_dir():
                continue
            try:
                for f in child.iterdir():
                    if not f.is_file():
                        continue
                    try:
                        if f.stat().st_mtime < cutoff:
                            continue
                        with f.open("rb") as fh:
                            if needle_bytes in fh.read():
                                return f
                    except OSError:
                        continue
            except OSError:
                continue
        return None

    def _render_result(self, log_hit, conv_hit, conv_path, topic, probe_text, elapsed):
        header = f"Probe UUID: {probe_text.rsplit('-', 1)[-1]}"
        timing = f"Watched for {elapsed:.1f}s"

        if log_hit and conv_hit:
            body = [
                "RESULT: PASS",
                "",
                "Full round-trip succeeded:",
                "  [x] Probe published to MQTT",
                "  [x] Gateway parsed it (matched in gateway log)",
                "  [x] NomadNet stored it (found in conversation file)",
                "",
                f"Conversation: {conv_path.parent.name[:16]}..." if conv_path else "",
                f"File:         {conv_path.name[:20]}..." if conv_path else "",
                "",
                header,
                timing,
            ]
        elif log_hit:
            body = [
                "RESULT: PARTIAL",
                "",
                "Gateway parsed the probe, but NomadNet did not store it.",
                "",
                "  [x] MQTT publish OK",
                "  [x] Gateway log hit",
                "  [ ] NomadNet conversation (no fresh file matching UUID)",
                "",
                "Likely causes:",
                "  - rnsd is not running or not reachable",
                "  - NomadNet is not running (LXMF has no recipient)",
                "  - default_lxmf_destination does not match NomadNet's identity",
                "  - lxmf library not installed in gateway's Python interpreter",
                "",
                "Next: rnstatus | head -30; check NomadNet process and identity.",
                "",
                header,
                timing,
            ]
        else:
            body = [
                "RESULT: FAIL",
                "",
                "The probe never reached the gateway.",
                "",
                "  [x] MQTT publish (mosquitto_pub returned 0)",
                "  [ ] Gateway log hit",
                "",
                f"Topic used: {topic}",
                "",
                "Likely causes:",
                "  - Gateway process is not running (ps -ef | grep gateway)",
                "  - Gateway is subscribed to a different channel/region",
                "  - mqtt_bridge config mismatch (check MQTT Bridge Settings)",
                "  - Log file rotated or not under ~/.config/meshforge/logs/",
                "",
                header,
                timing,
            ]

        body = [ln for ln in body if ln is not None]
        self.ctx.dialog.msgbox(
            "Test Gateway RX", "\n".join(body), width=70, height=25
        )
