"""
RF Tools Mixin - Line of Sight and RF calculations

Provides LOS calculator, Fresnel zone analysis, and link budget tools.
Extracted from tools.py for maintainability.
"""

import json
import math
import threading
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    import os
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


class RFToolsMixin:
    """Mixin providing RF calculation functionality for ToolsPanel"""

    # Earth radius in km
    EARTH_RADIUS_KM = 6371

    def _haversine_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate great-circle distance between two points.

        Args:
            lat1, lon1: First point coordinates in degrees
            lat2, lon2: Second point coordinates in degrees

        Returns:
            Distance in kilometers
        """
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return self.EARTH_RADIUS_KM * c

    def _fresnel_zone_radius(self, distance_km: float, freq_mhz: float, point_fraction: float = 0.5) -> float:
        """Calculate first Fresnel zone radius at a point along the path.

        Args:
            distance_km: Total path distance in km
            freq_mhz: Frequency in MHz
            point_fraction: Fraction along path (0-1, default 0.5 = midpoint)

        Returns:
            Fresnel zone radius in meters
        """
        wavelength_m = 300 / freq_mhz  # c / f in MHz gives wavelength in m
        d1 = distance_km * 1000 * point_fraction
        d2 = distance_km * 1000 * (1 - point_fraction)

        if d1 <= 0 or d2 <= 0:
            return 0

        # First Fresnel zone radius formula
        return math.sqrt((wavelength_m * d1 * d2) / (d1 + d2))

    def _earth_bulge(self, distance_km: float, point_fraction: float = 0.5) -> float:
        """Calculate earth curvature bulge at a point along the path.

        Args:
            distance_km: Total path distance in km
            point_fraction: Fraction along path (0-1)

        Returns:
            Earth bulge in meters
        """
        d1 = distance_km * point_fraction
        d2 = distance_km * (1 - point_fraction)

        # Earth bulge formula (assuming 4/3 earth radius for refraction)
        k = 4 / 3  # Effective earth radius factor
        return (d1 * d2 * 1000000) / (2 * self.EARTH_RADIUS_KM * 1000 * k)

    def _load_los_locations(self):
        """Load saved LOS locations from user config"""
        config_dir = get_real_user_home() / '.config' / 'meshforge'
        locations_file = config_dir / 'los_locations.json'

        if not locations_file.exists():
            return {'presets': [], 'history': []}

        try:
            with open(locations_file, 'r') as f:
                return json.load(f)
        except Exception:
            return {'presets': [], 'history': []}

    def _save_los_locations(self, locations: dict):
        """Save LOS locations to user config"""
        config_dir = get_real_user_home() / '.config' / 'meshforge'
        config_dir.mkdir(parents=True, exist_ok=True)
        locations_file = config_dir / 'los_locations.json'

        try:
            with open(locations_file, 'w') as f:
                json.dump(locations, f, indent=2)
        except Exception as e:
            GLib.idle_add(self._log, f"Error saving locations: {e}")

    def _on_calculate_los(self, button=None):
        """Calculate line of sight between two points"""
        # This would typically show a dialog to get coordinates
        # For the mixin, we provide the calculation logic
        GLib.idle_add(self._log, "LOS Calculator: Enter coordinates in dialog")

    def _calculate_los_result(self, lat1: float, lon1: float, elev1: float,
                              lat2: float, lon2: float, elev2: float,
                              freq_mhz: float = 915.0):
        """Calculate LOS parameters between two points.

        Args:
            lat1, lon1, elev1: Point A coordinates and elevation (m)
            lat2, lon2, elev2: Point B coordinates and elevation (m)
            freq_mhz: Operating frequency in MHz

        Returns:
            Dict with distance, fresnel zone, earth bulge, clearance info
        """
        distance_km = self._haversine_distance(lat1, lon1, lat2, lon2)
        fresnel_r = self._fresnel_zone_radius(distance_km, freq_mhz)
        bulge = self._earth_bulge(distance_km)

        # Simple LOS check (would need terrain data for accuracy)
        visual_los = (elev1 + elev2) / 2 > bulge

        return {
            'distance_km': distance_km,
            'distance_mi': distance_km * 0.621371,
            'fresnel_radius_m': fresnel_r,
            'earth_bulge_m': bulge,
            'frequency_mhz': freq_mhz,
            'visual_los': visual_los,
            'required_clearance_m': fresnel_r * 0.6 + bulge,  # 60% Fresnel clearance
        }

    def _on_link_budget(self, button=None):
        """Show link budget reference"""
        GLib.idle_add(self._log, "\n=== LoRa Link Budget Reference ===")
        GLib.idle_add(self._log, "915 MHz (US):")
        GLib.idle_add(self._log, "  TX Power: +20 to +30 dBm")
        GLib.idle_add(self._log, "  RX Sensitivity: -120 to -137 dBm (SF dependent)")
        GLib.idle_add(self._log, "  Typical antenna gain: 2-6 dBi")
        GLib.idle_add(self._log, "")
        GLib.idle_add(self._log, "Free Space Path Loss:")
        GLib.idle_add(self._log, "  1 km: ~92 dB")
        GLib.idle_add(self._log, "  10 km: ~112 dB")
        GLib.idle_add(self._log, "  100 km: ~132 dB")
