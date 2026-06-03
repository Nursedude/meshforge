"""
Tests for SessionStore — the Theme-A step-3 durable session layer.

All tests inject db_path under tmp_path — no test may touch ~/.config.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.session_store import (
    SessionStore,
    DEFAULT_IDLE_TIMEOUT_SEC,
    DEFAULT_MAX_SESSIONS,
)

MESH_A = "!aabb0042"
MESH_B = "!aabb0043"
PEER_A = "ab" * 16
PEER_B = "cd" * 16


@pytest.fixture
def store(tmp_path):
    return SessionStore(db_path=str(tmp_path / "sessions.db"))


@pytest.fixture
def clock(monkeypatch):
    import gateway.session_store as ss
    state = [1000.0]
    monkeypatch.setattr(ss.time, "time", lambda: state[0])
    return state


class TestSessionStoreCore:

    def test_touch_lookup_roundtrip(self, store):
        assert store.touch(MESH_A, PEER_A) is True
        assert store.lookup(MESH_A) == PEER_A

    def test_lookup_missing_returns_none(self, store):
        assert store.lookup(MESH_A) is None

    def test_most_recent_peer_wins(self, store, clock):
        store.touch(MESH_A, PEER_A)
        clock[0] = 1010.0
        store.touch(MESH_A, PEER_B)
        assert store.lookup(MESH_A) == PEER_B
        clock[0] = 1020.0
        store.touch(MESH_A, PEER_A)  # A talks again — most recent
        assert store.lookup(MESH_A) == PEER_A

    def test_ttl_expiry(self, store, clock):
        store.touch(MESH_A, PEER_A)
        clock[0] = 1000.0 + DEFAULT_IDLE_TIMEOUT_SEC + 1
        assert store.lookup(MESH_A) is None       # lazy TTL in the WHERE
        assert store.expire_idle() == 1           # row physically pruned
        assert store.active_count() == 0

    def test_max_entries_oldest_evicted(self, tmp_path, clock):
        s = SessionStore(db_path=str(tmp_path / "s.db"), max_sessions=2)
        s.touch(MESH_A, PEER_A)
        clock[0] = 1010.0
        s.touch(MESH_B, PEER_A)
        clock[0] = 1020.0
        s.touch("!aabb0044", PEER_B)
        assert s.expire_idle() == 1               # cap-evict the oldest
        assert s.lookup(MESH_A) is None
        assert s.active_count() == 2

    def test_message_count_upsert(self, store):
        store.touch(MESH_A, PEER_A)
        store.touch(MESH_A, PEER_A)
        rows = store.list_active()
        assert len(rows) == 1
        assert rows[0]["message_count"] == 2

    def test_normalization(self, store):
        assert store.touch("!AABB0042", PEER_A.upper()) is True
        assert store.lookup("!aabb0042") == PEER_A
        assert store.touch("aabb0042", PEER_A) is False   # missing !
        assert store.touch(MESH_A, "zz" * 16) is False    # non-hex peer
        assert store.touch(MESH_A, "ab" * 8) is False     # wrong length
        assert store.touch(None, PEER_A) is False

    def test_list_active_shape(self, store):
        store.touch(MESH_A, PEER_A)
        rows = store.list_active()
        assert set(rows[0].keys()) == {
            "mesh_id", "rns_peer_hash", "created_at",
            "last_activity", "message_count",
        }

    def test_expire_all_and_scoped(self, store):
        store.touch(MESH_A, PEER_A)
        store.touch(MESH_B, PEER_B)
        assert store.expire_all(MESH_A) == 1
        assert store.lookup(MESH_A) is None
        assert store.lookup(MESH_B) == PEER_B
        assert store.expire_all() == 1
        assert store.active_count() == 0


class TestSessionStoreSafety:

    def test_lazy_db_no_file_on_ctor(self, tmp_path):
        path = tmp_path / "lazy.db"
        s = SessionStore(db_path=str(path))
        assert s._initialized is False
        assert not path.exists()

    def test_first_use_opens_db(self, tmp_path):
        path = tmp_path / "lazy.db"
        s = SessionStore(db_path=str(path))
        s.touch(MESH_A, PEER_A)
        assert path.exists()

    def test_never_raises_on_bad_path(self):
        s = SessionStore(db_path="/nonexistent_dir/nope/x.db")
        assert s.touch(MESH_A, PEER_A) is False
        assert s.lookup(MESH_A) is None
        assert s.active_count() == 0
        assert s.expire_idle() == 0
        assert s.list_active() == []
        assert s.expire_all() == 0

    def test_magicmock_numerics_sanitized(self, tmp_path):
        s = SessionStore(db_path=str(tmp_path / "s.db"),
                         idle_timeout_sec=MagicMock(),
                         max_sessions=MagicMock())
        assert s._idle == DEFAULT_IDLE_TIMEOUT_SEC
        assert s._max == DEFAULT_MAX_SESSIONS
