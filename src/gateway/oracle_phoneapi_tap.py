"""Read-only PhoneAPI (:4403) tap that feeds the mesh-oracle EVERY decoded packet.

In ``mqtt_bridge`` ("zero-interference") mode the gateway's oracle is fed only by
meshtasticd's MQTT-json uplink, which carries 1-hop / relayed-back packets — a
node that reaches the gateway via a RELAY (multi-hop) is invisible to it. The
PhoneAPI ``meshtastic.receive`` pubsub delivers EVERY decoded packet, multi-hop
included. This tap opens ONE managed :4403 read connection (the connection
manager's single-consumer guard, #17), runs the read-only oracle on received
text, and replies as a channel BROADCAST over HTTP ``/api/v1/toradio`` (never
reads fromradio — TX stays #17/#75-safe; a relayed asker's node-id is unreliable
so the answer is broadcast on the channel and the real asker sees it).

Opt-in: ``MESHFORGE_ORACLE_ENABLED`` + ``MESHFORGE_ORACLE_PHONEAPI_TAP``. Inert
otherwise (``self._oracle`` stays ``None`` ⇒ ``run_loop`` returns at once). The
oracle is read-only — it never controls services, mutates config, or writes
fromradio. This is the multi-hop-visible complement to the MQTT-json oracle leg;
run it alongside the MQTT bridge (which keeps owning bridging/TX).
"""
from __future__ import annotations

import os

from utils.logging_config import get_logger
from utils.meshtastic_connection import get_connection_manager
from utils.safe_import import safe_import

logger = get_logger(__name__)

_pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')
_TRUE = {"1", "true", "yes", "on"}


class OraclePhoneAPITap:
    """A read-only :4403 reader that runs the mesh-oracle on every decoded text
    packet — seeing multi-hop nodes the MQTT-json uplink never carries."""

    def __init__(self, config, stop_event):
        self.config = config
        self._stop_event = stop_event
        self._conn_manager = None
        self._interface = None
        self._pubsub_handler = None
        self._oracle = None
        try:
            self._oracle = self._build_oracle()
        except Exception as e:  # pragma: no cover - never break gateway init
            logger.debug(f"oracle PhoneAPI tap not initialized: {e}")

    @property
    def enabled(self) -> bool:
        return self._oracle is not None

    # -- build ------------------------------------------------------------
    def _build_oracle(self):
        """Build the read-only oracle responder, or None if the tap is disabled.

        Gated by BOTH MESHFORGE_ORACLE_ENABLED and MESHFORGE_ORACLE_PHONEAPI_TAP
        so the tap is a deliberate opt-in (it holds a :4403 connection). Reuses
        the same channel-name whitelist (MESHFORGE_ORACLE_CHANNELS) + node
        allowlist as the other Meshtastic legs; replies BROADCAST on the channel.
        """
        if str(os.environ.get("MESHFORGE_ORACLE_ENABLED", "")).strip().lower() not in _TRUE:
            return None
        if str(os.environ.get("MESHFORGE_ORACLE_PHONEAPI_TAP", "")).strip().lower() not in _TRUE:
            return None
        if not _HAS_PUBSUB:
            logger.warning("oracle PhoneAPI tap: pubsub unavailable; tap inert")
            return None

        from oracle import fetch_api_status, read_snapshot
        from oracle.responder import MeshOracleResponder

        def _snapshot():
            return read_snapshot(status=fetch_api_status())

        def _send(text: str, dest: str, channel) -> bool:
            # Channel BROADCAST on the configured channel index via HTTP
            # /api/v1/toradio (no fromradio write). dest/channel are the asker
            # id / matched index — not a TX target; a relayed asker's id is
            # unreliable, so broadcast reaches the real asker on-channel.
            try:
                from .meshtastic_protobuf_client import send_text_direct_with_id
                host = getattr(self.config.meshtastic, "host", "localhost")
                http_port = getattr(self.config.meshtastic, "http_port", 9443) or 9443
                idx = int(getattr(self.config.meshtastic, "channel", 0) or 0)
                pkt = send_text_direct_with_id(
                    text=text, host=host, port=http_port,
                    destination=None, channel_index=idx)
                return pkt is not None
            except Exception as e:
                logger.debug(f"oracle tap reply send failed: {e}")
                return False

        def _log(record: dict) -> None:
            try:
                from mini_dudeai.history import append_jsonl
                from oracle import oracle_log_path
                p = oracle_log_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                err = append_jsonl(str(p), [record], 2 * 1024 * 1024)
                if err:  # #60/#9: a swallowed sandbox write must leave a witness
                    logger.warning(f"mesh oracle audit log write failed: {err}")
            except Exception as e:  # pragma: no cover - best-effort audit log
                logger.debug(f"oracle tap log append failed: {e}")

        allowed_channels = self._resolve_channels(
            os.environ.get("MESHFORGE_ORACLE_CHANNELS", ""))
        return MeshOracleResponder.from_env(
            snapshot_fn=_snapshot, send_fn=_send, log_fn=_log,
            allowed_channels=allowed_channels)

    def _resolve_channels(self, names_csv: str):
        """Resolve channel NAMES -> THIS box's local slot indices.

        The inbound PhoneAPI packet's ``channel`` is a box-local slot index, so a
        fleet-stable whitelist keyed on names is resolved to local indices via the
        same live channel-list query the bridge uses (#17/#75-safe). Unresolved
        names are logged + skipped (never silently index 0). Empty when unset.
        """
        names = [n.strip() for n in (names_csv or "").split(",") if n.strip()]
        if not names:
            return set()
        try:
            from gateway._channel_resolver import resolve_tx_channel_index
        except ImportError as e:  # pragma: no cover - defensive
            logger.warning(f"oracle tap channel resolver unavailable: {e}")
            return set()
        resolved = set()
        for name in names:
            idx, status = resolve_tx_channel_index(name, -1)
            if status in ("matches_config", "resolved") and idx >= 0:
                resolved.add(idx)
                logger.info(f"oracle tap channel {name!r} -> index {idx} ({status})")
            else:
                logger.warning(
                    f"oracle tap channel {name!r} not resolved ({status}); skipped")
        return resolved

    # -- lifecycle --------------------------------------------------------
    def run_loop(self) -> None:
        """Connect + subscribe, reconnect with backoff until stop_event. Inert
        (returns immediately) when the tap is disabled."""
        if self._oracle is None:
            logger.debug("oracle PhoneAPI tap disabled; not starting")
            return
        backoff = 2.0
        while not self._stop_event.is_set():
            if self._connect():
                backoff = 2.0
                # Stay connected: the meshtastic lib delivers RX on its own
                # thread. Wake periodically only to honor stop_event promptly +
                # notice a dropped link.
                while not self._stop_event.is_set() and self._healthy():
                    self._stop_event.wait(15)
                if not self._stop_event.is_set():
                    logger.warning("oracle tap link down; reconnecting")
                self._teardown()
            else:
                self._stop_event.wait(min(backoff, 60))
                backoff = min(backoff * 2, 60)
        self._teardown()

    def _connect(self) -> bool:
        try:
            host = getattr(self.config.meshtastic, "host", "localhost")
            port = int(getattr(self.config.meshtastic, "port", 4403) or 4403)
            self._conn_manager = get_connection_manager(host, port)
            if not self._conn_manager.acquire_persistent(owner="oracle_tap"):
                logger.warning(
                    "oracle tap could not acquire :4403 (held elsewhere); retrying")
                return False
            self._interface = self._conn_manager.get_interface()
            if self._interface is None:
                logger.warning("oracle tap got no interface from manager")
                self._teardown()
                return False
            if _HAS_PUBSUB:
                def on_receive(packet, interface=None):
                    self._on_receive(packet)
                self._pubsub_handler = on_receive
                _pub.subscribe(self._pubsub_handler, "meshtastic.receive")
            logger.info(
                "oracle PhoneAPI tap connected on :4403 — multi-hop nodes visible")
            return True
        except Exception as e:
            logger.warning(f"oracle tap connect failed: {e}")
            self._teardown()
            return False

    def _healthy(self) -> bool:
        # Best-effort: the manager still hands back an interface. A hard drop is
        # recovered on the next connect iteration after _teardown.
        try:
            return (self._conn_manager is not None
                    and self._conn_manager.get_interface() is not None)
        except Exception:
            return False

    def _teardown(self) -> None:
        try:
            if self._pubsub_handler is not None and _HAS_PUBSUB:
                try:
                    _pub.unsubscribe(self._pubsub_handler, "meshtastic.receive")
                except Exception:
                    pass
                self._pubsub_handler = None
            if self._conn_manager is not None:
                try:
                    self._conn_manager.release_persistent()
                except Exception:
                    pass
            self._interface = None
        except Exception as e:  # pragma: no cover
            logger.debug(f"oracle tap teardown error: {e}")

    # -- receive ----------------------------------------------------------
    def _on_receive(self, packet) -> None:
        """Run the oracle on a received TEXT packet (multi-hop included).

        Read-only: extracts sender/text/channel and calls the oracle. A handled
        query is answered + logged; everything else is ignored (this tap never
        bridges — the MQTT handler owns bridging)."""
        try:
            if self._oracle is None or not isinstance(packet, dict):
                return
            decoded = packet.get('decoded', {}) or {}
            if decoded.get('portnum') != 'TEXT_MESSAGE_APP':
                return
            payload = decoded.get('payload', b'')
            text = (payload.decode('utf-8', errors='ignore')
                    if isinstance(payload, bytes) else str(payload or ""))
            from_id = packet.get('fromId') or ""
            channel = packet.get('channel', 0)
            self._oracle.handle(from_id, text, channel)
        except Exception as e:
            logger.debug(f"oracle tap receive error: {e}")
