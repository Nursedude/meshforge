"""
Base class for gateway message handlers (ABC).

All gateway handlers (Meshtastic, MQTT, MeshCore) share a common constructor
signature and interface. This ABC codifies that contract and provides shared
concrete methods to eliminate duplication.
"""

from abc import ABC, abstractmethod
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from utils.defaults import MAX_MESHTASTIC_MSG_LENGTH

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .config import GatewayConfig
    from .node_tracker import UnifiedNodeTracker

logger = logging.getLogger(__name__)


def is_already_bridged(text: str) -> bool:
    """True if ``text`` carries a leading ``[RNS:…]`` bridge-routing tag.

    Such content was injected onto the mesh FROM the RNS network by a
    gateway, so it is already present in RNS. Bridging it back to RNS
    (Mesh→RNS) is always a loop / duplicate. The original self-echo filter
    only caught a gateway's OWN echo (sender == its node); on a shared-RNS
    fleet where several gateways sit on the same channel, each one hears the
    others' injections and would otherwise re-bridge them — so the guard
    must fire regardless of sender. Genuine operator content (web UI / CLI
    sends) has no ``[RNS:]`` prefix and is unaffected.

    Scoped to ``[RNS:]`` (MeshForge's own injection marker) — the
    demonstrated loop. MeshAnchor/MeshCore tags (``[MC:]``, ``[ch0:]``) are
    deliberately NOT included here to avoid touching cross-project interop.
    """
    return bool(text) and text.lstrip().startswith("[RNS:")


def chunk_for_mesh(message: str,
                   max_bytes: int = MAX_MESHTASTIC_MSG_LENGTH) -> List[str]:
    """Split text into UTF-8-byte-bounded chunks for Meshtastic TX.

    Meshtastic's on-air text payload is capped (``DATA_PAYLOAD_LEN`` = 237
    bytes); meshtasticd silently truncates anything larger. Multi-line
    bridge output relayed RNS→Mesh (e.g. the bot's ``leaderboard`` / ``wx``
    replies) used to be cut to one packet by ``_truncate_if_needed``,
    dropping every line past the cap. This chunker splits such content
    into multiple packets instead, each guaranteed ≤ ``max_bytes`` UTF-8
    bytes, so no content is lost.

    Boundaries, in preference order: newline (keeps whole lines together —
    how the leaderboard reads), then word, then — only for a single word
    longer than the budget — a hard UTF-8-safe character split.

    Returns at least one chunk for non-empty input; never returns empty
    strings; never exceeds ``max_bytes`` for any chunk. A message that
    already fits is returned unchanged as a single-element list.
    """
    if not message:
        return []
    if len(message.encode('utf-8')) <= max_bytes:
        return [message]

    def blen(s: str) -> int:
        return len(s.encode('utf-8'))

    def char_split(token: str) -> List[str]:
        # Separator-less token longer than the budget: cut on character
        # (codepoint) boundaries so we never split a multi-byte emoji.
        out: List[str] = []
        cur = ""
        for ch in token:
            if cur and blen(cur) + blen(ch) > max_bytes:
                out.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out

    def pack(atoms: List[str], sep: str) -> List[str]:
        out: List[str] = []
        cur = ""
        for atom in atoms:
            add = blen(atom) + (blen(sep) if cur else 0)
            if cur and blen(cur) + add > max_bytes:
                out.append(cur)
                cur = ""
            if not cur:
                if blen(atom) <= max_bytes:
                    cur = atom
                else:
                    # Atom itself exceeds the budget — split finer: by
                    # word if it has spaces, else by character.
                    finer = pack(atom.split(' '), ' ') if ' ' in atom \
                        else char_split(atom)
                    if finer:
                        out.extend(finer[:-1])
                        cur = finer[-1]
            else:
                cur = cur + sep + atom
        if cur:
            out.append(cur)
        return out

    return pack(message.split('\n'), '\n')


class BaseMessageHandler(ABC):
    """Abstract base for network message handlers."""

    def __init__(
        self,
        config: 'GatewayConfig',
        node_tracker: 'UnifiedNodeTracker',
        health: 'BridgeHealthMonitor',
        stop_event: threading.Event,
        stats: Dict[str, Any],
        stats_lock: threading.Lock,
        message_queue,
        message_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        should_bridge: Optional[Callable] = None,
    ):
        self.config = config
        self.node_tracker = node_tracker
        self.health = health
        self._stop_event = stop_event
        self.stats = stats
        self._stats_lock = stats_lock
        self._message_queue = message_queue
        self._message_callback = message_callback
        self._status_callback = status_callback
        self._should_bridge = should_bridge
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if handler is connected."""
        return self._connected

    @abstractmethod
    def run_loop(self) -> None:
        """Main loop — blocks until stop_event is set."""
        ...

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect and clean up resources."""
        ...

    @abstractmethod
    def send_text(self, message: str, destination: Optional[str] = None,
                  channel: int = 0) -> bool:
        """Send a text message. Returns True on success."""
        ...

    @abstractmethod
    def queue_send(self, payload: Dict) -> bool:
        """Send from persistent queue. Returns True on success."""
        ...

    @abstractmethod
    def test_connection(self) -> bool:
        """Test if the underlying transport is reachable."""
        ...

    def _notify_status(self, status: str) -> None:
        """Notify status callback."""
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

    def _truncate_if_needed(self, message: str,
                            max_length: int = MAX_MESHTASTIC_MSG_LENGTH) -> str:
        """Last-resort byte-limit guard for a SINGLE Meshtastic packet.

        Callers that may handle multi-line/long content (e.g. the RNS→Mesh
        bridge) must pre-split with ``chunk_for_mesh`` so this never fires —
        truncation drops data. If it does fire, that is a bug (a long send
        that bypassed chunking), so it logs at ERROR, not WARNING.
        """
        msg_bytes = message.encode('utf-8')
        if len(msg_bytes) > max_length:
            logger.error(
                f"Message exceeds limit ({len(msg_bytes)} > {max_length} "
                f"bytes) and was NOT chunked — TRUNCATING (data lost). "
                f"This is a bug: the sender should use chunk_for_mesh()."
            )
            truncated = msg_bytes[:max_length - 3]
            return truncated.decode('utf-8', errors='ignore') + '...'
        return message
