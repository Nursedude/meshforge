"""
Emergency Alert System (EAS) Plugin for MeshForge.

Integrates emergency alerts from multiple sources:
- NOAA/NWS Weather Alerts (api.weather.gov)
- FEMA iPAWS Alerts (archived and live feed access)
- USGS Volcano Alerts (volcanoes.usgs.gov)

Broadcasts alerts to mesh network and provides TUI display.

API Sources:
- NOAA: https://api.weather.gov/alerts/active
- USGS: https://volcanoes.usgs.gov/hans-public/api/volcano/
- FEMA: https://www.fema.gov/api/open/v1/IpawsArchivedAlerts

Configuration (~/.config/meshforge/plugins/eas_alerts.ini):
    See EAS_CONFIG_TEMPLATE for all options.

Usage:
    from plugins.eas_alerts import EASAlertsPlugin

    plugin = EASAlertsPlugin()
    plugin.activate()

    # Check for alerts
    alerts = plugin.check_all_alerts()

    # Get specific alerts
    weather = plugin.get_weather_alerts()
    volcano = plugin.get_volcano_alerts()
"""

import configparser
import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

# Import plugin base classes via safe_import
from utils.safe_import import safe_import

_IntegrationPlugin, _PluginMetadata, _PluginType, _HAS_PLUGINS = safe_import(
    'utils.plugins', 'IntegrationPlugin', 'PluginMetadata', 'PluginType'
)

if _HAS_PLUGINS:
    IntegrationPlugin = _IntegrationPlugin
    PluginMetadata = _PluginMetadata
    PluginType = _PluginType
else:
    # Fallback for standalone/test usage
    from abc import ABC, abstractmethod

    class PluginType(Enum):
        PANEL = "panel"
        INTEGRATION = "integration"
        TOOL = "tool"
        PROTOCOL = "protocol"

    @dataclass
    class PluginMetadata:
        name: str
        version: str
        description: str
        author: str
        plugin_type: PluginType
        dependencies: List[str] = field(default_factory=list)
        homepage: Optional[str] = None
        license: str = "GPL-3.0"

    class IntegrationPlugin(ABC):
        @staticmethod
        @abstractmethod
        def get_metadata() -> PluginMetadata:
            pass

        @abstractmethod
        def activate(self) -> None:
            pass

        @abstractmethod
        def deactivate(self) -> None:
            pass

        @abstractmethod
        def connect(self) -> bool:
            pass

        @abstractmethod
        def disconnect(self) -> None:
            pass

        @abstractmethod
        def is_connected(self) -> bool:
            pass

# Import centralized path utility for sudo compatibility
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration Template
# ============================================================================

EAS_CONFIG_TEMPLATE = """
# MeshForge Emergency Alert System (EAS) Configuration
# =====================================================
# This file controls emergency alert monitoring and broadcasting.
# Location: ~/.config/meshforge/plugins/eas_alerts.ini
#
# Enable/disable specific alert sources and configure filtering.
# Alerts can be broadcast to mesh network and/or displayed in UI.

[general]
# Master enable for the EAS plugin
enabled = True

# Polling interval in seconds (default: 5 minutes)
poll_interval = 300

# User-Agent for API requests
user_agent = MeshForge-EAS/1.0

# Enable mesh broadcast of alerts
broadcast_enabled = True

# Channel to broadcast alerts (0 = primary)
broadcast_channel = 0

# Interface for broadcasting (1 = primary)
broadcast_interface = 1

# Dedupe window in seconds (avoid repeating same alert)
dedupe_window = 3600

# Maximum alerts per check (0 = unlimited)
max_alerts_per_check = 10

# Logging level for alerts (DEBUG, INFO, WARNING)
log_level = INFO


[location]
# Your location for proximity-based alerts
# Latitude and Longitude (decimal degrees)
latitude = 48.50
longitude = -123.0

# Radius in km for local alerts
radius_km = 100

# State code for NWS alerts (e.g., WA, CA, HI)
state = WA

# FIPS codes for FEMA alerts (comma separated)
# Find codes: https://en.wikipedia.org/wiki/Federal_Information_Processing_Standard_state_code
fips_codes = 53

# SAME codes for county-level alerts (comma separated)
# Find codes: https://www.weather.gov/nwr/counties
same_codes = 053029,053073


[noaa_weather]
# NOAA/NWS Weather Alert Settings
# API: https://api.weather.gov/alerts/active
enabled = True

# Alert severity filter (comma separated)
# Options: Extreme, Severe, Moderate, Minor, Unknown
severity_filter = Extreme,Severe

# Alert certainty filter (comma separated)
# Options: Observed, Likely, Possible, Unlikely, Unknown
certainty_filter = Observed,Likely

# Alert urgency filter (comma separated)
# Options: Immediate, Expected, Future, Past, Unknown
urgency_filter = Immediate,Expected

# Ignore test alerts
ignore_tests = True

# Words to ignore in headlines (comma separated)
ignore_words = test,exercise,drill

# Maximum forecast duration in days
forecast_days = 3

# Number of alerts to display
alert_count = 5

# Enable coastal marine alerts
coastal_enabled = False

# Coastal zone URL (find at https://tgftp.nws.noaa.gov/data/forecasts/marine/coastal/)
coastal_zone_url =


[usgs_volcano]
# USGS Volcano Alert Settings
# API: https://volcanoes.usgs.gov/hans-public/api/volcano/
enabled = True

# Alert level filter (comma separated)
# Options: WARNING, WATCH, ADVISORY, NORMAL
level_filter = WARNING,WATCH,ADVISORY

# Color code filter (comma separated)
# Options: RED, ORANGE, YELLOW, GREEN
color_filter = RED,ORANGE,YELLOW

# Specific volcanoes to monitor (comma separated VNUM)
# Leave empty to monitor all elevated volcanoes
# Examples: 311240 (Kilauea), 311060 (Mauna Loa)
volcano_list =

# Ignore test alerts
ignore_tests = True

# Words to ignore (comma separated)
ignore_words = test,exercise


[fema_ipaws]
# FEMA iPAWS Alert Settings
# Public API for archived alerts (24h delay)
# Live feed requires registration at IPAWS User Portal
enabled = True

# Use archived alerts API (public, 24h delay)
use_archived = True

# OpenFEMA API endpoint
archived_api_url = https://www.fema.gov/api/open/v1/IpawsArchivedAlerts

# Event types to include (comma separated CAP event codes)
# Common codes: TOR (Tornado), SVR (Severe Thunderstorm), FFW (Flash Flood)
# Leave empty for all event types
event_types =

# Ignore test/exercise alerts
ignore_tests = True

# Words to ignore in headlines (comma separated)
ignore_words = test,exercise,drill

# Days of historical alerts to fetch (1-7)
history_days = 1


[notifications]
# How to handle different alert severities

# Sound alert for extreme severity
sound_extreme = True

# Sound alert for severe severity
sound_severe = False

# Desktop notification
desktop_notification = True

# Email notification (requires SMTP config)
email_notification = False

# Email addresses (comma separated)
email_addresses =


[filters]
# Global word filters applied to all sources
# Alerts containing these words are ignored

# Enable global word filter
enabled = False

# Words to filter (comma separated, case insensitive)
filter_words =

# Regex patterns to filter (comma separated)
filter_patterns =
"""


# ============================================================================
# Data Classes
# ============================================================================

class AlertSeverity(Enum):
    """Alert severity levels (CAP standard)."""
    EXTREME = "Extreme"
    SEVERE = "Severe"
    MODERATE = "Moderate"
    MINOR = "Minor"
    UNKNOWN = "Unknown"


class AlertSource(Enum):
    """Alert data sources."""
    NOAA = "NOAA/NWS"
    USGS = "USGS Volcano"
    FEMA = "FEMA iPAWS"


@dataclass
class Alert:
    """Unified alert data structure."""
    id: str
    source: AlertSource
    title: str
    description: str
    severity: AlertSeverity
    event_type: str
    effective: Optional[datetime] = None
    expires: Optional[datetime] = None
    areas: List[str] = field(default_factory=list)
    coordinates: Optional[tuple] = None  # (lat, lon)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def is_active(self) -> bool:
        """Check if alert is currently active."""
        now = datetime.now()
        if self.expires and self.expires < now:
            return False
        if self.effective and self.effective > now:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'source': self.source.value,
            'title': self.title,
            'description': self.description,
            'severity': self.severity.value,
            'event_type': self.event_type,
            'effective': self.effective.isoformat() if self.effective else None,
            'expires': self.expires.isoformat() if self.expires else None,
            'areas': self.areas,
            'coordinates': self.coordinates,
        }

    def to_mesh_message(self, max_length: int = 200) -> str:
        """Format alert for mesh broadcast."""
        # Build compact message
        severity_emoji = {
            AlertSeverity.EXTREME: "🔴",
            AlertSeverity.SEVERE: "🟠",
            AlertSeverity.MODERATE: "🟡",
            AlertSeverity.MINOR: "🟢",
            AlertSeverity.UNKNOWN: "⚪",
        }

        emoji = severity_emoji.get(self.severity, "⚠️")
        source_short = {
            AlertSource.NOAA: "NWS",
            AlertSource.USGS: "USGS",
            AlertSource.FEMA: "FEMA",
        }

        msg = f"{emoji} {source_short.get(self.source, 'EAS')}: {self.title}"

        if len(msg) < max_length - 20 and self.areas:
            area_str = ", ".join(self.areas[:2])
            if len(area_str) + len(msg) < max_length - 5:
                msg += f" [{area_str}]"

        return msg[:max_length]


# ============================================================================
# Plugin Implementation
# ============================================================================

class EASAlertsPlugin(IntegrationPlugin):
    """
    Emergency Alert System integration for MeshForge.

    Monitors NOAA, USGS, and FEMA alert feeds and broadcasts
    critical alerts to the mesh network.
    """

    def __init__(self):
        self._config: Optional[configparser.ConfigParser] = None
        self._config_path: Optional[Path] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_poll: Optional[datetime] = None
        self._seen_alerts: Dict[str, datetime] = {}
        self._alert_callbacks: List[Callable[[Alert], None]] = []
        self._current_alerts: List[Alert] = []

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="eas-alerts",
            version="1.0.0",
            description="Emergency Alert System - NOAA, FEMA iPAWS, USGS Volcano alerts",
            author="MeshForge Community",
            plugin_type=PluginType.INTEGRATION,
            dependencies=[],
            homepage="https://github.com/Nursedude/meshforge",
        )

    def _get_config_path(self) -> Path:
        """Get configuration file path."""
        if self._config_path:
            return self._config_path
        return get_real_user_home() / ".config" / "meshforge" / "plugins" / "eas_alerts.ini"

    def _load_config(self) -> configparser.ConfigParser:
        """Load or create configuration."""
        config = configparser.ConfigParser()
        config_path = self._get_config_path()

        if config_path.exists():
            try:
                config.read(config_path)
                logger.info(f"[EAS] Loaded config from {config_path}")
            except Exception as e:
                logger.error(f"[EAS] Failed to load config: {e}")
                config.read_string(EAS_CONFIG_TEMPLATE)
        else:
            # Create default config
            config.read_string(EAS_CONFIG_TEMPLATE)
            self._save_config(config)
            logger.info(f"[EAS] Created default config at {config_path}")

        return config

    def _save_config(self, config: Optional[configparser.ConfigParser] = None) -> None:
        """Save configuration to file."""
        if config is None:
            config = self._config
        if config is None:
            return

        config_path = self._get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(config_path, 'w') as f:
                config.write(f)
            logger.debug(f"[EAS] Saved config to {config_path}")
        except Exception as e:
            logger.error(f"[EAS] Failed to save config: {e}")

    def get_config_template(self) -> str:
        """Return the configuration template for documentation."""
        return EAS_CONFIG_TEMPLATE

    def activate(self) -> None:
        """Activate the EAS alerts plugin."""
        logger.info("[EAS] Activating Emergency Alert System plugin")
        self._config = self._load_config()

        if not self._config.getboolean('general', 'enabled', fallback=True):
            logger.info("[EAS] Plugin disabled in config")
            return

        # Start polling thread
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="EAS-Poll"
        )
        self._poll_thread.start()
        logger.info("[EAS] Alert polling started")

    def deactivate(self) -> None:
        """Deactivate the EAS alerts plugin."""
        logger.info("[EAS] Deactivating Emergency Alert System plugin")
        self._stop_event.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
        self._alert_callbacks.clear()
        logger.info("[EAS] Alert polling stopped")

    def connect(self) -> bool:
        """Connect to alert sources (start polling).

        For EAS, 'connected' means the polling thread is running
        and actively checking for alerts.

        Returns:
            True if polling was started successfully
        """
        if self.is_connected():
            logger.debug("[EAS] Already connected (polling active)")
            return True

        # Load config if not already loaded
        if not self._config:
            self._config = self._load_config()

        if not self._config.getboolean('general', 'enabled', fallback=True):
            logger.warning("[EAS] Plugin disabled in config, cannot connect")
            return False

        # Start polling thread
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="EAS-Poll"
        )
        self._poll_thread.start()
        logger.info("[EAS] Connected - alert polling started")
        return True

    def disconnect(self) -> None:
        """Disconnect from alert sources (stop polling)."""
        logger.info("[EAS] Disconnecting - stopping alert polling")
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        self._poll_thread = None
        logger.info("[EAS] Disconnected")

    def is_connected(self) -> bool:
        """Check if connected (polling is active).

        Returns:
            True if polling thread is running
        """
        return (
            self._poll_thread is not None
            and self._poll_thread.is_alive()
            and not self._stop_event.is_set()
        )

    def register_callback(self, callback: Callable[[Alert], None]) -> None:
        """Register a callback for new alerts."""
        self._alert_callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[Alert], None]) -> None:
        """Unregister an alert callback."""
        if callback in self._alert_callbacks:
            self._alert_callbacks.remove(callback)

    def _poll_loop(self) -> None:
        """Background polling loop."""
        interval = self._config.getint('general', 'poll_interval', fallback=300)

        # Initial check
        self._check_alerts()

        while not self._stop_event.is_set():
            self._stop_event.wait(interval)
            if not self._stop_event.is_set():
                self._check_alerts()

    def _check_alerts(self) -> None:
        """Check all enabled alert sources."""
        logger.debug("[EAS] Checking for alerts...")
        self._last_poll = datetime.now()

        all_alerts = []

        # Check NOAA Weather Alerts
        if self._config.getboolean('noaa_weather', 'enabled', fallback=True):
            try:
                noaa_alerts = self.get_weather_alerts()
                all_alerts.extend(noaa_alerts)
            except Exception as e:
                logger.error(f"[EAS] NOAA fetch error: {e}")

        # Check USGS Volcano Alerts
        if self._config.getboolean('usgs_volcano', 'enabled', fallback=True):
            try:
                volcano_alerts = self.get_volcano_alerts()
                all_alerts.extend(volcano_alerts)
            except Exception as e:
                logger.error(f"[EAS] USGS fetch error: {e}")

        # Check FEMA iPAWS Alerts
        if self._config.getboolean('fema_ipaws', 'enabled', fallback=True):
            try:
                fema_alerts = self.get_fema_alerts()
                all_alerts.extend(fema_alerts)
            except Exception as e:
                logger.error(f"[EAS] FEMA fetch error: {e}")

        # Process new alerts
        dedupe_window = self._config.getint('general', 'dedupe_window', fallback=3600)
        max_alerts = self._config.getint('general', 'max_alerts_per_check', fallback=10)

        new_alerts = []
        cutoff = datetime.now() - timedelta(seconds=dedupe_window)

        # Clean old seen alerts
        self._seen_alerts = {
            k: v for k, v in self._seen_alerts.items()
            if v > cutoff
        }

        for alert in all_alerts:
            if alert.id not in self._seen_alerts:
                new_alerts.append(alert)
                self._seen_alerts[alert.id] = datetime.now()

        if max_alerts > 0:
            new_alerts = new_alerts[:max_alerts]

        # Update current alerts
        self._current_alerts = all_alerts

        # Notify callbacks
        for alert in new_alerts:
            logger.info(f"[EAS] New alert: {alert.title} ({alert.source.value})")
            for callback in self._alert_callbacks:
                try:
                    callback(alert)
                except Exception as e:
                    logger.error(f"[EAS] Callback error: {e}")

        if new_alerts:
            logger.info(f"[EAS] Found {len(new_alerts)} new alerts")

    # ========================================================================
    # NOAA Weather Alerts
    # ========================================================================

    def get_weather_alerts(self) -> List[Alert]:
        """
        Fetch weather alerts from NOAA/NWS.

        API: https://api.weather.gov/alerts/active
        """
        alerts = []

        # Build URL based on config
        lat = self._config.getfloat('location', 'latitude', fallback=0)
        lon = self._config.getfloat('location', 'longitude', fallback=0)
        state = self._config.get('location', 'state', fallback='')

        if lat and lon:
            url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
        elif state:
            url = f"https://api.weather.gov/alerts/active?area={state}"
        else:
            url = "https://api.weather.gov/alerts/active"

        try:
            user_agent = self._config.get('general', 'user_agent', fallback='MeshForge-EAS/1.0')
            req = urllib.request.Request(url)
            req.add_header('User-Agent', user_agent)
            req.add_header('Accept', 'application/geo+json')

            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode('utf-8'))

            features = data.get('features', [])

            # Parse severity/certainty/urgency filters
            severity_filter = self._config.get('noaa_weather', 'severity_filter', fallback='Extreme,Severe')
            severity_list = [s.strip() for s in severity_filter.split(',')]

            ignore_words = self._config.get('noaa_weather', 'ignore_words', fallback='test,exercise').lower()
            ignore_list = [w.strip() for w in ignore_words.split(',')]

            for feature in features:
                props = feature.get('properties', {})

                # Apply filters
                severity = props.get('severity', 'Unknown')
                if severity not in severity_list:
                    continue

                headline = props.get('headline', '').lower()
                if any(word in headline for word in ignore_list if word):
                    continue

                # Parse alert
                alert = Alert(
                    id=props.get('id', ''),
                    source=AlertSource.NOAA,
                    title=props.get('headline', props.get('event', 'Weather Alert')),
                    description=props.get('description', '')[:500],
                    severity=self._parse_severity(severity),
                    event_type=props.get('event', ''),
                    effective=self._parse_datetime(props.get('effective')),
                    expires=self._parse_datetime(props.get('expires')),
                    areas=props.get('areaDesc', '').split('; '),
                    raw_data=props,
                )
                alerts.append(alert)

            logger.debug(f"[EAS] NOAA: {len(alerts)} alerts after filtering")

        except urllib.error.HTTPError as e:
            logger.error(f"[EAS] NOAA HTTP error: {e.code}")
        except urllib.error.URLError as e:
            logger.error(f"[EAS] NOAA URL error: {e.reason}")
        except Exception as e:
            logger.error(f"[EAS] NOAA error: {e}")

        return alerts

    # ========================================================================
    # USGS Volcano Alerts
    # ========================================================================

    def get_volcano_alerts(self) -> List[Alert]:
        """
        Fetch volcano alerts from USGS.

        API: https://volcanoes.usgs.gov/hans-public/api/volcano/getElevatedVolcanoes
        """
        alerts = []

        url = "https://volcanoes.usgs.gov/hans-public/api/volcano/getElevatedVolcanoes"

        try:
            user_agent = self._config.get('general', 'user_agent', fallback='MeshForge-EAS/1.0')
            req = urllib.request.Request(url)
            req.add_header('User-Agent', user_agent)

            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode('utf-8'))

            # Parse filters
            level_filter = self._config.get('usgs_volcano', 'level_filter', fallback='WARNING,WATCH,ADVISORY')
            level_list = [l.strip().upper() for l in level_filter.split(',')]

            color_filter = self._config.get('usgs_volcano', 'color_filter', fallback='RED,ORANGE,YELLOW')
            color_list = [c.strip().upper() for c in color_filter.split(',')]

            volcano_list = self._config.get('usgs_volcano', 'volcano_list', fallback='')
            specific_volcanoes = [v.strip() for v in volcano_list.split(',') if v.strip()]

            for volcano in data:
                # Filter by specific volcanoes if configured
                vnum = str(volcano.get('vnum', ''))
                if specific_volcanoes and vnum not in specific_volcanoes:
                    continue

                # Filter by alert level
                alert_level = volcano.get('alert_level', '').upper()
                color_code = volcano.get('color_code', '').upper()

                if alert_level not in level_list and color_code not in color_list:
                    continue

                # Map color to severity
                severity_map = {
                    'RED': AlertSeverity.EXTREME,
                    'ORANGE': AlertSeverity.SEVERE,
                    'YELLOW': AlertSeverity.MODERATE,
                    'GREEN': AlertSeverity.MINOR,
                }
                severity = severity_map.get(color_code, AlertSeverity.UNKNOWN)

                # Get volcano name and build alert
                name = volcano.get('volcano_name', 'Unknown Volcano')

                alert = Alert(
                    id=f"usgs-{vnum}-{volcano.get('current_status_timestamp', '')}",
                    source=AlertSource.USGS,
                    title=f"Volcano Alert: {name} ({color_code}/{alert_level})",
                    description=volcano.get('current_status', ''),
                    severity=severity,
                    event_type=f"Volcano-{alert_level}",
                    effective=self._parse_datetime(volcano.get('current_status_timestamp')),
                    areas=[volcano.get('state', '')],
                    coordinates=(
                        volcano.get('latitude'),
                        volcano.get('longitude')
                    ) if volcano.get('latitude') else None,
                    raw_data=volcano,
                )
                alerts.append(alert)

            logger.debug(f"[EAS] USGS: {len(alerts)} volcano alerts")

        except urllib.error.HTTPError as e:
            logger.error(f"[EAS] USGS HTTP error: {e.code}")
        except urllib.error.URLError as e:
            logger.error(f"[EAS] USGS URL error: {e.reason}")
        except Exception as e:
            logger.error(f"[EAS] USGS error: {e}")

        return alerts

    # ========================================================================
    # FEMA iPAWS Alerts
    # ========================================================================

    def get_fema_alerts(self) -> List[Alert]:
        """
        Fetch alerts from FEMA OpenFEMA API.

        API: https://www.fema.gov/api/open/v1/IpawsArchivedAlerts

        Note: This is archived alerts (24h delay). For live alerts,
        registration at IPAWS User Portal is required.
        """
        alerts = []

        if not self._config.getboolean('fema_ipaws', 'use_archived', fallback=True):
            logger.debug("[EAS] FEMA archived alerts disabled")
            return alerts

        # Build query for recent alerts
        fips_codes = self._config.get('location', 'fips_codes', fallback='')
        same_codes = self._config.get('location', 'same_codes', fallback='')
        history_days = self._config.getint('fema_ipaws', 'history_days', fallback=1)

        # Calculate date filter
        since_date = (datetime.now() - timedelta(days=history_days)).strftime('%Y-%m-%d')

        # Build URL with properly encoded parameters
        # Spaces in OData queries must be URL-encoded
        import urllib.parse
        base_url = "https://www.fema.gov/api/open/v1/IpawsArchivedAlerts"
        params = {
            '$filter': f"sent ge '{since_date}'",
            '$top': '50',
            '$orderby': 'sent desc'
        }

        # Add FIPS filter if configured
        if fips_codes:
            fips_list = [f.strip() for f in fips_codes.split(',')]
            # Note: FEMA API may have different filter syntax
            # This is a simplified example

        safe_chars = "'"
        url = f"{base_url}?{urllib.parse.urlencode(params, safe=safe_chars)}"

        try:
            user_agent = self._config.get('general', 'user_agent', fallback='MeshForge-EAS/1.0')
            req = urllib.request.Request(url)
            req.add_header('User-Agent', user_agent)

            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))

            records = data.get('IpawsArchivedAlerts', [])

            # Parse filters
            ignore_words = self._config.get('fema_ipaws', 'ignore_words', fallback='test,exercise').lower()
            ignore_list = [w.strip() for w in ignore_words.split(',')]

            event_types = self._config.get('fema_ipaws', 'event_types', fallback='')
            event_filter = [e.strip() for e in event_types.split(',') if e.strip()]

            for record in records:
                headline = record.get('headline', '').lower()

                # Apply ignore filter
                if any(word in headline for word in ignore_list if word):
                    continue

                # Apply event type filter
                event_code = record.get('eventCode', '')
                if event_filter and event_code not in event_filter:
                    continue

                # Map severity
                severity_str = record.get('severity', 'Unknown')
                severity = self._parse_severity(severity_str)

                alert = Alert(
                    id=record.get('id', ''),
                    source=AlertSource.FEMA,
                    title=record.get('headline', record.get('event', 'FEMA Alert')),
                    description=record.get('description', '')[:500],
                    severity=severity,
                    event_type=event_code,
                    effective=self._parse_datetime(record.get('effective')),
                    expires=self._parse_datetime(record.get('expires')),
                    areas=[record.get('areaDesc', '')],
                    raw_data=record,
                )
                alerts.append(alert)

            logger.debug(f"[EAS] FEMA: {len(alerts)} alerts")

        except urllib.error.HTTPError as e:
            logger.error(f"[EAS] FEMA HTTP error: {e.code}")
        except urllib.error.URLError as e:
            logger.error(f"[EAS] FEMA URL error: {e.reason}")
        except Exception as e:
            logger.error(f"[EAS] FEMA error: {e}")

        return alerts

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def _parse_severity(self, severity_str: str) -> AlertSeverity:
        """Parse severity string to enum."""
        severity_map = {
            'extreme': AlertSeverity.EXTREME,
            'severe': AlertSeverity.SEVERE,
            'moderate': AlertSeverity.MODERATE,
            'minor': AlertSeverity.MINOR,
        }
        return severity_map.get(severity_str.lower(), AlertSeverity.UNKNOWN)

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime string."""
        if not dt_str:
            return None
        try:
            # Handle various ISO formats
            # Replace Z with UTC offset for fromisoformat
            dt_str = dt_str.replace('Z', '+00:00')
            # Strip timezone for naive datetime (consistent comparison)
            if '+' in dt_str:
                dt_str = dt_str.split('+')[0]
            if '-' in dt_str and dt_str.count('-') > 2:
                # Handle negative offset like -05:00
                parts = dt_str.rsplit('-', 1)
                if len(parts[1]) <= 6:  # Timezone part
                    dt_str = parts[0]
            return datetime.fromisoformat(dt_str)
        except (ValueError, AttributeError) as e:
            logger.debug(f"[EAS] Failed to parse datetime '{dt_str}': {e}")
            return None

    def check_all_alerts(self) -> List[Alert]:
        """Manually check all alert sources and return results."""
        self._check_alerts()
        return self._current_alerts.copy()

    def get_current_alerts(self) -> List[Alert]:
        """Get currently cached alerts."""
        return self._current_alerts.copy()

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            'enabled': self._config.getboolean('general', 'enabled', fallback=True) if self._config else False,
            'last_poll': self._last_poll.isoformat() if self._last_poll else None,
            'current_alert_count': len(self._current_alerts),
            'seen_alerts_count': len(self._seen_alerts),
            'callback_count': len(self._alert_callbacks),
            'sources': {
                'noaa': self._config.getboolean('noaa_weather', 'enabled', fallback=True) if self._config else False,
                'usgs': self._config.getboolean('usgs_volcano', 'enabled', fallback=True) if self._config else False,
                'fema': self._config.getboolean('fema_ipaws', 'enabled', fallback=True) if self._config else False,
            }
        }

    def is_enabled(self) -> bool:
        """Check if plugin is enabled."""
        if not self._config:
            return False
        return self._config.getboolean('general', 'enabled', fallback=True)


# ============================================================================
# Convenience Functions
# ============================================================================

def create_default_config(path: Optional[Path] = None) -> Path:
    """
    Create default configuration file.

    Args:
        path: Optional custom path for config file

    Returns:
        Path to created config file
    """
    if path is None:
        path = get_real_user_home() / ".config" / "meshforge" / "plugins" / "eas_alerts.ini"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EAS_CONFIG_TEMPLATE)

    logger.info(f"[EAS] Created default config at {path}")
    return path


def get_quick_alerts(
    lat: float = 0,
    lon: float = 0,
    state: str = "",
    timeout: int = 15
) -> List[Dict[str, Any]]:
    """
    Quick function to fetch current alerts without full plugin setup.

    Args:
        lat: Latitude for point-based query
        lon: Longitude for point-based query
        state: State code for area-based query
        timeout: Request timeout in seconds

    Returns:
        List of alert dictionaries
    """
    alerts = []

    # Fetch NOAA alerts
    if lat and lon:
        url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
    elif state:
        url = f"https://api.weather.gov/alerts/active?area={state}"
    else:
        url = "https://api.weather.gov/alerts/active?status=actual&message_type=alert"

    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge-EAS/1.0')
        req.add_header('Accept', 'application/geo+json')

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))

        for feature in data.get('features', []):
            props = feature.get('properties', {})
            alerts.append({
                'source': 'NOAA',
                'title': props.get('headline', props.get('event', 'Alert')),
                'severity': props.get('severity', 'Unknown'),
                'event': props.get('event', ''),
                'expires': props.get('expires', ''),
            })
    except Exception as e:
        logger.error(f"[EAS] Quick fetch error: {e}")

    return alerts
