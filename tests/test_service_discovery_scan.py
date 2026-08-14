"""Parallel fallback network scan — Batch 6 (audit B1).

The nmap-less fallback used to probe 254 hosts serially at 0.3s timeout
each — up to ~76 seconds of a frozen TUI with no feedback. The scan must
now run concurrently and still find the right hosts.
"""

import os
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

        def fake_check_port(ip, port, timeout=0.3):
            time.sleep(0.05)  # serial: 254 x 0.05 = 12.7s; parallel: <2s
            return ip == planted

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
        with patch.object(sd_mod, 'check_port',
                          side_effect=lambda ip, port, timeout=0.3: ip in hits):
            found = ServiceDiscoveryHandler._parallel_port_scan("10.0.0.0/24")
        assert found == ["10.0.0.9", "10.0.0.20", "10.0.0.100"]

    def test_no_hits_returns_empty(self):
        with patch.object(sd_mod, 'check_port', return_value=False):
            assert ServiceDiscoveryHandler._parallel_port_scan("10.1.1.0/24") == []
