"""TUI audit finding 6 (2026-09-27): actions that looked live but were dead.

Group A — fixed: ham called five methods/signatures that do not exist;
mesh_alerts never recorded its feed; the sniffer said "active" in a process
that can see nothing; the cleanup scan blamed meshtasticd for an endpoint it
never serves; QA text pointed at a menu and a writer that do not exist.
Group B — kept but honest: failover / load balancer / demo alerts.
Every test redirects user-home writes to tmp (no live operator data).
"""

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from handler_test_utils import make_handler_context


@pytest.fixture
def home(tmp_path):
    with patch("amateur.ares_races.get_real_user_home", return_value=tmp_path), \
         patch("amateur.callsign.get_real_user_home", return_value=tmp_path):
        yield tmp_path


def _ham():
    from handlers.amateur_radio import AmateurRadioHandler
    h = AmateurRadioHandler.__new__(AmateurRadioHandler)
    h.ctx = make_handler_context()
    h.ctx.wait_for_enter = lambda *a, **k: None
    return h


def _no_clear():
    return patch("handlers.amateur_radio.clear_screen", lambda: None)


class TestHam:
    def test_callsign_lookup_uses_the_real_method(self, home, capsys):
        from amateur.callsign import CallsignInfo, CallsignManager
        info = CallsignInfo(callsign="WH6GXZ", name="Test Op", license_class="General")
        h = _ham()
        h.ctx.dialog._inputbox_returns = ["wh6gxz"]
        with _no_clear(), patch.object(CallsignManager, "lookup_callsign",
                                       return_value=info) as look:
            h._callsign_lookup()
        look.assert_called_once()
        out = capsys.readouterr().out
        assert "Test Op" in out and "Lookup failed" not in out

    def test_band_plan_lists_lora_relevant_bands(self, capsys):
        h = _ham()
        with _no_clear():
            h._band_plan_display()
        out = capsys.readouterr().out
        assert "Error loading band plan" not in out
        assert "902" in out or "420" in out          # 33 cm / 70 cm shown

    def test_compliance_uses_the_real_signature_and_dict(self, capsys):
        h = _ham()
        h.ctx.get_meshtastic_cli = lambda: "meshtastic"
        lora = SimpleNamespace(returncode=0, stdout="frequency: 906.875\ntx_power: 22\n")
        with _no_clear(), patch("handlers.amateur_radio.subprocess.run", return_value=lora):
            h._compliance_check()
        out = capsys.readouterr().out
        assert "Check failed" not in out
        assert "AUTHORIZED" in out                   # authorized or NOT AUTHORIZED
        assert "NOT checked" in out                  # power is honestly not checked

    def test_ics213_saves_and_proves_it(self, home, capsys):
        h = _ham()
        h.ctx.dialog._inputbox_returns = ["EOC", "WH6GXZ", "Test", "body text"]
        h.ctx.dialog._menu_returns = ["P"]
        with _no_clear():
            h._ics213_compose()
        out = capsys.readouterr().out
        assert "Saved as message" in out and "NOT SAVED" not in out
        logs = list((home / ".config" / "meshforge" / "ares_races").glob("traffic_log_*.json"))
        assert logs and json.loads(logs[0].read_text())[0]["subject"] == "Test"

    def test_ics213_failed_write_is_not_called_saved(self, home, capsys):
        h = _ham()
        h.ctx.dialog._inputbox_returns = ["EOC", "WH6GXZ", "Test", "body text"]
        h.ctx.dialog._menu_returns = ["R"]
        # planted fault: log_message swallows the write error internally
        with _no_clear(), patch("amateur.ares_races.ARESRACESTools.log_message",
                                lambda self, m: None):
            h._ics213_compose()
        out = capsys.readouterr().out
        assert "NOT SAVED" in out and "Saved as message" not in out

    def test_net_status_shows_saved_data_honestly(self, home, capsys):
        h = _ham()
        with _no_clear():
            h._ares_net_status()
        out = capsys.readouterr().out
        assert "Status unavailable" not in out
        assert "No live net-session tracking" in out


class TestMeshAlerts:
    def test_attach_records_the_feed(self):
        from utils.mesh_alert_engine import MeshAlertEngine
        e = MeshAlertEngine.__new__(MeshAlertEngine)
        e._subscriber = None
        sub = MagicMock()
        e.attach_subscriber(sub)
        assert e.has_feed() is True


class TestSniffer:
    def test_capture_dialog_is_gated_on_a_reticulum_measurement(self):
        import inspect
        from handlers import rns_sniffer as mod
        src = inspect.getsource(mod)
        armed = src[src.index("if sniffer_mod.start_rns_capture():"):]
        armed = armed[:armed.index('"Capture Started (No RNS)"')]
        assert "if _tui_has_reticulum():" in armed
        assert "NOT Seeing Traffic" in armed

    def test_reticulum_probe_is_a_real_measurement(self):
        from handlers.rns_sniffer import _tui_has_reticulum
        fake_rns = SimpleNamespace(Reticulum=SimpleNamespace(get_instance=lambda: None))
        with patch.dict(sys.modules, {"RNS": fake_rns}):
            assert _tui_has_reticulum() is False
        fake_rns.Reticulum.get_instance = lambda: object()
        with patch.dict(sys.modules, {"RNS": fake_rns}):
            assert _tui_has_reticulum() is True


class TestCleanupScan:
    def test_scan_does_not_blame_a_running_meshtasticd(self):
        from handlers import meshtasticd_nodedb as mod
        h = mod.MeshtasticdNodeDBHandler.__new__(mod.MeshtasticdNodeDBHandler) \
            if hasattr(mod, "MeshtasticdNodeDBHandler") else None
        cls = next(v for k, v in vars(mod).items()
                   if isinstance(v, type) and hasattr(v, "_scan_phantom_nodes"))
        h = cls.__new__(cls)
        h.ctx = make_handler_context()
        client = SimpleNamespace(is_available=False)
        with patch.object(mod, "_get_http_client", return_value=client), \
             patch("utils.service_check.check_service",
                   return_value=SimpleNamespace(available=True)), \
             patch("service_remediation.offer_service_fix") as fix:
            h._scan_phantom_nodes()
        fix.assert_not_called()                      # no restart offer
        texts = " ".join(c[1][1] for c in h.ctx.dialog.calls if c[0] == "msgbox")
        assert "never serves" in texts and "meshtasticd service: running" in texts


class TestGroupBHonest:
    def test_failover_texts_never_blame_a_stopped_bridge(self):
        import inspect
        from handlers import dual_radio_failover as mod
        src = inspect.getsource(mod)
        assert "bridge not running" not in src
        assert "Start the gateway bridge to see events" not in src
        assert "NOT OBSERVABLE" in mod.FAILOVER_NOT_HERE

    def test_load_balancer_texts_are_honest(self):
        import inspect
        from handlers import load_balancer as mod
        src = inspect.getsource(mod)
        assert '"Load balancer not active."' not in src
        assert "load_balancer_enabled: true" not in src   # would not take effect (6C)
        assert "NOT OBSERVABLE" in mod.LB_NOT_HERE

    def test_demo_no_longer_promises_alerts(self):
        import inspect
        from handlers import demo
        src = inspect.getsource(demo)
        assert "Dashboard > View Alerts" not in src

    def test_qa_hints_point_at_real_things(self):
        import inspect
        from handlers import quick_actions
        src = inspect.getsource(quick_actions)
        # the PRINTED strings, not prose in comments
        assert 'print("  Tip: Set position manually in Tools > GPS")' not in src
        assert 'print("  Nodes are added when received via MQTT or meshtastic CLI.")' not in src
