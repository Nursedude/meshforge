"""
MeshForge Meshtastic CLI Wrapper

Provides reliable CLI operations with double-tap retry logic.
The meshtastic CLI sometimes fails on first attempt but succeeds on retry.
This wrapper handles that gracefully.

Usage:
    from core.meshtastic_cli import MeshtasticCLI

    cli = MeshtasticCLI()

    # Get node info
    result = cli.get_info()
    if result.success:
        print(result.data)

    # Send message
    result = cli.send_text("Hello mesh!", destination="^all")

    # Export config
    result = cli.export_config()
"""

import json
import shutil
import subprocess
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path

from utils.cli import find_meshtastic_cli

logger = logging.getLogger(__name__)


@dataclass
class CLIResult:
    """Result from a CLI operation."""
    success: bool
    output: str = ""
    error: str = ""
    data: Optional[Any] = None  # Parsed data if applicable
    attempts: int = 1
    command: List[str] = field(default_factory=list)


class MeshtasticCLI:
    """
    Wrapper for meshtastic CLI with double-tap retry logic.

    Handles the common pattern where CLI fails on first attempt
    but succeeds on retry (usually due to device timing).
    """

    MAX_RETRIES = 3
    RETRY_DELAYS = [1, 2, 3]  # seconds between retries
    DEFAULT_TIMEOUT = 30  # seconds

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4403,
        device: Optional[str] = None,
        cli_path: Optional[str] = None,
    ):
        """
        Initialize CLI wrapper.

        Args:
            host: TCP host for meshtasticd connection
            port: TCP port (default 4403)
            device: Serial device path (e.g., /dev/ttyUSB0) - overrides TCP
            cli_path: Path to meshtastic CLI (auto-detected if None)
        """
        self.host = host
        self.port = port
        self.device = device
        self.cli_path = cli_path or self._find_cli()

        if not self.cli_path:
            logger.warning("meshtastic CLI not found in PATH")

    def _find_cli(self) -> Optional[str]:
        """Find meshtastic CLI using centralized path resolver."""
        return find_meshtastic_cli()

    def _build_base_args(self) -> List[str]:
        """Build base CLI arguments for connection."""
        args = [self.cli_path]

        if self.device:
            # Direct serial connection
            args.extend(['--port', self.device])
        else:
            # TCP connection to meshtasticd
            args.extend(['--host', f"{self.host}:{self.port}"])

        return args

    def run(
        self,
        args: List[str],
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = MAX_RETRIES,
        parse_json: bool = False,
    ) -> CLIResult:
        """
        Run meshtastic CLI command with retry logic.

        Args:
            args: Command arguments (without 'meshtastic' or connection args)
            timeout: Command timeout in seconds
            retries: Number of retry attempts
            parse_json: If True, attempt to parse output as JSON

        Returns:
            CLIResult with success status and output
        """
        if not self.cli_path:
            return CLIResult(
                success=False,
                error="meshtastic CLI not found",
                command=args,
            )

        full_command = self._build_base_args() + args
        last_error = ""

        for attempt in range(retries):
            try:
                logger.debug(
                    f"CLI attempt {attempt + 1}/{retries}: {' '.join(args)}"
                )

                result = subprocess.run(
                    full_command,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )

                if result.returncode == 0:
                    # Success
                    data = None
                    if parse_json and result.stdout.strip():
                        try:
                            data = json.loads(result.stdout)
                        except json.JSONDecodeError:
                            pass

                    return CLIResult(
                        success=True,
                        output=result.stdout,
                        data=data,
                        attempts=attempt + 1,
                        command=full_command,
                    )

                # Non-zero return code
                last_error = result.stderr or result.stdout
                logger.warning(
                    f"CLI attempt {attempt + 1} failed: {last_error[:100]}"
                )

            except KeyboardInterrupt:
                # User aborted - exit immediately without retry
                logger.info("CLI command aborted by user")
                return CLIResult(
                    success=False,
                    error="Aborted by user",
                    attempts=attempt + 1,
                    command=full_command,
                )

            except subprocess.TimeoutExpired:
                last_error = f"Command timed out after {timeout}s"
                logger.warning(f"CLI attempt {attempt + 1}: {last_error}")

            except Exception as e:
                last_error = str(e)
                logger.warning(f"CLI attempt {attempt + 1} exception: {e}")

            # Wait before retry (unless last attempt)
            if attempt < retries - 1:
                delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
                logger.debug(f"Waiting {delay}s before retry...")
                try:
                    time.sleep(delay)
                except KeyboardInterrupt:
                    # User aborted during retry wait
                    logger.info("CLI command aborted by user during retry wait")
                    return CLIResult(
                        success=False,
                        error="Aborted by user",
                        attempts=attempt + 1,
                        command=full_command,
                    )

        # All retries exhausted
        logger.error(f"CLI command failed after {retries} attempts: {args}")
        return CLIResult(
            success=False,
            error=last_error,
            attempts=retries,
            command=full_command,
        )

    # ─────────────────────────────────────────────────────────────
    # High-Level Commands
    # ─────────────────────────────────────────────────────────────

    def get_info(self) -> CLIResult:
        """Get device info."""
        return self.run(['--info'])

    def get_nodes(self) -> CLIResult:
        """Get list of known nodes."""
        result = self.run(['--nodes'])
        if result.success:
            # Parse node list from output
            result.data = self._parse_nodes(result.output)
        return result

    def get_node_count(self) -> int:
        """Get count of known nodes."""
        result = self.get_nodes()
        if result.success and result.data:
            return len(result.data)
        return 0

    def send_text(
        self,
        message: str,
        destination: str = "^all",
        channel: int = 0,
    ) -> CLIResult:
        """
        Send text message.

        Args:
            message: Message text
            destination: Node ID or ^all for broadcast
            channel: Channel index

        Returns:
            CLIResult
        """
        args = [
            '--sendtext', message,
            '--dest', destination,
            '--ch-index', str(channel),
        ]
        return self.run(args)

    def export_config(self) -> CLIResult:
        """Export device configuration."""
        return self.run(['--export-config'], parse_json=True)

    def get_channels(self) -> CLIResult:
        """Get channel configuration."""
        return self.run(['--ch-list'])

    def set_channel(
        self,
        index: int,
        name: Optional[str] = None,
        psk: Optional[str] = None,
    ) -> CLIResult:
        """Set channel configuration."""
        args = ['--ch-index', str(index)]
        if name:
            args.extend(['--ch-name', name])
        if psk:
            args.extend(['--ch-psk', psk])
        return self.run(args)

    def set_owner(self, name: str) -> CLIResult:
        """Set device owner name."""
        return self.run(['--set-owner', name])

    def set_location(self, lat: float, lon: float, alt: float = 0) -> CLIResult:
        """Set device location."""
        return self.run([
            '--setlat', str(lat),
            '--setlon', str(lon),
            '--setalt', str(int(alt)),
        ])

    def reboot(self) -> CLIResult:
        """Reboot the device."""
        return self.run(['--reboot'], timeout=10)

    def factory_reset(self) -> CLIResult:
        """Factory reset the device (DANGER!)."""
        logger.warning("Factory reset requested!")
        return self.run(['--factory-reset'], timeout=30)

    # ─────────────────────────────────────────────────────────────
    # Radio Configuration
    # ─────────────────────────────────────────────────────────────

    def set_with_verify(
        self,
        key: str,
        value: str,
        verify_section: Optional[str] = None,
    ) -> CLIResult:
        """Set a device config value and verify it took effect.

        Sends --set, waits briefly for the device to process, then
        reads back via --get to confirm the value was applied.

        Args:
            key: Full config key (e.g., 'lora.modem_preset')
            value: Value to set
            verify_section: Config section to read back (default: derived from key)

        Returns:
            CLIResult with '[verified]' or '[unverified]' appended to output
        """
        result = self.run(['--set', key, value])
        if not result.success:
            return result

        # Allow device to process the AdminMessage
        time.sleep(0.5)

        # Read back to verify
        section = verify_section or key.split('.')[0]
        check = self.run(['--get', section], retries=1)
        if check.success and value.lower() in check.output.lower():
            result.output = (result.output or '') + "\n[verified]"
            logger.debug("Verified %s = %s", key, value)
        else:
            result.output = (result.output or '') + "\n[unverified]"
            logger.warning("Could not verify %s = %s", key, value)

        return result

    def configure(self, yaml_path: str) -> CLIResult:
        """Apply a configuration YAML file via meshtastic --configure.

        Args:
            yaml_path: Path to YAML config file

        Returns:
            CLIResult from the configure command
        """
        return self.run(['--configure', yaml_path], timeout=60)

    def set_lora_region(self, region: str) -> CLIResult:
        """Set LoRa region (e.g., US, EU_868)."""
        return self.set_with_verify('lora.region', region)

    def set_lora_preset(self, preset: str) -> CLIResult:
        """Set LoRa modem preset (e.g., LONG_FAST, MEDIUM_SLOW)."""
        return self.set_with_verify('lora.modem_preset', preset)

    def set_channel_num(self, channel_num: int) -> CLIResult:
        """Set LoRa frequency slot (channel_num)."""
        return self.set_with_verify('lora.channel_num', str(channel_num))

    def set_hop_limit(self, hops: int) -> CLIResult:
        """Set hop limit for messages."""
        return self.set_with_verify('lora.hop_limit', str(hops))

    # ─────────────────────────────────────────────────────────────
    # MQTT Configuration
    # ─────────────────────────────────────────────────────────────

    def configure_mqtt(
        self,
        enabled: bool = True,
        address: str = "mqtt.meshtastic.org",
        username: str = "",
        password: str = "",
        root_topic: str = "msh",
    ) -> CLIResult:
        """Configure MQTT settings."""
        args = [
            '--set', f'mqtt.enabled', str(enabled).lower(),
            '--set', f'mqtt.address', address,
            '--set', f'mqtt.root_topic', root_topic,
        ]
        if username:
            args.extend(['--set', 'mqtt.username', username])
        if password:
            args.extend(['--set', 'mqtt.password', password])

        return self.run(args)

    # ─────────────────────────────────────────────────────────────
    # Output Parsing
    # ─────────────────────────────────────────────────────────────

    def _parse_nodes(self, output: str) -> List[Dict[str, Any]]:
        """Parse node list from CLI output."""
        nodes = []

        # The CLI outputs nodes in a table format or JSON
        # This is a basic parser - enhance based on actual output format
        lines = output.strip().split('\n')

        for line in lines:
            if line.startswith('!') or 'Node' in line:
                # Attempt to parse node line
                parts = line.split()
                if len(parts) >= 2:
                    node = {
                        'id': parts[0] if parts[0].startswith('!') else None,
                        'raw': line,
                    }
                    nodes.append(node)

        return nodes

    # ─────────────────────────────────────────────────────────────
    # Health Checks
    # ─────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check if CLI is available and can connect."""
        if not self.cli_path:
            return False

        result = self.run(['--info'], timeout=10, retries=1)
        return result.success

    def get_device_metrics(self) -> Optional[Dict[str, Any]]:
        """Get device metrics (battery, voltage, etc.)."""
        result = self.run(['--info'])
        if not result.success:
            return None

        # Parse metrics from output
        metrics = {}
        for line in result.output.split('\n'):
            if 'Battery' in line:
                # Extract battery percentage
                try:
                    metrics['battery'] = int(
                        line.split('%')[0].split()[-1]
                    )
                except (ValueError, IndexError):
                    pass
            elif 'Voltage' in line:
                try:
                    metrics['voltage'] = float(
                        line.split(':')[-1].strip().replace('V', '')
                    )
                except (ValueError, IndexError):
                    pass

        return metrics if metrics else None


# ─────────────────────────────────────────────────────────────────
# Convenience Functions
# ─────────────────────────────────────────────────────────────────

_default_cli: Optional[MeshtasticCLI] = None


def get_cli(
    host: str = "localhost",
    port: int = 4403,
    device: Optional[str] = None,
) -> MeshtasticCLI:
    """Get or create default CLI instance.

    If an instance already exists with different parameters, logs a warning
    and returns the existing instance. Use reset_cli() to force recreation.
    """
    global _default_cli

    if _default_cli is None:
        _default_cli = MeshtasticCLI(host=host, port=port, device=device)
    else:
        # Warn if parameters differ from existing instance
        if (_default_cli.host != host or _default_cli.port != port
                or _default_cli.device != device):
            logger.warning(
                f"get_cli() called with different params "
                f"(host={host}, port={port}, device={device}) "
                f"but instance already exists with "
                f"(host={_default_cli.host}, port={_default_cli.port}, "
                f"device={_default_cli.device}). "
                f"Use reset_cli() to force recreation."
            )

    return _default_cli


def reset_cli() -> None:
    """Reset the default CLI singleton, forcing recreation on next get_cli() call."""
    global _default_cli
    _default_cli = None


def quick_send(message: str, destination: str = "^all") -> bool:
    """Quick send a message."""
    cli = get_cli()
    result = cli.send_text(message, destination)
    return result.success


def get_node_count() -> int:
    """Quick get node count."""
    cli = get_cli()
    return cli.get_node_count()
