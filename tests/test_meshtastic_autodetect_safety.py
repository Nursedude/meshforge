"""The meshtastic CLI never auto-picks a USB serial port (2026-10-30).

auto_detect_connection's USB fallback took the FIRST /dev/ttyUSB*|ttyACM* —
and _run_command reaches it on any "Connection refused" (meshtasticd
restarting), then RE-RUNS the command with an explicit --port. On the
manager box the first ttyACM is the RNode rnsd holds (measured: meshtastic
findPorts -> ['/dev/ttyACM0']). Three review rounds found a new hole in every
filter (VID, rnsd discovery, --config=, mid-restart, sticky picks), so the
operator chose (10-30): NEVER auto-pick. Automatic choice is TCP 4403 only;
a USB radio is chosen ONCE in Settings > Meshtastic Connection, which now
persists it (it never could before), and a port a Reticulum config declares
is refused there too.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import commands.meshtastic as m  # noqa: E402


@pytest.fixture(autouse=True)
def default_state(monkeypatch, tmp_path):
    # Pin ambient state: no persisted file is read or written here.
    monkeypatch.setattr(m, "_connection_type", "localhost")
    monkeypatch.setattr(m, "_connection_value", "localhost")
    monkeypatch.setattr(m, "_persisted_loaded", False)
    monkeypatch.setattr(m, "_explicit_in_process", False)
    monkeypatch.setattr(m, "_persisted_stamp", lambda: None)
    monkeypatch.setattr(m, "_rns_config_paths", lambda: [])   # no box configs
    store = {}
    monkeypatch.setattr(m, "_read_persisted", lambda: dict(store))
    monkeypatch.setattr(m, "_write_persisted",
                        lambda t, v: store.update(type=t, value=v) or True)
    return store


@pytest.fixture
def dev(tmp_path):
    d = tmp_path / "dev"; d.mkdir()
    for n in ("ttyACM0", "ttyUSB0"):
        (d / n).write_text("")
    rns = tmp_path / "rns_config"
    rns.write_text("[interfaces]\n  [[R]]\n    type = RNodeInterface\n"
                   f"    port = {d / 'ttyACM0'}\n")
    return d, rns


# ---- auto-detect: TCP or nothing ---------------------------------------


def test_usb_ports_are_listed_never_chosen(monkeypatch, dev):
    d, rns = dev
    monkeypatch.setattr(m, "_meshtasticd_tcp_up", lambda: False)
    r = m.auto_detect_connection(dev_dir=d, rns_configs=[rns])
    assert not r.success
    assert (m._connection_type, m._connection_value) == ("localhost", "localhost")
    assert "ttyUSB0" in r.message and "Settings" in r.message
    assert "ttyACM0" in r.message and "Reticulum" in r.message   # marked, not offered


def test_tcp_is_the_only_automatic_choice(monkeypatch, dev):
    d, rns = dev
    monkeypatch.setattr(m, "_meshtasticd_tcp_up", lambda: True)
    r = m.auto_detect_connection(dev_dir=d, rns_configs=[rns])
    assert r.success and m._connection_type == "localhost"


def test_no_usb_and_no_tcp_says_so(monkeypatch, tmp_path):
    (tmp_path / "dev").mkdir()
    monkeypatch.setattr(m, "_meshtasticd_tcp_up", lambda: False)
    r = m.auto_detect_connection(dev_dir=tmp_path / "dev", rns_configs=[])
    assert not r.success and m._connection_type == "localhost"


# ---- the persisted connection ------------------------------------------


def test_cli_transport_args_reads_the_saved_serial_port(default_state):
    default_state.update(type="serial", value="/dev/ttyUSB0")
    assert m.cli_transport_args() == ["--port", "/dev/ttyUSB0"]


def test_an_in_process_choice_wins_over_the_saved_one(default_state):
    default_state.update(type="serial", value="/dev/ttyUSB0")
    m.set_connection("localhost", "10.0.0.5:4403")   # an in-process choice
    assert m.cli_transport_args() == ["--host", "10.0.0.5:4403"]


@pytest.mark.parametrize("saved", [
    {"type": "serial", "value": ""}, {"type": "carrier-pigeon", "value": "x"},
    {"type": "serial", "value": None}, {}])
def test_garbage_saved_state_falls_back_to_localhost(default_state, saved):
    default_state.clear(); default_state.update(saved)
    assert m.cli_transport_args() == ["--host", "localhost"]


# ---- save_connection validates before it writes ------------------------


def test_save_serial_port_persists_and_applies(default_state, dev):
    d, rns = dev
    r = m.save_connection("serial", str(d / "ttyUSB0"), rns_configs=[rns], dev_root=d)
    assert r.success
    assert default_state == {"type": "serial", "value": str(d / "ttyUSB0")}
    assert m.cli_transport_args() == ["--port", str(d / "ttyUSB0")]


def test_save_refuses_a_reticulum_declared_port(default_state, dev):
    d, rns = dev
    r = m.save_connection("serial", str(d / "ttyACM0"), rns_configs=[rns], dev_root=d)
    assert not r.success and "Reticulum" in r.message
    assert default_state == {}                     # nothing written


@pytest.mark.parametrize("bad", ["", "ttyUSB0", "/tmp/x", "/dev/../etc/passwd",
                                 "/dev/does-not-exist"])
def test_save_refuses_a_path_that_is_not_an_existing_dev_node(default_state, dev, bad):
    d, rns = dev
    r = m.save_connection("serial", bad, rns_configs=[rns], dev_root=d)
    assert not r.success and default_state == {}


def test_save_tcp_host_persists(default_state):
    r = m.save_connection("localhost", "10.0.0.5:4403")
    assert r.success and default_state == {"type": "localhost", "value": "10.0.0.5:4403"}


def test_failed_write_is_not_reported_as_saved(default_state, dev, monkeypatch):
    d, rns = dev
    monkeypatch.setattr(m, "_write_persisted", lambda t, v: False)
    r = m.save_connection("serial", str(d / "ttyUSB0"), rns_configs=[rns], dev_root=d)
    assert not r.success


# ---- BLE ---------------------------------------------------------------


def test_ble_connection_names_its_transport():
    m.set_connection("ble", "AA:BB:CC:DD:EE:FF")
    assert m.cli_transport_args() == ["--ble", "AA:BB:CC:DD:EE:FF"]


def test_empty_ble_address_falls_back_to_the_default():
    m.set_connection("ble", "  ")
    assert m._get_connection_args() == ["--host", "localhost"]


# ---- review 10-30 round 4 (the persisted connection) --------------------


def test_save_prefers_the_stable_by_id_path(default_state, dev, tmp_path):
    d, rns = dev
    byid = d / "serial" / "by-id"; byid.mkdir(parents=True)
    (byid / "usb-Heltec_T114-if00").symlink_to(d / "ttyUSB0")
    r = m.save_connection("serial", str(d / "ttyUSB0"), rns_configs=[rns], dev_root=d)
    assert r.success and default_state["value"] == str(byid / "usb-Heltec_T114-if00")


def test_unreadable_reticulum_config_refuses_the_save(default_state, dev, tmp_path, monkeypatch):
    # #5: a config that EXISTS but cannot be read is blind, not clear.
    d, rns = dev
    hidden = tmp_path / "hidden_config"; hidden.write_text("x")
    real_access = m.os.access
    monkeypatch.setattr(m.os, "access",
                        lambda p, mode: False if str(p) == str(hidden) else real_access(p, mode))
    r = m.save_connection("serial", str(d / "ttyUSB0"), rns_configs=[hidden], dev_root=d)
    assert not r.success and "could not be read" in r.message and default_state == {}


@pytest.mark.parametrize("bad", ["-rf", "10.0.0.5:44o3", "host name:4403", "h:0", "h:70000", ""])
def test_save_rejects_a_bad_tcp_host(default_state, bad):
    assert not m.save_connection("localhost", bad).success and default_state == {}


@pytest.mark.parametrize("good", ["localhost", "10.0.0.5:4403", "gw.local:4404"])
def test_save_accepts_a_good_tcp_host(default_state, good):
    assert m.save_connection("localhost", good).success


def test_run_command_loads_the_saved_target_before_the_tx_guard(default_state, monkeypatch):
    # #1: the guard must judge the SAME target the argv carries.
    default_state.update(type="localhost", value="10.0.0.5:4403")
    seen = {}
    monkeypatch.setattr(m, "assert_cli_args_allowed",
                        lambda args, host=None, port=None, **k: seen.update(host=host, port=port))
    monkeypatch.setattr(m, "_find_cli", lambda: "/usr/bin/meshtastic")
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    m._run_command(["--info"])
    assert seen == {"host": "10.0.0.5", "port": 4403}


def test_refused_saved_remote_never_falls_back_to_the_local_radio(default_state, monkeypatch):
    # #3: a configured target that refuses must FAIL, not re-run elsewhere.
    default_state.update(type="localhost", value="10.0.0.5:4403")
    runs = []
    monkeypatch.setattr(m, "assert_cli_args_allowed", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_cli", lambda: "/usr/bin/meshtastic")
    monkeypatch.setattr(m, "_meshtasticd_tcp_up", lambda: True)
    monkeypatch.setattr(m.subprocess, "run", lambda argv, **k: runs.append(argv) or type(
        "R", (), {"returncode": 1, "stdout": "", "stderr": "Connection refused"})())
    r = m._run_command(["--sendtext", "hi"])
    assert not r.success and len(runs) == 1
    assert "10.0.0.5" in " ".join(runs[0])


def test_unconfigured_refusal_surfaces_the_detect_message(default_state, monkeypatch):
    # #4: the listing + "choose in Settings" must reach the operator.
    monkeypatch.setattr(m, "assert_cli_args_allowed", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_cli", lambda: "/usr/bin/meshtastic")
    monkeypatch.setattr(m, "_meshtasticd_tcp_up", lambda: False)
    monkeypatch.setattr(m.subprocess, "run", lambda argv, **k: type(
        "R", (), {"returncode": 1, "stdout": "", "stderr": "Connection refused"})())
    r = m._run_command(["--info"])
    assert not r.success and "Settings > Meshtastic Connection" in r.message


def test_ensure_connection_sees_the_saved_choice(default_state, monkeypatch):
    # #8
    default_state.update(type="serial", value="/dev/ttyUSB7")
    monkeypatch.setattr(m, "test_connection", lambda: m.CommandResult.ok("ok"))
    monkeypatch.setattr(m, "_rns_config_paths", lambda: [])
    r = m.ensure_connection()
    assert r.success and m.get_connection().value == "/dev/ttyUSB7"


# ---- review 10-30 round 5 ------------------------------------------------


def test_a_saved_port_that_became_an_rnode_is_REFUSED_not_rerouted(default_state, dev, monkeypatch):
    # r5 #2: silently re-aiming to localhost is the re-targeting r4 forbade.
    d, rns = dev
    default_state.update(type="serial", value=str(d / "ttyACM0"))
    monkeypatch.setattr(m, "_rns_config_paths", lambda: [rns])
    with pytest.raises(m.TransportRefused) as ei:
        m.cli_transport_args()
    assert "Reticulum" in str(ei.value)


def test_use_time_check_blind_to_the_reticulum_config_refuses(default_state, dev, tmp_path, monkeypatch):
    # r5 #3: unreadable config at USE time = blind = refuse, as save does.
    d, rns = dev
    default_state.update(type="serial", value=str(d / "ttyUSB0"))
    hidden = tmp_path / "hidden"; hidden.write_text("x")
    monkeypatch.setattr(m, "_rns_config_paths", lambda: [hidden])
    real_access = m.os.access
    monkeypatch.setattr(m.os, "access",
                        lambda p, mode: False if str(p) == str(hidden) else real_access(p, mode))
    with pytest.raises(m.TransportRefused):
        m.cli_transport_args()


def test_run_command_turns_a_refusal_into_a_failed_result_and_runs_nothing(default_state, dev, monkeypatch):
    d, rns = dev
    default_state.update(type="serial", value=str(d / "ttyACM0"))
    monkeypatch.setattr(m, "_rns_config_paths", lambda: [rns])
    monkeypatch.setattr(m, "assert_cli_args_allowed", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_cli", lambda: "/usr/bin/meshtastic")
    ran = []
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: ran.append(a))
    r = m._run_command(["--info"])
    assert not r.success and not ran and "Reticulum" in r.message


def test_tx_guard_gets_host_and_port_split(default_state, monkeypatch):
    # r5 #1: "10.0.0.5:4403" as host + no port -> "10.0.0.5:4403:4403".
    default_state.update(type="localhost", value="10.0.0.5:4404")
    seen = {}
    monkeypatch.setattr(m, "assert_cli_args_allowed",
                        lambda args, host=None, port=None, **k: seen.update(host=host, port=port))
    monkeypatch.setattr(m, "_find_cli", lambda: "/usr/bin/meshtastic")
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    m._run_command(["--info"])
    assert seen == {"host": "10.0.0.5", "port": 4404}


def test_bracketed_ipv6_is_refused_the_cli_cannot_parse_it(default_state):
    # r5 #6 (supersedes the r4 acceptance of [ipv6]:port).
    assert not m.save_connection("localhost", "[fe80::1]:4403").success


def test_auto_detect_success_does_not_make_the_box_configured(monkeypatch):
    # r5 #7
    monkeypatch.setattr(m, "_meshtasticd_tcp_up", lambda: True)
    assert m.auto_detect_connection(dev_dir="/nonexistent", rns_configs=[]).success
    assert m._is_unconfigured()


def test_a_changed_saved_file_is_reloaded(default_state, monkeypatch):
    # r5 #5: a long-lived process sees a new choice made in another process.
    stamp = {"v": 1}
    monkeypatch.setattr(m, "_persisted_stamp", lambda: stamp["v"])
    default_state.update(type="serial", value="/dev/ttyUSB1")
    assert m.cli_transport_args() == ["--port", "/dev/ttyUSB1"]
    default_state.update(type="localhost", value="localhost")
    stamp["v"] = 2
    assert m.cli_transport_args() == ["--host", "localhost"]


def test_no_usb_message_still_names_the_settings_path(monkeypatch, tmp_path):
    # 10-30 CI: a runner has NO /dev/ttyUSB*|ttyACM*, so this branch is what
    # CI (and a box with the radio unplugged) sees. With no auto-pick, a USB
    # radio plugged in later must STILL be chosen in Settings — say so.
    (tmp_path / "dev").mkdir()
    monkeypatch.setattr(m, "_meshtasticd_tcp_up", lambda: False)
    r = m.auto_detect_connection(dev_dir=tmp_path / "dev", rns_configs=[])
    assert not r.success and "Settings > Meshtastic Connection" in r.message
