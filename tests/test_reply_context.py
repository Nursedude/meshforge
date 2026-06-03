"""
Tests for ReplyContextStore — the Theme-A step-1 reply-context memory.

Covers: record/get round-trip, TTL lazy eviction, LRU cap + access
refresh, config-value sanitization (MagicMock/garbage → defaults), and a
thread-safety smoke (bridge loop records while LXMF receive path reads).
"""

import sys
import os
import threading
from unittest.mock import MagicMock

import pytest

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.reply_context import (
    ReplyContextStore,
    DEFAULT_TTL_SEC,
    DEFAULT_MAX_ENTRIES,
    _sanitize_positive,
)

PEER_A = "aa" * 16
PEER_B = "bb" * 16
PEER_C = "cc" * 16
TOKEN_A = "meshtastic:!aabb0042"
TOKEN_B = "meshtastic:!aabb0043"
TOKEN_C = "meshtastic:!aabb0044"


class TestReplyContextStore:

    def test_record_then_get(self):
        store = ReplyContextStore()
        store.record(PEER_A, TOKEN_A)
        assert store.get(PEER_A) == TOKEN_A
        assert len(store) == 1

    def test_get_missing_returns_none(self):
        store = ReplyContextStore()
        assert store.get(PEER_A) is None
        assert store.get("") is None

    def test_key_case_insensitive(self):
        store = ReplyContextStore()
        store.record(PEER_A.upper(), TOKEN_A)
        assert store.get(PEER_A) == TOKEN_A

    def test_record_overwrites_existing(self):
        store = ReplyContextStore()
        store.record(PEER_A, TOKEN_A)
        store.record(PEER_A, TOKEN_B)
        assert store.get(PEER_A) == TOKEN_B
        assert len(store) == 1

    def test_empty_inputs_ignored(self):
        store = ReplyContextStore()
        store.record("", TOKEN_A)
        store.record(PEER_A, "")
        assert len(store) == 0

    def test_ttl_lazy_expiry_on_get(self, monkeypatch):
        import gateway.reply_context as rc
        clock = [1000.0]
        monkeypatch.setattr(rc.time, "time", lambda: clock[0])
        store = ReplyContextStore(ttl_sec=60)
        store.record(PEER_A, TOKEN_A)
        clock[0] = 1059.0
        assert store.get(PEER_A) == TOKEN_A   # inside TTL
        clock[0] = 1061.0
        assert store.get(PEER_A) is None      # expired + purged
        assert len(store) == 0

    def test_record_purges_stale_entries(self, monkeypatch):
        import gateway.reply_context as rc
        clock = [1000.0]
        monkeypatch.setattr(rc.time, "time", lambda: clock[0])
        store = ReplyContextStore(ttl_sec=60)
        store.record(PEER_A, TOKEN_A)
        clock[0] = 2000.0  # PEER_A long stale
        store.record(PEER_B, TOKEN_B)
        assert len(store) == 1  # opportunistic purge dropped PEER_A
        assert store.get(PEER_B) == TOKEN_B

    def test_lru_cap_evicts_oldest(self):
        store = ReplyContextStore(max_entries=2)
        store.record(PEER_A, TOKEN_A)
        store.record(PEER_B, TOKEN_B)
        store.record(PEER_C, TOKEN_C)
        assert len(store) == 2
        assert store.get(PEER_A) is None
        assert store.get(PEER_B) == TOKEN_B
        assert store.get(PEER_C) == TOKEN_C

    def test_get_refreshes_lru_position(self):
        store = ReplyContextStore(max_entries=2)
        store.record(PEER_A, TOKEN_A)
        store.record(PEER_B, TOKEN_B)
        store.get(PEER_A)                  # refresh A — B becomes oldest
        store.record(PEER_C, TOKEN_C)
        assert store.get(PEER_A) == TOKEN_A
        assert store.get(PEER_B) is None   # evicted instead of A

    def test_clear(self):
        store = ReplyContextStore()
        store.record(PEER_A, TOKEN_A)
        store.clear()
        assert len(store) == 0
        assert store.get(PEER_A) is None

    def test_thread_safety_smoke(self):
        """8 threads record+get concurrently; no exception, cap holds."""
        store = ReplyContextStore(max_entries=32)
        errors = []

        def worker(idx):
            try:
                for i in range(200):
                    key = f"{idx:02x}{i % 16:02x}" + "00" * 14
                    store.record(key, f"meshtastic:!aa{idx:02x}{i % 16:04x}")
                    store.get(key)
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(store) <= 32


class TestConfigSanitization:
    """MagicMock test configs and operator-edited JSON must degrade safely."""

    def test_magicmock_args_fall_back_to_defaults(self):
        store = ReplyContextStore(
            ttl_sec=MagicMock(), max_entries=MagicMock())
        assert store._ttl_sec == DEFAULT_TTL_SEC
        assert store._max_entries == DEFAULT_MAX_ENTRIES

    def test_none_args_fall_back_to_defaults(self):
        store = ReplyContextStore(ttl_sec=None, max_entries=None)
        assert store._ttl_sec == DEFAULT_TTL_SEC
        assert store._max_entries == DEFAULT_MAX_ENTRIES

    def test_nonpositive_args_fall_back_to_defaults(self):
        store = ReplyContextStore(ttl_sec=0, max_entries=-5)
        assert store._ttl_sec == DEFAULT_TTL_SEC
        assert store._max_entries == DEFAULT_MAX_ENTRIES

    def test_bool_is_not_a_valid_numeric(self):
        # True would silently mean ttl=1s — reject bools explicitly.
        assert _sanitize_positive(True, 99) == 99

    def test_valid_values_kept(self):
        store = ReplyContextStore(ttl_sec=3600, max_entries=10)
        assert store._ttl_sec == 3600
        assert store._max_entries == 10
