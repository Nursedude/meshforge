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


def main():
    """Run all diagnostics."""
    print()
    print("MeshForge Diagnostics")
    print("=====================")

    if not check_root():
        print("\nNote: Running without root - some checks may be limited")
        print("      For full diagnostics, run: sudo python3 diagnose.py")

    check_services()
    check_serial_ports()
    check_meshtastic_connection()
    check_cli()
    check_rns_port()
    check_rns_config()
    check_nomadnet()
    check_processes()

    print()
    print("=" * 60)
    print("  Diagnostics complete")
    print("=" * 60)
    print()


if __name__ == '__main__':
    main()
