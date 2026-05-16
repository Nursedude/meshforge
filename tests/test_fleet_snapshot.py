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
    SCHEDULE_STALE_MULTIPLIER,
    _normalize_timer,
    _probe_radio,
    _schedules_block,
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


# ─── Schedules block (T0 schedule-health) ──────────────────────────────


NOW = 1778870000.0  # reference instant for normalize tests


def test_normalize_timer_converts_microseconds_to_unix_seconds():
    raw = {
        "unit": "meshforge-tracer.timer",
        # 1778869400000000 µs = 1778869400.0 s (10 min before NOW)
        "last": 1778869400000000,
        "next": 1778870000000000,
    }
    entry = _normalize_timer(raw, "user", NOW)
    assert entry["name"] == "meshforge-tracer.timer"
    assert entry["scope"] == "user"
    assert entry["last_fire_unix"] == pytest.approx(1778869400.0)
    assert entry["next_fire_unix"] == pytest.approx(1778870000.0)
    assert entry["age_s"] == pytest.approx(600.0)
    assert entry["stale"] is False


def test_normalize_timer_zero_means_unset():
    """systemctl emits 0 for timers with no scheduled fire (the moc1 case)."""
    raw = {"unit": "broken.timer", "last": 1778869400000000, "next": 0}
    entry = _normalize_timer(raw, "user", NOW)
    assert entry["next_fire_unix"] is None
    assert entry["stale"] is True, (
        "next=None must flag stale — this is the moc1 18h-freeze signature"
    )


def test_normalize_timer_no_last_run_yet_not_stale():
    """Fresh boot before first fire: next set, last=0. Not stale."""
    raw = {"unit": "fresh.timer", "last": 0, "next": 1778870600000000}
    entry = _normalize_timer(raw, "system", NOW)
    assert entry["last_fire_unix"] is None
    assert entry["age_s"] is None
    assert entry["stale"] is False


def test_normalize_timer_flags_stale_when_age_exceeds_2x_interval():
    """If next-last = 600s (10min interval) and age > 1200s, flag red."""
    raw = {
        "unit": "lagging.timer",
        "last": int((NOW - 1500) * 1_000_000),  # 25 min ago
        "next": int((NOW - 900) * 1_000_000),   # 15 min ago — overdue
    }
    entry = _normalize_timer(raw, "user", NOW)
    assert entry["age_s"] == pytest.approx(1500.0)
    # interval = next - last = 600s; age=1500s > 2×600 ⇒ stale
    assert entry["stale"] is True


def test_normalize_timer_not_stale_when_age_under_2x_interval():
    raw = {
        "unit": "ok.timer",
        "last": int((NOW - 700) * 1_000_000),
        "next": int((NOW + 500) * 1_000_000),  # interval = 1200s
    }
    entry = _normalize_timer(raw, "system", NOW)
    # age=700, interval=1200, 2× = 2400 → not stale
    assert entry["stale"] is False


def test_normalize_timer_rejects_missing_unit():
    assert _normalize_timer({}, "system", NOW) is None
    assert _normalize_timer({"unit": ""}, "system", NOW) is None


def test_normalize_timer_handles_garbage_us_values():
    raw = {"unit": "garbage.timer", "last": "not-a-number", "next": None}
    entry = _normalize_timer(raw, "user", NOW)
    assert entry["last_fire_unix"] is None
    assert entry["next_fire_unix"] is None
    assert entry["stale"] is True  # next=None ⇒ stale


def test_schedules_block_filters_to_fleet_prefixes(monkeypatch):
    """OS timers like apt-daily.timer must not appear in the panel."""
    fake_system = [
        {"unit": "apt-daily.timer", "last": int(NOW * 1e6),
         "next": int((NOW + 86400) * 1e6)},
        {"unit": "meshforge-backup.timer", "last": int(NOW * 1e6),
         "next": int((NOW + 3600) * 1e6)},
    ]
    fake_user = [
        {"unit": "meshforge-tracer.timer",
         "last": int((NOW - 60) * 1e6), "next": int((NOW + 540) * 1e6)},
    ]

    def _fake_list(scope):
        return fake_system if scope == "system" else fake_user
    monkeypatch.setattr(fleet_snapshot, "_list_timers_scope", _fake_list)
    monkeypatch.setattr(fleet_snapshot.time, "time", lambda: NOW)

    block = _schedules_block()
    names = [u["name"] for u in block["units"]]
    assert "apt-daily.timer" not in names, "OS timer leaked into fleet panel"
    assert "meshforge-backup.timer" in names
    assert "meshforge-tracer.timer" in names


def test_schedules_block_stale_first_then_alphabetical(monkeypatch):
    """Operator scan-order: red badges surface together at the top."""
    fake_user = [
        {"unit": "meshforge-z-healthy.timer",
         "last": int((NOW - 60) * 1e6), "next": int((NOW + 540) * 1e6)},
        {"unit": "meshforge-a-stale.timer",
         "last": int((NOW - 60) * 1e6), "next": 0},
        {"unit": "meshforge-m-healthy.timer",
         "last": int((NOW - 60) * 1e6), "next": int((NOW + 540) * 1e6)},
    ]
    monkeypatch.setattr(
        fleet_snapshot, "_list_timers_scope",
        lambda scope: fake_user if scope == "user" else [],
    )
    monkeypatch.setattr(fleet_snapshot.time, "time", lambda: NOW)
    block = _schedules_block()
    names = [u["name"] for u in block["units"]]
    assert names[0] == "meshforge-a-stale.timer", (
        f"stale must surface first; got {names}"
    )
    assert names[1:] == [
        "meshforge-m-healthy.timer", "meshforge-z-healthy.timer",
    ]


def test_schedules_block_healthy_when_no_stale(monkeypatch):
    monkeypatch.setattr(
        fleet_snapshot, "_list_timers_scope",
        lambda scope: [
            {"unit": "meshforge-backup.timer",
             "last": int((NOW - 60) * 1e6),
             "next": int((NOW + 540) * 1e6)},
        ] if scope == "system" else [],
    )
    monkeypatch.setattr(fleet_snapshot.time, "time", lambda: NOW)
    block = _schedules_block()
    assert block["healthy"] is True
    assert block["stale_count"] == 0


def test_schedules_block_unhealthy_when_any_stale(monkeypatch):
    monkeypatch.setattr(
        fleet_snapshot, "_list_timers_scope",
        lambda scope: [
            {"unit": "meshforge-tracer.timer",
             "last": int((NOW - 18 * 3600) * 1e6),  # 18h ago, the moc1 case
             "next": 0},
        ] if scope == "user" else [],
    )
    monkeypatch.setattr(fleet_snapshot.time, "time", lambda: NOW)
    block = _schedules_block()
    assert block["healthy"] is False
    assert block["stale_count"] == 1
    assert block["units"][0]["age_s"] == pytest.approx(64800.0)


def test_snapshot_includes_schedules_block():
    snap = build_slo_snapshot()
    assert "schedules" in snap
    assert "healthy" in snap["schedules"]
    assert "stale_count" in snap["schedules"]
    assert "units" in snap["schedules"]
    assert isinstance(snap["schedules"]["units"], list)


def test_schedule_stale_multiplier_is_documented():
    """The constant exists for tunability; this test guards against
    accidentally shifting the heuristic without operator awareness."""
    assert SCHEDULE_STALE_MULTIPLIER == 2.0


# ─── _list_timers_scope: root→operator drop (mirror of fire_unit fix) ──
#
# The map daemon on meshanchor-server runs as User=root; root has no
# /run/user/0/bus, so `systemctl --user list-timers` from root sees
# nothing. _list_timers_scope drops privilege to the operator user
# the same way fire_unit does in c6d7609.


def _capture_subprocess_run(returncode: int = 0, stdout: str = "[]"):
    captured = {}

    def _fake(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return MagicMock(returncode=returncode, stdout=stdout, stderr="")

    return _fake, captured


def test_list_timers_non_root_user_scope_injects_xdg(monkeypatch):
    """Existing daemon-context fix path: non-root daemon (e.g. meshforge-map
    on the MF fleet boxes runs as wh6gxz) injects XDG_RUNTIME_DIR and
    calls plain `systemctl --user list-timers ...`."""
    fake, cap = _capture_subprocess_run()
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)
    monkeypatch.setattr(
        "utils.fleet_snapshot.os.environ",
        {k: v for k, v in __import__("os").environ.items() if k != "XDG_RUNTIME_DIR"},
    )
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", fake)
    fleet_snapshot._list_timers_scope("user")
    assert cap["cmd"][0] == "systemctl"
    assert "--user" in cap["cmd"]
    assert "sudo" not in cap["cmd"]
    assert cap["env"]["XDG_RUNTIME_DIR"] == "/run/user/1000"


def test_list_timers_root_user_scope_drops_to_operator(monkeypatch):
    """Root + user scope: same sudo -n -u <op> env XDG_RUNTIME_DIR pattern
    as fire_unit. Operator UID/name resolved via _find_operator_user.
    Closes the meshanchor-server schedules-panel under-report (only
    system timers showed because root's bus is empty)."""
    fake, cap = _capture_subprocess_run()
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "utils.fleet_test_runner._find_operator_user",
        lambda: (1000, "wh6gxz"),
    )
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", fake)
    fleet_snapshot._list_timers_scope("user")
    assert cap["cmd"][0] == "sudo"
    assert "-n" in cap["cmd"]
    assert cap["cmd"][cap["cmd"].index("-u") + 1] == "wh6gxz"
    env_idx = cap["cmd"].index("env")
    assert cap["cmd"][env_idx + 1] == "XDG_RUNTIME_DIR=/run/user/1000"
    assert "systemctl" in cap["cmd"]
    assert "--user" in cap["cmd"]
    assert "list-timers" in cap["cmd"]


def test_list_timers_root_system_scope_stays_plain(monkeypatch):
    """Root + system scope is the normal path. No drop. The drop only
    applies to user-scope reads."""
    fake, cap = _capture_subprocess_run()
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 0)
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", fake)
    fleet_snapshot._list_timers_scope("system")
    assert cap["cmd"][0] == "systemctl"
    assert "--user" not in cap["cmd"]
    assert "sudo" not in cap["cmd"]


def test_list_timers_root_user_scope_no_operator_returns_empty(monkeypatch):
    """If /run/user/ has no candidate UID, return [] rather than letting
    root's `systemctl --user` produce a cryptic bus error and bubble
    up as a stale schedules block."""

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be invoked")

    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "utils.fleet_test_runner._find_operator_user", lambda: None,
    )
    monkeypatch.setattr(
        "utils.fleet_snapshot.subprocess.run", _should_not_be_called,
    )
    assert fleet_snapshot._list_timers_scope("user") == []
