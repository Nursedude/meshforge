"""AUTO mode must not take a serial port somebody else owns (2026-10-02, moc3).

With meshtasticd stopped, the gateway's AUTO resolution picked the first
/dev/ttyUSB* — moc3's RNode, open in rnsd — and wrote Meshtastic framing into
a live RNS interface every ~90 s ("AUTO mode: no meshtasticd, USB device
detected, using SERIAL mode" / serial_connect[/dev/ttyUSB0] raised after 31 s).
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import meshtastic_connection as mc  # noqa: E402
from utils.meshtastic_connection import (  # noqa: E402
    ConnectionMode, MeshtasticConnectionManager, _device_held_by_other)
from utils.service_check import ServiceState  # noqa: E402


class _Status:
    def __init__(self, state):
        self.state = state
        self.available = state == ServiceState.AVAILABLE


def _mgr():
    return MeshtasticConnectionManager(host="localhost", port=4403,
                                       mode=ConnectionMode.AUTO)


def _resolve(mtd_state, held, device="/dev/ttyUSB0"):
    m = _mgr()
    with patch.object(m, "is_available", return_value=False), \
         patch.object(m, "_detect_usb_device", return_value=device), \
         patch.object(mc, "check_service", return_value=_Status(mtd_state)), \
         patch.object(mc, "_device_held_by_other", return_value=held):
        return m._resolve_mode()


class TestAutoResolution:
    def test_installed_meshtasticd_never_falls_back_to_serial(self):
        """THE moc3 case: meshtasticd stopped, a USB device present."""
        for st in (ServiceState.NOT_RUNNING, ServiceState.FAILED,
                   ServiceState.UNKNOWN, ServiceState.DEGRADED):
            assert _resolve(st, held=False) == ConnectionMode.TCP, st

    def test_device_open_in_another_process_is_not_taken(self):
        assert _resolve(ServiceState.NOT_INSTALLED, held=True) == ConnectionMode.TCP

    def test_unknown_ownership_is_not_taken(self):
        assert _resolve(ServiceState.NOT_INSTALLED, held=None) == ConnectionMode.TCP

    def test_free_device_without_meshtasticd_still_uses_serial(self):
        """The legitimate standalone case keeps working."""
        assert _resolve(ServiceState.NOT_INSTALLED, held=False) == ConnectionMode.SERIAL

    def test_explicit_serial_mode_is_unchanged(self):
        m = MeshtasticConnectionManager(mode=ConnectionMode.SERIAL)
        assert m._resolve_mode() == ConnectionMode.SERIAL

    def test_tcp_available_wins_without_any_ownership_scan(self):
        m = _mgr()
        with patch.object(m, "is_available", return_value=True), \
             patch.object(mc, "_device_held_by_other") as scan:
            assert m._resolve_mode() == ConnectionMode.TCP
            scan.assert_not_called()


class TestDeviceHeldByOther:
    """A fake /proc: <root>/<pid>/fd/<n> symlinks, as the kernel exposes them."""

    def _proc(self, tmp_path, holders):
        root = tmp_path / "proc"
        root.mkdir()
        (root / "self").mkdir()  # non-numeric entries are ignored
        for pid, targets in holders.items():
            fd = root / str(pid) / "fd"
            fd.mkdir(parents=True)
            for i, t in enumerate(targets):
                (fd / str(i)).symlink_to(t)
        return str(root)

    def test_held_device_reads_true(self, tmp_path):
        dev = tmp_path / "ttyUSB0"
        dev.write_text("")
        root = self._proc(tmp_path, {4242: [str(dev), "/dev/null"]})
        assert _device_held_by_other(str(dev), proc_root=root) is True

    def test_unheld_device_reads_false(self, tmp_path):
        dev = tmp_path / "ttyUSB0"
        dev.write_text("")
        root = self._proc(tmp_path, {4242: ["/dev/null"]})
        assert _device_held_by_other(str(dev), proc_root=root) is False

    def test_our_own_pid_does_not_count(self, tmp_path):
        dev = tmp_path / "ttyUSB0"
        dev.write_text("")
        root = self._proc(tmp_path, {os.getpid(): [str(dev)]})
        assert _device_held_by_other(str(dev), proc_root=root) is False

    def test_an_unreadable_process_makes_it_unknown_not_free(self, tmp_path):
        dev = tmp_path / "ttyUSB0"
        dev.write_text("")
        root = self._proc(tmp_path, {4242: ["/dev/null"]})
        real_listdir = os.listdir

        def listdir(p):
            if str(p).endswith(os.path.join("4242", "fd")):
                raise PermissionError(p)
            return real_listdir(p)

        with patch.object(mc.os, "listdir", side_effect=listdir):
            assert _device_held_by_other(str(dev), proc_root=root) is None

    def test_missing_proc_root_is_unknown(self, tmp_path):
        assert _device_held_by_other("/dev/ttyUSB0",
                                     proc_root=str(tmp_path / "nope")) is None
