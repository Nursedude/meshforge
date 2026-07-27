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
    CI_STATUS_STALE_AFTER_S,
    OPTIONAL_SERVICES,
    REQUIRED_SERVICES,
    SCHEDULE_STALE_MULTIPLIER,
    _SYSTEMCTL_STATE_TTL_S,
    _ci_overall,
    _ci_status_block,
    _normalize_timer,
    _parse_ci_status_file,
    _probe_radio,
    _probe_services_parallel,
    _schedules_block,
    _services_rollup,
    _systemctl_state,
    _systemctl_state_uncached,
    build_slo_snapshot,
)


@pytest.fixture(autouse=True)
def _clear_systemctl_cache():
    """Reset the module-level TTL cache between tests so subprocess
    mocks aren't shadowed by a previous test's cached result.
    Without this, test_systemctl_state_handles_timeout (unit="anything")
    poisons test_systemctl_state_handles_missing_binary (also "anything")
    and the second test never reaches its subprocess.run patch."""
    fleet_snapshot._systemctl_state_cache.clear()
    yield
    fleet_snapshot._systemctl_state_cache.clear()


@pytest.fixture(autouse=True)
def _neutral_watchdog_block(monkeypatch):
    """Decouple ``build_slo_snapshot()``'s ``overall_status`` from the HOST's
    real ``/var/lib/meshforge/watchdog.json``.

    ``overall_status`` folds in three host-coupled inputs — required-service
    probes, the cascade-detector singleton, and the watchdog file. The service
    tests patch ``_systemctl_state`` and the cascade tests patch
    ``get_singleton``/``_services_rollup``, but nothing pinned the watchdog
    block, so on a live fleet box (the federator's watchdog.json is routinely
    ``ok:false`` with wedge signals) ``_watchdog_block()`` silently demoted
    ``overall_status`` to ``degraded`` — making the ``…_ready…`` /
    ``…_stays_ready…`` tests pass only on a clean CI container and fail on a
    real host. That host coupling — NOT cross-test leakage — was the
    "flakiness". Pin it to the neutral not-installed shape (exactly what a box
    with no watchdog yields, which is the state CI tested against);
    ``test_overall_status_degraded_on_watchdog_wedge`` overrides it to cover
    the demotion branch explicitly.
    """
    monkeypatch.setattr(
        fleet_snapshot, "_watchdog_block",
        lambda: {"installed": False, "reason": "no_state_file"},
    )
    yield


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


# ─── TTL cache + parallel probe (2026-05-17 latency fix) ───────────────


class TestSystemctlStateCache:
    """The /fleet/slo handler ran 6 serial `systemctl is-active` calls;
    on Pi-class hardware that pushed end-to-end response time to 2.43 s —
    19% headroom under MA's 3 s peer-fetch timeout. Cache + parallel
    fanout brings it back to a comfortable margin. These tests lock in
    the contract."""

    def test_cache_hit_skips_subprocess(self):
        """Same unit within TTL → second call must not fork."""
        mock_result = MagicMock()
        mock_result.stdout = "active\n"
        with patch("subprocess.run", return_value=mock_result) as run:
            r1 = _systemctl_state("svc-x")
            r2 = _systemctl_state("svc-x")
        assert r1 == r2 == "available"
        assert run.call_count == 1, (
            "second call within TTL should hit the cache, not fork systemctl"
        )

    def test_cache_per_unit_independent(self):
        """Different units must not alias each other in the cache."""
        results = iter(["active\n", "inactive\n"])

        def fake(*a, **kw):
            mock = MagicMock()
            mock.stdout = next(results)
            return mock

        with patch("subprocess.run", side_effect=fake):
            assert _systemctl_state("svc-a") == "available"
            assert _systemctl_state("svc-b") == "not_running"

    def test_ttl_zero_bypasses_cache(self):
        """ttl_s=0 disables caching — used by tests that want a fresh
        subprocess call every time without managing the cache directly."""
        mock_result = MagicMock()
        mock_result.stdout = "active\n"
        with patch("subprocess.run", return_value=mock_result) as run:
            _systemctl_state("svc-z", ttl_s=0)
            _systemctl_state("svc-z", ttl_s=0)
        assert run.call_count == 2

    def test_ttl_expiry_re_forks(self):
        """After the TTL elapses, the cache MUST re-fork — otherwise a
        crashed daemon would show 'available' indefinitely. Drive
        time.monotonic() to simulate elapsed time without sleeping."""
        mock_result = MagicMock()
        mock_result.stdout = "active\n"
        clock = {"now": 1000.0}
        with patch("subprocess.run", return_value=mock_result) as run, \
             patch("utils.fleet_snapshot.time.monotonic",
                   side_effect=lambda: clock["now"]):
            _systemctl_state("svc-aged", ttl_s=2.0)
            clock["now"] += 5.0  # > TTL
            _systemctl_state("svc-aged", ttl_s=2.0)
        assert run.call_count == 2, "expired cache entry must re-fork"

    def test_default_ttl_is_two_seconds(self):
        """Lock in the 2 s default. Bumping it would coalesce more polls
        but risk surfacing stale service state to the MA dashboard;
        lowering it defeats the purpose. Either is a deliberate change."""
        assert _SYSTEMCTL_STATE_TTL_S == 2.0

    def test_uncached_helper_exists_for_direct_invocation(self):
        """`_systemctl_state_uncached` is the bypass primitive — tests
        and any future internal caller that wants a guaranteed fresh
        result should use it directly rather than passing ttl_s=0."""
        mock_result = MagicMock()
        mock_result.stdout = "inactive\n"
        with patch("subprocess.run", return_value=mock_result):
            assert _systemctl_state_uncached("svc-direct") == "not_running"


class TestProbeServicesParallel:
    """The parallel fanout — its whole purpose is to keep total wall
    time at max(unit_cost) instead of sum(unit_cost)."""

    def test_returns_state_for_every_unit(self):
        """Output dict must cover every input unit exactly once."""
        with patch("utils.fleet_snapshot._systemctl_state",
                   return_value="available"):
            out = _probe_services_parallel(("a", "b", "c", "d"))
        assert set(out.keys()) == {"a", "b", "c", "d"}
        assert all(v == "available" for v in out.values())

    def test_empty_input_returns_empty_dict(self):
        """Edge case: zero units → empty dict, not a spawned executor."""
        assert _probe_services_parallel(()) == {}

    def test_per_unit_results_are_distinct(self):
        """A failing service must not bleed its state into a healthy one."""
        def state_for(unit):
            return "available" if unit == "ok-svc" else "not_running"

        with patch("utils.fleet_snapshot._systemctl_state",
                   side_effect=state_for):
            out = _probe_services_parallel(("ok-svc", "broken-svc"))
        assert out == {"ok-svc": "available", "broken-svc": "not_running"}

    def test_worker_exception_does_not_corrupt_dict(self):
        """Defense-in-depth: if `_systemctl_state` ever stops swallowing
        its own errors (refactor regression), the parallel fanout must
        still produce a complete dict — missing units would crash the
        `_services_rollup` consumer that does `req_states[svc]`."""
        def state_for(unit):
            if unit == "boom":
                raise RuntimeError("simulated future-refactor leak")
            return "available"

        with patch("utils.fleet_snapshot._systemctl_state",
                   side_effect=state_for):
            out = _probe_services_parallel(("safe", "boom"))
        assert set(out.keys()) == {"safe", "boom"}
        assert out["safe"] == "available"
        assert out["boom"] == "not_running"

    def test_services_rollup_uses_parallel_probe(self):
        """End-to-end: `_services_rollup` must drive the parallel path,
        not regress to the old serial dict-comprehension. We assert by
        intercepting the parallel helper — if `_services_rollup` ever
        bypasses it, the captured call list goes empty."""
        captured: list = []

        def fake_parallel(units):
            captured.append(units)
            return {u: "available" for u in units}

        with patch("utils.fleet_snapshot._probe_services_parallel",
                   side_effect=fake_parallel):
            rollup = _services_rollup()

        assert len(captured) == 1
        assert set(captured[0]) == set(REQUIRED_SERVICES + OPTIONAL_SERVICES)
        assert rollup["available"] == rollup["total"]


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


def test_normalize_timer_unset_next_and_old_last_is_stale():
    """The real moc1 signature: NEXT unset AND `last` fire ~18h ago.
    A genuinely wedged timer — must flag stale."""
    raw = {
        "unit": "broken.timer",
        "last": int((NOW - 18 * 3600) * 1_000_000),  # 18h ago, like moc1
        "next": 0,
    }
    entry = _normalize_timer(raw, "user", NOW)
    assert entry["next_fire_unix"] is None
    assert entry["stale"] is True, (
        "next=None with an 18h-old last must flag stale — moc1 freeze signature"
    )


def test_normalize_timer_unset_next_but_just_fired_not_stale():
    """Fire-instant transient: systemd briefly reports NEXT=0 while it
    recomputes the next elapse (monotonic OnUnitActiveSec timers like
    meshanchor-map-poke.timer). `last` is ~now, so the timer just ran —
    it must NOT flicker the banner stale. Regression for the 2026-05-29
    meshanchor-server poke-timer false positive."""
    raw = {"unit": "meshanchor-map-poke.timer", "last": int(NOW * 1_000_000), "next": 0}
    entry = _normalize_timer(raw, "user", NOW)
    assert entry["next_fire_unix"] is None
    assert entry["age_s"] == pytest.approx(0.0, abs=0.5)
    assert entry["stale"] is False


def test_normalize_timer_unset_next_negative_age_not_stale():
    """Clock skew: a just-fired timer can record `last` a hair ahead of
    our sampled now, giving a slightly negative age. Never stale."""
    raw = {"unit": "skewed.timer", "last": int((NOW + 0.1) * 1_000_000), "next": 0}
    entry = _normalize_timer(raw, "user", NOW)
    assert entry["stale"] is False


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


# ─── _show_unit_props + _parse_unix_at ─────────────────────────────────
#
# Used by /fleet/tests to (a) detect not-installed units (LoadState !=
# loaded) so the dashboard greys them out instead of silently failing
# with exit=5, and (b) read service-side timestamps so a manual
# `systemctl start <unit>.service` advances the chip without waiting
# for the .timer's next tick.


def test_parse_unix_at_strips_leading_at():
    """systemctl `--timestamp=unix` emits values like `@1778910308`."""
    from utils.fleet_snapshot import _parse_unix_at
    assert _parse_unix_at("@1778910308") == 1778910308.0


def test_parse_unix_at_accepts_bare_int_for_robustness():
    from utils.fleet_snapshot import _parse_unix_at
    assert _parse_unix_at("1778910308") == 1778910308.0


def test_parse_unix_at_empty_means_unset():
    """systemctl emits `Key=` (empty value) for unset timestamps; the
    parser surfaces None so the merger can ignore it via `or None`."""
    from utils.fleet_snapshot import _parse_unix_at
    assert _parse_unix_at("") is None


def test_parse_unix_at_zero_means_unset():
    """Matches the 0-as-unset convention from _normalize_timer."""
    from utils.fleet_snapshot import _parse_unix_at
    assert _parse_unix_at("@0") is None
    assert _parse_unix_at("0") is None


def test_parse_unix_at_bad_input_returns_none():
    from utils.fleet_snapshot import _parse_unix_at
    assert _parse_unix_at("not-a-number") is None
    assert _parse_unix_at("@abc") is None


def test_show_unit_props_parses_key_value_output(monkeypatch):
    """Real systemctl output is one `Key=Value` per line. Empty values
    survive as empty strings (callers use `or None` semantics)."""
    fake, cap = _capture_subprocess_run(
        returncode=0,
        stdout=(
            "LoadState=loaded\n"
            "ExecMainExitTimestamp=@1778910308\n"
            "ActiveEnterTimestamp=\n"
        ),
    )
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", fake)
    out = fleet_snapshot._show_unit_props(
        "meshforge-lab-rollup.service", "user",
        ["LoadState", "ExecMainExitTimestamp", "ActiveEnterTimestamp"],
    )
    assert out["LoadState"] == "loaded"
    assert out["ExecMainExitTimestamp"] == "@1778910308"
    assert out["ActiveEnterTimestamp"] == ""
    # Correct invocation shape — same as _list_timers_scope pattern.
    assert cap["cmd"][0] == "systemctl"
    assert "--user" in cap["cmd"]
    assert "show" in cap["cmd"]
    assert "--timestamp=unix" in cap["cmd"]
    prop_idx = cap["cmd"].index("-p")
    assert cap["cmd"][prop_idx + 1] == (
        "LoadState,ExecMainExitTimestamp,ActiveEnterTimestamp"
    )


def test_show_unit_props_not_found_returns_loadstate(monkeypatch):
    """Synth-soak on every fleet box except moc returns LoadState=not-found.
    The dashboard turns this into `not_installed: true`."""
    fake, _ = _capture_subprocess_run(
        returncode=0,
        stdout=(
            "LoadState=not-found\n"
            "ExecMainExitTimestamp=\n"
            "ActiveEnterTimestamp=\n"
        ),
    )
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", fake)
    out = fleet_snapshot._show_unit_props(
        "meshforge-synth-soak.service", "user", ["LoadState"],
    )
    assert out["LoadState"] == "not-found"


def test_show_unit_props_nonzero_returncode_returns_empty(monkeypatch):
    """systemctl can fail (no user bus, etc.) — return {} so the dashboard
    treats the unit as 'no extra signal' rather than crashing."""
    fake, _ = _capture_subprocess_run(returncode=1, stdout="")
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", fake)
    assert fleet_snapshot._show_unit_props(
        "x.service", "user", ["LoadState"],
    ) == {}


def test_show_unit_props_timeout_returns_empty(monkeypatch):
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)
    monkeypatch.setattr(
        "utils.fleet_snapshot.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(
            __import__("subprocess").TimeoutExpired(cmd="systemctl", timeout=5)
        ),
    )
    assert fleet_snapshot._show_unit_props(
        "x.service", "user", ["LoadState"],
    ) == {}


def test_show_unit_props_root_user_scope_drops_to_operator(monkeypatch):
    """Same root→operator drop as _list_timers_scope. Required for
    meshanchor-server (map daemon as User=root) to read user-scope
    unit props."""
    fake, cap = _capture_subprocess_run(
        returncode=0, stdout="LoadState=loaded\n",
    )
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "utils.fleet_test_runner._find_operator_user",
        lambda: (1000, "wh6gxz"),
    )
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", fake)
    fleet_snapshot._show_unit_props(
        "meshforge-lab-rollup.service", "user", ["LoadState"],
    )
    assert cap["cmd"][0] == "sudo"
    assert "-n" in cap["cmd"]
    assert cap["cmd"][cap["cmd"].index("-u") + 1] == "wh6gxz"
    env_idx = cap["cmd"].index("env")
    assert cap["cmd"][env_idx + 1] == "XDG_RUNTIME_DIR=/run/user/1000"
    assert "show" in cap["cmd"]
    assert "--user" in cap["cmd"]


def test_show_unit_props_root_user_no_operator_returns_empty(monkeypatch):
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 0)
    monkeypatch.setattr(
        "utils.fleet_test_runner._find_operator_user", lambda: None,
    )
    monkeypatch.setattr(
        "utils.fleet_snapshot.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("subprocess.run must not be invoked")
        ),
    )
    assert fleet_snapshot._show_unit_props(
        "x.service", "user", ["LoadState"],
    ) == {}


# ─── CI status block ────────────────────────────────────────────────────
#
# `~/.meshforge-ci-status` is written twice daily by the
# meshforge-ci-status timer on whichever fleet box has it enabled.
# Format: a single "# generated <iso>" header plus one indented line
# per repo. Robust parsing matters because the block flows directly
# to the dashboard pill.


_CI_FILE_SAMPLE = (
    "# MeshForge ecosystem CI status — generated 2026-05-15T08:04:36-10:00\n"
    "  meshforge                            in_progress  a11095c  feat(fleet): T1.5\n"
    "  meshanchor                           in_progress  3fbf241  feat(fleet): panel\n"
    "  meshforge-maps                       success      0ec25c8  fix(tests): foo\n"
    "  meshing_around_meshforge             success      3d1c97b  github_actions\n"
    "  RNS-Management-Tool                  success      dc1b109  Merge pull request\n"
    "  RNS-Meshtastic-Gateway-Tool          success      cd2748a  fix(ci): drop -x\n"
)


def test_parse_ci_status_file_extracts_repos_and_overall():
    block = _parse_ci_status_file(_CI_FILE_SAMPLE)
    assert block["available"] is True
    assert block["generated_at"] == "2026-05-15T08:04:36-10:00"
    assert isinstance(block["generated_unix"], float)
    assert len(block["repos"]) == 6
    names = [r["name"] for r in block["repos"]]
    assert "meshforge" in names
    assert "RNS-Meshtastic-Gateway-Tool" in names
    # 4 success + 2 in_progress → overall is in_progress (no failure).
    assert block["overall"] == "in_progress"
    assert block["red_count"] == 0
    assert block["in_progress_count"] == 2


def test_parse_ci_status_file_ignores_overdue_pr_section():
    """The 'Overdue open PRs' section is informational; the pill must
    not surface its lines as repos."""
    sample = (
        _CI_FILE_SAMPLE
        + "\n"
        + "# Overdue open PRs (>14 days)\n"
        "  meshforge#1234  20d  user — Some title\n"
    )
    block = _parse_ci_status_file(sample)
    assert len(block["repos"]) == 6
    assert all("#" not in r["name"] for r in block["repos"])


def test_parse_ci_status_file_skips_lines_without_valid_sha():
    """Stray lines that don't carry a 7-char hex sha shouldn't poison
    the repo list — the parser is defensive against future format
    additions like commentary lines indented under a repo."""
    sample = (
        "# generated 2026-05-15T08:04:36-10:00\n"
        "  meshforge  success  abc1234  ok\n"
        "  meshanchor  success  notahex  bad sha\n"
        "  meshforge-maps  success  deadbee  good sha\n"
    )
    block = _parse_ci_status_file(sample)
    assert len(block["repos"]) == 2  # meshanchor's notahex line dropped
    assert {r["name"] for r in block["repos"]} == {"meshforge", "meshforge-maps"}


def test_parse_ci_status_file_handles_no_runs_state():
    """A brand-new repo with no CI runs shows up as 'no-runs'.
    The pill should surface it without trying to parse a sha."""
    sample = (
        "# generated 2026-05-15T08:04:36-10:00\n"
        "  newrepo  no-runs\n"
    )
    block = _parse_ci_status_file(sample)
    assert len(block["repos"]) == 1
    assert block["repos"][0]["state"] == "no-runs"
    assert block["repos"][0]["sha"] == ""


def test_ci_overall_failure_dominates():
    repos = [
        {"name": "a", "state": "success", "sha": "1234567"},
        {"name": "b", "state": "in_progress", "sha": "1234567"},
        {"name": "c", "state": "failure", "sha": "1234567"},
    ]
    assert _ci_overall(repos) == "failure"


def test_ci_overall_in_progress_when_no_failure():
    repos = [
        {"name": "a", "state": "success", "sha": "1234567"},
        {"name": "b", "state": "in_progress", "sha": "1234567"},
    ]
    assert _ci_overall(repos) == "in_progress"


def test_ci_overall_success_when_all_clean():
    repos = [
        {"name": "a", "state": "success", "sha": "1234567"},
        {"name": "b", "state": "success", "sha": "1234567"},
    ]
    assert _ci_overall(repos) == "success"


def test_ci_overall_unknown_when_no_repos():
    """Empty file or all-malformed lines → unknown (the pill renders
    grey, not green — silence isn't the same as healthy)."""
    assert _ci_overall([]) == "unknown"


def test_ci_overall_degraded_for_cancelled_or_skipped():
    """A run that was cancelled or skipped isn't a failure but isn't
    success either — render the pill as degraded (orange)."""
    repos = [
        {"name": "a", "state": "success", "sha": "1234567"},
        {"name": "b", "state": "cancelled", "sha": "1234567"},
    ]
    assert _ci_overall(repos) == "degraded"


def test_ci_status_block_returns_unavailable_when_file_missing(tmp_path, monkeypatch):
    """Most fleet boxes don't run the CI timer — the block should
    cleanly report unavailable so MA's pill picks another peer."""
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    block = _ci_status_block()
    assert block["available"] is False
    assert block["reason"] == "no_file"


def test_ci_status_block_returns_unavailable_when_no_operator_home(monkeypatch):
    """Root daemon with no resolvable operator user → unavailable.
    Reproduces meshanchor-server-style edge case if /run/user/ is
    empty for some reason."""
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: None)
    block = _ci_status_block()
    assert block["available"] is False
    assert block["reason"] == "no_operator_home"


def test_ci_status_block_parses_real_file(tmp_path, monkeypatch):
    """End-to-end: drop the sample file in a fake operator home,
    confirm the block carries repos + overall + age."""
    (tmp_path / ".meshforge-ci-status").write_text(_CI_FILE_SAMPLE)
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    block = _ci_status_block()
    assert block["available"] is True
    assert block["overall"] == "in_progress"
    assert len(block["repos"]) == 6
    assert block["age_s"] is not None  # depends on test-run wallclock vs sample ts
    assert isinstance(block["stale"], bool)


def test_ci_status_block_marks_stale_when_old(tmp_path, monkeypatch):
    """If the file's generated_at is older than CI_STATUS_STALE_AFTER_S
    (currently 14h — 1.4× the timer's 10h gap), mark stale=True so
    the pill renders amber instead of green/red."""
    # Build a sample with a deliberately-ancient timestamp.
    ancient_iso = "2020-01-01T00:00:00-10:00"
    sample = (
        f"# MeshForge ecosystem CI status — generated {ancient_iso}\n"
        "  meshforge  success  abc1234  ok\n"
    )
    (tmp_path / ".meshforge-ci-status").write_text(sample)
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    block = _ci_status_block()
    assert block["available"] is True
    assert block["stale"] is True
    assert block["age_s"] > CI_STATUS_STALE_AFTER_S


def test_snapshot_includes_ci_status_block():
    """Schema contract: /fleet/slo must carry a ci_status key so MA's
    rollup poller can read it for the dashboard pill."""
    snap = build_slo_snapshot()
    assert "ci_status" in snap
    assert isinstance(snap["ci_status"], dict)
    assert "available" in snap["ci_status"]


# ─── Observability blocks (Track 2.6) ───────────────────────────────────


def test_snapshot_includes_path_table_block():
    """The dashboard needs a per-host count of known RNS paths to
    answer 'where can this message go.' Contract: build_slo_snapshot
    carries `path_table: {available, count, ts, reason?}`."""
    snap = build_slo_snapshot()
    assert "path_table" in snap
    block = snap["path_table"]
    assert isinstance(block, dict)
    assert "available" in block
    assert "count" in block
    assert isinstance(block["count"], int)
    assert "ts" in block


def test_snapshot_includes_interfaces_block():
    """Compact summary so MA can show '<host>: 4/4 interfaces online'
    without dumping the per-interface RX/TX detail into /fleet/slo."""
    snap = build_slo_snapshot()
    assert "interfaces" in snap
    block = snap["interfaces"]
    assert isinstance(block, dict)
    assert "count" in block
    assert "online_count" in block
    assert isinstance(block["count"], int)
    assert isinstance(block["online_count"], int)


def test_snapshot_includes_cascade_block():
    """Cascade-detector summary so MA's rollup can red-flag a host
    with a pre_fail fingerprint without drilling into /fleet/cascade."""
    snap = build_slo_snapshot()
    assert "cascade" in snap
    block = snap["cascade"]
    assert isinstance(block, dict)
    # Stable keys consumers can rely on (no KeyError on healthy boxes).
    for k in ("total", "clean", "suspected", "pre_fail", "wedged", "degraded"):
        assert k in block, f"cascade.{k} missing — consumers must not KeyError"
        assert isinstance(block[k], int)


def test_overall_status_demotes_to_degraded_on_cascade_pre_fail(monkeypatch):
    """A pre_fail fingerprint must trip overall_status=degraded so the
    rollup dashboard surfaces it WITHOUT operators having to remember
    to look at /fleet/cascade separately."""
    class _FakeDetector:
        def summary(self):
            return {"clean": 0, "pre_fail": 1}
    monkeypatch.setattr(
        "utils.cascade_detector.get_singleton",
        lambda: _FakeDetector(),
    )
    snap = build_slo_snapshot()
    assert snap["overall_status"] == "degraded"
    assert any("cascade" in e for e in snap["errors"]), (
        "errors must mention cascade so operators see the reason"
    )


def test_overall_status_demotes_to_degraded_on_cascade_wedged(monkeypatch):
    """Same demotion for wedged severity — it's the most severe state."""
    class _FakeDetector:
        def summary(self):
            return {"clean": 0, "wedged": 1}
    monkeypatch.setattr(
        "utils.cascade_detector.get_singleton",
        lambda: _FakeDetector(),
    )
    snap = build_slo_snapshot()
    assert snap["overall_status"] == "degraded"


def test_overall_status_degraded_on_watchdog_wedge(monkeypatch):
    """A watchdog wedge signal must demote overall_status to degraded so MA's
    rollup carries the cross-box wedge signal without new HTTP plumbing
    (Issue stack #58-#69). Services + cascade are pinned healthy (the autouse
    fixture already neutralizes the watchdog; here we override it not-ok) so
    the watchdog is the only demotion source — covers the branch the autouse
    fixture otherwise masks."""
    monkeypatch.setattr(fleet_snapshot, "_systemctl_state",
                        lambda *a, **k: "available")
    monkeypatch.setattr(
        fleet_snapshot, "_watchdog_block",
        lambda: {
            "installed": True, "ok": False,
            "signals": [{"severity": "wedge",
                         "class": "rns_shared_instance_unresponsive",
                         "subject": "rnsd"}],
        },
    )
    snap = build_slo_snapshot()
    assert snap["overall_status"] == "degraded"
    assert any("watchdog" in e for e in snap["errors"]), (
        "errors must mention the watchdog wedge so operators see the reason"
    )


def test_overall_status_stays_ready_when_only_suspected_cascade(monkeypatch):
    """`suspected` is 1-hit hysteresis — too noisy to demote on. Only
    `pre_fail` and `wedged` count for overall_status demotion."""
    # Need to mock services_rollup as well so it doesn't naturally demote
    # in the test environment (where the gateway/map units may not exist).
    class _FakeDetector:
        def summary(self):
            return {"clean": 0, "suspected": 1}
    monkeypatch.setattr(
        "utils.cascade_detector.get_singleton",
        lambda: _FakeDetector(),
    )
    monkeypatch.setattr(
        "utils.fleet_snapshot._services_rollup",
        lambda: {
            "required": {"total": 1, "available": 1},
            "optional": {"total": 0, "available": 0},
            "_detail": {},
        },
    )
    snap = build_slo_snapshot()
    assert snap["overall_status"] == "ready"
    # No cascade-related error message either.
    assert not any("cascade" in e for e in snap["errors"])


# ─── M3 honest-signal: timer-probe failure ≠ no-timers (parity with MA) ──


def test_list_timers_scope_none_on_probe_failure(monkeypatch):
    """rc!=0 -> None (probe FAILED), distinct from [] (ran, no timers)."""
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)
    fake, _ = _capture_subprocess_run(returncode=1, stdout="")
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", fake)
    assert fleet_snapshot._list_timers_scope("system") is None


def test_list_timers_scope_empty_stdout_is_empty_list(monkeypatch):
    """rc==0 + no stdout -> [] (ran OK, genuinely no timers)."""
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)
    fake, _ = _capture_subprocess_run(returncode=0, stdout="")
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", fake)
    assert fleet_snapshot._list_timers_scope("system") == []


def test_schedules_block_probe_failure_unhealthy_with_reason(monkeypatch):
    """A failed timer probe renders unhealthy + reason — NOT clean green."""
    monkeypatch.setattr(fleet_snapshot, "_list_timers_scope",
                        lambda scope: None)
    block = _schedules_block()
    assert block["healthy"] is False
    assert "reason" in block and "unavailable" in block["reason"]


def test_schedules_block_no_timers_healthy_no_reason(monkeypatch):
    """Genuinely-empty ([]) stays healthy with no reason (the M3 split)."""
    monkeypatch.setattr(fleet_snapshot, "_list_timers_scope",
                        lambda scope: [])
    block = _schedules_block()
    assert block["healthy"] is True
    assert "reason" not in block


# ─── Phase-1 sources: crontab / verdicts / loop_crons (honest-signal) ────


def test_parse_crontab_skips_comments_env_and_malformed():
    jobs = fleet_snapshot._parse_crontab(
        "\n# comment\nMAILTO=root\nPATH=/usr/bin\n"
        "*/5 * * * * /bin/true\n@reboot /opt/start\nbadline\n")
    cmds = [j["command"] for j in jobs]
    assert cmds == ["/bin/true", "/opt/start"]   # env/comment/malformed gone
    assert jobs[0]["schedule"] == "*/5 * * * *"
    assert jobs[1]["schedule"] == "@reboot"


def test_read_crontab_no_crontab_is_empty_not_unavailable(monkeypatch):
    """rc=1 + 'no crontab for' is genuinely EMPTY, available stays True."""
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)

    def _fake(cmd, **kw):
        return MagicMock(returncode=1, stdout="",
                         stderr="no crontab for wh6gxz")
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", _fake)
    r = fleet_snapshot._read_crontab()
    assert r["available"] is True
    assert r["jobs"] == [] and r["count"] == 0


def test_read_crontab_error_is_unavailable(monkeypatch):
    """A genuine error (other rc=1 stderr) must NOT read as 'no cron jobs'."""
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)

    def _fake(cmd, **kw):
        return MagicMock(returncode=1, stdout="",
                         stderr="cannot connect to cron daemon")
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", _fake)
    r = fleet_snapshot._read_crontab()
    assert r["available"] is False
    assert "unavailable" in r["reason"]


def test_read_crontab_missing_binary_is_unavailable(monkeypatch):
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)

    def _fake(cmd, **kw):
        raise FileNotFoundError("crontab")
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", _fake)
    r = fleet_snapshot._read_crontab()
    assert r["available"] is False
    assert "probe_error" in r["reason"]


def test_read_crontab_success_parses(monkeypatch):
    monkeypatch.setattr("utils.fleet_snapshot.os.geteuid", lambda: 1000)

    def _fake(cmd, **kw):
        return MagicMock(returncode=0, stderr="",
                         stdout="*/5 * * * * /bin/true\n@daily /bin/backup\n")
    monkeypatch.setattr("utils.fleet_snapshot.subprocess.run", _fake)
    r = fleet_snapshot._read_crontab()
    assert r["available"] is True and r["count"] == 2


def test_parse_cron_verdicts_last_per_name_and_status():
    now = 2_000_000_000.0
    text = (
        "2026-06-08T14:00:00+00:00 jobA OK first\n"
        "2026-06-08T15:00:00+00:00 jobA FAIL second\n"
        "2026-06-08T15:00:00+00:00 jobB CONCERN hmm\n"
        "garbage line\n"
    )
    jobs = fleet_snapshot._parse_cron_verdicts(text, now)
    by = {j["name"]: j for j in jobs}
    assert by["jobA"]["status"] == "FAIL"
    assert by["jobA"]["message"] == "second"
    assert by["jobB"]["status"] == "CONCERN"
    assert by["jobA"]["age_s"] > 0


def test_read_cron_verdicts_missing_file_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    r = fleet_snapshot._read_cron_verdicts()
    assert r["available"] is False and r["reason"] == "no_file"


def test_read_cron_verdicts_present_counts_fail(monkeypatch, tmp_path):
    (tmp_path / "cron_verdicts.log").write_text(
        "2026-06-08T15:00:00+00:00 jobA FAIL boom\n")
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    r = fleet_snapshot._read_cron_verdicts()
    assert r["available"] is True and r["fail_count"] == 1


def test_wired_verdict_names_extracts_from_crontab():
    crontab = {"available": True, "jobs": [
        {"schedule": "* * * * *",
         "command": "/h/job.sh >/dev/null 2>&1 ; "
                    "/opt/meshforge/scripts/cron_verdict.sh soak_cron $?"},
        {"schedule": "0 5 * * *",
         "command": "/h/mh.sh >/dev/null 2>&1 || "
                    "/opt/meshforge/scripts/cron_verdict.sh memory_health FAIL x"},
        {"schedule": "* * * * *", "command": "/h/power.sh >/dev/null 2>&1"},  # no verdict
    ]}
    assert fleet_snapshot._wired_verdict_names(crontab) == {"soak_cron", "memory_health"}


def test_wired_verdict_names_none_when_crontab_unavailable():
    # Can't read the crontab -> can't prove orphan-ness -> None (caller won't filter).
    assert fleet_snapshot._wired_verdict_names(
        {"available": False, "reason": "x"}) is None


def test_read_cron_verdicts_drops_orphan_parked_cron(monkeypatch, tmp_path):
    # A parked cron (crontab line commented -> not wired) leaves a STALE verdict
    # and must NOT surface as a CONCERN in the fleet view. The drop needs BOTH
    # unwired AND stale -- a FRESH unwired verdict is a live signal (see the
    # keeps_fresh_orphan tests below), so make the parked verdict explicitly old.
    from datetime import datetime, timedelta, timezone
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00")
    (tmp_path / "cron_verdicts.log").write_text(
        f"{stale} soak_cron OK\n"
        f"{stale} mesh_client_pull CONCERN PARKED\n")
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    r = fleet_snapshot._read_cron_verdicts(wired_names={"soak_cron"})
    assert {j["name"] for j in r["jobs"]} == {"soak_cron"}  # stale orphan dropped
    assert r["concern_count"] == 0                          # parked != concern
    assert r["orphan_filtered"] == 1                        # witness, not silent
    # Itemized witness (Finding 4): the dropped stale verdict is named WITH its
    # status, so a dropped CONCERN/FAIL stays visible, not folded into a count.
    assert r["orphan_dropped"] == [{"name": "mesh_client_pull", "status": "CONCERN"}]


def test_read_cron_verdicts_no_filter_when_wired_none(monkeypatch, tmp_path):
    # Fail-safe: crontab unreadable -> keep everything (don't hide a real signal).
    (tmp_path / "cron_verdicts.log").write_text(
        "2026-06-08T15:01:00+00:00 mesh_client_pull CONCERN PARKED\n")
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    r = fleet_snapshot._read_cron_verdicts(wired_names=None)
    assert {j["name"] for j in r["jobs"]} == {"mesh_client_pull"}
    assert r["orphan_filtered"] == 0


def test_verdict_names_in_command_finditer_all_chained():
    # A single crontab line may chain TWO verdict calls -> BOTH are wired
    # (.finditer, not .search). Pins the shared SSOT used by both the orphan
    # filter and #78's probe so the two can never drift (honest_failure_modes #5).
    cmd = ("/h/a.sh ; /opt/meshforge/scripts/cron_verdict.sh job_a $? ; "
           "/h/b.sh ; /opt/meshforge/scripts/cron_verdict.sh job_b $?")
    assert fleet_snapshot._verdict_names_in_command(cmd) == ["job_a", "job_b"]
    assert fleet_snapshot._verdict_names_in_command("") == []


def test_read_cron_verdicts_keeps_fresh_orphan_secondary_verdict(
        monkeypatch, tmp_path):
    # REGRESSION (review finding #1): mf5_soak_watch.sh wires only
    # `mf5_soak_watch` on the crontab line but emits `mf5_soak_verdict` (the
    # final soak PASS/FAIL) from INSIDE the wrapper. That fresh, unwired verdict
    # must NOT be dropped as an orphan -- doing so hides a live soak FAIL.
    from datetime import datetime, timedelta, timezone
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00")
    (tmp_path / "cron_verdicts.log").write_text(
        f"{fresh} mf5_soak_watch OK\n"
        f"{fresh} mf5_soak_verdict FAIL soak regression\n")
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    r = fleet_snapshot._read_cron_verdicts(wired_names={"mf5_soak_watch"})
    assert "mf5_soak_verdict" in {j["name"] for j in r["jobs"]}  # fresh orphan KEPT
    assert r["fail_count"] == 1                                  # live FAIL surfaces
    assert r["orphan_filtered"] == 0                             # nothing hidden


def test_read_cron_verdicts_keeps_fresh_drops_stale_when_none_wired(
        monkeypatch, tmp_path):
    # REGRESSION (review finding #2): an available-but-empty crontab yields an
    # empty wired set (NOT None). The filter must still keep FRESH unwired
    # verdicts (live non-user-crontab emitters) and drop only STALE ones --
    # never blanket-drop every verdict to a clean-looking fail_count=0.
    from datetime import datetime, timedelta, timezone
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00")
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00")
    (tmp_path / "cron_verdicts.log").write_text(
        f"{fresh} live_secondary CONCERN active emitter\n"
        f"{stale} parked_cron FAIL long gone\n")
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    r = fleet_snapshot._read_cron_verdicts(wired_names=set())  # nothing wired
    assert {j["name"] for j in r["jobs"]} == {"live_secondary"}  # fresh kept
    assert r["concern_count"] == 1                              # live signal kept
    assert r["orphan_filtered"] == 1                            # only stale dropped
    # A dropped stale FAIL is named with its status, not hidden behind fail_count=0.
    assert r["orphan_dropped"] == [{"name": "parked_cron", "status": "FAIL"}]
    assert r["fail_count"] == 0  # the kept set has no FAIL — the dropped one is witnessed above


def test_read_loop_crons_absent_is_unavailable_ephemeral(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    r = fleet_snapshot._read_loop_crons()
    assert r["available"] is False
    assert r["reason"] == "no_file"
    assert r["ephemeral"] is True


def test_read_loop_crons_malformed_json(monkeypatch, tmp_path):
    (tmp_path / ".claude_loop_crons.json").write_text("{not json")
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    r = fleet_snapshot._read_loop_crons()
    assert r["available"] is False and r["reason"] == "malformed_json"


def test_read_loop_crons_present(monkeypatch, tmp_path):
    import json as _json
    (tmp_path / ".claude_loop_crons.json").write_text(_json.dumps(
        [{"id": "3d4dee9a", "cron": "7,37 * * * *",
          "prompt": "watch", "next_fire_unix": 123}]))
    monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
    r = fleet_snapshot._read_loop_crons()
    assert r["available"] is True and r["ephemeral"] is True
    assert r["jobs"][0]["id"] == "3d4dee9a"


def test_schedules_block_includes_new_subkeys(monkeypatch):
    monkeypatch.setattr(fleet_snapshot, "_list_timers_scope",
                        lambda scope: [])
    block = _schedules_block()
    assert {"crontab", "verdicts", "loop_crons"} <= set(block)


def test_failing_crontab_does_not_flip_timer_health(monkeypatch):
    """A broken crontab read is its OWN unavailable, never a timer fault."""
    monkeypatch.setattr(fleet_snapshot, "_list_timers_scope",
                        lambda scope: [])
    monkeypatch.setattr(fleet_snapshot, "_read_crontab",
                        lambda: {"available": False, "reason": "boom"})
    block = _schedules_block()
    assert block["healthy"] is True                 # timer health unaffected
    assert block["crontab"]["available"] is False


class TestProbeRadioPhoneApiDeferIssue17:
    """_probe_radio() must NOT connect_ex(:4403) when meshforge-gateway owns it.

    The 2026-06-27 moc dig: this snapshot is rebuilt on every status poll (~15s),
    and _probe_radio()'s connect_ex(("localhost", 4403)) is itself a contender on
    meshtasticd's single-consumer PhoneAPI (#17) — meshtasticd accepts it as a
    PhoneAPI client and force-closes the prior ("Force close previous TCP
    connection"), the sustained churn that tripped probe_meshtasticd_phoneapi_wedge.
    On a gateway box, report the radio from meshtasticd's service state instead
    (non-contending; honest — a down daemon still reads False).
    """

    @staticmethod
    def _states(**m):
        return lambda unit, *a, **k: m.get(unit, "not_running")

    def test_gateway_owns_phoneapi_meshtasticd_up_no_probe(self):
        with patch.object(fleet_snapshot, "_systemctl_state",
                          self._states(**{"meshforge-gateway": "available",
                                          "meshtasticd": "available"})), \
             patch("utils.fleet_snapshot.socket.socket") as mock_sock:
            out = _probe_radio()
            assert out["connected"] is True
            assert out["name"] == "meshtasticd"
            mock_sock.assert_not_called()        # NO contending :4403 connect

    def test_gateway_owns_phoneapi_meshtasticd_down_no_probe(self):
        with patch.object(fleet_snapshot, "_systemctl_state",
                          self._states(**{"meshforge-gateway": "available",
                                          "meshtasticd": "not_running"})), \
             patch("utils.fleet_snapshot.socket.socket") as mock_sock:
            out = _probe_radio()
            assert out["connected"] is False     # honest: daemon down ≠ connected
            mock_sock.assert_not_called()

    def test_non_gateway_box_still_probes_4403(self):
        with patch.object(fleet_snapshot, "_systemctl_state",
                          self._states(**{"meshforge-gateway": "not_running"})), \
             patch("utils.fleet_snapshot.socket.socket") as mock_sock, \
             patch("os.path.exists", return_value=False):
            mock_sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            out = _probe_radio()
            assert out["connected"] is True
            assert out["name"] == "meshtasticd"
            mock_sock.assert_called_once()       # the probe DID run (unchanged path)


def _now() -> float:
    import time
    return time.time()


def _iso(unix_ts: float) -> str:
    """Unix ts -> the ISO8601 form ``cron_verdict.sh`` writes (UTC, ``Z``)."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(unix_ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class TestPerCronVerdictStaleness:
    """The /fleet Cron Verdicts panel must judge each cron by its OWN cadence.

    Regression pin for 2026-07-24: ``VERDICT_STALE_AFTER_S`` (a flat 26h) was
    applied to EVERY verdict, so a weekly cron rendered amber ``OK STALE`` for
    ~85% of its cycle — observed live as ``local_brain_eval`` 5.4d and
    ``weekly_updates_digest`` 4.2d, both freshly OK and exactly on schedule —
    while Issue #78's ``probe_cron_verdict_stale`` (schedule-aware) read them
    clean. Two consumers of one artifact, two constants: honest_failure_modes
    #5. These tests hold the panel and the pager on the SAME math.
    """

    WEEK_SCHEDULE = "25 3 * * 0"        # local_brain_eval, Sunday 03:25
    DAY_SCHEDULE = "13 5 * * *"         # a daily cron

    # ── threshold derivation ──────────────────────────────────────────
    def test_weekly_cron_threshold_is_three_weeks(self):
        t = fleet_snapshot._verdict_stale_after_s(self.WEEK_SCHEDULE)
        assert t == pytest.approx(3 * 7 * 86400.0)

    def test_daily_cron_threshold_is_three_days(self):
        t = fleet_snapshot._verdict_stale_after_s(self.DAY_SCHEDULE)
        assert t == pytest.approx(3 * 86400.0)

    def test_frequent_cron_clamped_by_anti_flap_floor(self):
        # */2 * * * * -> 2min cadence; the floor keeps it off a hair trigger.
        t = fleet_snapshot._verdict_stale_after_s("*/2 * * * *")
        assert t == pytest.approx(2 * 3600.0)

    def test_reboot_cron_never_stale_checkable(self):
        assert fleet_snapshot._verdict_stale_after_s("@reboot") == float("inf")

    def test_unknown_schedule_keeps_the_flat_fallback(self):
        # An ORPHAN verdict has no crontab line -> the 26h the orphan filter
        # has always used. Absent evidence must never TIGHTEN the threshold.
        for missing in (None, "", "   "):
            assert fleet_snapshot._verdict_stale_after_s(missing) == float(
                fleet_snapshot.VERDICT_STALE_AFTER_S)

    def test_present_but_unparseable_schedule_follows_the_pager(self):
        # Distinct from "no schedule": a garbage schedule string defers to the
        # probe's own fallback so the two stay in lockstep. Looser than 26h,
        # never tighter — an unreadable cadence must not invent a STALE badge.
        from utils.watchdog_probes_liveness import (
            CRON_VERDICT_CADENCE_MULT,
            CRON_VERDICT_STALE_FLOOR_S,
            _cron_max_interval,
        )
        junk = "not a cron line"
        pager = max(CRON_VERDICT_STALE_FLOOR_S,
                    CRON_VERDICT_CADENCE_MULT * _cron_max_interval(junk))
        assert fleet_snapshot._verdict_stale_after_s(junk) == pytest.approx(pager)
        assert fleet_snapshot._verdict_stale_after_s(junk) > float(
            fleet_snapshot.VERDICT_STALE_AFTER_S)

    # ── the reported bug, end to end ──────────────────────────────────
    def test_weekly_cron_on_cadence_is_not_stale(self):
        now = 2_000_000_000.0
        age_s = 5.4 * 86400                      # the observed local_brain_eval age
        ts = _iso(now - age_s)
        jobs = fleet_snapshot._parse_cron_verdicts(
            f"{ts} local_brain_eval OK \n", now,
            schedules={"local_brain_eval": self.WEEK_SCHEDULE},
        )
        assert jobs[0]["stale"] is False
        assert jobs[0]["stale_after_s"] == pytest.approx(3 * 7 * 86400.0)

    def test_same_verdict_was_stale_under_the_flat_threshold(self):
        # Pins that the fix is what changed the answer, not the fixture.
        now = 2_000_000_000.0
        ts = _iso(now - 5.4 * 86400)
        jobs = fleet_snapshot._parse_cron_verdicts(
            f"{ts} local_brain_eval OK \n", now)      # no schedules -> fallback
        assert jobs[0]["stale"] is True

    def test_daily_cron_gone_silent_four_days_is_still_stale(self):
        # The fix must not blind the panel: a DAILY cron silent 4 days is a
        # real dead cron and has to keep its badge.
        now = 2_000_000_000.0
        ts = _iso(now - 4 * 86400)
        jobs = fleet_snapshot._parse_cron_verdicts(
            f"{ts} memory_health OK \n", now,
            schedules={"memory_health": self.DAY_SCHEDULE},
        )
        assert jobs[0]["stale"] is True

    # ── schedule extraction ───────────────────────────────────────────
    def test_verdict_schedules_maps_wired_names_to_schedules(self):
        crontab = {"available": True, "jobs": [
            {"schedule": self.WEEK_SCHEDULE,
             "command": "eval.sh; /opt/meshforge/scripts/cron_verdict.sh "
                        "local_brain_eval $?"},
            {"schedule": "*/5 * * * *",
             "command": "off.sh; /opt/meshforge/scripts/cron_verdict.sh "
                        "fleet_offline_check $?"},
            {"schedule": "* * * * *", "command": "/h/power.sh"},   # unwired
        ]}
        assert fleet_snapshot._verdict_schedules(crontab) == {
            "local_brain_eval": self.WEEK_SCHEDULE,
            "fleet_offline_check": "*/5 * * * *",
        }

    def test_verdict_schedules_empty_when_crontab_unavailable(self):
        # Unreadable crontab -> no schedules -> the flat fallback everywhere.
        # It must NOT invent a tight cadence out of missing evidence.
        assert fleet_snapshot._verdict_schedules(
            {"available": False, "reason": "boom"}) == {}

    def test_duplicate_wiring_keeps_the_loosest_cadence(self):
        crontab = {"available": True, "jobs": [
            {"schedule": "*/5 * * * *",
             "command": "a.sh; /opt/meshforge/scripts/cron_verdict.sh dup $?"},
            {"schedule": self.WEEK_SCHEDULE,
             "command": "b.sh; /opt/meshforge/scripts/cron_verdict.sh dup $?"},
        ]}
        assert fleet_snapshot._verdict_schedules(crontab) == {
            "dup": self.WEEK_SCHEDULE}

    # ── B6 (2026-07-26): a broken cadence import must be LOUD + labeled ──
    def _broken_liveness_import(self):
        # A None entry in sys.modules makes `from x import y` raise
        # ImportError — the exact silent-fallback branch under test.
        import sys as _sys
        return patch.dict(_sys.modules,
                          {"utils.watchdog_probes_liveness": None})

    def test_cadence_import_failure_falls_back_flat_and_warns_once(self, caplog):
        import logging
        fleet_snapshot._cadence_import_warned = False
        try:
            with self._broken_liveness_import(), \
                 caplog.at_level(logging.WARNING,
                                 logger="utils.fleet_snapshot"):
                t1 = fleet_snapshot._verdict_stale_after_s(self.WEEK_SCHEDULE)
                t2 = fleet_snapshot._verdict_stale_after_s(self.DAY_SCHEDULE)
            assert t1 == float(fleet_snapshot.VERDICT_STALE_AFTER_S)
            assert t2 == float(fleet_snapshot.VERDICT_STALE_AFTER_S)
            warnings = [r for r in caplog.records
                        if "cadence math unavailable" in r.getMessage()]
            assert len(warnings) == 1  # one-shot: first failure only
        finally:
            fleet_snapshot._cadence_import_warned = False

    def test_cadence_import_failure_labels_the_verdict_block(self):
        fleet_snapshot._cadence_import_warned = True  # silence, test the field
        try:
            now = 2_000_000_000.0
            ts = _iso(now - 3600)
            with self._broken_liveness_import():
                jobs = fleet_snapshot._parse_cron_verdicts(
                    f"{ts} local_brain_eval OK \n", now,
                    schedules={"local_brain_eval": self.WEEK_SCHEDULE})
            assert jobs[0]["cadence_source"] == "fallback"
        finally:
            fleet_snapshot._cadence_import_warned = False

    def test_healthy_cadence_path_has_no_fallback_label(self):
        now = 2_000_000_000.0
        ts = _iso(now - 3600)
        jobs = fleet_snapshot._parse_cron_verdicts(
            f"{ts} local_brain_eval OK \n", now,
            schedules={"local_brain_eval": self.WEEK_SCHEDULE})
        assert "cadence_source" not in jobs[0]

    def test_orphan_flat_is_not_labeled_fallback(self):
        # No schedule = the documented orphan path, not a lost import.
        now = 2_000_000_000.0
        ts = _iso(now - 3600)
        jobs = fleet_snapshot._parse_cron_verdicts(
            f"{ts} orphan_cron OK \n", now, schedules={})
        assert "cadence_source" not in jobs[0]

    # ── the anti-drift pin (honest_failure_modes #5) ──────────────────
    def test_panel_threshold_equals_the_issue78_pager_threshold(self):
        """ONE constant set, two consumers. If this breaks, the /fleet badge
        and the probe that actually pages disagree again — the exact defect."""
        from utils.watchdog_probes_liveness import (
            CRON_VERDICT_CADENCE_MULT,
            CRON_VERDICT_STALE_FLOOR_S,
            _cron_max_interval,
        )
        for sched in ("25 3 * * 0", "0 8 * * 1", "13 5 * * *", "40 * * * *",
                      "*/5 * * * *", "0 */2 * * *", "@daily", "@weekly",
                      "17 4 1 * *"):
            pager = max(CRON_VERDICT_STALE_FLOOR_S,
                        CRON_VERDICT_CADENCE_MULT * _cron_max_interval(sched))
            assert fleet_snapshot._verdict_stale_after_s(sched) == pytest.approx(
                pager), f"panel/pager threshold drift on {sched!r}"

    def test_orphan_drop_behaviour_is_preserved(self, monkeypatch, tmp_path):
        # An unwired verdict is dropped only when ALSO stale. Orphans carry no
        # schedule, so they keep the 26h rule the filter was built around.
        now_iso = _iso(_now() - 40 * 3600)          # 40h > 26h fallback
        (tmp_path / "cron_verdicts.log").write_text(
            f"{now_iso} parked_cron OK \n")
        monkeypatch.setattr(fleet_snapshot, "_operator_home", lambda: tmp_path)
        r = fleet_snapshot._read_cron_verdicts(wired_names=set(), schedules={})
        assert [j["name"] for j in r["jobs"]] == []
        assert r["orphan_filtered"] == 1
