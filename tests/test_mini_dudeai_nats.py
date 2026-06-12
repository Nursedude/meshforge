"""Dude-claw Phase A: stdlib NATS client + OpenClaw sensor source + actuator
action + standalone preset.

The fake NATS server below speaks just enough of the wire protocol (INFO/
CONNECT/SUB/PUB/MSG/PING/-ERR) to exercise every honest-failure-mode branch
without a real nats-server: timeouts, auth violations, EOFs, ok:false device
replies, unparseable readings. Every reply path the client can see in
production has a test twin here.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai.nats_client import (
    NatsConnection, NatsError, parse_server, request, request_many,
)
from mini_dudeai.sources.base import Condition
from mini_dudeai.sources.nats_sensor import NatsSensorSource, _extract_value
from mini_dudeai.actions.nats_action import IDEMPOTENT_TOOLS, NatsAction
from mini_dudeai import validate_config


# --------------------------------------------------------------------------
# fake NATS server
# --------------------------------------------------------------------------

class FakeNats:
    """Single-connection-at-a-time fake NATS server.

    handler(subject, payload_str) decides the response:
      dict/str        → one MSG reply
      list            → that many MSG replies (discovery)
      None            → no reply (client should time out)
      "-ERR ..." str  → raw error frame
    expected_token: when set, a CONNECT without the matching auth_token gets
    -ERR + close (real nats-server behavior for token auth).
    """

    def __init__(self, handler=None, expected_token=None, ping_first=False):
        self.handler = handler or (lambda subj, payload: {"ok": True})
        self.expected_token = expected_token
        self.ping_first = ping_first
        self.connects: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self.server = f"127.0.0.1:{self.port}"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    # -- internals ----------------------------------------------------

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                self._serve_conn(conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _serve_conn(self, conn):
        conn.settimeout(5)
        f = conn.makefile("rb")
        conn.sendall(b'INFO {"server_id":"fake","auth_required":'
                     + (b"true" if self.expected_token else b"false") + b"}\r\n")
        sub_inbox, sub_sid = None, None
        while True:
            line = f.readline()
            if not line:
                return
            line = line.rstrip(b"\r\n")
            if line.startswith(b"CONNECT "):
                opts = json.loads(line[8:].decode())
                self.connects.append(opts)
                if (self.expected_token
                        and opts.get("auth_token") != self.expected_token):
                    conn.sendall(b"-ERR 'Authorization Violation'\r\n")
                    return
            elif line.startswith(b"SUB "):
                _, sub_inbox, sub_sid = line.decode().split(" ")
            elif line.startswith(b"PUB "):
                parts = line.decode().split(" ")
                subject, nbytes = parts[1], int(parts[-1])
                reply_to = parts[2] if len(parts) == 4 else None
                payload = f.read(nbytes + 2)[:-2].decode()
                self.requests.append((subject, payload))
                if reply_to is None:
                    continue
                if self.ping_first:
                    conn.sendall(b"PING\r\n")
                out = self.handler(subject, payload)
                if out is None:
                    continue  # let the client time out
                if isinstance(out, str) and out.startswith("-ERR"):
                    conn.sendall(out.encode() + b"\r\n")
                    continue
                replies = out if isinstance(out, list) else [out]
                for r in replies:
                    body = (r if isinstance(r, str)
                            else json.dumps(r)).encode()
                    conn.sendall(
                        f"MSG {reply_to} {sub_sid} {len(body)}\r\n".encode()
                        + body + b"\r\n")
            elif line == b"PONG":
                continue


@pytest.fixture
def fake():
    servers = []

    def make(**kw):
        s = FakeNats(**kw)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


# --------------------------------------------------------------------------
# nats_client
# --------------------------------------------------------------------------

class TestParseServer:
    def test_variants(self):
        assert parse_server("moc1") == ("moc1", 4222)
        assert parse_server("moc1:4333") == ("moc1", 4333)
        assert parse_server("nats://moc1:4222") == ("moc1", 4222)
        assert parse_server("nats://moc1") == ("moc1", 4222)

    def test_empty_is_error(self):
        with pytest.raises(NatsError):
            parse_server("")


class TestNatsClient:
    def test_request_round_trip(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True, "result": "chip_temp: 33.2 C"})
        out = request(srv.server, "dev.tool_exec",
                      '{"tool":"sensor_read","name":"chip_temp"}')
        assert out == {"ok": True, "result": "chip_temp: 33.2 C"}
        assert srv.requests == [
            ("dev.tool_exec", '{"tool":"sensor_read","name":"chip_temp"}')]

    def test_no_reply_times_out_as_error(self, fake):
        srv = fake(handler=lambda s, p: None)
        with pytest.raises(NatsError, match="timed out"):
            request(srv.server, "dev.tool_exec", "{}", timeout_s=0.3)

    def test_server_err_frame_raises(self, fake):
        srv = fake(handler=lambda s, p: "-ERR 'Unknown Subject'")
        with pytest.raises(NatsError, match="server error"):
            request(srv.server, "dev.tool_exec", "{}")

    def test_connection_refused_is_nats_error(self):
        # bind a port then close it → nothing listening
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with pytest.raises(NatsError, match="connect"):
            request(f"127.0.0.1:{port}", "x", "", timeout_s=0.3)

    def test_ping_during_wait_is_answered(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True}, ping_first=True)
        out = request(srv.server, "dev.tool_exec", "{}")
        assert out == {"ok": True}

    def test_token_travels_in_connect(self, fake):
        srv = fake(expected_token="s3cret")
        out = request(srv.server, "dev.tool_exec", "{}", token="s3cret")
        assert out == {"ok": True}
        assert srv.connects[0]["auth_token"] == "s3cret"

    def test_bad_token_is_loud(self, fake):
        srv = fake(expected_token="s3cret")
        with pytest.raises(NatsError):
            request(srv.server, "dev.tool_exec", "{}", token="wrong",
                    timeout_s=0.5)

    def test_request_many_collects_window(self, fake):
        srv = fake(handler=lambda s, p: [{"device": "a"}, {"device": "b"}])
        out = request_many(srv.server, "_ion.discover", "", timeout_s=0.5)
        assert out == [{"device": "a"}, {"device": "b"}]

    def test_request_many_zero_replies_is_empty_not_error(self, fake):
        srv = fake(handler=lambda s, p: None)
        assert request_many(srv.server, "_ion.discover", "",
                            timeout_s=0.3) == []

    def test_non_json_reply_returned_as_text(self, fake):
        srv = fake(handler=lambda s, p: "plain text")
        assert request(srv.server, "dev.tool_exec", "{}") == "plain text"

    def test_several_requests_share_one_connection(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True, "echo": json.loads(p)})
        with NatsConnection(srv.server) as nc:
            a = nc.request("dev.tool_exec", '{"n":1}')
            b = nc.request("dev.tool_exec", '{"n":2}')
        assert a["echo"] == {"n": 1} and b["echo"] == {"n": 2}


# --------------------------------------------------------------------------
# value extraction
# --------------------------------------------------------------------------

class TestExtractValue:
    def test_wireclaw_string_shape(self):
        assert _extract_value("chip_temp: 33.2 C") == 33.2

    def test_digit_in_sensor_name_not_mistaken(self):
        # the number AFTER the colon is the reading, not "2" from "temp2"
        assert _extract_value("temp2: -4 C") == -4.0

    def test_bare_number(self):
        assert _extract_value(41) == 41.0
        assert _extract_value(3.5) == 3.5

    def test_no_number_is_none_not_zero(self):
        assert _extract_value("sensor offline") is None
        assert _extract_value(None) is None
        assert _extract_value(True) is None  # bool is not a reading


# --------------------------------------------------------------------------
# NatsSensorSource
# --------------------------------------------------------------------------

def _sensor(server, **over):
    spec = {"device": "claw", "sensor": "chip_temp",
            "tool": "temperature_read", "threshold": 40.0}
    spec.update(over)
    return NatsSensorSource(server=server, sensors=[spec], timeout_s=0.5)


class TestNatsSensorSourceConstruction:
    def test_zero_sensors_refused(self):
        with pytest.raises(ValueError, match="at least one sensor"):
            NatsSensorSource(server="x", sensors=[])

    @pytest.mark.parametrize("missing", ["device", "sensor", "threshold"])
    def test_missing_field_refused(self, missing):
        spec = {"device": "d", "sensor": "s", "threshold": 1}
        del spec[missing]
        with pytest.raises(ValueError, match=missing):
            NatsSensorSource(server="x", sensors=[spec])

    def test_bad_op_refused(self):
        with pytest.raises(ValueError, match="op"):
            NatsSensorSource(server="x", sensors=[
                {"device": "d", "sensor": "s", "threshold": 1, "op": "between"}])

    def test_non_numeric_threshold_refused(self):
        with pytest.raises(ValueError):
            NatsSensorSource(server="x", sensors=[
                {"device": "d", "sensor": "s", "threshold": "hot"}])


class TestNatsSensorSourceCollect:
    def test_breach_emits_condition(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True, "result": "chip_temp: 55.5 C"})
        conds = list(_sensor(srv.server).collect())
        assert len(conds) == 1
        c = conds[0]
        assert c.kind == "sensor_breach"
        assert c.subject == "claw.chip_temp"
        assert c.extras["value"] == 55.5
        assert c.extras["threshold"] == 40.0

    def test_below_threshold_emits_nothing(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True, "result": "chip_temp: 22.0 C"})
        assert list(_sensor(srv.server).collect()) == []

    def test_lt_op(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True, "result": "vbat: 3.2 V"})
        conds = list(_sensor(srv.server, sensor="vbat", op="lt",
                             threshold=3.5).collect())
        assert len(conds) == 1
        assert "lt" in conds[0].detail

    def test_device_ok_false_is_source_error(self, fake):
        srv = fake(handler=lambda s, p: {"ok": False, "error": "no such sensor"})
        conds = list(_sensor(srv.server).collect())
        assert [c.kind for c in conds] == ["source_error"]
        assert "no such sensor" in conds[0].detail

    def test_unparseable_reading_is_source_error_not_zero(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True, "result": "warming up"})
        conds = list(_sensor(srv.server).collect())
        assert [c.kind for c in conds] == ["source_error"]
        assert "unparseable" in conds[0].detail

    def test_bus_unreachable_is_one_source_error(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        src = _sensor(f"127.0.0.1:{port}")
        conds = list(src.collect())
        assert [c.kind for c in conds] == ["source_error"]
        assert "NATS unreachable" in conds[0].detail

    def test_per_sensor_failure_keeps_polling_others(self, fake):
        def handler(subject, payload):
            call = json.loads(payload)
            if call.get("name") == "broken":
                return {"ok": False, "error": "dead sensor"}
            return {"ok": True, "result": "fine: 99 C"}
        srv = fake(handler=handler)
        src = NatsSensorSource(server=srv.server, sensors=[
            {"device": "claw", "sensor": "broken", "threshold": 40},
            {"device": "claw", "sensor": "fine", "threshold": 40},
        ], timeout_s=0.5)
        kinds = sorted(c.kind for c in src.collect())
        assert kinds == ["sensor_breach", "source_error"]

    def test_sensor_read_carries_name_temperature_read_does_not(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True, "result": "x: 1"})
        src = NatsSensorSource(server=srv.server, sensors=[
            {"device": "claw", "sensor": "named", "threshold": 99},
            {"device": "claw", "sensor": "chip", "tool": "temperature_read",
             "threshold": 99},
        ], timeout_s=0.5)
        list(src.collect())
        calls = [json.loads(p) for _, p in srv.requests]
        assert calls[0] == {"tool": "sensor_read", "name": "named"}
        assert calls[1] == {"tool": "temperature_read"}


# --------------------------------------------------------------------------
# NatsAction
# --------------------------------------------------------------------------

LED_ON = {"tool": "led_set", "r": 255, "g": 0, "b": 0}
LED_OFF = {"tool": "led_set", "r": 0, "g": 0, "b": 0}


def _rule(**action_over):
    action = {"kind": "nats", "payload": dict(LED_ON),
              "payload_down": dict(LED_OFF)}
    action.update(action_over)
    return {"id": "test_rule", "match": {"kind": "sensor_breach"},
            "action": action}


def _cond():
    return Condition(kind="sensor_breach", subject="claw.chip_temp",
                     detail="hot")


class TestNatsActionConstruction:
    def test_requires_server(self):
        with pytest.raises(ValueError, match="server"):
            NatsAction(server="")

    def test_non_idempotent_default_payload_refused(self):
        with pytest.raises(ValueError, match="idempotent"):
            NatsAction(server="x", payload={"tool": "serial_send", "data": "go"})

    def test_payload_missing_tool_refused(self):
        with pytest.raises(ValueError, match="tool"):
            NatsAction(server="x", payload={"pin": 5})


class TestNatsActionExecute:
    def test_edge_up_sends_payload_ok(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True, "result": "led set"})
        a = NatsAction(server=srv.server, device="claw", timeout_s=0.5)
        out = a.execute(_rule(), _cond(), "edge_up")
        assert out.ok and out.action == "nats_edge_up"
        assert out.extras["tool"] == "led_set"
        assert json.loads(srv.requests[0][1]) == LED_ON
        assert srv.requests[0][0] == "claw.tool_exec"

    def test_edge_down_sends_payload_down(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True})
        a = NatsAction(server=srv.server, device="claw", timeout_s=0.5)
        out = a.execute(_rule(), _cond(), "edge_down")
        assert out.ok
        assert json.loads(srv.requests[0][1]) == LED_OFF

    def test_edge_down_without_payload_down_is_explicit_skip(self, fake):
        srv = fake()
        a = NatsAction(server=srv.server, device="claw", timeout_s=0.5)
        rule = _rule()
        del rule["action"]["payload_down"]
        out = a.execute(rule, _cond(), "edge_down")
        assert out.ok and out.extras.get("skipped")
        assert srv.requests == []  # never a fake actuation

    def test_device_refusal_is_failed_outcome(self, fake):
        srv = fake(handler=lambda s, p: {"ok": False, "error": "pin reserved"})
        a = NatsAction(server=srv.server, device="claw", timeout_s=0.5)
        out = a.execute(_rule(), _cond(), "edge_up")
        assert not out.ok and "pin reserved" in out.error

    def test_transport_failure_is_failed_outcome(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        a = NatsAction(server=f"127.0.0.1:{port}", device="claw",
                       timeout_s=0.3)
        out = a.execute(_rule(), _cond(), "edge_up")
        assert not out.ok and "transport" in out.error

    def test_non_idempotent_rule_payload_refused_at_execute(self, fake):
        srv = fake()
        a = NatsAction(server=srv.server, device="claw", timeout_s=0.5)
        out = a.execute(_rule(payload={"tool": "serial_send", "data": "x"}),
                        _cond(), "edge_up")
        assert not out.ok and "idempotent" in out.error
        assert srv.requests == []

    def test_missing_device_is_failed_outcome(self, fake):
        srv = fake()
        a = NatsAction(server=srv.server, timeout_s=0.5)  # no default device
        out = a.execute(_rule(), _cond(), "edge_up")
        assert not out.ok and "device" in out.error

    def test_rule_device_overrides_default(self, fake):
        srv = fake(handler=lambda s, p: {"ok": True})
        a = NatsAction(server=srv.server, device="default-claw", timeout_s=0.5)
        out = a.execute(_rule(device="other-claw"), _cond(), "edge_up")
        assert out.ok
        assert srv.requests[0][0] == "other-claw.tool_exec"

    def test_allowlist_is_the_documented_trio(self):
        assert IDEMPOTENT_TOOLS == {"gpio_write", "led_set", "actuator_set"}


# --------------------------------------------------------------------------
# config integration (validate_config + builders)
# --------------------------------------------------------------------------

class TestConfigIntegration:
    def test_validate_accepts_nats_kinds(self):
        errors = validate_config({
            "rules_path": "/tmp/r.json",
            "sources": [{"kind": "nats_sensor", "server": "localhost:4222",
                         "sensors": [{"device": "d", "sensor": "s",
                                      "threshold": 1}]}],
            "actions": {"claw": {"kind": "nats", "server": "localhost:4222"}},
        })
        assert errors == []

    def test_validate_flags_missing_required(self):
        errors = validate_config({
            "rules_path": "/tmp/r.json",
            "sources": [{"kind": "nats_sensor"}],
            "actions": {"claw": {"kind": "nats"}},
        })
        joined = " ".join(errors)
        assert "server" in joined and "sensors" in joined

    def test_token_env_unset_is_loud_at_build(self, monkeypatch):
        from mini_dudeai.config import _seed_nats_action
        monkeypatch.delenv("CLAW_TOKEN_TEST", raising=False)
        with pytest.raises(ValueError, match="CLAW_TOKEN_TEST"):
            _seed_nats_action({"kind": "nats", "server": "x",
                               "token_env": "CLAW_TOKEN_TEST"})

    def test_token_env_set_resolves(self, monkeypatch):
        from mini_dudeai.config import _resolve_nats_token
        monkeypatch.setenv("CLAW_TOKEN_TEST", "tok")
        assert _resolve_nats_token({"token_env": "CLAW_TOKEN_TEST"}) == "tok"
        assert _resolve_nats_token({"token": "lit"}) == "lit"
        assert _resolve_nats_token({}) is None


# --------------------------------------------------------------------------
# standalone preset
# --------------------------------------------------------------------------

class TestStandalonePreset:
    def _env(self, monkeypatch, tmp_path, **over):
        vals = {
            "MINI_DUDEAI_NATS_SERVER": "localhost:4222",
            "MINI_DUDEAI_NTFY_TOPIC": "test-topic",
            "MINI_DUDEAI_CLAW_DEVICE": "dudeclaw-01",
        }
        vals.update(over)
        for k in ("MINI_DUDEAI_NATS_SERVER", "MINI_DUDEAI_NTFY_TOPIC",
                  "MINI_DUDEAI_CLAW_DEVICE", "MINI_DUDEAI_NATS_TOKEN",
                  "MINI_DUDEAI_CLAW_SENSORS",
                  "MINI_DUDEAI_CLAW_TEMP_THRESHOLD"):
            monkeypatch.delenv(k, raising=False)
        for k, v in vals.items():
            if v is not None:
                monkeypatch.setenv(k, v)
        monkeypatch.setenv("MINI_DUDEAI_HOME", str(tmp_path))

    def test_builds_with_full_env(self, monkeypatch, tmp_path):
        self._env(monkeypatch, tmp_path)
        from mini_dudeai.presets.standalone import build_engine
        eng = build_engine()
        names = [getattr(s, "name", "") for s in eng.sources]
        assert "claw_sensors" in names
        assert set(eng.actions) == {"nats", "ntfy", "annotate", "none"}

    @pytest.mark.parametrize("missing,token", [
        ("MINI_DUDEAI_NATS_SERVER", "MINI_DUDEAI_NATS_SERVER"),
        ("MINI_DUDEAI_NTFY_TOPIC", "MINI_DUDEAI_NTFY_TOPIC"),
        ("MINI_DUDEAI_CLAW_DEVICE", "MINI_DUDEAI_CLAW_DEVICE"),
    ])
    def test_missing_required_env_fails_loud(self, monkeypatch, tmp_path,
                                             missing, token):
        self._env(monkeypatch, tmp_path, **{missing: None})
        from mini_dudeai.presets.standalone import build_engine
        with pytest.raises(ValueError, match=token):
            build_engine()

    def test_paths_are_claw_suffixed_for_flock_independence(
            self, monkeypatch, tmp_path):
        self._env(monkeypatch, tmp_path)
        from mini_dudeai.presets.standalone import build_engine
        eng = build_engine()
        # the #80 instance lock keys on the state path — the claw instance
        # must never collide with the fleet daemon's mini_dudeai_state.json
        assert "claw" in os.path.basename(eng.state_store.path)
        assert eng.state_store.path != os.path.join(
            str(tmp_path), "mini_dudeai_state.json")

    def test_seeds_rules_on_first_boot_only(self, monkeypatch, tmp_path):
        self._env(monkeypatch, tmp_path)
        from mini_dudeai.presets.standalone import build_engine
        build_engine()
        rules_path = tmp_path / "mini_dudeai_claw_rules.json"
        assert rules_path.exists()
        seeded = json.loads(rules_path.read_text())
        ids = {r["id"] for r in seeded["rules"]}
        assert {"chip_temp_hot_led", "chip_temp_hot_page",
                "claw_blind_any"} <= ids
        # operator edits survive a rebuild — the seed is a bootstrap,
        # never an overwrite
        rules_path.write_text(json.dumps({"rules": []}))
        build_engine()
        assert json.loads(rules_path.read_text()) == {"rules": []}

    def test_sensors_file_replaces_default_and_device_optional(
            self, monkeypatch, tmp_path):
        sensors = tmp_path / "sensors.json"
        sensors.write_text(json.dumps([
            {"device": "shop", "sensor": "temp", "threshold": 28}]))
        self._env(monkeypatch, tmp_path,
                  MINI_DUDEAI_CLAW_DEVICE=None,
                  MINI_DUDEAI_CLAW_SENSORS=str(sensors))
        from mini_dudeai.presets.standalone import build_engine
        eng = build_engine()
        src = [s for s in eng.sources if getattr(s, "name", "") == "claw_sensors"][0]
        assert src.sensors[0]["device"] == "shop"

    def test_invalid_sensors_file_fails_loud_no_silent_fallback(
            self, monkeypatch, tmp_path):
        sensors = tmp_path / "sensors.json"
        sensors.write_text("{not json")
        self._env(monkeypatch, tmp_path,
                  MINI_DUDEAI_CLAW_SENSORS=str(sensors))
        from mini_dudeai.presets.standalone import build_engine
        with pytest.raises(ValueError, match="refusing to fall back"):
            build_engine()

    def test_bad_threshold_env_fails_loud(self, monkeypatch, tmp_path):
        self._env(monkeypatch, tmp_path,
                  MINI_DUDEAI_CLAW_TEMP_THRESHOLD="toasty")
        from mini_dudeai.presets.standalone import build_engine
        with pytest.raises(ValueError, match="toasty"):
            build_engine()

    def test_seed_rule_payloads_are_idempotent_tools(self):
        seed_path = os.path.join(os.path.dirname(__file__), "..", "configs",
                                 "mini_dudeai_rules.claw.json")
        with open(seed_path) as f:
            seed = json.load(f)
        for rule in seed["rules"]:
            action = rule["action"]
            for key in ("payload", "payload_down"):
                if key in action:
                    assert action[key]["tool"] in IDEMPOTENT_TOOLS, (
                        f"{rule['id']}.{key} uses a non-idempotent tool — "
                        f"unsafe under the #81 retry queue")
