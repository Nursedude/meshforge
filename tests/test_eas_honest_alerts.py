"""EAS alerts must never manufacture an all-clear from a dead uplink.

THE DEFECT THESE TESTS PIN (found 2026-09-15):
``get_weather_alerts()`` caught ``URLError``, logged it, and fell through to
``return alerts`` — still the empty list it started with. Emergency Mode
rendered that as::

    No active weather alerts for your area.

A positive safety claim produced by a failed observation channel, in the
EMCOMM menu, on a kit whose whole design duty-cycles its uplink.
``honest_failure_modes.md`` #2: absence of evidence is not evidence of
absence.

Every test below fails against the pre-fix tree (the outcome API did not
exist and the screen printed the sentence unconditionally).
"""

import json
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'launcher_tui'))

import urllib.error

from plugins.eas_alerts import (  # noqa: E402
    EASAlertsPlugin, Alert, AlertSource, AlertSeverity, FetchOutcome,
    FETCH_OK, FETCH_UNREACHABLE, FETCH_ERROR, FETCH_DISABLED, FETCH_NEVER,
    format_alert_line,
)


@pytest.fixture
def plugin(tmp_path):
    """A plugin whose cache lives in tmp_path — never the operator's home."""
    p = EASAlertsPlugin()
    p._config = p._load_config()
    p._cache_path = lambda: tmp_path / "eas_last.json"   # type: ignore[method-assign]
    return p


def _alert(title="Hurricane Warning", source=AlertSource.NOAA):
    return Alert(
        id=f"urn:test:{title}", source=source, title=title,
        description="body", severity=AlertSeverity.EXTREME,
        event_type=title, areas=["Hawaii County"],
    )


# ---------------------------------------------------------------------------
# The core tri-state
# ---------------------------------------------------------------------------

class TestFetchOutcomeTriState:

    def test_network_failure_is_unreachable_not_clear(self, plugin):
        """The whole point: a dead network must NOT read as 'no alerts'."""
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("Network is unreachable")):
            result = plugin.get_weather_alerts()

        assert result == []            # legacy API shape preserved
        outcome = plugin.get_outcome(AlertSource.NOAA)
        assert outcome.status == FETCH_UNREACHABLE
        assert outcome.observed is False, (
            "observed=True would license the sentence 'no active alerts'"
        )
        assert "UNKNOWN" in outcome.summary_line()
        assert "no active alerts" not in outcome.summary_line().lower()

    def test_http_error_is_unreachable_not_clear(self, plugin):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.HTTPError(
                       "u", 503, "unavailable", {}, None)):
            plugin.get_weather_alerts()
        outcome = plugin.get_outcome(AlertSource.NOAA)
        assert outcome.status == FETCH_UNREACHABLE
        assert outcome.observed is False
        assert "503" in outcome.error

    def test_parse_failure_is_error_not_clear(self, plugin):
        """A non-network exception is still UNOBSERVED, never clear."""
        with patch('urllib.request.urlopen', side_effect=ValueError("bad json")):
            plugin.get_weather_alerts()
        outcome = plugin.get_outcome(AlertSource.NOAA)
        assert outcome.status == FETCH_ERROR
        assert outcome.observed is False

    def test_feed_answered_with_nothing_IS_clear(self, plugin):
        """The one case that may say 'no active alerts'."""
        fake = types.SimpleNamespace(
            read=lambda: json.dumps({"features": []}).encode(),
            __enter__=lambda s: s, __exit__=lambda *a: False)
        fake.__enter__ = lambda: fake
        with patch('urllib.request.urlopen') as m:
            m.return_value.__enter__.return_value.read.return_value = \
                json.dumps({"features": []}).encode()
            plugin.get_weather_alerts()
        outcome = plugin.get_outcome(AlertSource.NOAA)
        assert outcome.status == FETCH_OK
        assert outcome.observed is True
        assert "no active alerts" in outcome.summary_line()

    def test_never_fetched_is_never_not_clear(self, plugin):
        """A source never asked about is UNKNOWN, not empty."""
        outcome = plugin.get_outcome(AlertSource.FEMA)
        assert outcome.status == FETCH_NEVER
        assert outcome.observed is False
        assert "never fetched" in outcome.summary_line()

    def test_disabled_source_is_not_a_fault_and_not_an_answer(self, plugin):
        plugin._mark_disabled(AlertSource.USGS)
        outcome = plugin.get_outcome(AlertSource.USGS)
        assert outcome.status == FETCH_DISABLED
        assert outcome.observed is False
        assert "disabled" in outcome.summary_line()


# ---------------------------------------------------------------------------
# Last-known-good cache
# ---------------------------------------------------------------------------

class TestLastKnownGoodCache:

    def test_answer_is_persisted_and_survives_a_new_process(self, plugin, tmp_path):
        plugin._answered(AlertSource.NOAA, [_alert()])
        assert (tmp_path / "eas_last.json").exists()

        fresh = EASAlertsPlugin()
        fresh._cache_path = lambda: tmp_path / "eas_last.json"  # type: ignore
        cached, when = fresh._cached_for(AlertSource.NOAA)
        assert [a.title for a in cached] == ["Hurricane Warning"]
        assert when is not None

    def test_offline_surfaces_the_cached_answer_with_its_age(self, plugin, tmp_path):
        plugin._answered(AlertSource.NOAA, [_alert()])
        # backdate so the age is real, not 'just now'
        data = json.loads((tmp_path / "eas_last.json").read_text())
        data["NOAA/NWS"]["checked_at"] = (
            datetime.now() - timedelta(hours=4, minutes=12)).isoformat()
        (tmp_path / "eas_last.json").write_text(json.dumps(data))

        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("down")):
            plugin.get_weather_alerts()

        outcome = plugin.get_outcome(AlertSource.NOAA)
        assert outcome.observed is False
        assert len(outcome.cached_alerts) == 1
        assert "4h 12m ago" in outcome.summary_line()
        assert "UNKNOWN" in outcome.summary_line()

    def test_cached_alerts_are_NOT_returned_as_live(self, plugin, tmp_path):
        """Stale alerts must never reach the broadcast path as fresh.

        _check_alerts() re-broadcasts whatever get_*_alerts() returns; if the
        cache leaked into that return value the mesh would receive yesterday's
        hurricane warning as today's.
        """
        plugin._answered(AlertSource.NOAA, [_alert()])
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("down")):
            live = plugin.get_weather_alerts()
        assert live == [], "cached alerts must not be returned as live"

    def test_corrupt_cache_is_empty_not_fatal(self, plugin, tmp_path):
        (tmp_path / "eas_last.json").write_text("{not json")
        cached, when = plugin._cached_for(AlertSource.NOAA)
        assert cached == [] and when is None

    def test_unwritable_cache_warns_rather_than_failing_silently(self, plugin, caplog):
        plugin._cache_path = lambda: Path("/proc/nonexistent/eas.json")  # type: ignore
        plugin._answered(AlertSource.NOAA, [_alert()])
        assert any("could not persist" in r.message.lower() or
                   "could not persist" in r.getMessage().lower()
                   for r in caplog.records), "a swallowed write needs a witness"


# ---------------------------------------------------------------------------
# _check_alerts must not wipe state when it cannot see
# ---------------------------------------------------------------------------

class TestCheckAlertsHoldsStateWhenBlind:

    def test_all_sources_blind_holds_previous_alerts(self, plugin):
        plugin._current_alerts = [_alert()]
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("down")):
            plugin._check_alerts()
        assert len(plugin._current_alerts) == 1, (
            "an unobservable tick must not clear a real alert picture"
        )

    def test_an_observed_tick_does_replace_state(self, plugin):
        plugin._current_alerts = [_alert("Old")]
        plugin._answered(AlertSource.NOAA, [])   # NOAA really answered: none
        with patch.object(plugin, 'get_weather_alerts', return_value=[]), \
             patch.object(plugin, 'get_volcano_alerts', return_value=[]), \
             patch.object(plugin, 'get_fema_alerts', return_value=[]):
            plugin._check_alerts()
        assert plugin._current_alerts == []


# ---------------------------------------------------------------------------
# The formatter (one copy, two consumers)
# ---------------------------------------------------------------------------

class TestAlertFormatter:

    def test_renders_real_fields_not_a_dataclass_repr(self):
        """Pre-fix both callers read `headline`, which Alert does not have,
        so every real alert printed as ``Alert(id=..., source=...)``."""
        line = format_alert_line(_alert())
        assert "Hurricane Warning" in line
        assert "Extreme" in line
        assert "Alert(" not in line, "rendered the dataclass repr"
        assert "id=" not in line

    def test_blank_areas_do_not_render_empty_brackets(self):
        a = _alert()
        a.areas = ["", "  "]
        assert "[]" not in format_alert_line(a)

    def test_both_consumers_share_one_formatter(self):
        """hfm #5: two consumers of one behaviour share ONE implementation."""
        from handlers.emergency_mode import EmergencyModeHandler
        assert (EmergencyModeHandler._format_alert_line(_alert())
                == format_alert_line(_alert()))


# ---------------------------------------------------------------------------
# The consumer of record: the screens themselves
# ---------------------------------------------------------------------------

class TestScreensNeverClaimClearWhenBlind:

    def _render_emcomm(self, tmp_path, capsys):
        import handlers.emergency_mode as em
        h = em.EmergencyModeHandler()
        h.ctx = types.SimpleNamespace(wait_for_enter=lambda msg="": None)
        with patch.object(em, 'clear_screen', lambda: None), \
             patch.object(em.EASAlertsPlugin, '_cache_path',
                          lambda self: tmp_path / "eas_last.json"), \
             patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("Network is unreachable")):
            h._emcomm_eas_alerts()
        return capsys.readouterr().out

    def test_emcomm_screen_does_not_say_no_alerts_when_offline(self, tmp_path, capsys):
        out = self._render_emcomm(tmp_path, capsys)
        assert "No active weather alerts for your area" not in out, (
            "THE defect: a dead uplink rendered as an all-clear in EMCOMM"
        )
        assert "UNKNOWN" in out
        assert "not clear" in out.lower()

    def test_emcomm_screen_shows_cached_answer_labelled_as_history(
            self, tmp_path, capsys):
        seed = EASAlertsPlugin()
        seed._config = seed._load_config()
        seed._cache_path = lambda: tmp_path / "eas_last.json"  # type: ignore
        seed._answered(AlertSource.NOAA, [_alert()])

        out = self._render_emcomm(tmp_path, capsys)
        assert "(was)" in out, "history must be labelled, never shown as current"
        assert "Hurricane Warning" in out
        assert "Last answer" in out

    def test_dashboard_reports_unknown_not_no_alerts(self, tmp_path, capsys):
        import handlers.dashboard as dash
        with patch.object(dash.EASAlertsPlugin, '_cache_path',
                          lambda self: tmp_path / "eas_last.json"), \
             patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("down")):
            plug = dash.EASAlertsPlugin()
            plug._config = plug._load_config()
            plug.get_weather_alerts()
            outcome = plug.get_outcome(dash.AlertSource.NOAA)
        assert outcome.observed is False
        assert "Weather: No active alerts" not in outcome.summary_line()


class TestPluginIsUsableWithoutActivate:
    """The EAS screens construct EASAlertsPlugin() and call a fetcher directly.

    MEASURED 2026-09-15 against HEAD: ``__init__`` leaves ``_config = None``
    and ``_load_config()`` RETURNS the parser without assigning it, so every
    fetcher died on ``self._config.getfloat`` before touching the network.
    The EMCOMM screen therefore printed, online and offline alike::

        Alert check failed: 'NoneType' object has no attribute 'getfloat'
        (Check network connectivity)

    — blaming the network for a code defect — and the Dashboard's block
    swallowed it to logger.debug and printed NOTHING at all. The feature had
    never worked from either screen. These tests pin that it does now.
    """

    def test_fetch_all_checked_loads_config_itself(self, tmp_path):
        p = EASAlertsPlugin()
        p._cache_path = lambda: tmp_path / "eas_last.json"  # type: ignore
        assert p._config is None, "fresh plugin starts unconfigured"
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("down")):
            outcomes = p.fetch_all_checked()
        assert p._config is not None, (
            "_load_config() returns the parser; dropping the return is the "
            "bug that made every fetcher raise AttributeError"
        )
        assert len(outcomes) == 3
        assert all(o.status == FETCH_UNREACHABLE for o in outcomes)

    def test_emcomm_screen_does_not_blame_the_network_for_a_code_bug(
            self, tmp_path, capsys):
        import handlers.emergency_mode as em
        h = em.EmergencyModeHandler()
        h.ctx = types.SimpleNamespace(wait_for_enter=lambda msg="": None)
        with patch.object(em, 'clear_screen', lambda: None), \
             patch.object(em.EASAlertsPlugin, '_cache_path',
                          lambda self: tmp_path / "eas_last.json"), \
             patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("down")):
            h._emcomm_eas_alerts()
        out = capsys.readouterr().out
        assert "NoneType" not in out, "the config-never-loaded crash is back"
        assert "getfloat" not in out


    @pytest.mark.parametrize("fetcher", ["get_weather_alerts",
                                         "get_volcano_alerts",
                                         "get_fema_alerts"])
    def test_each_fetcher_loads_config_itself(self, tmp_path, fetcher):
        """A direct fetcher call on a fresh plugin must not die on
        ``None.getfloat`` — the Dashboard calls get_weather_alerts() that
        way, and 81674f0a fixed only fetch_all_checked (review 2026-09-22)."""
        p = EASAlertsPlugin()
        p._cache_path = lambda: tmp_path / "eas_last.json"  # type: ignore
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("down")):
            getattr(p, fetcher)()
        assert p._config is not None

    def test_dashboard_screen_does_not_blame_a_code_bug(self, tmp_path, capsys):
        """Render the REAL Dashboard alerts pane — no hand-loaded config.
        The older dashboard test above loads ``_config`` itself before
        calling the fetcher, which is exactly the step the screen skipped,
        so it passed while the screen printed `alert check failed
        (AttributeError)` online and offline alike."""
        import handlers.dashboard as dash
        h = dash.DashboardHandler()
        h.ctx = types.SimpleNamespace(env={}, env_state=None,
                                      wait_for_enter=lambda msg="": None)
        with patch.object(dash, 'clear_screen', lambda: None), \
             patch.object(dash.EASAlertsPlugin, '_cache_path',
                          lambda self: tmp_path / "eas_last.json"), \
             patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError("down")):
            h._show_alerts()
        out = capsys.readouterr().out
        assert "AttributeError" not in out, "the config-never-loaded crash is back"
        assert "Weather: UNKNOWN" in out
        assert "Weather: No active alerts" not in out


class TestSourceCaveats:

    def test_fema_archive_is_labelled_and_not_called_active(self):
        o = FetchOutcome(source=AlertSource.FEMA, status=FETCH_OK,
                         alerts=[_alert(source=AlertSource.FEMA)],
                         checked_at=datetime.now())
        line = o.summary_line()
        assert "archived" in line and "24 h delay" in line
        assert "active alert" not in line, (
            "an archive's records are not active local alerts"
        )
