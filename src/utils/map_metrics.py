"""Prometheus metrics for the MeshForge map service (Phase D-3).

Exposes a stable metric set on `:5000/metrics` that Phase D-4
(Prometheus deployment on moc1) will scrape and Phase E (Grafana
dashboards) will graph. The metric NAMES are the contract — renaming
breaks every saved dashboard panel — so they're consciously locked
here. Adding new metrics is fine; renaming or repurposing existing
ones is not.

Optional dependency: when `prometheus_client` isn't installed, all
helpers no-op and the /metrics endpoint returns 503 with a clear
message rather than 500. This keeps the map server runnable on
boxes that haven't installed monitoring/.txt deps yet.

Metric naming: `meshforge_map_<surface>_<unit>` (Prometheus
convention). Counters end in `_total`. Gauges describe a moment-in-
time value. Labels are kept small — high-cardinality labels (per-
node, per-channel) explode the time-series count and slow scrapes;
keep them at this layer ("source", "method", "status_class") and
push detail into the existing /api/status if dashboards need it.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from utils.safe_import import safe_import

_prom, _HAS_PROM = safe_import("prometheus_client")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric definitions — NAMES ARE THE CONTRACT. See module docstring.
# ---------------------------------------------------------------------------

if _HAS_PROM:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        CollectorRegistry,
        CONTENT_TYPE_LATEST,
        generate_latest,
    )

    # Use a dedicated registry so we don't pollute the default global
    # one (which prometheus_client populates with python_*, process_*
    # collectors that we may not want exposed to the fleet scraper).
    REGISTRY = CollectorRegistry(auto_describe=True)

    # ----- Service health (already surfaced via /healthz, mirrored here)
    SERVICE_UP = Gauge(
        "meshforge_map_service_up",
        "1 if the map server is past warming and serving live traffic.",
        registry=REGISTRY,
    )
    SERVICE_WARMING_SINCE = Gauge(
        "meshforge_map_service_warming_started_seconds",
        "Unix timestamp when the current warming cycle started. 0 when ready.",
        registry=REGISTRY,
    )

    # ----- Per-collector metrics (drives Phase E "source health" panels)
    COLLECTOR_RUNS = Counter(
        "meshforge_map_collector_runs_total",
        "Total collect() cycles a source has executed (success or error).",
        ["source"],
        registry=REGISTRY,
    )
    COLLECTOR_ERRORS = Counter(
        "meshforge_map_collector_errors_total",
        "Total collect() cycles that ended with reason_if_zero != 'ok'.",
        ["source", "reason"],
        registry=REGISTRY,
    )
    COLLECTOR_NODES_LAST_RUN = Gauge(
        "meshforge_map_collector_nodes_yielded",
        "Nodes returned by the most recent collect() for this source.",
        ["source"],
        registry=REGISTRY,
    )

    # ----- Node inventory (drives Phase E fleet-overview panels)
    NODES_WITH_POSITION = Gauge(
        "meshforge_map_nodes_with_position",
        "Total nodes with live coordinates across all sources (post-dedup).",
        registry=REGISTRY,
    )
    NODES_WITHOUT_POSITION = Gauge(
        "meshforge_map_nodes_without_position",
        "Nodes heard but not placeable (e.g. MeshCore without GPS).",
        ["network"],
        registry=REGISTRY,
    )

    # ----- HTTP-side metrics (drives Phase E request-rate / latency panels)
    HTTP_REQUESTS = Counter(
        "meshforge_map_http_requests_total",
        "HTTP requests served by the map handler.",
        ["method", "endpoint", "status_class"],
        registry=REGISTRY,
    )
    HTTP_LATENCY = Histogram(
        "meshforge_map_http_request_duration_seconds",
        "HTTP request latency (seconds).",
        ["endpoint"],
        # Buckets: very fast (sub-ms cache hits), normal API
        # responses, and slow paths (geojson with 38k features).
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        registry=REGISTRY,
    )

    # ----- DB metrics (drives Phase E "is dedup keeping up" panels)
    DB_OBSERVATIONS_INSERTED = Counter(
        "meshforge_map_db_observations_inserted_total",
        "node_history rows actually written (after Phase 1.5 dedup skip).",
        registry=REGISTRY,
    )
    DB_OBSERVATIONS_SKIPPED = Counter(
        "meshforge_map_db_observations_skipped_total",
        "node_history rows skipped because (lat,lon,network) was unchanged.",
        registry=REGISTRY,
    )

else:
    REGISTRY = None
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    def generate_latest(_registry=None) -> bytes:  # type: ignore[no-redef]
        return b""


# ---------------------------------------------------------------------------
# Helpers — call from collector / handler. All no-op gracefully without dep.
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """True iff prometheus_client imported successfully."""
    return _HAS_PROM


def render() -> tuple[bytes, str]:
    """Return (body, content_type) for /metrics endpoint.

    Empty body when prometheus_client isn't installed — caller
    should respond 503 in that case.
    """
    if not _HAS_PROM:
        return b"", CONTENT_TYPE_LATEST
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def set_warming(started_at: Optional[float]) -> None:
    """Record warming start (None when transitioning to ready)."""
    if not _HAS_PROM:
        return
    if started_at is None:
        SERVICE_UP.set(1)
        SERVICE_WARMING_SINCE.set(0)
    else:
        SERVICE_UP.set(0)
        SERVICE_WARMING_SINCE.set(started_at)


def record_collect(source: str, yielded: int, reason_if_zero: str) -> None:
    """Record one collect() cycle outcome for a source."""
    if not _HAS_PROM:
        return
    COLLECTOR_RUNS.labels(source=source).inc()
    if reason_if_zero != "ok":
        COLLECTOR_ERRORS.labels(source=source, reason=reason_if_zero).inc()
    COLLECTOR_NODES_LAST_RUN.labels(source=source).set(yielded)


def set_node_inventory(
    with_position: int, without_position_by_network: dict
) -> None:
    """Set the nodes_with_position / nodes_without_position gauges.

    Called from the collector after each full collect() so dashboards
    track the post-dedup counts that drive the live map.
    """
    if not _HAS_PROM:
        return
    NODES_WITH_POSITION.set(with_position)
    # Don't reset; new networks accumulate. In practice the network
    # set is small (meshtastic, meshcore, aredn, reticulum, hamclock)
    # and stable, so set() per-label is correct for "current state".
    for network, count in (without_position_by_network or {}).items():
        NODES_WITHOUT_POSITION.labels(network=network).set(count)


def record_http(method: str, endpoint: str, status_code: int, duration_s: float) -> None:
    """Record one HTTP request outcome.

    `endpoint` should be a route template ("/api/status",
    "/api/nodes/geojson"), NOT the raw path with parameters —
    parameter expansion would explode cardinality (one series per
    node id). Caller is responsible for normalizing.
    """
    if not _HAS_PROM:
        return
    status_class = f"{status_code // 100}xx"
    HTTP_REQUESTS.labels(
        method=method, endpoint=endpoint, status_class=status_class
    ).inc()
    HTTP_LATENCY.labels(endpoint=endpoint).observe(duration_s)


def record_db_observation(*, inserted: bool) -> None:
    """Record one node_history insert attempt — inserted vs deduped."""
    if not _HAS_PROM:
        return
    if inserted:
        DB_OBSERVATIONS_INSERTED.inc()
    else:
        DB_OBSERVATIONS_SKIPPED.inc()
