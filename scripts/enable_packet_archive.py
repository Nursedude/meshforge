#!/usr/bin/env python3
"""Enable long-term packet archival on this box.

Phase F companion to scripts/pcap_export.py. PacketArchive is opt-in
because it has a real cost: ~500 MB cap, 30-day retention, every
captured packet duplicated as a SQLite BLOB. Don't enable on busy
boxes (moc, moc3) without operator buy-in on SD wear.

Usage:
    python3 scripts/enable_packet_archive.py [--check] [--disable]

This script touches one piece of state: it instantiates
``TrafficInspector`` and calls ``enable_archival()``. The setting is
in-process; for archival to apply continuously, the process running
TrafficInspector (gateway, TUI session, sniffer daemon) must call
``enable_archival()`` itself OR run with this script's settings file
sourced. Today the simplest persistent path is: keep the TUI / gateway
running with archival enabled for the duration of the forensic window.

A persistent setting (config flag) is a deliberate Phase F+ follow-up.
"""

import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from monitoring.traffic_inspector import TrafficInspector  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="enable_packet_archive",
        description="Toggle PacketArchive on this box.",
    )
    p.add_argument("--check", action="store_true", help="Report status; do not change.")
    p.add_argument("--disable", action="store_true", help="Disable archival.")
    args = p.parse_args(argv)

    inspector = TrafficInspector()
    if args.check:
        print(f"archival_enabled={inspector.is_archival_enabled()}")
        stats = inspector.get_archive_stats()
        print(f"archive_stats={stats}")
        return 0

    if args.disable:
        inspector.disable_archival()
        print("PacketArchive disabled.")
        return 0

    inspector.enable_archival()
    print(
        "PacketArchive enabled in this process. To make it persistent, "
        "ensure the long-running capture process (gateway / TUI / sniffer) "
        "calls TrafficInspector().enable_archival() at startup."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
