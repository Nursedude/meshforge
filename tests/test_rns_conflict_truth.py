"""RNS Diagnostics reports a NomadNet conflict only when NomadNet OWNS the
shared-instance socket (2026-09-25): it used to fire whenever a nomadnet
process existed — the normal client state — and the Fix flow offered to
`pkill -f nomadnet`."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "..", "src"), os.path.join(HERE, "..", "src", "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)

import utils.watchdog_probes_rns as wpr  # noqa: E402
from handlers.rns_diagnostics import RNSDiagnosticsHandler  # noqa: E402


def _conflict(monkeypatch, scan, classified=None):
    monkeypatch.setattr(wpr, "_scan_rns_listener_owners", lambda name, **k: scan)
    if classified is not None:
        monkeypatch.setattr(wpr, "_classify_listener_owners", lambda owners, root: classified)
    return RNSDiagnosticsHandler.__new__(RNSDiagnosticsHandler)._check_lxmf_app_conflict()


def test_rnsd_owning_the_socket_is_no_conflict_even_with_nomadnet_running(monkeypatch):
    assert _conflict(monkeypatch, ({101: "rnsd"}, None), ([], [], [])) is None


def test_nomadnet_owning_the_socket_is_the_conflict(monkeypatch):
    inverted = [(202, "/usr/bin/python3 /home/u/.local/bin/nomadnet --rnsconfig /etc/reticulum")]
    assert _conflict(monkeypatch, ({202: "python3"}, None), ([], inverted, [])) == "NomadNet"


def test_unobservable_scan_claims_nothing(monkeypatch):
    assert _conflict(monkeypatch, None) is None
