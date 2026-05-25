"""Mesh ↔ RNS message transformation for RNSMeshtasticBridge.

Extracted from rns_bridge.py for file size compliance (CLAUDE.md #6).

Owns the bidirectional message conversion between Meshtastic and RNS/LXMF:
identity surfacing in LXMF envelopes, ``@address`` directed downlink
parsing, and persistent-queue requeuing on send failure.

Host class must provide:
- self.config (GatewayConfig)
- self.node_tracker (UnifiedNodeTracker)
- self.stats, self._stats_lock
- self.health (BridgeHealthMonitor)
- self._persistent_queue (PersistentMessageQueue or None)
- self.send_to_rns(content, destination_hash, *, title, fields)
- self.send_to_meshtastic(content, destination, channel)
- self._get_rns_destination(meshtastic_id) (defined here; override-able)
"""

import logging
import re
from typing import Optional

from .base_handler import chunk_for_mesh
from .message_queue import MessagePriority

logger = logging.getLogger(__name__)


class MessageTransformMixin:
    """Mixin: bidirectional Mesh↔RNS message processing + queue requeue."""

    def _process_mesh_to_rns(self, msg):
        """Process message from Meshtastic to RNS.

        Identity is carried in the LXMF title and fields dict so that
        RNS peers (NomadNet etc.) can distinguish which Meshtastic node
        a bridged message originated from. Body stays clean.

        On send failure for non-broadcast messages, attempts to persist
        to the persistent queue for later retry.
        """
        with self._stats_lock:
            self.stats['mesh_to_rns_attempted'] += 1
        try:
            content = msg.content

            long_name = ""
            short_name = ""
            if msg.source_id and getattr(self, 'node_tracker', None):
                try:
                    node = self.node_tracker.get_node_by_mesh_id(msg.source_id)
                    if node:
                        long_name = node.name or ""
                        short_name = getattr(node, 'short_name', '') or ""
                except Exception as e:
                    logger.debug(f"node_tracker lookup failed for {msg.source_id}: {e}")

            source_id = msg.source_id or "unknown"
            if long_name:
                title = f"{long_name} ({source_id}) via Meshtastic"
            else:
                title = f"{source_id} via Meshtastic"

            fields = {
                "meshforge_from_id": source_id,
                "meshforge_from_long": long_name,
                "meshforge_from_short": short_name,
                "meshforge_channel": (msg.metadata or {}).get("channel", ""),
                "meshforge_source_network": "meshtastic",
            }

            # Build destination list. Direct DM short-circuits to a single recipient;
            # broadcast fans out to every default_lxmf_destination configured.
            destinations: list = []
            if msg.destination_id and not msg.is_broadcast:
                direct = self._get_rns_destination(msg.destination_id)
                if direct:
                    destinations.append(direct)

            if not destinations:
                for hex_str in self.config.rns.get_lxmf_destinations():
                    try:
                        destinations.append(bytes.fromhex(hex_str))
                    except ValueError:
                        logger.warning(f"Invalid default_lxmf_destination hex: {hex_str!r}")

            sent_count = 0
            for dest_hash in destinations:
                if self.send_to_rns(content, dest_hash, title=title, fields=fields):
                    sent_count += 1

            if sent_count:
                if len(destinations) > 1:
                    logger.info(f"Bridge Mesh→RNS: {title} → {sent_count}/{len(destinations)} dest(s) — {content[:50]}")
                else:
                    logger.info(f"Bridge Mesh→RNS: {title} — {content[:50]}")
                with self._stats_lock:
                    self.stats['messages_mesh_to_rns'] += 1
                    self.stats['mesh_to_rns_delivered'] += 1
                self.health.record_message_sent("mesh_to_rns")
            elif msg.is_broadcast:
                # Broadcast that didn't land — debug-only, not an error. Could be no
                # default_lxmf_destination configured, or all configured peers were
                # unreachable; either way, broadcast best-effort delivery is fine.
                logger.debug(f"Mesh→RNS broadcast not delivered: {content[:30]}...")
                with self._stats_lock:
                    self.stats['mesh_to_rns_dropped'] += 1
            else:
                logger.warning(f"Failed to bridge Mesh→RNS: {content[:30]}...")
                with self._stats_lock:
                    self.stats['errors'] += 1
                    self.stats['mesh_to_rns_dropped'] += 1
                requeued = self._requeue_failed_message(msg, "rns")
                self.health.record_message_failed("mesh_to_rns", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging Mesh→RNS: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
                self.stats['mesh_to_rns_dropped'] += 1
            self.health.record_error("rns", e)
            self._requeue_failed_message(msg, "rns")
            self.health.record_message_failed("mesh_to_rns", requeued=True)

    def _get_rns_destination(self, meshtastic_id: str) -> Optional[bytes]:
        """Look up RNS destination hash for a Meshtastic node ID."""
        if hasattr(self, 'node_tracker') and self.node_tracker:
            node = self.node_tracker.get_node_by_mesh_id(meshtastic_id)
            if node and hasattr(node, 'rns_hash') and node.rns_hash:
                return node.rns_hash
        return None

    def _requeue_failed_message(self, msg, destination: str) -> bool:
        """Persist a failed message to the persistent queue for later retry.

        Args:
            msg: The message that failed to send (BridgedMessage or CanonicalMessage).
            destination: Target network ("meshtastic", "rns", or "meshcore").

        Returns:
            True if message was successfully persisted, False otherwise.
        """
        if not self._persistent_queue:
            return False

        try:
            # Handle both BridgedMessage (source_id) and CanonicalMessage (source_address)
            source_id = getattr(msg, 'source_id', None) or getattr(msg, 'source_address', '')
            dest_id = getattr(msg, 'destination_id', None) or getattr(msg, 'destination_address', '')
            # BridgedMessage.__post_init__ centralizes bytes→str (Hardening C);
            # CanonicalMessage may still arrive with non-str content, so guard.
            content = msg.content
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            elif not isinstance(content, str):
                content = ""
            self._persistent_queue.enqueue(
                payload={
                    'message': content,
                    'source_id': source_id,
                    'destination_id': dest_id or "",
                    'metadata': msg.metadata or {},
                },
                destination=destination,
                priority=MessagePriority.HIGH,
            )
            logger.debug(f"Failed message re-queued to persistent storage ({destination})")
            return True
        except Exception as e:
            logger.error(f"Failed to persist message for retry: {e}")
            return False

    def _resolve_mesh_destination(self, addr_token: str) -> Optional[str]:
        """Resolve an ``@address`` token to a Meshtastic node id.

        Accepts ``!abcdef12`` (hex id) directly, or a short_name that
        node_tracker can uniquely resolve. Returns the canonical
        ``!xxxxxxxx`` form, or None when unresolvable or ambiguous —
        the caller falls through to broadcast on None rather than
        silently misdelivering.
        """
        if not addr_token:
            return None
        hex_match = re.match(r'^!([0-9a-fA-F]{8})$', addr_token)
        if hex_match:
            return f"!{hex_match.group(1).lower()}"
        if getattr(self, 'node_tracker', None):
            try:
                node = self.node_tracker.get_node_by_short_name(addr_token)
                if node and node.meshtastic_id:
                    return node.meshtastic_id
            except Exception as e:
                logger.debug(f"short_name resolve failed for {addr_token!r}: {e}")
        return None

    def _process_rns_to_mesh(self, msg):
        """Process message from RNS to Meshtastic.

        Supports directed downlink via a leading ``@!xxxxxxxx`` or
        ``@shortname`` token in the message body. When resolvable, the
        token is stripped and the message is sent as a Meshtastic DM
        to that node. Unresolvable or absent = broadcast (unchanged).

        In mqtt_bridge mode, routes through the persistent queue for
        reliable delivery with retry. Otherwise sends directly and
        persists to queue on failure.

        Phase 1 fluid bridge — relay-on-receive: after local TX,
        originals (no ``meshforge_relayed_by`` LXMF field) are
        forwarded to each peer gateway hash listed in
        ``rns.peer_gateway_destinations`` so a single NomadNet send
        reaches every RF preset the cluster covers. Relayed copies
        carry the origin marker so peers don't re-relay.
        """
        # Lazy import to keep mixin file slim and avoid circular import
        # back into rns_bridge for the HAS_PERSISTENT_QUEUE flag.
        from . import rns_bridge as _rns_bridge_module
        HAS_PERSISTENT_QUEUE = _rns_bridge_module.HAS_PERSISTENT_QUEUE

        with self._stats_lock:
            self.stats['rns_to_mesh_attempted'] += 1
        try:
            raw = msg.content
            if isinstance(raw, bytes):
                original_body = raw.decode("utf-8", errors="replace")
            elif isinstance(raw, str):
                original_body = raw
            else:
                original_body = ""
            body = original_body  # working copy; @addr parsing strips below

            lxmf_fields = (msg.metadata or {}).get('lxmf_fields') or {}
            relayed_by = lxmf_fields.get('meshforge_relayed_by')
            # Relayed copies carry the original NomadNet's hash so the
            # [RNS:xxxx] prefix still attributes the operator, not the
            # relaying gateway.
            if relayed_by:
                effective_source = (
                    lxmf_fields.get('meshforge_origin_source_id')
                    or msg.source_id
                )
            else:
                effective_source = msg.source_id

            destination = None
            if body.startswith('@'):
                parts = body.split(None, 1)
                if len(parts) == 2:
                    addr_token = parts[0][1:]
                    resolved = self._resolve_mesh_destination(addr_token)
                    if resolved:
                        destination = resolved
                        body = parts[1]
                    else:
                        logger.info(
                            f"RNS→Mesh: @address {addr_token!r} unresolved, "
                            f"falling through to broadcast"
                        )

            # Attribution label for the [RNS:xxxx] tag. When the LXMF
            # originated as a Meshtastic broadcast at a PEER gateway
            # (meshforge_source_network == "meshtastic"), surface the
            # ORIGINAL mesh node — otherwise every node a given gateway
            # relays collapses to that gateway's RNS hash (e.g. every bot
            # reply re-injected by moc showing [RNS:f68c] = moc3's hash
            # rather than the bot). For genuinely RNS-origin content
            # (NomadNet etc.) keep the source RNS hash. The "[RNS:" shape is
            # preserved either way so the self-echo loop filter
            # (startswith('[RNS:')) and the bot's leading-bracket strip both
            # still match.
            if lxmf_fields.get('meshforge_source_network') == 'meshtastic':
                label = (
                    (lxmf_fields.get('meshforge_from_short') or '').strip()
                    or (lxmf_fields.get('meshforge_from_long') or '').strip()
                    or (lxmf_fields.get('meshforge_from_id') or '').lstrip('!')
                    or (effective_source or '')[:4]
                )
                label = str(label)[:10]
            else:
                label = (effective_source or '')[:4]
            prefix = f"[RNS:{label}] "
            content = prefix + body

            # Split oversize content into Meshtastic-byte-bounded packets.
            # Without this, content >228 bytes (e.g. a multi-line leaderboard
            # reply) was silently truncated to one packet by the handler's
            # _truncate_if_needed, dropping every line past the cap. The
            # [RNS:xxxx] prefix lands on chunk 0 only (byte-efficient; the
            # bot strips leading brackets anyway). A short message yields a
            # single chunk == content, so the common path is unchanged.
            chunks = chunk_for_mesh(content)
            tag = f" -> {destination}" if destination else ""
            multi = f" [{len(chunks)} chunks]" if len(chunks) > 1 else ""

            # In mqtt_bridge mode, use persistent queue for reliable delivery.
            # Each chunk is enqueued as its own item so it retries
            # independently (one failed chunk never re-sends the others).
            if (self._persistent_queue
                    and self.config.bridge_mode == "mqtt_bridge"
                    and HAS_PERSISTENT_QUEUE):
                all_queued = True
                for chunk in chunks:
                    payload = {
                        'message': chunk,
                        'channel': self.config.meshtastic.channel,
                        'source_id': msg.source_id,
                        'destination': destination,
                    }
                    # deduplicate=False: chunks are intentional fragments of
                    # ONE message, not independent messages. A repeated line
                    # (e.g. "amts: < 0.1in." twice in a wx forecast) is real
                    # content that must be delivered, and sibling chunks must
                    # never suppress each other. With dedup off here, a falsy
                    # return now means only a genuinely full queue.
                    if not self._persistent_queue.enqueue(
                        payload=payload,
                        destination="meshtastic",
                        priority=MessagePriority.NORMAL,
                        deduplicate=False,
                    ):
                        all_queued = False
                        break
                if all_queued:
                    logger.info(
                        f"Bridge RNS→Mesh (queued{tag}{multi}): {content[:50]}..."
                    )
                    with self._stats_lock:
                        self.stats['messages_rns_to_mesh'] += 1
                        self.stats['rns_to_mesh_delivered'] += 1
                    self.health.record_message_sent("rns_to_mesh")
                    if not relayed_by:
                        self._maybe_relay_to_peers(msg, original_body)
                    return
                # enqueue returned None — queue rejected a chunk
                logger.warning("Failed to enqueue RNS→Mesh chunk to persistent queue")
                with self._stats_lock:
                    self.stats['errors'] += 1
                    self.stats['rns_to_mesh_dropped'] += 1
                self.health.record_message_failed("rns_to_mesh", requeued=False)
                return

            # Direct send (non-MQTT mode or queue unavailable). Send every
            # chunk; success requires all of them to go out.
            sent = sum(
                1 for chunk in chunks
                if self.send_to_meshtastic(
                    chunk,
                    destination=destination,
                    channel=self.config.meshtastic.channel,
                )
            )
            if sent == len(chunks):
                logger.info(f"Bridge RNS→Mesh{tag}{multi}: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_rns_to_mesh'] += 1
                    self.stats['rns_to_mesh_delivered'] += 1
                self.health.record_message_sent("rns_to_mesh")
                if not relayed_by:
                    self._maybe_relay_to_peers(msg, original_body)
            else:
                logger.warning(
                    f"Failed to bridge RNS→Mesh ({sent}/{len(chunks)} chunks sent)"
                )
                with self._stats_lock:
                    self.stats['errors'] += 1
                    self.stats['rns_to_mesh_dropped'] += 1
                requeued = self._requeue_failed_message(msg, "meshtastic")
                self.health.record_message_failed("rns_to_mesh", requeued=requeued)

        except Exception as e:
            logger.error(f"Error bridging RNS→Mesh: {e}")
            with self._stats_lock:
                self.stats['errors'] += 1
                self.stats['rns_to_mesh_dropped'] += 1
            self.health.record_error("meshtastic", e)
            self._requeue_failed_message(msg, "meshtastic")
            self.health.record_message_failed("rns_to_mesh", requeued=True)

    def _maybe_relay_to_peers(self, msg, body: str) -> None:
        """Relay an originally-NomadNet-sourced LXMF to peer gateways.

        Phase 1 of the fluid bridge roadmap. When ``rns.peer_gateway_destinations``
        is configured, originals (no ``meshforge_relayed_by`` field) are
        forwarded to each peer gateway hash so a single NomadNet send into
        one gateway thread reaches every RF preset the cluster covers.

        Each relay carries:
        - ``meshforge_relayed_by``: this gateway's LXMF hash hex — the
          loop-prevention marker. Receiving gateways skip re-relay when set.
        - ``meshforge_origin_source_id``: the original NomadNet's hash hex —
          preserves the [RNS:xxxx] attribution at the receiving gateway.
        - ``meshforge_origin_title``: the original LXMF title — best-effort
          audit.

        Failures are logged and counted; they never propagate to the local
        R→M code path. The persistent-queue dedup window catches multi-path
        duplicates at receiving gateways.
        """
        try:
            peer_hexes = list(self.config.rns.get_peer_gateway_destinations())
        except (AttributeError, TypeError):
            # AttributeError: older config object missing the helper.
            # TypeError: MagicMock or non-iterable return — defensive.
            return
        if not peer_hexes:
            return

        own_src = getattr(self, '_lxmf_source', None)
        own_hash_attr = getattr(own_src, 'hash', None) if own_src else None
        own_hex = own_hash_attr.hex().lower() if own_hash_attr else ''
        if not own_hex:
            logger.debug("Relay-on-receive skipped: own LXMF hash unknown")
            return

        # Skip relay when the LXMF arrived from a peer gateway (either via
        # M→R fan-out across the broadcast list OR a prior relay). The
        # ``meshforge_relayed_by`` field gate already catches explicit
        # relays; this catches mesh-sourced fan-outs whose peer didn't set
        # the marker but whose source IS in the peer set. Without it,
        # every mesh-sourced message (already delivered to all gateways
        # via M→R fan-out) would be re-relayed → duplicates on every preset.
        peer_hex_set = {
            h.lower() for h in peer_hexes
            if isinstance(h, str) and len(h) == 32
        }
        src_id = (msg.source_id or '').lower()
        if src_id and src_id in peer_hex_set:
            logger.debug(
                f"Relay-on-receive skipped: source {src_id[:8]} is a peer gateway"
            )
            return

        relay_fields = {
            'meshforge_relayed_by': own_hex,
            'meshforge_origin_source_id': msg.source_id or '',
            'meshforge_origin_title': msg.title or '',
        }
        title = msg.title or 'MeshForge Gateway (relay)'

        relayed = 0
        attempted = 0
        for peer_hex in peer_hexes:
            if not isinstance(peer_hex, str) or len(peer_hex) != 32:
                logger.warning(
                    f"Skipping invalid peer_gateway_destinations entry: {peer_hex!r}"
                )
                continue
            if peer_hex.lower() == own_hex:
                continue  # defensive: own hash listed in peer set
            try:
                dest_bytes = bytes.fromhex(peer_hex)
            except ValueError:
                logger.warning(
                    f"Skipping non-hex peer_gateway_destinations entry: {peer_hex!r}"
                )
                continue
            attempted += 1
            try:
                ok = self.send_to_rns(
                    body, dest_bytes, title=title, fields=relay_fields,
                )
            except Exception as e:
                logger.warning(f"Relay to peer {peer_hex[:8]} raised: {e}")
                ok = False
            if ok:
                relayed += 1
            else:
                logger.warning(f"Relay to peer {peer_hex[:8]} failed")

        if attempted:
            logger.info(
                f"Phase-1 relay: forwarded R→M origin to "
                f"{relayed}/{attempted} peer gateway(s)"
            )
            with self._stats_lock:
                self.stats['relay_to_peers_attempted'] = (
                    self.stats.get('relay_to_peers_attempted', 0) + attempted
                )
                self.stats['relay_to_peers_delivered'] = (
                    self.stats.get('relay_to_peers_delivered', 0) + relayed
                )
