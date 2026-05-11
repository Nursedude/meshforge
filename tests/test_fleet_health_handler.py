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


# ----------------------------------------------------------------- helpers


def test_humanize_duration_thresholds():
    fn = FleetHealthHandler._humanize_duration
    assert fn(30) == "30s"
    assert fn(90) == "1 min"
    assert fn(3600) == "1.0 hr"
    assert fn(7200) == "2.0 hr"
    assert fn(86400 * 2) == "2.0 days"


def test_first_host_strips_port():
    assert FleetHealthHandler._first_host("192.168.86.38:4242") == "192.168.86.38"


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
        "ESTAB 0 0 192.168.86.38:4242 192.168.86.29:46146\n"
        "ESTAB 0 0 192.168.86.38:4242 192.168.86.249:47048\n"
        "ESTAB 0 0 192.168.86.38:22   192.168.86.29:55555\n"
    )
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: out))
    r = _handler()._probe_rns_hub_peers()
    assert r.status == "ok"
    assert "2 inbound" in r.headline


def test_probe_rns_hub_peers_outbound(monkeypatch):
    out = "ESTAB 0 0 192.168.86.29:46146 192.168.86.38:4242\n"
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: out))
    r = _handler()._probe_rns_hub_peers()
    assert r.status == "ok"
    assert "1 outbound" in r.headline
    assert "192.168.86.38" in r.headline


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
    assert len(items) == 1
    tag, label, gate = items[0]
    assert tag == "stack_health"
    assert "Stack Health" in label
    assert gate is None


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
        "_probe_lxmf_queue", "_probe_gateway_bridge", "_probe_map_db",
        "_probe_meshtasticd_radio",
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
