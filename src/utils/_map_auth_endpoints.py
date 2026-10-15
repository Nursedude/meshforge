"""Operator gate + login/logout for the map service (BIC-3, 2026-10-13).

DEFAULT-DENY: every POST/PUT needs a live operator session unless its path is
in ``SESSIONLESS_WRITES`` (only the login exchange itself), and the GETs with
side effects are listed in ``OPERATOR_GETS``. A new write route is therefore
gated the day it is written; nobody has to remember to add it (a denylist
defaults every new field to exposed — the 09-19 lesson).

The gate is a layer ON TOP of the Host, LAN and cross-site checks, never a
replacement: a request must pass all of them. Spec:
``.claude/plans/bic3_web_auth_2026_10_13.md``.
"""
from __future__ import annotations

import html
import json
from typing import Optional

from utils import web_auth
from utils._map_trust_gate import browser_origin_allowed

_LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MeshForge operator login</title>
<style>body{font:16px system-ui,sans-serif;max-width:26rem;margin:3rem auto;padding:0 1rem;
color:#17202a;background:#f6f7f8}input,button{font:inherit;padding:.5rem .7rem}
input{width:100%;box-sizing:border-box;letter-spacing:.2em;text-transform:uppercase}
button{margin-top:.8rem}code{background:#fff;border:1px solid #ccc;padding:0 .3em}
@media (prefers-color-scheme:dark){body{color:#e4e8ec;background:#12171c}code{background:#1a2027}}
</style></head><body>
<h1>Operator login</h1>
<p>Viewing needs no login. To send or change settings from this browser, run this on the box
(SSH or console), then type the code it prints:</p>
<p><code>__PAIR__</code></p>
<form id="f"><label for="c">Pairing code</label>
<input id="c" autocomplete="one-time-code" maxlength="12" required autofocus>
<button type="submit">Log in</button></form>
<p id="m" role="status"></p>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const m = document.getElementById('m');
  m.textContent = 'Checking…';
  try {
    const r = await fetch('/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code: document.getElementById('c').value})});
    const j = await r.json();
    if (r.ok) { m.textContent = 'Logged in as operator.';
      const back = new URLSearchParams(location.search).get('next');
      let dest = '/';
      try { const u = new URL(back || '/', location.origin); if (u.origin === location.origin) dest = u.href; } catch (e) {}
      location.href = dest; }
    else { m.textContent = j.detail || j.error || ('Refused (' + r.status + ')'); }
  } catch (err) { m.textContent = 'Could not reach the box: ' + err; }
});
</script></body></html>
"""


class AuthEndpointsMixin:
    """Mixed into MapRequestHandler. Needs ``_serve_json``, ``_client_is_trusted``,
    ``_send_security_headers``, ``allowed_origins``, ``headers``, ``server``."""

    auth_store: Optional[web_auth.SessionStore] = None
    auth_refusals = web_auth.RefusalLog()
    #: F4: per-client budget of wrong pairing codes, ahead of the shared burn.
    login_tries = web_auth.TryBudget()

    SESSIONLESS_WRITES = frozenset({"/login", "/logout"})
    OPERATOR_GETS = frozenset({"/fleet/logs", "/api/v1/fromradio", "/json/blink"})

    # ── plumbing ────────────────────────────────────────────────────────

    @classmethod
    def _auth(cls) -> web_auth.SessionStore:
        if cls.auth_store is None:
            cls.auth_store = web_auth.SessionStore()
        return cls.auth_store

    def _client_addr(self) -> str:
        try:
            return str(self.client_address[0])
        except (IndexError, AttributeError, TypeError):
            return "unknown"

    def _origin_ok(self) -> bool:
        """A PRESENT Origin that the map trusts — CSRF defence: a browser on
        a hostile page cannot forge Origin. It is NOT cookie-theft defence: a
        cookie sniffed off plain HTTP replays from curl with two forged
        headers (Host + Origin naming any single-label host), accepted until
        BIC-13b puts TLS under the cookie (adversarial pass 2026-10-14, F2)."""
        origin = self.headers.get("Origin")
        if not origin:
            return False
        try:
            page_port = self.server.server_address[1]
        except (AttributeError, IndexError, TypeError):
            page_port = None
        return browser_origin_allowed(origin, self.allowed_origins,
                                      self.headers.get("Host"), page_port)

    def _refuse_operator(self, method: str, path: str, detail: str) -> None:
        self.auth_refusals.note(self._client_addr(), method, path, detail)
        self._serve_json({"error": "operator login required",
                          "how": f"on the box: {web_auth.PAIR_COMMAND}, then open /login",
                          "detail": detail}, status=401)

    # ── the gate ────────────────────────────────────────────────────────

    def _refuse_without_operator(self, method: str, path: str) -> bool:
        """Send 401 + return True unless this request carries a live operator
        session from a trusted LAN browser on this page. On success
        ``_operator_ok`` is set for THIS request so a consumer-side re-check
        (``_refuse_side_effect_get``) costs no second store read."""
        self._operator_ok = False
        if method == "GET" and path not in self.OPERATOR_GETS:
            return False
        if method != "GET" and path in self.SESSIONLESS_WRITES:
            return False
        if not self._client_is_trusted():
            self._refuse_operator(method, path, "this client is not on the box's trusted LAN")
            return True
        token = web_auth.cookie_token(self.headers.get("Cookie"))
        try:
            live = self._auth().validate(token)
        except web_auth.StoreUnavailable as e:
            self._refuse_operator(method, path, str(e))
            return True
        if not live:
            self._refuse_operator(method, path, "no live operator session in this browser")
            return True
        if method != "GET" and not self._origin_ok():
            self._refuse_operator(method, path,
                                  "a session cookie without this page's Origin is not this browser")
            return True
        self._operator_ok = True
        return False

    def _refuse_side_effect_get(self, canonical: str) -> bool:
        """The gate at the CONSUMER (adversarial pass 2026-10-14, F1): a
        side-effect GET handler calls this with its canonical path, so every
        spelling that dispatches to it — the ``/mesh/`` alias, a percent-
        encoded slash, a dot or extra segment the exact-path list never saw —
        meets the same gate. ``OPERATOR_GETS`` stays as the outer layer; a
        request it already admitted is not re-validated."""
        if getattr(self, "_operator_ok", False):
            return False
        return self._refuse_without_operator("GET", canonical)

    # ── routes ──────────────────────────────────────────────────────────

    def _serve_login_page(self) -> None:
        body = _LOGIN_PAGE.replace("__PAIR__", html.escape(web_auth.PAIR_COMMAND)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_auth_status(self) -> None:
        """Which mode is this browser in — so the page can show it."""
        try:
            operator = self._auth().validate(web_auth.cookie_token(self.headers.get("Cookie")))
            out = {"operator": operator, "store": "ok"}
        except web_auth.StoreUnavailable as e:
            out = {"operator": False, "store": "unavailable", "detail": str(e)}
        out["login"] = "/login"
        out["auth"] = self.auth_status_block()
        self._serve_json(out)

    def _read_json_body(self, limit: int = 1024) -> Optional[dict]:
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._serve_json({"error": "unsupported media type",
                              "detail": "Send the body as JSON with 'Content-Type: application/json'."},
                             status=415)
            return None
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n < 0 or n > limit:
                raise ValueError("size")
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("shape")
            return data
        except (ValueError, TypeError):
            self._serve_json({"error": "bad request", "detail": "expected a small JSON object"},
                             status=400)
            return None

    def _handle_login(self) -> None:
        if not self._client_is_trusted() or not self._origin_ok():
            self.auth_refusals.note(self._client_addr(), "POST", "/login", "untrusted origin/client")
            self._serve_json({"error": "forbidden",
                              "detail": "log in from the map page on the box's own LAN"}, status=403)
            return
        data = self._read_json_body()
        if data is None:
            return
        client = self._client_addr()
        wait = self.login_tries.take(client)
        if wait > 0:
            self.auth_refusals.note(client, "POST", "/login", "wrong-code budget spent")
            self._serve_json({"error": "too many wrong codes from this client",
                              "detail": f"wait {int(wait) + 1} s, then try again"},
                             status=429)
            return
        try:
            token = self._auth().redeem(str(data.get("code", "")))
        except web_auth.StoreUnavailable as e:
            self.login_tries.refund(client)
            self._refuse_operator("POST", "/login", str(e))
            return
        if not token:
            self._refuse_operator("POST", "/login",
                                  "that code is wrong, used, expired or burned — run pair again")
            return
        self.login_tries.refund(client)   # a right code is not a wrong try
        body = json.dumps({"operator": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", web_auth.set_cookie_value(token))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_logout(self) -> None:
        if not self._origin_ok():
            self._serve_json({"error": "forbidden", "detail": "log out from the map page"},
                             status=403)
            return
        try:
            self._auth().end(web_auth.cookie_token(self.headers.get("Cookie")))
        except web_auth.StoreUnavailable:
            pass  # nothing to end; the cookie is cleared below either way
        body = json.dumps({"operator": False}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", web_auth.clear_cookie_value())
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def auth_status_block(self) -> dict:
        """For /api/status: counts only (unauthenticated endpoint)."""
        block = {"refusals": self.auth_refusals.count}
        try:
            st = self._auth().status()
            block.update(store="ok", sessions=st["sessions"])
        except web_auth.StoreUnavailable:
            block["store"] = "unavailable"
        block["store_errors"] = self._auth().store_errors
        return block
