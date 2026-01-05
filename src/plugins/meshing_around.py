"""
Meshing-Around Bot Integration Plugin for MeshForge.

Integrates with the meshing-around bot framework for advanced
Meshtastic automation, games, and services.

See: https://github.com/SpudGunMan/meshing-around

Features:
- Automated responses (ping/pong)
- Store-and-forward messaging
- Weather/earthquake alerts (NOAA, FEMA)
- Games (DopeWars, Blackjack)
- Ham radio quiz
- Asset tracking
- LLM integration (Ollama)

Usage:
    manager = PluginManager()
    manager.register(MeshingAroundPlugin)
    manager.activate("meshing-around")
"""

import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from utils.plugins import (
    IntegrationPlugin,
    PluginMetadata,
    PluginType,
)

logger = logging.getLogger(__name__)


class MeshingAroundPlugin(IntegrationPlugin):
    """Meshing-Around bot integration for MeshForge."""

    def __init__(self):
        self._connected = False
        self._process = None
        self._bot_dir: Optional[Path] = None

    @staticmethod
    def get_metadata() -> PluginMetadata:
        return PluginMetadata(
            name="meshing-around",
            version="0.1.0",
            description="Meshtastic bot framework - games, alerts, automation",
            author="SpudGunMan",
            plugin_type=PluginType.INTEGRATION,
            dependencies=["meshtastic"],
            homepage="https://github.com/SpudGunMan/meshing-around",
        )

    def activate(self) -> None:
        """Activate meshing-around integration."""
        logger.info("Meshing-Around plugin activated")

        # Check if meshing-around is installed
        self._bot_dir = self._find_bot_installation()
        if self._bot_dir:
            logger.info(f"Found meshing-around at: {self._bot_dir}")
        else:
            logger.warning("meshing-around not found. Install from GitHub.")

    def deactivate(self) -> None:
        """Deactivate meshing-around integration."""
        self.disconnect()
        logger.info("Meshing-Around plugin deactivated")

    def _find_bot_installation(self) -> Optional[Path]:
        """Find meshing-around installation."""
        search_paths = [
            Path.home() / "meshing-around",
            Path.home() / "src" / "meshing-around",
            Path("/opt/meshing-around"),
            Path.home() / ".local" / "share" / "meshing-around",
        ]

        for path in search_paths:
            if (path / "mesh_bot.py").exists():
                return path

        return None

    def connect(self) -> bool:
        """Start the meshing-around bot."""
        if not self._bot_dir:
            logger.error("meshing-around not installed")
            return False

        try:
            # Start the bot process
            bot_script = self._bot_dir / "mesh_bot.py"

            self._process = subprocess.Popen(
                ["python3", str(bot_script)],
                cwd=str(self._bot_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self._connected = True
            logger.info("Started meshing-around bot")
            return True

        except Exception as e:
            logger.error(f"Failed to start meshing-around: {e}")
            return False

    def disconnect(self) -> None:
        """Stop the meshing-around bot."""
        if self._process:
            self._process.terminate()
            self._process.wait(timeout=5)
            self._process = None
        self._connected = False
        logger.info("Stopped meshing-around bot")

    def is_connected(self) -> bool:
        """Check if bot is running."""
        if self._process:
            return self._process.poll() is None
        return False

    def send(self, data: Dict[str, Any]) -> bool:
        """Send command to bot (placeholder)."""
        # The bot handles messages directly via Meshtastic
        # This could be extended for inter-process communication
        return False

    def get_available_modules(self) -> list:
        """Get list of available bot modules."""
        return [
            "ping_pong",
            "store_forward",
            "weather",
            "earthquakes",
            "dopewars",
            "blackjack",
            "ham_quiz",
            "asset_tracking",
            "ollama_llm",
            "emergency_alerts",
        ]

    def install_bot(self) -> bool:
        """Install meshing-around from GitHub."""
        try:
            install_dir = Path.home() / "meshing-around"

            if install_dir.exists():
                logger.info("meshing-around already installed")
                self._bot_dir = install_dir
                return True

            logger.info("Cloning meshing-around from GitHub...")
            subprocess.run([
                "git", "clone",
                "https://github.com/SpudGunMan/meshing-around.git",
                str(install_dir)
            ], check=True)

            logger.info("Installing dependencies...")
            subprocess.run([
                "pip3", "install", "-r",
                str(install_dir / "requirements.txt")
            ], check=True)

            self._bot_dir = install_dir
            logger.info("meshing-around installed successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to install meshing-around: {e}")
            return False
