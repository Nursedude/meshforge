#!/usr/bin/env python3
"""
MeshForge Quick Diagnostic Tool

Version: 0.4.3-beta
Updated: 2026-01-06

A fast diagnostic tool for troubleshooting Meshtastic and RNS gateway issues.
Inspired by RNS Gateway's supervisor pattern but integrated with MeshForge.

Usage:
    python -m src.tools.mesh_diag          # Interactive menu
    python -m src.tools.mesh_diag --check  # Quick health check
    python -m src.tools.mesh_diag --fix    # Auto-fix common issues
"""

import sys
import os
import argparse

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.utils.system import (
    get_serial_ports,
    get_rns_config_dir,
    check_dependency,
    get_dependency_status,
    safe_run,
    LORA_SPEED_PRESETS
)
from src.utils.connections import detect_devices, ConnectionMode


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    NC = '\033[0m'  # No Color


def print_ok(msg: str) -> None:
    print(f"{Colors.GREEN}[OK]{Colors.NC} {msg}")


def print_warn(msg: str) -> None:
    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {msg}")


def print_error(msg: str) -> None:
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}")


def print_info(msg: str) -> None:
    print(f"{Colors.BLUE}[INFO]{Colors.NC} {msg}")


def print_header(title: str) -> None:
    print(f"\n{Colors.BOLD}{'=' * 50}{Colors.NC}")
    print(f"{Colors.BOLD}  {title}{Colors.NC}")
    print(f"{Colors.BOLD}{'=' * 50}{Colors.NC}\n")


def check_dependencies() -> dict:
    """Check all required and optional dependencies."""
    print_header("Dependency Check")

    deps = get_dependency_status()
    results = {'passed': 0, 'failed': 0, 'warnings': 0}

    # Required
    required = ['meshtastic', 'pyserial']
    for dep in required:
        key = 'pyserial' if dep == 'pyserial' else dep
        if deps.get(key, False):
            print_ok(f"{dep} installed")
            results['passed'] += 1
        else:
            print_error(f"{dep} NOT installed (required)")
            results['failed'] += 1

    # Optional
    optional = ['rns', 'lxmf', 'flask', 'textual', 'rich', 'gtk4']
    for dep in optional:
        if deps.get(dep, False):
            print_ok(f"{dep} installed")
            results['passed'] += 1
        else:
            print_warn(f"{dep} not installed (optional)")
            results['warnings'] += 1

    return results


def check_devices() -> dict:
    """Check for connected Meshtastic devices."""
    print_header("Device Detection")

    results = {'devices': [], 'found': 0}

    devices = detect_devices()
    if not devices:
        print_error("No Meshtastic devices detected")
        print_info("Check USB connection or start meshtasticd")
        return results

    for device in devices:
        print_ok(device['description'])
        results['devices'].append(device)
        results['found'] += 1

    return results


def check_rns_config() -> dict:
    """Check RNS configuration."""
    print_header("RNS Configuration")

    results = {'configured': False, 'interface_installed': False}

    config_dir = get_rns_config_dir()
    config_file = config_dir / 'config'
    interfaces_dir = config_dir / 'interfaces'

    if config_dir.exists():
        print_ok(f"RNS config directory: {config_dir}")
    else:
        print_warn(f"RNS config directory not found: {config_dir}")
        print_info("Run: rnsd  (to initialize RNS)")
        return results

    if config_file.exists():
        print_ok("RNS config file exists")
        results['configured'] = True

        # Check for Meshtastic interface
        try:
            content = config_file.read_text()
            if 'Meshtastic' in content:
                print_ok("Meshtastic interface configured in RNS")
                results['interface_installed'] = True
            else:
                print_warn("Meshtastic interface not configured in RNS")
        except Exception as e:
            print_warn(f"Could not read config: {e}")
    else:
        print_warn("RNS config file not found")

    # Check for interface file
    interface_file = interfaces_dir / 'Meshtastic_Interface.py'
    if interface_file.exists():
        print_ok("Meshtastic_Interface.py installed")
        results['interface_installed'] = True
    else:
        print_warn("Meshtastic_Interface.py not in interfaces folder")

    return results


def check_meshtastic_connection() -> dict:
    """Check Meshtastic device connection."""
    print_header("Meshtastic Connection Test")

    results = {'connected': False, 'node_info': None}

    if not check_dependency('meshtastic'):
        print_error("Meshtastic library not installed")
        return results

    # Try to get device info
    success, stdout, stderr = safe_run(
        [sys.executable, '-m', 'meshtastic', '--info'],
        timeout=15
    )

    if success:
        print_ok("Connected to Meshtastic device")
        results['connected'] = True
        # Extract basic info
        for line in stdout.split('\n')[:10]:
            if line.strip():
                print_info(line.strip())
    else:
        print_error("Could not connect to Meshtastic device")
        if stderr:
            print_info(stderr[:200])

    return results


def show_lora_presets() -> None:
    """Display LoRa speed presets."""
    print_header("LoRa Speed Presets (for RNS)")

    print(f"{'ID':<4} {'Name':<20} {'Delay':<8} {'Description'}")
    print("-" * 60)

    for speed_id, preset in sorted(LORA_SPEED_PRESETS.items(), key=lambda x: x[1]['delay']):
        print(f"{speed_id:<4} {preset['name']:<20} {preset['delay']:<8.1f} {preset['desc']}")

    print(f"\n{Colors.GREEN}Recommended: SHORT_TURBO (8) for RNS applications{Colors.NC}")


def run_quick_check() -> int:
    """Run quick health check and return exit code."""
    print_header("MeshForge Quick Health Check")

    issues = 0

    # Dependencies
    deps = check_dependencies()
    issues += deps['failed']

    # Devices
    devices = check_devices()
    if devices['found'] == 0:
        issues += 1

    # RNS
    rns = check_rns_config()

    # Connection
    mesh = check_meshtastic_connection()
    if not mesh['connected']:
        issues += 1

    # Summary
    print_header("Summary")
    if issues == 0:
        print_ok("All checks passed!")
    else:
        print_error(f"{issues} issue(s) found")

    return issues


def run_fix_mode() -> None:
    """Attempt to auto-fix common issues."""
    print_header("Auto-Fix Mode")

    # Check and install missing dependencies
    deps = get_dependency_status()

    if not deps.get('meshtastic'):
        print_info("Installing meshtastic...")
        success, _, _ = safe_run(
            [sys.executable, '-m', 'pip', 'install', 'meshtastic'],
            timeout=120
        )
        if success:
            print_ok("Meshtastic installed")
        else:
            print_error("Failed to install meshtastic")

    if not deps.get('pyserial'):
        print_info("Installing pyserial...")
        success, _, _ = safe_run(
            [sys.executable, '-m', 'pip', 'install', 'pyserial'],
            timeout=60
        )
        if success:
            print_ok("Pyserial installed")
        else:
            print_error("Failed to install pyserial")

    # Check dialout group (Linux)
    import platform
    if platform.system() == 'Linux':
        import os
        import grp
        try:
            dialout = grp.getgrnam('dialout')
            if os.getlogin() not in dialout.gr_mem:
                print_warn("User not in dialout group")
                print_info(f"Run: sudo usermod -a -G dialout {os.getlogin()}")
        except Exception:
            pass

    print_ok("Fix check complete")


def interactive_menu() -> None:
    """Interactive diagnostic menu."""
    while True:
        print("\033[2J\033[H", end="")  # Clear screen
        print_header("MeshForge Diagnostic Tool")
        print("1) Quick Health Check")
        print("2) Check Dependencies")
        print("3) Detect Devices")
        print("4) Test Meshtastic Connection")
        print("5) Check RNS Configuration")
        print("6) Show LoRa Presets")
        print("7) Auto-Fix Common Issues")
        print("8) Exit")

        choice = input("\nSelect option: ").strip()

        if choice == '1':
            run_quick_check()
        elif choice == '2':
            check_dependencies()
        elif choice == '3':
            check_devices()
        elif choice == '4':
            check_meshtastic_connection()
        elif choice == '5':
            check_rns_config()
        elif choice == '6':
            show_lora_presets()
        elif choice == '7':
            run_fix_mode()
        elif choice == '8':
            print("73 de MeshForge")
            sys.exit(0)
        else:
            print_warn("Invalid option")

        input("\nPress Enter to continue...")


def main():
    parser = argparse.ArgumentParser(
        description='MeshForge Quick Diagnostic Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s              Interactive menu
  %(prog)s --check      Quick health check
  %(prog)s --fix        Auto-fix common issues
  %(prog)s --presets    Show LoRa presets
        """
    )
    parser.add_argument('--check', action='store_true', help='Run quick health check')
    parser.add_argument('--fix', action='store_true', help='Auto-fix common issues')
    parser.add_argument('--presets', action='store_true', help='Show LoRa presets')

    args = parser.parse_args()

    if args.check:
        sys.exit(run_quick_check())
    elif args.fix:
        run_fix_mode()
    elif args.presets:
        show_lora_presets()
    else:
        interactive_menu()


if __name__ == '__main__':
    main()
