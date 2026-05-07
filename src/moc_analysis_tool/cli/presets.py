"""Named presets — bundles of AnalysisSpec defaults for common runs.

Adding a preset = adding one entry to PRESETS dict. The CLI lists
available presets via --list-presets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict

from moc_analysis_tool.spec import AnalysisSpec, TimeWindow


def _hawaii_may2026() -> AnalysisSpec:
    """BIRC 2026-05-17 talk's Hawaii LongFast capture window.

    Exactly mirrors the captured data in ``presentation_capture.db``:
    Apr 28 18:27 → May 4 00:00 UTC (the diag_24h log capture window
    plus a small tail for completeness).
    """
    return AnalysisSpec(
        name="hawaii_may2026",
        region_key="hawaii",
        window=TimeWindow(
            start=datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 4, 0, 0, tzinfo=timezone.utc),
        ),
        top_n=10,
        snr_min_samples=30,
    )


def _last_24h_hawaii() -> AnalysisSpec:
    """Rolling 24-hour Hawaii view for spot-checks / TUI demos."""
    return AnalysisSpec(
        name="hawaii_last24h",
        region_key="hawaii",
        window=TimeWindow.last_n_hours(24),
        top_n=10,
        snr_min_samples=10,  # smaller window → smaller floor
    )


def _last_24h_global() -> AnalysisSpec:
    """Rolling 24-hour view, no region filter — fleet-quarterly material."""
    return AnalysisSpec(
        name="global_last24h",
        region_key=None,
        window=TimeWindow.last_n_hours(24),
        top_n=10,
        snr_min_samples=10,
    )


PRESETS: Dict[str, Callable[[], AnalysisSpec]] = {
    "hawaii_may2026":  _hawaii_may2026,
    "hawaii_last24h":  _last_24h_hawaii,
    "global_last24h":  _last_24h_global,
}


def get_preset(name: str) -> AnalysisSpec:
    """Resolve a preset name to a fresh AnalysisSpec.

    Raises KeyError when the preset is unknown — caller should catch
    and emit a friendly error listing known presets.
    """
    factory = PRESETS.get(name)
    if factory is None:
        raise KeyError(name)
    return factory()


def list_presets() -> Dict[str, str]:
    """Return a name → description map suitable for CLI output."""
    out = {}
    for name, factory in PRESETS.items():
        spec = factory()
        win = spec.window
        out[name] = (
            f"region={spec.region_key or 'all'}  "
            f"window={win.start.strftime('%Y-%m-%dT%H:%M')}→"
            f"{win.end.strftime('%Y-%m-%dT%H:%M')}  "
            f"top_n={spec.top_n}"
        )
    return out
