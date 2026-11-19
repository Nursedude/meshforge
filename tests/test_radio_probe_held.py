"""Radio verdict from a NON-perturbing probe: presence is not use.

Ledger radio-probe-usb-present-is-not-connected-2026-11-15. A USB tty alone
read connected/serial: meshforge-lab's dev board (no radio, held by nobody)
looked like a radio. A presence-only fix (r1, review NOT CLEAN 11-19) would
have DARKed meshanchor-server, whose MeshCore radio IS on USB and held by the
MA daemon. Measured fleet 11-19: the tell is whether a process HOLDS the
device. Held -> in use; not held, or holders unreadable -> unverified.

The table below is the whole input space; ``radio_connection_from_probe`` is
byte-locked with MeshAnchor (status_blocks.py), so these rows pin both apps.
"""
import itertools
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import status_blocks as sb  # noqa: E402


# ── the verdict table: every (tcp, usb, held) combination ──────────────────
def _expected(tcp, usb, held):
    if tcp:
        return (True, "tcp")            # meshtasticd's PhoneAPI is listening
    if usb and held is True:
        return (True, "serial")              # a process has the device open
    if usb and held is False:
        return (None, "usb_unheld")          # present; no readable holder
    if usb:
        return (None, "usb_holder_unknown")  # present; holders unreadable
    return (False, "none")


ROWS = list(itertools.product((True, False), (True, False), (True, False, None)))


def test_table_covers_the_whole_input_space():
    assert len(ROWS) == 12


@pytest.mark.parametrize("tcp,usb,held", ROWS)
def test_verdict_table(tcp, usb, held):
    assert sb.radio_connection_from_probe(tcp, usb, held) == _expected(tcp, usb, held)


def test_held_defaults_to_unknown_for_old_spool_writers():
    # A spool written before usb_held existed carries no holder evidence.
    assert sb.radio_connection_from_probe(False, True) == (None, "usb_holder_unknown")


@pytest.mark.parametrize("truthy", [1, "true", "yes"])
def test_only_a_real_true_means_held(truthy):
    # JSON true is the only holder evidence; a stray truthy value is not.
    assert sb.radio_connection_from_probe(False, True, truthy) == (None, "usb_holder_unknown")


# ── tty_held: who has the device open, read from /proc fds only ────────────
def _proc(tmp_path, holders):
    """holders: {pid: [device paths its fds point at]} -> fake /proc root."""
    root = tmp_path / "proc"
    for pid, targets in holders.items():
        fd = root / str(pid) / "fd"
        fd.mkdir(parents=True)
        for i, t in enumerate(targets):
            os.symlink(t, fd / str(i + 3))
    (root / "self").mkdir(parents=True, exist_ok=True)  # non-pid entries ignored
    return root


@pytest.fixture
def dev(tmp_path):
    d = tmp_path / "dev"
    d.mkdir()
    acm = d / "ttyACM0"
    acm.write_text("")
    return acm


def test_held_when_a_process_has_it_open(tmp_path, dev):
    root = _proc(tmp_path, {101: ["/dev/null"], 202: [str(dev)]})
    assert sb.tty_held([str(dev)], proc_root=str(root)) is True


def test_held_through_a_symlinked_name(tmp_path, dev):
    # /dev/ttyMeshCore -> ttyACM0: the fd names the real node.
    alias = tmp_path / "dev" / "ttyMeshCore"
    os.symlink(dev, alias)
    root = _proc(tmp_path, {202: [str(dev)]})
    assert sb.tty_held([str(alias)], proc_root=str(root)) is True


def test_not_held_when_every_process_was_readable(tmp_path, dev):
    root = _proc(tmp_path, {101: ["/dev/null"], 102: []})
    assert sb.tty_held([str(dev)], proc_root=str(root)) is False


def test_unknown_when_some_process_could_not_be_read(tmp_path, dev, monkeypatch):
    root = _proc(tmp_path, {101: ["/dev/null"], 102: ["/dev/zero"]})
    real = os.listdir

    def listdir(p):
        if str(p).endswith(os.path.join("102", "fd")):
            raise PermissionError(13, "Permission denied")
        return real(p)
    monkeypatch.setattr(sb.os, "listdir", listdir)
    assert sb.tty_held([str(dev)], proc_root=str(root)) is None


def test_a_found_holder_wins_over_unreadable_others(tmp_path, dev, monkeypatch):
    root = _proc(tmp_path, {101: [str(dev)], 102: []})
    real = os.listdir

    def listdir(p):
        if str(p).endswith(os.path.join("102", "fd")):
            raise PermissionError(13, "Permission denied")
        return real(p)
    monkeypatch.setattr(sb.os, "listdir", listdir)
    assert sb.tty_held([str(dev)], proc_root=str(root)) is True


def test_unknown_when_proc_itself_is_unreadable(tmp_path, dev):
    assert sb.tty_held([str(dev)], proc_root=str(tmp_path / "no-proc")) is None


def test_no_devices_is_not_held(tmp_path):
    root = _proc(tmp_path, {101: ["/dev/null"]})
    assert sb.tty_held([], proc_root=str(root)) is False


def test_a_process_exiting_mid_scan_is_skipped(tmp_path, dev, monkeypatch):
    root = _proc(tmp_path, {101: ["/dev/null"], 102: []})
    real = os.listdir

    def listdir(p):
        if str(p).endswith(os.path.join("102", "fd")):
            raise FileNotFoundError(2, "gone")
        return real(p)
    monkeypatch.setattr(sb.os, "listdir", listdir)
    assert sb.tty_held([str(dev)], proc_root=str(root)) is False


# ── the spool writer (box_truth_collect, byte-locked) carries the holder ───
def _spool_src(tmp_path, devs, holders, listening=False):
    from utils import box_truth_collect as bc
    tcp = tmp_path / "tcp"
    tcp.write_text("  sl  local_address rem_address   st\n"
                   + ("   0: 00000000:1133 00000000:0000 0A 0\n" if listening else ""))
    d = tmp_path / "dev"
    d.mkdir(exist_ok=True)
    for name in devs:
        (d / name).write_text("")
    root = _proc(tmp_path, {pid: [str(d / n) for n in names]
                            for pid, names in holders.items()})
    return bc, bc.Sources(home=tmp_path, proc_tcp=(str(tcp),),
                          dev_globs=(str(d / "ttyUSB*"), str(d / "ttyACM*")),
                          proc_root=str(root))


def test_spool_probe_reports_a_held_usb_radio(tmp_path):
    bc, src = _spool_src(tmp_path, ["ttyACM0"], {77: ["ttyACM0"]})
    assert bc._radio_probe(src) == {"tcp_listening": False, "usb_present": True,
                                    "usb_held": True}


def test_spool_probe_reports_an_unheld_usb_device(tmp_path):
    bc, src = _spool_src(tmp_path, ["ttyACM0"], {77: []})
    assert bc._radio_probe(src) == {"tcp_listening": False, "usb_present": True,
                                    "usb_held": False}


def test_spool_probe_skips_the_fd_scan_when_tcp_answers(tmp_path):
    # tcp wins the verdict; the holder scan is not run (null = not measured).
    bc, src = _spool_src(tmp_path, ["ttyACM0"], {77: ["ttyACM0"]}, listening=True)
    assert bc._radio_probe(src) == {"tcp_listening": True, "usb_present": True,
                                    "usb_held": None}


# ── fleet_truth (byte-locked): the DARK reason says WHY ───────────────────
@pytest.mark.parametrize("mode", ["usb_unheld", "usb_holder_unknown"])
def test_radio_cell_names_which_blindness(mode):
    # review r2 nit: 'no process holds it' must not be said when the holder
    # could not be READ — the two modes carry different operator actions.
    from utils import fleet_truth as ft
    c = ft._radio_cell({"radio": {"connected": None, "mode": mode}})
    assert c["state"] == ft.DARK
    assert mode in c["reason"], c
    other = {"usb_unheld": "could not be read", "usb_holder_unknown": "no readable"}[mode]
    assert other not in c["reason"], c


# ── review r2 test gaps ────────────────────────────────────────────────────
def test_tty_held_never_opens_a_device(tmp_path, dev, monkeypatch):
    # THE invariant: opening a radio tty can reset the board / steal its reader.
    import builtins

    def refuse(*a, **k):
        raise AssertionError(f"tty_held opened {a[:1]}")
    monkeypatch.setattr(sb.os, "open", refuse)
    monkeypatch.setattr(builtins, "open", refuse)
    root = _proc(tmp_path, {101: [str(dev)]})
    assert sb.tty_held([str(dev)], proc_root=str(root)) is True


def test_a_holder_of_a_different_node_with_a_shared_prefix_is_not_a_holder(tmp_path, dev):
    other = dev.parent / "ttyACM01"
    other.write_text("")
    root = _proc(tmp_path, {101: [str(other)]})
    assert sb.tty_held([str(dev)], proc_root=str(root)) is False


def test_usb_radio_devices_is_one_sorted_deduplicated_list(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    for n in ("ttyUSB1", "ttyACM0", "ttyUSB0"):
        (d / n).write_text("")
    globs = (str(d / "ttyUSB*"), str(d / "ttyACM*"), str(d / "ttyACM*"))
    assert sb.usb_radio_devices(globs) == sorted(str(d / n) for n in ("ttyACM0", "ttyUSB0", "ttyUSB1"))


def test_spool_writer_uses_the_shared_glob_list():
    from utils import box_truth_collect as bc
    # IDENTITY, not equality: a hand-copied tuple with today's values is the
    # drift this guards against (honest_failure_modes #5).
    assert bc.DEV_GLOBS is sb.USB_RADIO_GLOBS
