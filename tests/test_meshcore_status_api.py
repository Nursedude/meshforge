"""The gateway's MeshCore status seam — GET /api/json/meshcore (roadmap 1e).

Born 2026-09-22: MeshForge's live gateway shape (bridge_cli.py) exposed
NOTHING about its MeshCore handler to a separate process, so the TUI could
not render the contact table, the firmware fact or the oracle posture. This
seam rides the gateway's existing :9090 listener. The pins here are the
honesty ones: unobservable never reads as off/empty, a build failure never
reads as disabled, and every posture field is read off the REAL responder
object — the twin shipped allowlist=0 by reading constructor kwarg names.
"""

import io
import json
import os
import sys
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.meshcore_handler import MeshCoreHandler, MeshCoreSimulator  # noqa: E402
from gateway import gateway_cli  # noqa: E402
from utils import meshcore_status_api as api  # noqa: E402
from utils.prometheus_exporter import MetricsHTTPHandler  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────

def _cfg(**over):
    mc = dict(enabled=True, device_path='/dev/ttyUSB1', baud_rate=115200,
              connection_type='serial', tcp_host='localhost', tcp_port=4000,
              auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
              simulation_mode=True, channel_poll_interval_sec=5,
              bridge_source_channels=None)
    mc.update(over)
    return SimpleNamespace(meshcore=SimpleNamespace(**mc),
                           meshtastic=SimpleNamespace(host='localhost', port=4403))


def _daemon_handler(build_side_effect=None, **cfg_over):
    """The gateway-side handler that BUILDS the responder (not the TUI one)."""
    patcher = patch.object(
        MeshCoreHandler, "_build_meshcore_oracle_responder",
        side_effect=build_side_effect if build_side_effect
        else (lambda self=None: None))
    with patcher:
        return MeshCoreHandler(
            config=_cfg(**cfg_over), node_tracker=MagicMock(), health=MagicMock(),
            stop_event=threading.Event(), stats={}, stats_lock=threading.Lock(),
            message_queue=Queue(maxsize=10))


def _live_oracle():
    """A REAL MeshOracleResponder, never a stub: the real class stores its
    posture PRIVATELY, so only the real object can disagree with a reader
    that guessed the attribute names."""
    from oracle.responder import MeshOracleResponder
    return MeshOracleResponder(
        snapshot_fn=lambda: {}, send_fn=lambda *a, **k: True,
        log_fn=lambda r: None,
        allowlist={"7eb0fa289c11"}, allowed_channels={1},
        answer_all=False, cooldown_s=10.0, transport="meshcore",
        consume=False)


def _bridge(handler=None, running=True, enabled=True, sim=True):
    from datetime import datetime
    b = SimpleNamespace()
    b._meshcore_handler = handler
    b._running = running
    b.config = SimpleNamespace(meshcore=SimpleNamespace(enabled=enabled,
                                                        simulation_mode=sim))
    b._stats_lock = threading.Lock()
    b.stats = {"start_time": datetime.now(), "meshcore_rx": 3, "meshcore_tx": 1}
    return b


@pytest.fixture(autouse=True)
def _clean_registry():
    gateway_cli.clear_registered_bridges()
    yield
    gateway_cli.clear_registered_bridges()


# ── oracle posture ──────────────────────────────────────────────────────

class TestOraclePosture:
    def test_no_handler_is_unobservable_not_off(self):
        p = api.oracle_posture(None)
        assert p["observable"] is False
        assert "enabled" not in p        # must not assert a state it cannot see

    def test_build_failure_is_distinct_from_disabled(self):
        broken = SimpleNamespace(_oracle_error="RuntimeError: boom", _oracle=None)
        off = SimpleNamespace(_oracle_error=None, _oracle=None)
        pb, po = api.oracle_posture(broken), api.oracle_posture(off)
        assert pb["enabled"] is False and pb["error"]
        assert po["enabled"] is False and "error" not in po
        assert pb != po

    def test_enabled_reports_the_live_shape_from_the_real_object(self):
        h = SimpleNamespace(_oracle_error=None, _oracle=_live_oracle())
        p = api.oracle_posture(h)
        assert p == {"observable": True, "enabled": True, "answer_all": False,
                     "allowlist": 1, "channels": ["1"], "cooldown_s": 10.0,
                     "consume": False, "transport": "meshcore"}
        assert "unreadable" not in p

    def test_allowlist_is_a_count_not_the_tokens(self):
        p = api.oracle_posture(SimpleNamespace(_oracle_error=None, _oracle=_live_oracle()))
        assert p["allowlist"] == 1
        assert "7eb0fa289c11" not in repr(p)

    def test_an_unreadable_field_is_named_not_defaulted(self):
        h = SimpleNamespace(_oracle_error=None, _oracle=SimpleNamespace(consume=False))
        p = api.oracle_posture(h)
        assert p["unreadable"], "silently defaulted every field"
        assert "allowlist" in p["unreadable"]

    def test_the_line_matches_the_daemons_own_describe(self):
        """The journal's `responder built:` line and the posture must agree
        field for field — two surfaces, one vocabulary."""
        o = _live_oracle()
        p = api.oracle_posture(SimpleNamespace(_oracle_error=None, _oracle=o))
        described = o.describe()
        assert f"allowlist={p['allowlist']}" in described
        assert f"answer_all={p['answer_all']}" in described
        assert f"consume={p['consume']}" in described


class TestHandlerRecordsABuildFailure:
    def test_a_raising_builder_leaves_a_witness(self):
        h = _daemon_handler(build_side_effect=RuntimeError("boom"))
        assert h._oracle is None
        assert h._oracle_error and "boom" in h._oracle_error
        assert api.oracle_posture(h)["error"]

    def test_a_clean_build_records_no_error(self):
        h = _daemon_handler()
        assert h._oracle is None and h._oracle_error is None
        assert api.oracle_posture(h) == {"observable": True, "enabled": False}


# ── contacts + device snapshots ─────────────────────────────────────────

class TestContactsSnapshot:
    def test_not_connected_is_unobserved_never_empty(self):
        h = _daemon_handler()
        snap = h.get_contacts_snapshot()
        assert snap["observed"] is False
        assert snap["contacts"] == [] and snap["count"] == 0
        assert "not connected" in snap["reason"]

    def test_simulator_table_is_normalised(self):
        h = _daemon_handler()
        h._meshcore = MeshCoreSimulator()
        h._connected = True
        snap = h.get_contacts_snapshot()
        assert snap["observed"] is True and snap["count"] == 3
        names = {c["name"] for c in snap["contacts"]}
        assert names == {"SimNode-Alpha", "SimNode-Bravo", "SimRepeater-01"}
        row = next(c for c in snap["contacts"] if c["name"] == "SimRepeater-01")
        assert row["prefix"] == "aabbccddeeff"
        assert row["role"] == "repeater"           # simulator's own role key
        assert row["last_advert_iso"] is None      # absent is None, never 1970
        assert row["lastmod_iso"] is None

    def test_extract_contacts_handles_both_wire_shapes(self):
        ev = SimpleNamespace(payload={"aa": {"adv_name": "x"}, "bb": {"adv_name": "y"}})
        assert [c["adv_name"] for c in MeshCoreHandler._extract_contacts(ev)] == ["x", "y"]
        assert MeshCoreHandler._extract_contacts([{"adv_name": "z"}]) == [{"adv_name": "z"}]
        assert MeshCoreHandler._extract_contacts(None) == []
        assert MeshCoreHandler._extract_contacts(SimpleNamespace(payload=42)) == []

    def test_wire_type_maps_to_role_and_epochs_to_local_iso(self):
        row = MeshCoreHandler._normalise_contact(
            {"adv_name": "p4", "public_key": bytes.fromhex("7eb0fa289c11" + "00" * 26),
             "type": 2, "last_advert": 2100000000, "lastmod": 1789336369,
             "out_path_len": -1, "adv_lat": 0.0, "adv_lon": 0.0})
        assert row["role"] == "repeater" and row["prefix"] == "7eb0fa289c11"
        assert row["last_advert_iso"].startswith("2036")
        assert row["lastmod_iso"].startswith("2026-09-13")

    def test_a_radio_that_does_not_answer_is_a_reason_not_a_list(self):
        h = _daemon_handler()
        h._connected = True

        class Dead:
            async def get_contacts(self):
                raise RuntimeError("serial timeout")
        h._meshcore = Dead()
        snap = h.get_contacts_snapshot()
        assert snap["observed"] is False and "serial timeout" in snap["reason"]


class TestDeviceInfoSnapshot:
    def test_simulator_names_its_source(self):
        h = _daemon_handler()
        h._meshcore = MeshCoreSimulator()
        h._connected = True
        d = h.get_device_info_snapshot()
        assert d["observed"] and d["source"] == "simulator"
        assert d["model"] == "MeshCoreSimulator" and d["fw_build"] == "sim"

    def test_real_query_reads_fw_ver_with_a_space_and_caches(self):
        """meshcore_py's DEVICE_INFO payload spells it 'fw ver' (reader.py:373)."""
        h = _daemon_handler()
        h._connected = True
        calls = []

        class Cmds:
            async def send_device_query(self):
                calls.append(1)
                return SimpleNamespace(payload={"fw ver": 11, "fw_build": "19-Apr-2026",
                                                "model": "RAK4631"})
        h._meshcore = SimpleNamespace(commands=Cmds())
        d = h.get_device_info_snapshot()
        assert d == {**d, "observed": True, "model": "RAK4631",
                     "fw_build": "19-Apr-2026", "fw_ver": 11, "source": "radio"}
        h.get_device_info_snapshot()
        assert len(calls) == 1, "second read within TTL must not hit the radio"
        h.get_device_info_snapshot(refresh=True)
        assert len(calls) == 2

    def test_not_connected_is_unobserved(self):
        d = _daemon_handler().get_device_info_snapshot()
        assert d["observed"] is False and d["model"] is None


# ── payload + registry + routing ─────────────────────────────────────────

class TestStatusPayload:
    def test_no_registered_bridge_is_unobservable(self):
        p = api.build_meshcore_status()
        assert p["observable"] is False and p["bridges"] == 0

    def test_registry_prefers_the_bridge_that_owns_the_handler(self):
        h = _daemon_handler()
        other = _bridge(handler=None)
        mine = _bridge(handler=h)
        gateway_cli.register_bridges([other, mine])
        p = api.build_meshcore_status()
        assert p["observable"] and p["bridges"] == 2 and p["handler"] is True
        assert p["oracle"] == {"observable": True, "enabled": False}
        assert p["counters"]["stats"]["meshcore_rx"] == 3
        assert "start_time" not in p["counters"]["stats"]
        assert p["counters"]["uptime_seconds"] >= 0

    def test_bridge_without_handler_is_observable_but_handlerless(self):
        gateway_cli.register_bridges([_bridge(handler=None)])
        p = api.build_meshcore_status()
        assert p["observable"] and p["handler"] is False
        assert p["oracle"]["observable"] is False
        assert p["device"]["observed"] is False

    def test_headless_singleton_shape_is_also_seen(self):
        b = _bridge(handler=None)
        with patch.object(gateway_cli, "_active_bridge", b):
            assert gateway_cli.registered_bridges() == [b]


def _stub(path):
    h = MetricsHTTPHandler.__new__(MetricsHTTPHandler)
    h.path = path
    h.wfile = io.BytesIO()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    return h


class TestRouting:
    def test_status_route_serves_json(self):
        gateway_cli.register_bridges([_bridge(handler=_daemon_handler())])
        h = _stub("/api/json/meshcore")
        h.do_GET()
        h.send_response.assert_called_with(200)
        body = json.loads(h.wfile.getvalue())
        assert body["observable"] and "oracle" in body and "device" in body

    def test_contacts_route_serves_the_table(self):
        dh = _daemon_handler()
        dh._meshcore = MeshCoreSimulator()
        dh._connected = True
        gateway_cli.register_bridges([_bridge(handler=dh)])
        h = _stub("/api/json/meshcore/contacts?x=1")
        h.do_GET()
        body = json.loads(h.wfile.getvalue())
        assert body["observed"] and body["count"] == 3

    def test_contacts_without_a_gateway_is_unobserved(self):
        h = _stub("/api/json/meshcore/contacts")
        h.do_GET()
        body = json.loads(h.wfile.getvalue())
        assert body["observed"] is False and body["contacts"] == []

    def test_unknown_meshcore_subpath_is_404(self):
        h = _stub("/api/json/meshcore/nope")
        h.do_GET()
        h.send_response.assert_called_with(404)
