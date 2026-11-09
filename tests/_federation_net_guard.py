"""Autouse guard for federation tests: NO real HTTP to a peer.

urllib.request.urlopen is the one door map_federation uses to reach a peer.
On 2026-11-08 this guard's first run found 14 poll tests (patching only
fetch_peer_directory) whose per-poll status probe sent REAL GETs — DNS +
connect to `.example.internal` and fleet names — plus 4 that patched a
renamed helper. A test that reaches the network pins live state, not code
(feedback: red-run fixtures must not reach the fleet).

Two layers:
  1. the poll's status probe (`_fetch_peer_status_extras`) answers
     (None, None) by default — a poll test opts into the real one by
     patching it back with REAL_STATUS_EXTRAS;
  2. urlopen itself records + refuses every un-mocked call, and the test
     FAILS at teardown if any happened. A test that mocks urlopen overrides
     it (nested patch wins).

Patches the SHARED urllib.request module for the test's duration — fine
here: these files run no real loopback servers.
"""
from unittest.mock import patch

import pytest

from utils.map_federation import _fetch_peer_status_extras as REAL_STATUS_EXTRAS  # noqa: N812


@pytest.fixture(autouse=True)
def _no_real_peer_http():
    calls = []

    def _refuse(req, *a, **kw):
        url = getattr(req, "full_url", req)
        calls.append(url)
        raise OSError(f"federation test tried real HTTP: {url}")

    with patch("urllib.request.urlopen", side_effect=_refuse), \
         patch("utils.map_federation._fetch_peer_status_extras",
               return_value=(None, None)):
        yield
    assert not calls, f"test reached the network: {calls}"
