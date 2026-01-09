"""
Unified Diagnostic Engine for MeshForge

This is the single source of truth for ALL diagnostics.
CLI, GTK, Web, and TUI all consume this engine.

Design Principles:
1. Singleton pattern - one engine per process
2. Thread-safe - supports concurrent UI updates
3. Callback-driven - real-time notifications for GUI/Web
4. Persistent logging - events written to disk
5. Category-based - checks organized by subsystem

Usage:
    engine = DiagnosticEngine.get_instance()
    engine.register_check_callback(my_handler)
    results = engine.run_all()
"""

import os
import socket
import subprocess
import shutil
import threading
import time
import json
import logging
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import (
    CheckResult, CheckStatus, CheckCategory,
    SubsystemHealth, HealthStatus,
    DiagnosticEvent, EventSeverity,
    DiagnosticReport,
    CheckCallback, HealthCallback, EventCallback, ProgressCallback
)

logger = logging.getLogger(__name__)


# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        """Fallback: Get real user home, even when running with sudo."""
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


class DiagnosticEngine:
    """
    Central diagnostic engine for MeshForge.

    Thread-safe singleton that provides:
    - Comprehensive system checks (9 categories)
    - Real-time callbacks for GUI/Web updates
    - Persistent event logging
    - Health monitoring
    - Report generation
    """

    _instance = None
    _lock = threading.Lock()

    # === Singleton ===

    @classmethod
    def get_instance(cls) -> 'DiagnosticEngine':
        """Get the singleton engine instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        if DiagnosticEngine._instance is not None:
            raise RuntimeError("Use DiagnosticEngine.get_instance()")

        # Results storage
        self._results: Dict[str, CheckResult] = {}
        self._results_lock = threading.Lock()

        # Health by subsystem
        self._health: Dict[str, SubsystemHealth] = {}
        self._health_lock = threading.Lock()

        # Event log (ring buffer)
        self._events: deque = deque(maxlen=1000)
        self._events_lock = threading.Lock()

        # Callbacks
        self._check_callbacks: List[CheckCallback] = []
        self._health_callbacks: List[HealthCallback] = []
        self._event_callbacks: List[EventCallback] = []
        self._progress_callbacks: List[ProgressCallback] = []
        self._callbacks_lock = threading.Lock()

        # Background monitoring
        self._monitor_running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_interval = 30  # seconds

        # Paths
        self._diag_dir = _get_real_user_home() / '.config' / 'meshforge' / 'diagnostics'
        self._ensure_dirs()

        logger.info("DiagnosticEngine initialized")

    def _ensure_dirs(self):
        """Create diagnostic directories."""
        try:
            self._diag_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create diagnostic directory: {e}")

    # === Callback Registration ===

    def register_check_callback(self, callback: CheckCallback):
        """Register callback for individual check results."""
        with self._callbacks_lock:
            self._check_callbacks.append(callback)

    def register_health_callback(self, callback: HealthCallback):
        """Register callback for subsystem health changes."""
        with self._callbacks_lock:
            self._health_callbacks.append(callback)

    def register_event_callback(self, callback: EventCallback):
        """Register callback for diagnostic events."""
        with self._callbacks_lock:
            self._event_callbacks.append(callback)

    def register_progress_callback(self, callback: ProgressCallback):
        """Register callback for progress updates."""
        with self._callbacks_lock:
            self._progress_callbacks.append(callback)

    def _notify_check(self, result: CheckResult):
        """Notify all check callbacks."""
        with self._callbacks_lock:
            callbacks = list(self._check_callbacks)
        for cb in callbacks:
            try:
                cb(result)
            except Exception as e:
                logger.error(f"Check callback error: {e}")

    def _notify_health(self, name: str, health: SubsystemHealth):
        """Notify all health callbacks."""
        with self._callbacks_lock:
            callbacks = list(self._health_callbacks)
        for cb in callbacks:
            try:
                cb(name, health)
            except Exception as e:
                logger.error(f"Health callback error: {e}")

    def _notify_progress(self, category: str, current: int, total: int):
        """Notify all progress callbacks."""
        with self._callbacks_lock:
            callbacks = list(self._progress_callbacks)
        for cb in callbacks:
            try:
                cb(category, current, total)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    # === Check Execution ===

    def run_all(self, async_mode: bool = False) -> List[CheckResult]:
        """
        Run all diagnostic checks across all categories.

        Args:
            async_mode: If True, run in background thread and return immediately

        Returns:
            List of all CheckResults (empty list if async_mode=True)
        """
        if async_mode:
            threading.Thread(target=self._run_all_internal, daemon=True).start()
            return []
        return self._run_all_internal()

    def _run_all_internal(self) -> List[CheckResult]:
        """Internal implementation of run_all."""
        all_results = []
        categories = list(CheckCategory)

        for i, category in enumerate(categories):
            self._notify_progress(category.value, i + 1, len(categories))
            results = self.run_category(category)
            all_results.extend(results)

        return all_results

    def run_category(self, category: CheckCategory) -> List[CheckResult]:
        """Run all checks in a specific category."""
        check_map = {
            CheckCategory.SERVICES: self._run_services_checks,
            CheckCategory.NETWORK: self._run_network_checks,
            CheckCategory.RNS: self._run_rns_checks,
            CheckCategory.MESHTASTIC: self._run_meshtastic_checks,
            CheckCategory.SERIAL: self._run_serial_checks,
            CheckCategory.HARDWARE: self._run_hardware_checks,
            CheckCategory.SYSTEM: self._run_system_checks,
            CheckCategory.HAM_RADIO: self._run_ham_radio_checks,
            CheckCategory.LOGS: self._run_logs_checks,
        }

        check_fn = check_map.get(category)
        if check_fn:
            return check_fn()
        return []

    # === Category Check Implementations ===

    def _run_services_checks(self) -> List[CheckResult]:
        """Check system services."""
        results = []

        # meshtasticd
        results.append(self._check_service('meshtasticd', 'Meshtastic daemon'))

        # rnsd
        results.append(self._check_process('rnsd', 'RNS daemon'))

        # nomadnet
        results.append(self._check_process('nomadnet', 'NomadNet'))

        # bluetooth
        results.append(self._check_service('bluetooth', 'Bluetooth'))

        self._update_subsystem_health('services', results)
        return results

    def _run_network_checks(self) -> List[CheckResult]:
        """Check network connectivity."""
        results = []

        # Internet connectivity
        results.append(self._check_internet())

        # DNS resolution
        results.append(self._check_dns())

        # Meshtasticd API port
        results.append(self._check_tcp_port(4403, 'meshtasticd API'))

        # Meshtasticd Web UI port
        results.append(self._check_tcp_port(9443, 'meshtasticd Web UI', optional=True))

        # MQTT (optional)
        results.append(self._check_tcp_port(1883, 'MQTT broker', optional=True))

        self._update_subsystem_health('network', results)
        return results

    def _run_rns_checks(self) -> List[CheckResult]:
        """Check Reticulum/RNS."""
        results = []

        # RNS installation
        results.append(self._check_rns_installed())

        # RNS config file
        results.append(self._check_rns_config())

        # rnsd running
        results.append(self._check_process('rnsd', 'RNS daemon'))

        # Port 29716 availability (AutoInterface)
        results.append(self._check_rns_port())

        # Meshtastic interface file
        results.append(self._check_meshtastic_interface_file())

        self._update_subsystem_health('rns', results)
        return results

    def _run_meshtastic_checks(self) -> List[CheckResult]:
        """Check Meshtastic."""
        results = []

        # Library installed
        results.append(self._check_meshtastic_installed())

        # CLI available
        results.append(self._check_meshtastic_cli())

        # Can connect via TCP
        results.append(self._check_meshtastic_connection())

        self._update_subsystem_health('meshtastic', results)
        return results

    def _run_serial_checks(self) -> List[CheckResult]:
        """Check serial ports."""
        results = []

        # Available ports
        results.append(self._check_serial_ports())

        # Dialout group membership
        results.append(self._check_dialout_group())

        self._update_subsystem_health('serial', results)
        return results

    def _run_hardware_checks(self) -> List[CheckResult]:
        """Check hardware interfaces."""
        results = []

        # SPI enabled
        results.append(self._check_spi())

        # I2C enabled
        results.append(self._check_i2c())

        # Temperature (Raspberry Pi)
        results.append(self._check_temperature())

        # SDR devices (optional)
        results.append(self._check_sdr())

        self._update_subsystem_health('hardware', results)
        return results

    def _run_system_checks(self) -> List[CheckResult]:
        """Check system resources."""
        results = []

        # Python version
        results.append(self._check_python_version())

        # Required packages
        results.append(self._check_pip_packages())

        # Memory
        results.append(self._check_memory())

        # Disk space
        results.append(self._check_disk_space())

        # CPU load
        results.append(self._check_cpu_load())

        self._update_subsystem_health('system', results)
        return results

    def _run_ham_radio_checks(self) -> List[CheckResult]:
        """Check HAM radio configuration."""
        results = []

        # Callsign configured
        results.append(self._check_callsign())

        self._update_subsystem_health('ham_radio', results)
        return results

    def _run_logs_checks(self) -> List[CheckResult]:
        """Analyze logs for errors."""
        results = []

        # Recent errors in meshtasticd
        results.append(self._check_service_logs('meshtasticd'))

        self._update_subsystem_health('logs', results)
        return results

    # === Individual Check Implementations ===

    def _check_service(self, service: str, display_name: str) -> CheckResult:
        """Check if a systemd service is running."""
        start = time.time()
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', service],
                capture_output=True, text=True, timeout=5
            )
            status_str = result.stdout.strip()
            duration = (time.time() - start) * 1000

            if status_str == 'active':
                return CheckResult(
                    name=f"{display_name}",
                    category=CheckCategory.SERVICES,
                    status=CheckStatus.PASS,
                    message="Service running",
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name=f"{display_name}",
                    category=CheckCategory.SERVICES,
                    status=CheckStatus.FAIL,
                    message=f"Service {status_str}",
                    fix_hint=f"sudo systemctl start {service}",
                    duration_ms=duration
                )
        except FileNotFoundError:
            return CheckResult(
                name=f"{display_name}",
                category=CheckCategory.SERVICES,
                status=CheckStatus.SKIP,
                message="systemctl not found",
                duration_ms=(time.time() - start) * 1000
            )
        except Exception as e:
            return CheckResult(
                name=f"{display_name}",
                category=CheckCategory.SERVICES,
                status=CheckStatus.FAIL,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    def _check_process(self, process: str, display_name: str) -> CheckResult:
        """Check if a process is running."""
        start = time.time()
        try:
            result = subprocess.run(
                ['pgrep', '-x', process],
                capture_output=True, text=True, timeout=5
            )
            duration = (time.time() - start) * 1000

            if result.returncode == 0:
                pids = result.stdout.strip().split('\n')
                return CheckResult(
                    name=f"{display_name}",
                    category=CheckCategory.SERVICES,
                    status=CheckStatus.PASS,
                    message=f"Running (PID: {pids[0]})",
                    details={"pids": pids},
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name=f"{display_name}",
                    category=CheckCategory.SERVICES,
                    status=CheckStatus.WARN,
                    message="Not running",
                    fix_hint=f"Start {process} if needed",
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name=f"{display_name}",
                category=CheckCategory.SERVICES,
                status=CheckStatus.FAIL,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    def _check_tcp_port(self, port: int, name: str, optional: bool = False) -> CheckResult:
        """Check if a TCP port is listening."""
        start = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            duration = (time.time() - start) * 1000

            if result == 0:
                return CheckResult(
                    name=f"{name} (:{port})",
                    category=CheckCategory.NETWORK,
                    status=CheckStatus.PASS,
                    message=f"Listening",
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name=f"{name} (:{port})",
                    category=CheckCategory.NETWORK,
                    status=CheckStatus.SKIP if optional else CheckStatus.FAIL,
                    message="Not reachable",
                    fix_hint=f"Ensure {name} is running",
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name=f"{name} (:{port})",
                category=CheckCategory.NETWORK,
                status=CheckStatus.FAIL,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    def _check_internet(self) -> CheckResult:
        """Check internet connectivity."""
        start = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('8.8.8.8', 53))
            sock.close()
            duration = (time.time() - start) * 1000

            if result == 0:
                return CheckResult(
                    name="Internet connectivity",
                    category=CheckCategory.NETWORK,
                    status=CheckStatus.PASS,
                    message="Connected",
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name="Internet connectivity",
                    category=CheckCategory.NETWORK,
                    status=CheckStatus.WARN,
                    message="No connection",
                    fix_hint="Check network configuration",
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name="Internet connectivity",
                category=CheckCategory.NETWORK,
                status=CheckStatus.WARN,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    def _check_dns(self) -> CheckResult:
        """Check DNS resolution."""
        start = time.time()
        try:
            socket.gethostbyname('google.com')
            duration = (time.time() - start) * 1000
            return CheckResult(
                name="DNS resolution",
                category=CheckCategory.NETWORK,
                status=CheckStatus.PASS,
                message="Working",
                duration_ms=duration
            )
        except socket.gaierror:
            return CheckResult(
                name="DNS resolution",
                category=CheckCategory.NETWORK,
                status=CheckStatus.WARN,
                message="DNS failed",
                fix_hint="Check /etc/resolv.conf",
                duration_ms=(time.time() - start) * 1000
            )

    def _check_rns_installed(self) -> CheckResult:
        """Check if RNS is installed."""
        start = time.time()
        try:
            import importlib
            importlib.import_module('RNS')
            duration = (time.time() - start) * 1000
            return CheckResult(
                name="RNS library",
                category=CheckCategory.RNS,
                status=CheckStatus.PASS,
                message="Installed",
                duration_ms=duration
            )
        except ImportError:
            return CheckResult(
                name="RNS library",
                category=CheckCategory.RNS,
                status=CheckStatus.FAIL,
                message="Not installed",
                fix_hint="pip3 install rns",
                duration_ms=(time.time() - start) * 1000
            )

    def _check_rns_config(self) -> CheckResult:
        """Check RNS configuration file."""
        start = time.time()
        config_path = _get_real_user_home() / '.reticulum' / 'config'

        if config_path.exists():
            try:
                content = config_path.read_text()
                has_interface = '[interface' in content.lower() or '[[' in content
                duration = (time.time() - start) * 1000

                if has_interface:
                    return CheckResult(
                        name="RNS config",
                        category=CheckCategory.RNS,
                        status=CheckStatus.PASS,
                        message=f"Found at {config_path}",
                        details={"path": str(config_path)},
                        duration_ms=duration
                    )
                else:
                    return CheckResult(
                        name="RNS config",
                        category=CheckCategory.RNS,
                        status=CheckStatus.WARN,
                        message="No interfaces configured",
                        fix_hint="Add interface to ~/.reticulum/config",
                        duration_ms=duration
                    )
            except Exception as e:
                return CheckResult(
                    name="RNS config",
                    category=CheckCategory.RNS,
                    status=CheckStatus.FAIL,
                    message=f"Read error: {e}",
                    duration_ms=(time.time() - start) * 1000
                )
        else:
            return CheckResult(
                name="RNS config",
                category=CheckCategory.RNS,
                status=CheckStatus.WARN,
                message="Not found (will be created on first run)",
                fix_hint="Run rnsd once to generate config",
                duration_ms=(time.time() - start) * 1000
            )

    def _check_rns_port(self) -> CheckResult:
        """Check if RNS AutoInterface port (29716) is available."""
        start = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(('0.0.0.0', 29716))
            sock.close()
            duration = (time.time() - start) * 1000
            return CheckResult(
                name="RNS AutoInterface port",
                category=CheckCategory.RNS,
                status=CheckStatus.PASS,
                message="Port 29716 available",
                duration_ms=duration
            )
        except OSError as e:
            if e.errno == 98:  # Address already in use
                return CheckResult(
                    name="RNS AutoInterface port",
                    category=CheckCategory.RNS,
                    status=CheckStatus.PASS,
                    message="Port in use (rnsd running)",
                    duration_ms=(time.time() - start) * 1000
                )
            return CheckResult(
                name="RNS AutoInterface port",
                category=CheckCategory.RNS,
                status=CheckStatus.FAIL,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    def _check_meshtastic_interface_file(self) -> CheckResult:
        """Check for Meshtastic_Interface.py in RNS config."""
        start = time.time()
        rns_dir = _get_real_user_home() / '.reticulum'
        interface_file = rns_dir / 'Meshtastic_Interface.py'

        if interface_file.exists():
            return CheckResult(
                name="Meshtastic Interface",
                category=CheckCategory.RNS,
                status=CheckStatus.PASS,
                message="Interface file found",
                details={"path": str(interface_file)},
                duration_ms=(time.time() - start) * 1000
            )
        else:
            return CheckResult(
                name="Meshtastic Interface",
                category=CheckCategory.RNS,
                status=CheckStatus.SKIP,
                message="Not installed (optional)",
                fix_hint="Install RNS_Over_Meshtastic_Gateway for LoRa bridge",
                duration_ms=(time.time() - start) * 1000
            )

    def _check_meshtastic_installed(self) -> CheckResult:
        """Check if meshtastic library is installed."""
        start = time.time()
        try:
            import importlib
            importlib.import_module('meshtastic')
            duration = (time.time() - start) * 1000
            return CheckResult(
                name="Meshtastic library",
                category=CheckCategory.MESHTASTIC,
                status=CheckStatus.PASS,
                message="Installed",
                duration_ms=duration
            )
        except ImportError:
            return CheckResult(
                name="Meshtastic library",
                category=CheckCategory.MESHTASTIC,
                status=CheckStatus.FAIL,
                message="Not installed",
                fix_hint="pip3 install meshtastic",
                duration_ms=(time.time() - start) * 1000
            )

    def _check_meshtastic_cli(self) -> CheckResult:
        """Check if meshtastic CLI is available."""
        start = time.time()
        cli_path = shutil.which('meshtastic')

        if cli_path:
            return CheckResult(
                name="Meshtastic CLI",
                category=CheckCategory.MESHTASTIC,
                status=CheckStatus.PASS,
                message=f"Found at {cli_path}",
                details={"path": cli_path},
                duration_ms=(time.time() - start) * 1000
            )
        else:
            # Check user local bin
            local_bin = _get_real_user_home() / '.local' / 'bin' / 'meshtastic'
            if local_bin.exists():
                return CheckResult(
                    name="Meshtastic CLI",
                    category=CheckCategory.MESHTASTIC,
                    status=CheckStatus.PASS,
                    message=f"Found at {local_bin}",
                    details={"path": str(local_bin)},
                    duration_ms=(time.time() - start) * 1000
                )
            return CheckResult(
                name="Meshtastic CLI",
                category=CheckCategory.MESHTASTIC,
                status=CheckStatus.WARN,
                message="Not in PATH",
                fix_hint="pip3 install meshtastic (includes CLI)",
                duration_ms=(time.time() - start) * 1000
            )

    def _check_meshtastic_connection(self) -> CheckResult:
        """Check if we can connect to a Meshtastic device."""
        start = time.time()

        # Try TCP first (meshtasticd)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', 4403))
            sock.close()

            if result == 0:
                return CheckResult(
                    name="Meshtastic connection",
                    category=CheckCategory.MESHTASTIC,
                    status=CheckStatus.PASS,
                    message="TCP connection available (meshtasticd)",
                    details={"method": "tcp", "port": 4403},
                    duration_ms=(time.time() - start) * 1000
                )
        except Exception:
            pass

        # Check for serial devices
        serial_devices = self._find_serial_devices()
        if serial_devices:
            return CheckResult(
                name="Meshtastic connection",
                category=CheckCategory.MESHTASTIC,
                status=CheckStatus.PASS,
                message=f"Serial device found: {serial_devices[0]}",
                details={"method": "serial", "devices": serial_devices},
                duration_ms=(time.time() - start) * 1000
            )

        return CheckResult(
            name="Meshtastic connection",
            category=CheckCategory.MESHTASTIC,
            status=CheckStatus.FAIL,
            message="No connection available",
            fix_hint="Start meshtasticd or connect device via USB",
            duration_ms=(time.time() - start) * 1000
        )

    def _check_serial_ports(self) -> CheckResult:
        """Check for available serial ports."""
        start = time.time()
        devices = self._find_serial_devices()
        duration = (time.time() - start) * 1000

        if devices:
            return CheckResult(
                name="Serial ports",
                category=CheckCategory.SERIAL,
                status=CheckStatus.PASS,
                message=f"Found: {', '.join(devices)}",
                details={"devices": devices},
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="Serial ports",
                category=CheckCategory.SERIAL,
                status=CheckStatus.WARN,
                message="No Meshtastic devices found",
                fix_hint="Connect device via USB",
                duration_ms=duration
            )

    def _find_serial_devices(self) -> List[str]:
        """Find Meshtastic-compatible serial devices."""
        devices = []
        dev_path = Path('/dev')

        # Common patterns
        patterns = ['ttyACM*', 'ttyUSB*']

        for pattern in patterns:
            devices.extend([str(d) for d in dev_path.glob(pattern)])

        return devices

    def _check_dialout_group(self) -> CheckResult:
        """Check if user is in dialout group."""
        start = time.time()
        try:
            import grp
            username = os.environ.get('SUDO_USER', os.environ.get('USER', 'root'))
            groups = [g.gr_name for g in grp.getgrall() if username in g.gr_mem]

            # Also check primary group
            try:
                import pwd
                user_info = pwd.getpwnam(username)
                primary_group = grp.getgrgid(user_info.pw_gid).gr_name
                groups.append(primary_group)
            except Exception:
                pass

            duration = (time.time() - start) * 1000

            if 'dialout' in groups:
                return CheckResult(
                    name="Dialout group",
                    category=CheckCategory.SERIAL,
                    status=CheckStatus.PASS,
                    message=f"User {username} in dialout group",
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name="Dialout group",
                    category=CheckCategory.SERIAL,
                    status=CheckStatus.WARN,
                    message=f"User {username} not in dialout group",
                    fix_hint=f"sudo usermod -a -G dialout {username} (then logout/login)",
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name="Dialout group",
                category=CheckCategory.SERIAL,
                status=CheckStatus.SKIP,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    def _check_spi(self) -> CheckResult:
        """Check if SPI is enabled."""
        start = time.time()
        spi_devices = list(Path('/dev').glob('spidev*'))
        duration = (time.time() - start) * 1000

        if spi_devices:
            return CheckResult(
                name="SPI interface",
                category=CheckCategory.HARDWARE,
                status=CheckStatus.PASS,
                message=f"Enabled ({len(spi_devices)} device(s))",
                details={"devices": [str(d) for d in spi_devices]},
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="SPI interface",
                category=CheckCategory.HARDWARE,
                status=CheckStatus.SKIP,
                message="Not enabled or not a Pi",
                fix_hint="Enable SPI in raspi-config if needed",
                duration_ms=duration
            )

    def _check_i2c(self) -> CheckResult:
        """Check if I2C is enabled."""
        start = time.time()
        i2c_devices = list(Path('/dev').glob('i2c-*'))
        duration = (time.time() - start) * 1000

        if i2c_devices:
            return CheckResult(
                name="I2C interface",
                category=CheckCategory.HARDWARE,
                status=CheckStatus.PASS,
                message=f"Enabled ({len(i2c_devices)} bus(es))",
                details={"devices": [str(d) for d in i2c_devices]},
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="I2C interface",
                category=CheckCategory.HARDWARE,
                status=CheckStatus.SKIP,
                message="Not enabled or not a Pi",
                fix_hint="Enable I2C in raspi-config if needed",
                duration_ms=duration
            )

    def _check_temperature(self) -> CheckResult:
        """Check CPU temperature (Raspberry Pi)."""
        start = time.time()
        temp_file = Path('/sys/class/thermal/thermal_zone0/temp')

        if temp_file.exists():
            try:
                temp_raw = temp_file.read_text().strip()
                temp_c = int(temp_raw) / 1000
                duration = (time.time() - start) * 1000

                if temp_c >= 80:
                    return CheckResult(
                        name="CPU temperature",
                        category=CheckCategory.HARDWARE,
                        status=CheckStatus.FAIL,
                        message=f"{temp_c:.1f}°C (CRITICAL)",
                        fix_hint="Add cooling or reduce load",
                        details={"temp_c": temp_c},
                        duration_ms=duration
                    )
                elif temp_c >= 70:
                    return CheckResult(
                        name="CPU temperature",
                        category=CheckCategory.HARDWARE,
                        status=CheckStatus.WARN,
                        message=f"{temp_c:.1f}°C (warm)",
                        details={"temp_c": temp_c},
                        duration_ms=duration
                    )
                else:
                    return CheckResult(
                        name="CPU temperature",
                        category=CheckCategory.HARDWARE,
                        status=CheckStatus.PASS,
                        message=f"{temp_c:.1f}°C",
                        details={"temp_c": temp_c},
                        duration_ms=duration
                    )
            except Exception as e:
                return CheckResult(
                    name="CPU temperature",
                    category=CheckCategory.HARDWARE,
                    status=CheckStatus.FAIL,
                    message=str(e),
                    duration_ms=(time.time() - start) * 1000
                )
        else:
            return CheckResult(
                name="CPU temperature",
                category=CheckCategory.HARDWARE,
                status=CheckStatus.SKIP,
                message="Not a Raspberry Pi",
                duration_ms=(time.time() - start) * 1000
            )

    def _check_sdr(self) -> CheckResult:
        """Check for SDR devices."""
        start = time.time()
        rtl_path = shutil.which('rtl_test')

        if rtl_path:
            try:
                result = subprocess.run(
                    ['rtl_test', '-t'],
                    capture_output=True, text=True, timeout=5
                )
                duration = (time.time() - start) * 1000

                if 'Found' in result.stderr or 'Found' in result.stdout:
                    return CheckResult(
                        name="RTL-SDR",
                        category=CheckCategory.HARDWARE,
                        status=CheckStatus.PASS,
                        message="Device found",
                        duration_ms=duration
                    )
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass

        return CheckResult(
            name="RTL-SDR",
            category=CheckCategory.HARDWARE,
            status=CheckStatus.SKIP,
            message="Not installed or no device",
            duration_ms=(time.time() - start) * 1000
        )

    def _check_python_version(self) -> CheckResult:
        """Check Python version."""
        import sys
        start = time.time()
        version = sys.version_info
        version_str = f"{version.major}.{version.minor}.{version.micro}"
        duration = (time.time() - start) * 1000

        if version >= (3, 9):
            return CheckResult(
                name="Python version",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.PASS,
                message=version_str,
                duration_ms=duration
            )
        elif version >= (3, 8):
            return CheckResult(
                name="Python version",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.WARN,
                message=f"{version_str} (3.9+ recommended)",
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="Python version",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.FAIL,
                message=f"{version_str} (requires 3.8+)",
                fix_hint="Upgrade Python to 3.9+",
                duration_ms=duration
            )

    def _check_pip_packages(self) -> CheckResult:
        """Check required pip packages."""
        start = time.time()
        required = {
            'meshtastic': 'meshtastic',
            'rns': 'RNS',
            'lxmf': 'LXMF',
        }
        installed = []
        missing = []

        import importlib
        for display_name, module_name in required.items():
            try:
                importlib.import_module(module_name)
                installed.append(display_name)
            except ImportError:
                missing.append(display_name)

        duration = (time.time() - start) * 1000

        if not missing:
            return CheckResult(
                name="Required packages",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.PASS,
                message=f"All installed ({len(installed)})",
                details={"installed": installed},
                duration_ms=duration
            )
        else:
            return CheckResult(
                name="Required packages",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.WARN,
                message=f"Missing: {', '.join(missing)}",
                fix_hint=f"pip3 install {' '.join(missing)}",
                details={"installed": installed, "missing": missing},
                duration_ms=duration
            )

    def _check_memory(self) -> CheckResult:
        """Check available memory."""
        start = time.time()
        try:
            with open('/proc/meminfo') as f:
                lines = f.readlines()

            mem_info = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(':')
                    value = int(parts[1])
                    mem_info[key] = value

            total_mb = mem_info.get('MemTotal', 0) / 1024
            available_mb = mem_info.get('MemAvailable', 0) / 1024
            percent_free = (available_mb / total_mb * 100) if total_mb > 0 else 0
            duration = (time.time() - start) * 1000

            if percent_free < 10:
                return CheckResult(
                    name="Memory",
                    category=CheckCategory.SYSTEM,
                    status=CheckStatus.FAIL,
                    message=f"{available_mb:.0f}MB free ({percent_free:.0f}%)",
                    fix_hint="Free up memory or add swap",
                    details={"total_mb": total_mb, "available_mb": available_mb},
                    duration_ms=duration
                )
            elif percent_free < 25:
                return CheckResult(
                    name="Memory",
                    category=CheckCategory.SYSTEM,
                    status=CheckStatus.WARN,
                    message=f"{available_mb:.0f}MB free ({percent_free:.0f}%)",
                    details={"total_mb": total_mb, "available_mb": available_mb},
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name="Memory",
                    category=CheckCategory.SYSTEM,
                    status=CheckStatus.PASS,
                    message=f"{available_mb:.0f}MB free ({percent_free:.0f}%)",
                    details={"total_mb": total_mb, "available_mb": available_mb},
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name="Memory",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.FAIL,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    def _check_disk_space(self) -> CheckResult:
        """Check available disk space."""
        start = time.time()
        try:
            stat = os.statvfs('/')
            total_gb = (stat.f_blocks * stat.f_frsize) / (1024**3)
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
            duration = (time.time() - start) * 1000

            if free_gb < 1:
                return CheckResult(
                    name="Disk space",
                    category=CheckCategory.SYSTEM,
                    status=CheckStatus.FAIL,
                    message=f"{free_gb:.1f}GB free (CRITICAL)",
                    fix_hint="Free up disk space",
                    details={"total_gb": total_gb, "free_gb": free_gb},
                    duration_ms=duration
                )
            elif free_gb < 5:
                return CheckResult(
                    name="Disk space",
                    category=CheckCategory.SYSTEM,
                    status=CheckStatus.WARN,
                    message=f"{free_gb:.1f}GB free",
                    details={"total_gb": total_gb, "free_gb": free_gb},
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name="Disk space",
                    category=CheckCategory.SYSTEM,
                    status=CheckStatus.PASS,
                    message=f"{free_gb:.1f}GB free",
                    details={"total_gb": total_gb, "free_gb": free_gb},
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name="Disk space",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.FAIL,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    def _check_cpu_load(self) -> CheckResult:
        """Check CPU load average."""
        start = time.time()
        try:
            load_1, load_5, load_15 = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            load_percent = (load_1 / cpu_count) * 100
            duration = (time.time() - start) * 1000

            if load_percent > 100:
                return CheckResult(
                    name="CPU load",
                    category=CheckCategory.SYSTEM,
                    status=CheckStatus.WARN,
                    message=f"{load_1:.2f} ({load_percent:.0f}% of {cpu_count} cores)",
                    details={"load_1": load_1, "load_5": load_5, "load_15": load_15},
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name="CPU load",
                    category=CheckCategory.SYSTEM,
                    status=CheckStatus.PASS,
                    message=f"{load_1:.2f} ({load_percent:.0f}%)",
                    details={"load_1": load_1, "load_5": load_5, "load_15": load_15},
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name="CPU load",
                category=CheckCategory.SYSTEM,
                status=CheckStatus.SKIP,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    def _check_callsign(self) -> CheckResult:
        """Check if HAM callsign is configured."""
        start = time.time()
        callsign = os.environ.get('CALLSIGN', os.environ.get('HAM_CALLSIGN', ''))
        duration = (time.time() - start) * 1000

        if callsign:
            return CheckResult(
                name="Callsign",
                category=CheckCategory.HAM_RADIO,
                status=CheckStatus.PASS,
                message=callsign,
                details={"callsign": callsign},
                duration_ms=duration
            )
        else:
            # Check NomadNet config
            nomadnet_config = _get_real_user_home() / '.nomadnetwork' / 'config'
            if nomadnet_config.exists():
                try:
                    content = nomadnet_config.read_text()
                    if 'display_name' in content:
                        return CheckResult(
                            name="Callsign",
                            category=CheckCategory.HAM_RADIO,
                            status=CheckStatus.PASS,
                            message="Set in NomadNet config",
                            duration_ms=duration
                        )
                except Exception:
                    pass

            return CheckResult(
                name="Callsign",
                category=CheckCategory.HAM_RADIO,
                status=CheckStatus.SKIP,
                message="Not configured (optional)",
                fix_hint="Set CALLSIGN environment variable",
                duration_ms=duration
            )

    def _check_service_logs(self, service: str) -> CheckResult:
        """Check recent service logs for errors."""
        start = time.time()
        try:
            result = subprocess.run(
                ['journalctl', '-u', service, '--since', '1 hour ago', '-p', 'err', '--no-pager', '-q'],
                capture_output=True, text=True, timeout=10
            )
            duration = (time.time() - start) * 1000

            lines = result.stdout.strip().split('\n') if result.stdout.strip() else []
            error_count = len(lines)

            if error_count == 0:
                return CheckResult(
                    name=f"{service} logs",
                    category=CheckCategory.LOGS,
                    status=CheckStatus.PASS,
                    message="No errors in last hour",
                    duration_ms=duration
                )
            elif error_count < 5:
                return CheckResult(
                    name=f"{service} logs",
                    category=CheckCategory.LOGS,
                    status=CheckStatus.WARN,
                    message=f"{error_count} error(s) in last hour",
                    details={"recent_errors": lines[:3]},
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name=f"{service} logs",
                    category=CheckCategory.LOGS,
                    status=CheckStatus.FAIL,
                    message=f"{error_count} errors in last hour",
                    fix_hint=f"Check: journalctl -u {service} -f",
                    details={"recent_errors": lines[:5]},
                    duration_ms=duration
                )
        except Exception as e:
            return CheckResult(
                name=f"{service} logs",
                category=CheckCategory.LOGS,
                status=CheckStatus.SKIP,
                message=str(e),
                duration_ms=(time.time() - start) * 1000
            )

    # === Health Management ===

    def _update_subsystem_health(self, name: str, checks: List[CheckResult]):
        """Update health status for a subsystem based on check results."""
        fail_count = sum(1 for c in checks if c.status == CheckStatus.FAIL)
        warn_count = sum(1 for c in checks if c.status == CheckStatus.WARN)

        if fail_count > 0:
            status = HealthStatus.UNHEALTHY
            message = f"{fail_count} failed check(s)"
        elif warn_count > 0:
            status = HealthStatus.DEGRADED
            message = f"{warn_count} warning(s)"
        else:
            status = HealthStatus.HEALTHY
            message = "All checks passed"

        # Get first fix hint from failed checks
        fix_hint = None
        for c in checks:
            if c.status == CheckStatus.FAIL and c.fix_hint:
                fix_hint = c.fix_hint
                break

        health = SubsystemHealth(
            name=name,
            status=status,
            message=message,
            checks=checks,
            last_check=datetime.now(),
            fix_hint=fix_hint
        )

        with self._health_lock:
            old_health = self._health.get(name)
            self._health[name] = health

        # Notify callbacks if status changed
        if old_health is None or old_health.status != status:
            self._notify_health(name, health)

        # Store results and notify
        with self._results_lock:
            for check in checks:
                self._results[f"{name}.{check.name}"] = check
                self._notify_check(check)

    def get_overall_health(self) -> HealthStatus:
        """Calculate overall system health from subsystems."""
        with self._health_lock:
            if not self._health:
                return HealthStatus.UNKNOWN

            statuses = [h.status for h in self._health.values()]

            if any(s == HealthStatus.UNHEALTHY for s in statuses):
                return HealthStatus.UNHEALTHY
            elif any(s == HealthStatus.DEGRADED for s in statuses):
                return HealthStatus.DEGRADED
            elif all(s == HealthStatus.HEALTHY for s in statuses):
                return HealthStatus.HEALTHY
            return HealthStatus.UNKNOWN

    # === Event Logging ===

    def log_event(
        self,
        severity: EventSeverity,
        source: str,
        message: str,
        category: Optional[CheckCategory] = None,
        details: Optional[Dict] = None,
        fix_hint: Optional[str] = None
    ) -> DiagnosticEvent:
        """Log a diagnostic event."""
        event = DiagnosticEvent(
            timestamp=datetime.now(),
            severity=severity,
            source=source,
            message=message,
            category=category,
            details=details,
            fix_hint=fix_hint
        )

        with self._events_lock:
            self._events.append(event)

        # Persist to file
        self._write_event_to_file(event)

        # Notify callbacks
        with self._callbacks_lock:
            callbacks = list(self._event_callbacks)
        for cb in callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")

        return event

    def _write_event_to_file(self, event: DiagnosticEvent):
        """Write event to daily log file."""
        try:
            log_file = self._diag_dir / f"events_{datetime.now().strftime('%Y%m%d')}.log"
            with open(log_file, 'a') as f:
                f.write(event.to_log_line() + "\n")
        except Exception as e:
            logger.debug(f"Failed to write event to file: {e}")

    # === Queries ===

    def get_health(self, subsystem: Optional[str] = None) -> Dict[str, SubsystemHealth]:
        """Get health status. If subsystem specified, get just that one."""
        with self._health_lock:
            if subsystem:
                return {subsystem: self._health.get(subsystem)} if subsystem in self._health else {}
            return dict(self._health)

    def get_results(
        self,
        category: Optional[CheckCategory] = None,
        status: Optional[CheckStatus] = None
    ) -> List[CheckResult]:
        """Get check results with optional filtering."""
        with self._results_lock:
            results = list(self._results.values())

        if category:
            results = [r for r in results if r.category == category]
        if status:
            results = [r for r in results if r.status == status]

        return results

    def get_events(
        self,
        severity: Optional[EventSeverity] = None,
        since: Optional[datetime] = None,
        limit: int = 100
    ) -> List[DiagnosticEvent]:
        """Get diagnostic events with optional filtering."""
        with self._events_lock:
            events = list(self._events)

        if severity:
            events = [e for e in events if e.severity == severity]
        if since:
            events = [e for e in events if e.timestamp >= since]

        return sorted(events, key=lambda e: e.timestamp, reverse=True)[:limit]

    # === Report Generation ===

    def generate_report(self) -> DiagnosticReport:
        """Generate comprehensive diagnostic report."""
        all_checks = self.get_results()

        # Summary counts
        summary = {
            'total': len(all_checks),
            'passed': sum(1 for c in all_checks if c.status == CheckStatus.PASS),
            'failed': sum(1 for c in all_checks if c.status == CheckStatus.FAIL),
            'warnings': sum(1 for c in all_checks if c.status == CheckStatus.WARN),
            'skipped': sum(1 for c in all_checks if c.status == CheckStatus.SKIP),
        }

        # Recommendations from failed checks
        recommendations = []
        for check in all_checks:
            if check.status == CheckStatus.FAIL and check.fix_hint:
                recommendations.append(f"[{check.category.value}] {check.fix_hint}")

        # Recent events
        hour_ago = datetime.now() - timedelta(hours=1)
        recent_events = self.get_events(since=hour_ago)

        return DiagnosticReport(
            generated_at=datetime.now(),
            overall_health=self.get_overall_health(),
            subsystems=self.get_health(),
            all_checks=all_checks,
            recent_events=recent_events,
            recommendations=recommendations[:10],
            summary=summary
        )

    def save_report(self, filename: Optional[str] = None) -> Path:
        """Save diagnostic report to JSON file."""
        if not filename:
            filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        report_path = self._diag_dir / filename
        report = self.generate_report()

        with open(report_path, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)

        return report_path

    # === Background Monitoring ===

    def start_monitoring(self, interval: int = 30):
        """Start background health monitoring."""
        self._monitor_interval = interval
        self._monitor_running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info(f"Background monitoring started (interval: {interval}s)")

    def stop_monitoring(self):
        """Stop background monitoring."""
        self._monitor_running = False
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
        logger.info("Background monitoring stopped")

    def _monitor_loop(self):
        """Background monitoring loop."""
        while self._monitor_running:
            try:
                # Run critical checks only (services, network)
                self._run_services_checks()
                self._run_network_checks()
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
            time.sleep(self._monitor_interval)

    # === Wizard Support ===

    def run_wizard(self, wizard_type: str = 'gateway') -> Dict:
        """
        Run diagnostic wizard.

        Returns structured data for UI to render wizard flow.
        """
        # Run relevant checks
        if wizard_type == 'gateway':
            categories = [CheckCategory.RNS, CheckCategory.MESHTASTIC, CheckCategory.SERIAL]
        else:
            categories = list(CheckCategory)

        all_results = []
        for cat in categories:
            all_results.extend(self.run_category(cat))

        # Analyze for recommendations
        failures = [r for r in all_results if r.status == CheckStatus.FAIL]
        warnings = [r for r in all_results if r.status == CheckStatus.WARN]

        # Detect available connection types
        serial_devices = self._find_serial_devices()
        tcp_available = any(
            r.status == CheckStatus.PASS and '4403' in r.name
            for r in all_results
        )

        return {
            'results': [r.to_dict() for r in all_results],
            'failures': [r.to_dict() for r in failures],
            'warnings': [r.to_dict() for r in warnings],
            'connection_options': {
                'serial': serial_devices,
                'tcp': tcp_available,
            },
            'recommended_connection': 'tcp' if tcp_available else 'serial' if serial_devices else None,
            'ready': len(failures) == 0
        }
