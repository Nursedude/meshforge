"""Tests for the /fleet honest-NOC visual (Phase 3 of the fleet-truth arc).

The page (web/fleet.html) is a faithful PROJECTION of /api/fleet/truth — same
bytes both consumers read. These tests pin the projection's honesty contract
statically (the render "money test" pins, minus a live browser):

- the route serves the page with security headers;
- the TRUTH sections read ONLY /api/fleet/truth; the NOC-local visibility
  section (logging/diag/soak, 2026-07-19) reads a fixed, test-pinned
  allowlist of local diagnostics endpoints and never feeds the verdict;
- the state mapping is DEFAULT-DARK — an unknown/new state string can never
  render green;
- no raw IP anywhere in the page source (MF014/MF015);
- the page is self-contained (no external scripts/styles);
- the legend names DARK as a first-class state;
- the logs panel's unit dropdown carries every server-allowlisted unit
  (reader/writer drift pin, honest_failure_modes #5).
"""

import re
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.map_http_handler import MapRequestHandler

FLEET_HTML = Path(__file__).parent.parent / "web" / "fleet.html"


@pytest.fixture(scope="module")
def page_source() -> str:
    assert FLEET_HTML.exists(), "web/fleet.html missing"
    return FLEET_HTML.read_text(encoding="utf-8")


def _make_handler() -> MapRequestHandler:
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {}
    h.wfile = BytesIO()
    h.send_response = MagicMock()
    h.end_headers = MagicMock()
    sent_headers: list = []
    h.send_header = lambda k, v: sent_headers.append((k, v))
    h._sent_headers = sent_headers
    h.web_dir = None  # fall back to repo web/ next to src/
    return h


class TestFleetPageRoute:
    def test_serve_fleet_page_serves_html(self):
        h = _make_handler()
        h._serve_fleet_page()
        h.send_response.assert_called_once_with(200)
        body = h.wfile.getvalue()
        assert b"Fleet Truth" in body
        assert b"/api/fleet/truth" in body

    def test_serve_fleet_page_sends_security_headers(self):
        h = _make_handler()
        h._serve_fleet_page()
        names = [k for k, _ in h._sent_headers]
        assert "Content-Security-Policy" in names
        assert "X-Content-Type-Options" in names
        assert "Cache-Control" in names

    def test_dispatch_routes_fleet_to_page(self):
        h = _make_handler()
        h.path = "/fleet"
        h.is_warming = False
        h._serve_fleet_page = MagicMock()
        h._dispatch_get("/fleet")
        h._serve_fleet_page.assert_called_once()

    def test_endpoint_label_covers_fleet(self):
        assert MapRequestHandler._endpoint_label("/fleet") == "/fleet"


class TestFleetPageHonestyContract:
    # The page's entire fetch surface: the truth SSOT + the three NOC-local
    # diagnostics endpoints (logging/diag/soak). Anything else is a contract
    # break — the truth sections must keep reading the truth document alone.
    ALLOWED_FETCH_TARGETS = {"TRUTH_URL", "SLO_URL", "SOAK_URL", "logsUrl"}
    ALLOWED_PATH_LITERALS = {
        "/api/fleet/truth", "/fleet/slo", "/lab/synth-rollup", "/fleet/logs",
    }

    def test_fetch_surface_is_allowlisted(self, page_source):
        """Every fetch() targets a named, allowlisted URL constant/helper."""
        fetches = re.findall(r"fetch\(\s*([A-Za-z_]\w*)", page_source)
        assert fetches, "page must fetch the truth endpoint"
        assert set(fetches) <= self.ALLOWED_FETCH_TARGETS, (
            f"unexpected fetch target(s): {set(fetches) - self.ALLOWED_FETCH_TARGETS}"
        )
        assert "TRUTH_URL" in fetches, "the truth endpoint must still be read"

    def test_endpoint_path_literals_are_allowlisted(self, page_source):
        """Belt for the fetch pin: the only absolute-path string literals in
        the page are the allowlisted endpoints — no side-channel URLs."""
        literals = set(re.findall(r"'(/[A-Za-z0-9_./-]+)'", page_source))
        assert literals == self.ALLOWED_PATH_LITERALS, (
            f"path-literal drift: {literals ^ self.ALLOWED_PATH_LITERALS}"
        )

    def test_local_panels_never_feed_the_verdict(self, page_source):
        """renderVerdict (the fleet verdict) is called only from renderAll on
        a truth fetch — the local pollLocal() path must not touch it."""
        m = re.search(r"async function pollLocal\(\)\s*\{(.*?)\n    \}",
                      page_source, re.DOTALL)
        assert m, "pollLocal missing"
        body = m.group(1)
        for forbidden in ("renderVerdict", "renderAll", "renderBoxes",
                          "renderMatrix", "fleet_state"):
            assert not re.search(rf"\b{forbidden}\b", body), (
                f"pollLocal must not touch truth rendering ({forbidden})"
            )

    def test_state_mapping_is_default_dark(self, page_source):
        """stClass: only exact 'healthy'/'failed' escape dark. An unknown or
        undefined state falls through to 'dark' — never green."""
        m = re.search(
            r"function stClass\(s\)\s*\{(.*?)\}", page_source, re.DOTALL)
        assert m, "stClass function missing"
        body = m.group(1)
        assert "if (s === 'healthy') return 'healthy';" in body
        assert "if (s === 'failed') return 'failed';" in body
        # The fall-through — the load-bearing line.
        assert re.search(r"return 'dark';\s*$", body.strip()), (
            "stClass must END by returning 'dark' for everything else"
        )
        # No other return paths exist that could leak a green class.
        returns = re.findall(r"return\s+'(\w+)'", body)
        assert returns == ["healthy", "failed", "dark"]

    def test_no_raw_ip_in_source(self, page_source):
        """MF014/MF015 money test: no dotted-quad anywhere in the page."""
        hits = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", page_source)
        assert hits == [], f"raw IP-like literals in fleet.html: {hits}"

    def test_self_contained_no_external_resources(self, page_source):
        assert "<script src=" not in page_source
        assert "<link " not in page_source
        assert "https://" not in page_source and "http://" not in page_source

    def test_legend_names_dark_as_first_class(self, page_source):
        assert "DARK" in page_source
        assert "no data never reads green" in page_source

    def test_fetch_failure_renders_dark_not_held_green(self, page_source):
        """The page itself obeys honest_failure_modes #2: a truth-endpoint
        fetch failure flips the verdict DARK with the failure named."""
        assert "renderFetchFailure" in page_source
        m = re.search(r"function renderFetchFailure\([^)]*\)\s*\{(.*?)\n    \}",
                      page_source, re.DOTALL)
        assert m, "renderFetchFailure missing"
        assert "'state dark'" in m.group(1)
        assert "unreachable" in m.group(1)


class TestFleetPageLocalPanels:
    """The NOC-local visibility section (logging/diag/soak — the 1.0 Fleet
    Monitor features, restored 2026-07-19)."""

    def test_panels_present(self, page_source):
        for panel_id in ("verdicts", "schedules", "soak", "logs",
                         "log-unit", "log-priority", "log-refresh"):
            assert f'id="{panel_id}"' in page_source, f"missing panel: {panel_id}"

    def test_section_is_labeled_local(self, page_source):
        """The section must declare itself LOCAL so an operator can't read a
        one-box journal tail as a fleet-wide claim."""
        assert "NOC-LOCAL VISIBILITY" in page_source
        assert "not the fleet-truth document" in page_source

    def test_panel_failures_render_dark_not_held(self, page_source):
        """Every local fetch path routes its failure into a visible in-panel
        error — no silent catch, no held reading."""
        assert "panelErr" in page_source
        m = re.search(r"function panelErr\([^)]*\)\s*\{(.*?)\n    \}",
                      page_source, re.DOTALL)
        assert m, "panelErr missing"
        assert "unreachable" in m.group(1)

    def test_verdict_pill_mapping_is_default_dark(self, page_source):
        """vstatusClass: only exact OK (non-stale) is green; OK-but-stale is
        CONCERN; unknown statuses fall through to dark."""
        m = re.search(r"function vstatusClass\([^)]*\)\s*\{(.*?)\n    \}",
                      page_source, re.DOTALL)
        assert m, "vstatusClass missing"
        body = m.group(1)
        assert "stale ? 'concern' : 'ok'" in body
        assert re.search(r"return 'dark';\s*$", body.strip()), (
            "vstatusClass must END by returning 'dark'"
        )

    def test_log_unit_dropdown_covers_server_allowlist(self, page_source):
        """Reader/writer drift pin: every unit the server allowlists must be
        offered by the page, so the panel can't silently lose a unit when the
        server side grows (honest_failure_modes #5)."""
        server_units = MapRequestHandler._FLEET_LOG_UNITS
        assert server_units, "server allowlist empty?"
        for unit in server_units:
            assert f"['{unit}'" in page_source, (
                f"log-unit dropdown missing server-allowlisted unit {unit!r}"
            )
