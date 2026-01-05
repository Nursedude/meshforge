"""
MeshForge Edition Detection and Feature Gating

Determines which edition is running and provides feature flags
for conditional functionality.

Editions:
- PRO: Full-featured desktop (default)
- Amateur: Ham radio focused
- .io: Lightweight plugin-based
"""

import os
from pathlib import Path
from enum import Enum
from typing import Dict, Set, Optional
import json
import logging

logger = logging.getLogger(__name__)


class Edition(Enum):
    """MeshForge edition identifiers"""
    PRO = "pro"
    AMATEUR = "amateur"
    IO = "io"

    @property
    def display_name(self) -> str:
        """Human-readable edition name"""
        names = {
            Edition.PRO: "MeshForge PRO",
            Edition.AMATEUR: "MeshForge Amateur Radio",
            Edition.IO: "MeshForge.io",
        }
        return names.get(self, "MeshForge")

    @property
    def tagline(self) -> str:
        """Edition tagline"""
        taglines = {
            Edition.PRO: "Professional Mesh Management",
            Edition.AMATEUR: "When All Else Fails",
            Edition.IO: "Mesh Made Simple",
        }
        return taglines.get(self, "")


# Feature definitions per edition
EDITION_FEATURES: Dict[Edition, Set[str]] = {
    Edition.PRO: {
        # Core features
        "dashboard",
        "service_management",
        "config_editor",
        "radio_config",
        "hardware_detection",
        "cli_interface",
        # Advanced features
        "rns_integration",
        "aredn_integration",
        "mqtt_integration",
        "api_access",
        # Visualization
        "node_map",
        "node_map_offline",
        "node_map_topo",
        "network_topology",
        # Tools
        "rf_calculators",
        "fresnel_calculator",
        "link_budget",
        "antenna_planning",
        # Learning
        "university_full",
        "assessments",
        "certifications",
        # Integration
        "hamclock",
        "plugins",
        "webhooks",
        # Advanced
        "multi_node",
        "fleet_management",
        "enterprise_logging",
    },

    Edition.AMATEUR: {
        # Core features (shared)
        "dashboard",
        "service_management",
        "config_editor",
        "radio_config",
        "hardware_detection",
        "cli_interface",
        # Advanced features
        "rns_integration",
        "aredn_integration",
        # Visualization
        "node_map",
        "node_map_offline",
        "node_map_topo",
        # Tools
        "rf_calculators",
        "fresnel_calculator",
        "link_budget",
        "antenna_planning",
        # Learning
        "university_full",
        "university_ham_track",
        "assessments",
        # Integration
        "hamclock",
        "plugins",
        # Ham-specific
        "callsign_management",
        "ares_races_tools",
        "band_plan_reference",
        "traffic_handling",
        "aprs_gateway",
        "winlink_interface",
        "contest_mode",
    },

    Edition.IO: {
        # Core features (minimal)
        "dashboard_basic",
        "config_basic",
        "radio_config_basic",
        # Visualization (basic)
        "node_map_basic",
        # Tools (basic)
        "rf_calculators_basic",
        # Learning (intro only)
        "university_intro",
        # Key differentiator
        "plugins",
        "plugin_marketplace",
        "web_interface",
    },
}


# Cached edition
_cached_edition: Optional[Edition] = None


def detect_edition() -> Edition:
    """
    Detect which MeshForge edition is running.

    Detection priority:
    1. Environment variable MESHFORGE_EDITION
    2. Config file ~/.config/meshforge/edition.json
    3. Presence of edition marker files
    4. Default to PRO

    Returns:
        Edition enum value
    """
    global _cached_edition

    if _cached_edition is not None:
        return _cached_edition

    # 1. Check environment variable
    env_edition = os.environ.get("MESHFORGE_EDITION", "").lower()
    if env_edition in ["pro", "amateur", "io"]:
        logger.debug(f"Edition from environment: {env_edition}")
        _cached_edition = Edition(env_edition)
        return _cached_edition

    # 2. Check config file
    config_dir = Path.home() / ".config" / "meshforge"
    edition_file = config_dir / "edition.json"

    if edition_file.exists():
        try:
            data = json.loads(edition_file.read_text())
            edition_str = data.get("edition", "").lower()
            if edition_str in ["pro", "amateur", "io"]:
                logger.debug(f"Edition from config: {edition_str}")
                _cached_edition = Edition(edition_str)
                return _cached_edition
        except (json.JSONDecodeError, KeyError, IOError) as e:
            logger.warning(f"Failed to read edition config: {e}")

    # 3. Check marker files
    src_dir = Path(__file__).parent.parent

    if (src_dir / ".amateur_edition").exists():
        logger.debug("Amateur edition marker found")
        _cached_edition = Edition.AMATEUR
        return _cached_edition

    if (src_dir / ".io_edition").exists():
        logger.debug(".io edition marker found")
        _cached_edition = Edition.IO
        return _cached_edition

    # 4. Default to PRO
    logger.debug("Defaulting to PRO edition")
    _cached_edition = Edition.PRO
    return _cached_edition


def set_edition(edition: Edition) -> None:
    """
    Set the current edition (persists to config).

    Args:
        edition: Edition to set
    """
    global _cached_edition

    config_dir = Path.home() / ".config" / "meshforge"
    config_dir.mkdir(parents=True, exist_ok=True)

    edition_file = config_dir / "edition.json"
    data = {
        "edition": edition.value,
        "display_name": edition.display_name,
    }
    edition_file.write_text(json.dumps(data, indent=2))

    _cached_edition = edition
    logger.info(f"Edition set to: {edition.value}")


def get_edition_features(edition: Optional[Edition] = None) -> Set[str]:
    """
    Get the feature set for an edition.

    Args:
        edition: Edition to check (defaults to current)

    Returns:
        Set of feature identifiers
    """
    if edition is None:
        edition = detect_edition()
    return EDITION_FEATURES.get(edition, set())


def has_feature(feature: str, edition: Optional[Edition] = None) -> bool:
    """
    Check if a feature is available in the current/specified edition.

    Args:
        feature: Feature identifier to check
        edition: Edition to check (defaults to current)

    Returns:
        True if feature is available
    """
    features = get_edition_features(edition)
    return feature in features


def require_feature(feature: str) -> None:
    """
    Decorator/function to require a feature.

    Raises:
        FeatureNotAvailableError: If feature not available
    """
    if not has_feature(feature):
        edition = detect_edition()
        raise FeatureNotAvailableError(
            f"Feature '{feature}' is not available in {edition.display_name}. "
            f"Consider upgrading to MeshForge PRO."
        )


class FeatureNotAvailableError(Exception):
    """Raised when a feature is not available in current edition"""
    pass


def feature_gate(feature: str):
    """
    Decorator to gate a function behind a feature flag.

    Usage:
        @feature_gate("rns_integration")
        def connect_to_rns():
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            require_feature(feature)
            return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


# Edition info for display
def get_edition_info() -> Dict:
    """
    Get comprehensive edition information for display.

    Returns:
        Dict with edition details
    """
    edition = detect_edition()
    features = get_edition_features()

    return {
        "edition": edition.value,
        "display_name": edition.display_name,
        "tagline": edition.tagline,
        "feature_count": len(features),
        "features": sorted(features),
        "is_pro": edition == Edition.PRO,
        "is_amateur": edition == Edition.AMATEUR,
        "is_io": edition == Edition.IO,
        "supports_plugins": "plugins" in features,
        "upgrade_available": edition != Edition.PRO,
    }
