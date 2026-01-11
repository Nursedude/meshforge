#!/usr/bin/env python3
"""
MeshForge Standalone Boot - Self-contained entry point

This module provides a completely standalone entry point for MeshForge that:
- Does NOT require root/sudo for basic functionality
- Does NOT depend on GTK, Textual, or other UI frameworks
- Gracefully degrades when dependencies are missing
- Provides access to all tools without external services
- Works in any Python 3.9+ environment

Usage:
    python3 src/standalone.py              # Interactive menu
    python3 src/standalone.py --tools      # List available tools
    python3 src/standalone.py rf           # Run RF calculator
    python3 src/standalone.py sim          # Run network simulator
    python3 src/standalone.py monitor      # Run node monitor
    python3 src/standalone.py --check      # Check dependencies
"""

import sys
import os
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

# Ensure src is in path
SRC_DIR = Path(__file__).parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Version
try:
    from __version__ import __version__
except ImportError:
    __version__ = "4.3.0"


# ═══════════════════════════════════════════════════════════════════════════════
# DEPENDENCY CHECKING - Zero external dependencies required for core functions
# ═══════════════════════════════════════════════════════════════════════════════

class DependencyStatus:
    """Track available dependencies"""

    def __init__(self):
        self.available: Dict[str, bool] = {}
        self.messages: Dict[str, str] = {}
        self._check_all()

    def _check_all(self):
        """Check all optional dependencies"""
        # Core Python - always available
        self.available['python'] = True
        self.messages['python'] = f"Python {sys.version_info.major}.{sys.version_info.minor}"

        # Rich - for pretty output
        try:
            import rich
            self.available['rich'] = True
            self.messages['rich'] = "Rich console available"
        except ImportError:
            self.available['rich'] = False
            self.messages['rich'] = "pip install rich"

        # GTK4 - for GUI
        try:
            import gi
            gi.require_version('Gtk', '4.0')
            from gi.repository import Gtk
            self.available['gtk'] = True
            self.messages['gtk'] = "GTK4 available"
        except (ImportError, ValueError):
            self.available['gtk'] = False
            self.messages['gtk'] = "apt install python3-gi gir1.2-gtk-4.0"

        # Textual - for TUI
        try:
            import textual
            self.available['textual'] = True
            self.messages['textual'] = "Textual TUI available"
        except ImportError:
            self.available['textual'] = False
            self.messages['textual'] = "pip install textual"

        # Flask - for Web UI
        try:
            import flask
            self.available['flask'] = True
            self.messages['flask'] = "Flask web server available"
        except ImportError:
            self.available['flask'] = False
            self.messages['flask'] = "pip install flask"

        # Meshtastic - for device communication
        try:
            import meshtastic
            self.available['meshtastic'] = True
            self.messages['meshtastic'] = "Meshtastic library available"
        except ImportError:
            self.available['meshtastic'] = False
            self.messages['meshtastic'] = "pip install meshtastic"

        # Check root
        self.available['root'] = os.geteuid() == 0
        self.messages['root'] = "Running as root" if self.available['root'] else "Not root (some features limited)"

    def print_status(self):
        """Print dependency status"""
        print("\n============== MeshForge Dependency Status ==============")

        for dep, available in self.available.items():
            status = "[OK]" if available else "[--]"
            msg = self.messages[dep]
            print(f"  {status} {dep:<12} {msg}")

        print("=========================================================\n")


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE TOOLS - Work without ANY external dependencies
# ═══════════════════════════════════════════════════════════════════════════════

class StandaloneTools:
    """Tools that work with zero external dependencies"""

    @staticmethod
    def rf_calculator():
        """RF Calculator - Pure Python, no dependencies"""
        print("\n═══ RF Calculator ═══\n")

        try:
            from utils.rf import (
                haversine_distance, fresnel_radius,
                free_space_path_loss, earth_bulge,
                link_budget, is_fast_available
            )
            fast_mode = " (Cython optimized)" if is_fast_available() else ""
            print(f"RF calculations ready{fast_mode}\n")
        except ImportError:
            # Inline implementation if utils not available
            import math

            def haversine_distance(lat1, lon1, lat2, lon2):
                R = 6371000
                lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
                delta_lat = math.radians(lat2 - lat1)
                delta_lon = math.radians(lon2 - lon1)
                a = (math.sin(delta_lat / 2) ** 2 +
                     math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
                return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

            def free_space_path_loss(distance_m, freq_mhz):
                return 20 * math.log10(distance_m) + 20 * math.log10(freq_mhz) - 27.55

            def fresnel_radius(distance_km, freq_ghz):
                return 17.3 * math.sqrt(distance_km / (4 * freq_ghz))

            def earth_bulge(distance_m):
                return (distance_m ** 2) / (8 * 6371000 * (4/3))

            print("RF calculations ready (inline mode)\n")

        while True:
            print("1. Distance between coordinates")
            print("2. Free Space Path Loss (FSPL)")
            print("3. Fresnel zone radius")
            print("4. Earth bulge calculation")
            print("5. Link budget")
            print("0. Back")

            try:
                choice = input("\nSelect [0]: ").strip() or "0"
            except (KeyboardInterrupt, EOFError):
                break

            if choice == "0":
                break

            elif choice == "1":
                try:
                    print("\nEnter coordinates (lat1, lon1, lat2, lon2):")
                    lat1 = float(input("  Lat1: "))
                    lon1 = float(input("  Lon1: "))
                    lat2 = float(input("  Lat2: "))
                    lon2 = float(input("  Lon2: "))
                    dist = haversine_distance(lat1, lon1, lat2, lon2)
                    print(f"\n  Distance: {dist/1000:.2f} km ({dist:.0f} m)\n")
                except ValueError:
                    print("Invalid input\n")

            elif choice == "2":
                try:
                    print("\nEnter distance and frequency:")
                    dist = float(input("  Distance (km): ")) * 1000
                    freq = float(input("  Frequency (MHz): "))
                    fspl = free_space_path_loss(dist, freq)
                    print(f"\n  FSPL: {fspl:.2f} dB\n")
                except ValueError:
                    print("Invalid input\n")

            elif choice == "3":
                try:
                    print("\nEnter distance and frequency:")
                    dist = float(input("  Distance (km): "))
                    freq = float(input("  Frequency (GHz): "))
                    radius = fresnel_radius(dist, freq)
                    print(f"\n  Fresnel radius: {radius:.2f} m\n")
                except ValueError:
                    print("Invalid input\n")

            elif choice == "4":
                try:
                    dist = float(input("\n  Distance (km): ")) * 1000
                    bulge = earth_bulge(dist)
                    print(f"\n  Earth bulge: {bulge:.2f} m\n")
                except ValueError:
                    print("Invalid input\n")

            elif choice == "5":
                try:
                    print("\nEnter link parameters:")
                    tx_power = float(input("  TX Power (dBm): "))
                    tx_gain = float(input("  TX Antenna Gain (dBi): "))
                    rx_gain = float(input("  RX Antenna Gain (dBi): "))
                    dist = float(input("  Distance (km): ")) * 1000
                    freq = float(input("  Frequency (MHz): "))

                    fspl = free_space_path_loss(dist, freq)
                    rx_power = tx_power + tx_gain + rx_gain - fspl
                    print(f"\n  FSPL: {fspl:.2f} dB")
                    print(f"  RX Power: {rx_power:.2f} dBm\n")
                except ValueError:
                    print("Invalid input\n")

    @staticmethod
    def frequency_calculator():
        """Meshtastic frequency slot calculator - Pure Python"""
        print("\n═══ Frequency Slot Calculator ═══\n")

        # DJB2 hash algorithm (same as Meshtastic firmware)
        def djb2_hash(s):
            h = 5381
            for c in s:
                h = ((h << 5) + h) + ord(c)
            return h & 0xFFFFFFFF

        # Region definitions - all 22 Meshtastic regions
        REGIONS = {
            # Americas
            'US': {'start': 902.0, 'end': 928.0, 'duty': 100, 'power': 30},
            'ANZ': {'start': 915.0, 'end': 928.0, 'duty': 100, 'power': 30},
            # Europe
            'EU_868': {'start': 869.4, 'end': 869.65, 'duty': 10, 'power': 27},
            'EU_433': {'start': 433.0, 'end': 434.0, 'duty': 10, 'power': 12},
            'UK_868': {'start': 869.4, 'end': 869.65, 'duty': 10, 'power': 27},
            'UA_868': {'start': 868.0, 'end': 868.6, 'duty': 100, 'power': 20},
            'UA_433': {'start': 433.0, 'end': 434.79, 'duty': 100, 'power': 12},
            'RU': {'start': 868.7, 'end': 869.2, 'duty': 100, 'power': 20},
            # Asia-Pacific
            'JP': {'start': 920.8, 'end': 923.8, 'duty': 100, 'power': 16},
            'KR': {'start': 920.0, 'end': 923.0, 'duty': 100, 'power': 10},
            'TW': {'start': 920.0, 'end': 925.0, 'duty': 100, 'power': 27},
            'CN': {'start': 470.0, 'end': 510.0, 'duty': 100, 'power': 19},
            'IN': {'start': 865.0, 'end': 867.0, 'duty': 100, 'power': 30},
            'TH': {'start': 920.0, 'end': 925.0, 'duty': 100, 'power': 16},
            'PH': {'start': 920.0, 'end': 925.0, 'duty': 100, 'power': 16},
            'SG_923': {'start': 920.0, 'end': 925.0, 'duty': 100, 'power': 20},
            'MY_433': {'start': 433.0, 'end': 435.0, 'duty': 100, 'power': 12},
            'MY_919': {'start': 919.0, 'end': 924.0, 'duty': 100, 'power': 20},
            # Oceania
            'NZ_865': {'start': 864.0, 'end': 868.0, 'duty': 100, 'power': 36},
            # 2.4 GHz ISM
            'LORA_24': {'start': 2400.0, 'end': 2483.5, 'duty': 100, 'power': 10},
        }

        # Preset bandwidths (kHz) - FIXED: LONG_SLOW/MODERATE use 125 kHz
        PRESETS = {
            'LONG_FAST': 250,
            'LONG_SLOW': 125,       # FIXED: was incorrectly 250
            'LONG_MODERATE': 125,   # FIXED: was incorrectly 250
            'MEDIUM_FAST': 250,
            'MEDIUM_SLOW': 250,
            'SHORT_FAST': 250,
            'SHORT_SLOW': 250,
            'SHORT_TURBO': 500,
            'VERY_LONG_SLOW': 62.5,
        }

        print("Regions:", ", ".join(REGIONS.keys()))
        print("Presets:", ", ".join(PRESETS.keys()))
        print()

        try:
            channel = input("Channel name [LongFast]: ").strip() or "LongFast"
            region = input("Region [US]: ").strip().upper() or "US"
            preset = input("Preset [LONG_FAST]: ").strip().upper() or "LONG_FAST"

            if region not in REGIONS:
                print(f"Unknown region: {region}")
                return

            if preset not in PRESETS:
                print(f"Unknown preset: {preset}")
                return

            reg = REGIONS[region]
            bandwidth = PRESETS[preset]

            # Calculate number of channels
            freq_range = (reg['end'] - reg['start']) * 1000  # kHz
            num_channels = int(freq_range / bandwidth)

            # Calculate slot
            hash_val = djb2_hash(channel)
            slot = hash_val % num_channels

            # Calculate frequency
            freq = reg['start'] + (bandwidth / 2000) + (slot * bandwidth / 1000)

            print(f"\n  Channel: {channel}")
            print(f"  Region: {region} ({reg['start']}-{reg['end']} MHz)")
            print(f"  Preset: {preset} ({bandwidth} kHz bandwidth)")
            print(f"  Hash: {hash_val}")
            print(f"  Slot: {slot} of {num_channels}")
            print(f"  Frequency: {freq:.3f} MHz\n")

        except (KeyboardInterrupt, EOFError):
            print()

    @staticmethod
    def network_simulator():
        """Simple network topology simulator - Pure Python"""
        print("\n═══ Network Simulator ═══\n")

        nodes = {}
        links = []

        def add_node():
            name = input("  Node name: ").strip()
            try:
                lat = float(input("  Latitude: "))
                lon = float(input("  Longitude: "))
                nodes[name] = {'lat': lat, 'lon': lon}
                print(f"  Added node: {name}")
            except ValueError:
                print("  Invalid coordinates")

        def show_nodes():
            if not nodes:
                print("  No nodes defined")
                return
            print(f"\n  {'Name':<15} {'Latitude':<12} {'Longitude':<12}")
            print("  " + "-" * 40)
            for name, pos in nodes.items():
                print(f"  {name:<15} {pos['lat']:<12.4f} {pos['lon']:<12.4f}")
            print()

        def calculate_links():
            if len(nodes) < 2:
                print("  Need at least 2 nodes")
                return

            import math
            def haversine(lat1, lon1, lat2, lon2):
                R = 6371
                lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
                delta_lat = math.radians(lat2 - lat1)
                delta_lon = math.radians(lon2 - lon1)
                a = (math.sin(delta_lat / 2) ** 2 +
                     math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
                return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

            print(f"\n  {'Link':<25} {'Distance':<12} {'Est. Quality':<12}")
            print("  " + "-" * 50)

            node_names = list(nodes.keys())
            for i, n1 in enumerate(node_names):
                for n2 in node_names[i+1:]:
                    dist = haversine(
                        nodes[n1]['lat'], nodes[n1]['lon'],
                        nodes[n2]['lat'], nodes[n2]['lon']
                    )
                    # Simple quality estimation
                    quality = max(0, min(100, 100 - dist * 2))
                    print(f"  {n1} <-> {n2:<15} {dist:>8.2f} km  {quality:>8.0f}%")
            print()

        while True:
            print("1. Add node")
            print("2. Show nodes")
            print("3. Calculate links")
            print("4. Clear all")
            print("0. Back")

            try:
                choice = input("\nSelect [0]: ").strip() or "0"
            except (KeyboardInterrupt, EOFError):
                break

            if choice == "0":
                break
            elif choice == "1":
                add_node()
            elif choice == "2":
                show_nodes()
            elif choice == "3":
                calculate_links()
            elif choice == "4":
                nodes.clear()
                print("  Cleared all nodes")

    @staticmethod
    def device_scanner():
        """Scan for USB/Serial devices - Pure Python"""
        print("\n═══ Device Scanner ═══\n")

        import glob

        # Check /dev for serial ports
        patterns = [
            '/dev/ttyUSB*',
            '/dev/ttyACM*',
            '/dev/tty.usbserial*',
            '/dev/tty.usbmodem*',
        ]

        devices = []
        for pattern in patterns:
            devices.extend(glob.glob(pattern))

        if devices:
            print("Found serial devices:")
            for dev in sorted(devices):
                print(f"  {dev}")

            # Try to get more info if pyserial available
            try:
                import serial.tools.list_ports
                print("\nDetailed port info:")
                for port in serial.tools.list_ports.comports():
                    print(f"  {port.device}")
                    print(f"    Description: {port.description}")
                    print(f"    VID:PID: {port.vid}:{port.pid}" if port.vid else "")
                    print()
            except ImportError:
                print("\n  (Install pyserial for detailed info)")
        else:
            print("No serial devices found")

        # Check for SPI devices
        spi_devs = glob.glob('/dev/spidev*')
        if spi_devs:
            print("\nSPI devices:")
            for dev in spi_devs:
                print(f"  {dev}")

        # Check for I2C devices
        i2c_devs = glob.glob('/dev/i2c-*')
        if i2c_devs:
            print("\nI2C buses:")
            for dev in i2c_devs:
                print(f"  {dev}")

        print()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN MENU
# ═══════════════════════════════════════════════════════════════════════════════

def print_banner():
    """Print the MeshForge banner"""
    print("""
=================================================================
    __  __           _     _____
   |  \/  | ___  ___| |__ |  ___|__  _ __ __ _  ___
   | |\/| |/ _ \/ __| '_ \| |_ / _ \| '__/ _` |/ _ \\
   | |  | |  __/\__ \ | | |  _| (_) | | | (_| |  __/
   |_|  |_|\___||___/_| |_|_|  \___/|_|  \__, |\___|
                                         |___/
-----------------------------------------------------------------
         Standalone Mode - No External Dependencies
=================================================================
""")
    print(f"  Version: {__version__}")
    print(f"  Python:  {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print()


def main_menu(deps: DependencyStatus):
    """Interactive main menu"""
    tools = StandaloneTools()

    while True:
        print("\n================== STANDALONE TOOLS ====================")
        print("  1. RF Calculator        - Distance, FSPL, Fresnel, Link")
        print("  2. Frequency Calculator - Channel slot, region settings")
        print("  3. Network Simulator    - Topology planning, link quality")
        print("  4. Device Scanner       - USB, Serial, SPI, I2C devices")
        print("---------------------------------------------------------")
        print("                   FULL INTERFACES")
        print("---------------------------------------------------------")

        if deps.available['gtk']:
            print("  g. GTK4 Desktop UI      [Available]")
        else:
            print("  g. GTK4 Desktop UI      [Not installed]")

        if deps.available['textual']:
            print("  t. Textual TUI          [Available]")
        else:
            print("  t. Textual TUI          [Not installed]")

        if deps.available['flask']:
            print("  w. Web Interface        [Available]")
        else:
            print("  w. Web Interface        [Not installed]")

        if deps.available['rich']:
            print("  r. Rich CLI             [Available]")
        else:
            print("  r. Rich CLI             [Not installed]")

        print("---------------------------------------------------------")
        print("  d. Check dependencies")
        print("  q. Quit")
        print("=========================================================")

        try:
            choice = input("\nSelect option: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n\nAloha! 🤙")
            break

        if choice == 'q':
            print("\nAloha! 🤙")
            break
        elif choice == '1':
            tools.rf_calculator()
        elif choice == '2':
            tools.frequency_calculator()
        elif choice == '3':
            tools.network_simulator()
        elif choice == '4':
            tools.device_scanner()
        elif choice == 'd':
            deps.print_status()
        elif choice == 'g':
            if deps.available['gtk']:
                print("\nLaunching GTK4 Desktop UI...")
                os.execv(sys.executable, [sys.executable, str(SRC_DIR / 'main_gtk.py')])
            else:
                print("\nGTK4 not available. Install with:")
                print("  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1")
        elif choice == 't':
            if deps.available['textual']:
                print("\nLaunching Textual TUI...")
                os.execv(sys.executable, [sys.executable, str(SRC_DIR / 'main_tui.py')])
            else:
                print("\nTextual not available. Install with:")
                print("  pip install textual")
        elif choice == 'w':
            if deps.available['flask']:
                print("\nLaunching Web Interface...")
                os.execv(sys.executable, [sys.executable, str(SRC_DIR / 'main_web.py')])
            else:
                print("\nFlask not available. Install with:")
                print("  pip install flask")
        elif choice == 'r':
            if deps.available['rich']:
                print("\nLaunching Rich CLI...")
                os.execv(sys.executable, [sys.executable, str(SRC_DIR / 'main.py')])
            else:
                print("\nRich not available. Install with:")
                print("  pip install rich")
        else:
            print("\nInvalid option")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="MeshForge Standalone - Self-contained mesh network tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 src/standalone.py              # Interactive menu
  python3 src/standalone.py --check      # Check dependencies
  python3 src/standalone.py rf           # RF calculator
  python3 src/standalone.py freq         # Frequency calculator
  python3 src/standalone.py sim          # Network simulator
  python3 src/standalone.py scan         # Device scanner
        """
    )
    parser.add_argument('tool', nargs='?', help='Tool to run directly')
    parser.add_argument('--check', action='store_true', help='Check dependencies')
    parser.add_argument('--version', action='version', version=f'MeshForge {__version__}')

    args = parser.parse_args()

    # Check dependencies
    deps = DependencyStatus()

    if args.check:
        deps.print_status()
        return

    tools = StandaloneTools()

    # Direct tool launch
    if args.tool:
        tool_map = {
            'rf': tools.rf_calculator,
            'freq': tools.frequency_calculator,
            'sim': tools.network_simulator,
            'scan': tools.device_scanner,
        }
        if args.tool in tool_map:
            print_banner()
            tool_map[args.tool]()
        else:
            print(f"Unknown tool: {args.tool}")
            print(f"Available: {', '.join(tool_map.keys())}")
        return

    # Interactive menu
    print_banner()
    main_menu(deps)


if __name__ == '__main__':
    main()
