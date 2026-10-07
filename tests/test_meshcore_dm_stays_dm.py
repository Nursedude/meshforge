"""A MeshCore DM stays a DM — operator declaration 2026-10-07.

Ingress enumeration row 3 (review_provenance 2026-10-06): a DM to the
gateway went oracle → router (allow-all) → bridge queue, and the bridge
re-broadcast it on the Meshtastic channel and fanned it out over LXMF. No
egress preserves DM-ness across meshes, so "stays a DM" means: never queued
for the bridge. What a DM may still do, and must keep doing:
  * be answered by the oracle, DIRECTED back to the sender (a DM answered
    as a DM);
  * reach the local message callback (UI / history) — that is not a bridge;
  * leave a witness (an INFO line + a counter), because a swallow with no
    witness never happened (honest_failure_modes #9).
Wire shape fed here is meshcore_py's CONTACT_MSG_RECV keys (pubkey_prefix,
type 'PRIV', text), never fabricated `channel`/`is_channel`.
"""

import asyncio
import logging
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.meshcore_handler import MeshCoreHandler
from gateway.config import GatewayConfig


def _dm_event(text="good morning", prefix="7eb0fa28a1b2"):
    return SimpleNamespace(type='CONTACT_MSG_RECV', payload={
        'type': 'PRIV', 'pubkey_prefix': prefix, 'text': text,
        'path_len': 255, 'txt_type': 0, 'sender_timestamp': 1791400000})


@pytest.fixture
def handler():
    cfg = GatewayConfig()
    cfg.meshcore.enabled = True
    cfg.meshcore.simulation_mode = True
    h = MeshCoreHandler(
        config=cfg,
        node_tracker=MagicMock(),
        health=MagicMock(),
        stop_event=MagicMock(),
        stats={},
        stats_lock=__import__('threading').Lock(),
        message_queue=Queue(maxsize=100),
        message_callback=None,
        should_bridge=lambda m: True,   # router allows everything, as live
    )
    return h


def _run(coro):
    asyncio.run(coro)


class TestDmNeverReachesTheBridge:

    def test_plain_dm_is_not_queued(self, handler):
        _run(handler._on_contact_message(_dm_event()))
        assert handler._message_queue.empty(), (
            "a MeshCore DM was queued for the bridge — it would be "
            "re-broadcast on another mesh")

    def test_not_queued_even_when_router_allows_all(self, handler):
        calls = []
        handler._should_bridge = lambda m: calls.append(m) or True
        _run(handler._on_contact_message(_dm_event()))
        assert handler._message_queue.empty()

    def test_not_queued_when_oracle_bridge_through(self, handler):
        # consume=False is bridge-through for CHANNEL queries; it must not
        # turn a DM into a broadcast either.
        handler._oracle = MagicMock()
        handler._oracle.handle.return_value = "dude-AI@x: ok"
        handler._oracle.consume = False
        _run(handler._on_contact_message(_dm_event("status")))
        handler._oracle.handle.assert_called_once()
        assert handler._message_queue.empty()

    def test_bridge_dms_true_in_config_cannot_reopen_it(self, handler):
        handler.config.meshcore.bridge_dms = True
        _run(handler._on_contact_message(_dm_event()))
        assert handler._message_queue.empty()


class TestWhatADmStillDoes:

    def test_oracle_still_answers_directed(self, handler):
        handler._oracle = MagicMock()
        handler._oracle.handle.return_value = "dude-AI@x: ok"
        _run(handler._on_contact_message(_dm_event("status", "abcdef012345")))
        handler._oracle.handle.assert_called_once_with(
            'abcdef012345', 'status', None)

    def test_local_callback_still_fires(self, handler):
        cb = MagicMock()
        handler._message_callback = cb
        _run(handler._on_contact_message(_dm_event()))
        assert cb.called

    def test_witness_counter_and_info_line(self, handler, caplog):
        with caplog.at_level(logging.INFO, logger="gateway.meshcore_handler"):
            _run(handler._on_contact_message(_dm_event(prefix="7eb0fa28a1b2")))
        assert handler.stats.get('meshcore_dm_kept') == 1
        assert handler.stats.get('meshcore_rx') == 1
        assert "kept as a DM" in caplog.text
        assert "7eb0fa28" in caplog.text
