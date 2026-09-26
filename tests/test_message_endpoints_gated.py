"""Message endpoints admit exactly who the read gate admits (2026-09-26).

/api/messages/received served message TEXT to any client that reached :5000
while the WebSocket pushing the same messages was already gated. The queue
endpoint (who-talks-to-whom) is its sibling and gets the same gate.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils.map_http_handler import MapRequestHandler

LAN = ["http://localhost", "http://127.0.0.1", "http://192.0.2."]


# Never read the box's real messages.db from a test (2026-09-23 class).
_EMPTY = SimpleNamespace(success=True, data={"messages": []})


def _handler(path, client):
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = path
    h.headers = {}
    h.client_address = (client, 5000)
    h.allowed_origins = LAN
    h.collector = None
    h.served = []
    h._serve_json = lambda payload, status=200: h.served.append((status, payload))
    return h


@pytest.mark.parametrize("method,path", [
    ("_serve_received_messages", "/api/messages/received?limit=500"),
    ("_serve_message_queue", "/api/messages/queue"),
])
class TestMessageEndpointsGated:
    def test_untrusted_client_gets_403_and_no_messages(self, method, path):
        h = _handler(path, "203.0.113.9")
        with patch("utils.map_http_handler._HAS_MSG_QUEUE", False), \
             patch("utils.map_http_handler.messaging.get_messages",
                   return_value=_EMPTY) as gm:
            getattr(h, method)()
        if client_is_untrusted(h):
            gm.assert_not_called()  # refused BEFORE any read
        assert len(h.served) == 1
        status, payload = h.served[0]
        assert status == 403
        assert "messages" not in payload

    @pytest.mark.parametrize("client", ["127.0.0.1", "192.0.2.41"])
    def test_trusted_client_still_answered(self, method, path, client):
        h = _handler(path, client)
        with patch("utils.map_http_handler._HAS_MSG_QUEUE", False), \
             patch("utils.map_http_handler.messaging.get_messages",
                   return_value=_EMPTY) as gm:
            getattr(h, method)()
        if client_is_untrusted(h):
            gm.assert_not_called()  # refused BEFORE any read
        status, payload = h.served[-1]
        assert status == 200
        assert "messages" in payload


def client_is_untrusted(h):
    return h.client_address[0] == "203.0.113.9"
