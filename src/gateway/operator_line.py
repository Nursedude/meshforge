"""Operator line — an LXMF address for the box's operators (2026-10-13).

Any LXMF app (Sideband, Columba, MeshChatX, MeshHub) can message this
address. A listed, signature-verified operator gets the read-only mesh-oracle
answer (``status``, ``whatsup``, ``wd``, ``node <id>``, ``link <a> <b>``,
``help``); everyone else gets SILENCE. The line also PAGES operators when the
watchdog's degraded/wedge set changes — over RNS, so the page still arrives
when the WAN (and ntfy with it) is gone.

Security model (the feature):
  * closed by default: ``operators`` empty → nobody is answered or paged;
  * a CLAIMED source hash is not a sender: ``signature_validated is True``
    is required (sibling of meshtastic_broadcast_bridge's 2026-10-07 fix);
  * strangers are never replied to — an auto-reply to anyone who heard our
    announce is an airtime amplifier — but every drop is counted;
  * NO path onto RF. Putting text on the radios stays with the bridge and its
    RNS→RF ingress policy: one door, one rule (honest_failure_modes #5).

Like the broadcast bridge it runs its OWN ``LXMF.LXMRouter``: LXMF caps a
router at one delivery identity, so it cannot share the gateway's. Command
work runs on a worker thread so the router's delivery callback never blocks
on the snapshot read or a path request.

Persistence: ``~/.config/meshforge/operator_line_identity`` and
``~/.config/meshforge/operator_line_storage/``.
"""
from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.boundary_timing import call_boundary
from utils.paths import get_real_user_home
from utils.safe_import import safe_import
from utils.tx_guard import assert_rns_tx_allowed

from .operator_line_config import OperatorLineConfig

_RNS_mod, _HAS_RNS = safe_import("RNS")
_LXMF_mod, _HAS_LXMF = safe_import("LXMF")

logger = logging.getLogger(__name__)

#: Same cap as the oracle's reply (== one Meshtastic payload; fits LoRa LXMF).
from oracle import MAX_REPLY_BYTES  # noqa: E402

_PAGING = ("degraded", "wedge")
_WORK_STOP = object()
#: One path request per operator per window when its message reads "source unknown".
_NUDGE_S = 300.0
#: LXMF's unverified_reason for "the sender's identity was never heard here".
_SOURCE_UNKNOWN = 0x01


# ── alerts: pure ──────────────────────────────────────────────────────────

class AlertTracker:
    """Turns successive watchdog snapshots into page lines.

    An unobservable watchdog (absent, unreadable, stale) is BLIND: it pages
    once and HOLDS the last-known set, so a dead observer can never produce a
    CLEARED page (honest_failure_modes #2).
    """

    def __init__(self) -> None:
        self.active: Dict[str, str] = {}
        self.blind: bool = False
        self._baselined = False

    @staticmethod
    def _visible(snap) -> bool:
        return bool(snap.wd_installed) and not snap.wd_stale and snap.wd_reason is None

    @staticmethod
    def _current(snap) -> Dict[str, str]:
        cur: Dict[str, str] = {}
        for sig in snap.wd_signals or []:
            sev = sig.get("severity")
            if sev in _PAGING:
                key = f"{sig.get('class')}({sig.get('subject')})"
                if cur.get(key) != "wedge":
                    cur[key] = sev
        return cur

    def observe(self, snap) -> List[str]:
        if not self._visible(snap):
            if self.blind:
                return []
            self.blind = True
            return [f"BLIND watchdog: {snap.wd_reason or 'not readable'}"]
        lines: List[str] = []
        if self.blind:
            self.blind = False
            lines.append("VISIBLE watchdog readable again")
        cur = self._current(snap)
        if not self._baselined:
            self._baselined = True
            self.active = cur
            if cur:
                lines.append("ACTIVE at start: " + ", ".join(f"{k} {v}" for k, v in sorted(cur.items())))
            return lines
        for key, sev in sorted(cur.items()):
            was = self.active.get(key)
            if was is None:
                lines.append(f"ALERT {sev} {key}")
            elif was != sev:
                lines.append(f"ALERT {sev} {key} (was {was})")
        for key in sorted(set(self.active) - set(cur)):
            lines.append(f"CLEARED {key}")
        self.active = cur
        return lines


def compose_page(box: Optional[str], lines: List[str]) -> str:
    """One page, ≤ MAX_REPLY_BYTES: '<box>: l1; l2; … +N more'."""
    head = f"{box}: " if box else ""
    out = head
    for i, line in enumerate(lines):
        rest = len(lines) - i - 1
        piece = (line if out == head else "; " + line)
        tail = f" +{rest} more" if rest else ""
        if len((out + piece + tail).encode()) > MAX_REPLY_BYTES:
            more = f" +{len(lines) - i} more"
            return (out + more) if out != head else (head + line)[: MAX_REPLY_BYTES - len(more)] + more
        out += piece
    return out


# ── the line ──────────────────────────────────────────────────────────────

def _default_snapshot():
    from oracle import fetch_api_status, read_snapshot
    return read_snapshot(status=fetch_api_status())


class OperatorLine:
    """Lifecycle: ``start()`` (router, announce, worker, alert poller) → ``stop()``."""

    def __init__(
        self,
        cfg: OperatorLineConfig,
        *,
        snapshot_fn: Optional[Callable[[], Any]] = None,
        deliver_fn: Optional[Callable[[str, str], bool]] = None,
        now_fn: Callable[[], float] = time.monotonic,
        rns_module: Any = None,
        lxmf_module: Any = None,
        identity_path: Optional[Path] = None,
        storage_path: Optional[Path] = None,
    ) -> None:
        self._cfg = cfg
        self._ops = set(cfg.operators)
        self._snapshot_fn = snapshot_fn or _default_snapshot
        self._deliver = deliver_fn or self._deliver_lxmf
        self._now = now_fn
        self._rns = rns_module if rns_module is not None else _RNS_mod
        self._lxmf = lxmf_module if lxmf_module is not None else _LXMF_mod
        config_dir = get_real_user_home() / ".config" / "meshforge"
        self._identity_path = identity_path or (
            Path(cfg.identity_file) if cfg.identity_file else config_dir / "operator_line_identity")
        self._storage_path = storage_path or config_dir / "operator_line_storage"
        self._display_name = cfg.display_name or f"MeshForge Operator ({socket.gethostname()})"

        self._router = None
        self._lxmf_source = None
        self._destination_hash: Optional[bytes] = None
        self._running = False
        self._stop_event = threading.Event()
        self._threads: List[threading.Thread] = []
        self._work: "queue.Queue[Any]" = queue.Queue(maxsize=64)

        self._lock = threading.Lock()
        self._last_reply: Dict[str, float] = {}
        self._alerts = AlertTracker()
        self._pending: List[str] = []
        self._last_page: Optional[float] = None
        self._nudged: Dict[str, float] = {}
        self._warmup: Dict[str, int] = {}
        self.stats: Dict[str, int] = {k: 0 for k in (
            "answered", "strangers", "unverified", "cooled", "queue_full",
            "pages", "deliver_failed", "nudged", "errors")}

    # -- properties ---------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def destination_hash_hex(self) -> str:
        return self._destination_hash.hex() if self._destination_hash else ""

    def _count(self, key: str) -> None:
        with self._lock:
            self.stats[key] = self.stats.get(key, 0) + 1

    # -- the decision: who gets what ---------------------------------------
    def respond(self, source_hex: str, text: str, *, signature_validated: Any) -> Optional[str]:
        """Return the reply for one inbound message, or None for silence."""
        if signature_validated is not True:
            self._count("unverified")
            return None
        src = (source_hex or "").lower()
        if src not in self._ops:
            self._count("strangers")
            return None
        now = self._now()
        with self._lock:
            last = self._last_reply.get(src)
            if last is not None and now - last < self._cfg.cooldown_s:
                self.stats["cooled"] += 1
                return None
            self._last_reply[src] = now
        from oracle import answer
        query = (text or "").strip() or "help"
        reply = answer(query, self._snapshot_fn())
        self._count("answered")
        return reply

    # -- alerts ---------------------------------------------------------------
    def alert_tick(self) -> None:
        """One poll: observe the watchdog, queue lines, page when allowed."""
        if not self._cfg.alerts_enabled or not self._ops:
            return
        try:
            snap = self._snapshot_fn()
        except Exception as e:  # the observer itself failed: blind, counted
            self._count("errors")
            logger.warning("operator line: snapshot failed: %s", e)
            return
        self._pending.extend(self._alerts.observe(snap))
        if not self._pending:
            return
        now = self._now()
        if self._last_page is not None and now - self._last_page < self._cfg.alert_min_interval_s:
            return
        page = compose_page(getattr(snap, "box", None), self._pending)
        self._pending = []
        self._last_page = now
        self._count("pages")
        for op in sorted(self._ops):
            if not self._deliver(op, page):
                self._count("deliver_failed")
                logger.warning("operator line: page to %s… not handed off", op[:8])

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> bool:
        if self._running:
            return True
        if self._rns is None or self._lxmf is None:
            logger.error("Operator line: RNS/LXMF not installed")
            return False
        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
            identity = self._load_or_create_identity()
            from gateway._rns_bridge_connection import quarantine_corrupt_ratchets
            from ._signal_guard import suppress_signal_off_main
            quarantine_corrupt_ratchets(self._storage_path)
            with suppress_signal_off_main():
                self._router = self._lxmf.LXMRouter(storagepath=str(self._storage_path))
            self._router.register_delivery_callback(self._on_lxmf_delivery)
            self._lxmf_source = self._router.register_delivery_identity(
                identity, display_name=self._display_name)
            self._destination_hash = getattr(self._lxmf_source, "hash", None)
            if self._lxmf_source is None or self._destination_hash is None:
                logger.error("Operator line: register_delivery_identity failed "
                             "(router state corrupt? wipe %s)", self._storage_path)
                self._router = None
                return False
            logger.info("Operator line registered: %s (%s), %d operator(s)%s",
                        self._display_name, self._destination_hash.hex(), len(self._ops),
                        " — CLOSED, answers nobody" if not self._ops else "")
            self._announce()
            self._running = True
            self._stop_event.clear()
            self._spawn("OperatorLine-Work", self._work_loop)
            self._spawn("OperatorLine-Warmup", self.warmup)
            if self._cfg.announce_interval_sec > 0:
                self._spawn("OperatorLine-Announce", self._announce_loop)
            if self._cfg.alerts_enabled and self._ops:
                self._spawn("OperatorLine-Alerts", self._alert_loop)
            _set_active(self)
            return True
        except Exception as e:
            logger.error("Operator line failed to start: %s", e)
            self._count("errors")
            return False

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        try:
            self._work.put_nowait(_WORK_STOP)
        except queue.Full:
            pass
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=3)
        self._threads = []
        self._router = None
        self._lxmf_source = None
        self._destination_hash = None
        _clear_active(self)

    def _spawn(self, name: str, target: Callable[[], None]) -> None:
        t = threading.Thread(target=target, name=name, daemon=True)
        # prune finished threads (nudges) so a months-long uptime can't accumulate them
        self._threads = [x for x in self._threads if x.is_alive()]
        self._threads.append(t)
        t.start()

    def _load_or_create_identity(self):
        path = self._identity_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                return self._rns.Identity.from_file(str(path))
            except Exception as e:
                logger.warning("Operator line identity %s unreadable (%s) — recreating", path, e)
        identity = self._rns.Identity()
        identity.to_file(str(path))
        return identity

    def _announce(self) -> None:
        if self._destination_hash is None or self._router is None:
            return
        try:
            assert_rns_tx_allowed(kind="rns_announce", detail="operator_line._announce")
            self._router.announce(self._destination_hash)
        except Exception as e:
            logger.debug("Operator line announce failed (will retry): %s", e)

    def _announce_loop(self) -> None:
        while self._running:
            if self._stop_event.wait(max(60, int(self._cfg.announce_interval_sec))):
                return
            self._announce()

    # -- identity warm-up (mirrors the gateway's 2026-10-08 ingress fix) ------
    # LXMF validates a sender with Identity.recall IN THIS PROCESS, which only
    # knows announces it heard while running. Measured on the real stack
    # (2026-10-13): an operator who announced before the line started read
    # "source unknown" and got silence. A path request to the local rnsd is
    # answered from its table and teaches this process the identity.

    def _recallable(self, h: str) -> bool:
        try:
            # _no_use=True: what LXMF asks; a plain recall is an RPC to rnsd (#72)
            return self._rns.Identity.recall(bytes.fromhex(h), _no_use=True) is not None
        except Exception as e:  # noqa: BLE001
            logger.debug("operator line recall %s: %s", h[:8], e)
            return False

    def _request_path(self, h: str) -> None:
        from gateway.bounded_rpc import bounded_call
        assert_rns_tx_allowed(kind="rns_path_request", detail="operator_line identity warm-up")
        bounded_call("rnsd.request_path", self._rns.Transport.request_path, bytes.fromhex(h),
                     target=h[:8], timeout_s=5.0, exit_on_wedge=False)

    def warmup(self, settle_s: float = 5.0) -> Dict[str, int]:
        """Request paths for operators this process cannot recall, then
        RE-MEASURE — "request sent" is not "identity known"."""
        ops = sorted(self._ops)
        missing = [h for h in ops if not self._recallable(h)]
        requested = failed = 0
        for h in missing:
            try:
                self._request_path(h)
                requested += 1
            except Exception as e:  # noqa: BLE001 — counted, never raised
                failed += 1
                logger.debug("operator line warm-up %s: %s", h[:8], e)
        if requested and settle_s > 0:
            self._stop_event.wait(settle_s)
        still = [h for h in missing if not self._recallable(h)] if requested else missing
        out = {"operators": len(ops), "known": len(ops) - len(missing),
               "requested": requested, "after": len(ops) - len(still), "failed": failed}
        self._warmup = out
        if ops:
            logger.info("Operator line warm-up: %d/%d operators recallable before, %d/%d after"
                        "%s", out["known"], len(ops), out["after"], len(ops),
                        f"; still unknown: {','.join(h[:8] for h in still)}" if still else "")
        return out

    def _nudge(self, source_hex: str) -> None:
        """A listed operator's message read "source unknown": one path request
        per window, off the delivery thread, so their NEXT message validates."""
        now = self._now()
        with self._lock:
            last = self._nudged.get(source_hex)
            if last is not None and now - last < _NUDGE_S:
                return
            self._nudged[source_hex] = now
            self.stats["nudged"] += 1

        def run() -> None:
            try:
                self._request_path(source_hex)
            except Exception as e:  # noqa: BLE001
                logger.debug("operator line nudge %s: %s", source_hex[:8], e)
        self._spawn("OperatorLine-Nudge", run)

    def _alert_loop(self) -> None:
        while self._running:
            try:
                self.alert_tick()
            except Exception as e:
                self._count("errors")
                logger.warning("operator line: alert tick failed: %s", e)
            if self._stop_event.wait(self._cfg.alert_poll_s):
                return

    # -- inbound ------------------------------------------------------------
    def _on_lxmf_delivery(self, lxmf_message: Any) -> None:
        """Router callback: verify cheaply, hand the rest to the worker."""
        if not self._running:
            return
        try:
            src = getattr(lxmf_message, "source_hash", b"")
            source_hex = src.hex() if isinstance(src, bytes) else str(src)
            raw = getattr(lxmf_message, "content", b"") or b""
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            validated = getattr(lxmf_message, "signature_validated", None)
            if validated is not True:
                self._count("unverified")
                reason = getattr(lxmf_message, "unverified_reason", None)
                if source_hex.lower() in self._ops and reason == _SOURCE_UNKNOWN:
                    self._nudge(source_hex.lower())
                logger.info("Operator line IGNORED unverified sender %s… (%s)", source_hex[:8],
                            {0x01: "source unknown", 0x02: "signature invalid"}.get(
                                getattr(lxmf_message, "unverified_reason", None), "not validated"))
                return
            try:
                self._work.put_nowait((source_hex, text))
            except queue.Full:
                self._count("queue_full")
        except Exception as e:
            self._count("errors")
            logger.error("Operator line inbound error: %s", e)

    def _work_loop(self) -> None:
        while self._running:
            try:
                item = self._work.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is _WORK_STOP:
                return
            source_hex, text = item
            try:
                reply = self.respond(source_hex, text, signature_validated=True)
                if reply is not None and not self._deliver(source_hex, reply):
                    self._count("deliver_failed")
            except Exception as e:
                self._count("errors")
                logger.warning("Operator line: answering %s… failed: %s", source_hex[:8], e)

    # -- outbound -------------------------------------------------------------
    def _deliver_lxmf(self, dest_hex: str, body: str) -> bool:
        """Hand one LXMF message from the line's identity to its router."""
        if self._router is None or self._lxmf_source is None:
            return False
        RNS, LXMF = self._rns, self._lxmf
        try:
            dest = bytes.fromhex(dest_hex)
            assert_rns_tx_allowed(kind="lxmf_outbound", detail="operator_line._deliver_lxmf")
            if not call_boundary("rnsd.has_path", RNS.Transport.has_path, dest, target=dest_hex[:8]):
                call_boundary("rnsd.request_path", RNS.Transport.request_path, dest, target=dest_hex[:8])
                for _ in range(30):
                    if RNS.Transport.has_path(dest) or self._stop_event.wait(0.1):
                        break
            ident = call_boundary("rnsd.identity_recall", RNS.Identity.recall, dest, target=dest_hex[:8])
            if ident is None:
                logger.info("Operator line: no identity for %s… yet (has it announced?)", dest_hex[:8])
                return False
            destination = RNS.Destination(ident, RNS.Destination.OUT, RNS.Destination.SINGLE,
                                          "lxmf", "delivery")
            self._router.handle_outbound(LXMF.LXMessage(destination, self._lxmf_source, body, "MeshForge"))
            return True
        except Exception as e:
            logger.warning("Operator line: send to %s… failed: %s", dest_hex[:8], e)
            return False

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            stats = dict(self.stats)
        return {
            "running": self._running, "destination": self.destination_hash_hex,
            "display_name": self._display_name, "operators": len(self._ops),
            "closed": not self._ops, "alerts_enabled": self._cfg.alerts_enabled,
            "watchdog_blind": self._alerts.blind, "active_alerts": len(self._alerts.active),
            "pending_page_lines": len(self._pending), "config_errors": list(self._cfg.errors),
            "warmup": dict(self._warmup),
            "stats": stats,
        }


# ── factory + accessor ────────────────────────────────────────────────────

def create_from_gateway_config(gateway_config: Any) -> Optional[OperatorLine]:
    """None unless ``operator_line.enabled is True`` (a MagicMock never starts it)."""
    cfg = getattr(gateway_config, "operator_line", None)
    if not isinstance(cfg, OperatorLineConfig) or cfg.enabled is not True:
        return None
    return OperatorLine(cfg)


_active: Optional[OperatorLine] = None
_active_lock = threading.Lock()


def _set_active(line: OperatorLine) -> None:
    global _active
    with _active_lock:
        _active = line


def _clear_active(line: OperatorLine) -> None:
    global _active
    with _active_lock:
        if _active is line:
            _active = None


def get_active_operator_line() -> Optional[OperatorLine]:
    with _active_lock:
        return _active
