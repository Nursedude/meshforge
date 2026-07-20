"""RNS inbound event handlers for the gateway bridge.

Part of the rns_bridge.py split (2026-07-14, MF025 size cap). Holds the RNS-side
inbound processing — the mesh-oracle RNS responder builder, the LXMF receive
handler, and the RNS announce handler. ``RNSMeshtasticBridge`` in rns_bridge.py
is the only consumer. Pure code motion — no behavior change.

Module-name resolution for the moved bodies:
- ``UnifiedNode`` and ``queue.Full`` are imported here directly (not patched).
- ``BridgedMessage`` and the RNS-sniffer set (``HAS_RNS_SNIFFER``,
  ``get_rns_sniffer``, ``RNSPacketInfo``, ``RNSPacketType``) are resolved LAZILY
  off ``gateway.rns_bridge`` at call time — both to avoid the circular import
  (the hub imports this mixin at load) and to keep the
  ``patch("gateway.rns_bridge.HAS_RNS_SNIFFER", ...)`` test seam working
  (calibrated_claims rule 7 — patch the module where the name is looked up).
All other imports the moved bodies use are function-local and moved with them.
"""
import logging
from queue import Full

from .node_tracker import UnifiedNode

logger = logging.getLogger(__name__)


class BridgeRnsEventsMixin:
    """RNS inbound event handlers (oracle responder, LXMF receive, announce) for
    RNSMeshtasticBridge, the sole host class."""

    def _build_rns_oracle_responder(self):
        """Construct the read-only mesh-oracle RNS responder, or None if disabled.

        Mirror of the Meshtastic leg (meshtastic_handler._build_oracle_responder)
        for the LXMF/RNS side. Default OFF — env checked BEFORE importing the
        oracle. Replies via the existing directed send_to_rns; reads a read-only
        NOC snapshot enriched with /api/status; appends to the shared audit log
        with transport="rns". The oracle never controls services or mutates
        config (autonomy rung 1 — report). Uses a separate allowlist
        (MESHFORGE_ORACLE_RNS_ALLOWLIST) keyed on LXMF source-hash hex.
        """
        import os
        if str(os.environ.get("MESHFORGE_ORACLE_ENABLED", "")).strip().lower() \
                not in ("1", "true", "yes", "on"):
            return None
        from oracle import fetch_api_status, read_snapshot
        from oracle.responder import MeshOracleResponder

        def _snapshot():
            return read_snapshot(status=fetch_api_status())

        def _send(text: str, dest: str, channel: int):
            # Return the RnsSendResult itself, NOT bool(...) — the responder
            # classifies on `.reason` so a real send exception is recorded as
            # send_error instead of falling into the benign bucket
            # (structural-dark row 2, oracle_rns_send_blind). It is still
            # bool-compatible, so `delivered` is unchanged.
            try:
                return self.send_to_rns(text, destination_hash=bytes.fromhex(dest))
            except Exception as e:
                logger.debug(f"mesh oracle (rns) send failed: {e}")
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
                logger.debug(f"mesh oracle (rns) log append failed: {e}")

        # The RNS→Mesh leg reads its OWN consume var (default consume): a direct
        # LXMF oracle query must never spill onto the Meshtastic RF channel, so
        # MESHFORGE_ORACLE_CONSUME=0 (Mesh→RNS bridge-through) does NOT flip this
        # leg. Bridge-through here is opt-in via MESHFORGE_ORACLE_RNS_CONSUME=0.
        return MeshOracleResponder.from_env(
            snapshot_fn=_snapshot, send_fn=_send, log_fn=_log,
            transport="rns", allowlist_env="MESHFORGE_ORACLE_RNS_ALLOWLIST",
            consume_env="MESHFORGE_ORACLE_RNS_CONSUME")

    def _on_lxmf_receive(self, message):
        """Handle incoming LXMF message"""
        try:
            # Update node info
            source_hash = message.source_hash
            node = UnifiedNode.from_rns(source_hash)
            self.node_tracker.add_node(node)

            # Capture LXMF message for traffic inspection. Sniffer symbols are
            # resolved off the hub so patch("gateway.rns_bridge.HAS_RNS_SNIFFER")
            # (and the sniffer factory) still apply to this extracted handler.
            from . import rns_bridge as _rns_bridge
            if _rns_bridge.HAS_RNS_SNIFFER:
                try:
                    sniffer = _rns_bridge.get_rns_sniffer()
                    if sniffer and sniffer._running:
                        # LXMessage.content can be either bytes (binary LXMF
                        # payload) or str — encode only when we got text.
                        # (Issue #1162: 'bytes'.encode() raised, dropping the
                        # capture; delivery itself was unaffected.)
                        raw_content = message.content or b''
                        content_bytes = (
                            raw_content.encode('utf-8')
                            if isinstance(raw_content, str)
                            else raw_content
                        )
                        packet_info = _rns_bridge.RNSPacketInfo(
                            packet_type=_rns_bridge.RNSPacketType.DATA,
                            source_hash=source_hash,
                            direction="inbound",
                            payload=content_bytes,
                            payload_size=len(content_bytes),
                            announce_aspect="lxmf.delivery",
                        )
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer LXMF capture error: {e}")

            # Pass through LXMF fields so the xform layer can inspect
            # meshforge_* namespace (Issue #39 attribution + relay-on-receive
            # origin marker). LXMF.fields is dict-or-None; normalize to dict.
            lxmf_fields = getattr(message, 'fields', None) or {}

            # content_id (dedup/identity arc, STEP 2b — measure-only): CARRY the
            # id a sibling gateway already minted on the M→R hop (so both legs
            # share ONE id), else MINT from the RNS origin. RNS/LXMF has no mesh
            # channel, so channel="" here; a later mesh re-injection recomputes
            # its own mesh-leg id from mesh content. Read by nothing yet (the
            # detector is STEP 4); never used to suppress.
            from .canonical_message import compute_content_id
            # BridgedMessage lives on the hub; import lazily (the hub imports
            # this mixin at load, so a top-level import would be circular).
            from .rns_bridge import BridgedMessage
            _carried_cid = (lxmf_fields.get('meshforge_content_id')
                            if isinstance(lxmf_fields, dict) else None)
            if isinstance(_carried_cid, bytes):
                _carried_cid = _carried_cid.decode('utf-8', errors='replace')
            _content_for_id = message.content
            if isinstance(_content_for_id, bytes):
                _content_for_id = _content_for_id.decode('utf-8', errors='replace')
            elif not isinstance(_content_for_id, str):
                _content_for_id = str(_content_for_id or '')
            content_id = _carried_cid or compute_content_id(
                f"rns:{source_hash.hex()}", _content_for_id, "")

            msg = BridgedMessage(
                source_network="rns",
                source_id=source_hash.hex(),
                destination_id=None,
                content=message.content,
                title=message.title,
                content_id=content_id,
                metadata={
                    'lxmf_stamp': message.stamp,
                    'lxmf_fields': lxmf_fields,
                }
            )

            # Mesh oracle (read-only, RNS leg): answer a query directed back to
            # the LXMF source — off-grid, no cloud. Default OFF; never breaks the
            # bridge; a handled query is consumed (not bridged/stored onward)
            # when consume=True (default), or bridged-through when consume=False.
            if self._oracle_rns is not None:
                try:
                    q = message.content
                    if isinstance(q, bytes):
                        q = q.decode('utf-8', errors='ignore')
                    reply = self._oracle_rns.handle(source_hash.hex(), q, 0)
                    if reply is not None and self._oracle_rns.consume:
                        return
                except Exception as e:
                    logger.debug(f"mesh oracle (rns) handle error: {e}")

            # Store incoming message for UI/history
            try:
                from commands import messaging
                # Combine title and content for RNS messages
                content = message.content
                if message.title:
                    content = f"[{message.title}] {content}"
                messaging.store_incoming(
                    from_id=source_hash.hex(),
                    content=content,
                    network="rns",
                    to_id=None,  # LXMF doesn't have destination in received messages
                )
            except Exception as e:
                logger.debug(f"Could not store incoming RNS message: {e}")

            # Queue for bridging if enabled (non-blocking to prevent deadlock)
            if self._router.should_bridge(msg):
                try:
                    self._rns_to_mesh_queue.put_nowait(msg)
                except Full:
                    logger.warning("RNS→Mesh queue full, dropping message")
                    with self._stats_lock:
                        self.stats['errors'] += 1

            # Notify callbacks
            self._notify_message(msg)

        except Exception as e:
            logger.error(f"Error processing LXMF message: {e}")

    def _on_rns_announce(self, dest_hash, announced_identity, app_data):
        """Handle RNS announce for node discovery"""
        try:
            # Capture announce packet for traffic inspection. Sniffer symbols
            # resolved off the hub (see the LXMF handler note) to preserve the
            # patch("gateway.rns_bridge.HAS_RNS_SNIFFER") test seam.
            from . import rns_bridge as _rns_bridge
            if _rns_bridge.HAS_RNS_SNIFFER:
                try:
                    import RNS
                    sniffer = _rns_bridge.get_rns_sniffer()
                    if sniffer and sniffer._running:
                        packet_info = _rns_bridge.RNSPacketInfo(
                            packet_type=_rns_bridge.RNSPacketType.ANNOUNCE,
                            destination_hash=dest_hash,
                            direction="inbound",
                            announce_app_data=app_data,
                            announce_aspect="lxmf.delivery",
                        )
                        # Get identity hash if available
                        if announced_identity:
                            try:
                                packet_info.source_hash = announced_identity.hash
                                packet_info.announce_identity = announced_identity.hash
                            except Exception:
                                pass
                        # Get hop count
                        try:
                            if RNS.Transport.has_path(dest_hash):
                                hops = RNS.Transport.hops_to(dest_hash)
                                packet_info.hops = hops if hops is not None else 0
                        except Exception:
                            pass
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer capture error: {e}")

            node = UnifiedNode.from_rns(dest_hash, app_data=app_data)
            self.node_tracker.add_node(node)
            logger.debug(f"Discovered RNS node: {dest_hash.hex()[:8]}")
        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")

