"""The one-shot tracer tears its links down before it exits (root-caused 2026-10-09).

Measured: with the echo's link cache EMPTY a loopback PING+ACK is 25 ms; 20 s
later, with the echo still holding the direct link to the PREVIOUS tracer
process (exited), the same loopback is 5,960 ms then 6,902 ms. LXMF caches a
direct link for LINK_MAX_INACTIVITY = 600 s, RNS declares a link stale at
STALE_TIME = 720 s, and the tracer fires every 600 s as a fresh process with the
same identity — so the echo's first ACK attempt goes out on a dead link and the
retry schedule (4 s job loop, 7 s path wait, 10 s delivery retry) is the RTT.
Over 7 d on four boxes the sub-200 ms mode was 1–4% of fires; moc4 sat at 9 s
median / 29 s max against the tracer's own 30 s ACK deadline.

The party that dies closes the door: RNS.Link.teardown() transmits a close to
the peer, whose LXMRouter.delivery_link_closed drops the cache entry, so the
next fire gets a fresh link and a millisecond answer.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lab import lxmf_tracer as t  # noqa: E402
from lab import _lab_common as common  # noqa: E402


def _fake_rns(links):
    rns = MagicMock()
    rns.Transport.active_links = links
    rns.Transport.has_path.return_value = True
    return rns


def test_teardown_closes_every_active_link_and_counts():
    a, b = MagicMock(), MagicMock()
    n = t.teardown_active_links(_fake_rns([a, b]), settle_s=0)
    assert n == 2
    a.teardown.assert_called_once()
    b.teardown.assert_called_once()


def test_teardown_tolerates_one_failing_link_and_still_closes_the_rest():
    bad, good = MagicMock(), MagicMock()
    bad.teardown.side_effect = RuntimeError("socket gone")
    n = t.teardown_active_links(_fake_rns([bad, good]), settle_s=0)
    assert n == 1
    good.teardown.assert_called_once()


def test_teardown_with_no_links_does_not_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(t.time, "sleep", lambda s: slept.append(s))
    assert t.teardown_active_links(_fake_rns([]), settle_s=0.3) == 0
    assert slept == []


def test_run_trace_tears_links_down_before_returning(monkeypatch):
    link = MagicMock()
    rns = _fake_rns([link])
    monkeypatch.setitem(sys.modules, "RNS", rns)
    monkeypatch.setitem(sys.modules, "LXMF", MagicMock())
    monkeypatch.setattr(common, "init_reticulum_with_watchdog",
                        lambda configdir: MagicMock())
    monkeypatch.setattr(common, "load_or_create_identity",
                        lambda name: (MagicMock(), None))
    monkeypatch.setattr(t, "assert_rns_tx_allowed", lambda **kw: None)
    order = []
    link.teardown.side_effect = lambda: order.append("teardown")
    peers = [t.Peer(name="peer-a", hash_hex="0" * 32)]
    t0 = time.monotonic()
    results = t.run_trace(peers, path_timeout_s=0.1, ack_timeout_s=0.2,
                          path_retries=1, path_retry_backoff_s=0.0)
    order.append("returned")
    assert results and results[0].peer == "peer-a"
    assert order == ["teardown", "returned"], order
    assert time.monotonic() - t0 < 5.0
