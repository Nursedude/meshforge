"""probe_tcp: an unmade probe is UNKNOWN, never CLOSED/DOWN (2026-09-23)."""
import os
import socket
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import latency_monitor as lm  # noqa: E402


def test_socket_creation_failure_raises_unobservable_not_false():
    with patch.object(lm.socket, "socket", side_effect=OSError("no sockets here")):
        with pytest.raises(lm.ProbeUnobservable):
            lm.probe_tcp("127.0.0.1", 1)


def test_refused_connect_is_still_an_observation():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    ok, _rtt = lm.probe_tcp("127.0.0.1", port, timeout=1.0)
    assert ok is False


def test_monitor_records_no_sample_and_witnesses_unmade_probes():
    m = lm.LatencyMonitor(services=[("svc", "127.0.0.1", 1)])
    with patch.object(lm.socket, "socket", side_effect=OSError("blocked")):
        health = m.probe_once()
    assert len(health["svc"].samples) == 0
    assert m.unobservable_probes == 1
    assert health["svc"].status == "UNKNOWN"
