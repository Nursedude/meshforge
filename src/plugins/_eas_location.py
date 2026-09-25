"""EAS alert LOCATION resolution — split out of eas_alerts.py (MF025 size cap).

Why it exists (2026-09-25 live-truth pass): every box's weather all-clear was
about the plugin TEMPLATE's example point (48.50,-123.0, the Washington coast).
The retired GTK EAS panel (e9bdefea) had written the operator's real point to
eas_location.json; the GTK removal (4d09c8c1) took the only reader with it.
Resolution order: a non-template [location] in eas_alerts.ini, then the
operator's eas_location.json, else "template" — which every consumer must
render as UNKNOWN, never as an all-clear.
"""
import configparser
import json
from pathlib import Path


def _is_template_location(cfg, template_text) -> bool:
    tmpl = configparser.ConfigParser()
    tmpl.read_string(template_text)
    try:
        return (abs(cfg.getfloat("location", "latitude") - tmpl.getfloat("location", "latitude")) < 1e-6
                and abs(cfg.getfloat("location", "longitude") - tmpl.getfloat("location", "longitude")) < 1e-6)
    except (configparser.Error, ValueError):
        return True


def _read_legacy_location(path):
    """(lat, lon) from the retired EAS panel's eas_location.json, or None.
    Validated: numbers in range, not (0, 0). Unreadable = None, never a guess."""
    try:
        d = json.loads(Path(path).read_text())
        lat, lon = float(d["latitude"]), float(d["longitude"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return lat, lon
