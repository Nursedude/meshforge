"""Meshtastic Broadcast Bridge — fan out Meshtastic channels as LXMF DMs.

Symmetric mirror of MeshAnchor's lxmf_broadcast_bridge.py. Closes the
one-way bridge gap discovered 2026-05-18: MeshCore→Meshtastic was already
working (via MeshAnchor's LXMFBroadcastBridge fanning out to subscribed
LXMF peers, including MeshForge gateways that re-publish to Meshtastic);
the reverse direction had no analogous handler on the MeshForge side.

A focused plug-in on top of the existing RNS runtime. The bridge runs
its OWN ``LXMF.LXMRouter`` (sharing the process-wide ``RNS.Transport``)
so it can register a delivery identity distinct from the gateway's.
LXMF 0.9.4's ``register_delivery_identity`` returns None if one is
already registered on the router — a single shared router can't host
both the gateway's identity and this broadcast identity.

Subscription protocol (over LXMF DM to the broadcast identity):

    "subscribe"      — add sender to fan-out list
    "unsubscribe"    — remove from fan-out list
    "channels"       — reply with the channel allowlist
    "help"           — short usage reply

Message flow:

    Meshtastic channel RX
        → MeshtasticHandler._handle_text_message → BridgedMessage
        → RNSMeshtasticBridge._notify_message → message_callbacks
        → MeshtasticBroadcastBridge.on_meshtastic_message
        → channel allowlist filter
        → format prefix
        → for each subscriber: LXMF.LXMessage(broadcast_identity → sub)
        → own_router.handle_outbound

This is intentionally RX-only fan-out — no reverse path back into
Meshtastic. Loop prevention is therefore trivial: a fan-out can't
re-enter on the Meshtastic side because the bridge never originates a
Meshtastic packet.

Persistence:
    ~/.config/meshforge/meshtastic_broadcast_identity   (RNS.Identity)
    ~/.config/meshforge/meshtastic_broadcast_storage/   (LXMF router state)
    ~/.config/meshforge/meshtastic_broadcast_subs.db    (sqlite — tracked
                                                         in utils.db_inventory)
"""

from __future__ import annotations

import json
import logging
import signal as _signal_mod
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.boundary_timing import call_boundary
from utils.db_helpers import connect_tuned
from utils.paths import get_real_user_home
from utils.safe_import import safe_import

from .config import GatewayConfig, MeshtasticBroadcastConfig
from utils.tx_guard import assert_rns_tx_allowed

_RNS_mod, _HAS_RNS = safe_import("RNS")
_LXMF_mod, _HAS_LXMF = safe_import("LXMF")

logger = logging.getLogger(__name__)


@contextmanager
def _suppress_signal_in_thread():
    """Disable signal.signal() registration on non-main threads.

    LXMF.LXMRouter() and RNS.Reticulum() register SIGINT/SIGTERM handlers
    in their constructors. Python only allows signal handler registration
    from the main thread, so the bridge — which starts from
    RNSMeshtasticBridge._rns_loop running in a background thread —
    blows up at LXMRouter() with "signal only works in main thread".

    Same fix the gateway uses: monkey-patch signal.signal to a no-op for
    the duration of the boot, then restore.
    """
    if threading.current_thread() is threading.main_thread():
        yield
        return
    original = _signal_mod.signal

    def _safe_signal(signalnum, handler):
        return _signal_mod.SIG_DFL

    _signal_mod.signal = _safe_signal
    try:
        yield
    finally:
        _signal_mod.signal = original


_VERB_SUBSCRIBE = "subscribe"
_VERB_UNSUBSCRIBE = "unsubscribe"
_VERB_CHANNELS = "channels"
_VERB_HELP = "help"


# Issue #66 synthetic ACK content prefixes. When the gateway emits a synth
# ACK back on the originating Meshtastic channel, the same packet round-
# trips through LoRa (radio self-RX or MQTT-bridge re-emission on a peer
# gateway) and re-enters MeshtasticHandler as a fresh BridgedMessage with
# `is_broadcast=True` and `source_network="meshtastic"`. Without this
# filter the bridge re-fans-out the ACK, mints a new pending-ack id, and
# emits another ACK back — a tight loop observed in field smoke on moc3
# at 22:21 HST (4+ ACK emissions in 10s before manual cutoff). The
# meshforge_is_synth_ack metadata flag set by _emit_ack_to_origin only
# survives in-process; over the wire only the textual content remains.
_SYNTH_ACK_CONTENT_PREFIXES = (
    "[delivered:",
    "[timeout:",
    "[failed:",
)


TIER_LOCAL = "local"
TIER_FEDERATION = "federation"
TIER_EXTERNAL = "external"

STATE_HEALTHY = "healthy"
STATE_DEGRADED = "degraded"
STATE_STALE = "stale"
STATE_DEAD = "dead"
# A subscriber we have sent to but for which we have NEVER received a real
# LXMF recipient delivery proof (finding-2 fix, 2026-07-23). Distinct from
# HEALTHY (which now requires a proof) and from DEAD (which we will never
# claim without evidence). This is the per-subscriber analogue of
# delivery_counters.compute_confirmation_view's ``unconfirmable_sent`` — the
# blind spot made VISIBLE rather than averaged into a healthy-looking value
# (Issue #74; honest_failure_modes #2 hold-and-surface).
STATE_UNCONFIRMED = "unconfirmed"

_BACKOFF_BY_TIER = {
    TIER_LOCAL:      (1.0,  600.0),
    TIER_FEDERATION: (5.0,  1800.0),
    TIER_EXTERNAL:   (30.0, 3600.0),
}

_DEGRADED_FAIL_THRESHOLD = 3
_STALE_SINCE_SUCCESS_SEC = 24 * 3600
_DEAD_SINCE_SUCCESS_SEC = 7 * 24 * 3600


@dataclass
class Subscriber:
    """One LXMF identity opted in to the broadcast fan-out.

    ``last_delivery`` is the last successful fan-out ENQUEUE (hand-off to the
    LXMF router) — best-effort, NOT a confirmed delivery. ``last_confirmed_at``
    is the last time a real LXMF recipient delivery proof arrived (state
    DELIVERED). The two are deliberately separate: the finding-2 fix
    (2026-07-23) stopped treating an enqueue as a delivery, so a subscriber is
    "confirmable" iff ``last_confirmed_at is not None``.
    """
    lxmf_hash: str
    added_at: datetime
    last_delivery: Optional[datetime] = None
    tier: str = TIER_EXTERNAL
    state: str = STATE_HEALTHY
    consecutive_failures: int = 0
    last_failure_at: Optional[datetime] = None
    last_confirmed_at: Optional[datetime] = None


class SubscriberStore:
    """SQLite-backed subscriber list. Operators add/remove via the LXMF
    subscription protocol; the bridge never GCs entries automatically
    because losing a subscriber silently is worse than carrying a dead
    one (the LXMF router will simply fail to find a path).
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, connect_tuned(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscribers (
                    lxmf_hash TEXT PRIMARY KEY,
                    added_at TEXT NOT NULL,
                    last_delivery TEXT
                )
                """
            )
            existing_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(subscribers)")
            }
            migrations = [
                ("tier", f"TEXT NOT NULL DEFAULT '{TIER_EXTERNAL}'"),
                ("state", f"TEXT NOT NULL DEFAULT '{STATE_HEALTHY}'"),
                ("consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
                ("last_failure_at", "TEXT"),
                # finding-2 (2026-07-23): real recipient-proof anchor. NULL by
                # default so EVERY pre-existing subscriber becomes UNCONFIRMED
                # until its next real proof — the old ``last_delivery`` values
                # were manufactured on enqueue and must NOT be inherited as
                # fake confirmations (honest reset).
                ("last_confirmed_at", "TEXT"),
            ]
            for col_name, col_def in migrations:
                if col_name in existing_cols:
                    continue
                conn.execute(
                    f"ALTER TABLE subscribers ADD COLUMN {col_name} {col_def}"
                )
            conn.commit()

    def add(self, lxmf_hash: str, tier: str = TIER_EXTERNAL) -> bool:
        lxmf_hash = lxmf_hash.lower()
        now = datetime.utcnow().isoformat()
        with self._lock, connect_tuned(self._db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO subscribers("
                "lxmf_hash, added_at, tier, state, consecutive_failures"
                ") VALUES (?, ?, ?, ?, 0)",
                # A brand-new subscriber has never been confirmed — UNCONFIRMED
                # is its honest birth state (finding-2). Starting HEALTHY would
                # also make every first failure emit a spurious healthy→
                # unconfirmed transition.
                (lxmf_hash, now, tier, STATE_UNCONFIRMED),
            )
            conn.commit()
            return cur.rowcount > 0

    def remove(self, lxmf_hash: str) -> bool:
        lxmf_hash = lxmf_hash.lower()
        with self._lock, connect_tuned(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM subscribers WHERE lxmf_hash = ?", (lxmf_hash,)
            )
            conn.commit()
            return cur.rowcount > 0

    _ALL_COLS = (
        "lxmf_hash, added_at, last_delivery, tier, state, "
        "consecutive_failures, last_failure_at, last_confirmed_at"
    )

    def _row_to_subscriber(self, row) -> Subscriber:
        h, added, last, tier, state, fails, failed, confirmed = row
        return Subscriber(
            lxmf_hash=h,
            added_at=_parse_iso(added) or datetime.utcnow(),
            last_delivery=_parse_iso(last),
            tier=tier or TIER_EXTERNAL,
            state=state or STATE_HEALTHY,
            consecutive_failures=int(fails or 0),
            last_failure_at=_parse_iso(failed),
            last_confirmed_at=_parse_iso(confirmed),
        )

    def list_all(self) -> List[Subscriber]:
        with self._lock, connect_tuned(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT {self._ALL_COLS} FROM subscribers"
            ).fetchall()
        return [self._row_to_subscriber(r) for r in rows]

    def list_by_state(self, state: str) -> List[Subscriber]:
        with self._lock, connect_tuned(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT {self._ALL_COLS} FROM subscribers WHERE state = ?",
                (state,),
            ).fetchall()
        return [self._row_to_subscriber(r) for r in rows]

    def list_by_tier(self, tier: str) -> List[Subscriber]:
        with self._lock, connect_tuned(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT {self._ALL_COLS} FROM subscribers WHERE tier = ?",
                (tier,),
            ).fetchall()
        return [self._row_to_subscriber(r) for r in rows]

    def mark_fanout_enqueued(self, lxmf_hash: str) -> None:
        """Record a successful fan-out ENQUEUE — a hand-off to the LXMF router.

        finding-2 (2026-07-23): this is NOT a confirmed delivery. ``handle_outbound``
        enqueues the LXM asynchronously; the recipient may never receive it. So —
        unlike the pre-fix ``mark_delivered`` this replaces — it must NOT reset the
        failure counter and must NOT return the subscriber to HEALTHY. Only a real
        LXMF delivery proof (``mark_confirmed``) does that. We stamp ``last_delivery``
        purely for operator observability ("last time we tried"); ``desired_state``
        no longer keys off it.
        """
        lxmf_hash = lxmf_hash.lower()
        now = datetime.utcnow().isoformat()
        with self._lock, connect_tuned(self._db_path) as conn:
            conn.execute(
                "UPDATE subscribers SET last_delivery = ? WHERE lxmf_hash = ?",
                (now, lxmf_hash),
            )
            conn.commit()

    def mark_confirmed(self, lxmf_hash: str) -> None:
        """A REAL LXMF recipient delivery proof (state DELIVERED) arrived.

        This is the ONLY path back to HEALTHY: it stamps the confirmable anchor
        ``last_confirmed_at``, clears the failure counter, and returns the
        subscriber to HEALTHY. A propagation-node hand-off (state SENT) is NOT a
        recipient proof and must NOT call this — it leaves the subscriber
        UNCONFIRMED (held, surfaced), per honest_failure_modes #2.
        """
        lxmf_hash = lxmf_hash.lower()
        now = datetime.utcnow().isoformat()
        prior_state: Optional[str] = None
        with self._lock, connect_tuned(self._db_path) as conn:
            row = conn.execute(
                "SELECT state FROM subscribers WHERE lxmf_hash = ?",
                (lxmf_hash,),
            ).fetchone()
            if row:
                prior_state = row[0]
            conn.execute(
                "UPDATE subscribers "
                "SET last_confirmed_at = ?, last_delivery = ?, "
                "    consecutive_failures = 0, state = ? "
                "WHERE lxmf_hash = ?",
                (now, now, STATE_HEALTHY, lxmf_hash),
            )
            conn.commit()
        if prior_state and prior_state != STATE_HEALTHY:
            logger.info(json.dumps({
                "event": "meshtastic_broadcast_state_transition",
                "hash": lxmf_hash[:8],
                "from": prior_state,
                "to": STATE_HEALTHY,
                "fails": 0,
                "reason": "delivery_confirmed",
            }))

    @staticmethod
    def desired_state(sub: "Subscriber", now: datetime) -> str:
        """Target state from row data — the confirmable-population rule (#74).

        A subscriber is judged HEALTHY/STALE/DEAD only once it has produced a
        real recipient proof (``last_confirmed_at is not None``). Before that we
        genuinely cannot tell "alive but proof-less path" from "dead", so we HOLD
        at UNCONFIRMED and never claim either extreme (honest_failure_modes #2).
        Active failures are still observable on an unconfirmed subscriber, so
        those surface as DEGRADED — an honest "having trouble" that stops short
        of the "was alive, went dark" claim STALE/DEAD make.

        Two-evidence rule (2026-07-26 review C2): STALE/DEAD claim "was alive,
        went dark", so they require BOTH the age gate AND sustained failures
        (consecutive_failures >= _DEGRADED_FAIL_THRESHOLD). Age alone after a
        quiet stretch is not evidence of death — one transient failure after 8
        quiet days used to flip HEALTHY→DEAD while the same history with zero
        failures read HEALTHY. Sub-threshold failures are a transient wobble:
        backoff still applies, no state claim is made.
        """
        confirmable = sub.last_confirmed_at is not None
        if not confirmable:
            if sub.consecutive_failures >= _DEGRADED_FAIL_THRESHOLD:
                return STATE_DEGRADED
            return STATE_UNCONFIRMED
        if sub.consecutive_failures < _DEGRADED_FAIL_THRESHOLD:
            return STATE_HEALTHY
        since_success = (now - sub.last_confirmed_at).total_seconds()
        if since_success >= _DEAD_SINCE_SUCCESS_SEC:
            return STATE_DEAD
        if since_success >= _STALE_SINCE_SUCCESS_SEC:
            return STATE_STALE
        return STATE_DEGRADED

    @staticmethod
    def backoff_seconds(sub: "Subscriber") -> float:
        if sub.consecutive_failures == 0:
            return 0.0
        floor, cap = _BACKOFF_BY_TIER.get(
            sub.tier, _BACKOFF_BY_TIER[TIER_EXTERNAL]
        )
        return min(floor * (2 ** (sub.consecutive_failures - 1)), cap)

    def should_attempt(
        self, sub: "Subscriber", now: Optional[datetime] = None,
    ) -> bool:
        if sub.consecutive_failures == 0:
            return True
        if sub.last_failure_at is None:
            return True
        window = self.backoff_seconds(sub)
        elapsed = ((now or datetime.utcnow()) - sub.last_failure_at).total_seconds()
        return elapsed >= window

    def mark_failed(self, lxmf_hash: str, reason: str) -> None:
        lxmf_hash = lxmf_hash.lower()
        now = datetime.utcnow()
        now_iso = now.isoformat()
        prior_state: Optional[str] = None
        new_state: Optional[str] = None
        new_fails: int = 0
        with self._lock, connect_tuned(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE subscribers "
                "SET last_failure_at = ?, "
                "    consecutive_failures = consecutive_failures + 1 "
                "WHERE lxmf_hash = ?",
                (now_iso, lxmf_hash),
            )
            if cur.rowcount == 0:
                conn.commit()
                return
            row = conn.execute(
                f"SELECT {self._ALL_COLS} FROM subscribers "
                f"WHERE lxmf_hash = ?",
                (lxmf_hash,),
            ).fetchone()
            sub = self._row_to_subscriber(row)
            new_fails = sub.consecutive_failures
            target = self.desired_state(sub, now)
            if target != sub.state:
                conn.execute(
                    "UPDATE subscribers SET state = ? WHERE lxmf_hash = ?",
                    (target, lxmf_hash),
                )
                prior_state = sub.state
                new_state = target
            conn.commit()
        logger.debug(
            "Meshtastic broadcast subscriber %s fan-out failed: %s",
            lxmf_hash[:8], reason,
        )
        if new_state and prior_state:
            logger.info(json.dumps({
                "event": "meshtastic_broadcast_state_transition",
                "hash": lxmf_hash[:8],
                "from": prior_state,
                "to": new_state,
                "fails": new_fails,
                "reason": reason,
            }))


# LXMF.LXMessage.DELIVERED (== 0x08). The delivery callback fires for BOTH a
# true recipient proof (__mark_delivered → state DELIVERED) AND a
# propagation-node hand-off (__mark_propagated → state SENT), so the callback
# MUST read lxm.state to tell them apart. Only DELIVERED is a recipient proof.
_LXM_STATE_DELIVERED_FALLBACK = 8


def _lxm_delivered_state(lxmf_module: Any) -> int:
    """The integer LXMessage.DELIVERED, tolerant of a mocked LXMF in tests."""
    val = getattr(getattr(lxmf_module, "LXMessage", None), "DELIVERED", None)
    return val if isinstance(val, int) else _LXM_STATE_DELIVERED_FALLBACK


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _msg_source_address(msg: Any) -> str:
    """Extract sender address from BridgedMessage or CanonicalMessage.

    MeshtasticHandler emits BridgedMessage (source_id); future
    CanonicalMessage callers expose source_address. Both flow through
    the same callback list.
    """
    return (
        getattr(msg, "source_address", None)
        or getattr(msg, "source_id", None)
        or ""
    )


def _msg_source_network(msg: Any) -> str:
    return getattr(msg, "source_network", "") or ""


def _msg_is_broadcast(msg: Any) -> bool:
    return bool(getattr(msg, "is_broadcast", False))


def _msg_metadata(msg: Any) -> Dict[str, Any]:
    return getattr(msg, "metadata", None) or {}


def _msg_content(msg: Any) -> str:
    # Pri-12 (gateway review 2026-07-23): CanonicalMessage advertises a bytes
    # `content` shape too. Without this, a bytes payload flowed into str ops
    # (startswith/format) → TypeError swallowed as a generic callback error →
    # the message silently failed to fan out. Normalize like _on_lxmf_delivery.
    raw = getattr(msg, "content", "") or ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw if isinstance(raw, str) else str(raw)


def format_broadcast_text(msg: Any, prefix_format: str) -> str:
    """Render a BridgedMessage / CanonicalMessage as the LXMF body text."""
    channel = _msg_metadata(msg).get("channel", 0)
    sender = _msg_source_address(msg) or "?"
    if len(sender) > 16:
        sender = sender[:16]
    text = _msg_content(msg)
    try:
        return prefix_format.format(channel=channel, sender=sender, text=text)
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(
            "Bad prefix_format %r: %s — using fallback", prefix_format, e
        )
        return f"[meshtastic ch{channel}:{sender}] {text}"


class MeshtasticBroadcastBridge:
    """Meshtastic→LXMF fan-out bridge.

    Owns its own LXMRouter so it can register a distinct delivery
    identity — LXMF 0.9.4 caps the gateway router at one identity.

    Lifecycle:
        bridge = MeshtasticBroadcastBridge(config)
        bridge.start()                              # boot router, announce
        bridge.on_meshtastic_message(bridged_msg)   # called from gateway
        bridge.stop()
    """

    def __init__(
        self,
        broadcast_config: MeshtasticBroadcastConfig,
        rns_module: Any = None,
        lxmf_module: Any = None,
        identity_path: Optional[Path] = None,
        db_path: Optional[Path] = None,
        storage_path: Optional[Path] = None,
        propagation_node: str = "",
        persistent_queue: Any = None,
        ack_emit_callback: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self._config = broadcast_config
        self._rns = rns_module if rns_module is not None else _RNS_mod
        self._lxmf = lxmf_module if lxmf_module is not None else _LXMF_mod
        self._propagation_node = (propagation_node or "").strip()
        # Issue #66 first-caller wiring. The parent RNSMeshtasticBridge
        # owns the PersistentMessageQueue (where pending-ack records live)
        # and the _maybe_emit_ack_for_msgid callable (which turns a
        # winning LXMF delivery into a synthetic [delivered:<id>] back to
        # the originating Meshtastic channel). Both are optional — when
        # None, the bridge falls back to pure fan-out with no ack tracking.
        # Only used when broadcast_config.ack_required is True.
        self._persistent_queue = persistent_queue
        self._ack_emit_callback = ack_emit_callback

        config_dir = get_real_user_home() / ".config" / "meshforge"
        self._identity_path = identity_path or (
            Path(broadcast_config.identity_file)
            if broadcast_config.identity_file
            else config_dir / "meshtastic_broadcast_identity"
        )
        db = db_path or (
            Path(broadcast_config.db_file)
            if broadcast_config.db_file
            else config_dir / "meshtastic_broadcast_subs.db"
        )
        self._storage_path = storage_path or (
            config_dir / "meshtastic_broadcast_storage"
        )
        self._subs = SubscriberStore(db)

        # Filled in start()
        self._identity = None
        self._router = None
        self._lxmf_source = None
        self._destination_hash: Optional[bytes] = None

        self._running = False
        self._started_at: Optional[datetime] = None
        self._stop_event = threading.Event()
        self._announce_thread: Optional[threading.Thread] = None

        self._stats_lock = threading.Lock()
        self.stats: Dict[str, int] = {
            "fanouts": 0,
            "subscribes": 0,
            "unsubscribes": 0,
            "filtered_channel": 0,
            "filtered_non_meshtastic": 0,
            "filtered_synth_ack": 0,
            "errors": 0,
            "skipped_backoff": 0,
        }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def destination_hash_hex(self) -> str:
        return self._destination_hash.hex() if self._destination_hash else ""

    def start(self) -> bool:
        if self._running:
            return True
        if self._rns is None or self._lxmf is None:
            logger.error("Meshtastic broadcast bridge: RNS/LXMF not installed")
            return False

        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
            self._identity = self._load_or_create_identity()

            # Power-loss-truncated ratchets crash register_delivery_identity()
            # AFTER Transport registration, wedging retries on 'already
            # registered' — validate and quarantine first (2026-08-27).
            from gateway._rns_bridge_connection import quarantine_corrupt_ratchets
            quarantine_corrupt_ratchets(self._storage_path)

            with _suppress_signal_in_thread():
                self._router = self._lxmf.LXMRouter(
                    storagepath=str(self._storage_path)
                )
            self._router.register_delivery_callback(self._on_lxmf_delivery)

            self._lxmf_source = self._router.register_delivery_identity(
                self._identity,
                display_name=self._config.display_name,
            )
            if self._lxmf_source is None:
                logger.error(
                    "Meshtastic broadcast bridge: register_delivery_identity "
                    "returned None (router state corrupt — wipe %s and restart)",
                    self._storage_path,
                )
                return False

            self._destination_hash = getattr(self._lxmf_source, "hash", None)
            if self._destination_hash is None:
                logger.error(
                    "Meshtastic broadcast bridge: source has no .hash attribute"
                )
                return False

            logger.info(
                "Meshtastic broadcast identity registered: %s (%s)",
                self._config.display_name,
                self._destination_hash.hex(),
            )

            if self._propagation_node:
                try:
                    self._router.set_outbound_propagation_node(
                        bytes.fromhex(self._propagation_node)
                    )
                    logger.info(
                        "Meshtastic broadcast propagation node: %s",
                        self._propagation_node,
                    )
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "Invalid propagation_node hash %r: %s",
                        self._propagation_node,
                        e,
                    )

            self._safe_announce()

            self._running = True
            self._started_at = datetime.utcnow()
            self._stop_event.clear()
            if self._config.announce_interval_sec > 0:
                self._announce_thread = threading.Thread(
                    target=self._announce_loop,
                    name="MeshtasticBroadcast-Announce",
                    daemon=True,
                )
                self._announce_thread.start()
            _set_active_bridge(self)
            return True
        except Exception as e:
            logger.error("Meshtastic broadcast bridge failed to start: %s", e)
            with self._stats_lock:
                self.stats["errors"] += 1
            return False

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._announce_thread and self._announce_thread.is_alive():
            self._announce_thread.join(timeout=3)
        self._announce_thread = None
        # LXMRouter has no clean shutdown method in 0.9.4 — drop ref and
        # let GC clean up. Re-creating on next start is safe because
        # storagepath persistence handles state.
        self._router = None
        self._lxmf_source = None
        self._destination_hash = None
        self._started_at = None
        _clear_active_bridge(self)

    def _load_or_create_identity(self):
        path = self._identity_path
        path.parent.mkdir(parents=True, exist_ok=True)
        RNS = self._rns
        if path.exists():
            try:
                return RNS.Identity.from_file(str(path))
            except Exception as e:
                logger.warning(
                    "Could not load broadcast identity %s: %s — recreating",
                    path, e,
                )
        identity = RNS.Identity()
        identity.to_file(str(path))
        return identity

    def _announce_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            interval = max(60, int(self._config.announce_interval_sec))
            if self._stop_event.wait(interval):
                return
            self._safe_announce()

    def _safe_announce(self) -> None:
        if self._destination_hash is None:
            return
        try:
            assert_rns_tx_allowed(
                kind="rns_announce",
                detail="meshtastic_broadcast_bridge._safe_announce")
            self._router.announce(self._destination_hash)
            logger.debug(
                "Meshtastic broadcast announce sent (%s)",
                self._destination_hash.hex(),
            )
        except Exception as e:
            logger.debug("Announce failed (will retry): %s", e)

    # ------------------------------------------------------------------
    # Inbound — from gateway hooks
    # ------------------------------------------------------------------

    def on_meshtastic_message(self, msg: Any) -> None:
        """Gateway message-callback hook. Filters and fans out.

        Accepts either ``BridgedMessage`` (what MeshtasticHandler emits
        today) or a future ``CanonicalMessage`` — field access is via
        getattr-shaped helpers so both shapes route through the same
        code path.
        """
        if not self._running:
            return
        if _msg_source_network(msg) != "meshtastic":
            with self._stats_lock:
                self.stats["filtered_non_meshtastic"] += 1
            return
        if not _msg_is_broadcast(msg):
            return
        content = _msg_content(msg)
        if not content:
            return

        # Loop guard: Issue #66 synth-ACKs round-trip through LoRa and
        # come back into MeshtasticHandler as fresh broadcasts. Without
        # this check the bridge re-fans them out, mints another
        # pending-ack id, and emits another ACK — observed loop on moc3
        # 2026-05-18 22:21 HST. The in-process meshforge_is_synth_ack
        # metadata flag (checked below) doesn't survive the wire.
        stripped = content.lstrip()
        if any(stripped.startswith(p) for p in _SYNTH_ACK_CONTENT_PREFIXES):
            with self._stats_lock:
                self.stats["filtered_synth_ack"] += 1
            return

        meta = _msg_metadata(msg)
        channel = meta.get("channel", 0)
        if self._config.channels and channel not in self._config.channels:
            with self._stats_lock:
                self.stats["filtered_channel"] += 1
            return

        body = format_broadcast_text(msg, self._config.prefix_format)
        title = "Meshtastic"

        subscribers = self._subs.list_all()
        if not subscribers:
            return

        # Issue #66 first-caller: mint ONE broadcast msg_id and register
        # ONE pending-ack record per fan-out (not per subscriber). The
        # first subscriber whose LXMF delivery callback fires wins —
        # PersistentMessageQueue.mark_acked is idempotent, so only the
        # first emits the synthetic ACK back to the originating channel.
        # Recursion-guard: never track a message that's already tagged
        # as a synth ACK.
        #
        # Origin-address routing:
        # - If the message carries a real source identity (Meshtastic
        #   DMs have a from_id like "!abcd1234"; MeshtasticHandler
        #   surfaces this as source_id even for channel broadcasts),
        #   route the synth ACK back to that node directly.
        # - Channel broadcasts where the source identity is unknown
        #   use the placeholder address "channel:<idx>" and the
        #   substrate's _emit_ack_to_origin dispatcher will broadcast
        #   the ACK back on the originating channel.
        ack_msg_id: Optional[str] = None
        sender = _msg_source_address(msg)
        if sender:
            ack_origin_address = sender
        else:
            ack_origin_address = f"channel:{channel}"
        if (self._config.ack_required
                and self._persistent_queue is not None
                and self._ack_emit_callback is not None
                and not meta.get("meshforge_is_synth_ack")):
            ack_msg_id = f"mbcast-{int(time.time() * 1000)}-ch{channel}"
            try:
                ok = self._persistent_queue.register_pending_ack(
                    ack_msg_id,
                    origin_network="meshtastic",
                    origin_address=ack_origin_address,
                    timeout_seconds=300,
                    allow_orphan=True,
                )
                if not ok:
                    logger.error(
                        "register_pending_ack returned False for %s "
                        "(allow_orphan was True — this is a hard DB "
                        "error, not a missing-row condition)",
                        ack_msg_id[:16],
                    )
                    ack_msg_id = None
            except Exception as e:
                logger.warning(
                    "register_pending_ack failed for %s: %s",
                    ack_msg_id[:16], e,
                )
                ack_msg_id = None

        now = datetime.utcnow()
        skipped = 0
        for sub in subscribers:
            if not self._subs.should_attempt(sub, now=now):
                skipped += 1
                continue
            self._send_to_subscriber(sub, body, title, ack_msg_id=ack_msg_id)
        if skipped:
            with self._stats_lock:
                self.stats["skipped_backoff"] += skipped

    def _on_lxmf_delivery(self, lxmf_message: Any) -> None:
        """LXMRouter delivery callback — subscription protocol commands."""
        if not self._running:
            return
        try:
            source_hash = getattr(lxmf_message, "source_hash", b"")
            source_hex = (
                source_hash.hex() if isinstance(source_hash, bytes)
                else str(source_hash)
            )
            content_raw = getattr(lxmf_message, "content", b"") or b""
            if isinstance(content_raw, bytes):
                content = content_raw.decode("utf-8", errors="replace")
            else:
                content = str(content_raw)

            self._handle_subscription_command(source_hex, content)
        except Exception as e:
            logger.error("Meshtastic broadcast inbound error: %s", e)
            with self._stats_lock:
                self.stats["errors"] += 1

    def _handle_subscription_command(self, source_hex: str, body: str) -> None:
        verb = (body.strip().split() or [""])[0].lower()

        if verb == _VERB_SUBSCRIBE:
            added = self._subs.add(source_hex)
            with self._stats_lock:
                self.stats["subscribes"] += 1
            reply = (
                "Subscribed. You'll receive Meshtastic channel "
                f"{self._config.channels} as LXMF DMs. Send 'unsubscribe' to stop."
                if added
                else "You are already subscribed."
            )
            self._reply(source_hex, reply)
        elif verb == _VERB_UNSUBSCRIBE:
            removed = self._subs.remove(source_hex)
            with self._stats_lock:
                self.stats["unsubscribes"] += 1
            reply = "Unsubscribed." if removed else "You were not subscribed."
            self._reply(source_hex, reply)
        elif verb == _VERB_CHANNELS:
            self._reply(
                source_hex,
                f"Bridging Meshtastic channels: {self._config.channels}",
            )
        elif verb == _VERB_HELP or verb == "":
            self._reply(
                source_hex,
                "MeshForge Meshtastic broadcast bridge. Commands: "
                "subscribe, unsubscribe, channels, help.",
            )
        else:
            if self._config.autosubscribe:
                self._subs.add(source_hex)
                with self._stats_lock:
                    self.stats["subscribes"] += 1
                self._reply(source_hex, "Subscribed (auto).")

    # ------------------------------------------------------------------
    # Outbound LXMF
    # ------------------------------------------------------------------

    def _send_to_subscriber(self, sub: Subscriber, body: str, title: str,
                            *, ack_msg_id: Optional[str] = None) -> bool:
        """Fan a broadcast out to one subscriber.

        When ``ack_msg_id`` is set, registers an LXMF delivery callback
        that drives Issue #66's first-wins ACK synthesis back to the
        originating Meshtastic channel.
        """
        if self._router is None or self._lxmf_source is None:
            return False
        RNS = self._rns
        LXMF = self._lxmf
        try:
            dest_hash = bytes.fromhex(sub.lxmf_hash)
        except ValueError:
            logger.warning("Invalid subscriber hash %r — skipping", sub.lxmf_hash)
            self._subs.mark_failed(sub.lxmf_hash, "invalid_hash")
            return False

        hash_short = sub.lxmf_hash[:8]
        try:
            if not call_boundary("rnsd.has_path",
                                 RNS.Transport.has_path, dest_hash,
                                 target=hash_short):
                call_boundary("rnsd.request_path",
                              RNS.Transport.request_path, dest_hash,
                              target=hash_short)
                for _ in range(30):
                    if RNS.Transport.has_path(dest_hash):
                        break
                    if self._stop_event.wait(0.1):
                        return False

            dest_identity = call_boundary(
                "rnsd.identity_recall",
                RNS.Identity.recall, dest_hash,
                target=hash_short,
            )
            if dest_identity is None:
                logger.debug(
                    "No identity recalled for %s — dropping fanout",
                    sub.lxmf_hash,
                )
                self._subs.mark_failed(sub.lxmf_hash, "identity_recall_null")
                return False

            destination = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery",
            )
            lxm = LXMF.LXMessage(destination, self._lxmf_source, body, title)

            # finding-2 (2026-07-23): register a delivery/failed callback on
            # EVERY fan-out (not just the ack_required path) so real LXMF
            # receipts drive subscriber health honestly. LXMessage holds a
            # single delivery_callback + single failed_callback, so the health
            # signal and the Issue #66 first-wins ACK synthesis are folded into
            # one pair of closures. ``ack_msg_id`` is None unless the operator
            # opted into ack_required, in which case the same callback also
            # drives the synthetic ACK back to the originating channel.
            sub_hash = sub.lxmf_hash
            delivered_state = _lxm_delivered_state(LXMF)
            ack_capture = ack_msg_id if self._ack_emit_callback is not None else None
            emit = self._ack_emit_callback

            def on_delivered(lxm_receipt, _hash=sub_hash, _ack=ack_capture,
                             _emit=emit, _delivered=delivered_state):
                # Distinguish a true recipient proof (state DELIVERED) from a
                # propagation-node hand-off (state SENT). Only the former is a
                # confirmation; the latter leaves the subscriber UNCONFIRMED.
                state = getattr(lxm_receipt, "state", None)
                if state == _delivered:
                    self._subs.mark_confirmed(_hash)
                    if _ack and _emit is not None:
                        try:
                            _emit(_ack, "delivered")
                        except Exception as e:
                            logger.debug(
                                "ack emit (delivered) failed for %s: %s",
                                _ack[:16], e,
                            )
                elif _ack:
                    # Propagated only (state SENT) — held as UNCONFIRMED, not
                    # HEALTHY, and the mesh sender must NOT be told
                    # "[delivered]" (2026-07-26 review C1). The ack vocabulary
                    # (_maybe_emit_ack_for_msgid / _format_ack_text) has no
                    # honest hand-off kind, and the loop filter
                    # _SYNTH_ACK_CONTENT_PREFIXES only covers delivered/
                    # timeout/failed, so a novel kind would re-enter the
                    # fan-out. Emit nothing: the pending ack resolves through
                    # the timeout sweep — the #16 "Sent (not guaranteed)"
                    # posture in ACK form.
                    logger.debug(
                        "propagation hand-off (state=%s) for ack %s — no "
                        "synthetic ACK emitted; recipient proof pending",
                        state, _ack[:16],
                    )

            def on_failed(lxm_receipt, _hash=sub_hash, _ack=ack_capture):
                self._subs.mark_failed(_hash, "lxmf_delivery_failed")
                if _ack:
                    logger.debug(
                        "LXMF delivery failed to %s for ack %s",
                        hash_short, _ack[:16],
                    )

            try:
                lxm.register_delivery_callback(on_delivered)
                lxm.register_failed_callback(on_failed)
            except (AttributeError, TypeError):
                logger.debug(
                    "LXMF callbacks not available — fan-out to %s will not "
                    "contribute delivery confirmation or ack synthesis",
                    hash_short,
                )

            assert_rns_tx_allowed(
                kind="lxmf_outbound",
                detail=f"broadcast fan-out -> {hash_short}")
            call_boundary("rnsd.handle_outbound",
                          self._router.handle_outbound, lxm,
                          target=hash_short)
            self._subs.mark_fanout_enqueued(sub.lxmf_hash)
            with self._stats_lock:
                self.stats["fanouts"] += 1
            return True
        except Exception as e:
            logger.debug("Fanout to %s failed: %s", sub.lxmf_hash, e)
            self._subs.mark_failed(
                sub.lxmf_hash, f"outbound_error:{type(e).__name__}"
            )
            with self._stats_lock:
                self.stats["errors"] += 1
            return False

    def _reply(self, source_hex: str, body: str) -> bool:
        sub = Subscriber(lxmf_hash=source_hex, added_at=datetime.utcnow())
        return self._send_to_subscriber(sub, body, "MeshForge")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        with self._stats_lock:
            stats_copy = dict(self.stats)
        subs = self._subs.list_all()
        now = datetime.utcnow()

        # Report the LIVE-derived state, not the stored column, so a subscriber
        # whose row hasn't been mutated since a threshold crossing (e.g. a stored
        # 'healthy' that has since aged into STALE, or a pre-fix row still marked
        # 'healthy' that is really UNCONFIRMED) can never lie in the status
        # surface (honest_failure_modes #5 — one source of truth for state).
        live_states = [SubscriberStore.desired_state(s, now) for s in subs]

        by_state: Dict[str, int] = {}
        for st in live_states:
            by_state[st] = by_state.get(st, 0) + 1
        confirmable = sum(1 for s in subs if s.last_confirmed_at is not None)

        return {
            "running": self._running,
            "destination_hash": self.destination_hash_hex,
            "display_name": self._config.display_name,
            "channels": list(self._config.channels),
            "announce_interval_sec": int(self._config.announce_interval_sec),
            "autosubscribe": bool(self._config.autosubscribe),
            "started_at_iso": (
                self._started_at.isoformat() if self._started_at else None
            ),
            "subscribers": [
                {
                    "lxmf_hash": s.lxmf_hash,
                    "added_at": s.added_at.isoformat() if s.added_at else None,
                    # last enqueue (best-effort hand-off), NOT a confirmed delivery
                    "last_delivery": (
                        s.last_delivery.isoformat() if s.last_delivery else None
                    ),
                    # last REAL recipient proof; None => never confirmed (blind spot)
                    "last_confirmed_at": (
                        s.last_confirmed_at.isoformat()
                        if s.last_confirmed_at else None
                    ),
                    "tier": s.tier,
                    "state": live,
                    "consecutive_failures": s.consecutive_failures,
                    "last_failure_at": (
                        s.last_failure_at.isoformat()
                        if s.last_failure_at else None
                    ),
                }
                for s, live in zip(subs, live_states)
            ],
            "subscriber_count": len(subs),
            # Honest confirmation accounting (mirrors #74's
            # compute_confirmation_view): surface the unconfirmed population as
            # its own visible number rather than folding it into a healthy-looking
            # scalar. ``unconfirmed_subscribers`` is the blind spot made VISIBLE.
            "delivery_confirmation": {
                "confirmable_subscribers": confirmable,
                "unconfirmed_subscribers": len(subs) - confirmable,
                "by_state": by_state,
            },
            "stats": stats_copy,
        }


def create_from_gateway_config(
    gateway_config: GatewayConfig,
    persistent_queue: Any = None,
    ack_emit_callback: Optional[Callable[[str, str], bool]] = None,
) -> Optional[MeshtasticBroadcastBridge]:
    """Convenience factory — returns None if the plug-in is disabled.

    Designed to be called from RNSMeshtasticBridge after the LXMF setup
    has completed. The bridge stands up its own LXMRouter (LXMF 0.9.4
    caps a router at one delivery identity, so it can't share the
    gateway's).

    ``persistent_queue`` + ``ack_emit_callback`` wire Issue #66
    first-caller behavior; both are optional and only consulted when
    ``meshtastic_broadcast.ack_required`` is True.
    """
    cfg = getattr(gateway_config, "meshtastic_broadcast", None)
    if cfg is None or not cfg.enabled:
        return None
    propagation = getattr(
        getattr(gateway_config, "rns", None), "propagation_node", ""
    )
    return MeshtasticBroadcastBridge(
        broadcast_config=cfg,
        propagation_node=propagation,
        persistent_queue=persistent_queue,
        ack_emit_callback=ack_emit_callback,
    )


# ─────────────────────────────────────────────────────────────────────
# Module-level active-bridge accessor — same pattern as
# meshcore_handler.get_active_handler. At most one broadcast bridge is
# active per daemon process; the bridge registers itself on start() and
# clears on stop() so config/status APIs can reach it without import
# cycles.
# ─────────────────────────────────────────────────────────────────────

_active_bridge: Optional["MeshtasticBroadcastBridge"] = None
_active_bridge_lock = threading.Lock()


def _set_active_bridge(bridge: "MeshtasticBroadcastBridge") -> None:
    global _active_bridge
    with _active_bridge_lock:
        _active_bridge = bridge


def _clear_active_bridge(bridge: "MeshtasticBroadcastBridge") -> None:
    """Identity-guarded clear so a stale stop() can't clobber a fresh start."""
    global _active_bridge
    with _active_bridge_lock:
        if _active_bridge is bridge:
            _active_bridge = None


def get_active_broadcast_bridge() -> Optional["MeshtasticBroadcastBridge"]:
    """Return the currently-running MeshtasticBroadcastBridge, or None."""
    with _active_bridge_lock:
        return _active_bridge
