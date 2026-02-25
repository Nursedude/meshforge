"""
MeshForge Service Orchestrator

Manages the complete NOC stack: meshtasticd, rnsd, and MeshForge services.
MeshForge IS the node - this orchestrator ensures all services start, run, and recover.

Supports:
    - Native meshtasticd (for SPI radios like Meshtoad)
    - Python meshtastic CLI (for USB serial radios)
    - Automatic config detection from /etc/meshforge/noc.yaml

Usage:
    # As module
    from core.orchestrator import ServiceOrchestrator
    orch = ServiceOrchestrator()
    orch.startup()

    # As standalone
    python -m core.orchestrator [--stop|--status|--install|--config]
"""

import os
import re
import sys
import time
import socket
import logging
import subprocess
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any

from utils.safe_import import safe_import

# Module-level safe imports
_yaml, _HAS_YAML = safe_import('yaml')
# Import centralized port checker for consistency across MeshForge
# See: utils/service_check.py - SINGLE SOURCE OF TRUTH
from utils.service_check import (
    check_port as _centralized_check_port,
    check_service,
    _detect_radio_hardware,
    _sudo_cmd,
    _sudo_write,
    ServiceState as _CheckState,
)

# Setup logging
logger = logging.getLogger(__name__)

# Configuration paths
NOC_CONFIG_PATH = Path("/etc/meshforge/noc.yaml")
MESHTASTICD_CONFIG_DIR = Path("/etc/meshtasticd")


class ServiceState(Enum):
    """Service states."""
    UNKNOWN = "unknown"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    NOT_INSTALLED = "not_installed"
    NOT_NEEDED = "not_needed"  # For usb-direct mode where no daemon runs


@dataclass
class ServiceConfig:
    """Configuration for a managed service."""
    name: str
    systemd_name: str
    check_binary: Optional[str] = None  # Binary that must exist for real install
    check_port: Optional[int] = None
    check_command: Optional[List[str]] = None
    startup_delay: int = 3  # seconds to wait after starting
    required: bool = True
    install_command: Optional[List[str]] = None
    dependencies: List[str] = field(default_factory=list)


@dataclass
class ServiceStatus:
    """Current status of a service."""
    name: str
    state: ServiceState
    pid: Optional[int] = None
    uptime: Optional[str] = None
    message: str = ""


class ServiceOrchestrator:
    """
    Orchestrates MeshForge NOC services.

    Manages meshtasticd, rnsd, and ensures they start in the correct order
    with proper health verification (double-tap).
    """

    # Service configurations
    SERVICES: Dict[str, ServiceConfig] = {
        'meshtasticd': ServiceConfig(
            name='meshtasticd',
            systemd_name='meshtasticd',
            check_binary='meshtasticd',
            check_port=4403,
            startup_delay=5,  # Device init takes time
            required=True,
            install_command=['pip3', 'install', 'meshtastic'],
        ),
        'rnsd': ServiceConfig(
            name='rnsd',
            systemd_name='rnsd',
            check_binary='rnsd',
            # No check_command or check_port: trust systemctl is-active.
            # rnstatus -s fails on fresh installs before interfaces are configured.
            startup_delay=3,
            required=True,
            install_command=['pipx', 'install', 'rns'],
            dependencies=['meshtasticd'],  # Start after meshtasticd
        ),
        'mosquitto': ServiceConfig(
            name='mosquitto',
            systemd_name='mosquitto',
            check_binary='mosquitto',
            check_port=1883,
            startup_delay=2,
            required=False,
            install_command=['apt-get', 'install', '-y', 'mosquitto'],
        ),
    }

    # Startup order (services started in this sequence)
    STARTUP_ORDER = ['meshtasticd', 'rnsd']

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize orchestrator."""
        # Instance-level copies to avoid mutating shared class attributes
        self.SERVICES = dict(self.__class__.SERVICES)
        self.STARTUP_ORDER = list(self.__class__.STARTUP_ORDER)
        self._config_path = config_path or NOC_CONFIG_PATH
        self._running = False
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._callbacks: Dict[str, List[Callable]] = {
            'service_started': [],
            'service_stopped': [],
            'service_failed': [],
            'all_ready': [],
        }

        # Load configuration
        self._load_config()

        # Adjust meshtasticd service based on daemon type
        self._configure_meshtasticd()

    def _load_config(self):
        """Load NOC configuration from /etc/meshforge/noc.yaml."""
        # Default config
        self.config = {
            'mode': 'local',  # local | client | remote-only
            'auto_start': True,
            'health_check_interval': 30,
            'restart_on_failure': True,
            'max_restart_attempts': 3,
            'radio': {
                'type': 'unknown',
                'daemon': 'python',
                'device': '',
            },
            'services': {
                'meshtasticd': {'managed': True, 'auto_start': True},
                'rnsd': {'managed': True, 'auto_start': True},
            },
        }

        # Load from config file
        if self._config_path and self._config_path.exists():
            if _HAS_YAML:
                try:
                    with open(self._config_path) as f:
                        file_config = _yaml.safe_load(f)
                        if file_config and 'noc' in file_config:
                            noc_config = file_config['noc']
                            # Merge configs
                            self._merge_config(noc_config)
                            logger.info(f"Loaded config from {self._config_path}")
                except Exception as e:
                    logger.warning(f"Failed to load config: {e}")
            else:
                logger.warning("PyYAML not installed, using defaults")
        else:
            logger.info("No config file found, using defaults")

    def _merge_config(self, noc_config: Dict[str, Any]):
        """Merge loaded config with defaults."""
        if 'mode' in noc_config:
            self.config['mode'] = noc_config['mode']

        if 'radio' in noc_config:
            self.config['radio'].update(noc_config['radio'])

        if 'services' in noc_config:
            for service, svc_config in noc_config['services'].items():
                if service not in self.config['services']:
                    self.config['services'][service] = {}
                self.config['services'][service].update(svc_config)

        if 'startup' in noc_config:
            startup = noc_config['startup']
            if 'health_check_interval' in startup:
                self.config['health_check_interval'] = startup['health_check_interval']
            if 'restart_on_failure' in startup:
                self.config['restart_on_failure'] = startup['restart_on_failure']
            if 'max_restart_attempts' in startup:
                self.config['max_restart_attempts'] = startup['max_restart_attempts']
            if 'auto_start_services' in startup:
                self.config['auto_start'] = startup['auto_start_services']

    def _configure_meshtasticd(self):
        """Configure meshtasticd service based on daemon type."""
        daemon_type = self.config['radio'].get('daemon', 'python')

        if daemon_type == 'native':
            # Native meshtasticd binary (for SPI radios)
            self.SERVICES['meshtasticd'] = ServiceConfig(
                name='meshtasticd',
                systemd_name='meshtasticd',
                check_binary='meshtasticd',
                check_port=4403,
                startup_delay=5,
                required=True,
                # No install command - requires .deb
            )
            logger.info("Configured for native meshtasticd (SPI radio)")
        elif daemon_type == 'native-usb':
            # Native meshtasticd with USB serial radio
            self.SERVICES['meshtasticd'] = ServiceConfig(
                name='meshtasticd',
                systemd_name='meshtasticd',
                check_binary='meshtasticd',
                check_port=4403,
                startup_delay=5,
                required=True,
            )
            logger.info("Configured for native meshtasticd (USB serial)")
        elif daemon_type == 'usb-direct':
            # USB radios don't need a daemon - CLI talks directly to device
            # Mark meshtasticd as not required since it doesn't need to run
            self.SERVICES['meshtasticd'] = ServiceConfig(
                name='meshtasticd',
                systemd_name='meshtasticd',
                check_binary=None,  # No binary check - it's a placeholder service
                check_port=None,    # No port check - no daemon running
                startup_delay=0,
                required=False,     # NOT required for usb-direct mode
            )
            # Remove meshtasticd from startup order for usb-direct mode
            if 'meshtasticd' in self.STARTUP_ORDER:
                self.STARTUP_ORDER = [s for s in self.STARTUP_ORDER if s != 'meshtasticd']
            # Also remove meshtasticd dependency from rnsd
            if 'rnsd' in self.SERVICES:
                rnsd_config = self.SERVICES['rnsd']
                self.SERVICES['rnsd'] = ServiceConfig(
                    name=rnsd_config.name,
                    systemd_name=rnsd_config.systemd_name,
                    check_binary=rnsd_config.check_binary,
                    check_port=rnsd_config.check_port,
                    check_command=rnsd_config.check_command,
                    startup_delay=rnsd_config.startup_delay,
                    required=rnsd_config.required,
                    install_command=rnsd_config.install_command,
                    dependencies=[],  # No dependencies in usb-direct mode
                )
            logger.info("Configured for USB-direct mode (no daemon required)")
            logger.info("USB radios: use 'meshtastic --port /dev/ttyUSB0 --info' directly")
        else:
            # Python CLI (for USB serial radios with native daemon)
            self.SERVICES['meshtasticd'] = ServiceConfig(
                name='meshtasticd',
                systemd_name='meshtasticd',
                check_binary='meshtasticd',
                check_port=4403,
                startup_delay=5,
                required=True,
                install_command=['pip3', 'install', 'meshtastic'],
            )
            logger.info("Configured for Python meshtastic CLI (USB radio)")

    def _check_meshtasticd_config(self) -> bool:
        """Check that meshtasticd has a radio config in config.d/.

        Without a radio config template, meshtasticd starts but never binds
        TCP port 4403 — causing health checks to fail.

        If config.d/ is empty, REFUSES to start and logs clear instructions
        directing the user to select hardware via the TUI or manual copy.
        No auto-detection or auto-deployment of configs.

        Returns:
            True if config.d/ has at least one valid config.
        """
        config_d = MESHTASTICD_CONFIG_DIR / "config.d"
        available_d = MESHTASTICD_CONFIG_DIR / "available.d"

        # Check if config.d/ already has configs
        if config_d.exists():
            existing = list(config_d.glob("*.yaml"))
            if existing:
                # Defense-in-depth: ensure SPI templates have explicit
                # Module: to prevent any residual Module: auto in
                # config.yaml from triggering meshtasticd autoconf.
                for tmpl in existing:
                    self._validate_template_module(tmpl)
                return True

        # ── config.d/ is empty — REFUSE to start ──
        logger.error(
            "No radio config in /etc/meshtasticd/config.d/ — "
            "meshtasticd cannot start without hardware configuration."
        )
        logger.error("")
        logger.error("Select your radio hardware using one of these methods:")
        logger.error("")
        logger.error(
            "  1. TUI: sudo python3 src/launcher_tui/main.py"
        )
        logger.error(
            "     → Meshtasticd Config → Hardware Config"
        )
        logger.error("")
        logger.error(
            "  2. Manual: sudo cp /etc/meshtasticd/available.d/"
            "<your-radio>.yaml /etc/meshtasticd/config.d/"
        )
        logger.error("")

        # List available templates if the directory exists
        if available_d.exists():
            templates = sorted(available_d.glob("*.yaml"))
            if templates:
                logger.error("Available templates:")
                for tmpl in templates:
                    logger.error(f"  - {tmpl.name}")
                logger.error("")

        return False

    def _validate_template_module(self, deployed_path: Path) -> None:
        """Validate that a deployed config.d template has an explicit Lora.Module line.

        Defense-in-depth: if a user manually copies an SPI template that
        lacks Module:, this patches it to prevent any residual Module: auto
        from triggering meshtasticd's autoconf hardware scan (which crashes
        on EEPROM CRC32 missing, no CH341, etc.).

        If Module: is missing, infers the correct value from the
        RADIO_TEMPLATES metadata or falls back to sx1262 (most common).
        """
        try:
            content = deployed_path.read_text()
        except OSError as e:
            logger.warning(f"Cannot read deployed template for Module check: {e}")
            return

        # Only relevant for SPI templates (those with Lora: + GPIO pins)
        if 'Lora:' not in content or 'CS:' not in content:
            return

        # Already has explicit Module: — nothing to do
        if re.search(r'^\s+Module:\s*\S', content, re.MULTILINE):
            return

        # Determine correct Module value from RADIO_TEMPLATES chip metadata
        stem = deployed_path.stem
        module_value = None
        try:
            from core.meshtasticd_config import RADIO_TEMPLATES
            chip = RADIO_TEMPLATES.get(stem, {}).get("chip")
            if chip:
                module_value = chip
        except ImportError:
            pass

        if not module_value:
            # Filename heuristics
            lower = stem.lower()
            if 'sx1276' in lower or 'rfm9' in lower or 'rfm95' in lower:
                module_value = 'sx1276'
            elif 'sx1268' in lower or '400m' in lower:
                module_value = 'sx1268'
            else:
                module_value = 'sx1262'

        # Inject Module: line after Lora: header
        patched = re.sub(
            r'^(Lora:\s*\n)',
            f'\\1  Module: {module_value}\n',
            content,
            count=1,
            flags=re.MULTILINE,
        )

        if patched != content:
            try:
                deployed_path.write_text(patched)
                logger.warning(
                    f"Injected 'Module: {module_value}' into "
                    f"{deployed_path.name} to prevent autoconf crash"
                )
            except OSError as e:
                logger.error(f"Cannot patch deployed template: {e}")

    def get_config_info(self) -> Dict[str, Any]:
        """Get current configuration information."""
        return {
            'config_file': str(self._config_path),
            'config_exists': self._config_path.exists() if self._config_path else False,
            'mode': self.config['mode'],
            'radio_type': self.config['radio'].get('type', 'unknown'),
            'daemon_type': self.config['radio'].get('daemon', 'python'),
            'device': self.config['radio'].get('device', ''),
            'meshtasticd_config_dir': str(MESHTASTICD_CONFIG_DIR),
            'meshtasticd_config_exists': MESHTASTICD_CONFIG_DIR.exists(),
            'health_check_interval': self.config['health_check_interval'],
            'restart_on_failure': self.config['restart_on_failure'],
            'max_restart_attempts': self.config['max_restart_attempts'],
        }

    # ─────────────────────────────────────────────────────────────
    # Service State Checks
    # ─────────────────────────────────────────────────────────────

    def is_installed(self, service_name: str) -> bool:
        """Check if service is properly installed (not just a placeholder unit)."""
        config = self.SERVICES.get(service_name)
        if not config:
            return False

        # Check binary exists (rejects placeholder services that use /bin/echo)
        if config.check_binary:
            result = subprocess.run(
                ['which', config.check_binary],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                return False

        # Check systemd unit exists
        result = subprocess.run(
            ['systemctl', 'list-unit-files', f'{config.systemd_name}.service'],
            capture_output=True,
            text=True,
            timeout=10
        )
        return config.systemd_name in result.stdout

    def is_running(self, service_name: str) -> bool:
        """Check if service is running via check_service() (SSOT)."""
        config = self.SERVICES.get(service_name)
        if not config:
            return False

        status = check_service(config.systemd_name)
        return status.available

    def is_healthy(self, service_name: str) -> bool:
        """
        Service health = systemctl is-active.

        Port/command checks are readiness indicators, not health gates.
        meshtasticd may be running (healthy) before port 4403 binds —
        the user configures the radio via the web UI at port 9443.
        For port readiness, use is_ready() instead.
        """
        return self.is_running(service_name)

    def is_ready(self, service_name: str) -> bool:
        """
        Full readiness: service running AND port/command responding.

        Use this when you need to verify the service is accepting
        connections (e.g., before opening a TCP client to port 4403).
        """
        config = self.SERVICES.get(service_name)
        if not config:
            return False

        if not self.is_running(service_name):
            return False

        if config.check_port:
            return self._check_port(config.check_port)
        elif config.check_command:
            return self._check_command(config.check_command)

        return True

    def _check_port(self, port: int, host: str = 'localhost', timeout: float = 2.0) -> bool:
        """Check if port is accepting connections.

        Uses centralized port checker from utils/service_check.py for consistency.
        """
        return _centralized_check_port(port, host, timeout)

    def _check_command(self, command: List[str], timeout: int = 10) -> bool:
        """Run command and check for success."""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _log_journal_tail(self, service_name: str, lines: int = 8):
        """Log recent journal entries for a failed service to aid debugging."""
        config = self.SERVICES.get(service_name)
        if not config:
            return
        try:
            result = subprocess.run(
                ['journalctl', '-u', config.systemd_name, '-n', str(lines),
                 '--no-pager', '-o', 'short-iso'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                logger.error(f"Recent {service_name} logs:")
                for line in result.stdout.strip().splitlines():
                    logger.error(f"  {line}")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    def get_status(self, service_name: str) -> ServiceStatus:
        """Get detailed status of a service."""
        config = self.SERVICES.get(service_name)
        if not config:
            return ServiceStatus(
                name=service_name,
                state=ServiceState.UNKNOWN,
                message=f"Unknown service: {service_name}"
            )

        # Check if service is not needed (e.g., usb-direct mode)
        # Services with no check_binary and not required don't need to run
        daemon_type = self.config['radio'].get('daemon', 'python')
        if daemon_type == 'usb-direct' and service_name == 'meshtasticd':
            return ServiceStatus(
                name=service_name,
                state=ServiceState.NOT_NEEDED,
                message="USB-direct mode: no daemon needed (use meshtastic CLI directly)"
            )

        if not self.is_installed(service_name):
            return ServiceStatus(
                name=service_name,
                state=ServiceState.NOT_INSTALLED,
                message=f"{service_name} is not installed"
            )

        if not self.is_running(service_name):
            return ServiceStatus(
                name=service_name,
                state=ServiceState.STOPPED,
                message=f"{service_name} is stopped"
            )

        # Get PID
        pid = self._get_pid(service_name)

        # Port readiness is informational, not a failure state
        if config.check_port and not self._check_port(config.check_port):
            return ServiceStatus(
                name=service_name,
                state=ServiceState.RUNNING,
                pid=pid,
                message=(
                    f"{service_name} is running "
                    f"(port {config.check_port} not yet bound)"
                )
            )

        return ServiceStatus(
            name=service_name,
            state=ServiceState.RUNNING,
            pid=pid,
            message=f"{service_name} is running"
        )

    def _get_pid(self, service_name: str) -> Optional[int]:
        """Get PID of service main process."""
        config = self.SERVICES.get(service_name)
        if not config:
            return None

        try:
            result = subprocess.run(
                ['systemctl', 'show', '-p', 'MainPID', config.systemd_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                pid_str = result.stdout.strip().replace('MainPID=', '')
                return int(pid_str) if pid_str and pid_str != '0' else None
        except (subprocess.TimeoutExpired, ValueError):
            pass
        return None

    def get_all_status(self) -> Dict[str, ServiceStatus]:
        """Get status of all managed services."""
        return {name: self.get_status(name) for name in self.SERVICES}

    # ─────────────────────────────────────────────────────────────
    # Service Control
    # ─────────────────────────────────────────────────────────────

    def _fix_stale_placeholder(self, service_name: str) -> bool:
        """
        Detect and fix stale placeholder service files.

        When a previous install created a placeholder systemd unit (Type=oneshot,
        ExecStart=/bin/echo ...) but the real binary has since been installed,
        regenerate the service file from the template and daemon-reload.

        Returns:
            True if a fix was applied, False if no fix was needed or possible.
        """
        config = self.SERVICES.get(service_name)
        if not config or not config.check_binary:
            return False

        # Read the current ExecStart from systemd
        try:
            result = subprocess.run(
                ['systemctl', 'show', config.systemd_name, '--property=ExecStart'],
                capture_output=True,
                text=True,
                timeout=5
            )
            exec_start = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

        # Check if ExecStart is a placeholder (echo command)
        if '/bin/echo' not in exec_start:
            return False

        # Placeholder detected — check if real binary exists
        try:
            bin_result = subprocess.run(
                ['which', config.check_binary],
                capture_output=True,
                text=True,
                timeout=5
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

        if bin_result.returncode != 0:
            logger.error(
                f"{service_name} service is a placeholder and binary not found. "
                f"Install: sudo apt install {config.check_binary}"
            )
            return False

        binary_path = bin_result.stdout.strip()
        logger.warning(
            f"{service_name} service is a stale placeholder — "
            f"real binary found at {binary_path}"
        )

        # Regenerate from template (same logic as install_noc.sh)
        template_path = (
            Path(__file__).resolve().parent.parent.parent
            / 'templates' / 'systemd' / 'meshtasticd-native.service'
        )

        if template_path.exists():
            template = template_path.read_text()
            service_content = template.replace('@MESHTASTICD_BIN@', binary_path)
        else:
            # Inline fallback (matches install_noc.sh NATIVE_USB_SERVICE)
            service_content = (
                "[Unit]\n"
                "Description=Meshtastic Daemon\n"
                "Documentation=https://meshtastic.org\n"
                "After=network.target\n"
                "\n"
                "[Service]\n"
                "Type=simple\n"
                "User=root\n"
                "WorkingDirectory=/etc/meshtasticd\n"
                f"ExecStart={binary_path} -c /etc/meshtasticd/config.yaml\n"
                "Restart=on-failure\n"
                "RestartSec=5\n"
                "\n"
                "[Install]\n"
                "WantedBy=multi-user.target\n"
            )

        service_path = f'/etc/systemd/system/{config.systemd_name}.service'
        success, msg = _sudo_write(service_path, service_content)
        if not success:
            logger.error(f"Failed to fix placeholder service: {msg}")
            return False

        # Reload systemd so it picks up the new unit file
        try:
            subprocess.run(
                _sudo_cmd(['systemctl', 'daemon-reload']),
                capture_output=True,
                text=True,
                timeout=10
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("daemon-reload failed after fixing placeholder")

        logger.info(f"Fixed stale placeholder service for {service_name}")
        return True

    def start_service(self, service_name: str, wait: bool = True) -> bool:
        """
        Start a service with health verification.

        Args:
            service_name: Name of service to start
            wait: If True, wait for service to be healthy

        Returns:
            True if service is running and healthy
        """
        config = self.SERVICES.get(service_name)
        if not config:
            logger.error(f"Unknown service: {service_name}")
            return False

        if not self.is_installed(service_name):
            logger.error(f"{service_name} is not installed")
            return False

        # Already running — no need to validate pre-start config
        # (the service's configuration is valid however it was set up)
        if self.is_running(service_name):
            logger.info(f"{service_name} is already running")
            return True

        # Pre-start: ensure meshtasticd has a radio config in config.d/
        # Only reached when we are about to START the service.
        if service_name == 'meshtasticd' and config.check_port:
            if not self._check_meshtasticd_config():
                logger.error("meshtasticd cannot start without a radio config")
                return False

        # Auto-fix stale placeholder service files before starting
        self._fix_stale_placeholder(service_name)

        logger.info(f"Starting {service_name}...")
        result = subprocess.run(
            ['systemctl', 'start', config.systemd_name],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.error(f"Failed to start {service_name}: {result.stderr}")
            return False

        if wait:
            # Poll service status with crash detection instead of blind sleep
            max_wait = config.startup_delay + 5  # e.g., 10s for meshtasticd
            service_up = False

            for elapsed in range(1, max_wait + 1):
                time.sleep(1)
                status = check_service(config.systemd_name)

                if status.available:
                    service_up = True
                    break

                if status.state == _CheckState.FAILED:
                    # Service crashed — log diagnostics immediately
                    logger.warning(
                        f"{service_name} crashed during startup (after {elapsed}s)"
                    )
                    self._log_journal_tail(service_name, lines=8)

                    # One restart attempt (device node may now be ready)
                    logger.info(f"Restarting {service_name}...")
                    subprocess.run(
                        ['systemctl', 'restart', config.systemd_name],
                        capture_output=True, text=True, timeout=30
                    )
                    # Wait for second attempt
                    for _ in range(config.startup_delay):
                        time.sleep(1)
                        status = check_service(config.systemd_name)
                        if status.available:
                            service_up = True
                            break
                    if not service_up:
                        logger.error(
                            f"{service_name} failed to start after restart"
                        )
                        self._log_journal_tail(service_name, lines=8)
                        self._emit('service_failed', service_name)
                        return False
                    break

            if not service_up:
                # Timed out — never became available or crashed
                logger.error(
                    f"{service_name} did not start within {max_wait}s"
                )
                self._log_journal_tail(service_name, lines=8)
                self._emit('service_failed', service_name)
                return False

            # Port readiness check with retries (non-blocking, per SSOT)
            if config.check_port:
                port_ready = False
                for _ in range(5):
                    if self._check_port(config.check_port):
                        port_ready = True
                        logger.info(
                            f"{service_name} port {config.check_port} ready"
                        )
                        break
                    time.sleep(1)

                if not port_ready:
                    # Re-check service state — it may have crashed during
                    # the port-wait window (e.g., wrong radio config →
                    # GPIO init failure → meshtasticd exits)
                    post_port_status = check_service(config.systemd_name)
                    if not post_port_status.available:
                        logger.error(
                            f"{service_name} crashed after start "
                            f"(port {config.check_port} never bound, "
                            f"service state: {post_port_status.state.value})"
                        )
                        self._log_journal_tail(service_name, lines=10)
                        logger.error(
                            "Check /etc/meshtasticd/config.d/ — verify "
                            "the radio config matches your hardware "
                            "(correct GPIO pins, Module type, etc.)."
                        )
                        self._emit('service_failed', service_name)
                        return False

                    # Service still running but port not bound — warn only
                    logger.warning(
                        f"{service_name} running but port {config.check_port} "
                        f"not yet bound — may need more startup time or "
                        f"radio configuration"
                    )

        logger.info(f"{service_name} started successfully")
        self._emit('service_started', service_name)
        return True

    def stop_service(self, service_name: str) -> bool:
        """Stop a service."""
        config = self.SERVICES.get(service_name)
        if not config:
            logger.error(f"Unknown service: {service_name}")
            return False

        if not self.is_running(service_name):
            logger.info(f"{service_name} is already stopped")
            return True

        logger.info(f"Stopping {service_name}...")
        result = subprocess.run(
            ['systemctl', 'stop', config.systemd_name],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.error(f"Failed to stop {service_name}: {result.stderr}")
            return False

        logger.info(f"{service_name} stopped")
        self._emit('service_stopped', service_name)
        return True

    def restart_service(self, service_name: str) -> bool:
        """Restart a service with health verification."""
        self.stop_service(service_name)
        time.sleep(1)
        return self.start_service(service_name)

    # ─────────────────────────────────────────────────────────────
    # Orchestration
    # ─────────────────────────────────────────────────────────────

    def startup(self, graceful: bool = False) -> bool:
        """
        Start all managed services in correct order.

        Args:
            graceful: If True, continue even when services fail (for no-radio scenarios)

        Returns:
            True if all required services are running and healthy (or graceful mode)
        """
        logger.info("═══ MeshForge NOC Startup ═══")

        # Pre-flight: check all required services are installed BEFORE starting anything
        missing = self._preflight_check()
        if missing:
            if not graceful:
                logger.error("═══ Pre-flight check failed ═══")
                logger.error("")
                logger.error("Required services not installed:")
                for svc_name, fix_cmd in missing:
                    logger.error(f"  • {svc_name}")
                    logger.error(f"    Fix: {fix_cmd}")
                logger.error("")
                logger.error("After installing, run: meshforge-noc --start")
                logger.error("Or run the full installer: sudo bash /opt/meshforge/scripts/install_noc.sh")
                return False
            else:
                for svc_name, fix_cmd in missing:
                    logger.warning(f"Service not installed: {svc_name} → {fix_cmd}")

        if graceful:
            logger.info("Graceful mode: will continue even if services fail")

        success = True
        failed_services = []

        for service_name in self.STARTUP_ORDER:
            config = self.SERVICES.get(service_name)
            if not config:
                continue

            # Skip if dependency already failed (don't cascade)
            dep_failed = False
            for dep in config.dependencies:
                if dep in failed_services:
                    logger.warning(f"Skipping {service_name}: dependency {dep} not available")
                    dep_failed = True
                    break
                if not self.is_running(dep):
                    logger.warning(f"Skipping {service_name}: dependency {dep} not running")
                    dep_failed = True
                    break

            if dep_failed:
                failed_services.append(service_name)
                if config.required and not graceful:
                    success = False
                continue

            # Start service
            if not self.start_service(service_name):
                failed_services.append(service_name)
                if config.required and not graceful:
                    logger.error(f"Required service {service_name} failed to start")
                    success = False
                elif graceful:
                    logger.warning(f"Graceful mode: {service_name} failed but continuing")
                else:
                    logger.warning(f"Optional service {service_name} failed to start")

        if success:
            logger.info("═══ All services started ═══")
            self._emit('all_ready')
        elif graceful:
            logger.warning("═══ Startup completed with failures (graceful mode) ═══")
            if failed_services:
                logger.warning(f"Failed services: {', '.join(failed_services)}")
            logger.info("MeshForge running in degraded mode - some features unavailable")
        else:
            logger.error("═══ Startup failed ═══")
            if failed_services:
                logger.error(f"Failed: {', '.join(failed_services)}")
                logger.error("Run 'meshforge-noc --status' for details")

        return success or graceful

    def _preflight_check(self) -> List[tuple]:
        """
        Check all required services are installed before attempting startup.

        Returns:
            List of (service_name, fix_command) for missing services.
            Empty list means all services are ready.
        """
        missing = []
        for service_name in self.STARTUP_ORDER:
            config = self.SERVICES.get(service_name)
            if not config or not config.required:
                continue
            if not self.is_installed(service_name):
                fix = self._get_install_hint(service_name)
                missing.append((service_name, fix))
        return missing

    def _get_install_hint(self, service_name: str) -> str:
        """Get actionable install command for a missing service."""
        hints = {
            'meshtasticd': 'sudo apt install meshtasticd (add repo first: see meshforge docs)',
            'rnsd': 'pipx install rns && sudo systemctl enable rnsd',
            'mosquitto': 'sudo apt install mosquitto',
        }
        return hints.get(service_name, f'Install {service_name}')

    def shutdown(self) -> bool:
        """Stop all managed services in reverse order."""
        logger.info("═══ MeshForge NOC Shutdown ═══")

        self._running = False
        success = True

        # Stop in reverse order
        for service_name in reversed(self.STARTUP_ORDER):
            if not self.stop_service(service_name):
                success = False

        logger.info("═══ Shutdown complete ═══")
        return success

    # ─────────────────────────────────────────────────────────────
    # Health Monitoring
    # ─────────────────────────────────────────────────────────────

    def start_monitoring(self, interval: int = 30):
        """Start background health monitoring."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            logger.warning("Monitoring already running")
            return

        self._running = True
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True
        )
        self._monitor_thread.start()
        logger.info(f"Health monitoring started (interval: {interval}s)")

    def stop_monitoring(self):
        """Stop health monitoring."""
        self._running = False
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Health monitoring stopped")

    # Cooldown period before resetting restart counters (seconds)
    RESTART_COOLDOWN = 300  # 5 minutes

    def _monitor_loop(self, interval: int):
        """Background health check loop."""
        restart_counts: Dict[str, int] = {name: 0 for name in self.SERVICES}
        last_failure_time: Dict[str, float] = {}

        while self._running:
            for service_name in self.STARTUP_ORDER:
                config = self.SERVICES.get(service_name)
                if not config or not config.required:
                    continue

                if not self.is_running(service_name):
                    logger.warning(f"{service_name} not running")

                    if self.config.get('restart_on_failure', True):
                        max_attempts = self.config.get('max_restart_attempts', 3)

                        # Reset counter after cooldown period (allow self-healing)
                        last_fail = last_failure_time.get(service_name, 0)
                        if (restart_counts[service_name] >= max_attempts
                                and time.time() - last_fail > self.RESTART_COOLDOWN):
                            logger.info(
                                f"Cooldown expired for {service_name}, "
                                f"resetting restart counter"
                            )
                            restart_counts[service_name] = 0

                        if restart_counts[service_name] < max_attempts:
                            logger.info(f"Attempting restart of {service_name}")
                            if self.restart_service(service_name):
                                restart_counts[service_name] = 0
                            else:
                                restart_counts[service_name] += 1
                                last_failure_time[service_name] = time.time()
                                self._emit('service_failed', service_name)
                        else:
                            logger.error(
                                f"{service_name} exceeded max restart attempts "
                                f"(cooldown resets in "
                                f"{int(self.RESTART_COOLDOWN - (time.time() - last_fail))}s)"
                            )

            if self._stop_event.wait(interval):
                break

    # ─────────────────────────────────────────────────────────────
    # Event System
    # ─────────────────────────────────────────────────────────────

    def on(self, event: str, callback: Callable):
        """Register callback for event."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit(self, event: str, *args):
        """Emit event to registered callbacks."""
        for callback in self._callbacks.get(event, []):
            try:
                callback(*args)
            except Exception as e:
                logger.error(f"Callback error for {event}: {e}")

    # ─────────────────────────────────────────────────────────────
    # Installation
    # ─────────────────────────────────────────────────────────────

    def check_installation(self) -> Dict[str, bool]:
        """Check what's installed."""
        return {name: self.is_installed(name) for name in self.SERVICES}

    def install_missing(self) -> bool:
        """Install missing required services."""
        success = True
        for name, config in self.SERVICES.items():
            if not config.required:
                continue

            if not self.is_installed(name):
                logger.info(f"Installing {name}...")
                if config.install_command:
                    try:
                        # For pipx commands, run as real user if we're under sudo
                        # This ensures packages install to user's ~/.local/bin not root's
                        cmd = config.install_command
                        if cmd and cmd[0] == 'pipx':
                            sudo_user = os.environ.get('SUDO_USER')
                            if sudo_user and sudo_user != 'root':
                                # Use -i for login shell to set HOME correctly
                                cmd = ['sudo', '-i', '-u', sudo_user] + cmd
                                logger.info(f"Running as {sudo_user}: {' '.join(cmd)}")

                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            timeout=300  # 5 minute timeout for installs
                        )
                        if result.returncode != 0:
                            logger.error(f"Failed to install {name}: {result.stderr}")
                            success = False
                    except subprocess.TimeoutExpired:
                        logger.error(f"Timeout installing {name}")
                        success = False
                else:
                    logger.error(f"No install command for {name}")
                    success = False

        return success


# ─────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────

def main():
    """CLI entry point for orchestrator."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
    )

    parser = argparse.ArgumentParser(description='MeshForge Service Orchestrator')
    parser.add_argument('--start', action='store_true', help='Start all services')
    parser.add_argument('--stop', action='store_true', help='Stop all services')
    parser.add_argument('--restart', action='store_true', help='Restart all services')
    parser.add_argument('--status', action='store_true', help='Show service status')
    parser.add_argument('--config', action='store_true', help='Show configuration')
    parser.add_argument('--install', action='store_true', help='Install missing services')
    parser.add_argument('--monitor', action='store_true', help='Start with monitoring')
    parser.add_argument('--graceful', action='store_true',
                        help='Continue even if services fail (for no-radio scenarios)')

    args = parser.parse_args()
    orch = ServiceOrchestrator()

    if args.config:
        print("\n═══ MeshForge NOC Configuration ═══\n")
        config_info = orch.get_config_info()
        print(f"  Config File:        {config_info['config_file']}")
        print(f"  Config Exists:      {config_info['config_exists']}")
        print(f"  NOC Mode:           {config_info['mode']}")
        print(f"  Radio Type:         {config_info['radio_type']}")
        print(f"  Daemon Type:        {config_info['daemon_type']}")
        if config_info['device']:
            print(f"  USB Device:         {config_info['device']}")
        print(f"  Meshtasticd Config: {config_info['meshtasticd_config_dir']}")
        print(f"  Config Dir Exists:  {config_info['meshtasticd_config_exists']}")
        print(f"  Health Check:       {config_info['health_check_interval']}s")
        print(f"  Restart on Fail:    {config_info['restart_on_failure']}")
        print(f"  Max Restarts:       {config_info['max_restart_attempts']}")

        # Show meshtasticd configs if they exist
        if MESHTASTICD_CONFIG_DIR.exists():
            available = list((MESHTASTICD_CONFIG_DIR / "available.d").glob("*.yaml"))
            enabled = list((MESHTASTICD_CONFIG_DIR / "config.d").glob("*.yaml"))
            print(f"\n  Available Configs:  {len(available)}")
            for cfg in available:
                print(f"    - {cfg.stem}")
            print(f"  Enabled Configs:    {len(enabled)}")
            for cfg in enabled:
                if cfg.is_symlink():
                    target = cfg.resolve().stem
                    print(f"    - {cfg.stem} -> {target}")
                else:
                    print(f"    - {cfg.stem}")
        print()
        sys.exit(0)

    if args.stop:
        sys.exit(0 if orch.shutdown() else 1)

    if args.restart:
        orch.shutdown()
        time.sleep(2)
        sys.exit(0 if orch.startup() else 1)

    if args.status:
        print("\n═══ MeshForge NOC Status ═══\n")
        config_info = orch.get_config_info()
        print(f"  Mode: {config_info['mode']} | Radio: {config_info['radio_type']} | Daemon: {config_info['daemon_type']}\n")

        statuses = orch.get_all_status()
        for name, status in statuses.items():
            state_icon = {
                ServiceState.RUNNING: '✓',
                ServiceState.STOPPED: '○',
                ServiceState.FAILED: '✗',
                ServiceState.NOT_INSTALLED: '?',
                ServiceState.NOT_NEEDED: '—',  # Dash indicates not applicable
            }.get(status.state, '?')
            pid_str = f" (PID: {status.pid})" if status.pid else ""
            print(f"  {state_icon} {name}: {status.state.value}{pid_str}")
            if status.message and (
                status.state != ServiceState.RUNNING
                or "not yet bound" in status.message
            ):
                print(f"      {status.message}")
        print()
        sys.exit(0)

    if args.install:
        sys.exit(0 if orch.install_missing() else 1)

    # Default: start
    if args.start or not any([args.stop, args.restart, args.status, args.install, args.config]):
        success = orch.startup(graceful=args.graceful)
        if (success or args.graceful) and args.monitor:
            import threading
            _stop_event = threading.Event()
            orch.start_monitoring()
            try:
                while not _stop_event.is_set():
                    _stop_event.wait(1)
            except KeyboardInterrupt:
                _stop_event.set()
                orch.shutdown()
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
