"""
MeshForge Quick Start Modes

Pre-configured modes for common use cases:
- Monitor: Just watch the mesh (no radio config needed)
- Node: Single node setup (Meshtastic only)
- Gateway: Bridge Meshtastic and RNS
- NOC: Full network operations center

Usage:
    from utils.quick_start import QUICK_START_MODES, apply_mode

    for mode in QUICK_START_MODES:
        print(f"{mode['name']}: {mode['description']}")

    apply_mode('gateway')
"""

import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from pathlib import Path

from utils.service_check import check_service

logger = logging.getLogger(__name__)


@dataclass
class QuickStartMode:
    """A quick start mode configuration."""
    id: str
    name: str
    description: str
    icon: str
    services_required: List[str]
    services_optional: List[str]
    features: List[str]
    setup_steps: List[str]


# Define the quick start modes
QUICK_START_MODES = [
    QuickStartMode(
        id="monitor",
        name="Monitor Only",
        description="Watch mesh activity without a radio",
        icon="👁️",
        services_required=[],
        services_optional=["meshtasticd"],
        features=[
            "MQTT monitoring (nodeless)",
            "View nodes from public servers",
            "Coverage maps",
            "AI diagnostics"
        ],
        setup_steps=[
            "No radio hardware required",
            "Configure MQTT server (optional)",
            "Start MeshForge"
        ]
    ),
    QuickStartMode(
        id="node",
        name="Single Node",
        description="Personal Meshtastic node",
        icon="📡",
        services_required=["meshtasticd"],
        services_optional=[],
        features=[
            "Send/receive messages",
            "View local nodes",
            "Radio configuration",
            "Coverage maps"
        ],
        setup_steps=[
            "Connect radio (USB or SPI HAT)",
            "Start meshtasticd",
            "Configure region/preset"
        ]
    ),
    QuickStartMode(
        id="gateway",
        name="Gateway",
        description="Bridge Meshtastic and RNS networks",
        icon="🌉",
        services_required=["meshtasticd", "rnsd"],
        services_optional=[],
        features=[
            "Route messages between networks",
            "Unified node view",
            "Gateway bridge",
            "Message queue persistence"
        ],
        setup_steps=[
            "Start meshtasticd",
            "Start rnsd",
            "Configure gateway settings",
            "Start bridge"
        ]
    ),
    QuickStartMode(
        id="noc",
        name="Full NOC",
        description="Complete Network Operations Center",
        icon="🖥️",
        services_required=["meshtasticd", "rnsd"],
        services_optional=["hamclock", "mosquitto"],
        features=[
            "All gateway features",
            "Space weather (HamClock)",
            "MQTT broker",
            "Service orchestration",
            "Health monitoring",
            "AI diagnostics (PRO)"
        ],
        setup_steps=[
            "Install NOC stack: sudo bash scripts/install_noc.sh",
            "Configure services",
            "Start orchestrator"
        ]
    )
]


def get_mode_by_id(mode_id: str) -> Optional[QuickStartMode]:
    """Get a quick start mode by its ID."""
    for mode in QUICK_START_MODES:
        if mode.id == mode_id:
            return mode
    return None


def get_available_modes() -> List[QuickStartMode]:
    """
    Get list of modes available based on installed services.

    Returns all modes but marks which ones are ready vs need setup.
    """
    available = []

    for mode in QUICK_START_MODES:
        mode_copy = QuickStartMode(
            id=mode.id,
            name=mode.name,
            description=mode.description,
            icon=mode.icon,
            services_required=mode.services_required.copy(),
            services_optional=mode.services_optional.copy(),
            features=mode.features.copy(),
            setup_steps=mode.setup_steps.copy()
        )

        # Check if required services are available
        missing = []
        for svc in mode.services_required:
            status = check_service(svc)
            if not status.available:
                missing.append(svc)

        if missing:
            mode_copy.description += f" (needs: {', '.join(missing)})"

        available.append(mode_copy)

    return available


def print_mode_menu(use_color: bool = True) -> str:
    """
    Generate a menu showing available quick start modes.

    Returns formatted string for display.
    """
    if use_color:
        BOLD = "\033[1m"
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        DIM = "\033[2m"
        RESET = "\033[0m"
    else:
        BOLD = GREEN = YELLOW = DIM = RESET = ""

    lines = []
    lines.append(f"{BOLD}Quick Start Modes{RESET}")
    lines.append("")

    for i, mode in enumerate(QUICK_START_MODES, 1):
        lines.append(f"  {mode.icon} [{i}] {BOLD}{mode.name}{RESET}")
        lines.append(f"      {DIM}{mode.description}{RESET}")
        lines.append("")

    return "\n".join(lines)


def get_mode_details(mode: QuickStartMode, use_color: bool = True) -> str:
    """
    Get detailed information about a mode.

    Returns formatted string for display.
    """
    if use_color:
        BOLD = "\033[1m"
        GREEN = "\033[92m"
        DIM = "\033[2m"
        RESET = "\033[0m"
    else:
        BOLD = GREEN = DIM = RESET = ""

    lines = []
    lines.append(f"{mode.icon} {BOLD}{mode.name}{RESET}")
    lines.append(f"   {mode.description}")
    lines.append("")

    lines.append(f"{BOLD}Features:{RESET}")
    for feature in mode.features:
        lines.append(f"  {GREEN}✓{RESET} {feature}")
    lines.append("")

    if mode.services_required:
        lines.append(f"{BOLD}Required Services:{RESET}")
        for svc in mode.services_required:
            lines.append(f"  • {svc}")
        lines.append("")

    if mode.services_optional:
        lines.append(f"{BOLD}Optional Services:{RESET}")
        for svc in mode.services_optional:
            lines.append(f"  {DIM}• {svc}{RESET}")
        lines.append("")

    lines.append(f"{BOLD}Setup Steps:{RESET}")
    for i, step in enumerate(mode.setup_steps, 1):
        lines.append(f"  {i}. {step}")

    return "\n".join(lines)


def apply_mode(mode_id: str) -> Dict[str, Any]:
    """
    Apply a quick start mode configuration.

    This prepares the environment for the selected mode but doesn't
    start services (that's done by the launcher/orchestrator).

    Returns dict with:
        - success: bool
        - mode: QuickStartMode
        - services_to_start: List[str]
        - message: str
    """
    mode = get_mode_by_id(mode_id)
    if not mode:
        return {
            'success': False,
            'mode': None,
            'services_to_start': [],
            'message': f"Unknown mode: {mode_id}"
        }

    # Determine which services need to be started
    services_to_start = mode.services_required.copy()

    # Check which optional services are available
    for svc in mode.services_optional:
        # Check if service is installed (even if not running)
        status = check_service(svc)
        # Add if it's installed and would be useful
        if status.state.value != "not_installed":
            services_to_start.append(svc)

    return {
        'success': True,
        'mode': mode,
        'services_to_start': services_to_start,
        'message': f"Ready to start {mode.name} mode"
    }


# CLI entry point
if __name__ == "__main__":
    print(print_mode_menu())
    print("\n" + "=" * 50 + "\n")

    # Show details for gateway mode as example
    gateway = get_mode_by_id("gateway")
    if gateway:
        print(get_mode_details(gateway))
