"""Browser chat inbox routes (BIC-1, landed 2026-10-13).

``/inbox`` is the page, ``GET /api/inbox`` the thread list, ``POST
/api/inbox/send`` a Meshtastic reply. Pure logic lives in ``utils.inbox_api``.
The send is a POST, so BIC-3's default-deny operator gate runs before it is
ever reached; the LAN and cross-site gates below stay as further layers.
"""
import json
from pathlib import Path


class InboxEndpointsMixin:
    """Mixed into MapRequestHandler (needs its gates, ``_serve_json``,
    ``_send_security_headers``, ``_MAX_MESSAGE_BODY``, ``web_dir``)."""

    def _serve_inbox_page(self):
        """Serve the browser chat inbox (`/inbox` → web/inbox.html, BIC-1)."""
        base = Path(self.web_dir) if self.web_dir else Path(__file__).parent.parent.parent / "web"
        page = base / "inbox.html"
        if not page.exists():
            self.send_error(404, "Inbox page not found")
            return
        data = page.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _serve_inbox(self):
        """GET /api/inbox — conversations, gated like /api/messages/received.

        An unreadable store answers 503 + ``state: unavailable``, never an
        empty 200 (that is how "no messages" and "can't see" diverge)."""
        if self._reject_if_untrusted():
            return
        from utils import inbox_api
        out = inbox_api.read_inbox()
        self._serve_json(out, status=200 if out["state"] == "ok" else 503)

    def _handle_inbox_send(self):
        """POST /api/inbox/send — same gates as /api/radio/message (it keys
        a licensed transmitter), then ``inbox_api`` validates, sends, records."""
        if self._reject_if_untrusted() or self._reject_cross_site_write():
            return
        from utils import inbox_api
        try:
            length = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > self._MAX_MESSAGE_BODY:
            self._serve_json({"error": "Invalid or oversized payload"}, status=400)
            return
        try:
            data = json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            self._serve_json({"error": "Invalid JSON"}, status=400)
            return
        params, err = inbox_api.validate_send(data)
        if err:
            self._serve_json({"error": err}, status=400)
            return
        status, body = inbox_api.send(params)
        self._serve_json(body, status=status)
