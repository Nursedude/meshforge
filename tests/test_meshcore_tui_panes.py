"""MeshForge TUI MeshCore panes ported from MeshAnchor (roadmap 1e, 2026-09-22):
contacts pane (1b), firmware brief (1c), oracle posture line (1d) — all fed
by the gateway's own :9090 status seam rather than an in-process handle
that is always empty from a separate TUI process.

Ported tests keep MeshAnchor's pins (the two look-alike receipt clocks,
null-island, unobservable ≠ never) and add the seam-specific ones: an
unreachable gateway is UNKNOWN on every surface, never "off"/"zero".
"""

import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
from handlers.meshcore import MeshCoreHandler  # noqa: E402


def _row(**over):
    row = {
        "name": "meshforge p4",
        "public_key": "7eb0fa289c11" + "0" * 52,
        "prefix": "7eb0fa289c11",
        "type": 1,
        "role": "companion",
        "last_advert": 2100000000,      # year 2036 — sender's clock, absurd
        "last_advert_iso": "2036-08-22T01:20:00",
        "lastmod": 1789336369,          # 2026-09-13 — record-modified cursor
        "lastmod_iso": "2026-09-13T11:52:49",
        "out_path_len": -1,
        "adv_lat": 0.0,
        "adv_lon": 0.0,
        "flags": 0,
    }
    row.update(over)
    return row


def _http(payload):
    """A urlopen stand-in returning ``payload`` as JSON."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *a: False
    return patch("urllib.request.urlopen", return_value=resp)


@pytest.fixture
def handler():
    h = MeshCoreHandler()
    h.set_context(make_handler_context())
    h.ctx.wait_for_enter = MagicMock()
    return h


# ── 1b: contacts pane ───────────────────────────────────────────────────

class TestFieldRendering:
    @pytest.mark.parametrize("raw,expected", [
        (-1, "flood"), (0, "direct"), (1, "1 hop"), (3, "3 hops"), (None, "?"),
    ])
    def test_path_rendering(self, handler, raw, expected):
        assert handler._contacts_path(raw) == expected

    def test_null_island_is_absence_not_a_coordinate(self, handler):
        assert handler._contacts_position(0.0, 0.0) == "-"
        assert handler._contacts_position(None, None) == "-"
        assert handler._contacts_position(20.87, -156.47) == "20.8700,-156.4700"

    def test_age_never_renders_a_negative_duration(self, handler):
        assert handler._contacts_age(datetime.now() + timedelta(hours=2)) == "clock skew"
        assert handler._contacts_age(None) == "?"


class TestReceiptJoin:
    def test_matches_in_both_directions(self, handler):
        known, seen = handler._contacts_lookup(
            {"7eb0fa289c11aabb": datetime(2026, 9, 21, 18, 0)}, "7eb0fa289c11")
        assert known and seen is not None
        known, _ = handler._contacts_lookup({"7eb0fa": datetime(2026, 9, 21)}, "7eb0fa289c11")
        assert known

    def test_absent_prefix_does_not_match(self, handler):
        known, seen = handler._contacts_lookup({"deadbeefcafe": datetime.now()}, "7eb0fa289c11")
        assert not known and seen is None


class TestFetchDegradesHonestly:
    def test_unreachable_returns_error_not_empty_list(self, handler):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            rows, err = handler._contacts_fetch()
        assert rows is None and "refused" in err

    def test_gateway_unobserved_is_its_reason_not_an_empty_table(self, handler):
        """The gateway answered ``observed: false`` (radio not connected).
        Rendering its empty list would say "the radio knows nobody"."""
        with _http({"observed": False, "count": 0, "contacts": [],
                    "reason": "meshcore not connected"}):
            rows, err = handler._contacts_fetch()
        assert rows is None and "not connected" in err

    def test_malformed_payload_is_an_error_not_silence(self, handler):
        with _http({"ts": 1}):
            rows, err = handler._contacts_fetch()
        assert rows is None and "contacts" in err

    def test_a_404_is_an_older_gateway_not_unreachable(self, handler):
        """The live fleet's gateways answer on :9090 today but predate this
        route; 'unreachable' would send the operator to the network."""
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            rows, reason = handler._contacts_fetch()
            payload, reason2 = handler._status_fetch()
        assert rows is None and payload is None
        for r in (reason, reason2):
            assert "predates" in r and "404" in r and "unreachable" not in r
            assert "Service Control -> meshforge-gateway -> Restart" in r

    def test_fetch_targets_the_gateways_own_listener(self, handler):
        with patch("urllib.request.urlopen", side_effect=OSError("x")) as uo:
            handler._contacts_fetch()
        assert uo.call_args[0][0].full_url == "http://127.0.0.1:9090/api/json/meshcore/contacts"


class TestLastHeardSource:
    def _render(self, handler, rows, receipts, capsys, declared=None):
        with patch.object(type(handler), "_contacts_fetch", return_value=(rows, None)), \
             patch.object(type(handler), "_contacts_receipts", return_value=receipts), \
             patch.object(type(handler), "_contacts_declared", return_value=declared or []):
            handler._meshcore_contacts()
        return capsys.readouterr().out

    def test_never_borrows_the_senders_clock_or_the_sync_cursor(self, handler, capsys):
        out = self._render(handler, [_row()], {}, capsys)
        assert "not since boot" in out
        assert "2036" not in out
        assert "09-13" not in out and "2026-09-13" not in out

    def test_uses_our_receipt_when_we_have_one(self, handler, capsys):
        seen = datetime.now() - timedelta(minutes=5)
        assert "5m ago" in self._render(handler, [_row()], {"7eb0fa289c11": seen}, capsys)

    def test_heard_but_unstamped_is_not_never(self, handler, capsys):
        out = self._render(handler, [_row()], {"7eb0fa289c11": None}, capsys)
        assert "heard, no ts" in out and "not since boot" not in out

    def test_unreadable_tracker_is_unknown_not_never(self, handler, capsys):
        out = self._render(handler, [_row()], None, capsys)
        assert "unknown" in out and "not since boot" not in out

    def test_unreachable_gateway_names_the_unit_to_start(self, handler, capsys):
        with patch.object(type(handler), "_contacts_fetch", return_value=(None, "refused")):
            handler._meshcore_contacts()
        out = capsys.readouterr().out
        assert "Could not read the contact table" in out
        assert "Service Control -> meshforge-gateway" in out   # in-app path, not a shell line (MF018)


class TestDeclaredOwnership:
    def test_declared_absence_is_a_row_not_silence(self, handler, capsys):
        with patch.object(type(handler), "_contacts_fetch", return_value=([_row()], None)), \
             patch.object(type(handler), "_contacts_receipts", return_value={}), \
             patch.object(type(handler), "_contacts_declared",
                          return_value=["meshforge p4", "meshforge p2"]):
            handler._meshcore_contacts()
        out = capsys.readouterr().out
        assert "ABSENT" in out and "meshforge p2" in out

    def test_undeclared_says_so(self, handler, capsys):
        with patch.object(type(handler), "_contacts_fetch", return_value=([_row()], None)), \
             patch.object(type(handler), "_contacts_receipts", return_value={}), \
             patch.object(type(handler), "_contacts_declared", return_value=[]):
            handler._meshcore_contacts()
        assert "No contacts declared as ours" in capsys.readouterr().out

    def test_matches_on_name_or_pubkey_prefix(self, handler):
        row = _row()
        assert handler._contacts_is_ours(["meshforge p4"], row)
        assert handler._contacts_is_ours(["7eb0fa"], row)
        assert not handler._contacts_is_ours(["somebody else"], row)

    def test_our_contacts_is_a_real_config_field(self):
        """GatewayConfig.load does MeshCoreConfig(**data): a key that is not
        a field fails the WHOLE config load, so the pane's knob must exist."""
        from gateway.config import MeshCoreConfig
        assert MeshCoreConfig(our_contacts=["p4"]).our_contacts == ["p4"]
        assert MeshCoreConfig().our_contacts == []


# ── 1c: firmware brief ──────────────────────────────────────────────────

class TestFirmwareBrief:
    def test_unreachable_is_unknown_not_no_firmware(self, handler):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            assert "firmware ?" in handler._meshcore_fw_brief()
            assert "unreachable" in handler._meshcore_fw_brief()

    def test_says_build_and_proto_never_version(self, handler):
        with _http({"observable": True, "device": {
                "observed": True, "model": "RAK4631", "fw_build": "19-Apr-2026",
                "fw_ver": 11, "source": "radio"}}):
            brief = handler._meshcore_fw_brief()
        assert brief == "RAK4631 build 19-Apr-2026 proto v11"
        assert "version" not in brief.lower() and "v11" in brief

    def test_simulator_is_marked(self, handler):
        with _http({"observable": True, "device": {
                "observed": True, "model": "MeshCoreSimulator", "fw_build": "sim",
                "fw_ver": None, "source": "simulator"}}):
            assert handler._meshcore_fw_brief().startswith("[SIM]")

    def test_brief_is_cached_so_the_menu_does_not_poll_per_keystroke(self, handler):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")) as uo:
            handler._meshcore_fw_brief()
            handler._meshcore_fw_brief()
        assert uo.call_count == 1

    def test_subtitle_carries_the_brief_when_enabled(self, handler):
        cfg = MagicMock()
        cfg.meshcore.enabled = True
        cfg.meshcore.connection_type = "serial"
        cfg.meshcore.device_path = "/dev/ttyUSB1"
        with patch("handlers.meshcore._GatewayConfig") as gc, \
             patch.object(type(handler), "_meshcore_fw_brief", return_value="RAK build X proto v11"):
            gc.load.return_value = cfg
            line = handler._meshcore_status_line()
        assert line.endswith("| RAK build X proto v11")


# ── 1d: oracle posture line + the stats pane that carries it ────────────

class TestPostureLine:
    def test_off_says_off_and_why(self, handler):
        line = handler._oracle_posture_line({"observable": True, "enabled": False})
        assert "OFF" in line and "MESHFORGE_ORACLE_ENABLED" in line

    def test_build_failure_is_loud_and_never_reads_as_off(self, handler):
        line = handler._oracle_posture_line(
            {"observable": True, "enabled": False, "error": "RuntimeError: boom"})
        assert "BUILD FAILED" in line and "boom" in line and "OFF (" not in line

    def test_unobservable_never_reads_as_off(self, handler):
        line = handler._oracle_posture_line({"observable": False, "reason": "no meshcore handler"})
        assert "UNKNOWN" in line and "OFF (" not in line

    def test_missing_key_from_an_older_gateway_is_unknown(self, handler):
        line = handler._oracle_posture_line(None)
        assert "UNKNOWN" in line and "OFF (" not in line

    def test_enabled_matches_the_gateways_own_vocabulary(self, handler):
        line = handler._oracle_posture_line({
            "observable": True, "enabled": True, "answer_all": False,
            "allowlist": 1, "channels": ["1"], "cooldown_s": 10.0,
            "consume": False, "transport": "meshcore"})
        for token in ("answer_all=False", "allowlist=1", "channels=1",
                      "cooldown=10s", "consume=False"):
            assert token in line, f"{token!r} missing from {line!r}"

    def test_unreadable_fields_are_named(self, handler):
        line = handler._oracle_posture_line({
            "observable": True, "enabled": True, "unreadable": ["allowlist"]})
        assert "unreadable: allowlist" in line


class TestStatsPaneReadsTheGateway:
    def test_unreachable_gateway_is_unknown_not_zero(self, handler, capsys):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")), \
             patch("handlers.meshcore._is_gateway_running", return_value=False):
            handler._meshcore_stats()
        out = capsys.readouterr().out
        assert "UNKNOWN" in out and "unreachable" in out
        assert "Messages RX" not in out          # no counters rendered as zero

    def test_live_gateway_renders_oracle_line_and_counters(self, handler, capsys):
        with _http({"observable": True, "running": True, "connected": True,
                    "oracle": {"observable": True, "enabled": True, "answer_all": False,
                               "allowlist": 2, "channels": [], "cooldown_s": 30.0,
                               "consume": True},
                    "counters": {"observable": True, "uptime_seconds": 3700,
                                 "stats": {"meshcore_rx": 7, "meshcore_tx": 2,
                                           "meshcore_acks": 1}}}):
            handler._meshcore_stats()
        out = capsys.readouterr().out
        assert "CONNECTED" in out and "Running" in out
        assert "Oracle:      ON  answer_all=False allowlist=2 channels=- cooldown=30s consume=True" in out
        assert "Messages RX:    7" in out
        assert "Uptime: 1h 1m 40s" in out

    def test_gateway_with_no_bridge_says_so(self, handler, capsys):
        with _http({"observable": False, "reason": "no gateway bridge registered in this process"}):
            handler._meshcore_stats()
        assert "runs no bridge" in capsys.readouterr().out

    def test_in_process_fallback_only_when_this_process_is_the_gateway(self, handler, capsys):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")), \
             patch("handlers.meshcore._is_gateway_running", return_value=True), \
             patch("handlers.meshcore._get_gateway_stats", return_value={
                 "running": True, "meshcore_connected": False,
                 "statistics": {"meshcore_rx": 1}, "uptime_seconds": 5}):
            handler._meshcore_stats()
        out = capsys.readouterr().out
        assert "DISCONNECTED" in out and "Messages RX:    1" in out
        assert "UNKNOWN (gateway did not report a posture" in out
