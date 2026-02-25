"""
Gateway Diagnostic Tool for MeshForge.

AI-like diagnostic system to help users get RNS and Meshtastic gateway working.
Checks hardware, software, and configuration - provides actionable fix hints.
"""

import os
import socket
import subprocess
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Dict, Optional

# First-party imports — always available
from utils.paths import get_real_user_home
from utils.service_check import check_service, check_systemd_service, check_process_with_pid, ServiceState
from utils.config_drift import detect_rnsd_config_drift

# Optional external dependencies
from utils.safe_import import safe_import
_meshtastic_mod, _HAS_MESHTASTIC = safe_import('meshtastic')
MeshChatService, MeshChatServiceState, _HAS_MESHCHAT = safe_import(
    'plugins.meshchat', 'MeshChatService', 'ServiceState'
)
if not _HAS_MESHCHAT:
    MeshChatService, MeshChatServiceState, _HAS_MESHCHAT = safe_import(
        'src.plugins.meshchat', 'MeshChatService', 'ServiceState'
    )
MeshChatClient, _HAS_MESHCHAT_CLIENT = safe_import(
    'plugins.meshchat.client', 'MeshChatClient'
)
if not _HAS_MESHCHAT_CLIENT:
    MeshChatClient, _HAS_MESHCHAT_CLIENT = safe_import(
        'src.plugins.meshchat.client', 'MeshChatClient'
    )


class CheckStatus(Enum):
    """Status of a diagnostic check."""
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    """Result of a single diagnostic check."""
    name: str
    status: CheckStatus
    message: str
    fix_hint: Optional[str] = None
    details: Optional[str] = None

    def is_ok(self) -> bool:
        """Return True if check passed or is just a warning/skip."""
        return self.status in (CheckStatus.PASS, CheckStatus.WARN, CheckStatus.SKIP)


@dataclass
class GatewayDiagnostic:
    """
    Comprehensive diagnostic tool for RNS/Meshtastic gateway setup.

    Checks:
    - System requirements (Python, pip packages)
    - Serial ports (USB Meshtastic devices)
    - TCP connectivity (meshtasticd daemon)
    - BLE availability
    - RNS installation and configuration
    - Meshtastic library and interface
    """

    results: List[CheckResult] = field(default_factory=list)
    connection_type: Optional[str] = None
    verbose: bool = False

    def run_all(self) -> List[CheckResult]:
        """Run all diagnostic checks."""
        self.results = []

        # System checks
        self.results.append(self.check_python_version())
        self.results.append(self.check_pip_packages())

        # RNS checks
        self.results.append(self.check_rns_installed())
        self.results.append(self.check_rns_config())
        self.results.append(self.check_rnsd_running())

        # Meshtastic checks
        self.results.append(self.check_meshtastic_installed())
        self.results.append(self.check_meshtastic_interface())
        self.results.append(self.check_meshtastic_module_for_rnsd())
        self.results.append(self.check_meshtasticd())

        # Optional integrations
        self.results.append(self.check_meshchat())
        self.results.append(self.check_meshchat_rns_integration())

        # Connection checks
        conn_types = self.detect_connection_types()
        if conn_types['serial']:
            self.results.append(CheckResult(
                name="Serial Connection",
                status=CheckStatus.PASS,
                message=f"Found {len(conn_types['serial'])} device(s): {', '.join(conn_types['serial'])}"
            ))
        else:
            self.results.append(CheckResult(
                name="Serial Connection",
                status=CheckStatus.WARN,
                message="No Meshtastic USB devices detected",
                fix_hint="Connect a Meshtastic device via USB, or use TCP/BLE"
            ))

        if conn_types['tcp']:
            self.results.append(CheckResult(
                name="TCP Connection",
                status=CheckStatus.PASS,
                message="meshtasticd available on localhost:4403"
            ))

        if conn_types['ble']:
            self.results.append(CheckResult(
                name="BLE Support",
                status=CheckStatus.PASS,
                message="Bluetooth LE available"
            ))

        return self.results

    def get_summary(self) -> str:
        """Generate human-readable diagnostic summary."""
        if not self.results:
            self.run_all()

        lines = []
        lines.append("=" * 50)
        lines.append("  MESHFORGE GATEWAY DIAGNOSTIC")
        lines.append("=" * 50)
        lines.append("")

        # Count by status
        counts = {s: 0 for s in CheckStatus}
        for r in self.results:
            counts[r.status] += 1

        lines.append(f"Summary: {counts[CheckStatus.PASS]} PASS | "
                     f"{counts[CheckStatus.FAIL]} FAIL | "
                     f"{counts[CheckStatus.WARN]} WARN")
        lines.append("")

        # Group by status
        for status in [CheckStatus.FAIL, CheckStatus.WARN, CheckStatus.PASS, CheckStatus.SKIP]:
            status_results = [r for r in self.results if r.status == status]
            if not status_results:
                continue

            lines.append(f"--- {status.value} ---")
            for r in status_results:
                icon = {"PASS": "✓", "FAIL": "✗", "WARN": "!", "SKIP": "-"}[status.value]
                lines.append(f"  {icon} {r.name}: {r.message}")
                if r.fix_hint and status == CheckStatus.FAIL:
                    lines.append(f"    → Fix: {r.fix_hint}")
            lines.append("")

        # Recommended next steps
        if counts[CheckStatus.FAIL] > 0:
            lines.append("=" * 50)
            lines.append("  RECOMMENDED ACTIONS")
            lines.append("=" * 50)
            for r in self.results:
                if r.status == CheckStatus.FAIL and r.fix_hint:
                    lines.append(f"\n• {r.name}:")
                    lines.append(f"  {r.fix_hint}")

        return "\n".join(lines)

    # ========================================
    # System Checks
    # ========================================

    def check_python_version(self) -> CheckResult:
        """Check Python version is 3.8+."""
        import sys
        version = sys.version_info
        if version >= (3, 8):
            return CheckResult(
                name="Python Version",
                status=CheckStatus.PASS,
                message=f"Python {version.major}.{version.minor}.{version.micro}"
            )
        else:
            return CheckResult(
                name="Python Version",
                status=CheckStatus.FAIL,
                message=f"Python {version.major}.{version.minor} (3.8+ required)",
                fix_hint="Upgrade Python: sudo apt install python3.10"
            )

    def check_pip_packages(self) -> CheckResult:
        """Check required pip packages."""
        # Check using direct imports (same Python environment)
        import importlib
        required = {
            'meshtastic': 'meshtastic',
            'rns': 'RNS',
            'lxmf': 'LXMF'
        }
        missing = []
        installed = []

        for display_name, module_name in required.items():
            try:
                importlib.import_module(module_name)
                installed.append(display_name)
            except ImportError:
                missing.append(display_name)
            except (SystemExit, KeyboardInterrupt, GeneratorExit):
                # Re-raise critical exceptions
                raise
            except BaseException:
                # Catch pyo3 PanicException and other errors from
                # RNS's cryptography library when cffi backend is missing
                missing.append(f"{display_name} (import error)")

        if not missing:
            return CheckResult(
                name="Required Packages",
                status=CheckStatus.PASS,
                message=f"{', '.join(installed)} installed"
            )
        else:
            return CheckResult(
                name="Required Packages",
                status=CheckStatus.WARN if installed else CheckStatus.FAIL,
                message=f"Missing: {', '.join(missing)}" + (f" (have: {', '.join(installed)})" if installed else ""),
                fix_hint=f"pip3 install --user {' '.join(missing)}"
            )

    # ========================================
    # RNS Checks
    # ========================================

    def check_rns_installed(self) -> CheckResult:
        """Check if RNS is installed and importable."""
        # Try direct import (same Python environment)
        try:
            import RNS
            version = getattr(RNS, '__version__', 'unknown')
            return CheckResult(
                name="RNS Installation",
                status=CheckStatus.PASS,
                message=f"Reticulum {version} installed"
            )
        except ImportError:
            return CheckResult(
                name="RNS Installation",
                status=CheckStatus.FAIL,
                message="RNS not installed",
                fix_hint="pipx install rns"
            )
        except (SystemExit, KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException as e:
            # Catch pyo3 PanicException from RNS's cryptography library
            return CheckResult(
                name="RNS Installation",
                status=CheckStatus.WARN,
                message=f"RNS installed but error: {e}",
                fix_hint="Try: pipx install rns  (or pipx upgrade rns)"
            )

    def check_rns_config(self) -> CheckResult:
        """Check RNS configuration file and detect config drift."""
        from utils.paths import ReticulumPaths
        config_path = ReticulumPaths.get_config_file()

        if not config_path.exists():
            return CheckResult(
                name="RNS Config",
                status=CheckStatus.FAIL,
                message="Config file not found",
                fix_hint="Run 'rnsd' once to create default config, or use MeshForge to create one"
            )

        # Check config content
        try:
            content = config_path.read_text()

            issues = []
            if '[interfaces]' not in content.lower():
                issues.append("No [interfaces] section")

            if 'autointerface' not in content.lower():
                issues.append("AutoInterface not configured")

            # Check for Meshtastic interface
            has_meshtastic = 'meshtastic' in content.lower()

            # Check for config drift between gateway and rnsd
            drift = detect_rnsd_config_drift()
            if drift.drifted:
                issues.append(
                    f"Config drift: gateway uses {drift.gateway_config_dir} "
                    f"but rnsd uses {drift.rnsd_config_dir}"
                )

            if issues:
                return CheckResult(
                    name="RNS Config",
                    status=CheckStatus.WARN,
                    message=f"Config exists but: {'; '.join(issues)}",
                    fix_hint=f"Config at: {config_path.parent}"
                )

            msg = f"Config valid ({config_path.parent})"
            if has_meshtastic:
                msg += " (Meshtastic interface configured)"

            return CheckResult(
                name="RNS Config",
                status=CheckStatus.PASS,
                message=msg
            )
        except Exception as e:
            return CheckResult(
                name="RNS Config",
                status=CheckStatus.FAIL,
                message=f"Error reading config: {e}",
                fix_hint=f"Check file permissions on {config_path}"
            )

    def check_rnsd_running(self) -> CheckResult:
        """Check if rnsd daemon is running."""
        try:
            running, pid = check_process_with_pid('rnsd')
            if running:
                return CheckResult(
                    name="RNS Daemon (rnsd)",
                    status=CheckStatus.PASS,
                    message=f"Running (PID: {pid})"
                )
            else:
                return CheckResult(
                    name="RNS Daemon (rnsd)",
                    status=CheckStatus.WARN,
                    message="Not running",
                    fix_hint="Start with: rnsd (or enable in MeshForge RNS panel)"
                )
        except Exception as e:
            return CheckResult(
                name="RNS Daemon (rnsd)",
                status=CheckStatus.WARN,
                message=f"Check failed: {e}"
            )

    # ========================================
    # Meshtastic Checks
    # ========================================

    def check_meshtastic_installed(self) -> CheckResult:
        """Check if meshtastic library is installed."""
        # Check module-level safe import result
        if _HAS_MESHTASTIC:
            version = getattr(_meshtastic_mod, '__version__', 'unknown')
            return CheckResult(
                name="Meshtastic Library",
                status=CheckStatus.PASS,
                message=f"meshtastic {version} installed"
            )

        # Fallback: try subprocess with sys.executable
        try:
            import sys
            result = subprocess.run(
                [sys.executable, '-c', 'import meshtastic; print(meshtastic.__version__)'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                return CheckResult(
                    name="Meshtastic Library",
                    status=CheckStatus.PASS,
                    message=f"meshtastic {version} installed"
                )
        except Exception:
            pass

        return CheckResult(
            name="Meshtastic Library",
            status=CheckStatus.FAIL,
            message="meshtastic library not installed",
            fix_hint="pip3 install --user meshtastic"
        )

    def check_meshtastic_interface(self) -> CheckResult:
        """Check Meshtastic_Interface.py for RNS."""
        from utils.paths import ReticulumPaths
        interface_path = ReticulumPaths.get_interfaces_dir() / "Meshtastic_Interface.py"

        if interface_path.exists():
            # Check file size (should be > 10KB for real interface)
            size = interface_path.stat().st_size
            if size > 10000:
                return CheckResult(
                    name="Meshtastic RNS Interface",
                    status=CheckStatus.PASS,
                    message=f"Installed ({size // 1024}KB)"
                )
            else:
                return CheckResult(
                    name="Meshtastic RNS Interface",
                    status=CheckStatus.WARN,
                    message=f"File exists but seems incomplete ({size} bytes)",
                    fix_hint="Re-download from MeshForge RNS panel"
                )
        else:
            return CheckResult(
                name="Meshtastic RNS Interface",
                status=CheckStatus.FAIL,
                message="Not installed",
                fix_hint="Install from MeshForge RNS panel → 'Install Interface' button"
            )

    def check_meshtastic_module_for_rnsd(self) -> CheckResult:
        """
        Check if meshtastic module is available in the Python environment that rnsd uses.

        This is critical when Meshtastic_Interface.py is installed - rnsd will fail
        to start if the meshtastic module isn't importable from root's Python.

        See persistent_issues.md Issue #24 for details.
        """
        from utils.paths import ReticulumPaths
        interface_path = ReticulumPaths.get_interfaces_dir() / "Meshtastic_Interface.py"

        # Only check if the interface is installed - otherwise not relevant
        if not interface_path.exists():
            return CheckResult(
                name="Meshtastic Module (for rnsd)",
                status=CheckStatus.SKIP,
                message="Meshtastic_Interface not installed, check not needed"
            )

        # Check if meshtastic is importable as root (how rnsd runs)
        try:
            result = subprocess.run(
                ['sudo', 'python3', '-c', 'import meshtastic; print(meshtastic.__version__)'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                version = result.stdout.strip()
                return CheckResult(
                    name="Meshtastic Module (for rnsd)",
                    status=CheckStatus.PASS,
                    message=f"meshtastic {version} available to rnsd"
                )
            else:
                # Module not found - this is the Issue #24 problem
                return CheckResult(
                    name="Meshtastic Module (for rnsd)",
                    status=CheckStatus.FAIL,
                    message="meshtastic not importable by root's Python (rnsd will fail!)",
                    fix_hint="sudo pip3 install --break-system-packages --ignore-installed meshtastic",
                    details="The Meshtastic_Interface.py plugin requires meshtastic to be installed "
                            "in root's Python path. pipx or --user installs won't work. "
                            "Use --break-system-packages --ignore-installed on Debian 12+ / Pi OS Bookworm."
                )
        except subprocess.TimeoutExpired:
            return CheckResult(
                name="Meshtastic Module (for rnsd)",
                status=CheckStatus.WARN,
                message="Check timed out",
                fix_hint="Run: sudo python3 -c 'import meshtastic' to test manually"
            )
        except FileNotFoundError:
            # sudo not available (testing environment)
            return CheckResult(
                name="Meshtastic Module (for rnsd)",
                status=CheckStatus.SKIP,
                message="Cannot check (sudo not available)"
            )

    def check_meshtasticd(self) -> CheckResult:
        """Check if meshtasticd service is running."""
        try:
            running, _ = check_process_with_pid('meshtasticd')
            if running:
                # Also check TCP port
                if self.check_tcp_port('localhost', 4403):
                    return CheckResult(
                        name="meshtasticd Service",
                        status=CheckStatus.PASS,
                        message="Running and listening on port 4403"
                    )
                else:
                    return CheckResult(
                        name="meshtasticd Service",
                        status=CheckStatus.WARN,
                        message="Process running but port 4403 not open",
                        fix_hint="Check meshtasticd configuration"
                    )
            else:
                return CheckResult(
                    name="meshtasticd Service",
                    status=CheckStatus.SKIP,
                    message="Not running (optional - use USB/BLE instead)"
                )
        except Exception as e:
            return CheckResult(
                name="meshtasticd Service",
                status=CheckStatus.SKIP,
                message=f"Check skipped: {e}"
            )

    # ========================================
    # Optional Integrations
    # ========================================

    def check_meshchat(self) -> CheckResult:
        """Check if MeshChat service is available (optional)."""
        if not _HAS_MESHCHAT:
            # MeshChat plugin not available - check port directly
            if self.check_tcp_port('localhost', 8000):
                return CheckResult(
                    name="MeshChat (Optional)",
                    status=CheckStatus.PASS,
                    message="Detected on port 8000"
                )
            return CheckResult(
                name="MeshChat (Optional)",
                status=CheckStatus.SKIP,
                message="Not installed (optional)"
            )

        try:
            service = MeshChatService()
            status = service.check_status(blocking=True)

            if status.available:
                version_str = f" v{status.version}" if status.version else ""
                return CheckResult(
                    name="MeshChat (Optional)",
                    status=CheckStatus.PASS,
                    message=f"Running{version_str} on port {service.port}",
                    details=f"PID: {status.pid}" if status.pid else None
                )
            elif status.state == MeshChatServiceState.STOPPED:
                return CheckResult(
                    name="MeshChat (Optional)",
                    status=CheckStatus.SKIP,
                    message="Installed but not running",
                    fix_hint=status.fix_hint
                )
            elif status.state == MeshChatServiceState.STARTING:
                return CheckResult(
                    name="MeshChat (Optional)",
                    status=CheckStatus.WARN,
                    message="Starting (port not ready yet)"
                )
            else:
                return CheckResult(
                    name="MeshChat (Optional)",
                    status=CheckStatus.SKIP,
                    message="Not installed (optional LXMF messaging)",
                    fix_hint="Install from: https://github.com/liamcottle/reticulum-meshchat"
                )
        except Exception as e:
            return CheckResult(
                name="MeshChat (Optional)",
                status=CheckStatus.SKIP,
                message=f"Check skipped: {e}"
            )

    def check_meshchat_rns_integration(self) -> CheckResult:
        """Check if MeshChat is properly connected to RNS (optional)."""
        if not _HAS_MESHCHAT_CLIENT:
            return CheckResult(
                name="MeshChat-RNS Link",
                status=CheckStatus.SKIP,
                message="MeshChat client not available"
            )

        try:
            client = MeshChatClient(timeout=3)
            if not client.is_available():
                return CheckResult(
                    name="MeshChat-RNS Link",
                    status=CheckStatus.SKIP,
                    message="MeshChat not running"
                )

            mc_status = client.get_status()
            if mc_status.rns_connected:
                details = []
                if mc_status.peer_count:
                    details.append(f"{mc_status.peer_count} peers")
                if mc_status.message_count:
                    details.append(f"{mc_status.message_count} messages")
                detail_str = f" ({', '.join(details)})" if details else ""
                return CheckResult(
                    name="MeshChat-RNS Link",
                    status=CheckStatus.PASS,
                    message=f"Connected to RNS{detail_str}"
                )
            else:
                return CheckResult(
                    name="MeshChat-RNS Link",
                    status=CheckStatus.FAIL,
                    message="MeshChat running but RNS not connected",
                    fix_hint="Check that rnsd is running with share_instance = Yes"
                )
        except Exception as e:
            return CheckResult(
                name="MeshChat-RNS Link",
                status=CheckStatus.SKIP,
                message=f"Check skipped: {e}"
            )

    # ========================================
    # Connection Detection
    # ========================================

    def detect_connection_types(self) -> Dict[str, any]:
        """Detect available connection types."""
        return {
            'serial': self._find_meshtastic_serial(),
            'tcp': self.check_tcp_port('localhost', 4403),
            'ble': self._check_ble_available(),
        }

    def list_serial_ports(self) -> List[Dict[str, str]]:
        """List available serial ports."""
        ports = []

        # Check common Meshtastic device paths
        patterns = [
            '/dev/ttyUSB*',
            '/dev/ttyACM*',
            '/dev/tty.usbserial*',
            '/dev/tty.usbmodem*',
        ]

        import glob
        for pattern in patterns:
            for device in glob.glob(pattern):
                description = self._get_serial_description(device)
                ports.append({
                    'device': device,
                    'description': description
                })

        return ports

    def _find_meshtastic_serial(self) -> List[str]:
        """Find Meshtastic devices on serial ports."""
        devices = []
        for port in self.list_serial_ports():
            desc = port['description'].lower()
            # Look for common Meshtastic device signatures
            if any(sig in desc for sig in ['cp210', 'ch340', 'meshtastic', 'esp32', 'silabs', 'ft232']):
                devices.append(port['device'])
            else:
                # Include any USB serial device
                devices.append(port['device'])
        return devices

    def _get_serial_description(self, device: str) -> str:
        """Get description for a serial device."""
        try:
            # Try to read from sysfs
            device_name = os.path.basename(device)
            sysfs_path = f"/sys/class/tty/{device_name}/device/../../product"
            if os.path.exists(sysfs_path):
                with open(sysfs_path, 'r') as f:
                    return f.read().strip()

            # Try lsusb
            result = subprocess.run(
                ['lsusb'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if any(chip in line.lower() for chip in ['cp210', 'ch340', 'silabs', 'esp32']):
                        return line.split(':', 1)[-1].strip() if ':' in line else line

        except Exception:
            pass

        return "USB Serial Device"

    def check_tcp_port(self, host: str, port: int) -> bool:
        """Check if a TCP port is open."""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            return result == 0
        except Exception:
            return False
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def check_rns_port_available(self, port: int = 29716) -> CheckResult:
        """Check if RNS AutoInterface UDP port is available for binding."""
        result = check_rns_port_available(port)
        if result['available']:
            return CheckResult(
                name="RNS Port Availability",
                status=CheckStatus.PASS,
                message=f"UDP port {port} is available"
            )
        else:
            fix_hint = result.get('fix_hint', "Kill existing RNS process or wait for it to exit")
            return CheckResult(
                name="RNS Port Availability",
                status=CheckStatus.FAIL,
                message=result['reason'],
                fix_hint=fix_hint
            )

    def _check_ble_available(self) -> bool:
        """Check if Bluetooth LE is available."""
        try:
            is_running, is_enabled = check_systemd_service('bluetooth')
            if is_running:
                return True
        except Exception:
            pass

        # Try hciconfig as fallback
        try:
            result = subprocess.run(
                ['hciconfig'],
                capture_output=True, text=True, timeout=5
            )
            return 'UP RUNNING' in result.stdout
        except Exception:
            return False

    # ========================================
    # Interactive Wizard
    # ========================================

    def run_wizard(self) -> str:
        """Run interactive diagnostic wizard with recommendations."""
        self.run_all()

        lines = []
        lines.append("\n" + "=" * 60)
        lines.append("  🔧 MESHFORGE GATEWAY SETUP WIZARD")
        lines.append("=" * 60)

        # Analyze results
        failures = [r for r in self.results if r.status == CheckStatus.FAIL]
        warnings = [r for r in self.results if r.status == CheckStatus.WARN]
        conn_types = self.detect_connection_types()

        if not failures:
            lines.append("\n✓ All critical checks passed!")

            # Check if Meshtastic_Interface.py plugin is installed
            plugin_path = ReticulumPaths.get_interfaces_dir() / "Meshtastic_Interface.py"
            if not plugin_path.exists():
                lines.append(f"\n⚠  Meshtastic_Interface.py plugin NOT installed")
                lines.append(f"   Required for RNS over Meshtastic bridging")
                lines.append(f"   Install from: RNS menu > Install Meshtastic Interface")
                lines.append(f"   Target: {ReticulumPaths.get_interfaces_dir()}/")

            # Recommend connection type
            config_path = ReticulumPaths.get_config_file()
            if conn_types['serial']:
                lines.append(f"\n📻 Recommended: Use Serial connection")
                lines.append(f"   Device: {conn_types['serial'][0]}")
                lines.append(f"   Add to {config_path}:")
                lines.append(f"   [[Meshtastic Interface]]")
                lines.append(f"     type = Meshtastic_Interface")
                lines.append(f"     port = {conn_types['serial'][0]}")
            elif conn_types['tcp']:
                lines.append(f"\n📡 Recommended: Use TCP connection")
                lines.append(f"   Add to {config_path}:")
                lines.append(f"   [[Meshtastic Interface]]")
                lines.append(f"     type = Meshtastic_Interface")
                lines.append(f"     tcp_port = 127.0.0.1:4403")
            elif conn_types['ble']:
                lines.append(f"\n📶 Recommended: Use Bluetooth LE")
                lines.append(f"   First pair your device, then add to config:")
                lines.append(f"   [[Meshtastic Interface]]")
                lines.append(f"     type = Meshtastic_Interface")
                lines.append(f"     ble_port = YourDevice_1234")
        else:
            lines.append(f"\n⚠️  {len(failures)} issue(s) need to be fixed:")
            lines.append("")

            for i, fail in enumerate(failures, 1):
                lines.append(f"{i}. {fail.name}")
                lines.append(f"   Problem: {fail.message}")
                if fail.fix_hint:
                    lines.append(f"   Fix: {fail.fix_hint}")
                lines.append("")

        if warnings:
            lines.append("\n💡 Notes:")
            for warn in warnings:
                lines.append(f"   • {warn.name}: {warn.message}")

        lines.append("\n" + "=" * 60)

        return "\n".join(lines)


# ========================================
# Standalone Utility Functions
# ========================================

def check_rns_port_available(port: int = 29716) -> Dict[str, any]:
    """
    Check if the RNS AutoInterface UDP port is available for binding.

    The RNS AutoInterface uses UDP multicast on port 29716 by default.
    If another RNS instance (or rnsd) is running, this port will be in use.

    Args:
        port: UDP port to check (default 29716 for RNS AutoInterface)

    Returns:
        dict with keys:
            - available: bool - True if port is available
            - reason: str - Description of the result
            - fix_hint: str - Suggested fix if port is unavailable
            - pids: List[int] - PIDs of processes using the port (if any)
    """
    result = {
        'available': True,
        'reason': f"UDP port {port} is available",
        'fix_hint': None,
        'pids': []
    }

    # First try to bind to the port
    sock = None
    try:
        # Try IPv6 first (covers IPv4 on most systems)
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('::', port))
    except OSError as e:
        if e.errno == 98:  # Address already in use
            result['available'] = False
            result['reason'] = f"UDP port {port} is already in use"

            # Find what's using the port
            pids = find_rns_processes()
            result['pids'] = pids

            if pids:
                result['fix_hint'] = (
                    f"Kill existing RNS process: sudo kill {pids[0]}\n"
                    f"Or stop rnsd: pkill -f rnsd\n"
                    f"Or use the shared RNS instance by running rnsd separately"
                )
            else:
                result['fix_hint'] = (
                    f"Another process is using port {port}.\n"
                    f"Find it with: sudo lsof -i UDP:{port}\n"
                    f"Or wait a few seconds and try again"
                )
        else:
            # Try IPv4 fallback
            # First close any existing socket
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
                sock = None

            sock4 = None
            try:
                sock4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock4.bind(('0.0.0.0', port))
            except OSError as e2:
                if e2.errno == 98:
                    result['available'] = False
                    result['reason'] = f"UDP port {port} is already in use"
                    pids = find_rns_processes()
                    result['pids'] = pids
                    result['fix_hint'] = (
                        f"Kill existing RNS process or stop rnsd: pkill -f rnsd"
                    )
            finally:
                if sock4:
                    try:
                        sock4.close()
                    except Exception:
                        pass
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    return result


def find_rns_processes() -> List[int]:
    """
    Find running RNS-related processes.

    Returns:
        List of PIDs for rnsd or other RNS processes
    """
    pids = []

    # Check for rnsd
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'rnsd'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            for pid_str in result.stdout.strip().split('\n'):
                try:
                    pids.append(int(pid_str))
                except ValueError:
                    pass
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Also check for python processes running RNS
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'RNS.Reticulum'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            for pid_str in result.stdout.strip().split('\n'):
                try:
                    pid = int(pid_str)
                    if pid not in pids:
                        pids.append(pid)
                except ValueError:
                    pass
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return pids


def diagnose_rnsd_connection(rns_pids: List[int], error: Exception = None) -> None:
    """Log diagnostic info when rnsd connection fails.

    Follows 'Eight Times Blind' principle: check port, check known causes,
    show the log. POLICY: Diagnose only — never restarts services or
    modifies configs.

    Args:
        rns_pids: PIDs returned by find_rns_processes()
        error: The exception from the failed RNS.Reticulum() call
    """
    import logging
    _log = logging.getLogger(__name__)

    # 1. Check if shared instance is actually available
    try:
        from utils.service_check import check_rns_shared_instance
        port_listening = check_rns_shared_instance()
    except ImportError:
        port_listening = None  # Can't check

    if rns_pids and port_listening is False:
        _log.warning(
            "rnsd PID %d exists but shared instance not available "
            "(zombie or hung during init)", rns_pids[0]
        )
    elif rns_pids and port_listening is True:
        # Port is listening but connection still failed — likely auth or config issue
        err_str = str(error).lower() if error else ""
        if "authentication" in err_str or "digest" in err_str:
            _log.warning("Cause: RPC auth mismatch (stale shared_instance tokens)")
            _log.info("Fix: sudo systemctl stop rnsd && "
                      "sudo rm -f /etc/reticulum/storage/shared_instance_* && "
                      "sudo systemctl start rnsd")
        else:
            _log.warning("rnsd port 37428 listening but connection failed — "
                         "possible config mismatch")

    # 2. Show recent rnsd journal (the actual diagnostic, per "Eight Times Blind")
    try:
        r = subprocess.run(
            ['journalctl', '-u', 'rnsd', '-n', '15', '--no-pager'],
            capture_output=True, text=True, timeout=10
        )
        if r.stdout and r.stdout.strip():
            _log.warning("Recent rnsd log:")
            for line in r.stdout.strip().splitlines():
                _log.warning("  %s", line.strip())
        else:
            _log.info("(no rnsd journal output)")
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        _log.debug("Could not read rnsd journal")

    # 3. Actionable hints
    _log.info("Restart rnsd: sudo systemctl restart rnsd")
    _log.info("Full logs: journalctl -u rnsd -n 50")


def kill_rns_processes(force: bool = False) -> Dict[str, any]:
    """
    Kill running RNS processes to free up the port.

    Args:
        force: If True, use SIGKILL instead of SIGTERM

    Returns:
        dict with keys:
            - success: bool
            - killed: List[int] - PIDs that were killed
            - message: str
    """
    pids = find_rns_processes()

    if not pids:
        return {
            'success': True,
            'killed': [],
            'message': "No RNS processes found"
        }

    killed = []
    signal_name = '-9' if force else '-15'

    for pid in pids:
        try:
            subprocess.run(
                ['kill', signal_name, str(pid)],
                capture_output=True, timeout=5
            )
            killed.append(pid)
        except subprocess.SubprocessError:
            pass

    # Also try pkill for any we might have missed
    try:
        subprocess.run(
            ['pkill', '-f', 'rnsd'],
            capture_output=True, timeout=5
        )
    except subprocess.SubprocessError:
        pass

    return {
        'success': len(killed) > 0 or len(pids) == 0,
        'killed': killed,
        'message': f"Killed {len(killed)} RNS process(es)" if killed else "No processes killed"
    }


def handle_address_in_use_error(error: Exception, logger=None) -> Dict[str, any]:
    """
    Handle the "Address already in use" error from RNS initialization.

    This is the main helper function to call when catching OSError during
    RNS.Reticulum() initialization.

    Args:
        error: The exception that was raised
        logger: Optional logger instance for output

    Returns:
        dict with keys:
            - is_address_in_use: bool - True if this is an address-in-use error
            - can_use_shared: bool - True if a shared RNS instance is available
            - rns_pids: List[int] - PIDs of existing RNS processes
            - message: str - User-friendly error message
            - fix_options: List[str] - Possible fixes
    """
    error_str = str(error).lower()

    result = {
        'is_address_in_use': False,
        'can_use_shared': False,
        'rns_pids': [],
        'message': str(error),
        'fix_options': []
    }

    # Check if this is an address-in-use error
    if 'address already in use' in error_str or (hasattr(error, 'errno') and error.errno == 98):
        result['is_address_in_use'] = True
        result['rns_pids'] = find_rns_processes()

        if result['rns_pids']:
            result['can_use_shared'] = True
            result['message'] = (
                f"RNS port is in use by existing process (PID: {result['rns_pids'][0]}). "
                f"This is likely rnsd or another MeshForge instance."
            )
            result['fix_options'] = [
                "Use the shared RNS instance (recommended if rnsd is running)",
                f"Stop existing RNS: pkill -f rnsd",
                f"Kill specific process: sudo kill {result['rns_pids'][0]}",
                "Wait a few seconds and try again"
            ]
        else:
            result['message'] = (
                "RNS port is in use by an unknown process. "
                "A previous instance may not have shut down cleanly."
            )
            result['fix_options'] = [
                "Find the process: sudo lsof -i UDP:29716",
                "Wait 30 seconds for socket timeout and try again",
                "Restart your system if the issue persists"
            ]

        if logger:
            logger.warning(result['message'])
            logger.info("Fix options:")
            for opt in result['fix_options']:
                logger.info(f"  - {opt}")

    return result


def main():
    """CLI entry point for gateway diagnostics."""
    import argparse

    parser = argparse.ArgumentParser(
        description="MeshForge Gateway Diagnostic Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  meshforge-diag              Run full diagnostic
  meshforge-diag --wizard     Interactive setup wizard
  meshforge-diag --summary    Quick summary only
        """
    )
    parser.add_argument('--wizard', '-w', action='store_true',
                        help='Run interactive setup wizard')
    parser.add_argument('--summary', '-s', action='store_true',
                        help='Show summary only')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output')

    args = parser.parse_args()

    diag = GatewayDiagnostic(verbose=args.verbose)

    if args.wizard:
        print(diag.run_wizard())
    else:
        diag.run_all()
        print(diag.get_summary())


if __name__ == "__main__":
    main()
