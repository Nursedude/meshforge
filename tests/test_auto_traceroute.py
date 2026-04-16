"""
Tests for auto-traceroute features:
- TracerouteStore (SQLite persistence)
- TracerouteResult formatting
- AutomationEngine on-demand traceroute
- Auto-discovery of active nodes
- Protobuf/CLI fallback logic
- Node ID validation
"""

import json
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# --- TracerouteResult tests ---

class TestTracerouteResult:
    """Test TracerouteResult dataclass formatting."""

    def _make_result(self, **kwargs):
        from utils.automation_engine import TracerouteResult
        defaults = {
            "node_id": "!abc12345",
            "timestamp": datetime(2026, 3, 5, 14, 30, 0),
            "success": True,
            "hops": 2,
        }
        defaults.update(kwargs)
        return TracerouteResult(**defaults)

    def test_format_route_with_hops(self):
        r = self._make_result(
            route=[0xDEF456, 0xABC123],
            snr_towards=[-5.0, -8.2],
        )
        formatted = r.format_route()
        assert "Local" in formatted
        assert "!00def456" in formatted
        assert "(-5.0dB)" in formatted

    def test_format_route_empty(self):
        r = self._make_result(route=[], output="cli output here")
        assert r.format_route() == "cli output here"

    def test_format_route_no_data(self):
        r = self._make_result(route=[], output="")
        assert r.format_route() == "(no route data)"

    def test_format_return_route(self):
        r = self._make_result(
            route_back=[0xABC123, 0xDEF456],
            snr_back=[-6.0, -4.5],
        )
        formatted = r.format_return_route()
        assert "Local" in formatted
        assert "(-6.0dB)" in formatted

    def test_format_return_route_empty(self):
        r = self._make_result(route_back=[])
        assert r.format_return_route() == "(no return route)"

    def test_format_log_line_success(self):
        r = self._make_result(
            node_name="Hilltop",
            route=[0xDEF456],
            snr_towards=[-5.0],
        )
        line = r.format_log_line()
        assert "TRACEROUTE !abc12345 (Hilltop)" in line
        assert "OK" in line
        assert "2 hops" in line

    def test_format_log_line_failure(self):
        r = self._make_result(success=False, error="Timeout after 60s")
        line = r.format_log_line()
        assert "FAIL" in line
        assert "Timeout" in line


# --- TracerouteStore tests ---

class TestTracerouteStore:
    """Test SQLite-backed persistent storage."""

    @pytest.fixture
    def store(self, tmp_path):
        from utils.automation_engine import TracerouteStore
        db_path = tmp_path / "test_traceroute.db"
        return TracerouteStore(db_path=db_path)

    @pytest.fixture
    def sample_result(self):
        from utils.automation_engine import TracerouteResult
        return TracerouteResult(
            node_id="!abc12345",
            timestamp=datetime(2026, 3, 5, 14, 30, 0),
            success=True,
            hops=3,
            node_name="TestNode",
            route=[0xDEF456, 0x789ABC],
            snr_towards=[-5.0, -8.2],
            route_back=[0x789ABC, 0xDEF456],
            snr_back=[-6.0, -4.5],
        )

    def test_init_creates_table(self, store):
        """Database table should be created on init."""
        conn = sqlite3.connect(store._db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        assert "traceroute_results" in tables

    def test_store_and_retrieve(self, store, sample_result):
        """Store a result and retrieve it."""
        store.store(sample_result)
        results = store.get_recent(limit=10)
        assert len(results) == 1
        r = results[0]
        assert r["node_id"] == "!abc12345"
        assert r["success"] is True
        assert r["hops"] == 3
        assert r["node_name"] == "TestNode"

    def test_get_recent_ordering(self, store):
        """Results should be ordered newest first."""
        from utils.automation_engine import TracerouteResult
        for i in range(5):
            store.store(TracerouteResult(
                node_id=f"!node{i:04d}",
                timestamp=datetime(2026, 3, 5, 14, i, 0),
                success=True,
                hops=i,
            ))
        results = store.get_recent(limit=3)
        assert len(results) == 3
        # Newest first
        assert results[0]["node_id"] == "!node0004"
        assert results[2]["node_id"] == "!node0002"

    def test_get_for_node(self, store):
        """Retrieve history for a specific node."""
        from utils.automation_engine import TracerouteResult
        for i in range(3):
            store.store(TracerouteResult(
                node_id="!target01",
                timestamp=datetime(2026, 3, 5, 14, i, 0),
                success=True,
                hops=i,
            ))
        store.store(TracerouteResult(
            node_id="!other001",
            timestamp=datetime(2026, 3, 5, 14, 10, 0),
            success=True,
        ))
        results = store.get_for_node("!target01")
        assert len(results) == 3
        assert all(r["node_id"] == "!target01" for r in results)

    def test_get_summary(self, store):
        """Per-node summary with success rates."""
        from utils.automation_engine import TracerouteResult
        # 2 successes, 1 failure for node A
        for success in [True, True, False]:
            store.store(TracerouteResult(
                node_id="!nodeaaaa",
                timestamp=datetime.now(),
                success=success,
                hops=2 if success else 0,
                node_name="NodeA",
            ))
        summary = store.get_summary()
        assert len(summary) == 1
        s = summary[0]
        assert s["node_id"] == "!nodeaaaa"
        assert s["total"] == 3
        assert s["successes"] == 2
        assert 60 < s["success_rate"] < 70  # ~66.7%
        assert s["avg_hops"] == 2.0

    def test_prune_old_entries(self, store):
        """Prune should remove entries older than threshold."""
        from utils.automation_engine import TracerouteResult
        old_time = datetime.now() - timedelta(days=60)
        recent_time = datetime.now()

        store.store(TracerouteResult(
            node_id="!old00001",
            timestamp=old_time,
            success=True,
        ))
        store.store(TracerouteResult(
            node_id="!new00001",
            timestamp=recent_time,
            success=True,
        ))

        removed = store.prune(days=30)
        assert removed == 1

        results = store.get_recent()
        assert len(results) == 1
        assert results[0]["node_id"] == "!new00001"

    def test_json_fields_parsed(self, store, sample_result):
        """JSON fields should be parsed into lists."""
        store.store(sample_result)
        results = store.get_recent()
        r = results[0]
        assert isinstance(r["route_json"], list)
        assert isinstance(r["snr_towards_json"], list)
        assert len(r["route_json"]) == 2
        assert len(r["snr_towards_json"]) == 2

    def test_empty_store(self, store):
        """Empty store should return empty lists."""
        assert store.get_recent() == []
        assert store.get_for_node("!abc") == []
        assert store.get_summary() == []


# --- Node ID validation ---

class TestNodeIdValidation:
    """Test node ID validation and conversion."""

    def test_valid_node_ids(self):
        from utils.automation_engine import validate_node_id
        assert validate_node_id("!abc12345")
        assert validate_node_id("!ABCDEF00")
        assert validate_node_id("!1")
        assert validate_node_id("!abcd1234")

    def test_invalid_node_ids(self):
        from utils.automation_engine import validate_node_id
        assert not validate_node_id("")
        assert not validate_node_id("abc12345")  # missing !
        assert not validate_node_id("!xyz")  # non-hex
        assert not validate_node_id("!123456789")  # too long (9 digits)
        assert not validate_node_id("!")  # empty after !

    def test_node_id_to_int(self):
        from utils.automation_engine import _node_id_to_int
        assert _node_id_to_int("!abc12345") == 0xABC12345
        assert _node_id_to_int("!1") == 1
        assert _node_id_to_int("!FF") == 255

    def test_node_id_to_int_invalid(self):
        from utils.automation_engine import _node_id_to_int
        assert _node_id_to_int("invalid") is None
        assert _node_id_to_int("") is None
        assert _node_id_to_int(None) is None


# --- AutomationEngine tests ---

class TestAutomationEngineTraceroute:
    """Test AutomationEngine traceroute functionality."""

    @pytest.fixture
    def engine(self, tmp_path):
        """Create an engine with temp storage."""
        with patch(
            'utils.automation_engine._get_traceroute_db_path',
            return_value=tmp_path / "test.db",
        ), patch(
            'utils.automation_engine._get_traceroute_log_path',
            return_value=tmp_path / "traceroute.log",
        ):
            from utils.automation_engine import AutomationEngine
            return AutomationEngine()

    def test_run_single_traceroute_invalid_id(self, engine):
        """Invalid node ID should return error result."""
        result = engine.run_single_traceroute("invalid")
        assert not result.success
        assert "Invalid" in result.error

    @patch('utils.automation_engine.subprocess.run')
    def test_run_single_traceroute_cli_fallback(self, mock_run, engine):
        """Should fall back to CLI when protobuf unavailable."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Route to !abc12345: !def456 -> !abc12345",
            stderr="",
        )

        # Ensure protobuf client is unavailable
        with patch.dict(
            'utils.automation_engine.__dict__',
            {'_HAS_PROTOBUF_CLIENT': False},
        ):
            result = engine.run_single_traceroute("!abc12345")

        assert result.success or mock_run.called

    @patch('utils.automation_engine.subprocess.run')
    def test_run_single_traceroute_timeout(self, mock_run, engine):
        """CLI timeout should return failure result."""
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd="meshtastic", timeout=60)

        with patch.dict(
            'utils.automation_engine.__dict__',
            {'_HAS_PROTOBUF_CLIENT': False},
        ):
            result = engine.run_single_traceroute("!abc12345")

        assert not result.success
        assert "Timeout" in result.error

    def test_discover_active_nodes_no_inventory(self, engine):
        """Discovery should return empty when inventory unavailable."""
        with patch.dict(
            'utils.automation_engine.__dict__',
            {'_HAS_NODE_INVENTORY': False},
        ):
            nodes = engine._discover_active_nodes()
        assert nodes == []

    def test_discover_active_nodes_with_inventory(self, engine):
        """Discovery should return node IDs from inventory."""
        mock_inv = MagicMock()
        mock_node1 = MagicMock(node_id="!aaa11111")
        mock_node2 = MagicMock(node_id="!bbb22222")
        mock_inv.return_value.get_online_nodes.return_value = [
            mock_node1, mock_node2,
        ]

        with patch.dict(
            'utils.automation_engine.__dict__',
            {'_HAS_NODE_INVENTORY': True, '_NodeInventory': mock_inv},
        ):
            nodes = engine._discover_active_nodes()

        assert nodes == ["!aaa11111", "!bbb22222"]

    def test_get_traceroute_store(self, engine):
        """Engine should expose the store."""
        store = engine.get_traceroute_store()
        assert store is not None
        assert hasattr(store, 'get_recent')
        assert hasattr(store, 'store')

    def test_record_traceroute_persists(self, engine):
        """Recording should persist to SQLite."""
        from utils.automation_engine import TracerouteResult
        result = TracerouteResult(
            node_id="!abc12345",
            timestamp=datetime.now(),
            success=True,
            hops=2,
        )
        engine._record_traceroute(result)

        stored = engine.get_traceroute_store().get_recent()
        assert len(stored) == 1
        assert stored[0]["node_id"] == "!abc12345"


# --- Traceroute log tests ---

class TestTracerouteLogging:
    """Test traceroute log file setup."""

    def test_log_path_uses_real_home(self, tmp_path):
        fake_home = tmp_path / "testuser"
        fake_home.mkdir()
        with patch(
            'utils.automation_engine.get_real_user_home',
            return_value=fake_home,
        ):
            from utils.automation_engine import _get_traceroute_log_path
            path = _get_traceroute_log_path()
        assert str(fake_home) in str(path)
        assert "traceroute.log" in str(path)

    def test_db_path_uses_real_home(self, tmp_path):
        fake_home = tmp_path / "testuser"
        fake_home.mkdir()
        with patch(
            'utils.automation_engine.get_real_user_home',
            return_value=fake_home,
        ):
            from utils.automation_engine import _get_traceroute_db_path
            path = _get_traceroute_db_path()
        assert str(fake_home) in str(path)
        assert "traceroute_history.db" in str(path)
