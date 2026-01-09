"""
MeshChat Service Detection and Management

Follows MeshForge patterns for service lifecycle management:
- Background thread checks (UI stays responsive)
- Multiple service name fallbacks
- pgrep fallback for non-systemd systems
- Thread-safe status updates via callbacks
"""

import logging
import os
import socket
import subprocess
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any

logger = logging.getLogger(__name__)

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        """Fallback for when utils.paths is not available."""
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


class ServiceState(Enum):
    """Service lifecycle states."""
    UNKNOWN = "unknown"
    NOT_INSTALLED = "not_installed"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class ServiceStatus:
    """Complete service status information."""
    state: ServiceState = ServiceState.UNKNOWN
    installed: bool = False
    running: bool = False
    port_open: bool = False
    service_name: Optional[str] = None
    pid: Optional[int] = None
    version: Optional[str] = None
    error: Optional[str] = None
    fix_hint: Optional[str] = None

    @property
    def available(self) -> bool:
        """True if service is running and reachable."""
        return self.running and self.port_open


class MeshChatService:
    """
    MeshChat service detection and management.

    Handles systemd service detection with fallback to process detection.
    All checks run in background threads to keep UI responsive.

    Usage:
        service = MeshChatService()
        service.check_status(callback=update_ui)

        if service.last_status.available:
            # Service is ready for API calls
            pass
    """

    # Service configuration
    DEFAULT_PORT = 8000
    SERVICE_NAMES = ['reticulum-meshchat', 'meshchat']
    # Use specific patterns to avoid matching grep/pgrep commands
    PROCESS_PATTERNS = ['python.*meshchat', 'meshchat.py']

    # Installation hints
    INSTALL_URL = "https://github.com/liamcottle/reticulum-meshchat"
    INSTALL_HINT = (
        "Install MeshChat:\n"
        "  git clone https://github.com/liamcottle/reticulum-meshchat\n"
        "  cd reticulum-meshchat\n"
        "  pip install -r requirements.txt\n"
        "  npm install --omit=dev && npm run build-frontend\n"
        "  python meshchat.py"
    )

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = DEFAULT_PORT
    ):
        self.host = host
        self.port = port
        self._last_status: Optional[ServiceStatus] = None
        self._check_lock = threading.Lock()

    @property
    def last_status(self) -> ServiceStatus:
        """Get last known status (may be stale)."""
        if self._last_status is None:
            return ServiceStatus(state=ServiceState.UNKNOWN)
        return self._last_status

    def check_status(
        self,
        callback: Optional[Callable[[ServiceStatus], None]] = None,
        blocking: bool = False
    ) -> Optional[ServiceStatus]:
        """
        Check MeshChat service status.

        Args:
            callback: Function to call with status (for async updates)
            blocking: If True, run synchronously and return status

        Returns:
            ServiceStatus if blocking=True, otherwise None
        """
        if blocking:
            return self._do_check()

        def check_thread():
            status = self._do_check()
            if callback:
                callback(status)

        thread = threading.Thread(target=check_thread, daemon=True)
        thread.start()
        return None

    def _do_check(self) -> ServiceStatus:
        """Perform the actual service check."""
        with self._check_lock:
            status = ServiceStatus()

            # Step 1: Check systemd service
            service_info = self._check_systemd_service()
            status.installed = service_info['installed']
            status.service_name = service_info['service_name']

            if service_info['running']:
                status.running = True
                status.pid = service_info.get('pid')

            # Step 2: Fallback to process detection
            if not status.running:
                proc_info = self._check_process()
                if proc_info['running']:
                    status.running = True
                    status.pid = proc_info.get('pid')
                    if not status.installed:
                        status.installed = True  # Running means it's installed somehow

            # Step 3: Check port accessibility
            status.port_open = self._check_port()

            # Step 4: Determine state
            if status.running and status.port_open:
                status.state = ServiceState.RUNNING
            elif status.running and not status.port_open:
                status.state = ServiceState.STARTING
                status.error = f"Running but port {self.port} not responding"
            elif status.installed:
                status.state = ServiceState.STOPPED
                status.fix_hint = self._get_start_hint(status.service_name)
            else:
                status.state = ServiceState.NOT_INSTALLED
                status.fix_hint = self.INSTALL_HINT

            # Step 5: Try to get version if running
            if status.running and status.port_open:
                status.version = self._get_version()

            self._last_status = status
            return status

    def _check_systemd_service(self) -> Dict[str, Any]:
        """Check systemd service status."""
        result = {
            'installed': False,
            'running': False,
            'service_name': None,
            'pid': None
        }

        for name in self.SERVICE_NAMES:
            try:
                # Check if service is active
                proc = subprocess.run(
                    ['systemctl', 'is-active', name],
                    capture_output=True, text=True, timeout=5
                )
                if proc.returncode == 0 and proc.stdout.strip() == 'active':
                    result['installed'] = True
                    result['running'] = True
                    result['service_name'] = name

                    # Get PID
                    pid_proc = subprocess.run(
                        ['systemctl', 'show', name, '--property=MainPID'],
                        capture_output=True, text=True, timeout=5
                    )
                    if pid_proc.returncode == 0:
                        try:
                            pid_line = pid_proc.stdout.strip()
                            result['pid'] = int(pid_line.split('=')[1])
                        except (ValueError, IndexError):
                            pass
                    break

                # Check if installed but not running
                check_proc = subprocess.run(
                    ['systemctl', 'is-enabled', name],
                    capture_output=True, text=True, timeout=5
                )
                if check_proc.returncode == 0 or 'disabled' in check_proc.stdout:
                    result['installed'] = True
                    result['service_name'] = name

            except subprocess.TimeoutExpired:
                logger.debug(f"Timeout checking service: {name}")
            except FileNotFoundError:
                # systemctl not available
                logger.debug("systemctl not found - not a systemd system")
                break
            except Exception as e:
                logger.debug(f"Error checking service {name}: {e}")

        return result

    def _check_process(self) -> Dict[str, Any]:
        """Fallback process detection using pgrep."""
        result = {
            'running': False,
            'pid': None
        }

        # Get our own PID to exclude from results
        our_pid = os.getpid()

        for pattern in self.PROCESS_PATTERNS:
            try:
                proc = subprocess.run(
                    ['pgrep', '-f', pattern],
                    capture_output=True, text=True, timeout=5
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    pids = proc.stdout.strip().split('\n')
                    # Filter out our own process and any subprocesses
                    valid_pids = []
                    for pid_str in pids:
                        try:
                            pid = int(pid_str)
                            # Exclude our process and check if actually running meshchat
                            if pid != our_pid:
                                # Verify this is actually meshchat.py, not just matching pattern
                                cmdline_path = f'/proc/{pid}/cmdline'
                                if os.path.exists(cmdline_path):
                                    with open(cmdline_path, 'r') as f:
                                        cmdline = f.read()
                                        if 'meshchat.py' in cmdline or 'reticulum-meshchat' in cmdline:
                                            valid_pids.append(pid)
                        except (ValueError, IOError):
                            pass

                    if valid_pids:
                        result['running'] = True
                        result['pid'] = valid_pids[0]
                        break
            except Exception as e:
                logger.debug(f"Error checking process {pattern}: {e}")

        return result

    def _check_port(self) -> bool:
        """Check if MeshChat port is accessible."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((self.host, self.port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"Port check failed: {e}")
            return False

    def _get_version(self) -> Optional[str]:
        """Try to get MeshChat version from API."""
        try:
            from .client import MeshChatClient
            client = MeshChatClient(host=self.host, port=self.port, timeout=2)
            status = client.get_status()
            return status.version
        except Exception as e:
            logger.debug(f"Failed to get version: {e}")
            return None

    def _get_start_hint(self, service_name: Optional[str]) -> str:
        """Get hint for starting the service."""
        if service_name:
            return f"Start with: sudo systemctl start {service_name}"
        return "Start manually: cd reticulum-meshchat && python meshchat.py"

    def start(self, callback: Optional[Callable[[bool, str], None]] = None):
        """
        Start MeshChat service (requires sudo).

        Args:
            callback: Function(success: bool, message: str) called when done
        """
        def do_start():
            success = False
            message = "No service found"

            for name in self.SERVICE_NAMES:
                try:
                    # Check if service exists
                    check = subprocess.run(
                        ['systemctl', 'status', name],
                        capture_output=True, text=True, timeout=5
                    )
                    if check.returncode == 4:  # Unit not found
                        continue

                    # Start service
                    result = subprocess.run(
                        ['sudo', 'systemctl', 'start', name],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        success = True
                        message = f"Started {name}"
                        break
                    else:
                        message = result.stderr.strip() or "Start failed"

                except subprocess.TimeoutExpired:
                    message = "Command timed out"
                except Exception as e:
                    message = str(e)

            if callback:
                callback(success, message)

        threading.Thread(target=do_start, daemon=True).start()

    def stop(self, callback: Optional[Callable[[bool, str], None]] = None):
        """Stop MeshChat service (requires sudo)."""
        def do_stop():
            success = False
            message = "No service found"

            for name in self.SERVICE_NAMES:
                try:
                    result = subprocess.run(
                        ['sudo', 'systemctl', 'stop', name],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        success = True
                        message = f"Stopped {name}"
                        break
                except Exception as e:
                    message = str(e)

            if callback:
                callback(success, message)

        threading.Thread(target=do_stop, daemon=True).start()

    def get_diagnostic_info(self) -> Dict[str, Any]:
        """Get diagnostic information for troubleshooting."""
        status = self.check_status(blocking=True)

        info = {
            'service': {
                'state': status.state.value,
                'installed': status.installed,
                'running': status.running,
                'port_open': status.port_open,
                'service_name': status.service_name,
                'pid': status.pid,
                'version': status.version
            },
            'config': {
                'host': self.host,
                'port': self.port,
                'url': f"http://{self.host}:{self.port}"
            },
            'hints': {
                'install_url': self.INSTALL_URL,
                'fix': status.fix_hint
            }
        }

        # Add log location if we can find it
        log_paths = [
            get_real_user_home() / '.config' / 'meshchat' / 'logs',
            get_real_user_home() / '.meshchat' / 'logs',
            Path('/var/log/meshchat')
        ]
        for path in log_paths:
            if path.exists():
                info['logs'] = str(path)
                break

        return info
