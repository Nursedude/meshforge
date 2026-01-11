"""
Tools Blueprint - RF and Radio Utilities API

Provides REST API endpoints for:
- Frequency slot calculator (djb2 hash)
- Free space path loss
- Link budget calculations
- Fresnel zone calculations
"""

from flask import Blueprint, jsonify, request
import math

tools_bp = Blueprint('tools', __name__)


# ============================================================================
# Frequency Slot Calculator
# ============================================================================

# All 22 Meshtastic regions with correct band definitions
REGIONS = {
    # Americas
    'US': {'start': 902.0, 'end': 928.0, 'duty': 100, 'power': 30, 'desc': 'United States ISM'},
    'ANZ': {'start': 915.0, 'end': 928.0, 'duty': 100, 'power': 30, 'desc': 'Australia/New Zealand'},
    # Europe
    'EU_868': {'start': 869.4, 'end': 869.65, 'duty': 10, 'power': 27, 'desc': 'EU 869 MHz SRD'},
    'EU_433': {'start': 433.0, 'end': 434.0, 'duty': 10, 'power': 12, 'desc': 'EU 433 MHz'},
    'UK_868': {'start': 869.4, 'end': 869.65, 'duty': 10, 'power': 27, 'desc': 'UK 869 MHz'},
    'UA_868': {'start': 868.0, 'end': 868.6, 'duty': 100, 'power': 20, 'desc': 'Ukraine 868 MHz'},
    'UA_433': {'start': 433.0, 'end': 434.79, 'duty': 100, 'power': 12, 'desc': 'Ukraine 433 MHz'},
    'RU': {'start': 868.7, 'end': 869.2, 'duty': 100, 'power': 20, 'desc': 'Russia'},
    # Asia-Pacific
    'JP': {'start': 920.8, 'end': 923.8, 'duty': 100, 'power': 16, 'desc': 'Japan'},
    'KR': {'start': 920.0, 'end': 923.0, 'duty': 100, 'power': 10, 'desc': 'Korea'},
    'TW': {'start': 920.0, 'end': 925.0, 'duty': 100, 'power': 27, 'desc': 'Taiwan'},
    'CN': {'start': 470.0, 'end': 510.0, 'duty': 100, 'power': 19, 'desc': 'China'},
    'IN': {'start': 865.0, 'end': 867.0, 'duty': 100, 'power': 30, 'desc': 'India'},
    'TH': {'start': 920.0, 'end': 925.0, 'duty': 100, 'power': 16, 'desc': 'Thailand'},
    'PH': {'start': 920.0, 'end': 925.0, 'duty': 100, 'power': 16, 'desc': 'Philippines'},
    'SG_923': {'start': 920.0, 'end': 925.0, 'duty': 100, 'power': 20, 'desc': 'Singapore 923'},
    'MY_433': {'start': 433.0, 'end': 435.0, 'duty': 100, 'power': 12, 'desc': 'Malaysia 433 MHz'},
    'MY_919': {'start': 919.0, 'end': 924.0, 'duty': 100, 'power': 20, 'desc': 'Malaysia 919 MHz'},
    # Oceania
    'NZ_865': {'start': 864.0, 'end': 868.0, 'duty': 100, 'power': 36, 'desc': 'New Zealand 865 MHz'},
    # 2.4 GHz ISM
    'LORA_24': {'start': 2400.0, 'end': 2483.5, 'duty': 100, 'power': 10, 'desc': '2.4 GHz ISM (worldwide)'},
}

# Preset bandwidths (kHz)
PRESETS = {
    'LONG_FAST': {'bandwidth': 250, 'sf': 11, 'cr': '4/5'},
    'LONG_SLOW': {'bandwidth': 125, 'sf': 12, 'cr': '4/8'},
    'LONG_MODERATE': {'bandwidth': 125, 'sf': 11, 'cr': '4/8'},
    'MEDIUM_FAST': {'bandwidth': 250, 'sf': 10, 'cr': '4/5'},
    'MEDIUM_SLOW': {'bandwidth': 250, 'sf': 11, 'cr': '4/5'},
    'SHORT_FAST': {'bandwidth': 250, 'sf': 9, 'cr': '4/5'},
    'SHORT_SLOW': {'bandwidth': 250, 'sf': 10, 'cr': '4/5'},
    'SHORT_TURBO': {'bandwidth': 500, 'sf': 8, 'cr': '4/5'},
    'VERY_LONG_SLOW': {'bandwidth': 62.5, 'sf': 12, 'cr': '4/8'},
}


def djb2_hash(s: str) -> int:
    """
    DJB2 hash algorithm - same as Meshtastic firmware RadioInterface.cpp

    Args:
        s: String to hash (channel name)

    Returns:
        32-bit unsigned hash value
    """
    h = 5381
    for c in s:
        h = ((h << 5) + h) + ord(c)
    return h & 0xFFFFFFFF


@tools_bp.route('/tools/frequency-slot', methods=['GET', 'POST'])
def frequency_slot():
    """
    Calculate Meshtastic frequency slot from channel name.

    GET /api/tools/frequency-slot?channel=LongFast&region=US&preset=LONG_FAST
    POST /api/tools/frequency-slot with JSON body

    Query/JSON parameters:
        channel: Channel name (default: "LongFast")
        region: Region code (default: "US")
        preset: Modem preset (default: "LONG_FAST")
        slot: Direct slot number (optional, overrides channel hash)

    Returns:
        JSON with frequency calculation results
    """
    # Get parameters from query string or JSON body
    if request.method == 'POST':
        data = request.get_json() or {}
    else:
        data = {}

    channel = data.get('channel') or request.args.get('channel', 'LongFast')
    region = data.get('region') or request.args.get('region', 'US')
    preset = data.get('preset') or request.args.get('preset', 'LONG_FAST')
    direct_slot = data.get('slot') or request.args.get('slot')

    # Validate region
    region = region.upper()
    if region not in REGIONS:
        return jsonify({
            'error': f'Unknown region: {region}',
            'valid_regions': list(REGIONS.keys())
        }), 400

    # Validate preset
    preset = preset.upper()
    if preset not in PRESETS:
        return jsonify({
            'error': f'Unknown preset: {preset}',
            'valid_presets': list(PRESETS.keys())
        }), 400

    reg = REGIONS[region]
    preset_info = PRESETS[preset]
    bandwidth = preset_info['bandwidth']

    # Calculate number of channels
    freq_range = (reg['end'] - reg['start']) * 1000  # kHz
    num_channels = max(1, int(freq_range / bandwidth))

    # Calculate slot
    if direct_slot is not None:
        try:
            slot = int(direct_slot)
            if slot < 0 or slot >= num_channels:
                return jsonify({
                    'error': f'Slot must be 0-{num_channels - 1}',
                    'num_channels': num_channels
                }), 400
            hash_val = None
        except ValueError:
            return jsonify({'error': 'Invalid slot number'}), 400
    else:
        hash_val = djb2_hash(channel)
        slot = hash_val % num_channels

    # Calculate frequency
    # Formula: freq = freqStart + (bw/2000) + (slot * bw/1000)
    freq_mhz = reg['start'] + (bandwidth / 2000) + (slot * bandwidth / 1000)
    freq_low = freq_mhz - (bandwidth / 2000)
    freq_high = freq_mhz + (bandwidth / 2000)

    result = {
        'channel': channel,
        'region': region,
        'region_description': reg['desc'],
        'preset': preset,
        'bandwidth_khz': bandwidth,
        'spreading_factor': preset_info['sf'],
        'coding_rate': preset_info['cr'],
        'band': {
            'start_mhz': reg['start'],
            'end_mhz': reg['end'],
            'duty_cycle': reg['duty'],
            'max_power_dbm': reg['power']
        },
        'calculation': {
            'num_channels': num_channels,
            'slot': slot,
            'center_freq_mhz': round(freq_mhz, 4),
            'low_freq_mhz': round(freq_low, 4),
            'high_freq_mhz': round(freq_high, 4)
        }
    }

    if hash_val is not None:
        result['hash'] = hash_val

    return jsonify(result)


@tools_bp.route('/tools/frequency-slot/regions', methods=['GET'])
def get_regions():
    """Get all supported regions with their band information."""
    return jsonify({
        'regions': {
            name: {
                'start_mhz': info['start'],
                'end_mhz': info['end'],
                'duty_cycle': info['duty'],
                'max_power_dbm': info['power'],
                'description': info['desc']
            }
            for name, info in REGIONS.items()
        }
    })


@tools_bp.route('/tools/frequency-slot/presets', methods=['GET'])
def get_presets():
    """Get all supported modem presets with their parameters."""
    return jsonify({
        'presets': {
            name: {
                'bandwidth_khz': info['bandwidth'],
                'spreading_factor': info['sf'],
                'coding_rate': info['cr']
            }
            for name, info in PRESETS.items()
        }
    })


# ============================================================================
# RF Calculations
# ============================================================================

@tools_bp.route('/tools/fspl', methods=['GET'])
def fspl():
    """
    Calculate Free Space Path Loss.

    GET /api/tools/fspl?distance_km=10&freq_mhz=915

    Returns:
        FSPL in dB
    """
    try:
        distance_km = float(request.args.get('distance_km', 1))
        freq_mhz = float(request.args.get('freq_mhz', 915))

        if distance_km <= 0 or freq_mhz <= 0:
            return jsonify({'error': 'Distance and frequency must be positive'}), 400

        # FSPL = 20*log10(d_km) + 20*log10(f_MHz) + 32.45
        fspl_db = 20 * math.log10(distance_km) + 20 * math.log10(freq_mhz) + 32.45

        return jsonify({
            'distance_km': distance_km,
            'frequency_mhz': freq_mhz,
            'fspl_db': round(fspl_db, 2)
        })
    except (ValueError, TypeError) as e:
        return jsonify({'error': str(e)}), 400


@tools_bp.route('/tools/link-budget', methods=['GET'])
def link_budget():
    """
    Calculate link budget and received signal strength.

    GET /api/tools/link-budget?tx_power=20&tx_gain=2&rx_gain=2&distance_km=10&freq_mhz=915

    Returns:
        Link budget analysis including FSPL and received power
    """
    try:
        tx_power = float(request.args.get('tx_power', 20))
        tx_gain = float(request.args.get('tx_gain', 0))
        rx_gain = float(request.args.get('rx_gain', 0))
        distance_km = float(request.args.get('distance_km', 1))
        freq_mhz = float(request.args.get('freq_mhz', 915))
        rx_sensitivity = float(request.args.get('rx_sensitivity', -130))

        if distance_km <= 0 or freq_mhz <= 0:
            return jsonify({'error': 'Distance and frequency must be positive'}), 400

        # FSPL calculation
        fspl_db = 20 * math.log10(distance_km) + 20 * math.log10(freq_mhz) + 32.45

        # Link budget: TX_power + TX_gain + RX_gain - FSPL
        eirp = tx_power + tx_gain
        rx_power = eirp + rx_gain - fspl_db
        link_margin = rx_power - rx_sensitivity

        return jsonify({
            'tx_power_dbm': tx_power,
            'tx_gain_dbi': tx_gain,
            'rx_gain_dbi': rx_gain,
            'distance_km': distance_km,
            'frequency_mhz': freq_mhz,
            'eirp_dbm': round(eirp, 2),
            'fspl_db': round(fspl_db, 2),
            'rx_power_dbm': round(rx_power, 2),
            'rx_sensitivity_dbm': rx_sensitivity,
            'link_margin_db': round(link_margin, 2),
            'link_viable': link_margin > 0
        })
    except (ValueError, TypeError) as e:
        return jsonify({'error': str(e)}), 400


@tools_bp.route('/tools/fresnel', methods=['GET'])
def fresnel():
    """
    Calculate Fresnel zone radius.

    GET /api/tools/fresnel?distance_km=10&freq_mhz=915&zone=1

    Returns:
        Fresnel zone radius at midpoint
    """
    try:
        distance_km = float(request.args.get('distance_km', 1))
        freq_mhz = float(request.args.get('freq_mhz', 915))
        zone = int(request.args.get('zone', 1))

        if distance_km <= 0 or freq_mhz <= 0 or zone < 1:
            return jsonify({'error': 'Invalid parameters'}), 400

        # Fresnel radius at midpoint: r = sqrt(n * wavelength * d1 * d2 / (d1 + d2))
        # At midpoint d1 = d2 = distance/2
        wavelength = 299.792458 / freq_mhz  # meters
        d_m = distance_km * 1000
        radius = math.sqrt(zone * wavelength * (d_m / 2) * (d_m / 2) / d_m)

        return jsonify({
            'distance_km': distance_km,
            'frequency_mhz': freq_mhz,
            'zone': zone,
            'wavelength_m': round(wavelength, 4),
            'radius_m': round(radius, 2),
            'clearance_60pct_m': round(radius * 0.6, 2)
        })
    except (ValueError, TypeError) as e:
        return jsonify({'error': str(e)}), 400
