#!/usr/bin/env python3
"""BIC-3 operator-gate ADVERSARIAL DRILL (non-author review, Fable 5.1, 2026-10-14).

Every ``test_ATTACK_*`` encodes the attack SUCCEEDING — a PASS is a confirmed
defect, a FAIL means the gate held. ``test_control_*`` pin that the same
fixture refuses the canonical spellings, so an ATTACK pass is not an artifact.
Deliberately under ``scripts/`` (pytest ``testpaths = tests``) so the suite
never counts an attack's success as green. Run it by hand:

    python3 -m pytest scripts/bic3_gate_drill.py -q -rA
    REPO=/opt/meshanchor python3 -m pytest scripts/bic3_gate_drill.py -q -rA   # the twin

Fixes landed 2026-10-15 (F1 gate at the consumer, F4 per-client budget):
the regression pins live in ``tests/test_web_auth.py`` ("9. the adversarial
pass"). From that commit on, every ``test_ATTACK_*`` here must FAIL except the
two F2 pins (``copied_cookie_with_forged_origin``, ``operator_get_with_copied_
cookie_and_no_origin``) — those encode the ACCEPTED LAN-sniffing exposure
until BIC-13b TLS and keep passing by design. Any other ATTACK passing is a
regression. Findings + verdict: review_provenance.md table row 2026-10-14
"BIC-3 OPERATOR LOGIN — non-author pass".
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("REPO") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from test_web_auth import server, store, _req, JSON, _login, _origin  # noqa: E402,F401
from utils.map_http_handler import MapRequestHandler  # noqa: E402
class _FakeProxy:
    """Stands in for the meshtasticd API proxy OBJECT and records what reached
    it. The consumer functions (`_proxy_fromradio`, `_proxy_json`) stay REAL —
    the first cut of this drill replaced them with recorders, which also
    removed the consumer-side gate the F1 fix put there, so the drill kept
    reading "attack succeeds" after the fix (caught 2026-10-15)."""
    def __init__(self):
        self.calls = []

    def get_next_packet(self, client_id):
        self.calls.append(("fromradio", client_id))
        return b""

    def proxy_json(self, path):
        self.calls.append(("json", path))
        return b"{}"


@pytest.fixture
def proxy_on(monkeypatch):
    fake = _FakeProxy()
    monkeypatch.setattr(MapRequestHandler, "api_proxy", fake, raising=False)
    return fake.calls


# F1a: the /mesh/ prefix reaches the SAME fromradio proxy with no operator session
@pytest.mark.parametrize("path", ["/mesh/api/v1/fromradio?all=false", "/mesh/json/blink"])
def test_ATTACK_mesh_prefix_reaches_side_effect_proxy_ungated(server, proxy_on, path):
    srv, _ = server
    status, body, _ = _req(srv, "GET", path, None, JSON)
    assert status == 200, (status, body)           # attack succeeded if 200
    assert proxy_on, "proxy never called"


# F1b: spellings of the direct route the exact-match allowlist does not cover
@pytest.mark.parametrize("path", ["/api/v1/fromradio%2Fx", "/api/v1/fromradio.x?all=false",
                                  "/api/v1/fromradio/extra"])
def test_ATTACK_fromradio_suffix_spellings_bypass_exact_allowlist(server, proxy_on, path):
    srv, _ = server
    status, body, _ = _req(srv, "GET", path, None, JSON)
    assert status == 200, (status, body)
    assert proxy_on and proxy_on[0][0] == "fromradio"


# F4: a copied cookie + a forged single-label Host/Origin pair passes the write gate
def test_ATTACK_copied_cookie_with_forged_origin_passes(server, store):
    srv, sent = server
    cookie = _login(srv, store)
    port = srv.server_address[1]
    hdrs = dict(JSON, Cookie=cookie, Host=f"moc:{port}", Origin=f"http://moc:{port}")
    status, body, _ = _req(srv, "POST", "/api/settings", "{}", hdrs)
    assert status != 401, (status, body)   # gate passed; the handler answered


# F4b: an OPERATOR_GET needs no Origin at all — a copied cookie in curl reads it
def test_ATTACK_operator_get_with_copied_cookie_and_no_origin(server, store, monkeypatch):
    srv, _ = server
    cookie = _login(srv, store)
    seen = []
    monkeypatch.setattr(MapRequestHandler, "_serve_fleet_logs",
                        lambda self: (seen.append(1), self._serve_json({"ok": True})), raising=False)
    status, body, _ = _req(srv, "GET", "/fleet/logs", None, {"Cookie": cookie})
    assert status == 200 and seen, (status, body)


# F8: a LAN guest can burn every pairing code the operator mints (no per-client limit)
def test_ATTACK_lan_guest_burns_pairing_code(server, store):
    srv, _ = server
    store.start_pairing()
    for _ in range(5):
        _req(srv, "POST", "/login", json.dumps({"code": "AAAAAAAA"}), dict(JSON, Origin=_origin(srv)))
    assert store.status()["pairing_pending"] is False


# HEAD on an operator route: static tree only, never the API handler (control)
def test_control_head_on_operator_route_is_not_the_handler(server, monkeypatch):
    srv, _ = server
    seen = []
    monkeypatch.setattr(MapRequestHandler, "_serve_fleet_logs",
                        lambda self: seen.append(1), raising=False)
    status, _, _ = _req(srv, "HEAD", "/fleet/logs", None, {})
    assert status == 404 and not seen


# ── controls: the same fixture refuses the canonical spellings ──────────
@pytest.mark.parametrize("path", ["/api/v1/fromradio?all=false", "/api/v1/fromradio/", "/json/blink"])
def test_control_canonical_operator_gets_are_refused(server, proxy_on, path):
    srv, _ = server
    status, _, _ = _req(srv, "GET", path, None, JSON)
    assert status == 401 and not proxy_on


def test_control_copied_cookie_without_origin_is_refused(server, store):
    srv, _ = server
    cookie = _login(srv, store)
    status, _, _ = _req(srv, "POST", "/api/settings", "{}", dict(JSON, Cookie=cookie))
    assert status == 401


def test_control_forged_origin_without_cookie_is_refused(server):
    srv, _ = server
    port = srv.server_address[1]
    status, _, _ = _req(srv, "POST", "/api/settings", "{}",
                        dict(JSON, Host=f"moc:{port}", Origin=f"http://moc:{port}"))
    assert status == 401
