"""RNS→RF ingress policy: WHO may put bytes on our radios through the bridge.

Born 2026-10-06 from the ingress trust-boundary enumeration. Measured on
both live gateways: the router bridged every sender on every network, and
an RNS identity on the regional network that found the gateway's announced
LXMF destination was forwarded onto Meshtastic RF with no sender policy —
the MeshCore Public leak mirror-imaged. The regional RNS network is
membership by configuration (published parameters, a deliberate join), and
that membership was implicit in the code. This module makes it explicit.

Why RNS is the one place a source policy is SOUND: an LXMF source hash is
a signed identity. A Meshtastic node id can be set to anything, so a
filter there is theatre and the channel key is the real policy.

Three postures, declared in ``gateway.json`` ``rns``:

* ``bridge_source_identities`` empty  → **open** (today's behaviour, and
  the router's disclosure line says so).
* list present + ``bridge_source_policy: "observe"`` (the default once a
  list exists) → every unlisted sender is still bridged, but is RECORDED:
  an INFO witness with the full hash and a persisted ledger — observe
  before alarm (operator doctrine 09-30). This is the soak that proves the
  list is complete before anything is refused.
* ``"enforce"`` → an unlisted sender is refused: not queued to the bridge,
  witnessed, counted, ledgered. The oracle keeps its OWN identity policy
  and still runs first, so a stranger may query it if it allows.

Peer gateways (``peer_gateway_destinations``) are always listed — they
relay, and refusing them would partition the hub.

The ledger is the tripwire's published diff: ``rns_ingress_ledger.json``
beside the other gateway state, one entry per unlisted sender with
first/last seen and counts. It is what the fleet page can read. Bounded:
``MAX_SENDERS`` entries, oldest ``last_seen`` evicted. Every write is
atomic and never raises into the ingress path (hfm #9: a swallow leaves a
witness — the ledger IS the witness, so a ledger write failure is logged
at WARNING, once per process).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("gateway.rns_ingress")

POLICY_OPEN = "open"
POLICY_OBSERVE = "observe"
POLICY_ENFORCE = "enforce"
#: The two DECLARABLE policies. ``open`` is derived (empty list), never set.
DECLARABLE_POLICIES = (POLICY_OBSERVE, POLICY_ENFORCE)

VERDICT_LISTED = "listed"
VERDICT_PEER = "peer_gateway"
VERDICT_UNLISTED = "unlisted"

LEDGER_FILENAME = "rns_ingress_ledger.json"
LEDGER_SCHEMA = "rns_ingress_ledger/v1"
MAX_SENDERS = 200


def normalize_hashes(raw: Any) -> List[str]:
    """``str | list`` → lowercase 32-hex list; anything else is dropped.

    A malformed entry is dropped rather than raising: one typo must not
    turn the whole list into "no list" (which would read as OPEN) — but it
    is returned by :func:`malformed_hashes` so validation can name it.
    """
    items: Iterable[Any]
    if isinstance(raw, str):
        items = [raw] if raw else []
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        return []
    out: List[str] = []
    for h in items:
        if isinstance(h, str):
            s = h.strip().lower()
            if len(s) == 32 and all(c in "0123456789abcdef" for c in s):
                if s not in out:
                    out.append(s)
    return out


def malformed_hashes(raw: Any) -> List[str]:
    """Entries of ``raw`` that :func:`normalize_hashes` would drop."""
    items = [raw] if isinstance(raw, str) else (
        list(raw) if isinstance(raw, (list, tuple)) else [])
    good = set(normalize_hashes(raw))
    return [repr(h) for h in items
            if not (isinstance(h, str) and h.strip().lower() in good)]


def effective_policy(identities: List[str], declared: Optional[str]) -> str:
    """The policy actually in force: ``open`` with no list, else the declared
    one, defaulting to ``observe`` when the declaration is absent/unknown."""
    if not identities:
        return POLICY_OPEN
    d = (declared or "").strip().lower()
    return d if d in DECLARABLE_POLICIES else POLICY_OBSERVE


def verdict(source_hex: str, identities: List[str],
            peer_gateways: Iterable[str]) -> str:
    """Classify one sender. Pure; the caller decides what the verdict does."""
    s = (source_hex or "").strip().lower()
    if s in set(identities):
        return VERDICT_LISTED
    if s in {p.strip().lower() for p in peer_gateways if isinstance(p, str)}:
        return VERDICT_PEER
    return VERDICT_UNLISTED


class IngressLedger:
    """Persisted record of unlisted senders — the tripwire's diff."""

    def __init__(self, path: Path, *, max_senders: int = MAX_SENDERS,
                 now_fn=time.time):
        self.path = Path(path)
        self._max = max_senders
        self._now = now_fn
        self._lock = threading.Lock()
        self._write_failed_logged = False
        self._doc: Dict[str, Any] = self._load()

    # ── persistence ──────────────────────────────────────────────────
    def _load(self) -> Dict[str, Any]:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("senders"), dict):
                return doc
        except (OSError, ValueError):
            pass
        return {"schema": LEDGER_SCHEMA, "senders": {}}

    def _write(self) -> None:
        try:
            from utils.paths import atomic_write_text
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps(self._doc, separators=(",", ":")))
        except Exception as exc:  # noqa: BLE001 — never into the ingress path
            if not self._write_failed_logged:
                self._write_failed_logged = True
                logger.warning("rns ingress ledger unwritable at %s: %s — "
                               "unlisted senders are being witnessed in the "
                               "journal only", self.path, exc)

    # ── recording ────────────────────────────────────────────────────
    def record(self, source_hex: str, *, policy: str, refused: bool,
               label: str = "") -> Dict[str, Any]:
        """Note one unlisted-sender event; returns that sender's entry."""
        now = float(self._now())
        s = (source_hex or "").lower()
        with self._lock:
            senders: Dict[str, Any] = self._doc.setdefault("senders", {})
            ent = senders.get(s)
            if ent is None:
                ent = {"first_seen": now, "last_seen": now, "seen": 0,
                       "refused": 0, "label": label or s[:4]}
                senders[s] = ent
            ent["last_seen"] = now
            ent["seen"] = int(ent.get("seen", 0)) + 1
            if refused:
                ent["refused"] = int(ent.get("refused", 0)) + 1
            ent["last_policy"] = policy
            if len(senders) > self._max:
                oldest = sorted(senders.items(),
                                key=lambda kv: kv[1].get("last_seen", 0))
                for k, _ in oldest[:len(senders) - self._max]:
                    senders.pop(k, None)
            self._doc["policy"] = policy
            self._doc["updated_at"] = now
            self._write()
            return dict(ent)

    def stamp(self, *, policy: str, listed: int) -> None:
        """Record the posture even when nothing was refused, so a reader can
        tell "enforcing, nobody unlisted" from "never ran"."""
        with self._lock:
            self._doc["policy"] = policy
            self._doc["listed"] = int(listed)
            self._doc["updated_at"] = float(self._now())
            self._write()

    # ── reading ──────────────────────────────────────────────────────
    def snapshot(self, *, window_s: float = 86400.0) -> Dict[str, Any]:
        """What a status page needs: posture + the unlisted senders seen in
        ``window_s`` (newest first) + lifetime totals."""
        with self._lock:
            doc = json.loads(json.dumps(self._doc))
        return project_ledger_doc(doc, now=float(self._now()), window_s=window_s)


def project_ledger_doc(doc: Any, *, now: float,
                       window_s: float = 86400.0) -> Dict[str, Any]:
    """The ONE projection of a raw ledger document that every surface
    reads — the gateway's ``get_status()``, the map's ``/fleet/slo`` and the
    truth spool's raw-file leg all land on the same shape (hfm #5: two
    consumers of one artifact share one constant). A malformed document
    projects as empty, never as a crash."""
    senders = doc.get("senders") if isinstance(doc, dict) else None
    if not isinstance(senders, dict):
        senders = {}
    policy = doc.get("policy") if isinstance(doc, dict) else None
    listed = doc.get("listed") if isinstance(doc, dict) else None
    updated = doc.get("updated_at") if isinstance(doc, dict) else None
    recent: List[Tuple[str, Dict[str, Any]]] = []
    for k, v in senders.items():
        if not isinstance(v, dict):
            continue
        try:
            age = now - float(v.get("last_seen", 0))
        except (TypeError, ValueError):
            continue
        if age <= window_s:
            recent.append((k, v))
    recent.sort(key=lambda kv: kv[1].get("last_seen", 0), reverse=True)
    return {
        "schema": LEDGER_SCHEMA,
        "policy": policy if isinstance(policy, str) else None,
        "listed": listed if isinstance(listed, int) and not isinstance(listed, bool) else None,
        "updated_at": updated if isinstance(updated, (int, float)) else None,
        "window_s": window_s,
        "unlisted_recent": [
            {"hash": k, "label": v.get("label", str(k)[:4]),
             "seen": v.get("seen", 0), "refused": v.get("refused", 0),
             "last_seen": v.get("last_seen")}
            for k, v in recent],
        "unlisted_total": len([v for v in senders.values() if isinstance(v, dict)]),
        "refused_total": sum(int(v.get("refused", 0) or 0) for v in senders.values()
                             if isinstance(v, dict)),
    }


def read_ledger_projection(path: Optional[Path] = None, *,
                           now: Optional[float] = None,
                           window_s: float = 86400.0) -> Optional[Dict[str, Any]]:
    """Read + project the ledger file on THIS box; ``None`` when there is no
    ledger (no gateway here, or one that has never stamped). Unreadable or
    malformed → ``None`` too, so a surface can only ever say "absent", never
    invent a posture. Never raises."""
    try:
        p = Path(path) if path is not None else default_ledger_path()
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return None
        return project_ledger_doc(doc, now=float(now if now is not None else time.time()),
                                  window_s=window_s)
    except Exception:  # noqa: BLE001 — absent/unreadable is "no ledger"
        return None


def default_ledger_path() -> Path:
    """``$XDG_STATE_HOME/meshforge/<LEDGER_FILENAME>`` under the REAL user's
    home (sudo-safe resolution, MF001)."""
    import os
    from utils.paths import get_real_user_home
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else get_real_user_home() / ".local" / "state"
    return root / "meshforge" / LEDGER_FILENAME
