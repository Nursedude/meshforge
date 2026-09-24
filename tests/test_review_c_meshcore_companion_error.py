"""Review C (third-party, 2026-09-23): MeshForge's MeshCore DM path ignores
the Event meshcore_py returns. meshcore_py 2.3.14 ``commands.send_msg`` never
raises — a companion timeout / no_event_received / device ERR frame comes back
as ``Event(EventType.ERROR, {...})`` (verified on meshanchor-server's venv,
``meshcore/commands/base.py`` ``send()``). MeshAnchor e40f66cd fixed this
(``companion_error``); the MeshForge port 286a812f carried only the
denominator rule, so MF still records a refused DM as SENT.
"""
import asyncio
import os
import sys
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway import delivery_counters as dc  # noqa: E402
from gateway.meshcore_handler import MeshCoreHandler  # noqa: E402
from utils import tx_guard  # noqa: E402


class _Evt:
    """meshcore_py 2.3.14 Event shape: .type / .payload / .is_error()."""

    def __init__(self, typ, payload):
        self.type = typ
        self.payload = payload

    def is_error(self):
        return self.type == "command_error"


@pytest.fixture
def handler():
    config = SimpleNamespace(
        meshcore=SimpleNamespace(
            enabled=True, device_path="/dev/ttyUSB1", baud_rate=115200,
            connection_type="serial", tcp_host="localhost", tcp_port=4000,
            auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
            simulation_mode=True, channel_poll_interval_sec=5),
        meshtastic=SimpleNamespace(host="localhost", port=4403),
    )
    health = MagicMock()
    health.record_error.return_value = "transient"
    return MeshCoreHandler(
        config=config, node_tracker=MagicMock(), health=health,
        stop_event=threading.Event(), stats={"errors": 0},
        stats_lock=threading.Lock(), message_queue=Queue(maxsize=100),
    )


@pytest.fixture(autouse=True)
def fresh_counters():
    dc._reset_singleton_for_tests()
    dc.get_singleton()._reset_for_tests()
    yield
    dc._reset_singleton_for_tests()


def _meshcore_events():
    return [e for e in dc.get_singleton().recent() if e.protocol == "meshcore"]


def _send_dm(handler, send_evt):
    contact = {"public_key": b"\xab\xcd", "adv_name": "p3"}
    cmds = SimpleNamespace(
        get_contacts=AsyncMock(return_value=[contact]),
        send_msg=AsyncMock(return_value=send_evt),
        send_chan_msg=AsyncMock(return_value=None),
    )
    handler._meshcore = SimpleNamespace(commands=cmds)
    handler._connected = True
    with tx_guard.allow_meshcore_egress():
        return asyncio.run(handler._send_message("hello", destination="abcd"))


def test_dm_companion_error_event_is_a_drop_not_sent(handler):
    assert _send_dm(handler, _Evt("command_error", {"reason": "timeout"})) is False
    (ev,) = _meshcore_events()
    assert ev.state is dc.DeliveryState.DROPPED
    assert "timeout" in ev.note


def test_dm_device_err_frame_names_the_code(handler):
    # reader.py dispatches a device ERR frame as {"error_code": n,
    # "code_string": "ERR_CODE_..."} — no "reason" key.
    assert _send_dm(handler, _Evt("command_error",
                                  {"error_code": 5, "code_string": "ERR_CODE_FILE_IO_ERROR"})) is False
    (ev,) = _meshcore_events()
    assert ev.state is dc.DeliveryState.DROPPED
    assert "ERR_CODE_FILE_IO_ERROR" in ev.note


def test_dm_msg_sent_event_is_sent(handler):
    assert _send_dm(handler, _Evt("message_sent", {"expected_ack": b"\x01\x02"})) is True
    (ev,) = _meshcore_events()
    assert ev.state is dc.DeliveryState.SENT


def test_dm_none_return_is_sent(handler):
    # Older library / simulator double: absence of an error is not an error.
    assert _send_dm(handler, None) is True
    (ev,) = _meshcore_events()
    assert ev.state is dc.DeliveryState.SENT
