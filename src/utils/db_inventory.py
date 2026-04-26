"""DB inventory — single source of truth for every SQLite consumer.

Why this exists: with 13+ SQLite DBs across MeshForge, an ad-hoc audit
on each new DB is going to miss the same way Phase 1 missed
health_state.db. This registry is what the audit script + the
`tests/test_db_inventory.py::test_every_runtime_db_in_inventory` test
read from. Adding a new DB without updating this file fails the test.

Usage:
    from utils.db_inventory import INVENTORY, DBSpec
    for spec in INVENTORY:
        path = spec.path_factory()
        ...

To add a new DB: append a DBSpec with the runtime path factory + the
expected pragmas. The audit runs `connect_tuned(path)` and asserts
`PRAGMA journal_mode/synchronous/journal_size_limit` match.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from utils.paths import get_real_user_home


@dataclass(frozen=True)
class DBSpec:
    """Contract for a single SQLite database in MeshForge."""
    name: str                              # short name, used in audit output
    path_factory: Callable[[], Path]       # returns the runtime path
    creator_module: str                    # module that owns the DB
    has_auto_prune: bool                   # is retention enforced automatically?
    retention_days: Optional[int] = None   # None if not time-based
    expected_journal_mode: str = "wal"
    expected_synchronous: int = 1          # NORMAL
    expected_journal_size_limit: int = 67_108_864  # 64 MB
    # Optional pragma overrides for DBs that intentionally diverge
    # (e.g. auto_vacuum=INCREMENTAL — order-sensitive, not via helper).
    pragma_overrides: Dict[str, object] = field(default_factory=dict)
    # Optional notes for operators (shown in TUI / verbose audit).
    notes: str = ""


def _meshforge_data_dir() -> Path:
    return get_real_user_home() / ".local" / "share" / "meshforge"


def _meshforge_config_dir() -> Path:
    return get_real_user_home() / ".config" / "meshforge"


def _meshforge_cache_dir() -> Path:
    return get_real_user_home() / ".cache" / "meshforge"


INVENTORY: List[DBSpec] = [
    DBSpec(
        name="node_history",
        path_factory=lambda: _meshforge_data_dir() / "node_history.db",
        creator_module="utils.node_history",
        has_auto_prune=True,
        retention_days=7,
        notes="Per-node trajectory observations. Hourly auto-prune.",
    ),
    DBSpec(
        name="messages",
        path_factory=lambda: _meshforge_config_dir() / "messages.db",
        creator_module="commands.messaging",
        has_auto_prune=True,
        retention_days=30,
        notes="Mesh message history. Hourly auto-prune from store_incoming.",
    ),
    DBSpec(
        name="analytics",
        path_factory=lambda: _meshforge_config_dir() / "analytics.db",
        creator_module="utils.analytics",
        has_auto_prune=True,
        retention_days=30,
        notes="Link budget + network health snapshots.",
    ),
    DBSpec(
        name="traffic_capture",
        path_factory=lambda: _meshforge_config_dir() / "traffic_capture.db",
        creator_module="monitoring.traffic_storage",
        has_auto_prune=True,
        retention_days=1,  # 24 hours
        notes="Packet capture. Time-based (24h) + row-cap (10k) retention.",
    ),
    DBSpec(
        name="offline_sync",
        path_factory=lambda: _meshforge_data_dir() / "offline_sync.db",
        creator_module="utils.offline_sync",
        has_auto_prune=True,
        retention_days=3,  # 72 hours
        notes="Offline event queue. 72h retention on synced/dead records.",
    ),
    DBSpec(
        name="diagnostic_history",
        path_factory=lambda: _meshforge_config_dir() / "diagnostic_history.db",
        creator_module="utils.diagnostic_engine",
        has_auto_prune=True,
        retention_days=30,
        notes="Diagnostic engine output. Hourly auto-prune from _save_diagnosis.",
    ),
    DBSpec(
        name="health_state",
        path_factory=lambda: _meshforge_config_dir() / "health_state.db",
        creator_module="utils.shared_health_state",
        has_auto_prune=True,
        retention_days=7,
        notes="Service health state + latency samples. Hourly auto-purge.",
    ),
    DBSpec(
        name="traceroute_history",
        path_factory=lambda: _meshforge_data_dir() / "traceroute_history.db",
        creator_module="utils.automation_engine",
        has_auto_prune=True,
        retention_days=30,
        notes="Auto-traceroute results. Startup prune + after-store prune.",
    ),
    DBSpec(
        name="tactical_timeline",
        path_factory=lambda: _meshforge_config_dir() / "tactical_timeline.db",
        creator_module="tactical.timeline",
        has_auto_prune=False,
        retention_days=None,
        notes="Tactical X1 events. No auto-prune (low write rate; bounded).",
    ),
    DBSpec(
        name="topology_history",
        path_factory=lambda: _meshforge_config_dir() / "topology_history.db",
        creator_module="utils.topology_snapshot",
        has_auto_prune=False,
        retention_days=None,
        notes="Periodic topology snapshots. No auto-prune currently.",
    ),
    DBSpec(
        name="metrics",
        path_factory=lambda: _meshforge_cache_dir() / "metrics.db",
        creator_module="utils.metrics_history",
        has_auto_prune=True,
        retention_days=None,  # bounded by row cap, not time
        notes="Telemetry metrics. Background cleanup ~60s.",
    ),
    DBSpec(
        name="contact_mapping",
        path_factory=lambda: _meshforge_config_dir() / "contact_mapping.db",
        creator_module="gateway.contact_mapping",
        has_auto_prune=False,
        retention_days=None,
        notes="Gateway contact map. Small, bounded.",
    ),
    DBSpec(
        name="message_queue",
        path_factory=lambda: _meshforge_config_dir() / "message_queue.db",
        creator_module="gateway.message_queue",
        has_auto_prune=True,
        retention_days=None,  # bounded by retry policy + dead-letter
        notes="Gateway retry queue. Bounded by 5-retry dead-letter policy.",
    ),
    DBSpec(
        name="packet_archive",
        path_factory=lambda: _meshforge_config_dir() / "packet_archive.db",
        creator_module="monitoring.traffic_storage",
        has_auto_prune=True,
        retention_days=None,  # row-cap based, sibling to traffic_capture
        notes="PacketArchive — long-form packet retention with manual cleanup.",
    ),
]


def find_spec(name: str) -> Optional[DBSpec]:
    """Look up a DBSpec by name."""
    for spec in INVENTORY:
        if spec.name == name:
            return spec
    return None
