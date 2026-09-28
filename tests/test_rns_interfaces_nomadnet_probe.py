"""TUI audit finding 7 (2026-09-27): the NomadNet connectivity probe ran a raw
`RNS.Reticulum()` in a `python3 -c` subprocess, allowlisted out of MF019 on
the grounds that an isolated subprocess cannot hang the TUI (#68). That was
true, but it could still HOST the shared instance while rnsd was down (#69
squat) for up to its 15 s timeout. It now runs open_reticulum(...,
require_listener=True) inside NomadNet's own interpreter.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context


def _handler():
    from handlers.rns_interfaces import RNSInterfacesHandler
    h = RNSInterfacesHandler.__new__(RNSInterfacesHandler)
    h.ctx = make_handler_context()
    return h


def _probe(stdout, rc=0, stderr=""):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)
    env = {k: v for k, v in os.environ.items() if k != "SUDO_USER"}
    with patch("handlers.rns_interfaces.subprocess.run", side_effect=fake_run), \
         patch.dict(os.environ, env, clear=True):
        result = _handler()._test_nomadnet_connectivity("/usr/bin/python3")
    return result, seen["cmd"]


def test_probe_goes_through_the_chokepoint_and_refuses_to_host():
    _, cmd = _probe("connected\n")
    snippet = cmd[cmd.index("-c") + 1]
    assert "from utils.rns_init import open_reticulum" in snippet
    assert "require_listener=True" in snippet
    assert "RNS.Reticulum(" not in snippet


def test_no_rnsd_is_reported_as_not_connected_not_hosted():
    (status, detail, _), _ = _probe("no-rnsd\n")
    assert status == "standalone"
    assert "refused to host" in detail


def test_connected_and_standalone_still_map():
    assert _probe("connected\n")[0][0] == "connected"
    assert _probe("standalone\n")[0][0] == "standalone"


def test_a_foreign_owner_failure_is_an_error():
    # open_reticulum fails LOUD on a foreign @rns owner (#69) -> non-zero exit
    (status, detail, _), _ = _probe("", rc=1, stderr="RuntimeError: @rns/default owned by pid 42")
    assert status == "error" and "owned by pid 42" in detail


def test_rns_interfaces_is_no_longer_allowlisted():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    lint = (root / "scripts" / "lint.py").read_text()
    block = lint[lint.index("chokepoint_files = ("):]
    block = block[:block.index(")")]
    assert "rns_interfaces" not in block
