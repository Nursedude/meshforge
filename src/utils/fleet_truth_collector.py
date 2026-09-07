"""HTTP fan-out + TTL cache that feeds the honest fleet-truth SSOT.

The NOC box self-aggregates: it fetches ``/fleet/slo`` + ``/api/status`` from
every host in ``fleet_hosts`` (names-first via ``fleet_naming``, so DHCP-reshuffle
drift shows up as ``resolution_method``), plus its own via localhost. A peer that
times out (~3s) becomes a DARK box in the truth schema — never a dropped row.
Results are TTL-cached (~12s) so the human dashboard's 5s poll and an incoming
Claude session's orientation coalesce onto ONE fan-out.

Deliberately NOT polling MeshAnchor's ``/fleet/rollup``: that would couple this
domain's truth to a different domain's daemon (a dead MA would paint the whole MF
fleet dark). ``/fleet/slo`` + ``/api/status`` are the cross-box contract both
domains already serve identically.

Kept thin + pure-adjacent: the shaping logic lives in ``fleet_truth`` (unit-
tested without HTTP); this module only does I/O + caching.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_PORT = 5000
PEER_TIMEOUT_S = 3.0     # MA-proven /fleet/slo budget on Pi-class hardware
CACHE_TTL_S = 12.0       # < the human 5s poll would over-fetch; 12s coalesces
MAX_WORKERS = 16
# Bounded read (2026-07-19 adversarial review): a byte-dripping or huge-body
# peer must not hang the fan-out (the lock is held across it) nor buffer
# unbounded bytes. Both caps are far above any legitimate truth-fan-out body.
MAX_BODY_BYTES = 8 * 1024 * 1024
READ_DEADLINE_S = 10.0

_IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

# ── ssh-spool fallback (2026-07-19) ─────────────────────────────────────
# Some fleet boxes are unreachable to the HTTP fan-out BY NETWORK DESIGN
# (kiai: behind the site NAT, ssh rides a reverse tunnel; moc3: no map
# service at all). scripts/fleet_truth_spool.py — an OPERATOR-context cron,
# where the fleet ssh keys live (the /fleet/dups precedent: the sandboxed
# map service never sshes) — ssh-fetches those boxes' localhost endpoints
# (+ raw watchdog.json for map-less boxes) into this spool. The collector
# reads it ONLY when the direct fetch failed, and only while FRESH — a
# stale spool reads dark, never last-known-healthy.
SPOOL_SCHEMA = "truth_spool/v1"
SPOOL_STALE_S = 360.0  # 3x the */2min cron cadence


def truth_spool_dir() -> "Path":
    """``$XDG_STATE_HOME/meshforge/truth_spool`` (operator-owned) — shared
    constant between the cron writer and this reader."""
    from pathlib import Path
    from utils.paths import get_real_user_home
    import os as _os
    xdg = _os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else get_real_user_home() / ".local" / "state"
    return base / "meshforge" / "truth_spool"


def _read_spool(alias: str, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Fresh spool doc for ``alias``, or None (absent / unreadable / stale /
    wrong schema). Never raises."""
    try:
        path = truth_spool_dir() / f"{alias}.json"
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict) or doc.get("schema") != SPOOL_SCHEMA:
            return None
        fetched_at = doc.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return None
        wall_now = now if now is not None else time.time()
        age = wall_now - fetched_at
        # Clamp the backward branch too (honest_failure_modes #6, RTC-less
        # fleet): a writer stamp AHEAD of this reader's clock is not evidence
        # of freshness — it is a clock fault, and must read stale, never fresh.
        if age < 0 or age > SPOOL_STALE_S:
            return None  # stale spool = unobservable, never last-known-healthy
        return doc
    except (OSError, ValueError):
        return None


def _mask_ip_alias(alias: str) -> str:
    """An IP-shaped fleet_hosts entry must never surface as the box alias
    (MF014/MF015 — the schema carries names, never addresses). Deterministic
    non-reversible label so the operator can still correlate entries."""
    return "ip-entry-" + hashlib.sha1(alias.encode()).hexdigest()[:6]


def _read_bounded(resp, max_bytes: int, deadline_s: float) -> bytes:
    """Read a response with a total-wall deadline and a size cap. The socket
    timeout bounds each recv; this bounds the WHOLE read (drip-feed peers)."""
    deadline = time.monotonic() + deadline_s
    chunks: List[bytes] = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("bounded read deadline exceeded")
        chunk = resp.read(65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("response exceeds size cap")
        chunks.append(chunk)


def _http_get_json(url: str, timeout: float) -> Optional[Dict[str, Any]]:
    """GET a JSON body. Returns the parsed dict, or None on any failure
    (unreachable / timeout / garbage HTTP / non-JSON / oversize / drip) —
    the caller treats None as DARK. http.client.HTTPException is in the net
    (2026-07-19 review): a peer answering garbage HTTP must degrade to a
    dark box via this normal path, never escape to the worker-error path."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (LAN)
            body = _read_bounded(r, MAX_BODY_BYTES, READ_DEADLINE_S)
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, http.client.HTTPException, OSError,
            ValueError, TimeoutError) as e:
        logger.debug("fleet_truth fetch failed %s: %s", url, e)
        return None


def _resolve_peer(alias: str) -> "tuple[str, str]":
    """Return (target_host, resolution_method) for a peer alias, names-first.
    Never raises; falls back to the bare alias. NEVER surfaces the raw IP to
    callers that put it in the schema — only the method + alias leave here."""
    try:
        from utils import fleet_naming
        reg = fleet_naming.load_registry_quiet()
        res = fleet_naming.resolve(alias, reg)
        # res.target is the connectable host (may be a name or, on ip_fallback,
        # an IP). We use it only to build the fetch URL; the schema carries the
        # METHOD, never the address (MF014/MF015).
        target = res.target or alias
        return target, (res.method or "bare")
    except Exception as e:  # fleet_naming optional / registry absent
        logger.debug("peer resolve fallback for %s: %s", alias, e)
        return alias, "bare"


def _fetch_peer(alias: str, *, is_self: bool, port: int) -> Dict[str, Any]:
    """Fetch one box's /fleet/slo + /api/status. Returns a snapshot dict shaped
    for ``fleet_truth.build_box_truth``."""
    if is_self:
        host, method = "localhost", "self"
    else:
        host, method = _resolve_peer(alias)
    # MF014/MF015: an IP-shaped hosts entry must not become the served alias.
    display = _mask_ip_alias(alias) if _IPV4_RE.match(alias) else alias
    if _IPV4_RE.match(alias):
        method = "ip_literal"
    base = f"http://{host}:{port}"
    slo = _http_get_json(f"{base}/fleet/slo", PEER_TIMEOUT_S)
    status = _http_get_json(f"{base}/api/status", PEER_TIMEOUT_S)
    error = None
    answered_at: Optional[float] = time.time() if (slo or status) else None
    # None = undecided. A box that ANSWERED over HTTP obviously has the
    # surface, so this only ever matters for the spool path below.
    http_surface_expected: Optional[bool] = None
    # Same tri-state contract as http_surface_expected: True / False /
    # None-if-undecidable, and None must NEVER become False.
    watchdog_expected: Optional[bool] = None
    spool_services: Optional[Dict[str, Any]] = None
    spool_schedules: Optional[Dict[str, Any]] = None

    if slo is None and status is None:
        # Direct fan-out failed — try the ssh spool (fresh-only).
        spool = _read_spool(alias)
        if spool is not None:
            status = spool.get("status") if isinstance(spool.get("status"), dict) else None
            slo = spool.get("slo") if isinstance(spool.get("slo"), dict) else None
            raw_wd = spool.get("raw_watchdog")
            raw_mini = spool.get("raw_mini")
            radio_probe = spool.get("radio_probe")
            if isinstance(spool.get("services"), dict):
                spool_services = spool["services"]
            spool_schedules = judge_spooled_schedules(
                alias, spool.get("schedules"))
            # Decided by the spool writer (it holds the role catalog). True /
            # False / None-if-undecidable; None must NEVER become False —
            # "not expected" is what stops a dark box tainting the verdict.
            hse = spool.get("http_surface_expected")
            if isinstance(hse, bool):
                http_surface_expected = hse
            wde = spool.get("watchdog_expected")
            if isinstance(wde, bool):
                watchdog_expected = wde
            if status is None and (isinstance(raw_wd, dict) or isinstance(raw_mini, dict)):
                # Map-less box (moc3): synthesize the blocks we DO have from the
                # raw non-HTTP reads, each through the SAME transform + staleness
                # threshold the status handler uses (shared SSOT — a stale raw
                # file reads dark, never green-with-old-numbers).
                from utils._map_status_endpoints import (
                    mini_block_from_payload, watchdog_block_from_payload)
                status = {}
                if isinstance(raw_wd, dict):
                    status["watchdog"] = watchdog_block_from_payload(raw_wd)
                if isinstance(raw_mini, dict):
                    status["mini_dudeai"] = mini_block_from_payload(raw_mini)
                # Carry the box's OWN role declaration through in the same
                # shape an HTTP box reports it (/api/status.app.role), so the
                # builder needs no map-less special case. Only ever set from
                # the box's real file — never inferred.
                deploy = spool.get("deployment")
                if isinstance(deploy, dict) and isinstance(deploy.get("role"), str):
                    status["app"] = {"name": "meshforge", "role": deploy["role"]}
            if slo is None and isinstance(radio_probe, dict):
                # Radio from the SAME non-perturbing probe the map uses (LISTEN
                # state / usb — never a #17-contending :4403 connect).
                from utils._map_status_endpoints import radio_connection_from_probe
                connected, mode = radio_connection_from_probe(
                    bool(radio_probe.get("tcp_listening")),
                    bool(radio_probe.get("usb_present")))
                slo = {"radio": {"connected": connected, "mode": mode}}
            if slo is not None or status is not None:
                method = "ssh_spool"
                # Honest observation age: when the spool answered, age runs
                # from the spool FETCH, not from now.
                answered_at = float(spool["fetched_at"])
            else:
                error = (f"no response from {display} ({method}); "
                         "ssh spool fresh but empty-handed")
        else:
            error = f"no response from {display} ({method})"

    return {
        "alias": display,
        "resolution_method": method,
        "status": status,
        "slo": slo,
        "error": error,
        "answered_at": answered_at,
        "http_surface_expected": http_surface_expected,
        "watchdog_expected": watchdog_expected,
        "spool_services": spool_services,
        "spool_schedules": spool_schedules,
    }


def judge_spooled_schedules(alias: str, section: "Optional[Dict[str, Any]]",
                           *, now: "Optional[float]" = None,
                           ) -> "Optional[Dict[str, Any]]":
    """Judge a map-less peer's spooled crontab + cron verdicts (2026-09-07).

    WHY THIS EXISTS. lehua is a `field-node`: no map (so ``fleet_snapshot``
    never publishes its verdicts) and no watchdog (so
    ``probe_cron_verdict_stale`` — the ONLY emitter of the signal class mini's
    rule matches — never runs there). Its hourly self-healing hosts-block cron
    wrote an honest verdict into a file that NOTHING read: a producing half
    shipped without its consuming half (honest_failure_modes #4).

    The judging is the box's OWN probe, run here on its spooled text, with a
    per-alias debounce file and a capture sink so a peer's verdict can never
    overwrite this box's disposition record. One predicate, two callers.

    Returns None when there is nothing to say (no section spooled).
    """
    if not isinstance(section, dict):
        return None
    import base64

    def _dec(key: str) -> "Optional[str]":
        """b64 -> text. Empty or undecodable -> None = UNOBSERVABLE.

        Deliberately not "": an absent file and an empty one encode
        identically here, so claiming "the log is empty" would be a guess.
        None makes the probe treat verdicts as unreadable, which fails dark.
        """
        v = section.get(key)
        if not isinstance(v, str) or not v:
            return None
        try:
            return base64.b64decode(v.encode("ascii"), validate=True).decode(
                "utf-8", "replace")
        except Exception:
            return None

    crontab_text = _dec("crontab_b64")
    if crontab_text is None:
        return {"state": "dark", "reason": "peer crontab unobservable"}

    sink: list = []
    try:
        from utils.watchdog_probes_liveness import probe_cron_verdict_stale
        sig = probe_cron_verdict_stale(
            crontab_text=crontab_text,
            verdicts_text=_dec("verdicts_b64"),
            now=now,
            state_path=str(truth_spool_dir() / f"cron_debounce.{alias}.json"),
            disposition_sink=sink,
        )
    except Exception as exc:                      # never break the fan-out
        return {"state": "dark", "reason": f"peer cron judge failed: {exc}"}

    # The probe returns None for BOTH inert and clean; the sink is what tells
    # them apart. Worst-wins, matching note_disposition's own contract.
    disps = [d for _c, d, _r in sink]
    reason = next((r for _c, d, r in sink if d not in ("clean",) and r), None)
    if sig is not None:
        return {"state": "failed",
                "reason": getattr(sig, "detail", None) or "cron verdict unhealthy"}
    if "indeterminate" in disps:
        return {"state": "dark", "reason": reason or "cron verdicts unobservable"}
    if "inert" in disps:
        return {"state": "inert",
                "reason": reason or "no crons wired to cron_verdict.sh"}
    if "clean" in disps:
        return {"state": "healthy", "reason": "wired crons fresh and passing"}
    return {"state": "dark", "reason": "cron judge returned no disposition"}


def collect_snapshots(*, port: int = DEFAULT_PORT) -> "tuple[List[Dict[str, Any]], int]":
    """Fan out to self + every fleet_hosts peer in parallel. Returns
    ``(snapshots, hosts_declared)``. hosts_declared includes self."""
    from mini_dudeai.rollup import resolve_fleet_hosts
    self_host = socket.gethostname()
    peers = [h for h in resolve_fleet_hosts() if h and h != self_host]
    declared = len(peers) + 1  # + self

    jobs = [(self_host, True)] + [(p, False) for p in peers]
    snapshots: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs))) as ex:
        futs = {ex.submit(_fetch_peer, alias, is_self=is_self, port=port): alias
                for alias, is_self in jobs}
        for fut in as_completed(futs):
            try:
                snapshots.append(fut.result())
            except Exception as e:  # a fan-out worker must never sink the whole build
                alias = futs[fut]
                logger.warning("fleet_truth fetch worker for %s raised: %s", alias, e)
                # Same MF014/MF015 masking as the happy path — the worker-error
                # snapshot is served too (2026-07-21 review).
                display = _mask_ip_alias(alias) if _IPV4_RE.match(alias) else alias
                snapshots.append({"alias": display, "resolution_method": "error",
                                  "status": None, "slo": None,
                                  "error": f"fetch worker error: {e}", "answered_at": None})
    _stamp_declared_posture(snapshots)
    return snapshots, declared


def _stamp_declared_posture(snapshots: List[Dict[str, Any]]) -> None:
    """Attach the operator's declared posture (utils.fleet_posture, read ONCE
    per fan-out) to the snapshots of boxes declared dormant/detached, so the
    truth builder can render them as a fourth state instead of an alarm.
    Absent file = nothing stamped = today's document. A broken file is logged
    and stamps nothing (a broken declaration must never silence a box)."""
    try:
        from utils import fleet_posture as fp
        p = fp.read_posture()
        if p.status in (fp.UNREADABLE, fp.INVALID):
            logger.warning("fleet_posture %s: %s — treating every box as active",
                           p.status, p.detail)
            return
        if p.status != fp.DECLARED:
            return
        for snap in snapshots:
            b = p.boxes.get(str(snap.get("alias")))
            if b is not None and b.silent:
                snap["posture"] = {"state": b.state, "note": b.note}
    except Exception as e:  # never sink a fan-out over a posture read
        logger.warning("fleet_posture read failed: %s — treating every box as active", e)


# ── TTL-cached singleton ────────────────────────────────────────────────
_lock = threading.Lock()
_cache: Dict[str, Any] = {"truth": None, "built_at": 0.0}


def get_fleet_truth(*, port: int = DEFAULT_PORT, ttl_s: float = CACHE_TTL_S,
                    force: bool = False) -> Dict[str, Any]:
    """Return the current fleet-truth document, refreshing via fan-out when the
    TTL has expired. Thread-safe single-flight: concurrent callers within the
    TTL share one fan-out. Never raises — a build error yields a self-describing
    dark document rather than a 500."""
    # TTL runs on the MONOTONIC clock (2026-07-19 review): this fleet's
    # RTC-less Pis take NTP steps, and a wall-clock TTL would serve a frozen
    # doc as fresh for the whole backstep (honest_failure_modes #6).
    now_mono = time.monotonic()
    with _lock:
        cached = _cache["truth"]
        if not force and cached is not None and (now_mono - _cache["built_at"]) < ttl_s:
            return cached
        try:
            from utils.fleet_truth import build_fleet_truth
            from utils.watchdog_probe_core import SIGNAL_CLASSES
            snapshots, declared = collect_snapshots(port=port)
            truth = build_fleet_truth(
                snapshots, now=time.time(),
                signal_classes=list(SIGNAL_CLASSES),
                noc_host=socket.gethostname(),
                hosts_declared=declared, ttl_s=ttl_s,
            )
            # Membership transparency (2026-07-19 adversarial review): an
            # absent/empty fleet_hosts list is DEFINED as standalone (the
            # membership wizard's contract), but a 1-box "fleet" reading
            # healthy is only honest if the doc SAYS it's a fleet of one —
            # a lost hosts file is indistinguishable from a declared
            # standalone, so surface the mode instead of hiding it.
            truth["fanout"]["membership"] = "fleet" if declared > 1 else "standalone"
        except Exception as e:
            logger.error("fleet_truth build failed: %s", e)
            truth = {
                "schema": "fleet_truth/v1", "generated_at": time.time(),
                "noc_host": socket.gethostname(),
                "fleet_state": "dark",
                "fanout": {"hosts_declared": None, "hosts_answered": 0,
                           "ttl_s": ttl_s, "stale": True, "error": str(e)},
                "counts": {"healthy": 0, "failed": 0, "dark": 0},
                "boxes": [], "structural_dark": [],
            }
        _cache["truth"] = truth
        _cache["built_at"] = time.monotonic()
        return truth
