"""Parallel fallback network scan — Batch 6 (audit B1) + review F1.

The nmap-less fallback used to probe 254 hosts serially at 0.3s timeout
each — up to ~76 seconds of a frozen TUI. Review F1 then found the probe
itself had NEVER worked: it called ``check_port(ip, port)`` while the real
signature is ``check_port(port, host=..., timeout=...)`` — and this file's
first version mocked the wrong shape, certifying the phantom signature
(the FakeDialog-parity lesson at function level).

Defense here is two-layered: the mocked tests use the REAL argument
shape, and one end-to-end test runs the REAL check_port against a live
loopback listener with no mocks at all.
"""

import os
import socket
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handlers import service_discovery as sd_mod
from handlers.service_discovery import ServiceDiscoveryHandler


class TestParallelPortScan:
    def test_finds_planted_ip_fast(self):
        planted = "192.168.77.42"

        def fake_check_port(port, host='localhost', timeout=0.3):
            # Mirrors utils._port_detection.check_port's REAL signature.
            time.sleep(0.05)  # serial: 254 x 0.05 = 12.7s; parallel: <2s
            return host == planted

        start = time.monotonic()
        with patch.object(sd_mod, 'check_port', side_effect=fake_check_port):
            found = ServiceDiscoveryHandler._parallel_port_scan("192.168.77.0/24")
        elapsed = time.monotonic() - start

        assert found == [planted]
        assert elapsed < 3, (
            f"scan took {elapsed:.1f}s — the parallel sweep regressed to "
            "serial (audit B1: 254 hosts x 0.3s = ~76s frozen screen)"
        )

    def test_results_sorted_numerically(self):
        hits = {"10.0.0.9", "10.0.0.100", "10.0.0.20"}
        with patch.object(
                sd_mod, 'check_port',
                side_effect=lambda port, host='localhost', timeout=0.3: host in hits):
            found = ServiceDiscoveryHandler._parallel_port_scan("10.0.0.0/24")
        assert found == ["10.0.0.9", "10.0.0.20", "10.0.0.100"]

    def test_no_hits_returns_empty(self):
        with patch.object(sd_mod, 'check_port', return_value=False):
            assert ServiceDiscoveryHandler._parallel_port_scan("10.1.1.0/24") == []

    def test_real_check_port_finds_live_loopback_listener(self):
        """END-TO-END, zero mocks: the scan must find a real listener via the
        real check_port. This is the test that fails on any argument-order
        drift between the scan and utils._port_detection.check_port (F1) —
        a mocked signature can never prove the real one.
        """
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.42", 0))  # any 127.0.0.0/8 addr works on Linux
            listener.listen(1)
            port = listener.getsockname()[1]

            found = ServiceDiscoveryHandler._parallel_port_scan(
                "127.0.0.0/24", port=port, timeout=0.2)
            assert "127.0.0.42" in found, (
                "real check_port did not find the live listener — argument "
                f"shape drifted? found={found!r}"
            )
        finally:
            listener.close()
