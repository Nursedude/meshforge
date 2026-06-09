"""Tests for BridgeCorrelationStore — the Thread-2 durable per-message
mesh<->RNS correlation store that makes reply routing survive a bridge
restart.

All tests inject db_path under tmp_path — no test may touch ~/.config.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.correlation_store import (
    BridgeCorrelationStore,
    DEFAULT_TTL_SEC,
    DEFAULT_MAX_ROWS,
)

MESH_A = "!aabb0042"
MESH_B = "!aabb0043"
PEER_A = "ab" * 16   # 32 hex
PEER_B = "cd" * 16
TOKEN_A = "meshtastic:!aabb0042"
TOKEN_B = "meshtastic:!aabb0043"


@pytest.fixture
def store(tmp_path):
    return BridgeCorrelationStore(db_path=str(tmp_path / "corr.db"))


@pytest.fixture
def clock(monkeypatch):
    import gateway.correlation_store as cs
    state = [1000.0]
    monkeypatch.setattr(cs.time, "time", lambda: state[0])
    return state


class TestCorrelationCore:

    def test_record_lookup_roundtrip(self, store):
        assert store.record(MESH_A, PEER_A, direction="m2r") is True
        assert store.lookup_reply_token(PEER_A) == TOKEN_A

    def test_lookup_missing_returns_none(self, store):
        assert store.lookup_reply_token(PEER_A) is None

    def test_restart_survives(self, tmp_path):
        """The headline property: a fresh store on the same DB still
        resolves the reply token — what the in-memory ReplyContextStore
        loses on restart."""
        path = str(tmp_path / "corr.db")
        s1 = BridgeCorrelationStore(db_path=path)
        assert s1.record(MESH_A, PEER_A, direction="m2r") is True
        # Simulate a bridge restart: brand-new object, same file.
        s2 = BridgeCorrelationStore(db_path=path)
        assert s2.lookup_reply_token(PEER_A) == TOKEN_A

    def test_latest_m2r_wins(self, store, clock):
        store.record(MESH_A, PEER_A, direction="m2r")
        clock[0] = 1010.0
        store.record(MESH_B, PEER_A, direction="m2r")  # B last messaged peer A
        assert store.lookup_reply_token(PEER_A) == TOKEN_B
        clock[0] = 1020.0
        store.record(MESH_A, PEER_A, direction="m2r")  # A again — most recent
        assert store.lookup_reply_token(PEER_A) == TOKEN_A

    def test_direction_isolation(self, store):
        """An r2m row must not satisfy an m2r reply lookup."""
        assert store.record(MESH_A, PEER_A, direction="r2m") is True
        assert store.lookup_reply_token(PEER_A) is None

    def test_ttl_expiry(self, store, clock):
        store.record(MESH_A, PEER_A, direction="m2r")
        clock[0] = 1000.0 + DEFAULT_TTL_SEC + 1
        assert store.lookup_reply_token(PEER_A) is None  # lazy TTL in WHERE
        assert store.expire_idle() == 1                  # physically pruned
        assert store.active_count() == 0

    def test_max_rows_oldest_evicted(self, tmp_path, clock):
        s = BridgeCorrelationStore(db_path=str(tmp_path / "s.db"), max_rows=2)
        s.record(MESH_A, PEER_A, direction="m2r")
        clock[0] = 1010.0
        s.record(MESH_B, PEER_A, direction="m2r")
        clock[0] = 1020.0
        s.record(MESH_A, PEER_B, direction="m2r")
        assert s.expire_idle() == 1          # cap-evict the oldest row
        assert s.active_count() == 2

    def test_bad_input_rejected(self, store):
        assert store.record("aabb0042", PEER_A) is False      # missing !
        assert store.record(MESH_A, "zz" * 16) is False       # non-hex peer
        assert store.record(MESH_A, "ab" * 8) is False        # wrong length
        assert store.record(None, PEER_A) is False
        assert store.record(MESH_A, PEER_A, direction="x") is False
        assert store.lookup_reply_token(PEER_A) is None        # nothing written

    def test_normalization(self, store):
        assert store.record("!AABB0042", PEER_A.upper(),
                            direction="m2r") is True
        assert store.lookup_reply_token(PEER_A) == TOKEN_A
        assert store.lookup_reply_token(PEER_A.upper()) == TOKEN_A

    def test_packet_id_bool_guard(self, store):
        """bool is an int subclass — must not land in the int column."""
        assert store.record(MESH_A, PEER_A, direction="m2r",
                            mesh_packet_id=True) is True
        rows = store.list_recent()
        assert rows[0]["mesh_packet_id"] is None

    def test_list_recent_shape(self, store):
        store.record(MESH_A, PEER_A, direction="m2r", channel="meshforge")
        rows = store.list_recent()
        assert set(rows[0].keys()) == {
            "corr_id", "mesh_node", "mesh_packet_id", "channel",
            "rns_peer_hash", "direction", "created_ts", "last_interaction_ts",
        }
        assert rows[0]["channel"] == "meshforge"

    def test_clear(self, store):
        store.record(MESH_A, PEER_A, direction="m2r")
        store.record(MESH_B, PEER_B, direction="m2r")
        assert store.clear() == 2
        assert store.active_count() == 0


class TestCorrelationSafety:

    def test_lazy_db_no_file_on_ctor(self, tmp_path):
        path = tmp_path / "lazy.db"
        s = BridgeCorrelationStore(db_path=str(path))
        assert s._initialized is False
        assert not path.exists()

    def test_first_use_opens_db(self, tmp_path):
        path = tmp_path / "lazy.db"
        s = BridgeCorrelationStore(db_path=str(path))
        s.record(MESH_A, PEER_A, direction="m2r")
        assert path.exists()

    def test_lookup_only_does_not_create_until_resolved(self, tmp_path):
        """A bad-input lookup short-circuits before _ensure_db, so the
        file stays absent on a flag-off box that never records."""
        path = tmp_path / "lazy.db"
        s = BridgeCorrelationStore(db_path=str(path))
        assert s.lookup_reply_token("not-a-hash") is None
        assert not path.exists()

    def test_never_raises_on_bad_path(self):
        s = BridgeCorrelationStore(db_path="/nonexistent_dir/nope/x.db")
        assert s.record(MESH_A, PEER_A, direction="m2r") is False
        assert s.lookup_reply_token(PEER_A) is None
        assert s.active_count() == 0
        assert s.expire_idle() == 0
        assert s.list_recent() == []
        assert s.clear() == 0

    def test_magicmock_numerics_sanitized(self, tmp_path):
        s = BridgeCorrelationStore(db_path=str(tmp_path / "s.db"),
                                   ttl_sec=MagicMock(),
                                   max_rows=MagicMock())
        assert s._ttl == DEFAULT_TTL_SEC
        assert s._max == DEFAULT_MAX_ROWS
