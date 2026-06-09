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
- self._reply_context (ReplyContextStore — Theme-A reply routing cache)
- self._identity_binder (IdentityBinder — Theme-A identity SSOT)
- self._correlation (BridgeCorrelationStore — Thread-2 durable reply mapping)
"""

import logging
import re
from typing import Optional

from .base_handler import chunk_for_mesh, get_rf_tx_registry, is_already_bridged
from .canonical_message import format_reply_token, parse_reply_token
from .message_queue import MessagePriority

logger = logging.getLogger(__name__)

# Synthetic / bridge-tag content prefixes excluded from reply routing.
# [delivered:/[failed:/[timeout: are Issue #66 ACK synthetics; [Mesh:/[MC:/
# [ch are sibling-bridge injection tags (MeshAnchor re-emit shapes). None of
# these is a human reply, so they must neither seed the reply-context memory
# nor be auto-directed as a downlink ([RNS: is covered separately by
# is_already_bridged).
_REPLY_EXCLUDED_PREFIXES = (
    "[delivered:", "[failed:", "[timeout:", "[Mesh:", "[MC:", "[ch",
)


class MessageTransformMixin:
    """Mixin: bidirectional Mesh↔RNS message processing + queue requeue."""

    # --- Theme-A step 1: reply routing helpers ---

    def _reply_routing_on(self) -> bool:
        """Strict read of rns.reply_routing_enabled (default False).

        ``is True`` deliberately: MagicMock test configs and malformed
        gateway.json values are truthy-but-not-True and must read as OFF,
        preserving legacy broadcast behavior everywhere the flag wasn't
        explicitly enabled.
        """
        rns_cfg = getattr(self.config, 'rns', None)
        return getattr(rns_cfg, 'reply_routing_enabled', False) is True

    def _dual_path_dedup_on(self) -> bool:
        """Strict read of rns.dual_path_dedup_enabled (default False).

        Same ``is True`` discipline as _reply_routing_on — MagicMock test
        configs and malformed values read as OFF, preserving the legacy
        always-deliver behavior everywhere the flag wasn't explicitly set.
        """
        rns_cfg = getattr(self.config, 'rns', None)
        return getattr(rns_cfg, 'dual_path_dedup_enabled', False) is True

    def _dual_path_dedup_window(self) -> float:
        rns_cfg = getattr(self.config, 'rns', None)
        try:
            return float(getattr(rns_cfg, 'dual_path_dedup_window_sec', 60))
        except (TypeError, ValueError):
            return 60.0

    def _identity_on(self) -> bool:
        """Strict read of rns.cross_protocol_identity_enabled (default False).

        Theme-A step 2. Same ``is True`` discipline as _reply_routing_on —
        MagicMock configs and malformed values read as OFF, keeping the
        identity SSOT (population + resolution) inert unless explicitly
        enabled.
        """
        rns_cfg = getattr(self.config, 'rns', None)
        return getattr(rns_cfg, 'cross_protocol_identity_enabled', False) is True

    def _sessions_on(self) -> bool:
        """Strict read of rns.sessions_enabled (default False).

        Theme-A step 3. Same ``is True`` discipline as the other gates —
        MagicMock configs and malformed values read as OFF, keeping the
        session layer (touches + DM-to-gateway routing + sweep) inert
        unless explicitly enabled.
        """
        rns_cfg = getattr(self.config, 'rns', None)
        return getattr(rns_cfg, 'sessions_enabled', False) is True

    def _own_mesh_node_id(self) -> Optional[str]:
        """The gateway's own Meshtastic node id, normalized ``!hex8``.

        Config-first: meshtastic.gateway_node_id (operator-supplied).
        None when unset/malformed — DM-to-gateway detection then no-ops
        and such traffic falls through to today's broadcast behavior.
        """
        mesh_cfg = getattr(self.config, 'meshtastic', None)
        raw = getattr(mesh_cfg, 'gateway_node_id', '') or ''
        if not isinstance(raw, str):
            return None
        norm = raw.strip().lower()
        return norm if re.match(r'^![0-9a-f]{8}$', norm) else None

    def _maybe_touch_session_r2m(self, msg, destination,
                                 original_body, lxmf_fields) -> None:
        """R→M directed delivery succeeded → open/refresh the session.

        Theme-A step 3. ``destination`` is the resolved ``!hex8`` mesh id;
        ``msg.source_id`` is the RNS peer hash. Carries its OWN exclusion
        check — the ``@addr`` rung resolves OUTSIDE the step-1 gated
        block, so peer-gateway sources / bridge-origin copies that used
        ``@addr`` must be excluded here too. Never raises.
        """
        try:
            if not (self._sessions_on() and destination and msg.source_id):
                return
            if self._reply_excluded(original_body, lxmf_fields,
                                    msg.source_id or ''):
                return
            self._sessions.touch(destination, msg.source_id)
        except Exception as e:
            logger.debug(f"session touch (r2m) failed: {e}")

    def _peer_gateway_hash_set(self) -> set:
        """Normalized lowercase set of peer gateway LXMF hashes."""
        try:
            peer_hexes = self.config.rns.get_peer_gateway_destinations()
        except (AttributeError, TypeError):
            return set()
        if not isinstance(peer_hexes, (list, tuple)):
            return set()
        return {
            h.lower() for h in peer_hexes
            if isinstance(h, str) and len(h) == 32
        }

    def _reply_excluded(self, body: str, lxmf_fields: dict,
                        source_id: str = '') -> bool:
        """True when a message must not participate in reply routing.

        Excludes anything that is not a human-authored original: content
        already carrying a bridge tag ([RNS: via is_already_bridged, plus
        the sibling-bridge / ACK-synthetic prefixes), gateway-bridged
        copies (any meshforge_source_network / meshforge_relayed_by marker
        — the M→R fan-out a PEER gateway receives is a broadcast to
        re-broadcast, never a reply to auto-direct), and traffic from a
        configured peer gateway hash (infrastructure, not a user).
        Genuine NomadNet/Sideband replies carry none of these.
        """
        stripped = (body or '').lstrip()
        if is_already_bridged(stripped):
            return True
        if stripped.startswith(_REPLY_EXCLUDED_PREFIXES):
            return True
        fields = lxmf_fields or {}
        if fields.get('meshforge_source_network'):
            return True
        if fields.get('meshforge_relayed_by'):
            return True
        if source_id and source_id.lower() in self._peer_gateway_hash_set():
            return True
        return False

    def _resolve_reply_token(self, token) -> Optional[str]:
        """Canonical reply token → validated Meshtastic node id, or None.

        Only the meshtastic protocol is honored this step; the address is
        re-validated through _resolve_mesh_destination (the token is
        untrusted wire data).
        """
        proto, addr = parse_reply_token(token)
        if proto != 'meshtastic' or not addr:
            return None
        return self._resolve_mesh_destination(addr)

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

            # Theme-A step 1: canonical reply token — always emitted (pure
            # metadata; lets compliant RNS clients/gateways address a reply
            # without the manual @id syntax).
            reply_token = format_reply_token("meshtastic", source_id)

            # Theme-A step 2: identity SSOT population (gated, throttled in
            # the binder). Active mesh talkers become contacts; re-bridged /
            # synthetic content never does.
            if (self._identity_on() and msg.source_id
                    and not self._reply_excluded(content, {})):
                self._identity_binder.populate(
                    'meshtastic', msg.source_id.lower(), long_name)

            fields = {
                "meshforge_from_id": source_id,
                "meshforge_from_long": long_name,
                "meshforge_from_short": short_name,
                "meshforge_channel": (msg.metadata or {}).get("channel", ""),
                "meshforge_source_network": "meshtastic",
                "meshforge_reply_to": reply_token,
            }

            # Theme-A step 3: a Meshtastic DM addressed to the gateway's
            # OWN node is the private-reply channel. When sessions are on
            # and the sender has an active session, route the content as
            # a single private LXMF send to that peer instead of the
            # broadcast fan-out today's lookup miss would trigger. Flag
            # off / own-id unknown / synthetic content → fall through to
            # today's behavior unchanged. No-session → log + fall through
            # (broadcast fallback, operator-ratified).
            own_id = self._own_mesh_node_id() if self._sessions_on() else None
            if (own_id and not msg.is_broadcast and msg.destination_id
                    and msg.destination_id.lower() == own_id
                    and msg.source_id
                    and not self._reply_excluded(content, {})):
                peer_hex = self._sessions.lookup(msg.source_id)
                dest_bytes = None
                if peer_hex:
                    try:
                        dest_bytes = bytes.fromhex(peer_hex)
                    except (ValueError, TypeError):
                        dest_bytes = None
                if dest_bytes and self.send_to_rns(
                        content, dest_bytes, title=title, fields=fields):
                    self._sessions.touch(msg.source_id, peer_hex)
                    if self._reply_routing_on():
                        # Keep the step-1 R→M memory warm for the peer.
                        self._reply_context.record(peer_hex, reply_token)
                    with self._stats_lock:
                        self.stats['session_routed_m2r'] = (
                            self.stats.get('session_routed_m2r', 0) + 1)
                        self.stats['messages_mesh_to_rns'] += 1
                        self.stats['mesh_to_rns_delivered'] += 1
                    self.health.record_message_sent("mesh_to_rns")
                    logger.info(
                        f"Bridge Mesh→RNS (session): {title} -> "
                        f"{peer_hex[:8]} — {content[:50]}")
                    return
                if not peer_hex:
                    with self._stats_lock:
                        self.stats['session_dm_no_session'] = (
                            self.stats.get('session_dm_no_session', 0) + 1)
                    logger.info(
                        f"Mesh→RNS DM-to-gateway from {msg.source_id} with "
                        f"no active session — broadcast fallback")
                # peer send failure / bad hex also falls through to fan-out

            # Build destination list. Direct DM short-circuits to a single recipient;
            # broadcast fans out to every default_lxmf_destination configured.
            destinations: list = []
            directed_m2r_dest = None
            if msg.destination_id and not msg.is_broadcast:
                direct = self._get_rns_destination(msg.destination_id)
                if direct:
                    destinations.append(direct)
                    directed_m2r_dest = direct

            if not destinations:
                for hex_str in self.config.rns.get_lxmf_destinations():
                    try:
                        destinations.append(bytes.fromhex(hex_str))
                    except ValueError:
                        logger.warning(f"Invalid default_lxmf_destination hex: {hex_str!r}")

            # Theme-A step 1: remember which mesh node messaged each RNS
            # peer so a stock-client reply (no @addr, no echoed fields) can
            # be auto-directed back. Gated; never seeded by re-bridged /
            # synthetic content, and never for peer-gateway hashes
            # (infrastructure threads don't reply).
            record_ok = (
                self._reply_routing_on()
                and msg.source_id
                and not self._reply_excluded(content, {})
            )
            peer_gateways = self._peer_gateway_hash_set() if record_ok else set()

            sent_count = 0
            for dest_hash in destinations:
                if self.send_to_rns(content, dest_hash, title=title, fields=fields):
                    sent_count += 1
                    if record_ok:
                        dest_hex = dest_hash.hex().lower()
                        if dest_hex not in peer_gateways:
                            self._reply_context.record(dest_hex, reply_token)
                            # Thread-2: durable mirror of the reply mapping
                            # so it survives a bridge restart (the property
                            # the in-memory cache loses). Channel by NAME
                            # (#77), not the box-local slot index.
                            self._correlation.record(
                                msg.source_id, dest_hex,
                                channel=str(
                                    (msg.metadata or {}).get("channel", "")),
                                direction="m2r")
                    # Theme-A step 3: a successful DIRECTED M→R DM opens/
                    # refreshes the session (broadcast fan-out never does).
                    if (self._sessions_on()
                            and directed_m2r_dest is not None
                            and dest_hash == directed_m2r_dest
                            and msg.source_id
                            and not self._reply_excluded(content, {})):
                        self._sessions.touch(
                            msg.source_id, dest_hash.hex().lower())

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
        """Look up RNS destination hash for a Meshtastic node ID.

        node_tracker first (live discovery); Theme-A step 2 falls back to
        the identity SSOT (contact mapping) when gated on — an operator
        link or conservative auto-bind can route a directed M→R DM that
        discovery alone couldn't.
        """
        if hasattr(self, 'node_tracker') and self.node_tracker:
            node = self.node_tracker.get_node_by_mesh_id(meshtastic_id)
            if node and hasattr(node, 'rns_hash') and node.rns_hash:
                return node.rns_hash
        if self._identity_on() and meshtastic_id:
            rns_hex = self._identity_binder.resolve(
                'meshtastic', meshtastic_id.lower(), 'rns')
            if rns_hex:
                try:
                    dest = bytes.fromhex(rns_hex)
                except (ValueError, TypeError):
                    logger.warning(
                        f"identity rns address unconvertible: {rns_hex!r}")
                    return None
                with self._stats_lock:
                    self.stats['identity_resolved_m2r'] = (
                        self.stats.get('identity_resolved_m2r', 0) + 1)
                return dest
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

    def _requeue_failed_chunks(self, chunks, destination_node,
                               target: str = "meshtastic") -> bool:
        """Persist already-chunked, byte-bounded RNS→Mesh content for retry.

        Used by the direct-send path on PARTIAL failure. Each chunk is
        enqueued as its own ``message``-shaped item (key ``destination`` — what
        the queue's meshtastic sender reads to route a DM) so the retry ships
        bounded packets to the right target, rather than re-queuing the whole
        un-chunked original (which the retry would truncate and broadcast).
        Returns True if at least one chunk was persisted.
        """
        if not self._persistent_queue or not chunks:
            return False
        requeued = 0
        for chunk in chunks:
            try:
                if self._persistent_queue.enqueue(
                    payload={
                        'message': chunk,
                        'channel': self.config.meshtastic.channel,
                        'source_id': '',
                        'destination': destination_node,
                    },
                    destination=target,
                    priority=MessagePriority.HIGH,
                ):
                    requeued += 1
            except Exception as e:
                logger.error(f"Failed to persist RNS→Mesh chunk for retry: {e}")
        return requeued > 0

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

            # Theme-A step 2: identity SSOT population (gated, throttled).
            # Only human-authored RNS originals — peer gateways and
            # bridge-origin copies must never become contacts.
            if (self._identity_on() and msg.source_id
                    and not self._reply_excluded(
                        original_body, lxmf_fields, msg.source_id or '')):
                rns_name = ""
                try:
                    node = self.node_tracker.get_node_by_rns_hash(
                        bytes.fromhex(msg.source_id))
                    if node:
                        rns_name = node.name or ""
                except (ValueError, TypeError, AttributeError):
                    pass
                self._identity_binder.populate(
                    'rns', msg.source_id.lower(), rns_name)

            destination = None
            reply_route = ""  # "field"|"memory"|"contact" when reply routing resolved it
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

            # Theme-A step 1+2 — reply routing (gated, default off). Only
            # for human-authored originals (no bridge markers / synthetics);
            # precedence: explicit @addr (above, always wins) > echoed
            # meshforge_reply_to field > reply-context memory > identity
            # SSOT contact rung (step 2, needs its own flag too) > broadcast.
            if (destination is None
                    and self._reply_routing_on()
                    and not self._reply_excluded(
                        original_body, lxmf_fields, msg.source_id or '')):
                resolved = self._resolve_reply_token(
                    lxmf_fields.get('meshforge_reply_to'))
                if resolved:
                    destination = resolved
                    reply_route = "field"
                else:
                    # Reply-context: fast in-process cache first, then the
                    # durable correlation store (Thread-2) so the mapping
                    # survives a bridge restart — the property the in-memory
                    # cache loses. A DB hit warms the cache for next time.
                    key = (msg.source_id or '').lower()
                    token = self._reply_context.get(key)
                    db_hit = False
                    if token is None:
                        token = self._correlation.lookup_reply_token(key)
                        if token:
                            db_hit = True
                            self._reply_context.record(key, token)
                    resolved = self._resolve_reply_token(token)
                    if resolved:
                        destination = resolved
                        reply_route = "memory_db" if db_hit else "memory"
                if destination is None and self._identity_on():
                    mesh_addr = self._identity_binder.resolve(
                        'rns', (msg.source_id or '').lower(), 'meshtastic')
                    if mesh_addr:
                        # Re-validate through the same path the token rungs
                        # use — the DB row is operator/heuristic data, not
                        # gospel.
                        resolved = self._resolve_mesh_destination(mesh_addr)
                        if resolved:
                            destination = resolved
                            reply_route = "contact"
                if reply_route:
                    with self._stats_lock:
                        skey = f'reply_routed_from_{reply_route}'
                        self.stats[skey] = self.stats.get(skey, 0) + 1

            # Thread-2: durable r2m observability — record that this RNS
            # peer directed a message at a specific mesh node. NOT read for
            # routing in this slice; gated on reply_routing so flag-off
            # boxes never open the DB, and excludes synthetic / re-bridged
            # content (same gate as the reply-routing rungs above).
            if (destination is not None
                    and self._reply_routing_on()
                    and msg.source_id
                    and not self._reply_excluded(
                        original_body, lxmf_fields, msg.source_id or '')):
                self._correlation.record(
                    destination, msg.source_id, direction="r2m")

            # Dual-path dedup (gated, default off). On a box whose LOCAL
            # mesh_bridge also carries this RF traffic, a broadcast the
            # mesh_bridge already transmitted onto the primary radio
            # (true-origin downlink or tagged toradio) arrives here a second
            # time via a peer gateway's RNS relay and would land on the
            # radio TWICE. Suppress ONLY on a registry hit — when the local
            # radio missed the message on RF (live trace 2026-06-04: ~40% of
            # relayed events), there is no hit and this copy still delivers,
            # preserving the relay's fallback value. Broadcasts only — a
            # resolved DM destination is never dual-path.
            if (destination is None
                    and self._dual_path_dedup_on()
                    and get_rf_tx_registry().seen_within(
                        body, self._dual_path_dedup_window())):
                with self._stats_lock:
                    self.stats['rns_to_mesh_dual_path_suppressed'] = (
                        self.stats.get('rns_to_mesh_dual_path_suppressed', 0) + 1)
                logger.info(
                    f"Bridge RNS→Mesh suppressed (dual-path dedup — already "
                    f"on RF via mesh_bridge): {body[:50]}...")
                return

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
            # [RNS:xxxx] prefix rides EVERY chunk (prefix= reserves its
            # bytes): the tag is the echo-loop invariant, and the original
            # chunk-0-only shape leaked untagged tail chunks through
            # is_already_bridged on every gateway — live 2026-06-04, a
            # dual-radio box re-bridged a peer's relayed tails back onto
            # its primary RF. A short message yields a single chunk ==
            # prefix+body, so the common path is unchanged.
            chunks = chunk_for_mesh(body, prefix=prefix)
            if destination and reply_route:
                tag = f" -> {destination} (reply:{reply_route})"
            elif destination:
                tag = f" -> {destination}"
            else:
                tag = ""
            multi = f" [{len(chunks)} chunks]" if len(chunks) > 1 else ""

            # In mqtt_bridge mode, use persistent queue for reliable delivery.
            # Each chunk is enqueued as its own item (independent retry).
            # Dedup is LEFT ON (default): a byte-identical chunk already
            # queued/delivered within the dedup window is suppressed, so a
            # re-sent forecast/command (e.g. a wx command amplified upstream
            # into several copies) is not re-broadcast onto RF over and over.
            #
            # A falsy enqueue has TWO causes and they are NOT the same:
            #   - dedup suppression — benign: the chunk is already on its way,
            #     so it must not abort the others and is not a failure;
            #   - queue rejection (full + unsheddable) — a real DROP: that
            #     chunk's content is lost and must be counted as such, not
            #     silently booked as delivered. is_recent_duplicate() tells
            #     the two apart (enqueue checks dedup before the size limit, so
            #     a rejected chunk is by construction not a recent duplicate).
            # (Trade-off on the dedup side: a line that legitimately repeats
            # within one reply, arriving within the window, can be suppressed
            # too — the real cure is removing the upstream command duplication.)
            if (self._persistent_queue
                    and self.config.bridge_mode == "mqtt_bridge"
                    and HAS_PERSISTENT_QUEUE):
                enqueued = 0
                suppressed = 0
                rejected = 0
                for chunk in chunks:
                    payload = {
                        'message': chunk,
                        'channel': self.config.meshtastic.channel,
                        'source_id': msg.source_id,
                        'destination': destination,
                    }
                    if self._persistent_queue.enqueue(
                        payload=payload,
                        destination="meshtastic",
                        priority=MessagePriority.NORMAL,
                    ):
                        enqueued += 1
                    elif self._persistent_queue.is_recent_duplicate(
                            payload, "meshtastic"):
                        suppressed += 1
                    else:
                        rejected += 1

                if rejected:
                    # At least one chunk was dropped under queue pressure —
                    # genuine data loss, not a benign duplicate. Surface it.
                    logger.warning(
                        f"Bridge RNS→Mesh dropped under queue pressure "
                        f"({rejected}/{len(chunks)} chunks rejected{tag}): "
                        f"{content[:50]}..."
                    )
                    with self._stats_lock:
                        self.stats['errors'] += 1
                        self.stats['rns_to_mesh_dropped'] += 1
                    self.health.record_message_failed(
                        "rns_to_mesh", requeued=False)
                    # Still relay whatever DID get queued so peers aren't
                    # starved of the chunks that made it.
                    if enqueued and not relayed_by:
                        self._maybe_relay_to_peers(msg, original_body)
                    return

                # Every chunk was queued or benignly dedup-suppressed.
                with self._stats_lock:
                    self.stats['messages_rns_to_mesh'] += 1
                    self.stats['rns_to_mesh_delivered'] += 1
                self.health.record_message_sent("rns_to_mesh")
                # Theme-A step 3: directed delivery opens/refreshes the
                # session (gated + excluded inside the helper).
                self._maybe_touch_session_r2m(
                    msg, destination, original_body, lxmf_fields)
                if enqueued:
                    note = f", {suppressed} dup-suppressed" if suppressed else ""
                    logger.info(
                        f"Bridge RNS→Mesh (queued{tag}{multi}{note}): {content[:50]}..."
                    )
                    # Relay originals onward only when something new was
                    # queued — a fully-suppressed duplicate was already relayed
                    # on its first pass.
                    if not relayed_by:
                        self._maybe_relay_to_peers(msg, original_body)
                else:
                    logger.debug(
                        f"Bridge RNS→Mesh fully dup-suppressed: {content[:50]}..."
                    )
                return

            # Direct send (non-MQTT mode or queue unavailable). Send every
            # chunk; success requires all of them to go out.
            failed_chunks = [
                chunk for chunk in chunks
                if not self.send_to_meshtastic(
                    chunk,
                    destination=destination,
                    channel=self.config.meshtastic.channel,
                )
            ]
            if not failed_chunks:
                logger.info(f"Bridge RNS→Mesh{tag}{multi}: {content[:50]}...")
                with self._stats_lock:
                    self.stats['messages_rns_to_mesh'] += 1
                    self.stats['rns_to_mesh_delivered'] += 1
                self.health.record_message_sent("rns_to_mesh")
                # Theme-A step 3: directed delivery opens/refreshes the
                # session (gated + excluded inside the helper).
                self._maybe_touch_session_r2m(
                    msg, destination, original_body, lxmf_fields)
                if not relayed_by:
                    self._maybe_relay_to_peers(msg, original_body)
            else:
                sent = len(chunks) - len(failed_chunks)
                logger.warning(
                    f"Failed to bridge RNS→Mesh ({sent}/{len(chunks)} chunks sent)"
                )
                with self._stats_lock:
                    self.stats['errors'] += 1
                    self.stats['rns_to_mesh_dropped'] += 1
                # Re-queue ONLY the chunks that failed — each already
                # byte-bounded and carrying the resolved destination. Re-queuing
                # the whole un-chunked original (the old behavior) made the
                # retry exceed the byte cap → _truncate_if_needed dropped every
                # line past the cap, and the payload also lost the DM target
                # (broadcast to ^all). This preserves the full content and the
                # destination, and never re-sends the chunks that already went.
                requeued = self._requeue_failed_chunks(
                    failed_chunks, destination, "meshtastic")
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
