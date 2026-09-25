"""Dashboard › View Alerts + Emergency Mode — live-truth pass 2026-09-25.

Two all-clears were claims about nothing:
- "Mesh: No active alerts" from a mesh alert engine the TUI never fed;
- "Weather: No active alerts" for the TEMPLATE's example point (48.50,-123.0,
  the Washington coast): every box had either no EAS config or one WRITTEN
  from the template, so every weather all-clear was about the wrong place.
"""
import configparser
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.eas_alerts import EAS_CONFIG_TEMPLATE, EASAlertsPlugin  # noqa: E402


def _plugin(tmp_path, lat=None, lon=None, severity=None):
    cfg = configparser.ConfigParser()
    cfg.read_string(EAS_CONFIG_TEMPLATE)
    if lat is not None:
        cfg.set("location", "latitude", str(lat))
        cfg.set("location", "longitude", str(lon))
    if severity is not None:
        cfg.set("noaa_weather", "severity_filter", severity)
    p = EASAlertsPlugin()
    p._config = cfg
    p._config_path = tmp_path / "eas_alerts.ini"
    return p


def test_the_template_location_is_not_a_configured_location(tmp_path):
    assert _plugin(tmp_path).location_is_template() is True
    assert _plugin(tmp_path, 19.7, -155.1).location_is_template() is False     # an example operator point


def test_an_unreadable_location_is_not_configured(tmp_path):
    p = _plugin(tmp_path)
    p._config.set("location", "latitude", "not-a-number")
    assert p.location_is_template() is True


def test_the_notice_says_why_and_where(tmp_path):
    n = _plugin(tmp_path).location_notice()
    assert "NOT CONFIGURED" in n and "48.50" in n and "eas_alerts.ini" in n


def test_an_all_clear_names_its_severity_filter(tmp_path):
    assert _plugin(tmp_path, 19.7, -155.1, "Extreme,Severe").severity_filter_text() == "Extreme,Severe"


def test_an_engine_with_no_feed_says_so():
    from utils.mesh_alert_engine import get_alert_engine
    engine = get_alert_engine()
    before = engine._subscriber
    try:
        engine._subscriber = None
        assert engine.has_feed() is False
        engine._subscriber = object()
        assert engine.has_feed() is True
    finally:
        engine._subscriber = before


def test_the_severity_filter_is_read_from_the_template_section(tmp_path):
    """Pins the SECTION name: the first cut read [noaa] (absent), fell back to
    "" silently, and the all-clear never named its filter."""
    assert _plugin(tmp_path).severity_filter_text() == "Extreme,Severe"      # the template's own value
