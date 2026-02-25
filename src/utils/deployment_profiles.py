"""
MeshForge Deployment Profiles

Defines 5 deployment scenarios so users can run MeshForge in their
chosen configuration without irrelevant dependencies blocking them.

Profiles:
    radio_maps  - meshtasticd + coverage mapping, RF tools
    monitor     - MQTT packet analysis, no radio needed
    meshcore    - MeshCore companion radio integration
    gateway     - Full Meshtastic <> RNS bridge
    full        - Everything including MQTT broker

Usage:
    from utils.deployment_profiles import load_or_detect_profile

    profile = load_or_detect_profile()
    print(profile.display_name)
    print(profile.validate())
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Service checker (optional — profile module should work standalone)
_check_service, _ServiceState, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'ServiceState'
)


class ProfileName(Enum):
    """Deployment profile identifiers."""
    RADIO_MAPS = "radio_maps"
    MONITOR = "monitor"
    MESHCORE = "meshcore"
    MESHCHAT = "meshchat"
    GATEWAY = "gateway"
    FULL = "full"


@dataclass
class ProfileDefinition:
    """Definition of a deployment profile."""
    name: ProfileName
    display_name: str
    description: str
    required_services: List[str]
    optional_services: List[str]
    required_packages: List[str]
    optional_packages: List[str]
    feature_flags: Dict[str, bool]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON persistence."""
        return {
            'name': self.name.value,
            'display_name': self.display_name,
            'description': self.description,
        }


@dataclass
class ProfileHealth:
    """Result of validating a profile against the current system."""
    profile: ProfileDefinition
    missing_services: List[str] = field(default_factory=list)
    missing_packages: List[str] = field(default_factory=list)
    available_services: List[str] = field(default_factory=list)
    available_packages: List[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """All required services and packages present."""
        return not self.missing_services and not self.missing_packages

    @property
    def summary(self) -> str:
        """Human-readable health summary."""
        if self.ready:
            return f"{self.profile.display_name}: Ready"
        parts = []
        if self.missing_services:
            parts.append(f"services: {', '.join(self.missing_services)}")
        if self.missing_packages:
            parts.append(f"packages: {', '.join(self.missing_packages)}")
        return f"{self.profile.display_name}: Missing {'; '.join(parts)}"


# ============================================================================
# Profile Definitions
# ============================================================================

PROFILES: Dict[ProfileName, ProfileDefinition] = {
    ProfileName.RADIO_MAPS: ProfileDefinition(
        name=ProfileName.RADIO_MAPS,
        display_name="Radio + Maps",
        description="Meshtastic radio configuration and coverage mapping",
        required_services=["meshtasticd"],
        optional_services=[],
        required_packages=["rich", "yaml", "requests", "folium"],
        optional_packages=["psutil", "distro"],
        feature_flags={
            "meshtastic": True,
            "meshcore": False,
            "rns": False,
            "gateway": False,
            "meshchat": False,
            "mqtt": False,
            "maps": True,
            "tactical": False,
        },
    ),
    ProfileName.MONITOR: ProfileDefinition(
        name=ProfileName.MONITOR,
        display_name="Monitor",
        description="MQTT packet analysis and traffic inspection (no radio required)",
        required_services=[],
        optional_services=["mosquitto", "meshtasticd"],
        required_packages=["rich", "yaml", "requests", "paho"],
        optional_packages=["psutil", "websockets"],
        feature_flags={
            "meshtastic": False,
            "meshcore": False,
            "rns": False,
            "gateway": False,
            "meshchat": False,
            "mqtt": True,
            "maps": False,
            "tactical": False,
        },
    ),
    ProfileName.MESHCORE: ProfileDefinition(
        name=ProfileName.MESHCORE,
        display_name="MeshCore",
        description="MeshCore companion radio integration",
        required_services=[],
        optional_services=["meshtasticd"],
        required_packages=["rich", "yaml", "requests"],
        optional_packages=["psutil"],
        feature_flags={
            "meshtastic": True,
            "meshcore": True,
            "rns": False,
            "gateway": False,
            "meshchat": False,
            "mqtt": False,
            "maps": False,
            "tactical": False,
        },
    ),
    ProfileName.MESHCHAT: ProfileDefinition(
        name=ProfileName.MESHCHAT,
        display_name="MeshChat",
        description="Meshtastic <> RNS bridge with MeshChat LXMF messaging",
        required_services=["meshtasticd", "rnsd"],
        optional_services=["reticulum-meshchat", "meshchat"],
        required_packages=["rich", "yaml", "requests", "RNS", "LXMF"],
        optional_packages=["paho", "psutil", "folium"],
        feature_flags={
            "meshtastic": True,
            "meshcore": False,
            "rns": True,
            "gateway": True,
            "meshchat": True,
            "mqtt": False,
            "maps": True,
            "tactical": False,
        },
    ),
    ProfileName.GATEWAY: ProfileDefinition(
        name=ProfileName.GATEWAY,
        display_name="Gateway",
        description="Full Meshtastic <> RNS bridge with message routing",
        required_services=["meshtasticd", "rnsd"],
        optional_services=["mosquitto"],
        required_packages=["rich", "yaml", "requests", "RNS", "LXMF", "paho"],
        optional_packages=["websockets", "psutil", "folium"],
        feature_flags={
            "meshtastic": True,
            "meshcore": False,
            "rns": True,
            "gateway": True,
            "meshchat": False,
            "mqtt": True,
            "maps": True,
            "tactical": True,
        },
    ),
    ProfileName.FULL: ProfileDefinition(
        name=ProfileName.FULL,
        display_name="Full Stack",
        description="All features enabled including MQTT broker",
        required_services=["meshtasticd", "rnsd", "mosquitto"],
        optional_services=[],
        required_packages=[
            "rich", "yaml", "requests", "RNS", "LXMF", "paho",
            "folium", "websockets", "psutil", "distro",
        ],
        optional_packages=[],
        feature_flags={
            "meshtastic": True,
            "meshcore": True,
            "rns": True,
            "gateway": True,
            "meshchat": True,
            "mqtt": True,
            "maps": True,
            "tactical": True,
        },
    ),
}


# ============================================================================
# Package Detection
# ============================================================================

# Map import names to the actual module to try importing
_PACKAGE_IMPORT_MAP = {
    "rich": "rich",
    "yaml": "yaml",
    "requests": "requests",
    "folium": "folium",
    "RNS": "RNS",
    "LXMF": "LXMF",
    "paho": "paho.mqtt.client",
    "websockets": "websockets",
    "psutil": "psutil",
    "distro": "distro",
    "meshcore": "meshcore",
}


def _check_package(name: str) -> bool:
    """Check if a Python package is importable."""
    import_name = _PACKAGE_IMPORT_MAP.get(name, name)
    _, available = safe_import(import_name)
    return available


def _check_service_available(name: str) -> bool:
    """Check if a system service is running."""
    if not _HAS_SERVICE_CHECK:
        logger.debug("service_check not available, skipping service detection")
        return False
    try:
        status = _check_service(name)
        return status.available
    except Exception as e:
        logger.debug("Service check failed for %s: %s", name, e)
        return False


# ============================================================================
# Profile Validation
# ============================================================================

def validate_profile(profile: ProfileDefinition) -> ProfileHealth:
    """Validate a profile against the current system state.

    Checks required services and packages, returns a ProfileHealth
    indicating what is present vs missing.
    """
    health = ProfileHealth(profile=profile)

    for svc in profile.required_services:
        if _check_service_available(svc):
            health.available_services.append(svc)
        else:
            health.missing_services.append(svc)

    for pkg in profile.required_packages:
        if _check_package(pkg):
            health.available_packages.append(pkg)
        else:
            health.missing_packages.append(pkg)

    return health


# ============================================================================
# Profile Detection
# ============================================================================

def detect_profile() -> ProfileDefinition:
    """Auto-detect the best profile based on running services and installed packages.

    Detection priority (most specific first):
    1. Full — all 3 services running
    2. MeshChat — meshtasticd + rnsd + MeshChat port 8000
    3. Gateway — meshtasticd + rnsd running
    4. MeshCore — meshcore package available
    5. Monitor — paho-mqtt available, no radio services
    6. Radio+Maps — meshtasticd running (fallback)

    Returns the best-fit profile, defaulting to radio_maps.
    """
    has_meshtasticd = _check_service_available("meshtasticd")
    has_rnsd = _check_service_available("rnsd")
    has_mosquitto = _check_service_available("mosquitto")

    # Full stack: all 3 services
    if has_meshtasticd and has_rnsd and has_mosquitto:
        logger.info("Auto-detected profile: full (all services running)")
        return PROFILES[ProfileName.FULL]

    # Gateway + MeshChat: meshtasticd + rnsd + MeshChat port 8000
    if has_meshtasticd and has_rnsd:
        try:
            from utils.service_check import check_port
            if check_port(8000, '127.0.0.1'):
                logger.info("Auto-detected profile: meshchat (gateway + meshchat)")
                return PROFILES[ProfileName.MESHCHAT]
        except Exception:
            pass
        logger.info("Auto-detected profile: gateway (meshtasticd + rnsd)")
        return PROFILES[ProfileName.GATEWAY]

    # MeshCore: meshcore package available
    if _check_package("meshcore"):
        logger.info("Auto-detected profile: meshcore (meshcore package found)")
        return PROFILES[ProfileName.MESHCORE]

    # Monitor: paho available, no meshtasticd
    if not has_meshtasticd and _check_package("paho"):
        logger.info("Auto-detected profile: monitor (no radio, MQTT available)")
        return PROFILES[ProfileName.MONITOR]

    # Default: radio + maps
    logger.info("Auto-detected profile: radio_maps (default)")
    return PROFILES[ProfileName.RADIO_MAPS]


# ============================================================================
# Profile Persistence
# ============================================================================

_PROFILE_PATH = get_real_user_home() / ".config" / "meshforge" / "deployment.json"


def save_profile(profile: ProfileDefinition) -> bool:
    """Save profile selection to disk."""
    try:
        _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"profile": profile.name.value}
        with open(_PROFILE_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info("Saved deployment profile: %s", profile.display_name)
        return True
    except (IOError, OSError) as e:
        logger.warning("Failed to save deployment profile: %s", e)
        return False


def load_profile() -> Optional[ProfileDefinition]:
    """Load saved profile from disk. Returns None if no saved profile."""
    if not _PROFILE_PATH.exists():
        return None
    try:
        with open(_PROFILE_PATH, 'r') as f:
            data = json.load(f)
        name_str = data.get("profile")
        if not name_str:
            return None
        profile_name = ProfileName(name_str)
        logger.info("Loaded saved profile: %s", profile_name.value)
        return PROFILES[profile_name]
    except (json.JSONDecodeError, IOError, ValueError, KeyError) as e:
        logger.warning("Failed to load deployment profile: %s", e)
        return None


def load_or_detect_profile() -> ProfileDefinition:
    """Load saved profile, or auto-detect if none saved."""
    saved = load_profile()
    if saved is not None:
        return saved
    return detect_profile()


def get_profile_by_name(name: str) -> Optional[ProfileDefinition]:
    """Look up a profile by string name. Returns None if not found."""
    try:
        return PROFILES[ProfileName(name)]
    except (ValueError, KeyError):
        return None


def list_profiles() -> List[ProfileDefinition]:
    """Return all available profiles in display order."""
    return [
        PROFILES[ProfileName.RADIO_MAPS],
        PROFILES[ProfileName.MONITOR],
        PROFILES[ProfileName.MESHCORE],
        PROFILES[ProfileName.MESHCHAT],
        PROFILES[ProfileName.GATEWAY],
        PROFILES[ProfileName.FULL],
    ]
