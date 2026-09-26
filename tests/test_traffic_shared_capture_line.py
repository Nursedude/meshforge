"""Traffic Inspector's menu must not read as "this box captures nothing".

On moc (2026-09-26) the header said "Capture: STOPPED" beside Statistics
showing 5,027 packets the GATEWAY captured into the shared DB. The header now
says what THIS process does, and how fresh the shared DB is.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from launcher_tui.handlers import traffic_inspector as ti


def _line(packets=None, raises=None):
    class _Insp:
        def get_packets(self, limit=100):
            if raises:
                raise raises
            return packets or []

    h = ti.TrafficInspectorHandler()
    with patch.object(ti, "get_traffic_inspector", lambda: _Insp()):
        return h._shared_capture_line()


def _pkt(seconds_ago):
    return SimpleNamespace(timestamp=datetime.now() - timedelta(seconds=seconds_ago))


@pytest.mark.parametrize("ago,expect", [
    (12, "newest packet 12s ago"),
    (600, "newest packet 10m ago"),
    (54 * 86400, "newest packet 54d ago"),
])
def test_age_of_the_newest_shared_packet(ago, expect):
    assert expect in _line([_pkt(ago)])


def test_empty_db_says_empty():
    assert _line([]) == "Shared capture DB: empty"


def test_unreadable_db_is_unknown_not_empty():
    assert "UNKNOWN (OperationalError)" in _line(raises=type("OperationalError", (Exception,), {})())


def test_future_timestamp_is_named():
    assert "FUTURE" in _line([_pkt(-3600)])
