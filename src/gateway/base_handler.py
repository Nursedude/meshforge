"""
Base class for gateway message handlers (ABC).

All gateway handlers (Meshtastic, MQTT, MeshCore) share a common constructor
signature and interface. This ABC codifies that contract and provides shared
concrete methods to eliminate duplication.
"""

from abc import ABC, abstractmethod
import hashlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from utils.defaults import MAX_MESHTASTIC_MSG_LENGTH

if TYPE_CHECKING:
    from .bridge_health import BridgeHealthMonitor
    from .config import GatewayConfig
    from .node_tracker import UnifiedNodeTracker

logger = logging.getLogger(__name__)


# Any leading bridge tag means the content already took a bridge detour
# and is present in the LXMF/RNS world — re-bridging it Mesh→RNS loops.
# Mirrors MeshAnchor's ECHO_LOOP_INVARIANT_PREFIXES (gateway/config.py)
# — keep the two lists in sync when adding a tag on either side.
#   "[RNS:"      MeshForge RNS→Mesh injection marker
#   "[MC:" / "[MeshCore]"  MeshAnchor MeshCore→Meshtastic egress
#   "[ch0:" / "[ch1:"      MeshAnchor LXMFBroadcast fan-out tags
#   "[Mesh:"     mesh→LXMF aggregation prefix (Issue #35)
BRIDGE_TAG_PREFIXES = (
    "[MeshCore]", "[MC:", "[RNS:", "[ch0:", "[ch1:", "[Mesh:",
)


def is_already_bridged(text: str) -> bool:
    """True if ``text`` carries a leading bridge-routing tag.

    Such content was injected onto the mesh FROM the RNS/LXMF world by a
    bridge, so it is already present there. Bridging it back to RNS
    (Mesh→RNS) is always a loop / duplicate. The original self-echo filter
    only caught a gateway's OWN echo (sender == its node); on a shared-RNS
    fleet where several gateways sit on the same channel, each one hears the
    others' injections and would otherwise re-bridge them — so the guard
    must fire regardless of sender. Genuine operator content (web UI / CLI
    sends) has no bridge prefix and is unaffected.

    Originally scoped to ``[RNS:]`` only, with MeshCore tags deliberately
    excluded. That exclusion was disproven live on moc (2026-06-03): bare
    ``[MC:p4]`` content injected through the gateway's own radio was
    re-bridged M→R to the full LXMF fan-out AND Phase-1 relayed to peer
    gateways — an echo amplifier. Now mirrors MeshAnchor's
    ``nested_drop_prefixes`` invariant list.
    """
    if not text:
        return False
    return text.lstrip().startswith(BRIDGE_TAG_PREFIXES)


def _strip_bridge_tags(text: str) -> str:
    """Remove LEADING bridge tags ([Mesh:..] / [RNS:..] / ...) iteratively.

    Normalization helper for RecentRfTxRegistry: the same logical content
    appears raw on one path (true-origin downlink injects ``msg.content``)
    and tagged on another (``[RNS:xxxx] msg.content`` toradio copy), so tag
    stripping is what lets the two match. Stops at the first non-tag text or
    a malformed tag (no closing bracket).
    """
    out = text.lstrip()
    while out.startswith(BRIDGE_TAG_PREFIXES):
        close = out.find(']')
        if close < 0:
            break
        out = out[close + 1:].lstrip()
    return out


class RecentRfTxRegistry:
    """Cross-subsystem "recently transmitted on the primary radio" registry.

    Two independent, both-by-design paths can put the SAME logical content
    on a gateway's primary radio seconds apart (observed live 2026-06-04):

      A. the local mesh_bridge cross-preset forward (true-origin downlink,
         or tagged toradio fallback), and
      B. a peer gateway's Mesh→RNS relay arriving back at this box's
         rns_bridge and going out as a tagged toradio broadcast.

    Suppressing path B unconditionally loses messages — in the live trace
    ~40% of relayed events arrived ONLY via B (the local radio missed them
    on RF). This registry implements the safe middle: path A registers what
    it actually transmitted; path B suppresses its copy only on a hit, so
    the relay keeps its fallback value while the visible duplicate dies.

    Keys are content-normalized (leading bridge tags stripped, whitespace
    collapsed, sha256) so ``[RNS:xx] hello`` matches the raw ``hello`` a
    downlink injected. Thread-safe; entries expire lazily at ``max_age_s``
    and the table is bounded by ``max_entries`` (oldest evicted).
    """

    def __init__(self, max_entries: int = 512, max_age_s: float = 300.0):
        self._max_entries = max_entries
        self._max_age_s = max_age_s
        self._entries: Dict[str, float] = {}   # key -> time.monotonic()
        self._lock = threading.Lock()

    @staticmethod
    def _key(text: str) -> Optional[str]:
        normalized = " ".join(_strip_bridge_tags(text or "").split())
        if not normalized:
            return None
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._max_age_s
        stale = [k for k, ts in self._entries.items() if ts < cutoff]
        for k in stale:
            del self._entries[k]
        while len(self._entries) > self._max_entries:
            oldest = min(self._entries, key=self._entries.get)
            del self._entries[oldest]

    def register(self, text: str) -> None:
        """Record that ``text`` was just transmitted on the radio."""
        key = self._key(text)
        if key is None:
            return
        now = time.monotonic()
        with self._lock:
            self._entries[key] = now
            self._prune_locked(now)

    def seen_within(self, text: str, window_s: float) -> bool:
        """True if equivalent content was registered in the last window_s."""
        key = self._key(text)
        if key is None:
            return False
        now = time.monotonic()
        with self._lock:
            ts = self._entries.get(key)
            return ts is not None and (now - ts) <= window_s


# Process-wide instance — the composable bridges (mesh_bridge + rns_bridge)
# are built independently by bridge_cli with no shared object, so the
# registry is a module singleton both sides resolve at use time via
# get_rf_tx_registry() (tests monkeypatch the module global).
_rf_tx_registry = RecentRfTxRegistry()


def get_rf_tx_registry() -> RecentRfTxRegistry:
    """The process-wide recently-transmitted-on-radio registry."""
    return _rf_tx_registry


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
