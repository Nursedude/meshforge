#!/usr/bin/env python3
"""MeshForge Diagnostic Tool

Checks system configuration and identifies common issues.

Usage:
    python3 src/cli/diagnose.py
    sudo python3 src/cli/diagnose.py  # For full diagnostics
"""

import collections
import os
import re
import sys
import socket
import subprocess
import shutil
from pathlib import Path

# Ensure src directory is in path for imports
_src_dir = Path(__file__).parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from utils.paths import get_real_user_home, ReticulumPaths
from utils.safe_import import safe_import

# Module-level safe imports
_check_service, _check_port, _ServiceState, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'check_port', 'ServiceState'
)
SERVICE_CHECK_AVAILABLE = _HAS_SERVICE_CHECK

from utils.cli import find_meshtastic_cli

_GatewayDiagnostic, _HAS_GATEWAY_DIAG = safe_import(
    'utils.gateway_diagnostic', 'GatewayDiagnostic'
)

_get_udp_port_owner, _HAS_PORT_OWNER = safe_import(
    'utils.service_check', 'get_udp_port_owner'
)


def print_header(title: str):
    """Print a section header."""
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_status(name: str, status: bool, detail: str = ""):
    """Print a status line."""
    icon = "\u2713" if status else "\u2717"  # checkmark or X
    color_start = "\033[92m" if status else "\033[91m"  # green or red
    color_end = "\033[0m"
    detail_str = f" - {detail}" if detail else ""
    print(f"  {color_start}{icon}{color_end} {name}{detail_str}")


def is_root() -> bool:
    """Check if running as root."""
    from utils.system import check_root
    return check_root()


def check_services():
    """Check service status."""
    print_header("SERVICES")

    services = ['meshtasticd', 'rnsd']
    for svc in services:
        if SERVICE_CHECK_AVAILABLE:
            # Use centralized service checker
            status = _check_service(svc)
            is_active = status.available
            detail = status.state.value
            if not is_active and status.fix_hint:
                detail = f"{detail} - {status.fix_hint}"
            print_status(svc, is_active, detail)
        else:
            # Fallback to direct systemctl call
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', svc],
                    capture_output=True, text=True, timeout=5
                )
                is_active = result.stdout.strip() == 'active'
                print_status(svc, is_active, result.stdout.strip())
            except FileNotFoundError:
                print_status(svc, False, "systemctl not found")
            except subprocess.TimeoutExpired:
                print_status(svc, False, "timeout")
            except Exception as e:
                print_status(svc, False, str(e))


def check_rns_port():
    """Check if RNS port is available."""
    print_header("RNS NETWORK")

    # Check if rnsd is running (via service_check single source of truth)
    rnsd_running = False
    if SERVICE_CHECK_AVAILABLE:
        rnsd_status = _check_service('rnsd')
        rnsd_running = rnsd_status.available

    # Check port 29716 (RNS AutoInterface)
    port_available = True
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.bind(('::', 29716))
        sock.close()
    except OSError as e:
        port_available = False
        if rnsd_running:
            # Port in use BY rnsd — this is correct and expected
            print_status("RNS port 29716", True,
                         "in use by rnsd (expected)")
        else:
            # Port in use but rnsd not running — real conflict
            print_status("RNS port 29716", False, f"in use ({e})")

        # Show process info either way for diagnostics
        try:
            result = subprocess.run(
                ['lsof', '-i', ':29716'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout:
                print(f"    Process using port:")
                for line in result.stdout.strip().split('\n')[1:2]:
                    print(f"      {line}")
        except Exception:
            pass

    if port_available:
        print_status("RNS port 29716", True, "available")

    # Check shared instance (RNS uses abstract domain sockets on Linux)
    try:
        from utils.service_check import check_rns_shared_instance
        port_bound = check_rns_shared_instance()
    except ImportError:
        # Fallback: inline UDP bind test
        port_bound = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            sock.bind(('127.0.0.1', 37428))
            sock.close()
            # Bind succeeded = port NOT in use
        except OSError as e:
            if e.errno in (98, 48, 10048):  # EADDRINUSE
                port_bound = True
        finally:
            try:
                sock.close()
            except Exception:
                pass
    try:
        if port_bound:
            print_status("RNS shared instance", True, "listening on UDP 37428")
            # Show which process owns the port
            if _HAS_PORT_OWNER:
                owner = _get_udp_port_owner(37428)
                if owner:
                    proc_name, pid = owner
                    print(f"    Owner: {proc_name} (PID {pid})")
        else:
            # Check if share_instance is disabled in config
            hint = "not running"
            try:
                from commands.rns import read_config, _parse_share_instance
                cfg = read_config()
                if cfg.success:
                    content = cfg.data.get('content', '')
                    if not _parse_share_instance(content):
                        hint = ("not enabled — add 'share_instance = Yes' "
                                "to [reticulum] config, then restart rnsd")
            except ImportError:
                pass
            print_status("RNS shared instance", False, hint)

            # Check if another process is holding the port
            if _HAS_PORT_OWNER:
                owner = _get_udp_port_owner(37428)
                if owner:
                    proc_name, pid = owner
                    print(f"    Port held by: {proc_name} (PID {pid})")
                    if 'nomadnet' in proc_name.lower():
                        print("    NOTE: NomadNet is holding port 37428.")
                        print("    rnsd cannot bind while NomadNet owns "
                              "this port.")
                        print("    Fix: Stop NomadNet, start rnsd first, "
                              "then NomadNet.")
    except Exception as e:
        print_status("RNS shared instance", False, str(e))


def check_serial_ports():
    """Check available serial ports."""
    print_header("SERIAL PORTS")

    ports = list(Path('/dev').glob('ttyACM*')) + list(Path('/dev').glob('ttyUSB*'))

    if ports:
        for port in ports:
            # Check if readable
            try:
                readable = os.access(port, os.R_OK | os.W_OK)
                print_status(str(port), readable, "accessible" if readable else "permission denied")
            except Exception as e:
                print_status(str(port), False, str(e))
    else:
        print("  No serial ports found (ttyACM*, ttyUSB*)")


def check_meshtastic_connection():
    """Check Meshtastic TCP connection."""
    print_header("MESHTASTIC CONNECTION")

    # Check port 4403 (meshtasticd API)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', 4403))
        sock.close()
        if result == 0:
            print_status("meshtasticd API", True, "listening on 4403")
        else:
            print_status("meshtasticd API", False, "not reachable on 4403")
    except Exception as e:
        print_status("meshtasticd API", False, str(e))

    # Check port 9443 (web interface - HTTPS)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', 9443))
        sock.close()
        if result == 0:
            print_status("meshtasticd web UI", True, "listening on 9443")
        else:
            print_status("meshtasticd web UI", False, "not reachable on 9443")
    except Exception as e:
        print_status("meshtasticd web UI", False, str(e))


def check_cli():
    """Check meshtastic CLI availability."""
    print_header("MESHTASTIC CLI")

    cli_path = find_meshtastic_cli()

    if cli_path:
        print_status("meshtastic CLI", True, cli_path)

        # Get version
        try:
            result = subprocess.run(
                [cli_path, '--version'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                print(f"    Version: {version}")
        except Exception:
            pass
    else:
        print_status("meshtastic CLI", False, "not found")
        print("    Install with: pipx install meshtastic")


def check_rns_config():
    """Check RNS configuration."""
    print_header("RNS CONFIGURATION")

    # Check for config file - use real user home for sudo compatibility
    config_locations = [
        ReticulumPaths.get_config_file(),
    ]

    config_found = False
    for config_path in config_locations:
        if config_path.exists():
            print_status("RNS config", True, str(config_path))
            config_found = True

            # Check for interfaces
            try:
                content = config_path.read_text()
                if 'Meshtastic' in content or 'RNode' in content:
                    print("    Contains Meshtastic/RNode interface configuration")
                if 'TCPClientInterface' in content:
                    print("    Contains TCP client interface")
                if 'AutoInterface' in content:
                    print("    Contains AutoInterface (local discovery)")
            except Exception:
                pass
            break

    if not config_found:
        print_status("RNS config", False, "not found")
        print("    Run 'rnsd' once to create default config")


def check_rns_interfaces():
    """Check RNS interface status via rnstatus.

    Parses rnstatus output to show per-interface TX/RX counters.
    Detects RX-only interfaces (link establishment failing) and
    zero-traffic interfaces (not yet active).
    """
    print_header("RNS INTERFACES")

    rnstatus_path = shutil.which('rnstatus')
    if not rnstatus_path:
        # Check user local bin
        user_home = get_real_user_home()
        candidate = user_home / '.local' / 'bin' / 'rnstatus'
        if candidate.exists():
            rnstatus_path = str(candidate)

    if not rnstatus_path:
        print_status("rnstatus", False,
                     "not found (install RNS: pipx install rns)")
        return

    try:
        result = subprocess.run(
            [rnstatus_path],
            capture_output=True, text=True, timeout=15
        )
        combined = (result.stdout or '') + (result.stderr or '')
        if ('no shared' in combined.lower()
                or 'could not' in combined.lower()):
            print_status("RNS shared instance", False,
                         "cannot connect to rnsd")
            # Check if port 37428 appears bound (contradictory state)
            try:
                from utils.service_check import (
                    check_rns_shared_instance, get_udp_port_owner
                )
                if check_rns_shared_instance():
                    print("    Note: Port 37428 appears bound but "
                          "rnstatus cannot connect.")
                    print("    This may indicate rnsd is initializing "
                          "or in a degraded state.")
                    print("    Try: sudo systemctl restart rnsd")
                    owner = get_udp_port_owner(37428)
                    if owner:
                        proc_name, pid = owner
                        print(f"    Port owner: {proc_name} "
                              f"(PID {pid})")
            except ImportError:
                pass
            return

        # Parse interface lines from rnstatus output
        # Format:  InterfaceName[DisplayName]
        #   Traffic   : ↑NNN B  NNN bps
        #               ↓NNN B  NNN bps
        current_iface = None
        tx_cache = {}
        rx_only_ifaces = []
        zero_traffic_ifaces = []
        any_iface_found = False

        for line in combined.splitlines():
            # Interface header line
            iface_match = re.match(
                r'\s*(\w+)\[(.+?)\]', line
            )
            if iface_match:
                current_iface = (
                    f"{iface_match.group(1)}[{iface_match.group(2)}]"
                )
                any_iface_found = True
                continue

            # TX line (↑ = upload/transmit)
            tx_match = re.search(r'↑\s*([\d,.]+)\s*(\w+)', line)
            if tx_match and current_iface:
                tx_val = tx_match.group(1).replace(',', '')
                tx_unit = tx_match.group(2)
                tx_cache[current_iface] = (
                    float(tx_val), tx_unit
                )

            # RX line (↓ = download/receive)
            rx_match = re.search(r'↓\s*([\d,.]+)\s*(\w+)', line)
            if rx_match and current_iface:
                rx_val = rx_match.group(1).replace(',', '')
                rx_unit = rx_match.group(2)

                tx_info = tx_cache.get(
                    current_iface, (0, 'B')
                )
                tx_bytes = tx_info[0]
                rx_bytes = float(rx_val)

                if rx_bytes > 0 and tx_bytes == 0:
                    print_status(
                        current_iface, False,
                        f"RX-only (↑0 ↓{rx_val} {rx_unit})"
                    )
                    rx_only_ifaces.append(current_iface)
                elif rx_bytes == 0 and tx_bytes == 0:
                    print_status(
                        current_iface, False,
                        "no traffic (↑0 ↓0)"
                    )
                    zero_traffic_ifaces.append(current_iface)
                else:
                    print_status(
                        current_iface, True,
                        f"↑{tx_info[0]:.0f} {tx_info[1]}  "
                        f"↓{rx_val} {rx_unit}"
                    )
                current_iface = None

        if rx_only_ifaces:
            print()
            print("  WARNING: RX-only interfaces detected.")
            print("  Packets received but link establishment "
                  "(SYN/ACK) failing.")
            print("  Common causes:")
            print("    - Shared instance not fully initialized")
            print("    - NomadNet/rnsd port conflict on 37428")
            print("    - Blocking interface preventing startup")

        if zero_traffic_ifaces:
            mesh_zero = [i for i in zero_traffic_ifaces
                         if 'Meshtastic' in i]
            if mesh_zero:
                print()
                print("  NOTE: Meshtastic interface(s) show zero "
                      "traffic.")
                print("  If rnsd just restarted, allow 60-90s for "
                      "link establishment.")

        if not any_iface_found:
            print("  No interfaces found in rnstatus output.")

    except FileNotFoundError:
        print_status("rnstatus", False, "not found")
    except subprocess.TimeoutExpired:
        print_status("rnstatus", False,
                     "timed out (rnsd may be unresponsive)")


def check_nomadnet():
    """Check NomadNet installation and recent logs."""
    print_header("NOMADNET")

    # Check if installed
    nomadnet_path = shutil.which('nomadnet')
    if not nomadnet_path:
        # Check user local bin
        sudo_user = os.environ.get('SUDO_USER', os.environ.get('USER', ''))
        if sudo_user:
            user_path = Path(f'/home/{sudo_user}/.local/bin/nomadnet')
            if user_path.exists():
                nomadnet_path = str(user_path)

    if nomadnet_path:
        print_status("NomadNet", True, nomadnet_path)

        # Check if running
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                pids = result.stdout.strip().split('\n')
                print(f"    Running (PIDs: {', '.join(pids)})")
        except Exception:
            pass

        # Read NomadNet logfile for recent errors
        user_home = get_real_user_home()
        logfile = user_home / '.nomadnetwork' / 'logfile'
        if logfile.exists():
            try:
                with open(logfile, 'r') as f:
                    last_lines = list(
                        collections.deque(f, maxlen=10)
                    )
                error_found = False
                for line in last_lines:
                    if ('AuthenticationError' in line
                            or 'digest' in line.lower()):
                        print_status(
                            "NomadNet log", False,
                            "RPC auth failure (identity mismatch)"
                        )
                        print("    Fix: Ensure rnsd and NomadNet use "
                              "same RNS config")
                        error_found = True
                        break
                    elif 'PermissionError' in line:
                        print_status(
                            "NomadNet log", False,
                            "permission denied in recent logs"
                        )
                        print("    Check: ls -la ~/.nomadnetwork/")
                        error_found = True
                        break
                    elif ('ModuleNotFoundError' in line
                            or 'ImportError' in line):
                        print_status(
                            "NomadNet log", False,
                            "missing Python dependency"
                        )
                        print("    Fix: pipx reinstall nomadnet")
                        error_found = True
                        break
                if not error_found:
                    print_status("NomadNet log", True,
                                 "no recent errors")
            except PermissionError:
                print_status("NomadNet log", False,
                             f"cannot read {logfile}")
        else:
            print(f"    Log: {logfile} (not found yet)")
    else:
        print_status("NomadNet", False, "not installed")
        print("    Install with: pipx install nomadnet")


def check_processes():
    """Check for conflicting processes."""
    print_header("PROCESS CHECK")

    processes_to_check = [
        ('meshtasticd', 'Meshtastic daemon'),
        ('rnsd', 'RNS daemon'),
        ('nomadnet', 'NomadNet'),
        ('meshforge', 'MeshForge'),
    ]

    for proc_name, description in processes_to_check:
        try:
            result = subprocess.run(
                ['pgrep', '-f', proc_name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                pids = [p for p in result.stdout.strip().split('\n') if p]
                # Filter out grep itself
                real_pids = []
                for pid in pids:
                    try:
                        cmdline = Path(f'/proc/{pid}/cmdline').read_text()
                        if 'grep' not in cmdline and 'pgrep' not in cmdline:
                            real_pids.append(pid)
                    except Exception:
                        pass

                if real_pids:
                    print_status(description, True, f"running (PIDs: {', '.join(real_pids)})")
                else:
                    print_status(description, False, "not running")
            else:
                print_status(description, False, "not running")
        except Exception as e:
            print_status(description, False, str(e))


def check_system_resources():
    """Check system resources."""
    print_header("SYSTEM RESOURCES")

    # CPU info
    try:
        with open('/proc/cpuinfo') as f:
            cpuinfo = f.read()
            model = None
            for line in cpuinfo.split('\n'):
                if 'model name' in line or 'Model' in line:
                    model = line.split(':')[1].strip()
                    break
            if model:
                print_status("CPU", True, model[:50])
    except Exception:
        print_status("CPU", False, "unable to read")

    # Memory
    try:
        with open('/proc/meminfo') as f:
            meminfo = f.read()
            total = 0
            available = 0
            for line in meminfo.split('\n'):
                if 'MemTotal' in line:
                    total = int(line.split()[1]) // 1024  # MB
                elif 'MemAvailable' in line:
                    available = int(line.split()[1]) // 1024  # MB
            used = total - available
            pct = (used / total * 100) if total > 0 else 0
            ok = pct < 90
            print_status("Memory", ok, f"{used}MB / {total}MB ({pct:.0f}% used)")
    except Exception:
        print_status("Memory", False, "unable to read")

    # Disk space
    try:
        result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                used_pct = int(parts[4].replace('%', ''))
                ok = used_pct < 90
                print_status("Disk space /", ok, f"{parts[2]} used of {parts[1]} ({parts[4]})")
    except Exception:
        print_status("Disk space", False, "unable to read")

    # Temperature (Raspberry Pi)
    try:
        temp_file = Path('/sys/class/thermal/thermal_zone0/temp')
        if temp_file.exists():
            temp_c = int(temp_file.read_text().strip()) / 1000
            ok = temp_c < 80
            print_status("CPU Temp", ok, f"{temp_c:.1f}°C")
    except Exception:
        pass  # Not all systems have this


def check_network_interfaces():
    """Check network interfaces."""
    print_header("NETWORK INTERFACES")

    try:
        result = subprocess.run(['ip', 'addr'], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split('\n')

        current_iface = None
        for line in lines:
            if not line.startswith(' '):
                # Interface line
                parts = line.split(':')
                if len(parts) >= 2:
                    current_iface = parts[1].strip()
            elif 'inet ' in line and current_iface:
                # IPv4 address line
                parts = line.strip().split()
                ip = parts[1] if len(parts) >= 2 else 'unknown'
                if current_iface not in ('lo',):
                    print_status(current_iface, True, ip)
    except Exception as e:
        print_status("Network", False, str(e))

    # Check internet connectivity
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex(('8.8.8.8', 53))
        sock.close()
        print_status("Internet", result == 0, "connected" if result == 0 else "no connectivity")
    except Exception:  # Error reported to user
        print_status("Internet", False, "unable to check")


def check_spi_gpio():
    """Check SPI and GPIO interfaces (for LoRa hardware)."""
    print_header("HARDWARE INTERFACES")

    # SPI devices
    spi_path = Path('/dev')
    spi_devices = list(spi_path.glob('spidev*'))
    if spi_devices:
        print_status("SPI", True, f"{len(spi_devices)} device(s): {', '.join(d.name for d in spi_devices)}")
    else:
        print_status("SPI", False, "no SPI devices found (enable in raspi-config)")

    # I2C devices
    i2c_devices = list(spi_path.glob('i2c-*'))
    if i2c_devices:
        print_status("I2C", True, f"{len(i2c_devices)} bus(es)")
    else:
        print_status("I2C", False, "no I2C devices found")

    # GPIO (check if gpiomem available)
    gpio_mem = Path('/dev/gpiomem')
    gpio_chip = Path('/dev/gpiochip0')
    if gpio_mem.exists() or gpio_chip.exists():
        print_status("GPIO", True, "accessible")
    else:
        print_status("GPIO", False, "not accessible")


def check_sdr():
    """Check SDR hardware and software."""
    print_header("SDR (Software Defined Radio)")

    # Check RTL-SDR
    rtl_test = shutil.which('rtl_test')
    if rtl_test:
        try:
            result = subprocess.run(['rtl_test', '-t'], capture_output=True, text=True, timeout=5)
            output = result.stderr + result.stdout
            if 'Found' in output:
                print_status("RTL-SDR device", True, "detected")
            else:
                print_status("RTL-SDR device", False, "no device found")
        except subprocess.TimeoutExpired:
            print_status("RTL-SDR device", True, "detected (test timeout)")
        except Exception as e:
            print_status("RTL-SDR", False, str(e))
    else:
        print_status("RTL-SDR tools", False, "not installed (apt install rtl-sdr)")

    # Check OpenWebRX
    openwebrx = shutil.which('openwebrx')
    if openwebrx:
        try:
            if SERVICE_CHECK_AVAILABLE:
                svc_status = _check_service('openwebrx')
                print_status("OpenWebRX", svc_status.available, svc_status.state.value)
            else:
                result = subprocess.run(['systemctl', 'is-active', 'openwebrx'],
                                       capture_output=True, text=True, timeout=5)
                status = result.stdout.strip()
                print_status("OpenWebRX", status == 'active', status)
        except Exception:
            print_status("OpenWebRX", True, "installed")
    else:
        print_status("OpenWebRX", False, "not installed")

    # Check GQRX
    gqrx = shutil.which('gqrx')
    print_status("GQRX", gqrx is not None, "installed" if gqrx else "not installed")


def check_logs():
    """Check for recent errors in logs."""
    print_header("RECENT LOG ERRORS")

    log_sources = [
        ('meshtasticd', ['journalctl', '-u', 'meshtasticd', '-n', '10', '--no-pager', '-p', 'err']),
        ('rnsd', ['journalctl', '-u', 'rnsd', '-n', '10', '--no-pager', '-p', 'err']),
    ]

    for name, cmd in log_sources:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            lines = [l for l in result.stdout.strip().split('\n')
                     if l and '-- No entries --' not in l]
            if lines:
                print(f"\n  {name} errors ({len(lines)} recent):")
                for line in lines[:3]:  # Show first 3
                    print(f"    {line[:80]}")
            else:
                print_status(f"{name} errors", True, "none")
        except Exception:
            pass

    # NomadNet logfile (not in journalctl — uses its own logfile)
    user_home = get_real_user_home()
    nomadnet_log = user_home / '.nomadnetwork' / 'logfile'
    if nomadnet_log.exists():
        try:
            with open(nomadnet_log, 'r') as f:
                lines = list(collections.deque(f, maxlen=20))
            error_patterns = [
                'Error', 'Exception', 'CRITICAL',
                'AuthenticationError', 'PermissionError',
            ]
            error_lines = [
                line for line in lines
                if any(p in line for p in error_patterns)
            ]
            if error_lines:
                print(f"\n  NomadNet errors "
                      f"({len(error_lines)} recent):")
                for line in error_lines[:3]:
                    print(f"    {line[:80]}")
            else:
                print_status("NomadNet errors", True, "none")
        except (OSError, PermissionError):
            print_status("NomadNet log", False, "cannot read")


def check_ham_callsign():
    """Check for ham radio callsign configuration."""
    print_header("HAM RADIO CONFIG")

    # Check for callsign in various places
    callsign_found = False

    # Check environment
    callsign = os.environ.get('CALLSIGN', os.environ.get('HAM_CALLSIGN', ''))
    if callsign:
        print_status("Callsign (env)", True, callsign)
        callsign_found = True

    # Check NomadNet config for identity
    nomad_config = get_real_user_home() / '.nomadnetwork' / 'config'
    if nomad_config.exists():
        try:
            content = nomad_config.read_text()
            if 'identity' in content.lower():
                print_status("NomadNet identity", True, "configured")
        except Exception:
            pass

    if not callsign_found:
        print_status("Callsign", False, "not found in environment (set CALLSIGN=)")


def run_gateway_wizard():
    """Run the AI-like gateway diagnostic wizard."""
    if not _HAS_GATEWAY_DIAG:
        print("\nError: Could not load gateway diagnostic module")
        print("Make sure you're running from the MeshForge directory")
        return False

    try:
        diag = _GatewayDiagnostic()
        print(diag.run_wizard())
        return True
    except Exception as e:
        print(f"\nError running gateway diagnostic: {e}")
        return False


def main():
    """Run all diagnostics."""
    import argparse

    parser = argparse.ArgumentParser(
        description="MeshForge Diagnostic Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 diagnose.py              Full system diagnostics
  python3 diagnose.py --gateway    RNS/Meshtastic gateway wizard
  python3 diagnose.py -g           Gateway wizard (short form)
  python3 diagnose.py -v           Verbose output
        """
    )
    parser.add_argument('--gateway', '-g', action='store_true',
                        help='Run RNS/Meshtastic gateway setup wizard')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output with debug info')
    parser.add_argument('--json', action='store_true',
                        help='Output results as JSON')

    args = parser.parse_args()

    # Set up logging based on verbosity
    if args.verbose:
        import logging
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        )
        print("Verbose mode enabled - debug logging active\n")

    if args.gateway:
        run_gateway_wizard()
        return

    print()
    print("MeshForge Diagnostics")
    print("=====================")
    print("For RF engineers, network operators, and HAMs")
    print("\nTip: Use --gateway for RNS/Meshtastic setup wizard")

    if not is_root():
        print("\nNote: Running without root - some checks may be limited")
        print("      For full diagnostics, run: sudo python3 diagnose.py")

    # Core services and connectivity
    check_services()
    check_serial_ports()
    check_meshtastic_connection()
    check_cli()
    check_rns_port()
    check_rns_config()
    check_rns_interfaces()
    check_nomadnet()
    check_processes()

    # System health
    check_system_resources()
    check_network_interfaces()

    # Hardware interfaces (LoRa)
    check_spi_gpio()

    # SDR tools
    check_sdr()

    # Ham radio specific
    check_ham_callsign()

    # Log analysis
    check_logs()

    print()
    print("=" * 60)
    print("  Diagnostics complete")
    print("=" * 60)
    print()

    # Wait for user input before exiting (prevents window from closing)
    try:
        input("\nPress Enter to continue...")
    except (KeyboardInterrupt, EOFError):
        pass


if __name__ == '__main__':
    main()
