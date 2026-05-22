"""Tests for MapRequestHandler — focused on T1.2 gzip behavior.

The handler subclasses SimpleHTTPRequestHandler whose __init__ wants a real
socket. We bypass that by constructing instances via __new__ and stubbing
the minimum surface area each test exercises (`headers`, `wfile`, the
status/header sinks). This keeps the gzip path testable without standing
up a real HTTP server.
"""

from io import BytesIO
from unittest.mock import MagicMock

import gzip
import json
import threading

import pytest

from utils.map_http_handler import MapRequestHandler


def _make_handler(accept_encoding: str = "") -> MapRequestHandler:
    """Build a MapRequestHandler with just enough state to call _serve_json."""
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {"Accept-Encoding": accept_encoding} if accept_encoding else {}
    h.wfile = BytesIO()
    h.send_response = MagicMock()
    h.end_headers = MagicMock()
    sent_headers: list = []
    h.send_header = lambda k, v: sent_headers.append((k, v))
    h._sent_headers = sent_headers
    return h


class TestClientAcceptsGzip:
    @pytest.mark.parametrize("header,expected", [
        ("", False),
        ("identity", False),
        ("gzip", True),
        ("gzip, deflate, br", True),
        ("deflate, gzip", True),
        ("gzip;q=0.5", True),
        ("gzip;q=0", False),
        ("identity;q=1, gzip;q=0", False),
    ])
    def test_header_parsing(self, header, expected):
        h = _make_handler(header)
        assert h._client_accepts_gzip() is expected


class TestServeJsonGzip:
    """Server gzips when client accepts AND payload exceeds threshold."""

    def test_small_payload_not_gzipped_even_when_accepted(self):
        # 100 features * ~50 bytes each is well under the 10 KB threshold.
        h = _make_handler("gzip")
        h._serve_json({"type": "FeatureCollection", "features": []})
        body = h.wfile.getvalue()
        # Must be raw JSON, not gzip magic bytes (0x1f 0x8b).
        assert not body.startswith(b"\x1f\x8b")
        encoding_headers = [v for k, v in h._sent_headers if k == "Content-Encoding"]
        assert encoding_headers == [], "small payload was gzipped"

    def test_large_payload_gzipped_when_client_accepts(self):
        # Build payload comfortably above the 10 KB threshold.
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("gzip")
        h._serve_json(payload)
        body = h.wfile.getvalue()
        assert body.startswith(b"\x1f\x8b"), "expected gzip magic bytes"
        # Round-trip: gunzip → json must equal original payload.
        assert json.loads(gzip.decompress(body)) == payload
        encoding_headers = [v for k, v in h._sent_headers if k == "Content-Encoding"]
        assert encoding_headers == ["gzip"]
        # Content-Length must equal the gzipped length, not the raw length.
        cl_headers = [int(v) for k, v in h._sent_headers if k == "Content-Length"]
        assert cl_headers == [len(body)]

    def test_large_payload_not_gzipped_without_accept_encoding(self):
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("")  # no Accept-Encoding header
        h._serve_json(payload)
        body = h.wfile.getvalue()
        assert not body.startswith(b"\x1f\x8b")
        # Plain JSON round-trips.
        assert json.loads(body) == payload
        encoding_headers = [v for k, v in h._sent_headers if k == "Content-Encoding"]
        assert encoding_headers == []

    def test_large_payload_not_gzipped_when_gzip_explicitly_disabled(self):
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("identity, gzip;q=0")
        h._serve_json(payload)
        body = h.wfile.getvalue()
        assert not body.startswith(b"\x1f\x8b")

    def test_vary_accept_encoding_always_sent(self):
        # Vary: Accept-Encoding must be advertised on EVERY response so
        # caches/CDNs key correctly, regardless of whether THIS response
        # was gzipped.
        h = _make_handler("")
        h._serve_json({"small": "payload"})
        vary_headers = [v for k, v in h._sent_headers if k == "Vary"]
        assert "Accept-Encoding" in vary_headers


class TestServeJsonSizeObserverIssue64:
    """size_observer callback wires the HTTP serializer's actual byte
    counts to the size-budget alarm. Without this, /api/status.directory
    would never see the size measurement and the alarm could never fire.
    """

    def test_observer_called_with_raw_bytes_when_not_gzipped(self):
        observed: list = []
        h = _make_handler("")  # client didn't accept gzip
        h._serve_json(
            {"k": "v"},
            size_observer=lambda raw, comp: observed.append((raw, comp)),
        )
        assert len(observed) == 1
        raw, comp = observed[0]
        assert raw > 0
        assert comp is None, (
            "Compressed bytes must be None when response wasn't gzipped — "
            "operators distinguish 'no gzip negotiated' from 'gzipped to 0 bytes'"
        )

    def test_observer_called_with_compressed_bytes_when_gzipped(self):
        observed: list = []
        # Large payload + gzip-accepting client → response is gzipped.
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("gzip")
        h._serve_json(
            payload,
            size_observer=lambda raw, comp: observed.append((raw, comp)),
        )
        assert len(observed) == 1
        raw, comp = observed[0]
        assert raw > comp > 0, (
            f"Expected raw > compressed > 0, got raw={raw} comp={comp}"
        )

    def test_observer_exception_does_not_break_request(self, caplog):
        """observability mustn't break the request — a broken observer
        must be swallowed and logged."""
        def explode(raw, comp):
            raise RuntimeError("observer is broken")

        h = _make_handler("")
        h._serve_json({"k": "v"}, size_observer=explode)
        # If observer broke the request, h.send_response wouldn't have
        # been called. It's a MagicMock — assert it was reached.
        h.send_response.assert_called_once()

    def test_no_observer_is_default(self):
        """Backward compat: callers that don't pass size_observer get
        the original behavior with no overhead and no callback."""
        h = _make_handler("")
        # Just doesn't crash — the default signature path.
        h._serve_json({"k": "v"})


# ── F8: Server-side View preset filter ─────────────────────────────────
from utils.map_http_handler import (
    VIEW_PRESETS,
    _apply_view_preset,
    _apply_view_preset_to_position_less,
    _feature_numeric_timestamp,
)


def _feat(origin="local_radio", federated=False, last_heard=None,
          last_seen=None, network="meshtastic", node_id="!abc"):
    """Build a minimal GeoJSON Feature for preset-filter tests.

    Mirrors the shape `_make_feature` produces in the live geojson path
    plus the federated branch in `_merge_federation`. Tests pass either
    `last_heard` (numeric, live shape) or `last_seen` (numeric epoch,
    directory shape) — preset filter accepts either.
    """
    props = {
        "id": node_id,
        "network": network,
        "source_origin": origin,
        "federated": federated,
    }
    if last_heard is not None:
        props["last_heard"] = last_heard
    if last_seen is not None:
        props["last_seen"] = last_seen
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-157.8, 21.3]},
        "properties": props,
    }


class TestFeatureNumericTimestamp:
    """Picks the right field across live + directory + federated shapes."""

    def test_prefers_last_heard_over_last_seen(self):
        # Live geojson features: last_seen is a string ("5m ago"),
        # last_heard is the numeric epoch. Filter must use last_heard.
        ts = _feature_numeric_timestamp({
            "last_heard": 1_700_000_000.0,
            "last_seen": "5m ago",
        })
        assert ts == 1_700_000_000.0

    def test_falls_back_to_last_seen_when_numeric(self):
        # Directory snapshot features: only last_seen, numeric epoch.
        ts = _feature_numeric_timestamp({"last_seen": 1_700_000_000.0})
        assert ts == 1_700_000_000.0

    def test_returns_none_for_string_last_seen_with_no_last_heard(self):
        # Federated features with peer-pushed string: no usable timestamp.
        assert _feature_numeric_timestamp({"last_seen": "now"}) is None

    def test_returns_none_for_zero_or_missing(self):
        assert _feature_numeric_timestamp({"last_heard": 0}) is None
        assert _feature_numeric_timestamp({}) is None


class TestApplyViewPreset:
    """Server-side View preset filter — pure function, no I/O."""

    NOW = 2_000_000_000.0  # fixed `now` so tests are deterministic

    def test_pass_through_for_none_preset(self):
        feats = [_feat()]
        assert _apply_view_preset(feats, None) is feats

    def test_pass_through_for_unknown_preset(self):
        feats = [_feat()]
        assert _apply_view_preset(feats, "no-such-preset") is feats

    def test_pass_through_for_custom(self):
        feats = [_feat(origin="meshcore_public")]
        assert _apply_view_preset(feats, "custom", now=self.NOW) == feats

    def test_pass_through_for_fleet_union(self):
        feats = [_feat(origin="meshcore_public", federated=True)]
        assert _apply_view_preset(feats, "fleet_union", now=self.NOW) == feats

    def test_pass_through_for_all_gps(self):
        feats = [_feat(origin="meshcore_public", federated=True)]
        assert _apply_view_preset(feats, "all_gps", now=self.NOW) == feats

    def test_live_rf_keeps_local_radio_within_5min(self):
        keep = _feat(origin="local_radio", last_heard=self.NOW - 60)
        out = _apply_view_preset([keep], "live_rf", now=self.NOW)
        assert out == [keep]

    def test_live_rf_drops_other_origins(self):
        # MQTT_local + meshcore_public are both not local_radio.
        feats = [
            _feat(origin="mqtt_local", last_heard=self.NOW - 10),
            _feat(origin="meshcore_public", last_heard=self.NOW - 10),
        ]
        assert _apply_view_preset(feats, "live_rf", now=self.NOW) == []

    def test_live_rf_drops_federated(self):
        # Even local_radio source must be dropped if federated=True.
        feats = [_feat(origin="local_radio", federated=True,
                       last_heard=self.NOW - 60)]
        assert _apply_view_preset(feats, "live_rf", now=self.NOW) == []

    def test_live_rf_drops_stale(self):
        feats = [_feat(origin="local_radio", last_heard=self.NOW - 600)]
        assert _apply_view_preset(feats, "live_rf", now=self.NOW) == []

    def test_live_rf_drops_features_without_numeric_timestamp(self):
        # Conservative: if we can't verify freshness, drop. Prevents
        # federated-string-last_seen from sneaking through the age gate.
        feats = [_feat(origin="local_radio")]  # no last_heard, no last_seen
        assert _apply_view_preset(feats, "live_rf", now=self.NOW) == []

    def test_live_rf_mqtt_accepts_both_origins(self):
        feats = [
            _feat(origin="local_radio", last_heard=self.NOW - 60),
            _feat(origin="mqtt_local", last_heard=self.NOW - 60),
        ]
        assert _apply_view_preset(feats, "live_rf_mqtt", now=self.NOW) == feats

    def test_live_rf_mqtt_drops_external(self):
        feats = [_feat(origin="meshcore_public", last_heard=self.NOW - 60)]
        assert _apply_view_preset(feats, "live_rf_mqtt", now=self.NOW) == []

    def test_live_rf_mqtt_15min_window(self):
        # Within 15min: keep. Beyond: drop.
        keep = _feat(origin="local_radio", last_heard=self.NOW - 800)
        drop = _feat(origin="local_radio", last_heard=self.NOW - 1000)
        out = _apply_view_preset([keep, drop], "live_rf_mqtt", now=self.NOW)
        assert out == [keep]

    def test_external_only_keeps_meshcore_aredn_public(self):
        feats = [
            _feat(origin="meshcore_public"),
            _feat(origin="aredn_worldmap"),
            _feat(origin="public_fallback"),
            _feat(origin="mqtt_global"),
        ]
        assert _apply_view_preset(feats, "external_only", now=self.NOW) == feats

    def test_external_only_drops_local_origins(self):
        feats = [
            _feat(origin="local_radio"),
            _feat(origin="rns_path_table"),
            _feat(origin="mqtt_local"),
        ]
        assert _apply_view_preset(feats, "external_only", now=self.NOW) == []

    def test_local_only_drops_federated(self):
        feats = [
            _feat(origin="local_radio", federated=False),
            _feat(origin="meshcore_public", federated=True),
        ]
        out = _apply_view_preset(feats, "local_only", now=self.NOW)
        assert len(out) == 1
        assert out[0]["properties"]["federated"] is False

    def test_local_only_keeps_external_when_not_federated(self):
        # external_only and local_only are different axes — local_only
        # is "this box's own data, no peers", which includes public
        # aggregator data this box pulled itself.
        feats = [_feat(origin="meshcore_public", federated=False)]
        assert _apply_view_preset(feats, "local_only", now=self.NOW) == feats

    def test_uses_last_seen_as_fallback_for_directory_shape(self):
        # Directory snapshot features have last_seen (numeric epoch) but
        # no last_heard. Filter must still work.
        feats = [_feat(origin="local_radio", last_seen=self.NOW - 60)]
        # Strip last_heard if it would have been there
        del feats[0]["properties"]
        feats[0]["properties"] = {
            "source_origin": "local_radio",
            "federated": False,
            "last_seen": self.NOW - 60,
            "id": "!x",
            "network": "meshtastic",
        }
        out = _apply_view_preset(feats, "live_rf", now=self.NOW)
        assert out == feats

    def test_known_presets_cover_dropdown(self):
        # Sanity: every option in web/node_map.html's dropdown maps to
        # a server-side spec. If the dropdown grows, this fails until
        # VIEW_PRESETS is extended in lockstep.
        dropdown_options = {
            "custom", "live_rf", "live_rf_mqtt",
            "all_gps", "external_only", "fleet_union", "local_only",
        }
        assert dropdown_options.issubset(set(VIEW_PRESETS.keys()))


class TestApplyViewPresetToPositionLess:
    """Same predicates, dict shape (no Feature wrapper)."""

    NOW = 2_000_000_000.0

    def test_pass_through_for_unknown_preset(self):
        entries = [{"id": "x", "source_origin": "meshcore_public"}]
        assert _apply_view_preset_to_position_less(entries, "no-such") is entries

    def test_external_only_filters_dict_entries(self):
        entries = [
            {"id": "a", "source_origin": "meshcore_public"},
            {"id": "b", "source_origin": "local_radio"},
        ]
        out = _apply_view_preset_to_position_less(
            entries, "external_only", now=self.NOW
        )
        assert len(out) == 1
        assert out[0]["id"] == "a"

    def test_local_only_drops_federated_dict_entries(self):
        entries = [
            {"id": "a", "source_origin": "rns_path_table", "federated": False},
            {"id": "b", "source_origin": "rns_path_table", "federated": True},
        ]
        out = _apply_view_preset_to_position_less(
            entries, "local_only", now=self.NOW
        )
        assert len(out) == 1
        assert out[0]["id"] == "a"


# ---------------------------------------------------------------------------
# /lab/rollup endpoint — serves rollup markdown produced by
# meshforge-lab-rollup.service. See feedback_bundle_full_stack_not_light_version.
# ---------------------------------------------------------------------------

class TestServeLabRollup:
    """The /lab/* endpoints surface the rollup state files."""

    def _make_handler_with_state(self, tmp_path, monkeypatch,
                                  filename=None, content=b""):
        """Point XDG_STATE_HOME at tmp_path and optionally seed a file.

        ``filename`` is str|None and ``content`` is bytes|str — annotations
        omitted because this file currently has to import-compile under
        Python 3.9 in CI (PEP-604 union syntax landed in 3.10).
        """
        state_dir = tmp_path / "meshforge"
        state_dir.mkdir(parents=True, exist_ok=True)
        if filename is not None:
            data = content.encode() if isinstance(content, str) else content
            (state_dir / filename).write_bytes(data)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

        h = _make_handler("gzip")
        return h

    def test_leaderboard_variant_serves_file(self, tmp_path, monkeypatch):
        h = self._make_handler_with_state(
            tmp_path, monkeypatch,
            filename="lab-traffic-rollup-leaderboard.md",
            content="| pair | fail % |\n|---|---:|\n| foo->bar | 100.0 |\n",
        )
        h._serve_lab_rollup(variant="leaderboard")
        body = h.wfile.getvalue()
        # Small payload — not gzipped, raw markdown comes through.
        assert b"foo->bar" in body
        h.send_response.assert_called_once_with(200)
        content_types = [v for k, v in h._sent_headers if k == "Content-Type"]
        assert any("text/markdown" in v for v in content_types)

    def test_alphabetical_variant_routes_to_different_file(self, tmp_path, monkeypatch):
        h = self._make_handler_with_state(
            tmp_path, monkeypatch,
            filename="lab-traffic-rollup.md",
            content="alphabetical-marker\n",
        )
        h._serve_lab_rollup(variant="alphabetical")
        assert b"alphabetical-marker" in h.wfile.getvalue()
        h.send_response.assert_called_once_with(200)

    def test_synth_variant_routes_to_synth_file(self, tmp_path, monkeypatch):
        h = self._make_handler_with_state(
            tmp_path, monkeypatch,
            filename="lab-synth-soak-rollup.md",
            content="synth-marker\n",
        )
        h._serve_lab_rollup(variant="synth")
        assert b"synth-marker" in h.wfile.getvalue()

    def test_missing_file_returns_404_with_install_hint(self, tmp_path, monkeypatch):
        # No file seeded — must 404 and emit a fix-hint pointing at the
        # systemd template. Per feedback_visual_dashboard_as_human_claude_collab,
        # a silent-empty response is the failure mode we're explicitly avoiding.
        h = self._make_handler_with_state(tmp_path, monkeypatch)
        h._serve_lab_rollup(variant="leaderboard")
        h.send_response.assert_called_once_with(404)
        body = h.wfile.getvalue()
        assert b"meshforge-lab-rollup" in body
        assert b"systemctl --user enable --now" in body

    def test_unknown_variant_returns_404(self, tmp_path, monkeypatch):
        h = self._make_handler_with_state(tmp_path, monkeypatch)
        h._serve_lab_rollup(variant="garbage")
        h.send_response.assert_called_once_with(404)
        assert b"unknown rollup variant" in h.wfile.getvalue()


class TestServeFleetTestsList:
    """/fleet/tests merges timer-driven and manual-fire signals.

    Two failure modes this guards against:
      1. "Refresh lab rollup" chip never advances after a manual click,
         because the dashboard was reading the .timer's LastTriggerUSec
         only. Fixed by merging the service's ExecMainExitTimestamp /
         ActiveEnterTimestamp into last_fire_unix (most recent wins).
      2. "Synth soak fire" button on a host where the unit isn't
         installed silently fails with exit=5 from systemctl. Fixed by
         exposing not_installed (LoadState != "loaded") so the UI can
         grey the button out.
    """

    def _seed(self, monkeypatch, timer_index=None, props_by_unit=None):
        """Patch the two helpers _serve_fleet_tests_list reaches into.

        ``timer_index`` keys are .service unit names → normalized timer
        entry (same shape as _normalize_timer returns).
        ``props_by_unit`` keys are (unit, scope) → dict of
        ``systemctl show -p`` key=value strings.
        """
        timer_index = timer_index or {}
        props_by_unit = props_by_unit or {}

        # _list_timers_scope returns raw `list-timers` JSON entries; the
        # handler post-processes those via _normalize_timer. Patch both
        # together: emit the synthetic timer entries via _list_timers_scope
        # and make _normalize_timer pass them through unchanged.
        synthetic_raw = []
        for activates, entry in timer_index.items():
            synthetic_raw.append({
                "unit": entry.get("name", "x.timer"),
                "activates": activates,
            })

        def _fake_list_timers(scope):
            return [r for r in synthetic_raw
                    if timer_index[r["activates"]]["scope"] == scope]

        def _fake_normalize(raw, scope, now):
            return timer_index.get(raw["activates"])

        def _fake_show(unit, scope, _props):
            return props_by_unit.get((unit, scope), {})

        monkeypatch.setattr(
            "utils.fleet_snapshot._list_timers_scope", _fake_list_timers,
        )
        monkeypatch.setattr(
            "utils.fleet_snapshot._normalize_timer", _fake_normalize,
        )
        monkeypatch.setattr(
            "utils.fleet_snapshot._show_unit_props", _fake_show,
        )

    def _call(self):
        h = _make_handler("")
        h._serve_fleet_tests_list()
        body = h.wfile.getvalue()
        # Strip HTTP status-line framing — _serve_json writes just the
        # body to wfile and status/headers go through send_response /
        # send_header (which are mocks in _make_handler). The body IS
        # the JSON document.
        return json.loads(body), h

    def test_manual_fire_advances_chip_when_service_timestamp_newer(
        self, monkeypatch,
    ):
        """The smoking gun. Timer last triggered an hour ago, but the
        operator just hit "Refresh lab rollup" 30s ago — chip should
        show 30s, not 3600s."""
        import time as _time
        now = _time.time()
        self._seed(
            monkeypatch,
            timer_index={
                "meshforge-lab-rollup.service": {
                    "name": "meshforge-lab-rollup.timer",
                    "scope": "user",
                    "last_fire_unix": now - 3600,
                    "next_fire_unix": now + 600,
                    "age_s": 3600.0,
                    "stale": False,
                },
            },
            props_by_unit={
                ("meshforge-lab-rollup.service", "user"): {
                    "LoadState": "loaded",
                    "ExecMainExitTimestamp": f"@{int(now - 30)}",
                    "ActiveEnterTimestamp": "",
                },
            },
        )
        payload, _ = self._call()
        rollup = next(t for t in payload["tests"] if t["id"] == "lab-rollup")
        assert rollup["last_fire_unix"] == pytest.approx(now - 30, abs=2)
        assert rollup["age_s"] == pytest.approx(30.0, abs=2)
        assert rollup["not_installed"] is False

    def test_timer_wins_when_more_recent_than_service_exit(
        self, monkeypatch,
    ):
        """If the timer just fired (5s ago) and the last manual fire was
        an hour ago, the chip reflects the timer fire."""
        import time as _time
        now = _time.time()
        self._seed(
            monkeypatch,
            timer_index={
                "meshforge-tracer.service": {
                    "name": "meshforge-tracer.timer",
                    "scope": "user",
                    "last_fire_unix": now - 5,
                    "next_fire_unix": now + 295,
                    "age_s": 5.0,
                    "stale": False,
                },
            },
            props_by_unit={
                ("meshforge-tracer.service", "user"): {
                    "LoadState": "loaded",
                    "ExecMainExitTimestamp": f"@{int(now - 3600)}",
                    "ActiveEnterTimestamp": "",
                },
            },
        )
        payload, _ = self._call()
        tracer = next(t for t in payload["tests"] if t["id"] == "tracer")
        assert tracer["last_fire_unix"] == pytest.approx(now - 5, abs=2)
        assert tracer["age_s"] == pytest.approx(5.0, abs=2)

    def test_not_installed_flag_set_when_unit_missing(self, monkeypatch):
        """Synth-soak is installed on only one fleet host; the rest
        return LoadState=not-found. Dashboard needs the flag to grey
        the button instead of letting a click silently fail."""
        self._seed(
            monkeypatch,
            props_by_unit={
                ("meshforge-synth-soak.service", "user"): {
                    "LoadState": "not-found",
                    "ExecMainExitTimestamp": "",
                    "ActiveEnterTimestamp": "",
                },
            },
        )
        payload, _ = self._call()
        synth = next(t for t in payload["tests"] if t["id"] == "synth-soak")
        assert synth["not_installed"] is True
        assert synth["last_fire_unix"] is None
        assert synth["age_s"] is None

    def test_loaded_unit_is_not_flagged_not_installed(self, monkeypatch):
        self._seed(
            monkeypatch,
            props_by_unit={
                ("meshforge-lab-rollup.service", "user"): {
                    "LoadState": "loaded",
                    "ExecMainExitTimestamp": "",
                    "ActiveEnterTimestamp": "",
                },
            },
        )
        payload, _ = self._call()
        rollup = next(t for t in payload["tests"] if t["id"] == "lab-rollup")
        assert rollup["not_installed"] is False

    def test_missing_loadstate_defaults_to_installed(self, monkeypatch):
        """systemctl show failure returns {} from _show_unit_props.
        Missing LoadState must NOT mark the unit not_installed — that
        would mass-grey buttons on a transient systemctl hiccup.
        Treat "no signal" as "assume installed, click will reveal."
        """
        self._seed(monkeypatch, props_by_unit={})  # all units return {}
        payload, _ = self._call()
        for t in payload["tests"]:
            assert t["not_installed"] is False, (
                f"{t['id']}: must default to installed when LoadState "
                f"is unknown — see test docstring"
            )

    def test_all_four_known_tests_emitted(self, monkeypatch):
        """The MA dashboard reads the test list from this endpoint
        (no hardcoded JS list). All four _FLEET_TESTS entries must
        appear so the buttons render."""
        self._seed(monkeypatch)
        payload, _ = self._call()
        emitted_ids = {t["id"] for t in payload["tests"]}
        assert emitted_ids == {
            "tracer", "synth-soak", "lab-rollup", "ci-status",
        }
        for t in payload["tests"]:
            assert {"id", "unit", "scope", "label", "description",
                    "last_fire_unix", "next_fire_unix", "age_s",
                    "not_installed"} <= set(t.keys())


class TestServeText:
    """_serve_text is the helper used by /lab/* and any future markdown-y
    endpoints. Mirrors _serve_json gzip + CORS behavior."""

    def test_str_input_encoded_as_utf8(self):
        h = _make_handler("")
        h._serve_text("hello-Ω")
        assert h.wfile.getvalue() == "hello-Ω".encode("utf-8")

    def test_bytes_input_passed_through(self):
        h = _make_handler("")
        h._serve_text(b"\x01\x02\x03")
        assert h.wfile.getvalue() == b"\x01\x02\x03"

    def test_large_payload_gzipped_when_client_accepts(self):
        h = _make_handler("gzip")
        h._serve_text("x" * 50000, content_type="text/markdown")
        body = h.wfile.getvalue()
        assert body.startswith(b"\x1f\x8b"), "expected gzip magic bytes"
        assert gzip.decompress(body) == ("x" * 50000).encode("utf-8")

    def test_custom_status_code(self):
        h = _make_handler("")
        h._serve_text("oops", status=503)
        h.send_response.assert_called_once_with(503)


# ── Issue #70: /api/nodes/directory response cache ─────────────────────
from utils._directory_response_cache import DirectoryResponseCache


def _make_directory_handler(
    *,
    accept_encoding: str = "gzip",
    snapshot=None,
    cache: DirectoryResponseCache = None,
    record_size_observer=None,
    raise_on_snapshot=False,
    query=None,
):
    """Wire a handler with the minimum collector surface ``_serve_directory``
    touches: a history with ``get_directory_snapshot`` +
    ``record_directory_serialized_size`` and a real response cache.
    """
    h = _make_handler(accept_encoding)

    class _History:
        def __init__(self):
            self.snapshot_call_count = 0
            self.record_calls: list = []

        def get_directory_snapshot(self, include_position_less=True):
            self.snapshot_call_count += 1
            if raise_on_snapshot:
                raise RuntimeError("simulated DB failure")
            return snapshot or ([], [])

        def record_directory_serialized_size(self, raw, compressed):
            self.record_calls.append((raw, compressed))
            if record_size_observer is not None:
                record_size_observer(raw, compressed)

    class _Collector:
        def __init__(self):
            self._history = _History()
            self._directory_response_cache = (
                cache if cache is not None else DirectoryResponseCache(ttl_s=5.0)
            )

    h.collector = _Collector()
    h._query = query or {}
    return h


class TestDirectoryHandlerCacheIssue70:
    """Pin the headline contracts:

    1. Two consecutive requests within TTL run get_directory_snapshot once
       (and json.dumps once — implied by the snapshot count).
    2. size_observer fires exactly once across N within-TTL requests, so
       the Issue #64 stats cache isn't churned.
    3. Cached gzipped variant is reused for gzip-accepting clients;
       cached raw variant is reused for non-accepting clients — both
       served from a single build.
    4. DB errors still propagate to a 500 response (no caching the failure).
    """

    def test_consecutive_requests_within_ttl_invoke_snapshot_once(self):
        snapshot = ([], [{"id": "!a", "network": "rns"}])
        h1 = _make_directory_handler(snapshot=snapshot)
        h1._serve_directory()
        # h2 shares the same collector → cache + history are the same instances
        h2 = _make_handler("gzip")
        h2.collector = h1.collector
        h2._query = {}
        h2._serve_directory()
        assert h1.collector._history.snapshot_call_count == 1, (
            "second request within TTL must not re-run get_directory_snapshot"
        )

    def test_size_observer_fires_only_on_cache_miss(self):
        h1 = _make_directory_handler(snapshot=([], []))
        h1._serve_directory()
        h2 = _make_handler("gzip")
        h2.collector = h1.collector
        h2._query = {}
        h2._serve_directory()
        # Issue #64 record_directory_serialized_size fires once across two
        # requests — cache hits reuse the size already recorded.
        assert len(h1.collector._history.record_calls) == 1, (
            f"size_observer must fire only on cache miss; got "
            f"{h1.collector._history.record_calls}"
        )

    def test_gzip_and_non_gzip_clients_share_build(self):
        # Build a payload big enough to cross _GZIP_MIN_BYTES so the
        # gzip variant gets cached.
        big_snapshot = (
            [],
            [{"id": f"!{i:08d}", "network": "rns", "name": "x" * 100}
             for i in range(200)],
        )
        cache = DirectoryResponseCache(ttl_s=5.0)
        h_gz = _make_directory_handler(
            accept_encoding="gzip", snapshot=big_snapshot, cache=cache
        )
        h_gz._serve_directory()
        body_gz = h_gz.wfile.getvalue()
        assert body_gz.startswith(b"\x1f\x8b"), "gzip client must get gzip"

        # Second handler, same collector + cache, but client doesn't
        # accept gzip → must get raw bytes from the SAME cached build
        # (collector._history.snapshot_call_count stays 1).
        h_raw = _make_handler("")
        h_raw.collector = h_gz.collector
        h_raw._query = {}
        h_raw._serve_directory()
        body_raw = h_raw.wfile.getvalue()
        assert not body_raw.startswith(b"\x1f\x8b"), (
            "non-gzip client must get raw bytes"
        )
        assert gzip.decompress(body_gz) == body_raw, (
            "raw and gzipped served from the same cache must round-trip"
        )
        assert h_gz.collector._history.snapshot_call_count == 1, (
            "second client (different encoding) must NOT trigger rebuild"
        )

    def test_snapshot_failure_returns_500_and_does_not_cache(self):
        h = _make_directory_handler(raise_on_snapshot=True)
        h._serve_directory()
        h.send_response.assert_called_with(500)
        # Cache must remain empty so a recovered DB doesn't serve a
        # cached failure body to the next caller.
        assert h.collector._directory_response_cache.stats()["entry_count"] == 0

    def test_concurrent_misses_coalesce_into_one_snapshot(self):
        """The headline regression guard for the live-fleet wedge.

        Federation peers polling at the same TTL boundary used to each
        pay json.dumps + gzip independently — stacked under GIL until
        the slowest one cleared. With single-flight, exactly one
        get_directory_snapshot runs across N concurrent requests; the
        rest receive the originating build's bytes.
        """
        snapshot = (
            [],
            [{"id": f"!{i:08d}", "network": "rns", "name": "x" * 50}
             for i in range(100)],
        )
        cache = DirectoryResponseCache(ttl_s=5.0)
        # Share one collector across all handler instances so they
        # share the cache and the history.
        seed = _make_directory_handler(snapshot=snapshot, cache=cache)
        collector = seed.collector

        ready = threading.Barrier(6)

        def _worker():
            h = _make_handler("gzip")
            h.collector = collector
            h._query = {}
            ready.wait()
            h._serve_directory()

        threads = [threading.Thread(target=_worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert collector._history.snapshot_call_count == 1, (
            f"6 concurrent requests must coalesce to one DB scan + serialize; "
            f"got {collector._history.snapshot_call_count}"
        )
        # record_directory_serialized_size also fires only once — Issue
        # #64 stats cache stays warm across the burst.
        assert len(collector._history.record_calls) == 1


