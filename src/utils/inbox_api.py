"""MOC Inbox (BIC-1): one thread list over the messages store, for a browser.

``/inbox`` is the page; ``GET /api/inbox`` and ``POST /api/inbox/send`` are
its API. Pure functions here, the HTTP gates stay in ``map_http_handler``.

Honesty rules this module keeps (honest_failure_modes #1, #2):
  * a store that cannot be read is ``unavailable`` — never an empty inbox;
  * an outbound row reads ``handed_off`` / ``not_sent`` — the store's
    ``delivered`` column means "the send call handed it to the radio",
    which is not delivery, so the word "delivered" is never shown;
  * RNS threads are read-only: ``commands.gateway._get_bridge()`` builds a
    NEW unstarted bridge in the calling process, so a send from the map
    process would never reach the running gateway daemon. They say so.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from commands import messaging

logger = logging.getLogger(__name__)

#: Every spelling the stores use for "no single recipient".
BROADCAST_IDS = (None, "", "^all", "!ffffffff")

#: Meshtastic text payload is ~233 bytes; stay under it with headroom.
MAX_TEXT_BYTES = 200

#: Meshtastic channel indices are 0 (primary) .. 7.
MAX_CHANNEL = 7

RNS_SEND_BLOCKED = (
    "Replying over RNS needs the gateway send channel, which is not wired "
    "to the web inbox yet. Reply from the TUI (Messaging) for now."
)

_NODE_ID = re.compile(r"^![0-9a-fA-F]{1,8}$")


def _thread_key(msg: Dict[str, Any]) -> Tuple[str, str, Optional[str], Optional[int]]:
    """(key, kind, partner, channel) for one stored row."""
    network = msg.get("network") or "unknown"
    outbound = msg.get("from_id") == "local"
    peer = msg.get("to_id") if outbound else msg.get("from_id")
    if network == "rns":
        # LXMF is always point-to-point; inbound rows are stored to_id=None.
        return f"rns:{peer}", "dm", peer, None
    if msg.get("to_id") in BROADCAST_IDS:
        ch = msg.get("channel") or 0
        return f"{network}:channel:{ch}", "channel", None, ch
    return f"{network}:{peer}", "dm", peer, None


_HAS_OFFSET = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def _utc(ts: Optional[str]) -> Optional[str]:
    """Tag a stored timestamp as UTC. Rows are written with SQLite
    ``CURRENT_TIMESTAMP`` (UTC, no offset); a browser reads an offset-less
    ISO time as LOCAL, so on an HST box every message showed 10 h off."""
    if not ts or _HAS_OFFSET.search(ts):
        return ts
    return ts.replace(" ", "T") + "+00:00"


def _state(msg: Dict[str, Any]) -> str:
    if msg.get("from_id") != "local":
        return "received"
    return "handed_off" if msg.get("delivered") else "not_sent"


def build_threads(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group stored rows into conversations, newest conversation first."""
    threads: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        key, kind, partner, channel = _thread_key(msg)
        t = threads.get(key)
        if t is None:
            network = msg.get("network") or "unknown"
            can_send = network == "meshtastic"
            t = threads[key] = {
                "key": key, "network": network, "kind": kind,
                "partner": partner, "channel": channel,
                "can_send": can_send,
                "send_blocked_reason": None if can_send else (
                    RNS_SEND_BLOCKED if network == "rns"
                    else f"Sending on {network} is not supported from the inbox."),
                "messages": [],
            }
        t["messages"].append({
            "id": msg.get("id"),
            "ts": _utc(msg.get("timestamp")),
            "direction": "out" if msg.get("from_id") == "local" else "in",
            "from_id": msg.get("from_id"),
            "content": msg.get("content", ""),
            "snr": msg.get("snr"),
            "rssi": msg.get("rssi"),
            "state": _state(msg),
        })
    out = []
    for t in threads.values():
        t["messages"].sort(key=lambda m: (m["ts"] or "", m["id"] or 0))
        last = t["messages"][-1]
        t["last_ts"] = last["ts"]
        t["preview"] = last["content"][:80]
        t["count"] = len(t["messages"])
        out.append(t)
    out.sort(key=lambda t: t["last_ts"] or "", reverse=True)
    return out


def read_inbox(limit: int = 500) -> Dict[str, Any]:
    """Tri-state read: ``ok`` with threads, or ``unavailable`` with the reason."""
    try:
        result = messaging.get_messages(limit=limit, network="all")
    except Exception as e:  # the store itself raised — still unobservable, not empty
        logger.warning("inbox: message store raised: %s", e)
        return {"state": "unavailable", "error": f"message store error: {e}"}
    if not result.success:
        return {"state": "unavailable", "error": result.message}
    rows = (result.data or {}).get("messages", [])
    return {"state": "ok", "threads": build_threads(rows), "message_count": len(rows),
            "rx": _rx_coverage()}


def _rx_coverage() -> Dict[str, Any]:
    """What the receive path can actually hear (2026-10-13): an empty inbox
    beside a radio that journals texts is a blind listener, not a quiet mesh.
    The page names heard vs not-heard channels; unknown is said, not assumed."""
    try:
        from utils.message_listener import get_listener_status
        st = get_listener_status()
        return {k: st.get(k) for k in ("state", "messages_received", "channels_seen",
                                       "subscription", "connected_since", "error")}
    except Exception as e:
        logger.debug("inbox: listener status unreadable: %s", e)
        return {"state": "unknown"}


def validate_send(data: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (params, None) for a sendable request, or (None, reason)."""
    if not isinstance(data, dict):
        return None, "body must be a JSON object"
    network = data.get("network")
    if network == "rns":
        return None, RNS_SEND_BLOCKED
    if network != "meshtastic":
        return None, "network must be 'meshtastic'"
    text = data.get("text")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        return None, f"text is required (1-{MAX_TEXT_BYTES} bytes)"
    channel = data.get("channel", 0)
    if isinstance(channel, bool) or not isinstance(channel, int) or not 0 <= channel <= MAX_CHANNEL:
        return None, f"channel must be an integer 0-{MAX_CHANNEL}"
    destination = data.get("destination", "^all")
    if destination in BROADCAST_IDS:
        return {"text": text, "channel": channel, "dest_num": None, "to_id": None}, None
    if not isinstance(destination, str) or not _NODE_ID.match(destination):
        return None, "destination must be '^all' or a node id like !a1b2c3d4"
    return {"text": text, "channel": channel,
            "dest_num": int(destination[1:], 16), "to_id": destination.lower()}, None


def send(params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Transmit a validated Meshtastic message and record it. Returns (HTTP status, body)."""
    from gateway.meshtastic_protobuf_client import send_text_direct_with_id
    try:
        packet_id = send_text_direct_with_id(
            text=params["text"], destination=params["dest_num"],
            channel_index=params["channel"],
            want_ack=params["dest_num"] is not None)
    except Exception as e:
        logger.warning("inbox: transmit raised: %s", e)
        packet_id = None
    handed_off = packet_id is not None
    rec = messaging.store_outgoing(
        content=params["text"], network="meshtastic", to_id=params["to_id"],
        channel=params["channel"], delivered=handed_off)
    body = {
        "state": "handed_off" if handed_off else "not_sent",
        "detail": ("Handed to the radio. Delivery is unconfirmed." if handed_off
                   else "The radio did not accept the message (is meshtasticd running?)."),
        "recorded": bool(rec.success),
    }
    if not rec.success:
        body["record_error"] = rec.message
    return (200 if handed_off else 502), body
