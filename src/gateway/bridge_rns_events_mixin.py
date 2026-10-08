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
import threading
import time
from queue import Full

from .node_tracker import UnifiedNode

#: Guards the lazy first construction of the RNS ingress ledger (see
#: ``_rns_ingress_ledger``); process-wide because the bridge is one per
#: process and two ledger objects on one file clobber each other.
_RNS_INGRESS_INIT_LOCK = threading.Lock()

logger = logging.getLogger(__name__)

#: Monotonic clock for the path-nudge rate limit — a module name so a test
#: can pin it without patching the SHARED ``time`` module (2026-09-20 class).
_monotonic = time.monotonic
#: At most one source-unknown path request per member hash per this many
#: seconds: a forger spamming claims for a listed hash must not make us storm.
INGRESS_PATH_NUDGE_S = 60.0
#: LXMF's unverified_reason for "source never announced" (rns_ingress_policy
#: UNVERIFIED_REASONS) — the ONLY reason a path request can cure.
_SOURCE_UNKNOWN = 0x01
#: Seconds the warm-up waits for rnsd's path responses before re-measuring.
INGRESS_WARMUP_SETTLE_S = 5.0
#: Single-flight: one warm-up at a time however often RNS reconnects.
_WARMUP_LOCK = threading.Lock()


def _spawn(fn, *args) -> None:
    """Daemon thread seam (tests run it inline)."""
    threading.Thread(target=fn, args=args, name="rns-ingress-nudge",
                     daemon=True).start()


def _lxmf_text(value) -> str:
    """LXMF ``content``/``title`` as text: bytes decoded (utf-8, replace),
    None → '', anything else str()'d. Never a repr."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


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
            consume_env="MESHFORGE_ORACLE_RNS_CONSUME", leg="rns")

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
                    src_hex = source_hash.hex().lower()
                    if src_hex in self._peer_gateway_hash_set():
                        # A PEER GATEWAY is never an oracle principal. It
                        # relays its whole RF segment as this one LXMF
                        # identity (raw text, attribution only in fields),
                        # so answering it = answering every node on the
                        # sibling's mesh, bypassing THAT box's allowlist and
                        # cooldown, collapsing all its askers into one
                        # cooldown bucket, and re-broadcasting our reply on
                        # its RF via its R->M leg — cross-mesh answering,
                        # which the operator excluded 2026-06-22. Live
                        # before this guard: 74 of moc3's 118 audit records
                        # were moc's hash, incl. a `help` moc had declined
                        # (cooldown) answered here 2 s later. The relayed
                        # text bridges on as ordinary chat ("cmd crosses,
                        # ack doesn't"); the decline leaves the witness.
                        self._oracle_rns.decline(
                            source_hash.hex(), q, reason="peer_gateway_relay",
                            channel=0)
                    else:
                        reply = self._oracle_rns.handle(source_hash.hex(), q, 0)
                        if reply is not None and self._oracle_rns.consume:
                            return
                except Exception as e:
                    logger.debug(f"mesh oracle (rns) handle error: {e}")

            # Store incoming message for UI/history
            try:
                from commands import messaging
                # Combine title and content for RNS messages. LXMF hands
                # both as BYTES; formatting them raw stored reprs like
                # "[b'MeshForge Gateway'] b'dude-AI…'" in messages.db (moc
                # rows 16239/16250, read 2026-09-01) — a legibility lie the
                # TUI history then repeats. Decode first.
                content = _lxmf_text(message.content)
                title = _lxmf_text(message.title)
                if title:
                    content = f"[{title}] {content}"
                messaging.store_incoming(
                    from_id=source_hash.hex(),
                    content=content,
                    network="rns",
                    to_id=None,  # LXMF doesn't have destination in received messages
                )
            except Exception as e:
                logger.debug(f"Could not store incoming RNS message: {e}")

            # RNS→RF ingress policy (2026-10-06): WHO may reach our radios
            # through the bridge. Runs AFTER the oracle (which keeps its own
            # identity policy) and BEFORE the router, so a refusal is about
            # bridging only. See gateway/rns_ingress_policy.py.
            if not self._rns_ingress_admits(
                    source_hash.hex(),
                    signature_validated=getattr(message, 'signature_validated',
                                                None),
                    unverified_reason=getattr(message, 'unverified_reason',
                                              None)):
                self._notify_message(msg)
                return

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

    # ── RNS→RF ingress policy ────────────────────────────────────────
    def _rns_ingress_ledger(self):
        """Lazily-built, process-wide ledger of unlisted senders. Built under
        a lock: get_status() (status thread) and the LXMF delivery thread may
        race to the first construction, and two ledger objects would clobber
        each other's writes (non-author review 2026-10-07)."""
        led = getattr(self, "_rns_ingress_ledger_obj", None)
        if led is None:
            with _RNS_INGRESS_INIT_LOCK:
                led = getattr(self, "_rns_ingress_ledger_obj", None)
                if led is None:
                    from .rns_ingress_policy import (
                        IngressLedger, default_ledger_path)
                    path = getattr(self, "_rns_ingress_ledger_path", None)
                    led = IngressLedger(path or default_ledger_path())
                    self._rns_ingress_ledger_obj = led
        return led

    # ── identity warm-up (2026-10-08) ───────────────────────────────────
    # LXMF validates a source with RNS.Identity.recall IN THIS PROCESS, which
    # knows only announces it heard while running. moc3, 2026-10-08: rnsd held
    # 627f's path, the gateway (restarted 08:20) did not, so 627f's next
    # message read "source unknown (never announced)" — refused under enforce,
    # after every restart, until each peer next announced. A path request to
    # the local rnsd is answered from its table and teaches this process the
    # identity. Witness-keeping only: never a decision.

    def _rns_recall_identity(self, dest: bytes):
        """The question LXMF itself asks (``LXMessage`` validates the source
        with ``Identity.recall(h, _no_use=True)``): a local dict read, no RPC
        to rnsd — without ``_no_use`` a hit calls ``_used_destination_data``,
        an RPC on rnsd's zero-backlog listener (the #72 surface; review
        2026-10-08)."""
        import RNS
        return RNS.Identity.recall(dest, _no_use=True)

    def _rns_request_path(self, dest: bytes) -> str:
        """``sent`` | ``blocked`` (tx_guard armed — deliberate, not a fault)."""
        import RNS
        from gateway.bounded_rpc import bounded_call
        from utils.tx_guard import TransmitBlocked, assert_rns_tx_allowed
        try:
            assert_rns_tx_allowed(kind="rns_path_request",
                                  detail="ingress identity warm-up")
        except TransmitBlocked:
            return "blocked"
        bounded_call("rnsd.request_path", RNS.Transport.request_path, dest,
                     target=dest.hex()[:8], timeout_s=5.0, exit_on_wedge=False)
        return "sent"

    def rns_ingress_warmup(self, *, settle_s: float = INGRESS_WARMUP_SETTLE_S) -> dict:
        """At RNS connect: request a path for every listed/peer identity this
        process cannot recall, wait ``settle_s``, then RE-MEASURE — the line
        says how many are recallable AFTER, because "request sent" is not the
        END (rnsd silently ignores a request whose announce cache entry is
        gone; review 2026-10-08). Single-flight; never raises; {} when open."""
        if not _WARMUP_LOCK.acquire(blocking=False):
            return {}
        try:
            return self._rns_ingress_warmup_once(settle_s)
        finally:
            _WARMUP_LOCK.release()

    def _rns_ingress_warmup_once(self, settle_s: float) -> dict:
        try:
            posture = self.rns_ingress_posture()
            if posture["policy"] == "open":
                return {}
            members = sorted(set(posture["identities"])
                             | set(self._peer_gateway_hash_set()))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"rns ingress warm-up skipped: {e}")
            return {}

        def recallable(h: str) -> bool:
            try:
                return self._rns_recall_identity(bytes.fromhex(h)) is not None
            except Exception as e:  # noqa: BLE001
                logger.debug(f"rns ingress recall {h[:8]}: {e}")
                return False

        missing = [h for h in members if not recallable(h)]
        requested = blocked = failed = 0
        for h in missing:
            try:
                r = self._rns_request_path(bytes.fromhex(h))
                requested += r == "sent"
                blocked += r == "blocked"
            except Exception as e:  # noqa: BLE001 — counted, never raised
                failed += 1
                logger.debug(f"rns ingress warm-up {h[:8]}: {e}")
        if requested and settle_s > 0:
            ev = getattr(self, "_stop_event", None)
            if ev is not None:
                ev.wait(settle_s)
        still = [h for h in missing if not recallable(h)] if requested else missing
        after = len(members) - len(still)
        logger.info(
            "RNS ingress warm-up: %d/%d recallable in-process before, %d/%d "
            "after %d path request(s)%s%s%s", len(members) - len(missing),
            len(members), after, len(members), requested,
            f"; {blocked} blocked by tx_guard" if blocked else "",
            f"; {failed} FAILED" if failed else "",
            f"; still unknown: {','.join(h[:8] for h in still)}" if still else "")
        return {"members": len(members), "known": len(members) - len(missing),
                "requested": requested, "blocked": blocked, "failed": failed,
                "after": after}

    def _rns_ingress_path_nudge(self, source_hex: str) -> None:
        """One path request per member hash per INGRESS_PATH_NUDGE_S, OFF the
        LXMF delivery thread — a send that blocks must never stall ingress."""
        last = getattr(self, "_rns_ingress_nudged", None)
        if last is None:
            last = self._rns_ingress_nudged = {}
        now = _monotonic()
        h = source_hex.lower()
        if h in last and now - last[h] < INGRESS_PATH_NUDGE_S:
            return
        last[h] = now
        _spawn(self._rns_request_path, bytes.fromhex(h))

    def rns_ingress_posture(self) -> dict:
        """The declared list + the policy in force, for status surfaces."""
        from .rns_ingress_policy import effective_policy
        rns_cfg = getattr(self.config, "rns", None)
        try:
            identities = list(rns_cfg.get_bridge_source_identities())
        except Exception:  # noqa: BLE001 — a mocked/absent section is OPEN
            identities = []
        declared = getattr(rns_cfg, "bridge_source_policy", None)
        policy = effective_policy(identities, declared
                                  if isinstance(declared, str) else None)
        return {"policy": policy, "listed": len(identities),
                "identities": identities}

    def rns_ingress_stamp(self) -> None:
        """Write the posture into the ledger at start, so a declared gateway
        with ZERO unlisted senders is distinguishable, on /fleet, from a box
        that runs no gateway. Writes only when a list is declared (or a
        ledger already exists): an OPEN gateway without a ledger reads as
        absent on the fleet page — stated in `_rns_ingress_cell`'s docstring,
        and the price of never writing state from a unit-test construction.
        Never raises."""
        try:
            posture = self.rns_ingress_posture()
            led = self._rns_ingress_ledger()
            if posture["policy"] != "open" or led.path.exists():
                led.stamp(policy=posture["policy"], listed=posture["listed"],
                          identities=posture["identities"])
        except Exception as e:  # noqa: BLE001
            logger.debug(f"rns ingress stamp skipped: {e}")

    def _lxmf_identity_registry(self):
        """The identity registry (name + purpose per hash), re-read when its
        file changes. Labels only — the allowlist decides (2026-10-07)."""
        cache = getattr(self, "_lxmf_identity_registry_obj", None)
        if cache is None:
            from .lxmf_identity_registry import RegistryCache
            cache = RegistryCache(getattr(self, "_lxmf_identity_registry_dir",
                                          None))
            self._lxmf_identity_registry_obj = cache
        return cache.get()

    def _rns_ingress_admits(self, source_hex: str, *,
                            signature_validated=None,
                            unverified_reason=None) -> bool:
        """True when ``source_hex`` may be bridged onto RF. Never raises.

        Membership counts only with ``signature_validated is True``: LXMF
        delivers a message whose signature failed (or whose source never
        announced) with the CLAIMED source hash, so a claim alone is not an
        identity (2026-10-07)."""
        try:
            from .rns_ingress_policy import (
                POLICY_ENFORCE, POLICY_OPEN, VERDICT_LISTED, VERDICT_PEER,
                VERDICT_UNVERIFIED, verdict)
            posture = self.rns_ingress_posture()
            policy = posture["policy"]
            if policy == POLICY_OPEN:
                return True
            v = verdict(source_hex, posture["identities"],
                        self._peer_gateway_hash_set(),
                        signature_validated=signature_validated)
            if v in (VERDICT_LISTED, VERDICT_PEER):
                # measured USE (house cleaning, 2026-10-07) — a witness,
                # never decisive: a ledger failure must not refuse a member.
                try:
                    self._rns_ingress_ledger().note_listed(source_hex)
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"rns ingress use note failed: {e}")
                return True
            unverified = v == VERDICT_UNVERIFIED
            refused = policy == POLICY_ENFORCE
        except Exception as e:  # noqa: BLE001 — a broken POLICY must not
            # silently CLOSE the bridge, but must not silently open it
            # either: say so, and fall back to today's behaviour (open).
            logger.warning(f"rns ingress policy error ({e}); admitting "
                           f"{source_hex[:8]} as if OPEN")
            return True
        # The DECISION is made. Everything below is witness-keeping, and a
        # failure there must change nothing about the decision. First: a
        # MEMBER that arrived source-unknown is one this process cannot
        # recall yet — ask rnsd so its NEXT message validates (2026-10-08).
        if unverified and unverified_reason == _SOURCE_UNKNOWN:
            try:
                self._rns_ingress_path_nudge(source_hex)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"rns ingress path nudge failed: {e}")
        # (non-author
        # review 2026-10-07: a corrupt ledger entry raised inside the guarded
        # block above and admitted a stranger under enforce, forever, while
        # the refused counter still climbed — state must never open the
        # bridge; only a policy error may).
        with self._stats_lock:
            self.stats['rns_ingress_unlisted'] = (
                self.stats.get('rns_ingress_unlisted', 0) + 1)
            if refused:
                self.stats['rns_to_mesh_refused_unlisted'] = (
                    self.stats.get('rns_to_mesh_refused_unlisted', 0) + 1)
        # WHO it is (name + purpose), never WHETHER it passes: a registry
        # failure leaves the decision above untouched and says it is blind.
        try:
            ident = self._lxmf_identity_registry()
            who = ident.describe(source_hex)
            label = ident.label(source_hex)
        except Exception as e:  # noqa: BLE001
            who = f"unnamed [{source_hex[:8]}] (registry error: {e})"
            label = ""
        ent: dict = {}
        try:
            ent = self._rns_ingress_ledger().record(
                source_hex, policy=policy, refused=refused, label=label,
                unverified=unverified)
        except Exception as e:  # noqa: BLE001 — witnessed, never decisive
            logger.warning(f"rns ingress ledger record failed ({e}); the "
                           f"journal line below is the only witness")
        # The tripwire's witness: the FULL hash, the policy, and what
        # happened — so the journal alone can seed or amend the list.
        if unverified:
            from .rns_ingress_policy import unverified_words
            why = (f"CLAIMS a listed identity but {unverified_words(unverified_reason)}"
                   f" — treated as unlisted")
        else:
            why = "is not in bridge_source_identities"
        logger.info(
            "RNS ingress %s: sender %s (%s) %s "
            "(policy=%s, seen %d×%s) — %s",
            "REFUSED" if refused else "unlisted",
            source_hex.lower(), who, why, policy, ent.get("seen", 1),
            f", refused {ent.get('refused', 0)}×" if refused else "",
            "not bridged onto RF" if refused else
            "bridged anyway (observe); add it to the list or enforce")
        return not refused

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
                    # The sniffer registers its OWN announce handler (aspect
                    # filter None = every announce). When those hooks are in,
                    # capturing here too stored every lxmf.delivery announce
                    # TWICE — measured 2026-09-26 on both gateways: identical
                    # rows 3-30 ms apart, Traffic Statistics doubled. Capture
                    # here only as the fallback when the sniffer runs without
                    # hooks (started before RNS was importable).
                    if (sniffer and sniffer._running
                            and not getattr(sniffer, "_hooks_installed", False)):
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

