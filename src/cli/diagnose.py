#!/usr/bin/env python3
"""MeshForge Diagnostic Tool

Checks system configuration and identifies common issues.

Usage:
    python3 src/cli/diagnose.py
    sudo python3 src/cli/diagnose.py  # For full diagnostics
"""

import os
import sys
import socket
import subprocess
import shutil
from pathlib import Path


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


def check_root() -> bool:
    """Check if running as root."""
    return os.geteuid() == 0


def check_services():
    """Check service status."""
    print_header("SERVICES")

    services = ['meshtasticd', 'rnsd']
    for svc in services:
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

    # Check port 29716 (RNS AutoInterface)
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.bind(('::', 29716))
        sock.close()
        print_status("RNS port 29716", True, "available")
        port_available = True
    except OSError as e:
        print_status("RNS port 29716", False, f"in use ({e})")
        port_available = False

        # Try to find what's using it
        try:
            result = subprocess.run(
                ['lsof', '-i', ':29716'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout:
                print(f"    Process using port:")
                for line in result.stdout.strip().split('\n')[1:2]:  # First result only
                    print(f"      {line}")
        except Exception:
            pass

    # Check shared instance port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', 37428))
        sock.close()
        if result == 0:
            print_status("RNS shared instance", True, "listening on 37428")
        else:
            print_status("RNS shared instance", False, "not running")
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

    # Check port 9443 (web interface)
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

    try:
        # Use centralized CLI finder
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from utils.cli import find_meshtastic_cli
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

    except ImportError:
        # Fallback
        cli_path = shutil.which('meshtastic')
        if cli_path:
            print_status("meshtastic CLI", True, cli_path)
        else:
            print_status("meshtastic CLI", False, "not found")


def check_rns_config():
    """Check RNS configuration."""
    print_header("RNS CONFIGURATION")

    # Check for config file
    config_locations = [
        Path.home() / '.reticulum' / 'config',
        Path('/etc/reticulum/config'),
    ]

    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user:
        config_locations.insert(0, Path(f'/home/{sudo_user}/.reticulum/config'))

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


def check_nomadnet():
    """Check NomadNet installation."""
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
    except Exception:
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
            lines = [l for l in result.stdout.strip().split('\n') if l and '-- No entries --' not in l]
            if lines:
                print(f"\n  {name} errors ({len(lines)} recent):")
                for line in lines[:3]:  # Show first 3
                    print(f"    {line[:80]}")
            else:
                print_status(f"{name} errors", True, "none")
        except Exception:
            pass


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
    nomad_config = Path.home() / '.nomadnetwork' / 'config'
    if nomad_config.exists():
        try:
            content = nomad_config.read_text()
            if 'identity' in content.lower():
                print_status("NomadNet identity", True, "configured")
        except Exception:
            pass

    if not callsign_found:
        print_status("Callsign", False, "not found in environment (set CALLSIGN=)")


def main():
    """Run all diagnostics."""
    print()
    print("MeshForge Diagnostics")
    print("=====================")
    print("For RF engineers, network operators, and HAMs")

    if not check_root():
        print("\nNote: Running without root - some checks may be limited")
        print("      For full diagnostics, run: sudo python3 diagnose.py")

    # Core services and connectivity
    check_services()
    check_serial_ports()
    check_meshtastic_connection()
    check_cli()
    check_rns_port()
    check_rns_config()
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


if __name__ == '__main__':
    main()
