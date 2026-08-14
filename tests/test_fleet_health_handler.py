"""Tests for FleetHealthHandler (T0 — Fleet Health diagnostic surface)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Match the import shape used by the running TUI (cd into src; relative imports).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))

from handlers.fleet_health import (  # noqa: E402  (after sys.path mutation)
    FleetHealthHandler,
    ProbeResult,
)


def _handler() -> FleetHealthHandler:
    return FleetHealthHandler()


_PROBE_NAMES = [
    "_probe_rnsd", "_probe_rns_path_table", "_probe_rns_hub_peers",
    "_probe_nomadnet", "_probe_lxmf_queue", "_probe_gateway_bridge",
    "_probe_peer_gateways", "_probe_map_service", "_probe_map_db",
    "_probe_meshtasticd_radio",
]


def test_render_overview_offers_fix_for_degraded(monkeypatch):
    # Stack Health offers the profile-gated fix chooser for degraded local
    # services after rendering (TUI workflow arc). Stub probes so the render
    # touches nothing real; assert the degraded set is routed to the chooser and
    # the bare wait-for-enter is skipped when a chooser is shown.
    h = _handler()
    h.ctx = MagicMock()
    for name in _PROBE_NAMES:
        monkeypatch.setattr(
            FleetHealthHandler, name,
            lambda self, _n=name: ProbeResult(label=_n, status="ok", headline="ok"),
        )
    seen = {}
    monkeypatch.setattr("service_remediation.collect_degraded_services",
                        lambda names: [("rnsd", False)])
    monkeypatch.setattr("service_remediation.offer_service_fix_chooser",
                        lambda ctx, deg: seen.update(deg=deg) or True)
    h._render_overview()
    assert seen.get("deg") == [("rnsd", False)]
    h.ctx.wait_for_enter.assert_not_called()  # chooser shown -> early return


# ----------------------------------------------------------------- helpers


def test_humanize_duration_thresholds():
    fn = FleetHealthHandler._humanize_duration
    assert fn(30) == "30s"
    assert fn(90) == "1 min"
    assert fn(3600) == "1.0 hr"
    assert fn(7200) == "2.0 hr"
    assert fn(86400 * 2) == "2.0 days"


def test_first_host_strips_port():
    assert FleetHealthHandler._first_host("192.0.2.38:4242") == "192.0.2.38"


# ----------------------------------------------------------------- rnsd probe


def test_probe_rnsd_inactive(monkeypatch):
    fake_status = MagicMock(available=False)
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: fake_status,
    )
    r = _handler()._probe_rnsd()
    assert r.status == "fail"
    assert "not running" in r.headline


def test_probe_rnsd_active(monkeypatch):
    fake_status = MagicMock(available=True)
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: fake_status,
    )
    monkeypatch.setattr(
        FleetHealthHandler,
        "_service_uptime_seconds",
        classmethod(lambda cls, unit: 3600 * 24 * 2),
    )
    r = _handler()._probe_rnsd()
    assert r.status == "ok"
    assert "2.0 days" in r.headline


# ----------------------------------------------------------- RNS path table


def test_probe_rns_path_table_no_rnpath(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    r = _handler()._probe_rns_path_table()
    assert r.status == "info"
    assert "not installed" in r.headline


def test_probe_rns_path_table_empty(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/rnpath")
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: ""))
    r = _handler()._probe_rns_path_table()
    assert r.status == "warn"
    assert "empty" in r.headline


def test_probe_rns_path_table_populated(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/rnpath")
    sample = (
        "<aaa> is 1 hop  away via <hub> on AutoInterfacePeer[eth0] expires X\n"
        "<bbb> is 2 hops away via <hub> on TCPInterface[Hub/X:4242] expires X\n"
        "<ccc> is 0 hops away via <self> on LocalInterface[rns/default] expires X\n"
        "<ddd> is 0 hops away via <self> on LocalInterface[rns/default] expires X\n"
    )
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: sample))
    r = _handler()._probe_rns_path_table()
    assert r.status == "ok"
    # 2 network destinations, 2 IPC peers
    assert "2 network destinations" in r.headline
    assert "2 local IPC peers" in r.headline


# ----------------------------------------------------------- RNS hub peers


def test_probe_rns_hub_peers_inbound(monkeypatch):
    out = (
        "ESTAB 0 0 192.0.2.38:4242 192.0.2.29:46146\n"
        "ESTAB 0 0 192.0.2.38:4242 192.0.2.249:47048\n"
        "ESTAB 0 0 192.0.2.38:22   192.0.2.29:55555\n"
    )
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: out))
    r = _handler()._probe_rns_hub_peers()
    assert r.status == "ok"
    assert "2 inbound" in r.headline


def test_probe_rns_hub_peers_outbound(monkeypatch):
    out = "ESTAB 0 0 192.0.2.29:46146 192.0.2.38:4242\n"
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: out))
    r = _handler()._probe_rns_hub_peers()
    assert r.status == "ok"
    assert "1 outbound" in r.headline
    assert "192.0.2.38" in r.headline


def test_probe_rns_hub_peers_none(monkeypatch):
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: ""))
    r = _handler()._probe_rns_hub_peers()
    assert r.status == "info"
    assert "no :4242 TCP sessions" in r.headline


# ----------------------------------------------------------------- NomadNet


def test_probe_nomadnet_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    r = _handler()._probe_nomadnet()
    assert r.status == "info"
    assert "not installed" in r.headline


def test_probe_nomadnet_quiet_fail(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    nndir = tmp_path / ".nomadnetwork"
    nndir.mkdir()
    logfile = nndir / "logfile"
    logfile.write_text("old\n")
    # Backdate the logfile to 2 days ago.
    old = time.time() - 86400 * 2
    import os
    os.utime(logfile, (old, old))
    # Daemon "running"
    monkeypatch.setattr(FleetHealthHandler, "_pgrep_count", staticmethod(lambda _: 1))
    r = _handler()._probe_nomadnet()
    assert r.status == "fail"
    assert "QUIET" in r.headline
    assert "delay" in (r.hint or "")


def test_probe_nomadnet_daemon_dead(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    nndir = tmp_path / ".nomadnetwork"
    nndir.mkdir()
    (nndir / "logfile").write_text("x\n")
    monkeypatch.setattr(FleetHealthHandler, "_pgrep_count", staticmethod(lambda _: 0))
    r = _handler()._probe_nomadnet()
    assert r.status == "fail"
    assert "daemon NOT running" in r.headline


def test_probe_nomadnet_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    nndir = tmp_path / ".nomadnetwork"
    nndir.mkdir()
    (nndir / "logfile").write_text("x\n")  # mtime = now
    monkeypatch.setattr(FleetHealthHandler, "_pgrep_count", staticmethod(lambda _: 1))
    r = _handler()._probe_nomadnet()
    assert r.status == "ok"


# ----------------------------------------------------------- LXMF queue


def test_probe_lxmf_queue_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    outdir = tmp_path / ".nomadnetwork" / "storage" / "messages" / "outbound"
    outdir.mkdir(parents=True)
    r = _handler()._probe_lxmf_queue()
    assert r.status == "ok"
    assert "empty" in r.headline


def test_probe_lxmf_queue_stuck(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    outdir = tmp_path / ".nomadnetwork" / "storage" / "messages" / "outbound"
    outdir.mkdir(parents=True)
    for i in range(15):
        (outdir / f"msg{i}").write_text("x")
    r = _handler()._probe_lxmf_queue()
    assert r.status == "fail"
    assert "stuck pending" in r.headline


def test_probe_lxmf_queue_normal(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    outdir = tmp_path / ".nomadnetwork" / "storage" / "messages" / "outbound"
    outdir.mkdir(parents=True)
    (outdir / "msg1").write_text("x")
    (outdir / "msg2").write_text("x")
    r = _handler()._probe_lxmf_queue()
    assert r.status == "warn"
    assert "2 pending" in r.headline


# ---------------------------------------------------- peer-gateway probe


def test_probe_peer_gateways_daemon_inactive(monkeypatch):
    """If meshforge-gateway isn't running, the probe is N/A (info)."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=False),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "info"
    assert "not applicable" in r.headline


def test_probe_peer_gateways_silent_journal(monkeypatch):
    """Gateway up but journalctl returns nothing — warn."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: ""))
    r = _handler()._probe_peer_gateways()
    assert r.status == "warn"
    assert "no gateway journal output" in r.headline


def test_probe_peer_gateways_no_peers_in_log(monkeypatch):
    """Gateway active, journal has output but no peer lines — isolation warn."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: "May 11 08:25 just a regular log line\n"),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "warn"
    assert "no peer-gateway log entries" in r.headline


def test_probe_peer_gateways_node_tracker_signal(monkeypatch):
    """Production signal: node_tracker RNS announces (heartbeat off).

    This is the line shape that ACTUALLY fires in production today
    on moc3. The probe must surface peer-gateway-named RNS nodes as
    live peers even when the heartbeat MQTT feature is disabled.
    """
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "May 11 16:39 py[1]: 2026-05-11 16:39 | gateway.node_tracker | "
        "INFO | Discovered RNS node: 3dfbdb5d (MeshForge Gateway (moc)) "
        "[LXMF_DELIVERY]\n"
        "May 11 16:46 py[1]: 2026-05-11 16:46 | gateway.node_tracker | "
        "INFO | Discovered RNS node: 627fa566 (MeshAnchor Broadcast) "
        "[LXMF_DELIVERY]\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "ok"
    assert "2 live" in r.headline


def test_probe_peer_gateways_handles_nested_parens_in_name(monkeypatch):
    """Production names have nested parens — must not truncate at inner )."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "Discovered RNS node: 3dfbdb5d (MeshForge Gateway (moc)) "
        "[LXMF_DELIVERY]\n"
        "Discovered RNS node: f68c2f56 (MeshForge Gateway (moc3)) "
        "[LXMF_DELIVERY]\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "ok"
    assert "MeshForge Gateway (moc)" in r.headline
    assert "MeshForge Gateway (moc3)" in r.headline


def test_probe_peer_gateways_ignores_non_gateway_rns_nodes(monkeypatch):
    """RNS announces from non-gateway destinations must NOT count."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "Discovered RNS node: aaa (lab-echo (box-a)) [LXMF_DELIVERY]\n"
        "Discovered RNS node: bbb (random nomadnet user) [LXMF_DELIVERY]\n"
        "Discovered RNS node: ccc (validator) [LXMF_DELIVERY]\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "warn"
    assert "no peer-gateway log entries" in r.headline


def test_probe_peer_gateways_heartbeat_signal(monkeypatch):
    """When heartbeat MQTT IS enabled, its log lines also feed the probe."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "Discovered peer gateway: moc3-mf (role=meshtastic)\n"
        "Discovered peer gateway: peer-2 (role=test)\n"
        "GATEWAY HEARTBEAT: peer peer-2 is DOWN\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "ok"
    assert "1 live" in r.headline
    assert "1 DOWN" in r.headline


def test_probe_peer_gateways_all_down(monkeypatch):
    """All known peers are DOWN — fail."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "Discovered peer gateway: only-peer (role=test)\n"
        "GATEWAY HEARTBEAT: peer only-peer is DOWN\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "fail"
    assert "all marked DOWN" in r.headline


# ------------------------------------------------------------ Map service


def test_probe_map_service_active(monkeypatch):
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    monkeypatch.setattr(
        FleetHealthHandler,
        "_service_uptime_seconds",
        classmethod(lambda cls, unit: 3600 * 5),
    )
    r = _handler()._probe_map_service()
    assert r.status == "ok"
    assert "5.0 hr" in r.headline


def test_probe_map_service_disabled_on_gateway_box(monkeypatch):
    """moc3-shape box: map deliberately off, gateway active."""
    def _cs(name):
        return MagicMock(available=(name == "meshforge-gateway"))
    monkeypatch.setattr("utils.service_check.check_service", _cs)
    monkeypatch.setattr(
        FleetHealthHandler,
        "_unit_file_state",
        classmethod(lambda cls, unit: "disabled"),
    )
    r = _handler()._probe_map_service()
    assert r.status == "info"
    assert "gateway-priority" in r.headline
    assert r.hint and ":5000" in r.hint and ":8808" in r.hint


def test_probe_map_service_disabled_no_gateway(monkeypatch):
    """Disabled but not a gateway box — still INFO, generic hint."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=False),
    )
    monkeypatch.setattr(
        FleetHealthHandler,
        "_unit_file_state",
        classmethod(lambda cls, unit: "masked"),
    )
    r = _handler()._probe_map_service()
    assert r.status == "info"
    assert "masked" in r.headline


def test_probe_map_service_not_installed(monkeypatch):
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=False),
    )
    monkeypatch.setattr(
        FleetHealthHandler,
        "_unit_file_state",
        classmethod(lambda cls, unit: "not-found"),
    )
    r = _handler()._probe_map_service()
    assert r.status == "info"
    assert "not installed" in r.headline


def test_probe_map_service_enabled_but_stopped(monkeypatch):
    """Enabled unit that's not running — operator should investigate."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=False),
    )
    monkeypatch.setattr(
        FleetHealthHandler,
        "_unit_file_state",
        classmethod(lambda cls, unit: "enabled"),
    )
    r = _handler()._probe_map_service()
    assert r.status == "fail"
    assert "not running" in r.headline
    # MF018 Q3 sweep: hints point in-app now, never at a shell.
    assert r.hint and "in-app" in r.hint


# ----------------------------------------------------------------- Map DB


def test_probe_map_db_thresholds(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    dbdir = tmp_path / ".local" / "share" / "meshforge"
    dbdir.mkdir(parents=True)
    db = dbdir / "node_history.db"
    # Sparse-file trick — write past offset to set size without allocating blocks.
    with open(db, "wb") as f:
        f.seek(int(5.5 * 1024**3))
        f.write(b"\0")
    wal = db.with_name(db.name + "-wal")
    with open(wal, "wb") as f:
        f.seek(250 * 1024 * 1024)
        f.write(b"\0")
    r = _handler()._probe_map_db()
    assert r.status == "fail"
    assert "5.5 GB" in r.headline
    assert "wal 250 MB" in r.headline


def test_probe_map_db_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    r = _handler()._probe_map_db()
    assert r.status == "info"
    assert "no node_history.db" in r.headline


# ------------------------------------------------------------------ handler


def test_handler_registration_shape():
    h = _handler()
    assert h.handler_id == "fleet_health"
    assert h.menu_section == "dashboard"
    items = h.menu_items()
    assert len(items) == 2
    tag, label, gate = items[0]
    assert tag == "stack_health"
    assert "Stack Health" in label
    assert gate is None
    tag2, label2, gate2 = items[1]
    assert tag2 == "fleet_posture"
    assert "Fleet Posture" in label2
    assert gate2 is None


# --------------------------------------------------- fleet posture (T1 pane)


def test_plainify_strips_markdown_keeps_content():
    from handlers.fleet_health import _plainify
    md = ("# mini-dudeai fleet posture — 9 boxes\n"
          "_rolled up 2026-07-18 08:21:37 · per-box freshness re-derived now_\n"
          "\n"
          "🟢 **boxA** (self) — fresh · 52 rules · src_errors=0\n"
          "🟢 **boxB** — fresh · 💭 3 delta(s) pending\n")
    out = _plainify(md)
    assert "**" not in out
    assert "boxA" in out and "boxB" in out
    assert "3 delta(s) pending" in out
    assert "fleet posture" in out


def test_rollup_command_plain_when_not_root():
    from handlers.fleet_health import _rollup_command
    cmd, note = _rollup_command(euid=1000, sudo_user=None)
    assert cmd[-3:] == ["python3", "-m", "mini_dudeai.rollup"]
    assert "sudo" not in cmd
    assert note is None


def test_rollup_command_drops_to_invoking_user_when_root():
    """ssh as root has no fleet keys — every box would read 'unreachable',
    an ambiguous→definitive lie. Root must drop to the invoking user."""
    from handlers.fleet_health import _rollup_command
    cmd, note = _rollup_command(euid=0, sudo_user="opuser")
    assert cmd[:4] == ["sudo", "-n", "-u", "opuser"]
    assert cmd[-3:] == ["python3", "-m", "mini_dudeai.rollup"]
    assert "opuser" in (note or "")


def test_rollup_command_root_without_sudo_user_warns():
    from handlers.fleet_health import _rollup_command
    cmd, note = _rollup_command(euid=0, sudo_user=None)
    assert "sudo" not in cmd
    assert note and "root" in note


def test_render_fleet_posture_prints_pane(monkeypatch, capsys):
    h = _handler()
    h.ctx = MagicMock()
    monkeypatch.setattr(
        h, "_run",
        lambda cmd, timeout=90: "# pane\n🟢 **moc** — fresh\n")
    with patch("backend.clear_screen", lambda: None):
        h._render_fleet_posture()
    out = capsys.readouterr().out
    assert "moc" in out and "**" not in out


def test_render_fleet_posture_honest_on_empty_output(monkeypatch, capsys):
    """No output ≠ healthy pane — the failure must be said out loud."""
    h = _handler()
    h.ctx = MagicMock()
    monkeypatch.setattr(h, "_run", lambda cmd, timeout=90: None)
    with patch("backend.clear_screen", lambda: None):
        h._render_fleet_posture()
    out = capsys.readouterr().out
    assert "no output" in out.lower()


def test_render_overview_does_not_raise(monkeypatch, capsys):
    """Smoke: with all probes mocked to fixed results, render the screen."""
    h = _handler()
    ctx = MagicMock()
    ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
    ctx.wait_for_enter = lambda *_: None
    h.set_context(ctx)

    # Mock every probe to return a deterministic result.
    def _fake(label, status="ok"):
        return ProbeResult(label=label, status=status, headline=f"{label} headline")

    monkeypatch.setattr(h, "_probe_rnsd", lambda: _fake("rnsd"))
    monkeypatch.setattr(h, "_probe_rns_path_table", lambda: _fake("path"))
    monkeypatch.setattr(h, "_probe_rns_hub_peers", lambda: _fake("hub"))
    monkeypatch.setattr(h, "_probe_nomadnet", lambda: _fake("nomadnet", "warn"))
    monkeypatch.setattr(h, "_probe_lxmf_queue", lambda: _fake("queue"))
    monkeypatch.setattr(h, "_probe_gateway_bridge", lambda: _fake("bridge"))
    monkeypatch.setattr(h, "_probe_peer_gateways", lambda: _fake("peers"))
    monkeypatch.setattr(h, "_probe_map_service", lambda: _fake("mapsvc"))
    monkeypatch.setattr(h, "_probe_map_db", lambda: _fake("db"))
    monkeypatch.setattr(h, "_probe_meshtasticd_radio", lambda: _fake("radio"))

    # backend.clear_screen is imported inside the method
    with patch("backend.clear_screen", lambda: None):
        h.execute("stack_health")

    out = capsys.readouterr().out
    assert "Stack Health" in out
    assert "[ OK ]" in out
    assert "[WARN]" in out


def test_probe_exception_is_isolated(monkeypatch, capsys):
    """If one probe raises, the screen still renders the others."""
    h = _handler()
    ctx = MagicMock()
    ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
    ctx.wait_for_enter = lambda *_: None
    h.set_context(ctx)

    def boom():
        raise RuntimeError("bang")

    monkeypatch.setattr(h, "_probe_rnsd", boom)
    for name in (
        "_probe_rns_path_table", "_probe_rns_hub_peers", "_probe_nomadnet",
        "_probe_lxmf_queue", "_probe_gateway_bridge", "_probe_peer_gateways",
        "_probe_map_service", "_probe_map_db", "_probe_meshtasticd_radio",
    ):
        monkeypatch.setattr(
            h, name,
            lambda label=name: ProbeResult(
                label=label, status="ok", headline="ok"
            ),
        )

    with patch("backend.clear_screen", lambda: None):
        h.execute("stack_health")

    out = capsys.readouterr().out
    # The booming probe shows as [ -- ] with a probe-error headline.
    assert "probe error" in out
    # The other probes still rendered.
    assert "[ OK ]" in out
