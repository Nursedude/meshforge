"""Kilo K1 — link-matrix observatory: every packet is a channel sounding.

An **edge** is one (receiver ← sender) RF observation harvested from a
packet the receiver's gateway uplinked to the json MQTT topic. Zero new
TX — passive listening only (invariant #1); capture rides the existing
``MQTTNodelessSubscriber`` packet hook inside a bounded collect window
(#73: one hardened client, no new fd surface).

Semantics honesty (the parts that bite if forgotten):
  * Receiver identity is the json topic SUFFIX (the uplinking gateway's
    own node id) — parsed from the topic, never queried from the radio
    (#17). Sender is the packet's ``from`` (the ORIGINATOR; the payload's
    ``sender`` field is the gateway again, not the originator).
  * Meshtastic rx snr/rssi describe the LAST HOP into the receiver.
    ``hops_away == 0`` → the edge truly is (sender → receiver);
    ``hops_away > 0`` → the RF edge is (relayer → receiver) and the
    relayer is known only by its last id byte (``relay_partial``).
    ``hops_away`` ABSENT → unknown, which is never assumed direct.
    The matrix is direct-only by default for exactly this reason.
  * A 0.0 snr is a legitimate reading, never "absent" (None-vs-0
    discipline); an absent snr is stored as NULL and excluded from
    medians but still counts as presence.
  * The receiver hearing its OWN uplinked packets is not an RF sounding
    (no air path was measured) — self-edges are skipped, with a witness
    count.

Drift is judged per edge against the edge's OWN rolling baseline
(median ± MAD band over the retention horizon), not against a Friis
prediction — indoor absolute prediction is folly; the rf.py comparison
is a K1.1+ outdoor/calibrated-pair feature. Tri-state honesty: sparse
data is SPARSE (⚪ unknown), never "fine". Surfacing drift to mini/ntfy
is a deliberately SEPARATE later step — the CLI view ships first,
evidence before alerting.
"""
from __future__ import annotations

import threading
import time
from statistics import median
from typing import Dict, List, Optional, Tuple

from kilo.registry import KiloNode, anchor_map
from monitoring._mqtt_types import VALID_RSSI_RANGE, VALID_SNR_RANGE, \
    node_num_to_id

# ── drift constants (two consumers — classify_drift and the tests — one
#    place). Band = max(DRIFT_BAND_SIGMAS robust-σ, floor): Meshtastic snr
#    is quantized to 0.25 dB, so an ultra-stable edge has MAD ≈ 0 and a
#    raw MAD band would page on quantization noise.
DRIFT_MIN_BASELINE = 20   # samples before a baseline is believable
DRIFT_MIN_RECENT = 5      # samples before the recent window is believable
DRIFT_BAND_FLOOR_DB = 2.0
DRIFT_MAD_SIGMA = 1.4826  # MAD → σ for normally-distributed data
DRIFT_BAND_SIGMAS = 2.0
DRIFT_SHIFTED_BAND_MULT = 2.0  # beyond this × band = SHIFTED (pages once
# the matrix is cron_verdict-wired — named so tuning finds it here, not
# hunting an inline literal)

DEFAULT_WINDOW_S = 24 * 3600.0  # recent window; older-than-this = baseline

DRIFT_GLYPHS = {"OK": "🟢", "DRIFTING": "🟡", "SHIFTED": "🔴",
                "SPARSE": "⚪"}

# parse_edge/EdgeBuffer dispositions — witness-counter vocabulary (closed;
# the buffer counts every packet under exactly one of these; pinned by
# TestDispositionsClosed).
DISPOSITIONS = ("ok", "self", "no_receiver", "no_sender", "unparseable",
                "overflow")

# EdgeBuffer hard cap — a leaked/unstopped callback must not grow without
# bound on the paho thread; drops land in the "overflow" witness counter.
EDGE_BUFFER_MAX_ROWS = 50_000


def parse_edge(topic: str, data: dict, now: Optional[float] = None
               ) -> Tuple[Optional[tuple], str]:
    """One decoded json packet → (edge row, "ok") or (None, why-not).

    The row matches kilo.store.record_edges order: (ts, receiver, sender,
    channel, snr, rssi, hops_away, hop_start, relay_partial, packet_id).
    Identities are lowercased here so grouping/joins never re-fold.
    ``ts`` is receipt time at the collecting box — radio clocks on an
    RTC-less fleet are not trusted for ordering.
    """
    if not isinstance(data, dict):
        return None, "unparseable"
    parts = [p for p in str(topic).split("/") if p]
    if len(parts) < 2 or not parts[-1].startswith("!"):
        return None, "no_receiver"
    receiver = parts[-1].lower()
    channel = parts[-2]  # channel NAME from the topic — the numeric
    # payload "channel" is the box-LOCAL slot index and differs per box

    sender = node_num_to_id(data.get("from"))
    if sender is None:
        return None, "no_sender"
    if sender == receiver:
        return None, "self"

    snr = _bounded_float(data.get("snr"), *VALID_SNR_RANGE)
    rssi = _bounded_float(data.get("rssi"), *VALID_RSSI_RANGE)
    hops_away = _bounded_int(data.get("hops_away"), 0, 15)
    hop_start = _bounded_int(data.get("hop_start"), 0, 15)
    relay = _bounded_int(data.get("relay_node", data.get("relayNode")),
                         1, 255)  # 0 = "not relayed" marker, not a byte
    pid = data.get("id")
    packet_id = str(pid) if isinstance(pid, (int, str)) \
        and not isinstance(pid, bool) else None

    ts = float(now) if now is not None else time.time()
    return (ts, receiver, sender, channel, snr, rssi, hops_away,
            hop_start, relay, packet_id), "ok"


def _bounded_float(value, lo: float, hi: float) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def _bounded_int(value, lo: int, hi: int) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        i = int(value)
    except (TypeError, ValueError):
        return None
    return i if lo <= i <= hi else None


class EdgeBuffer:
    """Thread-safe edge accumulator between the paho network thread and
    the collect window's main loop. The callback must never touch the
    SQLite connection (check_same_thread) and must never raise into the
    decoder — every packet lands in exactly one witness counter."""

    def __init__(self):
        self._lock = threading.Lock()
        self._rows: List[tuple] = []
        self._counts: Dict[str, int] = {}

    def on_packet(self, topic, data) -> None:
        try:
            row, disp = parse_edge(topic, data)
        except Exception:  # a swallow with a witness, never a broken decode
            row, disp = None, "unparseable"
        with self._lock:
            if row is not None and len(self._rows) >= EDGE_BUFFER_MAX_ROWS:
                row, disp = None, "overflow"
            self._counts[disp] = self._counts.get(disp, 0) + 1
            if row is not None:
                self._rows.append(row)

    def drain(self) -> List[tuple]:
        with self._lock:
            rows, self._rows = self._rows, []
        return rows

    def counts(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._counts)


def classify_drift(baseline: List[float], recent: List[float]) -> dict:
    """Pure tri-state drift verdict for one edge's snr samples.

    SPARSE (⚪) — too few samples on either side: UNKNOWN, never "fine".
    OK (🟢) — recent median within the baseline band.
    DRIFTING (🟡) — outside the band but within twice it.
    SHIFTED (🔴) — beyond twice the band: the link changed.
    """
    out = {"state": "SPARSE", "baseline_n": len(baseline),
           "recent_n": len(recent), "baseline_median": None,
           "recent_median": None, "band_db": None, "deviation_db": None}
    if baseline:
        out["baseline_median"] = round(median(baseline), 2)
    if recent:
        out["recent_median"] = round(median(recent), 2)
    if len(baseline) < DRIFT_MIN_BASELINE or len(recent) < DRIFT_MIN_RECENT:
        return out
    bmed = median(baseline)
    mad = median([abs(x - bmed) for x in baseline])
    band = max(DRIFT_BAND_SIGMAS * DRIFT_MAD_SIGMA * mad,
               DRIFT_BAND_FLOOR_DB)
    dev = median(recent) - bmed
    out["band_db"] = round(band, 2)
    out["deviation_db"] = round(dev, 2)
    if abs(dev) <= band:
        out["state"] = "OK"
    elif abs(dev) <= DRIFT_SHIFTED_BAND_MULT * band:
        out["state"] = "DRIFTING"
    else:
        out["state"] = "SHIFTED"
    return out


def build_matrix(conn, nodes: List[KiloNode],
                 window_s: float = DEFAULT_WINDOW_S,
                 now: Optional[float] = None,
                 direct_only: bool = True) -> dict:
    """Receivers × senders link matrix over the edge store.

    Cell = recent-window sample count + median snr + drift verdict vs the
    edge's own baseline (everything older than the window, within the 7d
    retention). Labels join the registry's CURRENT anchors at READ time
    (the K0 live-proof lesson — never trust the ingest-time stamp). A
    pair that was heard in the baseline but is silent in the window still
    gets a cell (n=0, SPARSE) — a vanished edge must not vanish from the
    view. With ``direct_only`` (default) hops_away==0 rows only; unknown
    hops are excluded there too (unknown ≠ direct).
    """
    from kilo.store import EDGE_RETENTION_DAYS, edges_since

    now = time.time() if now is None else now
    retention_s = EDGE_RETENTION_DAYS * 86400.0
    raw = edges_since(conn, now - retention_s)
    anchors = anchor_map(nodes) if nodes else {}
    recent_cut = now - window_s

    totals = {"edges_total": 0, "edges_direct": 0, "edges_relayed": 0,
              "edges_unknown_hops": 0, "edges_no_snr": 0}
    counts: Dict[Tuple[str, str], int] = {}
    recent: Dict[Tuple[str, str], List[float]] = {}
    baseline: Dict[Tuple[str, str], List[float]] = {}
    oldest_ts: Optional[float] = None
    for ts, receiver, sender, snr, hops in raw:
        oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
        totals["edges_total"] += 1
        if hops is None:
            totals["edges_unknown_hops"] += 1
        elif hops == 0:
            totals["edges_direct"] += 1
        else:
            totals["edges_relayed"] += 1
        if snr is None:
            totals["edges_no_snr"] += 1
        if direct_only and hops != 0:
            continue
        pair = (receiver, sender)
        if ts >= recent_cut:
            counts[pair] = counts.get(pair, 0) + 1
            if snr is not None:
                recent.setdefault(pair, []).append(snr)
        elif snr is not None:
            baseline.setdefault(pair, []).append(snr)

    cells = []
    for pair in sorted(set(counts) | set(baseline)):
        receiver, sender = pair
        rec = recent.get(pair, [])
        cells.append({
            "receiver": receiver, "sender": sender,
            "receiver_label": anchors.get(receiver, receiver),
            "sender_label": anchors.get(sender, sender),
            "n": counts.get(pair, 0),
            "median_snr": round(median(rec), 2) if rec else None,
            "drift": classify_drift(baseline.get(pair, []), rec),
        })
    # Baseline-horizon honesty: 100% SPARSE is GUARANTEED while the store
    # is younger than the window (baseline = older-than-window only) or
    # the window swallows the whole retention. That global fact must be a
    # witness in the result, or the operator bug-hunts per-edge "sparse"
    # cells that cannot possibly be anything else.
    if window_s >= retention_s:
        horizon = {"empty_by_construction": True,
                   "why": (f"window {window_s / 3600.0:g}h ≥ retention "
                           f"{retention_s / 3600.0:g}h — baseline can "
                           f"never fill; use --window-hours < "
                           f"{retention_s / 3600.0:g}")}
    elif oldest_ts is None:
        horizon = {"empty_by_construction": True,
                   "why": "no edges stored yet"}
    elif oldest_ts >= recent_cut:
        horizon = {"empty_by_construction": True,
                   "why": (f"store's oldest edge is "
                           f"{(now - oldest_ts) / 3600.0:.1f}h old — "
                           f"younger than the {window_s / 3600.0:g}h "
                           f"window, so no edge predates it")}
    else:
        horizon = {"empty_by_construction": False, "why": ""}
    horizon["oldest_edge_age_s"] = (round(now - oldest_ts, 1)
                                    if oldest_ts is not None else None)

    return {"window_s": window_s, "direct_only": direct_only,
            "receivers": sorted({c["receiver"] for c in cells}),
            "senders": sorted({c["sender"] for c in cells}),
            "cells": cells, "totals": totals,
            "baseline_horizon": horizon}
