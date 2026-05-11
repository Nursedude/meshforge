"""Contract tests for the MA-peer SLO snapshot shape.

MA's `/fleet/rollup` poller (see `MA src/monitoring/fleet_rollup.py:
_fetch_peer_snapshot`) expects this exact shape on `/fleet/slo`. If any
required key disappears or changes type, MA's rollup renders an empty
panel for the peer — silently. These tests are the contract that keeps
the cross-repo schema stable.
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from utils import fleet_snapshot
from utils.fleet_snapshot import (
    OPTIONAL_SERVICES,
    REQUIRED_SERVICES,
    _probe_radio,
    _services_rollup,
    _systemctl_state,
    build_slo_snapshot,
)


# ─── Shape contract ────────────────────────────────────────────────────


def test_snapshot_top_level_keys_match_ma_slo_view():
    snap = build_slo_snapshot()
    expected = {
        "generated_at", "host", "uptime_s", "overall_status",
        "services", "boundaries_top", "radio", "errors",
    }
    assert expected.issubset(snap.keys()), (
        f"missing required keys for MA peer contract: "
        f"{expected - snap.keys()}"
    )


def test_snapshot_types_match_ma_expectations():
    snap = build_slo_snapshot()
    assert isinstance(snap["generated_at"], float)
    assert isinstance(snap["host"], str) and snap["host"]
    assert isinstance(snap["uptime_s"], float)
    assert snap["overall_status"] in ("ready", "degraded")
    assert isinstance(snap["services"], dict)
    assert isinstance(snap["boundaries_top"], list)
    assert isinstance(snap["radio"], dict)
    assert isinstance(snap["errors"], list)


def test_services_block_has_required_and_optional_buckets():
    snap = build_slo_snapshot()
    s = snap["services"]
    for key in ("total", "available", "by_state", "required", "optional"):
        assert key in s, f"services.{key} missing"
    for bucket_name in ("required", "optional"):
        bucket = s[bucket_name]
        for key in ("total", "available", "by_state"):
            assert key in bucket, f"services.{bucket_name}.{key} missing"


def test_services_block_is_internally_consistent():
    """services.total == required.total + optional.total."""
    snap = build_slo_snapshot()
    s = snap["services"]
    assert s["total"] == s["required"]["total"] + s["optional"]["total"]
    assert s["available"] == s["required"]["available"] + s["optional"]["available"]


def test_required_total_matches_module_constant():
    snap = build_slo_snapshot()
    assert snap["services"]["required"]["total"] == len(REQUIRED_SERVICES)
    assert snap["services"]["optional"]["total"] == len(OPTIONAL_SERVICES)


def test_radio_block_shape_matches_ma_expectations():
    snap = build_slo_snapshot()
    r = snap["radio"]
    for key in ("connected", "name", "preset", "battery_pct"):
        assert key in r, f"radio.{key} missing"
    assert isinstance(r["connected"], bool)


def test_internal_detail_field_is_stripped_from_response():
    """`_detail` is an internal hint used to derive `errors`; never expose it."""
    snap = build_slo_snapshot()
    assert "_detail" not in snap["services"]


# ─── State derivation ──────────────────────────────────────────────────


def test_overall_status_ready_when_all_required_available():
    with patch.object(fleet_snapshot, "_systemctl_state", return_value="available"):
        snap = build_slo_snapshot()
    assert snap["overall_status"] == "ready"
    assert snap["errors"] == []


def test_overall_status_degraded_when_required_missing():
    def state(unit):
        return "not_running" if unit == "meshtasticd" else "available"
    with patch.object(fleet_snapshot, "_systemctl_state", side_effect=state):
        snap = build_slo_snapshot()
    assert snap["overall_status"] == "degraded"
    assert any("meshtasticd" in e for e in snap["errors"])


def test_optional_service_failure_does_not_demote_overall_status():
    def state(unit):
        if unit in REQUIRED_SERVICES:
            return "available"
        return "not_running"
    with patch.object(fleet_snapshot, "_systemctl_state", side_effect=state):
        snap = build_slo_snapshot()
    assert snap["overall_status"] == "ready"
    assert snap["errors"] == []  # only required-svc failures populate errors


# ─── Service probe robustness ──────────────────────────────────────────


def test_systemctl_state_handles_timeout():
    with patch("subprocess.run", side_effect=__import__("subprocess").TimeoutExpired(cmd="systemctl", timeout=3)):
        assert _systemctl_state("anything") == "not_running"


def test_systemctl_state_handles_missing_binary():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _systemctl_state("anything") == "not_running"


def test_systemctl_state_active_maps_to_available():
    mock_result = MagicMock()
    mock_result.stdout = "active\n"
    with patch("subprocess.run", return_value=mock_result):
        assert _systemctl_state("meshtasticd") == "available"


def test_systemctl_state_inactive_maps_to_not_running():
    mock_result = MagicMock()
    mock_result.stdout = "inactive\n"
    with patch("subprocess.run", return_value=mock_result):
        assert _systemctl_state("meshtasticd") == "not_running"


# ─── Radio probe ───────────────────────────────────────────────────────


def test_radio_probe_meshtasticd_listening():
    mock_sock = MagicMock()
    mock_sock.__enter__.return_value = mock_sock
    mock_sock.connect_ex.return_value = 0  # success
    with patch("socket.socket", return_value=mock_sock):
        r = _probe_radio()
    assert r["connected"] is True
    assert r["name"] == "meshtasticd"


def test_radio_probe_falls_back_to_meshcore_symlink():
    """No meshtasticd, but /dev/ttyMeshCore present → connected via MeshCore."""
    mock_sock = MagicMock()
    mock_sock.__enter__.return_value = mock_sock
    mock_sock.connect_ex.return_value = 1  # refused
    with patch("socket.socket", return_value=mock_sock), \
         patch("os.path.exists", return_value=True):
        r = _probe_radio()
    assert r["connected"] is True
    assert r["name"] == "meshcore"


def test_radio_probe_no_radio_returns_disconnected():
    mock_sock = MagicMock()
    mock_sock.__enter__.return_value = mock_sock
    mock_sock.connect_ex.return_value = 1
    with patch("socket.socket", return_value=mock_sock), \
         patch("os.path.exists", return_value=False):
        r = _probe_radio()
    assert r["connected"] is False
    assert r["name"] is None


# ─── Smoke ─────────────────────────────────────────────────────────────


def test_host_field_matches_socket_gethostname():
    snap = build_slo_snapshot()
    assert snap["host"] == socket.gethostname()


def test_uptime_s_is_nonneg_and_monotonic_between_calls():
    snap1 = build_slo_snapshot()
    snap2 = build_slo_snapshot()
    assert snap1["uptime_s"] >= 0
    assert snap2["uptime_s"] >= snap1["uptime_s"]


def test_uptime_s_reads_from_proc_not_module_load_time():
    """Lazy-importing the module must NOT set uptime to ~0.

    Regression test for the original module-level `time.monotonic()`
    reference: when the HTTP handler lazy-imports `fleet_snapshot`,
    the monotonic clock starts at first-request time, not daemon-start.
    The /proc-based reading is immune to import order.
    """
    snap = build_slo_snapshot()
    # A live daemon must have been up at least ~1s by the time it
    # serves /fleet/slo. If this is ~0, we regressed to the import-bug.
    # Test environment: process is the pytest worker, which has run
    # for at least the collection + setup time.
    assert snap["uptime_s"] > 0.5, (
        f"uptime_s={snap['uptime_s']:.3f}s — looks like the monotonic-"
        "at-import bug regressed. Read from /proc/self/stat instead."
    )


def test_boundaries_top_is_empty_phase_1():
    """MF doesn't instrument systemd boundaries yet. Empty is valid for MA."""
    snap = build_slo_snapshot()
    assert snap["boundaries_top"] == []
