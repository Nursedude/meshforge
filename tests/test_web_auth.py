"""BIC-3: operator login before any web write (spec .claude/plans/bic3_web_auth_2026_10_13.md).

Before this, a trusted-LAN ``curl`` with a JSON body and no Origin keyed the
radio, ``/api/settings`` accepted a write from ANY client, and the dormant
toradio/blink/restart proxy routes had no gate of their own. Pinned through a
REAL HTTP server and the real handler; only the transmitter is replaced, by a
recorder that never touches a radio.
"""
import http.client
import json
import os
import threading
import time
from unittest.mock import patch

import pytest

from utils import web_auth
from utils.map_http_handler import MapRequestHandler
from utils.map_data_service import _make_http_server  # the live listener class

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]
MSG = json.dumps({"text": "auth probe", "destination": "^all"})
JSON = {"Content-Type": "application/json"}


@pytest.fixture
def store(tmp_path):
    return web_auth.SessionStore(tmp_path / "web_sessions.json")


@pytest.fixture
def server(monkeypatch, store):
    monkeypatch.setattr(MapRequestHandler, "allowed_origins", LAN)
    monkeypatch.setattr(MapRequestHandler, "collector", None, raising=False)
    monkeypatch.setattr(MapRequestHandler, "auth_store", store, raising=False)
    # F4: the wrong-code budget is class-wide; every test starts with a fresh one
    monkeypatch.setattr(MapRequestHandler, "login_tries", web_auth.TryBudget(), raising=False)
    # pin ambient class state: another suite's MapServer.start() leaves
    # is_warming=True on the class (seen in the full run 2026-10-13)
    monkeypatch.setattr(MapRequestHandler, "is_warming", False)
    srv = _make_http_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sent = []

    def fake_send(text, destination=None):
        sent.append((text, destination))
        return True

    with patch("gateway.meshtastic_protobuf_client.send_text_direct", fake_send):
        yield srv, sent
    srv.shutdown()
    srv.server_close()


def _req(srv, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read(), r.getheader("Set-Cookie")
    finally:
        c.close()


def _origin(srv):
    return f"http://localhost:{srv.server_address[1]}"


def _login(srv, store):
    code = store.start_pairing()
    status, body, cookie = _req(srv, "POST", "/login", json.dumps({"code": code}),
                                dict(JSON, Origin=_origin(srv)))
    assert status == 200, body
    assert cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    return cookie.split(";", 1)[0]


def _op(srv, cookie):
    return dict(JSON, Origin=_origin(srv), Cookie=cookie)


# ── 1. the radio: a LAN curl no longer transmits ─────────────────────────

def test_lan_curl_without_a_session_never_keys_the_radio(server):
    srv, sent = server
    status, body, _ = _req(srv, "POST", "/api/radio/message", MSG, JSON)
    assert status == 401
    out = json.loads(body)
    assert out["error"] == "operator login required"
    assert "meshforge_web.py pair" in out["how"]
    assert sent == []


def test_a_logged_in_operator_still_transmits(server, store):
    srv, sent = server
    cookie = _login(srv, store)
    status, body, _ = _req(srv, "POST", "/api/radio/message", MSG, _op(srv, cookie))
    assert status == 200, body
    assert sent == [("auth probe", None)]


def test_a_stolen_cookie_without_origin_is_refused(server, store):
    """Q7: a cookie arriving with no Origin is not a browser on this page."""
    srv, sent = server
    cookie = _login(srv, store)
    status, _, _ = _req(srv, "POST", "/api/radio/message", MSG, dict(JSON, Cookie=cookie))
    assert status == 401
    assert sent == []


def test_the_cross_site_layer_still_holds_for_an_operator(server, store):
    srv, sent = server
    cookie = _login(srv, store)
    status, _, _ = _req(srv, "POST", "/api/radio/message", MSG,
                        {"Content-Type": "text/plain", "Origin": _origin(srv), "Cookie": cookie})
    assert status == 415
    assert sent == []


# ── 2. settings: no write from anyone without a session ──────────────────

def test_settings_write_needs_a_session(server, store):
    srv, _ = server
    status, _, _ = _req(srv, "POST", "/api/settings", json.dumps({"selected_region": None}),
                        dict(JSON, Origin=_origin(srv)))
    assert status == 401
    cookie = _login(srv, store)
    status, body, _ = _req(srv, "POST", "/api/settings", json.dumps({"selected_region": None}),
                           _op(srv, cookie))
    assert status == 200, body


# ── 3. journals are operator reads ───────────────────────────────────────

def test_fleet_logs_need_a_session(server):
    srv, _ = server
    status, _, _ = _req(srv, "GET", "/fleet/logs?unit=meshforge-map")
    assert status == 401


# ── 4. route census: default-deny, so a new write route cannot slip in ──

@pytest.mark.parametrize("method,path", [
    ("POST", "/api/radio/message"), ("POST", "/api/settings"), ("POST", "/fleet/run-test"),
    ("POST", "/api/inbox/send"),
    ("POST", "/api/v1/toradio"), ("POST", "/mesh/api/v1/toradio"),
    ("POST", "/json/blink"), ("POST", "/mesh/json/blink"),
    ("POST", "/restart"), ("POST", "/mesh/restart"),
    ("PUT", "/api/v1/toradio"), ("PUT", "/mesh/api/v1/toradio"),
    ("POST", "/a/route/nobody/has/written/yet"),
    ("PUT", "/a/route/nobody/has/written/yet"),
    ("GET", "/fleet/logs"), ("GET", "/api/v1/fromradio"), ("GET", "/json/blink"),
])
def test_every_write_and_side_effect_route_is_operator_gated(server, method, path):
    srv, sent = server
    status, _, _ = _req(srv, method, path, "{}" if method != "GET" else None, JSON)
    assert status == 401, (method, path)
    assert sent == []


def test_only_login_and_logout_accept_a_write_without_a_session():
    assert MapRequestHandler.SESSIONLESS_WRITES == frozenset({"/login", "/logout"})


# ── 5. pairing ───────────────────────────────────────────────────────────

def test_code_shape_is_typeable_and_unambiguous(store):
    code = store.start_pairing()
    assert len(code) == 8
    assert not set(code) & set("01ILO")


def test_five_wrong_codes_burn_the_pairing(store):
    """The SHARED burn (Q16) lives in the store: five wrong redemptions void
    the code for everyone. Over HTTP a single client meets the per-client
    budget first (F4, 2026-10-14) — see test_one_client_cannot_burn_a_pairing_code."""
    code = store.start_pairing()
    for _ in range(web_auth.PAIRING_MAX_TRIES):
        assert store.redeem("WRONGXYZ") is None
    assert store.redeem(code) is None  # burned: the right code no longer works
    assert store.status()["pairing_pending"] is False


def test_a_code_works_once(server, store):
    srv, _ = server
    code = store.start_pairing()
    h = dict(JSON, Origin=_origin(srv))
    assert _req(srv, "POST", "/login", json.dumps({"code": code}), h)[0] == 200
    assert _req(srv, "POST", "/login", json.dumps({"code": code}), h)[0] == 401


def test_an_expired_code_is_refused(store, monkeypatch):
    code = store.start_pairing()
    real = time.time
    monkeypatch.setattr(web_auth.time, "time", lambda: real() + web_auth.PAIRING_TTL_S + 1)
    assert store.redeem(code) is None


def test_login_from_a_hostile_origin_is_refused(server, store):
    srv, _ = server
    code = store.start_pairing()
    status, _, _ = _req(srv, "POST", "/login", json.dumps({"code": code}),
                        dict(JSON, Origin="http://evil.example"))
    assert status == 403


def test_store_holds_hashes_never_secrets(server, store):
    srv, _ = server
    code = store.start_pairing()
    raw = store.path.read_text()
    assert code not in raw
    cookie = _login(srv, store)
    assert cookie.split("=", 1)[1] not in store.path.read_text()
    assert (store.path.stat().st_mode & 0o777) == 0o600


# ── 6. an unreadable or corrupt store refuses; it never fails open ───────

def test_corrupt_store_refuses_with_reason_and_keeps_the_corpse(server, store):
    srv, sent = server
    cookie = _login(srv, store)
    store.path.write_text("\x00\x00not json")
    status, body, _ = _req(srv, "POST", "/api/radio/message", MSG, _op(srv, cookie))
    assert status == 401
    assert "corrupt" in json.loads(body)["detail"]
    corpses = list(store.path.parent.glob("web_sessions.json.corrupt-*"))
    assert len(corpses) == 1 and corpses[0].read_bytes().startswith(b"\x00\x00")
    assert sent == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 000 file; this test needs a real refusal")
def test_unreadable_store_refuses_and_is_not_replaced(server, store):
    srv, sent = server
    cookie = _login(srv, store)
    store.path.chmod(0)
    try:
        status, body, _ = _req(srv, "POST", "/api/radio/message", MSG, _op(srv, cookie))
        assert status == 401
        assert "unreadable" in json.loads(body)["detail"]
        assert store.path.exists()
    finally:
        store.path.chmod(0o600)
    assert sent == []


# ── 7. revoke-all and the 30-day idle expiry ─────────────────────────────

def test_revoke_all_kills_live_cookies(server, store):
    srv, _ = server
    cookie = _login(srv, store)
    store.revoke_all()
    status, _, _ = _req(srv, "POST", "/api/settings", json.dumps({"selected_region": None}),
                        _op(srv, cookie))
    assert status == 401


def test_an_idle_session_expires(server, store, monkeypatch):
    srv, _ = server
    cookie = _login(srv, store)
    real = time.time
    monkeypatch.setattr(web_auth.time, "time", lambda: real() + web_auth.SESSION_IDLE_S + 60)
    status, _, _ = _req(srv, "POST", "/api/settings", json.dumps({"selected_region": None}),
                        _op(srv, cookie))
    assert status == 401


def test_logout_ends_only_this_browser(server, store):
    srv, _ = server
    a, b = _login(srv, store), _login(srv, store)
    assert _req(srv, "POST", "/logout", "{}", _op(srv, a))[0] == 200
    body = json.dumps({"selected_region": None})
    assert _req(srv, "POST", "/api/settings", body, _op(srv, a))[0] == 401
    assert _req(srv, "POST", "/api/settings", body, _op(srv, b))[0] == 200


# ── 8. every reader the fleet uses still answers without a cookie ────────

# /api/status is a reader too, but it probes the radio port and the suite's
# tx_guard tripwire kills that request — its gate decision is pinned below.
@pytest.mark.parametrize("path", ["/healthz", "/metrics", "/api/nodes/geojson",
                                  "/api/nodes/directory", "/fleet/slo", "/api/settings",
                                  "/api/auth/status"])
def test_readers_need_no_session(server, path):
    srv, _ = server
    status, _, _ = _req(srv, "GET", path)
    assert status != 401, path


def test_status_is_never_an_operator_read():
    assert "/api/status" not in MapRequestHandler.OPERATOR_GETS


def test_refusals_are_counted(server):
    srv, _ = server
    before = json.loads(_req(srv, "GET", "/api/auth/status")[1])["auth"]["refusals"]
    _req(srv, "POST", "/api/radio/message", MSG, JSON)
    after = json.loads(_req(srv, "GET", "/api/auth/status")[1])["auth"]["refusals"]
    assert after == before + 1


def test_auth_status_tells_the_page_which_mode_it_is_in(server, store):
    srv, _ = server
    assert json.loads(_req(srv, "GET", "/api/auth/status")[1])["operator"] is False
    cookie = _login(srv, store)
    out = json.loads(_req(srv, "GET", "/api/auth/status", headers={"Cookie": cookie})[1])
    assert out["operator"] is True


def test_login_answers_while_the_map_is_warming(server, monkeypatch):
    """Login needs no collector; a cold-starting map must not 503 it."""
    srv, _ = server
    monkeypatch.setattr(MapRequestHandler, "is_warming", True)
    assert _req(srv, "GET", "/api/auth/status")[0] == 200
    assert _req(srv, "GET", "/login")[0] == 200


def test_printed_commands_work_from_any_directory(server):
    """The 401, login page and TUI print the CLI for the operator to paste into
    whatever shell is open; repo-relative failed from $HOME (2026-10-13)."""
    from pathlib import Path
    cli = Path(web_auth.PAIR_COMMAND.split()[1])
    assert cli.is_absolute() and cli.is_file(), cli
    srv, _ = server
    assert web_auth.PAIR_COMMAND.encode() in _req(srv, "GET", "/login")[1]
    status, body, _ = _req(srv, "POST", "/api/radio/message", MSG, JSON)
    assert status == 401 and str(cli).encode() in body


def test_401_and_login_page_name_the_no_shell_tui_door(server):
    """2026-11-16: a ham with no shell is pointed at the TUI's Pair action —
    the SAME constant the TUI menu row is built from (web_pair)."""
    import html
    from utils.web_pair import PAIR_TUI_PATH
    srv, _ = server
    page = _req(srv, "GET", "/login")[1].decode("utf-8")
    assert html.escape(PAIR_TUI_PATH) in page and "__TUI__" not in page
    status, body, _ = _req(srv, "POST", "/api/radio/message", MSG, JSON)
    assert status == 401
    assert PAIR_TUI_PATH in json.loads(body)["how"]


# ── 9. the adversarial pass (2026-10-14, scripts/bic3_gate_drill.py) ──────
# Each block below was an ATTACK that succeeded on `95623429`; flipped here
# to assert the gate, red first.

class _FakeProxy:
    """Stands in for the meshtasticd API proxy: records what reached it."""
    def __init__(self):
        self.calls = []

    def get_next_packet(self, client_id):
        self.calls.append(("fromradio", client_id))
        return b""

    def proxy_json(self, path):
        self.calls.append(("json", path))
        return b"{}"


@pytest.fixture
def proxy(monkeypatch):
    fake = _FakeProxy()
    monkeypatch.setattr(MapRequestHandler, "api_proxy", fake, raising=False)
    return fake


@pytest.fixture
def fresh_budget(monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "login_tries", web_auth.TryBudget(), raising=False)


# F1 — OPERATOR_GETS is an exact-path list while GET dispatch matches by
# prefix/alias: every spelling that reaches the fromradio DRAIN or blink must
# meet the gate AT THE CONSUMER, not at the path string.
@pytest.mark.parametrize("path", [
    "/mesh/api/v1/fromradio?all=false", "/mesh/json/blink",
    "/api/v1/fromradio%2Fx", "/api/v1/fromradio.x?all=false", "/api/v1/fromradio/extra",
])
def test_every_spelling_of_a_side_effect_get_is_refused(server, proxy, path):
    srv, _ = server
    status, _, _ = _req(srv, "GET", path, None, JSON)
    assert status == 401, path
    assert proxy.calls == []


def test_an_operator_still_reaches_fromradio_and_blink_by_any_spelling(server, store, proxy):
    srv, _ = server
    cookie = _login(srv, store)
    for path in ("/api/v1/fromradio?all=false", "/mesh/api/v1/fromradio?all=false",
                 "/json/blink", "/mesh/json/blink"):
        status, body, _ = _req(srv, "GET", path, None, {"Cookie": cookie})
        assert status == 200, (path, body)
    assert [c[0] for c in proxy.calls] == ["fromradio", "fromradio", "json", "json"]


# F2 — the Origin check is CSRF defence (a browser cannot forge Origin). It is
# NOT cookie-theft defence: a sniffed cookie replays with two forged headers
# until BIC-13b puts TLS under the cookie. Pinned so the claim stays honest.
def test_a_sniffed_cookie_replays_with_forged_host_and_origin_until_tls(server, store):
    srv, _ = server
    cookie = _login(srv, store)
    port = srv.server_address[1]
    status, _, _ = _req(srv, "POST", "/api/settings", "{}",
                        dict(JSON, Cookie=cookie, Host=f"moc:{port}", Origin=f"http://moc:{port}"))
    assert status != 401


# F3 — `?next=/\evil.com` passed startsWith('/') && !startsWith('//'); browsers
# read a backslash as a slash for http, so that was a post-login open redirect.
def test_login_page_redirects_only_to_this_origin(server):
    srv, _ = server
    status, body, _ = _req(srv, "GET", "/login")
    page = body.decode("utf-8")
    assert status == 200
    assert "new URL(" in page and "origin === location.origin" in page
    assert "startsWith('//')" not in page


# F4 — one hostile LAN client could spend the operator's 5-try pairing code in
# milliseconds. A per-client wrong-code budget sits AHEAD of the shared burn.
def test_one_client_cannot_burn_a_pairing_code(server, store, fresh_budget):
    srv, _ = server
    store.start_pairing()
    statuses = [_req(srv, "POST", "/login", json.dumps({"code": "AAAAAAAA"}),
                     dict(JSON, Origin=_origin(srv)))[0] for _ in range(6)]
    assert statuses[:web_auth.LOGIN_TRIES_PER_CLIENT] == [401] * web_auth.LOGIN_TRIES_PER_CLIENT
    assert set(statuses[web_auth.LOGIN_TRIES_PER_CLIENT:]) == {429}
    assert store.status()["pairing_pending"] is True


def test_a_blocked_client_cannot_redeem_even_the_right_code(server, store, fresh_budget):
    srv, _ = server
    code = store.start_pairing()
    for _ in range(web_auth.LOGIN_TRIES_PER_CLIENT):
        _req(srv, "POST", "/login", json.dumps({"code": "AAAAAAAA"}), dict(JSON, Origin=_origin(srv)))
    status, _, cookie = _req(srv, "POST", "/login", json.dumps({"code": code}),
                             dict(JSON, Origin=_origin(srv)))
    assert status == 429 and cookie is None
    assert store.status()["pairing_pending"] is True


def test_the_budget_is_per_client_and_expires(monkeypatch):
    b = web_auth.TryBudget(limit=2, window_s=100.0)
    now = [1000.0]
    monkeypatch.setattr(web_auth.time, "monotonic", lambda: now[0])
    assert b.wait_s("a") == 0 and b.wait_s("b") == 0
    b.wrong("a"); b.wrong("a")
    assert b.wait_s("a") > 0 and b.wait_s("b") == 0
    now[0] += 101
    assert b.wait_s("a") == 0


# ── 11-14: a phone was logged out 30 d after PAIRING however often it sent ──
# The cookie's Max-Age was set ONCE, at /login, while the server slides its
# idle window on every use — so the browser dropped a cookie the server still
# honoured (operator iPhone 2026-11-14 18:04: 401 "no live operator session in
# this browser", server session last used 11-08, live to 12-08).

def _cookie_parts(set_cookie):
    return [p.strip() for p in (set_cookie or "").split(";")]


def test_a_validated_operator_write_renews_the_cookie(server, store):
    srv, _ = server
    cookie = _login(srv, store)
    status, body, renewed = _req(srv, "POST", "/api/radio/message", MSG, _op(srv, cookie))
    assert status == 200, body
    assert renewed, "a validated write must re-issue the session cookie"
    parts = _cookie_parts(renewed)
    assert parts[0] == cookie, "renewal re-issues the SAME token, never a new session"
    assert f"Max-Age={web_auth.SESSION_IDLE_S}" in parts
    assert "HttpOnly" in parts and "SameSite=Strict" in parts


def test_a_refused_write_sets_no_cookie(server):
    srv, _ = server
    status, _, set_cookie = _req(srv, "POST", "/api/radio/message", MSG, JSON)
    assert status == 401
    assert set_cookie is None


def test_an_ordinary_get_sets_no_cookie_even_with_a_live_session(server, store):
    """Only a request that passed the operator gate renews — a page view does
    not mint lifetime for a cookie the server never checked."""
    srv, _ = server
    cookie = _login(srv, store)
    _, _, set_cookie = _req(srv, "GET", "/api/auth/status", None, {"Cookie": cookie})
    assert set_cookie is None


def test_logout_clears_and_never_renews(server, store):
    srv, _ = server
    cookie = _login(srv, store)
    status, _, set_cookie = _req(srv, "POST", "/logout", "{}", _op(srv, cookie))
    assert status == 200
    assert set_cookie and "Max-Age=0" in set_cookie
    assert cookie not in set_cookie, "logout must not hand the token back"


def test_inbox_401_banner_names_the_expiry_and_links_login():
    """The phone user saw 'open /login (on the box: …)' and nothing they could
    act on; the banner must say WHY and give a tappable /login."""
    import pathlib
    page = (pathlib.Path(__file__).parent.parent / "web" / "inbox.html").read_text()
    i = page.index('j.error === "operator login required"')
    assert "setLoginBanner(" in page[i:i + 120], "the 401 branch must use the login banner"
    j = page.index("function setLoginBanner")
    fn = page[j:page.index("\n  }\n", j)]
    assert "expired" in fn.lower()
    assert 'a.href = "/login"' in fn, "a tappable /login, built as a DOM node"
    assert "innerHTML" not in fn


# ── LOGIN-UX 11-16: the wrong-code budget is per CODE, not per client ──
# Measured 13:36-13:47 (operator phone): a stale code tapped 3x in 4 s spent
# the 3-try budget -> 600 s pause. A code lives 300 s, so a fresh code minted on
# the box during the pause was DEAD ON ARRIVAL ("429s kick in on first try with
# a good code; once resolved the key was stale"). A new console mint = a new
# budget. Guessing stays bounded: 3 wrong per client per code, 5 shared burn.

def _try(srv, code):
    return _req(srv, "POST", "/login", json.dumps({"code": code}), dict(JSON, Origin=_origin(srv)))


def test_a_fresh_code_is_not_dead_on_arrival_after_a_stale_one_spent_the_budget(
        server, store, fresh_budget):
    srv, _ = server
    store.start_pairing()                                   # the stale code
    for _ in range(web_auth.LOGIN_TRIES_PER_CLIENT):
        assert _try(srv, "AAAAAAAA")[0] == 401
    assert _try(srv, "AAAAAAAA")[0] == 429                  # paused, as before
    fresh = store.start_pairing()                           # operator mints again
    status, body, cookie = _try(srv, fresh)
    assert status == 200, body
    assert cookie


def test_a_fresh_code_still_bounds_wrong_tries_from_one_client(server, store, fresh_budget):
    srv, _ = server
    store.start_pairing()
    for _ in range(web_auth.LOGIN_TRIES_PER_CLIENT):
        _try(srv, "AAAAAAAA")
    store.start_pairing()
    statuses = [_try(srv, "AAAAAAAA")[0] for _ in range(web_auth.LOGIN_TRIES_PER_CLIENT + 1)]
    assert statuses == [401] * web_auth.LOGIN_TRIES_PER_CLIENT + [429]
    assert store.status()["pairing_pending"] is True       # one client still cannot burn it


def test_the_pause_says_why_and_when_in_clock_time(server, store, fresh_budget):
    import re
    srv, _ = server
    store.start_pairing()
    for _ in range(web_auth.LOGIN_TRIES_PER_CLIENT):
        _try(srv, "AAAAAAAA")
    status, body, _ = _try(srv, "AAAAAAAA")
    assert status == 429
    j = json.loads(body)
    detail = j["detail"]
    assert f"{web_auth.LOGIN_TRIES_PER_CLIENT} wrong" in detail
    assert "may be fine" in detail                          # a right code is refused here too
    assert "new code" in detail                             # the way out that works now
    assert re.search(r"\b\d{2}:\d{2}\b", detail), detail    # clock time, not "wait 597 s"
    assert isinstance(j.get("retry_after_s"), int) and j["retry_after_s"] > 0


def _stored_pairing(store):
    return json.loads(store.path.read_text())["pairing"]


def test_a_mint_racing_a_wrong_try_cannot_buy_one_client_extra_tries(
        server, store, fresh_budget, monkeypatch):
    """Review r1 MED-1 (11-16): the key is read unlocked, then redeem() judges
    under the lock. A mint landing in between charged the try to the OLD code
    while it spent the NEW code's store tries, so one client reached the new
    code 3 + 3 times and burned it (reviewer: 192/300 trials). Deterministic:
    the mint lands inside the first redeem."""
    srv, _ = server
    store.start_pairing()
    real = store.redeem
    calls = []

    def racing_redeem(code):
        calls.append(code)
        if len(calls) == 1:
            store.start_pairing()          # the operator's mint, inside the window
        return real(code)

    monkeypatch.setattr(store, "redeem", racing_redeem)
    statuses = [_try(srv, "AAAAAAAA")[0] for _ in range(web_auth.LOGIN_TRIES_PER_CLIENT + 1)]
    assert statuses == [401] * web_auth.LOGIN_TRIES_PER_CLIENT + [429]
    assert _stored_pairing(store)["tries"] == web_auth.LOGIN_TRIES_PER_CLIENT


def test_concurrent_racers_and_a_mint_cannot_let_one_client_burn_the_new_code(
        server, store, fresh_budget, monkeypatch):
    """Review r2 MED (11-16): the r1 cure charged the new code only AFTER
    redeem(), so 3 racers keyed on the old code plus 3 takers on the new key
    all reached redeem(): 6 tries on one code from one client, burned at 5
    (reviewer: hammer 186-211/300 burned). The seam is ONE lock around the
    login step (key read -> take -> redeem -> re-read -> charge)."""
    srv, _ = server
    store.start_pairing()
    real = store.redeem
    go = threading.Event()
    entered = []
    lock = threading.Lock()

    def held_redeem(code):
        with lock:
            entered.append(1)
            n = len(entered)
        if n <= 3:
            go.wait(5)
        return real(code)

    monkeypatch.setattr(store, "redeem", held_redeem)
    statuses = []

    def one():
        statuses.append(_try(srv, "AAAAAAAA")[0])

    racers = [threading.Thread(target=one) for _ in range(3)]
    for t in racers:
        t.start()
    deadline = time.monotonic() + 1.0          # unlocked code: all 3 enter; locked: 1
    while len(entered) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    store.start_pairing()                      # the operator's mint, mid-race
    takers = [threading.Thread(target=one) for _ in range(4)]
    for t in takers:
        t.start()
    time.sleep(0.3)
    go.set()
    for t in racers + takers:
        t.join(10)
    p = _stored_pairing(store)
    assert p is not None, "one client burned the fresh code"
    assert p["tries"] <= web_auth.LOGIN_TRIES_PER_CLIENT, (p, statuses)


def test_an_expired_code_charges_no_extra_key(server, store, fresh_budget, monkeypatch):
    """Review r2 mutant M8: nothing pinned the `is not None` guard on the re-read."""
    srv, _ = server
    store.start_pairing()
    d = json.loads(store.path.read_text())
    d["pairing"]["expires"] = time.time() - 1   # expired, still in the file
    store.path.write_text(json.dumps(d))
    assert _try(srv, "AAAAAAAA")[0] == 401
    keys = list(MapRequestHandler.login_tries._tries)
    assert len(keys) == 1 and not keys[0].endswith("|None"), keys


def test_no_valid_until_for_an_expired_code(server, store, fresh_budget, monkeypatch):
    """Review r2 mutant M13."""
    from utils import _map_auth_endpoints as mae
    body = mae._pause_body(30, time.time() - 5)
    assert "valid until" not in body["detail"]
    assert "valid until" in mae._pause_body(30, time.time() + 120)["detail"]


def test_one_paused_client_does_not_pause_another(server, store, fresh_budget, monkeypatch):
    """Review r1 mutant M2 survived: no test used two client addresses."""
    srv, _ = server
    monkeypatch.setattr(MapRequestHandler, "_client_addr",
                        lambda self: self.headers.get("X-Test-Client", "127.0.0.1"))
    code = store.start_pairing()
    for _ in range(web_auth.LOGIN_TRIES_PER_CLIENT):
        _req(srv, "POST", "/login", json.dumps({"code": "AAAAAAAA"}),
             dict(JSON, Origin=_origin(srv), **{"X-Test-Client": "192.0.2.66"}))
    status, _, _ = _req(srv, "POST", "/login", json.dumps({"code": "AAAAAAAA"}),
                        dict(JSON, Origin=_origin(srv), **{"X-Test-Client": "192.0.2.66"}))
    assert status == 429
    status, body, cookie = _req(srv, "POST", "/login", json.dumps({"code": code}),
                                dict(JSON, Origin=_origin(srv), **{"X-Test-Client": "192.0.2.7"}))
    assert status == 200 and cookie, body


def test_a_corrupt_store_says_so_on_login_and_charges_no_try(server, store, fresh_budget):
    """Review r1 LOW-2: the key read moved the corrupt file aside and swallowed
    the error, so redeem() saw 'never paired' and the browser read 'wrong code'."""
    srv, _ = server
    store.start_pairing()
    store.path.write_text("{not json")
    status, body, _ = _try(srv, "AAAAAAAA")
    assert status == 401
    assert "corrupt" in json.loads(body)["detail"]
    assert MapRequestHandler.login_tries._tries == {}


def test_an_unreadable_store_counts_one_error_per_login(server, store, fresh_budget):
    """Review r1 LOW-3 (+M8): two reads per login counted every failure twice."""
    srv, _ = server
    store.start_pairing()
    os.chmod(store.path, 0)
    try:
        before = store.store_errors
        status, _, _ = _try(srv, "AAAAAAAA")
        assert status == 401
        assert store.store_errors == before + 1
        assert MapRequestHandler.login_tries._tries == {}
    finally:
        os.chmod(store.path, 0o600)


@pytest.mark.parametrize("expires", [1e30, float("inf")])
def test_the_pause_still_answers_with_an_absurd_expiry_in_the_store(
        server, store, fresh_budget, expires):
    """Review r1 LOW-4: _clock() raised OverflowError and the connection dropped."""
    srv, _ = server
    store.start_pairing()
    d = json.loads(store.path.read_text())
    d["pairing"]["expires"] = expires
    store.path.write_text(json.dumps(d))
    for _ in range(web_auth.LOGIN_TRIES_PER_CLIENT):
        _try(srv, "AAAAAAAA")
    status, body, _ = _try(srv, "AAAAAAAA")
    assert status == 429
    assert "valid until" not in json.loads(body)["detail"]


def test_the_login_button_is_disabled_while_a_try_is_in_flight(server):
    """The 13:36 burst: 3 taps in 4 s each spent a try before the first reply came back."""
    srv, _ = server
    page = _req(srv, "GET", "/login")[1].decode("utf-8")
    submit = page[page.index("addEventListener('submit'"):page.index("</script>")]
    assert ".disabled = true" in submit
    assert submit.index(".disabled = true") < submit.index("await fetch('/login'")
    assert "finally" in submit and ".disabled = false" in submit[submit.index("finally"):]


# ── LOGIN-UX (3), 11-16: /login says when this browser is ALREADY logged in ──
# Client-side on purpose: GET /login stays free of store reads/side effects;
# the page asks the existing /api/auth/status. It claims "logged in" only on
# operator === true; a failed or odd answer shows nothing (never a false yes).

def _login_page(srv):
    return _req(srv, "GET", "/login")[1].decode("utf-8")


def test_login_page_asks_status_and_claims_logged_in_only_on_true(server):
    srv, _ = server
    page = _login_page(srv)
    script = page[page.index("<script>"):page.index("</script>")]
    assert "fetch('/api/auth/status'" in script
    i = script.index("fetch('/api/auth/status'")
    block = script[i:i + 900]
    assert "j.operator === true" in block, "only a literal true may claim a live session"
    assert "already logged in" in block.lower()
    assert "innerHTML" not in script
    assert "catch" in block, "a failed status read must show nothing, not throw"


def test_status_says_operator_true_for_a_logged_in_browser_and_false_without(server, store):
    """The answer the page relies on, through the real handler."""
    srv, _ = server
    cookie = _login(srv, store)
    st = json.loads(_req(srv, "GET", "/api/auth/status", None, {"Cookie": cookie})[1])
    assert st["operator"] is True
    st = json.loads(_req(srv, "GET", "/api/auth/status")[1])
    assert st["operator"] is False


def test_login_page_get_reads_no_store(server, store, monkeypatch):
    """GET /login must stay side-effect free: no store read at all."""
    srv, _ = server
    calls = []
    real = store._read
    monkeypatch.setattr(store, "_read", lambda: calls.append(1) or real())
    status, _, _ = _req(srv, "GET", "/login")
    assert status == 200 and calls == []
