"""
LoRa Preset Mapping - Meshtastic ↔ RNode Configuration

Provides proven/tested configurations for bridging Meshtastic and RNS networks.
The key to successful bridging is matching LoRa parameters exactly.

Meshtastic presets are mapped to RNode configuration parameters:
- Frequency (Hz)
- Bandwidth (Hz)
- Spreading Factor (7-12)
- Coding Rate (5-8, representing 4/5 through 4/8)

Usage:
    from utils.lora_presets import get_rnode_config_for_meshtastic_preset

    config = get_rnode_config_for_meshtastic_preset('MEDIUM_FAST', region='US')
    # Returns: {'frequency': 906875000, 'bandwidth': 250000, 'spreading_factor': 10, ...}
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional, List
from enum import Enum

from utils.ports import MESHTASTICD_PORTS

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Use centralized service checker
_check_service, _check_systemd_service, _ServiceState, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'check_systemd_service', 'ServiceState'
)
if _HAS_SERVICE_CHECK:
    check_service = _check_service
    check_systemd_service = _check_systemd_service
    ServiceState = _ServiceState

from utils.cli import find_meshtastic_cli


class MeshtasticPreset(Enum):
    """Official Meshtastic modem presets (fastest to slowest)"""
    SHORT_TURBO = 'SHORT_TURBO'      # SF7, BW500, CR8 - Very fast, <1km (may be illegal)
    SHORT_FAST = 'SHORT_FAST'        # SF7, BW250, CR8 - Very fast, 1-5km
    SHORT_SLOW = 'SHORT_SLOW'        # SF7, BW125, CR8 - Fast, 1-5km
    MEDIUM_FAST = 'MEDIUM_FAST'      # SF10, BW250, CR8 - MtnMesh Standard
    MEDIUM_SLOW = 'MEDIUM_SLOW'      # SF10, BW125, CR8 - Balanced
    LONG_FAST = 'LONG_FAST'          # SF11, BW250, CR8 - Default Meshtastic
    LONG_MODERATE = 'LONG_MODERATE'  # SF11, BW125, CR8 - Extended range
    LONG_SLOW = 'LONG_SLOW'          # SF12, BW125, CR8 - Extreme range (SAR)
    VERY_LONG_SLOW = 'VERY_LONG_SLOW'  # SF12, BW62.5, CR8 - Experimental


@dataclass
class LoRaConfig:
    """LoRa radio configuration parameters"""
    frequency: int          # Hz (e.g., 906875000 for 906.875 MHz)
    bandwidth: int          # Hz (e.g., 250000 for 250 kHz)
    spreading_factor: int   # 7-12
    coding_rate: int        # 5-8 (representing 4/5 through 4/8)
    tx_power: int           # dBm (0-22 typical, up to 30 for high-power)

    # Metadata
    preset_name: str = ""
    description: str = ""
    estimated_range: str = ""
    estimated_throughput: str = ""

    def to_dict(self) -> Dict:
        return {
            'frequency': self.frequency,
            'bandwidth': self.bandwidth,
            'spreading_factor': self.spreading_factor,
            'coding_rate': self.coding_rate,
            'tx_power': self.tx_power,
            'preset_name': self.preset_name,
            'description': self.description,
        }


# Meshtastic preset definitions (LoRa parameters only, frequency from region)
MESHTASTIC_PRESETS = {
    'SHORT_TURBO': {
        'bandwidth': 500000,
        'spreading_factor': 7,
        'coding_rate': 8,
        'description': 'Very high speed, very short range (<1km)',
        'estimated_range': '<1 km',
        'estimated_throughput': '~21.9 kbps',
        'rns_data_speed': 8,  # RNS_Over_Meshtastic setting
        'rns_delay': 0.4,     # Recommended for RNS bridge
        'warning': 'May be illegal in some regions (500kHz BW)',
    },
    'SHORT_FAST': {
        'bandwidth': 250000,
        'spreading_factor': 7,
        'coding_rate': 8,
        'description': 'High speed, short range - Urban/high-density',
        'estimated_range': '1-5 km',
        'estimated_throughput': '~10.9 kbps',
        'rns_data_speed': 6,
        'rns_delay': 1.0,
    },
    'SHORT_SLOW': {
        'bandwidth': 125000,
        'spreading_factor': 7,
        'coding_rate': 8,
        'description': 'Fast, reliable short range',
        'estimated_range': '1-5 km',
        'estimated_throughput': '~5.5 kbps',
        'rns_data_speed': 5,
        'rns_delay': 3.0,
    },
    'MEDIUM_FAST': {
        'bandwidth': 250000,
        'spreading_factor': 10,
        'coding_rate': 8,
        'description': 'MtnMesh Community Standard - Best balance',
        'estimated_range': '5-20 km',
        'estimated_throughput': '~3.5 kbps',
        'rns_data_speed': 4,
        'rns_delay': 4.0,
        'recommended': True,
    },
    'MEDIUM_SLOW': {
        'bandwidth': 125000,
        'spreading_factor': 10,
        'coding_rate': 8,
        'description': 'Balanced speed and range',
        'estimated_range': '5-20 km',
        'estimated_throughput': '~1.8 kbps',
        'rns_data_speed': 3,
        'rns_delay': 6.0,
    },
    'LONG_FAST': {
        'bandwidth': 250000,
        'spreading_factor': 11,
        'coding_rate': 8,
        'description': 'Default Meshtastic - Good for most deployments',
        'estimated_range': '10-30 km',
        'estimated_throughput': '~1.1 kbps',
        'rns_data_speed': 0,
        'rns_delay': 8.0,
        'default': True,
        'rns_warning': 'Not recommended for RNS - slow throughput',
    },
    'LONG_MODERATE': {
        'bandwidth': 125000,
        'spreading_factor': 11,
        'coding_rate': 8,
        'description': 'Extended range with moderate speed',
        'estimated_range': '15-40 km',
        'estimated_throughput': '~550 bps',
        'rns_data_speed': 7,
        'rns_delay': 12.0,
        'rns_warning': 'Very slow for RNS data transfer',
    },
    'LONG_SLOW': {
        'bandwidth': 125000,
        'spreading_factor': 12,
        'coding_rate': 8,
        'description': 'Maximum range - Search and Rescue',
        'estimated_range': '20-50 km',
        'estimated_throughput': '~300 bps',
        'rns_data_speed': 1,
        'rns_delay': 15.0,
        'rns_warning': 'Not recommended for RNS - extremely slow',
    },
    'VERY_LONG_SLOW': {
        'bandwidth': 62500,
        'spreading_factor': 12,
        'coding_rate': 8,
        'description': 'Experimental - Extreme range',
        'estimated_range': '30-60+ km',
        'estimated_throughput': '~150 bps',
        'rns_data_speed': None,  # Not supported
        'rns_delay': None,
        'warning': 'Experimental, very slow',
        'rns_warning': 'Not supported by RNS_Over_Meshtastic',
    },
}


# RNS_Over_Meshtastic data_speed to preset mapping
# From: https://github.com/landandair/RNS_Over_Meshtastic
RNS_DATA_SPEED_MAP = {
    8: {'preset': 'SHORT_TURBO', 'delay': 0.4, 'throughput': '~500 B/s', 'recommended': True},
    6: {'preset': 'SHORT_FAST', 'delay': 1.0, 'throughput': '~300 B/s'},
    5: {'preset': 'SHORT_SLOW', 'delay': 3.0, 'throughput': '~150 B/s'},
    4: {'preset': 'MEDIUM_FAST', 'delay': 4.0, 'throughput': '~100 B/s'},
    3: {'preset': 'MEDIUM_SLOW', 'delay': 6.0, 'throughput': '~70 B/s'},
    7: {'preset': 'LONG_MODERATE', 'delay': 12.0, 'throughput': '~35 B/s'},
    0: {'preset': 'LONG_FAST', 'delay': 8.0, 'throughput': '~50 B/s', 'warning': 'Not recommended'},
    1: {'preset': 'LONG_SLOW', 'delay': 15.0, 'throughput': '~25 B/s', 'warning': 'Very slow'},
}


# Region-specific frequency settings
# Meshtastic uses channel slots - these are the primary frequencies
REGION_FREQUENCIES = {
    'US': {
        'primary': 906875000,    # 906.875 MHz - Common for gateway
        'slot_0': 903080000,     # First channel slot
        'slot_20': 906875000,    # MtnMesh standard slot
        'range': (902000000, 928000000),
    },
    'EU': {
        'primary': 869525000,    # 869.525 MHz
        'slot_0': 869450000,
        'range': (863000000, 870000000),
    },
    'AU': {
        'primary': 916000000,
        'slot_0': 915400000,
        'range': (915000000, 928000000),
    },
    'NZ': {
        'primary': 865200000,
        'slot_0': 864000000,
        'range': (864000000, 868000000),
    },
    'TW': {
        'primary': 923000000,
        'slot_0': 922000000,
        'range': (920000000, 925000000),
    },
    'JP': {
        'primary': 920000000,
        'slot_0': 920000000,
        'range': (920000000, 923000000),
    },
}


# Proven/tested gateway configurations
# Reference: https://github.com/landandair/RNS_Over_Meshtastic
PROVEN_GATEWAY_CONFIGS = {
    'rns_turbo_gateway': {
        'name': 'RNS Turbo Gateway',
        'description': 'Recommended for RNS_Over_Meshtastic - Maximum throughput',
        'meshtastic_preset': 'SHORT_TURBO',
        'meshtastic_slot': 0,
        'region': 'US',
        'frequency': 903080000,
        'bandwidth': 500000,
        'spreading_factor': 7,
        'coding_rate': 8,
        'tx_power': 22,
        'rns_data_speed': 8,
        'rns_throughput': '~500 B/s',
        'tested': True,
        'recommended_for_rns': True,
        'notes': 'Best for RNS bridge - ~500 bytes/sec, 0.4s delay',
        'warning': '500kHz BW may be illegal in some regions',
    },
    'rns_shortfast_gateway': {
        'name': 'RNS Short-Fast Gateway',
        'description': 'Legal alternative for RNS bridge - Good throughput',
        'meshtastic_preset': 'SHORT_FAST',
        'meshtastic_slot': 0,
        'region': 'US',
        'frequency': 903080000,
        'bandwidth': 250000,
        'spreading_factor': 7,
        'coding_rate': 8,
        'tx_power': 22,
        'rns_data_speed': 6,
        'rns_throughput': '~300 B/s',
        'tested': True,
        'recommended_for_rns': True,
        'notes': 'Good RNS bridge option - ~300 bytes/sec, 1.0s delay',
    },
    'mtnmesh_gateway': {
        'name': 'MtnMesh Gateway',
        'description': 'Tested configuration for MtnMesh community networks',
        'meshtastic_preset': 'MEDIUM_FAST',
        'meshtastic_slot': 20,
        'region': 'US',
        'frequency': 906875000,  # Matches MtnMesh slot 20
        'bandwidth': 250000,
        'spreading_factor': 10,
        'coding_rate': 8,
        'tx_power': 22,
        'rns_data_speed': 4,
        'rns_throughput': '~100 B/s',
        'tested': True,
        'notes': 'Standard MtnMesh configuration - SF10, BW250, CR8',
    },
    'long_range_gateway': {
        'name': 'Long Range Gateway',
        'description': 'Default Meshtastic compatibility - maximum interop',
        'meshtastic_preset': 'LONG_FAST',
        'meshtastic_slot': 0,
        'region': 'US',
        'frequency': 903080000,  # Slot 0
        'bandwidth': 250000,
        'spreading_factor': 11,
        'coding_rate': 8,
        'tx_power': 22,
        'rns_data_speed': 0,
        'rns_throughput': '~50 B/s',
        'tested': True,
        'notes': 'Compatible with default Meshtastic installations',
        'rns_warning': 'Not recommended for RNS - slow throughput',
    },
    'urban_fast_gateway': {
        'name': 'Urban Fast Gateway',
        'description': 'High-speed for dense urban environments',
        'meshtastic_preset': 'SHORT_FAST',
        'meshtastic_slot': 0,
        'region': 'US',
        'frequency': 903080000,
        'bandwidth': 250000,
        'spreading_factor': 7,
        'coding_rate': 8,
        'tx_power': 20,
        'rns_data_speed': 6,
        'rns_throughput': '~300 B/s',
        'tested': True,
        'notes': 'Fastest reliable config for city deployments',
    },
    'sar_gateway': {
        'name': 'SAR/Emergency Gateway',
        'description': 'Maximum range for Search and Rescue operations',
        'meshtastic_preset': 'LONG_SLOW',
        'meshtastic_slot': 0,
        'region': 'US',
        'frequency': 903080000,
        'bandwidth': 125000,
        'spreading_factor': 12,
        'coding_rate': 8,
        'tx_power': 30,  # High-power hardware required
        'rns_data_speed': 1,
        'rns_throughput': '~25 B/s',
        'tested': True,
        'notes': 'Extreme range, very slow - for emergency comms only',
        'rns_warning': 'Not recommended for RNS - extremely slow',
    },
}


def get_rnode_config_for_meshtastic_preset(
    preset: str,
    region: str = 'US',
    channel_slot: int = 0,
    tx_power: int = 22
) -> LoRaConfig:
    """
    Get RNode configuration that matches a Meshtastic preset.

    Args:
        preset: Meshtastic modem preset name (e.g., 'MEDIUM_FAST')
        region: ITU region for frequency selection ('US', 'EU', etc.)
        channel_slot: Meshtastic channel slot number (0-20+)
        tx_power: Desired TX power in dBm

    Returns:
        LoRaConfig with matching parameters
    """
    preset_upper = preset.upper().replace(' ', '_').replace('-', '_')

    if preset_upper not in MESHTASTIC_PRESETS:
        raise ValueError(f"Unknown preset: {preset}. Valid presets: {list(MESHTASTIC_PRESETS.keys())}")

    preset_data = MESHTASTIC_PRESETS[preset_upper]
    region_data = REGION_FREQUENCIES.get(region.upper(), REGION_FREQUENCIES['US'])

    # Calculate frequency from channel slot
    # Meshtastic channel spacing varies by region and bandwidth
    base_freq = region_data['slot_0']
    channel_spacing = preset_data['bandwidth']  # Approximate
    frequency = base_freq + (channel_slot * channel_spacing)

    # Ensure frequency is within region limits
    freq_min, freq_max = region_data['range']
    frequency = max(freq_min, min(frequency, freq_max))

    return LoRaConfig(
        frequency=frequency,
        bandwidth=preset_data['bandwidth'],
        spreading_factor=preset_data['spreading_factor'],
        coding_rate=preset_data['coding_rate'],
        tx_power=tx_power,
        preset_name=preset_upper,
        description=preset_data['description'],
        estimated_range=preset_data.get('estimated_range', ''),
        estimated_throughput=preset_data.get('estimated_throughput', ''),
    )


def get_proven_gateway_config(config_name: str) -> Optional[Dict]:
    """Get a proven/tested gateway configuration by name."""
    return PROVEN_GATEWAY_CONFIGS.get(config_name)


def list_proven_configs() -> List[Dict]:
    """List all proven gateway configurations with metadata."""
    return [
        {
            'id': key,
            'name': config['name'],
            'description': config['description'],
            'preset': config['meshtastic_preset'],
            'tested': config.get('tested', False),
        }
        for key, config in PROVEN_GATEWAY_CONFIGS.items()
    ]


def detect_meshtastic_settings(verbose: bool = False) -> Optional[Dict]:
    """
    Detect current Meshtastic LoRa settings from connected device.

    First checks if meshtasticd service is running (systemctl), then tries:
    1. meshtasticd on localhost:4403 (via meshtastic CLI)
    2. Direct USB/serial connection
    3. CLI auto-detect

    Args:
        verbose: If True, include attempt summary in result

    Returns dict with:
        - preset: str (preset name like 'MEDIUM_FAST') or None
        - frequency: int (Hz)
        - bandwidth: int (Hz)
        - spreading_factor: int
        - coding_rate: int
        - detection_method: str (how it was detected)
        - attempts_log: list (if verbose, summary of attempts)
        - service_running: bool (True if meshtasticd systemd service is active)

    Returns None if detection fails completely (no service, no CLI).
    Returns dict with service_running=True but preset=None if service runs but CLI unavailable.
    """
    import subprocess
    import glob

    attempts_log = []
    result_data = None
    service_running = False

    def log_attempt(method: str, success: bool, detail: str = ""):
        status = "✓" if success else "✗"
        msg = f"{status} {method}"
        if detail:
            msg += f": {detail}"
        attempts_log.append(msg)
        if success:
            logger.info(f"Meshtastic detection: {method} succeeded")
        else:
            logger.debug(f"Meshtastic detection: {method} failed - {detail}")

    # =========================================================================
    # First: Check if meshtasticd systemd service is running
    # =========================================================================
    try:
        # Use centralized service checker if available
        if _HAS_SERVICE_CHECK:
            status = check_service('meshtasticd')
            if status.available:
                service_running = True
                log_attempt("meshtasticd systemd service", True, "running")
            else:
                log_attempt("meshtasticd systemd service", False, status.state.value or "not active")
        else:
            # Fallback to direct systemctl call
            result = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip() == 'active':
                service_running = True
                log_attempt("meshtasticd systemd service", True, "running")
            else:
                log_attempt("meshtasticd systemd service", False, result.stdout.strip() or "not active")
    except subprocess.TimeoutExpired:
        log_attempt("meshtasticd systemd service", False, "check timed out")
    except FileNotFoundError:
        # Not a systemd system, skip this check
        pass
    except Exception as e:
        log_attempt("meshtasticd systemd service", False, str(e))

    # Check if meshtastic CLI is available using centralized finder
    _meshtastic_cli_path = find_meshtastic_cli()
    meshtastic_cli_available = _meshtastic_cli_path is not None

    def run_meshtastic_cmd(args: list, timeout: int = 10) -> tuple:
        """Run meshtastic CLI with detailed error handling.

        Returns:
            Tuple of (CompletedProcess or None, error_reason: str)
        """
        if not meshtastic_cli_available:
            return None, "CLI not installed"

        try:
            result = subprocess.run(
                [_meshtastic_cli_path] + args,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            if result.returncode == 0 and result.stdout.strip():
                return result, None
            elif result.returncode != 0:
                stderr = result.stderr.strip()[:50] if result.stderr else "unknown error"
                return None, f"exit code {result.returncode}: {stderr}"
            else:
                return None, "empty response"
        except subprocess.TimeoutExpired:
            return None, f"timed out ({timeout}s)"
        except FileNotFoundError:
            return None, "CLI not found"
        except Exception as e:
            return None, str(e)[:50]

    def parse_meshtastic_output(output: str) -> Optional[Dict]:
        """Parse meshtastic --export-config output"""
        lines = output.strip().split('\n')
        settings = {}

        for line in lines:
            line_lower = line.lower()
            if 'modem_preset' in line_lower:
                parts = line.split(':')
                if len(parts) >= 2:
                    preset = parts[1].strip().strip('"\'')
                    settings['preset'] = preset
            elif 'region' in line_lower and 'region' not in settings:
                parts = line.split(':')
                if len(parts) >= 2:
                    settings['region'] = parts[1].strip().strip('"\'')

        return settings if 'preset' in settings else None

    # =========================================================================
    # Method 1: Try meshtasticd TCP (most common)
    # =========================================================================
    for port in MESHTASTICD_PORTS:
        method = f"meshtasticd TCP :{port}"
        result, err = run_meshtastic_cmd(['--host', 'localhost', '--port', str(port), '--export-config'])
        if result:
            settings = parse_meshtastic_output(result.stdout)
            if settings:
                log_attempt(method, True)
                result_data = settings
                result_data['detection_method'] = method
                break
        log_attempt(method, False, err or "No response")

    # =========================================================================
    # Method 2: Try direct serial/USB connection
    # =========================================================================
    if not result_data:
        serial_ports = []
        for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*', '/dev/tty.usbserial*', '/dev/tty.usbmodem*']:
            serial_ports.extend(glob.glob(pattern))

        if serial_ports:
            for port in sorted(set(serial_ports))[:2]:  # Try up to 2 ports
                method = f"USB {port}"
                result, err = run_meshtastic_cmd(['--port', port, '--export-config'], timeout=15)
                if result:
                    settings = parse_meshtastic_output(result.stdout)
                    if settings:
                        log_attempt(method, True)
                        result_data = settings
                        result_data['detection_method'] = method
                        break
                log_attempt(method, False, err or "No response")
        else:
            log_attempt("USB serial", False, "No devices found")

    # =========================================================================
    # Method 3: CLI auto-detect (last resort)
    # =========================================================================
    if not result_data:
        method = "CLI auto-detect"
        result, err = run_meshtastic_cmd(['--export-config'], timeout=15)
        if result:
            settings = parse_meshtastic_output(result.stdout)
            if settings:
                log_attempt(method, True)
                result_data = settings
                result_data['detection_method'] = method
        if not result_data:
            log_attempt(method, False, err or "No device found")

    # =========================================================================
    # Build final result
    # =========================================================================
    if result_data:
        preset = result_data.get('preset')
        if preset and preset in MESHTASTIC_PRESETS:
            preset_data = MESHTASTIC_PRESETS[preset]
            region = result_data.get('region', 'US')
            region_data = REGION_FREQUENCIES.get(region, REGION_FREQUENCIES['US'])

            final_result = {
                'preset': preset,
                'region': region,
                'frequency': region_data['primary'],
                'bandwidth': preset_data['bandwidth'],
                'spreading_factor': preset_data['spreading_factor'],
                'coding_rate': preset_data['coding_rate'],
                'description': preset_data.get('description', ''),
                'detection_method': result_data.get('detection_method', 'unknown'),
                'service_running': service_running,
            }

            if verbose:
                final_result['attempts_log'] = attempts_log

            return final_result

    # Preset detection failed - but service might still be running!
    if verbose:
        return {
            'preset': None,
            'error': not service_running,  # Only error if service not running
            'service_running': service_running,
            'attempts_log': attempts_log
        }

    # Return minimal info if service is running even without preset
    if service_running:
        return {'preset': None, 'service_running': True}

    return None


def format_bandwidth_display(bandwidth_hz: int) -> str:
    """Format bandwidth in Hz to display string."""
    if bandwidth_hz >= 1000000:
        return f"{bandwidth_hz / 1000000:.1f} MHz"
    elif bandwidth_hz >= 1000:
        return f"{bandwidth_hz / 1000:.1f} kHz"
    return f"{bandwidth_hz} Hz"


def bandwidth_hz_to_index(bandwidth_hz: int) -> int:
    """Convert bandwidth in Hz to dropdown index (matching RNodeMixin dropdown)."""
    bandwidth_map = {
        7800: 0,
        10400: 1,
        15600: 2,
        20800: 3,
        31250: 4,
        41700: 5,
        62500: 6,
        125000: 7,
        250000: 8,
        500000: 9,
    }
    return bandwidth_map.get(bandwidth_hz, 8)  # Default to 250kHz


def coding_rate_to_index(coding_rate: int) -> int:
    """Convert coding rate (5-8) to dropdown index."""
    # Dropdown: ["4/5", "4/6", "4/7", "4/8"]
    return coding_rate - 5  # 5->0, 6->1, 7->2, 8->3
