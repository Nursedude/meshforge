"""End-to-end smoke tests for the map domain HTTP service.

Exercises the real `MapServer` HTTP wiring against an in-process
ephemeral instance. These are the foundation for Phase D Prometheus
exporter validation: any future `/metrics` endpoint should be
testable here without standing up rnsd / mosquitto.

Scope:
- HTTP shape contract for the endpoints Prometheus / Grafana will
  scrape (`/api/status`, `/api/nodes/geojson`).
- Issue #43 fields are present (`source_diagnostics`,
  `nodes_without_position`, `radio_config`).
- Issue #44 main-thread RNS init invariant: the server can be
  hammered concurrently without the worker-thread `signal.signal`
  trap firing (validated by sending parallel requests in a thread
  pool — collector lock + prewarm are exercised together).

Out of scope (covered elsewhere):
- Collector content correctness — see
  `tests/test_map_data_collector_diagnostics.py`.
- Migration / WAL behavior — see `tests/test_migrate_map_service.py`.
- HTML rendering — not part of the metric/data contract.
"""

from __future__ import annotations

import concurrent.futures
import json
import urllib.error
import urllib.request


def _get_json(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        assert resp.status == 200, f"{url} returned {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


class TestStatusEndpoint:
    """Contract tests for /api/status — Prometheus's primary scrape target."""

    def test_status_returns_running(self, map_server):
        data = _get_json(f"{map_server.url}/api/status")
        # Top-level keys the dashboard / scraper depend on. If any of
        # these go missing, the dashboard breaks silently — lock the
        # contract here.
        assert data.get("status") == "running"
        assert "time" in data
        assert "history" in data

    def test_status_has_issue43_diagnostic_fields(self, map_server):
        """Issue #43 added per-source diagnostics + radio_config so
        operators can answer 'why is my node missing on box X but
        visible on box Y' without grepping source. The fields must
        be present even when no sources have run yet (empty values
        are fine; missing keys break dashboards)."""
        data = _get_json(f"{map_server.url}/api/status")
        assert "source_diagnostics" in data, (
            "source_diagnostics is the Phase E 'what's healthy' panel "
            "source; it must always be present"
        )
        assert "nodes_without_position" in data, (
            "nodes_without_position drives the 'MeshCore visible without "
            "GPS' Phase E panel; must always be present"
        )
        assert "radio_config" in data, (
            "radio_config exposes local HAT preset + region so cross-box "
            "fleet diffs are possible without SSHing"
        )

    def test_status_radio_config_shape(self, map_server):
        """radio_config is bimodal:
        - meshtasticd reachable → {modem_preset, region, channel_num, ...}
        - unreachable → {available: False, reason: "..."}

        Phase E dashboards must handle both. Lock the contract here so
        future changes don't add a third shape that silently breaks
        the dashboard fallback rendering.
        """
        data = _get_json(f"{map_server.url}/api/status")
        radio = data.get("radio_config")
        assert isinstance(radio, dict), "radio_config must be a dict"
        if radio.get("available") is False:
            # Unreachable shape: must include a reason for operator UX
            assert "reason" in radio, (
                "radio_config={available:False} must include a 'reason' "
                "string so dashboards can surface the cause to operators"
            )
            assert isinstance(radio["reason"], str)
            assert radio["reason"], "reason must be non-empty"
        else:
            # Reachable shape: keys Phase E will diff across fleet boxes
            for required in ("modem_preset", "region", "channel_num"):
                assert required in radio, (
                    f"radio_config (reachable) must include '{required}' "
                    f"— missing keys break per-box diff workflows"
                )

    def test_status_nodes_without_position_shape(self, map_server):
        """nodes_without_position is `{total: int, by_network: {...}}`."""
        data = _get_json(f"{map_server.url}/api/status")
        nwp = data.get("nodes_without_position")
        assert isinstance(nwp, dict)
        assert "total" in nwp
        assert isinstance(nwp["total"], int)
        assert "by_network" in nwp
        assert isinstance(nwp["by_network"], dict)


class TestGeoJSONEndpoint:
    """Contract tests for /api/nodes/geojson — main map data feed."""

    def test_geojson_returns_feature_collection(self, map_server):
        data = _get_json(f"{map_server.url}/api/nodes/geojson")
        assert data.get("type") == "FeatureCollection", (
            "leaflet/openlayers + our map.html depend on the "
            "FeatureCollection wrapper; renaming it would break clients"
        )
        assert "features" in data
        assert isinstance(data["features"], list)

    def test_geojson_features_have_required_properties(self, map_server):
        """If any features come back (depends on local fleet state),
        they must have at minimum `id`, `network`, and a Point geometry.
        Empty list is acceptable when no sources have data."""
        data = _get_json(f"{map_server.url}/api/nodes/geojson")
        for feat in data.get("features", []):
            assert feat.get("type") == "Feature"
            assert "geometry" in feat
            assert feat["geometry"].get("type") == "Point"
            assert "coordinates" in feat["geometry"]
            props = feat.get("properties", {})
            assert "id" in props, "feature missing id"
            assert "network" in props, "feature missing network"


class TestConcurrentRequests:
    """Issue #44 invariant: ThreadingHTTPServer + main-thread RNS init.

    With ThreadingHTTPServer, every request runs on a worker thread.
    If RNS isn't pre-warmed on the main thread, the first cache-miss
    `/api/status` hits `RNS.Reticulum()` from a worker → `signal.signal`
    raises ValueError → collector marks RNS as 'unreachable' and the
    half-initialized singleton corrupts subsequent requests.

    `MapServer.start_background()` calls `_prewarm_collector()` on the
    main thread before binding. This test confirms the prewarm worked
    by hammering with parallel requests; if the invariant were
    violated, source_diagnostics would race-leak 'main thread'
    AuthenticationError reasons across runs.
    """

    def test_parallel_status_requests_dont_corrupt_diagnostics(self, map_server):
        url = f"{map_server.url}/api/status"

        def _hit() -> dict:
            return _get_json(url, timeout=15.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_hit) for _ in range(16)]
            results = [f.result() for f in futures]

        # All requests succeeded
        assert len(results) == 16
        # No request observed an RNS-thread-init error in diagnostics
        for r in results:
            for source, diag in (r.get("source_diagnostics") or {}).items():
                notes = (diag.get("notes") or "").lower()
                assert "main thread" not in notes, (
                    f"source {source} reported a worker-thread RNS init "
                    f"error: {diag} — Issue #44 invariant is violated"
                )

    def test_parallel_geojson_requests_serve_consistent_shape(self, map_server):
        """Concurrent /api/nodes/geojson calls all return valid
        FeatureCollections (the collect-lock contract from Issue #44)."""
        url = f"{map_server.url}/api/nodes/geojson"

        def _hit() -> dict:
            return _get_json(url, timeout=15.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = [f.result() for f in [pool.submit(_hit) for _ in range(8)]]

        for r in results:
            assert r.get("type") == "FeatureCollection"
            assert isinstance(r.get("features"), list)


class TestNotFoundIsSane:
    """Spot-check the request handler doesn't 500 on unknown paths."""

    def test_unknown_api_path_returns_404(self, map_server):
        try:
            urllib.request.urlopen(
                f"{map_server.url}/api/this-does-not-exist", timeout=5
            )
        except urllib.error.HTTPError as e:
            assert e.code == 404
        else:
            raise AssertionError("expected HTTP 404 for unknown path")


class TestHealthz:
    """F3 fix: /healthz is the cold-start-safe endpoint."""

    def test_healthz_returns_200_in_ready_state(self, map_server):
        # The fixture waits for ready; healthz reflects that.
        data = _get_json(f"{map_server.url}/healthz")
        assert data.get("state") == "ready"

    def test_healthz_does_not_require_warming_field(self, map_server):
        """Ready state body shape: just {state: "ready"}. Other keys
        are warming-only (since, elapsed_s) — keep the ready path
        minimal so monitors don't have to handle absent fields."""
        data = _get_json(f"{map_server.url}/healthz")
        assert "state" in data
        # When ready, since/elapsed_s may be absent — that's fine.

    def test_healthz_status_code_is_200_always(self, map_server):
        """`up` metric in Prometheus is `is /healthz 200?`. Must
        return 200 for both warming and ready states; warming sets
        the state in body, never status code. (Ready-state assertion
        here; warming-state covered separately by the harness's
        wait_ready test fixture.)"""
        with urllib.request.urlopen(
            f"{map_server.url}/healthz", timeout=5
        ) as resp:
            assert resp.status == 200


class TestMetricsEndpoint:
    """Phase D-3: /metrics is the Prometheus scrape target.

    Contract:
    - /metrics ALWAYS bypasses the warming gate (Prometheus scrapes
      at startup; we don't want to lose the warming-time data).
    - When prometheus_client is installed: returns 200 + the
      standard text exposition format with the metric names locked
      in `utils/map_metrics.py`.
    - When NOT installed: returns 503 + structured error pointing
      operators at requirements/monitoring.txt.
    """

    def test_metrics_returns_200_when_prom_available(self, map_server):
        from utils import map_metrics
        if not map_metrics.is_available():
            pytest.skip("prometheus_client not installed in this venv")
        with urllib.request.urlopen(
            f"{map_server.url}/metrics", timeout=10
        ) as resp:
            assert resp.status == 200
            content_type = resp.headers.get("Content-Type", "")
            # prometheus_client ships text format 0.0.4 at minimum
            assert "text/plain" in content_type
            body = resp.read().decode("utf-8")
        # Locked metric names — renaming breaks Phase E dashboards.
        for required in (
            "meshforge_map_service_up",
            "meshforge_map_service_warming_started_seconds",
            "meshforge_map_collector_runs_total",
            "meshforge_map_collector_errors_total",
            "meshforge_map_collector_nodes_yielded",
            "meshforge_map_nodes_with_position",
            "meshforge_map_nodes_without_position",
            "meshforge_map_http_requests_total",
            "meshforge_map_http_request_duration_seconds",
        ):
            assert required in body, (
                f"Phase E dashboards depend on `{required}` — "
                f"renaming or removing it breaks saved panels"
            )

    def test_metrics_records_http_request_count(self, map_server):
        """A real /api/status request increments the http_requests
        counter. This proves the do_GET wrapper is wired."""
        from utils import map_metrics
        if not map_metrics.is_available():
            pytest.skip("prometheus_client not installed")
        # Hit /api/status once to ensure at least 1 sample
        urllib.request.urlopen(
            f"{map_server.url}/api/status", timeout=10
        ).read()
        with urllib.request.urlopen(
            f"{map_server.url}/metrics", timeout=10
        ) as resp:
            body = resp.read().decode("utf-8")
        # Look for the api/status request count line
        # Expected line shape:
        #   meshforge_map_http_requests_total{endpoint="/api/status",method="GET",status_class="2xx"} N
        api_status_lines = [
            ln for ln in body.splitlines()
            if "meshforge_map_http_requests_total" in ln
            and 'endpoint="/api/status"' in ln
            and "#" not in ln  # exclude HELP/TYPE lines
        ]
        assert api_status_lines, (
            "expected meshforge_map_http_requests_total{endpoint=/api/status} sample"
        )

    def test_metrics_during_warming_does_not_503(self, tmp_path):
        """/metrics must scrape cleanly even during cold-start warming
        — Prometheus should never see 503 for metric scrapes, only
        for /api/* endpoints. (Otherwise dashboards lose the
        warming-window samples that show recovery rate.)"""
        from tests.e2e.harness.map_server import MapServerHarness
        from utils import map_metrics
        if not map_metrics.is_available():
            pytest.skip("prometheus_client not installed")

        harness = MapServerHarness()
        try:
            harness.start(wait_ready=False)
            # Hit /metrics during warming — must not 503
            with urllib.request.urlopen(
                f"{harness.url}/metrics", timeout=10
            ) as resp:
                assert resp.status == 200
                body = resp.read().decode("utf-8")
            assert "meshforge_map_service_up" in body
        finally:
            harness.stop()


class TestWarmingState:
    """F3 fix: cold-start window returns 503 + state, not connection-refused.

    These tests use a fresh harness that does NOT wait_ready, so we
    catch the warming window deterministically. The first collect
    can be slow on a Pi (10-30s); we only need to observe one 503
    before warming completes.
    """

    def test_warming_returns_503_for_api_status(self, tmp_path):
        from tests.e2e.harness.map_server import MapServerHarness

        harness = MapServerHarness()
        try:
            # Don't wait — we want to catch the warming window.
            harness.start(wait_ready=False)
            # /healthz must be 200 immediately on bind.
            with urllib.request.urlopen(
                f"{harness.url}/healthz", timeout=5
            ) as resp:
                assert resp.status == 200
                healthz_body = json.loads(resp.read().decode())
            # If we caught warming, /api/status returns 503. If
            # warming finished impossibly fast, that's also fine —
            # check both branches.
            try:
                resp = urllib.request.urlopen(
                    f"{harness.url}/api/status", timeout=5
                )
                # Warmup completed before our request — accept ready state.
                assert healthz_body.get("state") in ("warming", "ready")
            except urllib.error.HTTPError as e:
                if e.code == 503:
                    body = json.loads(e.read().decode())
                    assert body.get("state") == "warming"
                    assert body.get("error") == "service_warming"
                    assert "since" in body
                    assert "retry_after_s" in body
                else:
                    raise
        finally:
            harness.stop()

    def test_warming_then_ready_via_wait_ready(self, tmp_path):
        from tests.e2e.harness.map_server import MapServerHarness

        harness = MapServerHarness()
        try:
            harness.start(wait_ready=False)
            # Block until ready (or fail with TimeoutError on a
            # truly-stuck server).
            harness.wait_ready(timeout_s=600.0)
            # Now /api/status works.
            with urllib.request.urlopen(
                f"{harness.url}/api/status", timeout=10
            ) as resp:
                assert resp.status == 200
        finally:
            harness.stop()
