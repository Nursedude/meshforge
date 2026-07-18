"""Tests for the fleet-membership wizard core (first-run declaration).

The installing user declares standalone-or-fleet in the TUI instead of
hand-authoring ~/.config/meshforge/fleet_hosts. Pure logic pinned here:
input parsing (reject garbage loudly), state reading (absent/empty file =
standalone, never an error), atomic write with provenance header,
standalone declaration (rename, not delete — reversible), and reachability
probing with an injected runner (unreachable is reported per-host, never
folded into a healthy-looking summary).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "launcher_tui"))

from handlers._fleet_membership_core import (  # noqa: E402
    declare_standalone,
    membership_state,
    parse_host_input,
    probe_hosts,
    write_fleet_hosts,
)


# ------------------------------------------------------------ parse_host_input

def test_parse_accepts_commas_whitespace_and_dedupes():
    assert parse_host_input("boxa, boxb boxc\nboxa") == ["boxa", "boxb", "boxc"]


def test_parse_rejects_shell_metacharacters():
    with pytest.raises(ValueError):
        parse_host_input("boxa; rm -rf /")
    with pytest.raises(ValueError):
        parse_host_input("box$(whoami)")


def test_parse_empty_input_is_empty_list():
    assert parse_host_input("   \n ") == []


# ----------------------------------------------------------- membership_state

def test_state_fleet_when_file_has_hosts(tmp_path):
    f = tmp_path / "fleet_hosts"
    f.write_text("boxa\nboxb  # comment\n")
    st = membership_state(str(f))
    assert st["mode"] == "fleet"
    assert st["hosts"] == ["boxa", "boxb"]


def test_state_standalone_when_file_absent_or_empty(tmp_path):
    f = tmp_path / "fleet_hosts"
    assert membership_state(str(f))["mode"] == "standalone"
    f.write_text("# only comments\n")
    assert membership_state(str(f))["mode"] == "standalone"


# ---------------------------------------------------------- write / standalone

def test_write_then_read_roundtrip(tmp_path):
    f = tmp_path / "sub" / "fleet_hosts"
    write_fleet_hosts(str(f), ["boxa", "boxb"])
    st = membership_state(str(f))
    assert st["mode"] == "fleet" and st["hosts"] == ["boxa", "boxb"]
    # provenance header so a later reader knows where this file came from
    assert f.read_text().startswith("#")


def test_declare_standalone_renames_not_deletes(tmp_path):
    f = tmp_path / "fleet_hosts"
    f.write_text("boxa\n")
    moved = declare_standalone(str(f))
    assert not f.exists()
    assert moved and Path(moved).exists()
    assert "boxa" in Path(moved).read_text()
    assert membership_state(str(f))["mode"] == "standalone"


def test_declare_standalone_on_absent_file_is_noop():
    assert declare_standalone("/nonexistent/path/fleet_hosts") is None


# ----------------------------------------------------------------- probe_hosts

def test_probe_reports_per_host_honestly():
    results = probe_hosts(
        ["up1", "down1", "up2"],
        runner=lambda host, timeout_s: host.startswith("up"))
    assert results == [("up1", "ok"), ("down1", "unreachable"), ("up2", "ok")]


def test_probe_runner_exception_is_unreachable_not_crash():
    def boom(host, timeout_s):
        raise OSError("ssh missing")
    assert probe_hosts(["boxa"], runner=boom) == [("boxa", "unreachable")]


# ------------------------------------------------------- handler flow (smoke)

def test_edit_flow_writes_file_via_dialogs(tmp_path, monkeypatch):
    """menu → edit → probe summary → confirm → file lands with the hosts."""
    from unittest.mock import MagicMock
    import handlers._fleet_membership_core as fm
    from handlers.fleet_provision import FleetProvisionHandler

    hosts_file = tmp_path / "fleet_hosts"
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(hosts_file))
    monkeypatch.setattr(fm, "probe_hosts",
                        lambda hosts, runner=None, timeout_s=8.0:
                        [(h, "ok") for h in hosts])

    h = FleetProvisionHandler()
    h.ctx = MagicMock()
    h.ctx.dialog.menu.side_effect = [("edit"), None]      # edit, then exit
    h.ctx.dialog.inputbox.return_value = "boxa, boxb"
    h.ctx.dialog.yesno.return_value = True

    h._membership_menu()

    st = fm.membership_state(str(hosts_file))
    assert st["mode"] == "fleet" and st["hosts"] == ["boxa", "boxb"]
