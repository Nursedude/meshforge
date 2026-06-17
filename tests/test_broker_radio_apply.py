"""In-app radio MQTT apply (MF018 Class 5 privilege-relaunch audit).

broker's "run these commands to configure your radio" shell instruction is now
an in-app apply via the meshtastic CLI, built from the broker_profiles SSOT
(get_meshtastic_mqtt_setup_argv) — so the executed commands and the displayed
ones can never drift, and a failed device write is reported honestly (never a
silent "configured").
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))

from utils.broker_profiles import (  # noqa: E402
    get_meshtastic_mqtt_setup_argv,
    host_needs_lan_ip,
    get_meshtastic_mqtt_setup_commands,
    create_private_profile,
    create_custom_profile,
)


class TestSetupArgvSSOT:
    def test_local_broker_needs_lan_ip(self):
        assert host_needs_lan_ip(create_private_profile()) is True

    def test_remote_broker_no_lan_ip(self):
        p = create_custom_profile(name="r", host="mqtt.example.net", port=8883)
        assert host_needs_lan_ip(p) is False

    def test_argv_uses_host_override(self):
        argv = get_meshtastic_mqtt_setup_argv(create_private_profile(),
                                              host="192.0.2.5")
        flat = " ".join(argv[0])
        assert argv[0][0] == "meshtastic"
        assert "mqtt.address 192.0.2.5" in flat
        assert "mqtt.enabled" in flat
        assert any("uplink_enabled" in " ".join(c) for c in argv)
        assert any("downlink_enabled" in " ".join(c) for c in argv)

    def test_argv_includes_credentials_when_set(self):
        p = create_private_profile(username="u", password="pw")
        flat = " ".join(get_meshtastic_mqtt_setup_argv(p, host="h")[0])
        assert "mqtt.username u" in flat
        assert "mqtt.password pw" in flat

    def test_display_string_matches_argv_settings(self):
        # SSOT parity: every mqtt.* / uplink / downlink token in the argv must
        # appear in the displayed command block — they can't drift.
        p = create_custom_profile(name="r", host="mqtt.example.net", port=8883,
                                  username="n", password="pw", use_tls=True)
        display = get_meshtastic_mqtt_setup_commands(p)
        for cmd in get_meshtastic_mqtt_setup_argv(p):
            for tok in cmd:
                if tok.startswith("mqtt.") or tok in (
                        "uplink_enabled", "downlink_enabled"):
                    assert tok in display, f"{tok} missing from display"


class TestApplyMqttToRadio:
    def _handler(self):
        from handlers.broker import BrokerHandler
        h = BrokerHandler()
        h.ctx = MagicMock()
        return h

    def test_local_broker_cancelled_lan_ip_aborts(self):
        h = self._handler()
        h.ctx.dialog.inputbox.return_value = None  # operator cancelled the IP
        h._apply_mqtt_to_radio(create_private_profile())
        h.ctx.get_meshtastic_cli.assert_not_called()  # aborted before apply

    def test_cli_missing_reports_and_offers_nothing(self):
        h = self._handler()
        h.ctx.get_meshtastic_cli.return_value = None
        p = create_custom_profile(name="r", host="mqtt.example.net", port=8883)
        with patch("remediation.propose_remediation") as pr:
            h._apply_mqtt_to_radio(p)
            pr.assert_not_called()
        assert h.ctx.dialog.msgbox.called  # honest "CLI not available" message

    def test_apply_runs_cli_with_resolved_path_and_reports_ok(self):
        h = self._handler()
        h.ctx.get_meshtastic_cli.return_value = "/usr/bin/meshtastic"
        p = create_custom_profile(name="r", host="mqtt.example.net", port=8883)
        captured = {}
        with patch("remediation.propose_remediation",
                   side_effect=lambda c, t, f, a: captured.update(action=a[0])):
            h._apply_mqtt_to_radio(p)
        with patch("subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="", stderr="")) as sr:
            ok, msg = captured["action"].apply()
        assert ok is True
        # cmd[0] 'meshtastic' replaced by the resolved CLI path
        assert sr.call_args_list[0][0][0][0] == "/usr/bin/meshtastic"
        # ran one invocation per argv command (set + channel)
        assert sr.call_count == 2

    def test_apply_reports_failure_on_nonzero_exit(self):
        h = self._handler()
        h.ctx.get_meshtastic_cli.return_value = "/usr/bin/meshtastic"
        p = create_custom_profile(name="r", host="mqtt.example.net", port=8883)
        captured = {}
        with patch("remediation.propose_remediation",
                   side_effect=lambda c, t, f, a: captured.update(action=a[0])):
            h._apply_mqtt_to_radio(p)
        with patch("subprocess.run",
                   return_value=MagicMock(returncode=1, stdout="", stderr="radio busy")):
            ok, msg = captured["action"].apply()
        assert ok is False
        assert "radio busy" in msg or "rejected" in msg.lower()
