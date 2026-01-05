#!/usr/bin/env python3
"""
Meshtasticd Manager - Web UI

Browser-based interface for managing meshtasticd.
Access via http://your-pi-ip:8080/

Usage:
    sudo python3 src/main_web.py              # Run on port 8080
    sudo python3 src/main_web.py --port 8888  # Custom port
    sudo python3 src/main_web.py --host 0.0.0.0  # Listen on all interfaces
"""

import os
import sys
import re
import json
import socket
import signal
import subprocess
import threading
import argparse
import secrets
import atexit
from pathlib import Path
from datetime import datetime
from functools import wraps

# Track running subprocesses for cleanup
_running_processes = []
_shutdown_flag = False

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from flask import Flask, render_template_string, jsonify, request, redirect, url_for, session
except ImportError:
    print("Flask not installed. Installing...")
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--break-system-packages', 'flask'],
                   capture_output=True)
    from flask import Flask, render_template_string, jsonify, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Configuration - SECURITY: Default to localhost only
# Use --host 0.0.0.0 explicitly to expose to network (requires --password)
CONFIG = {
    'auth_enabled': False,
    'password': None,  # Set via --password or environment
    'host': '127.0.0.1',  # SECURE DEFAULT: localhost only
    'port': 8080,
}


# Security headers middleware
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP - allow inline styles/scripts for single-file app, but restrict sources
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    return response

# CPU stats for delta calculation
_last_cpu = None

# PID file for tracking
WEB_PID_FILE = Path('/tmp/meshtasticd-web.pid')


def cleanup_processes():
    """Kill any lingering subprocesses"""
    global _shutdown_flag
    _shutdown_flag = True

    for proc in _running_processes[:]:
        try:
            if proc.poll() is None:  # Still running
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass
    _running_processes.clear()

    # Clean up PID file
    try:
        if WEB_PID_FILE.exists():
            WEB_PID_FILE.unlink()
    except Exception:
        pass


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print(f"\nReceived signal {signum}, shutting down...")
    cleanup_processes()
    sys.exit(0)


def run_subprocess(cmd, **kwargs):
    """Run a subprocess and track it for cleanup"""
    global _shutdown_flag
    if _shutdown_flag:
        return None

    # Set defaults for safety
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if kwargs.get('capture_output') else None,
            stderr=subprocess.PIPE if kwargs.get('capture_output') else None,
            text=kwargs.get('text', True)
        )
        _running_processes.append(proc)

        timeout = kwargs.get('timeout', 30)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            result = subprocess.CompletedProcess(
                cmd, proc.returncode, stdout or '', stderr or ''
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise
        finally:
            if proc in _running_processes:
                _running_processes.remove(proc)

        return result
    except Exception as e:
        # Clean up on error
        try:
            if proc in _running_processes:
                _running_processes.remove(proc)
        except (NameError, ValueError):
            pass
        raise


# Register cleanup handlers
atexit.register(cleanup_processes)
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ============================================================================
# Authentication
# ============================================================================

def login_required(f):
    """Decorator for routes that require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if CONFIG['auth_enabled'] and not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Use constant-time comparison to prevent timing attacks
        user_password = request.form.get('password', '')
        stored_password = CONFIG['password'] or ''
        # Both must be strings for compare_digest
        if secrets.compare_digest(user_password, stored_password):
            session['authenticated'] = True
            return redirect(url_for('index'))
        return render_template_string(LOGIN_TEMPLATE, error="Invalid password")
    return render_template_string(LOGIN_TEMPLATE, error=None)


@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))


# ============================================================================
# Utility Functions
# ============================================================================

def validate_config_name(config_name):
    """
    Validate config filename to prevent path traversal attacks.
    Returns (is_valid, error_message).
    Only allows alphanumeric, hyphen, underscore, and dot characters.
    Must end with .yaml or .yml extension.
    """
    if not config_name:
        return False, "Config name is required"

    # Block any path separators or parent directory references
    if '/' in config_name or '\\' in config_name or '..' in config_name:
        return False, "Invalid config name: path separators not allowed"

    # Only allow safe characters: alphanumeric, hyphen, underscore, dot
    import re
    if not re.match(r'^[a-zA-Z0-9_.-]+$', config_name):
        return False, "Invalid config name: only alphanumeric, hyphen, underscore, and dot allowed"

    # Must have valid extension
    if not (config_name.endswith('.yaml') or config_name.endswith('.yml')):
        return False, "Invalid config name: must end with .yaml or .yml"

    # Prevent hidden files
    if config_name.startswith('.'):
        return False, "Invalid config name: hidden files not allowed"

    return True, None


def find_meshtastic_cli():
    """Find meshtastic CLI path - uses centralized utils.cli"""
    try:
        from utils.cli import find_meshtastic_cli as _find_cli
        return _find_cli()
    except ImportError:
        # Fallback if utils not available
        import shutil
        return shutil.which('meshtastic')


def check_service_status():
    """Check meshtasticd service status using multiple methods"""
    is_running = False
    status_detail = "Stopped"

    # Method 1: systemctl
    try:
        result = subprocess.run(['systemctl', 'is-active', 'meshtasticd'],
                               capture_output=True, text=True)
        if result.stdout.strip() == 'active':
            is_running = True
            status_detail = "Running (systemd)"
    except Exception:
        pass

    # Method 2: pgrep
    if not is_running:
        try:
            result = subprocess.run(['pgrep', '-f', 'meshtasticd'],
                                   capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                is_running = True
                status_detail = "Running (process)"
        except Exception:
            pass

    # Method 3: TCP port
    if not is_running:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            if sock.connect_ex(('localhost', 4403)) == 0:
                is_running = True
                status_detail = "Running (TCP 4403)"
            sock.close()
        except Exception:
            pass

    return is_running, status_detail


def get_system_stats():
    """Get system statistics"""
    global _last_cpu
    stats = {}

    # CPU usage
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        cpu_vals = [int(x) for x in line.split()[1:8]]
        idle = cpu_vals[3]
        total = sum(cpu_vals)
        if _last_cpu:
            diff_idle = idle - _last_cpu[0]
            diff_total = total - _last_cpu[1]
            stats['cpu_percent'] = round(100 * (1 - diff_idle / diff_total), 1) if diff_total > 0 else 0
        else:
            stats['cpu_percent'] = 0
        _last_cpu = (idle, total)
    except Exception:
        stats['cpu_percent'] = 0

    # Memory usage
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
        mem_info = {}
        for line in lines:
            parts = line.split(':')
            if len(parts) == 2:
                key = parts[0].strip()
                val = int(parts[1].split()[0])
                mem_info[key] = val
        total = mem_info.get('MemTotal', 1)
        avail = mem_info.get('MemAvailable', mem_info.get('MemFree', 0))
        used = total - avail
        stats['mem_percent'] = round(100 * used / total, 1) if total > 0 else 0
        stats['mem_used_mb'] = round(used / 1024)
        stats['mem_total_mb'] = round(total / 1024)
    except Exception:
        stats['mem_percent'] = 0
        stats['mem_used_mb'] = 0
        stats['mem_total_mb'] = 0

    # Disk usage
    try:
        stat = os.statvfs('/')
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bfree * stat.f_frsize
        used = total - free
        stats['disk_percent'] = round(100 * used / total, 1) if total > 0 else 0
        stats['disk_used_gb'] = round(used / (1024**3), 1)
        stats['disk_total_gb'] = round(total / (1024**3), 1)
    except Exception:
        stats['disk_percent'] = 0
        stats['disk_used_gb'] = 0
        stats['disk_total_gb'] = 0

    # Temperature
    try:
        temp = None
        temp_file = Path('/sys/class/thermal/thermal_zone0/temp')
        if temp_file.exists():
            temp = int(temp_file.read_text().strip()) / 1000
        if temp is None:
            result = subprocess.run(['vcgencmd', 'measure_temp'],
                                   capture_output=True, text=True)
            if result.returncode == 0 and 'temp=' in result.stdout:
                temp = float(result.stdout.split('=')[1].replace("'C", "").strip())
        stats['temperature'] = round(temp, 1) if temp else None
    except Exception:
        stats['temperature'] = None

    # Uptime
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_sec = float(f.read().split()[0])
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        mins = int((uptime_sec % 3600) // 60)
        if days > 0:
            stats['uptime'] = f"{days}d {hours}h {mins}m"
        elif hours > 0:
            stats['uptime'] = f"{hours}h {mins}m"
        else:
            stats['uptime'] = f"{mins}m"
    except Exception:
        stats['uptime'] = "--"

    return stats


def get_service_logs(lines=50):
    """Get recent service logs"""
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'meshtasticd', '-n', str(lines), '--no-pager'],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout
    except Exception as e:
        return f"Error fetching logs: {e}"


# Radio info cache
_radio_cache = {'data': None, 'timestamp': 0}
_RADIO_CACHE_TTL = 30  # seconds


def get_radio_info(use_cache=True):
    """Get radio info from meshtastic CLI with caching"""
    import time

    # Return cached data if fresh
    if use_cache and _radio_cache['data']:
        age = time.time() - _radio_cache['timestamp']
        if age < _RADIO_CACHE_TTL:
            return _radio_cache['data']

    cli = find_meshtastic_cli()
    if not cli:
        return {'error': 'Meshtastic CLI not found. Install with: pipx install meshtastic'}

    # Check if port is reachable first (quick check)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        result = sock.connect_ex(('localhost', 4403))
        sock.close()
        if result != 0:
            return {'error': 'meshtasticd not running (port 4403 closed)'}
    except Exception:
        return {'error': 'Cannot check meshtasticd port 4403'}

    try:
        # Increased timeout to 30 seconds - CLI can be slow
        result = run_subprocess(
            [cli, '--host', 'localhost', '--info'],
            timeout=30
        )
        if result is None:  # Shutdown in progress
            return {'error': 'Server shutting down'}
        if result.returncode == 0:
            output = result.stdout
            info = {}

            # Parse JSON fields
            hw_match = re.search(r'"hwModel":\s*"([^"]+)"', output)
            if hw_match:
                info['hardware'] = hw_match.group(1)

            fw_match = re.search(r'"firmwareVersion":\s*"([^"]+)"', output)
            if fw_match:
                info['firmware'] = fw_match.group(1)

            region_match = re.search(r'"region":\s*"([^"]+)"', output)
            if region_match:
                info['region'] = region_match.group(1)

            name_match = re.search(r'"longName":\s*"([^"]+)"', output)
            if name_match:
                info['name'] = name_match.group(1)

            id_match = re.search(r'"id":\s*"([^"]+)"', output)
            if id_match:
                info['node_id'] = id_match.group(1)

            # Cache successful result
            _radio_cache['data'] = info
            _radio_cache['timestamp'] = time.time()

            return info if info else {'error': 'No radio info found in response'}

        # Check for common errors
        stderr = result.stderr or ''
        if 'Connection refused' in stderr:
            return {'error': 'meshtasticd refused connection'}
        if 'timed out' in stderr.lower():
            return {'error': 'Radio not responding (check connection)'}

        return {'error': result.stderr or 'Failed to get radio info'}

    except subprocess.TimeoutExpired:
        return {'error': 'Radio info timeout (30s) - radio may be busy or disconnected'}
    except Exception as e:
        return {'error': f'Error: {str(e)}'}


def get_configs():
    """Get available and active configurations"""
    configs = {'available': [], 'active': [], 'main_config': None}

    meshtasticd_dir = Path('/etc/meshtasticd')
    available_d = meshtasticd_dir / 'available.d'
    config_d = meshtasticd_dir / 'config.d'
    main_config = meshtasticd_dir / 'config.yaml'

    # Check main config.yaml
    if main_config.exists():
        try:
            size = main_config.stat().st_size
            configs['main_config'] = f"config.yaml ({size} bytes)"
        except Exception:
            configs['main_config'] = "config.yaml (exists)"

    # Check available.d
    if available_d.exists():
        for f in sorted(available_d.glob('*.yaml')) + sorted(available_d.glob('*.yml')):
            configs['available'].append(f.name)

    # Check config.d
    if config_d.exists():
        for f in sorted(config_d.glob('*.yaml')) + sorted(config_d.glob('*.yml')):
            configs['active'].append(f.name)

    # If no directories exist, note that
    if not meshtasticd_dir.exists():
        configs['error'] = 'meshtasticd not installed (/etc/meshtasticd missing)'

    return configs


def detect_hardware():
    """Detect hardware and service status"""
    detected = []

    is_running, status = check_service_status()
    if is_running:
        info = get_radio_info()
        if 'error' not in info:
            hw = info.get('hardware', 'Connected')
            fw = info.get('firmware', '')
            detected.append({
                'type': 'Active',
                'device': 'meshtasticd',
                'description': f"Running - {hw}" + (f" (v{fw})" if fw else "")
            })
        else:
            detected.append({
                'type': 'Active',
                'device': 'meshtasticd',
                'description': f"Running - {status}"
            })
    else:
        detected.append({
            'type': 'Info',
            'device': 'meshtasticd',
            'description': 'Service not running'
        })

    # Check SPI devices
    spi_devices = list(Path('/dev').glob('spidev*'))
    for dev in spi_devices:
        detected.append({
            'type': 'SPI',
            'device': dev.name,
            'description': 'SPI device available'
        })

    # Check I2C devices
    i2c_devices = list(Path('/dev').glob('i2c-*'))
    for dev in i2c_devices:
        detected.append({
            'type': 'I2C',
            'device': dev.name,
            'description': 'I2C bus available'
        })

    return detected


def get_nodes():
    """Get mesh nodes from meshtastic CLI"""
    cli = find_meshtastic_cli()
    if not cli:
        return {'error': 'Meshtastic CLI not found'}

    # Check if port is reachable
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        if sock.connect_ex(('localhost', 4403)) != 0:
            sock.close()
            return {'error': 'meshtasticd not running (port 4403)'}
        sock.close()
    except Exception:
        return {'error': 'Cannot connect to meshtasticd'}

    try:
        result = run_subprocess(
            [cli, '--host', 'localhost', '--nodes'],
            timeout=30
        )
        if result is None:
            return {'error': 'Server shutting down'}
        if result.returncode == 0:
            output = result.stdout
            nodes = []

            # Parse node entries - look for node info patterns
            # Format: !abcd1234: User Name (SHORT)
            import re
            node_pattern = re.compile(
                r'(!?[a-fA-F0-9]{8}):\s*([^\(]+)\s*\(([^\)]+)\)'
            )

            for match in node_pattern.finditer(output):
                node_id, name, short_name = match.groups()
                nodes.append({
                    'id': node_id.strip(),
                    'name': name.strip(),
                    'short': short_name.strip()
                })

            # Also try to parse the table format if present
            # Look for lines with | separators
            lines = output.strip().split('\n')
            for line in lines:
                if '│' in line and '!' in line:
                    parts = [p.strip() for p in line.split('│')]
                    if len(parts) >= 4:
                        # Try to extract node info from table row
                        for part in parts:
                            if part.startswith('!'):
                                node_id = part
                                break
                        else:
                            continue

                        # Get other fields
                        node_data = {
                            'id': node_id,
                            'name': parts[1] if len(parts) > 1 else '',
                            'short': parts[2] if len(parts) > 2 else ''
                        }
                        # Avoid duplicates
                        if not any(n['id'] == node_id for n in nodes):
                            nodes.append(node_data)

            return {'nodes': nodes, 'raw': output}

        return {'error': result.stderr or 'Failed to get nodes', 'raw': result.stdout}

    except subprocess.TimeoutExpired:
        return {'error': 'Timeout getting nodes (30s)'}
    except Exception as e:
        return {'error': str(e)}


# Node monitor for full node data with positions
_node_monitor = None
_node_monitor_lock = threading.Lock()


def get_nodes_full():
    """Get detailed node info including positions using NodeMonitor"""
    global _node_monitor

    # Check if port is reachable first
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        if sock.connect_ex(('localhost', 4403)) != 0:
            sock.close()
            return {'error': 'meshtasticd not running (port 4403)'}
        sock.close()
    except Exception:
        return {'error': 'Cannot connect to meshtasticd'}

    try:
        with _node_monitor_lock:
            # Import NodeMonitor
            try:
                from monitoring.node_monitor import NodeMonitor
            except ImportError:
                try:
                    from src.monitoring.node_monitor import NodeMonitor
                except ImportError:
                    return {'error': 'NodeMonitor not available'}

            # Create or reuse monitor
            if _node_monitor is None or not _node_monitor.is_connected:
                if _node_monitor:
                    try:
                        _node_monitor.disconnect()
                    except Exception:
                        pass
                _node_monitor = NodeMonitor(host='localhost', port=4403)
                if not _node_monitor.connect(timeout=10.0):
                    return {'error': 'Failed to connect to meshtasticd'}

            # Get nodes
            nodes = []
            my_node = _node_monitor.get_my_node()

            for node in _node_monitor.get_nodes():
                node_data = {
                    'id': node.node_id,
                    'name': node.long_name or node.short_name or node.node_id,
                    'short': node.short_name,
                    'hardware': node.hardware_model,
                    'role': node.role,
                    'snr': node.snr,
                    'hops': node.hops_away,
                    'via_mqtt': node.via_mqtt,
                    'is_me': node.node_id == _node_monitor.my_node_id,
                }

                # Position
                if node.position and (node.position.latitude or node.position.longitude):
                    node_data['position'] = {
                        'latitude': node.position.latitude,
                        'longitude': node.position.longitude,
                        'altitude': node.position.altitude,
                    }

                # Metrics
                if node.metrics:
                    node_data['battery'] = node.metrics.battery_level
                    node_data['voltage'] = node.metrics.voltage
                    if node.metrics.temperature:
                        node_data['temperature'] = node.metrics.temperature
                    if node.metrics.humidity:
                        node_data['humidity'] = node.metrics.humidity

                # Last heard
                if node.last_heard:
                    node_data['last_heard'] = node.last_heard.isoformat()
                    # Calculate how long ago
                    delta = datetime.now() - node.last_heard
                    if delta.total_seconds() < 60:
                        node_data['last_heard_ago'] = f"{int(delta.total_seconds())}s ago"
                    elif delta.total_seconds() < 3600:
                        node_data['last_heard_ago'] = f"{int(delta.total_seconds() / 60)}m ago"
                    elif delta.total_seconds() < 86400:
                        node_data['last_heard_ago'] = f"{int(delta.total_seconds() / 3600)}h ago"
                    else:
                        node_data['last_heard_ago'] = f"{int(delta.total_seconds() / 86400)}d ago"

                nodes.append(node_data)

            # Count nodes with positions
            nodes_with_position = sum(1 for n in nodes if 'position' in n)

            return {
                'nodes': nodes,
                'my_node_id': _node_monitor.my_node_id,
                'total_nodes': len(nodes),
                'nodes_with_position': nodes_with_position,
            }

    except Exception as e:
        return {'error': f'Error getting nodes: {str(e)}'}


def send_mesh_message(text, destination=None):
    """Send a message to the mesh"""
    cli = find_meshtastic_cli()
    if not cli:
        return {'error': 'Meshtastic CLI not found'}

    if not text or not text.strip():
        return {'error': 'Message cannot be empty'}

    # Validate message length (Meshtastic limit ~230 bytes)
    # UTF-8 can use multiple bytes per char, so check both
    text = text.strip()
    if len(text) > 230:
        return {'error': 'Message too long (max 230 characters)'}
    if len(text.encode('utf-8')) > 230:
        return {'error': 'Message too long (max 230 bytes, unicode chars count as more)'}

    # Validate destination if provided (Meshtastic node IDs are hex with optional ! prefix)
    if destination:
        destination = str(destination).strip()
        # Node IDs: !abc123def or abc123def (hex)
        if not re.match(r'^!?[0-9a-fA-F]{1,16}$', destination):
            return {'error': 'Invalid destination: must be hex node ID (e.g., !abc123 or abc123)'}

    # Check if port is reachable
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        if sock.connect_ex(('localhost', 4403)) != 0:
            sock.close()
            return {'error': 'meshtasticd not running'}
        sock.close()
    except Exception:
        return {'error': 'Cannot connect to meshtasticd'}

    try:
        cmd = [cli, '--host', 'localhost', '--sendtext', text]
        if destination:
            cmd.extend(['--dest', destination])

        result = run_subprocess(cmd, timeout=30)

        if result is None:
            return {'error': 'Server shutting down'}
        if result.returncode == 0:
            return {'success': True, 'message': 'Message sent'}
        return {'error': result.stderr or 'Failed to send message'}

    except subprocess.TimeoutExpired:
        return {'error': 'Timeout sending message'}
    except Exception as e:
        return {'error': str(e)}


# ============================================================================
# API Routes
# ============================================================================

@app.route('/api/status')
@login_required
def api_status():
    """Get overall status"""
    is_running, status_detail = check_service_status()
    stats = get_system_stats()

    return jsonify({
        'service': {
            'running': is_running,
            'status': status_detail
        },
        'system': stats
    })


@app.route('/api/logs')
@login_required
def api_logs():
    """Get service logs"""
    lines = request.args.get('lines', 50, type=int)
    return jsonify({'logs': get_service_logs(lines)})


@app.route('/api/radio')
@login_required
def api_radio():
    """Get radio info (cached by default, use ?refresh=1 to force)"""
    force_refresh = request.args.get('refresh', '0') == '1'
    return jsonify(get_radio_info(use_cache=not force_refresh))


@app.route('/api/configs')
@login_required
def api_configs():
    """Get configurations"""
    return jsonify(get_configs())


@app.route('/api/hardware')
@login_required
def api_hardware():
    """Get hardware detection"""
    return jsonify({'devices': detect_hardware()})


@app.route('/api/service/<action>', methods=['POST'])
@login_required
def api_service_action(action):
    """Control service"""
    if action not in ['start', 'stop', 'restart']:
        return jsonify({'error': 'Invalid action'}), 400

    try:
        result = subprocess.run(
            ['systemctl', action, 'meshtasticd'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return jsonify({'success': True, 'message': f'Service {action}ed'})
        return jsonify({'success': False, 'error': result.stderr})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/config/activate', methods=['POST'])
@login_required
def api_activate_config():
    """Activate a configuration"""
    data = request.get_json()
    config_name = data.get('config')

    # Validate config name to prevent path traversal
    is_valid, error = validate_config_name(config_name)
    if not is_valid:
        return jsonify({'error': error}), 400

    src = Path('/etc/meshtasticd/available.d') / config_name
    dst = Path('/etc/meshtasticd/config.d') / config_name

    # Additional safety: verify resolved paths are within expected directories
    if not str(src.resolve()).startswith('/etc/meshtasticd/available.d/'):
        return jsonify({'error': 'Invalid config path'}), 400
    if not str(dst.resolve()).startswith('/etc/meshtasticd/config.d/'):
        return jsonify({'error': 'Invalid config path'}), 400

    if not src.exists():
        return jsonify({'error': 'Config not found'}), 404

    try:
        import shutil
        shutil.copy2(src, dst)
        return jsonify({'success': True, 'message': f'{config_name} activated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config/deactivate', methods=['POST'])
@login_required
def api_deactivate_config():
    """Deactivate a configuration"""
    data = request.get_json()
    config_name = data.get('config')

    # Validate config name to prevent path traversal
    is_valid, error = validate_config_name(config_name)
    if not is_valid:
        return jsonify({'error': error}), 400

    config_path = Path('/etc/meshtasticd/config.d') / config_name

    # Additional safety: verify resolved path is within expected directory
    if not str(config_path.resolve()).startswith('/etc/meshtasticd/config.d/'):
        return jsonify({'error': 'Invalid config path'}), 400

    if not config_path.exists():
        return jsonify({'error': 'Config not active'}), 404

    try:
        config_path.unlink()
        return jsonify({'success': True, 'message': f'{config_name} deactivated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config/content/<config_name>')
@login_required
def api_config_content(config_name):
    """Get config file content"""
    # Validate config name to prevent path traversal
    is_valid, error = validate_config_name(config_name)
    if not is_valid:
        return jsonify({'error': error}), 400

    # Check both directories
    for base in ['/etc/meshtasticd/config.d', '/etc/meshtasticd/available.d']:
        path = Path(base) / config_name
        # Additional safety: verify resolved path is within expected directory
        resolved = str(path.resolve())
        if not (resolved.startswith('/etc/meshtasticd/config.d/') or
                resolved.startswith('/etc/meshtasticd/available.d/')):
            return jsonify({'error': 'Invalid config path'}), 400
        if path.exists():
            try:
                return jsonify({'content': path.read_text()})
            except Exception as e:
                return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'Config not found'}), 404


@app.route('/api/versions')
@login_required
def api_versions():
    """Get component versions and update availability"""
    try:
        try:
            from updates.version_checker import get_version_summary
        except ImportError:
            try:
                from src.updates.version_checker import get_version_summary
            except ImportError:
                return jsonify({'error': 'Version checker not available'})

        return jsonify(get_version_summary())
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/processes')
@login_required
def api_processes():
    """Get top processes"""
    try:
        result = subprocess.run(
            ['ps', 'aux', '--sort=-%cpu'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')[:11]
            return jsonify({'processes': lines})
        return jsonify({'error': result.stderr})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/nodes')
@login_required
def api_nodes():
    """Get mesh nodes"""
    return jsonify(get_nodes())


@app.route('/api/nodes/full')
@login_required
def api_nodes_full():
    """Get detailed mesh nodes with positions for map display"""
    return jsonify(get_nodes_full())


@app.route('/api/nodes/geojson')
@login_required
def api_nodes_geojson():
    """Get mesh nodes as GeoJSON for map display"""
    data = get_nodes_full()

    if 'error' in data:
        return jsonify({"type": "FeatureCollection", "features": [], "error": data['error']})

    features = []
    now = datetime.now()

    for node in data.get('nodes', []):
        # Skip nodes without position
        if 'position' not in node:
            continue

        pos = node['position']
        lat = pos.get('latitude')
        lon = pos.get('longitude')

        # Skip invalid coordinates
        if lat is None or lon is None or (lat == 0 and lon == 0):
            continue

        # Determine if online (heard in last 15 minutes)
        is_online = False
        last_seen = node.get('last_heard_ago', 'Unknown')
        if 's ago' in last_seen or 'm ago' in last_seen:
            is_online = True
        elif 'h ago' in last_seen:
            try:
                hours = int(last_seen.replace('h ago', ''))
                is_online = hours < 1
            except ValueError:
                pass

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]  # GeoJSON is [lon, lat]
            },
            "properties": {
                "id": node.get('id', ''),
                "name": node.get('name', 'Unknown'),
                "short": node.get('short', ''),
                "network": "meshtastic",
                "is_online": is_online,
                "is_local": node.get('is_me', False),
                "is_gateway": node.get('role', '').upper() in ['ROUTER', 'REPEATER', 'ROUTER_CLIENT'],
                "snr": node.get('snr'),
                "battery": node.get('battery'),
                "last_seen": last_seen,
                "hardware": node.get('hardware', ''),
                "altitude": pos.get('altitude'),
                "hops": node.get('hops'),
            }
        }
        features.append(feature)

    return jsonify({
        "type": "FeatureCollection",
        "features": features,
        "total_nodes": data.get('total_nodes', 0),
        "nodes_with_position": len(features),
        "my_node_id": data.get('my_node_id', '')
    })


@app.route('/api/message', methods=['POST'])
@login_required
def api_send_message():
    """Send a mesh message"""
    data = request.get_json()
    text = data.get('text', '')
    destination = data.get('destination')
    return jsonify(send_mesh_message(text, destination))


@app.route('/api/config/edit', methods=['POST'])
@login_required
def api_edit_config():
    """Edit a config file"""
    data = request.get_json()
    config_name = data.get('config')
    content = data.get('content')

    # Validate config name to prevent path traversal
    is_valid, error = validate_config_name(config_name)
    if not is_valid:
        return jsonify({'error': error}), 400

    if content is None:
        return jsonify({'error': 'No content provided'}), 400

    # Only allow editing in config.d or available.d
    for base in ['/etc/meshtasticd/config.d', '/etc/meshtasticd/available.d']:
        path = Path(base) / config_name
        # Additional safety: verify resolved path is within expected directory
        resolved = str(path.resolve())
        if not (resolved.startswith('/etc/meshtasticd/config.d/') or
                resolved.startswith('/etc/meshtasticd/available.d/')):
            return jsonify({'error': 'Invalid config path'}), 400
        if path.exists():
            try:
                # Create backup
                backup_path = path.with_suffix(path.suffix + '.bak')
                if path.exists():
                    import shutil
                    shutil.copy2(path, backup_path)

                # Write new content
                path.write_text(content)
                return jsonify({'success': True, 'message': f'{config_name} saved'})
            except Exception as e:
                return jsonify({'error': str(e)}), 500

    return jsonify({'error': 'Config not found'}), 404


def validate_journalctl_since(since_value):
    """Validate journalctl --since parameter to prevent injection.

    Allows: ISO dates, relative times like '1 hour ago', 'today', 'yesterday'
    """
    import re
    if not since_value:
        return True, None

    # Limit length
    if len(since_value) > 50:
        return False, "Time value too long"

    # Whitelist safe patterns:
    # - ISO dates: 2024-01-01, 2024-01-01 12:00:00
    # - Relative: "1 hour ago", "30 minutes ago", "2 days ago"
    # - Named: today, yesterday, now
    safe_patterns = [
        r'^[\d]{4}-[\d]{2}-[\d]{2}(\s[\d]{2}:[\d]{2}(:[\d]{2})?)?$',  # ISO date/datetime
        r'^\d+\s+(second|minute|hour|day|week|month|year)s?\s+ago$',  # Relative time
        r'^(today|yesterday|now|tomorrow)$',  # Named times
    ]

    for pattern in safe_patterns:
        if re.match(pattern, since_value.lower().strip()):
            return True, None

    return False, "Invalid time format. Use ISO date (2024-01-01) or relative (1 hour ago)"


@app.route('/api/logs/stream')
@login_required
def api_logs_stream():
    """Stream service logs (returns last N lines, call repeatedly for updates)"""
    lines = request.args.get('lines', 100, type=int)
    since = request.args.get('since', '')

    # Validate since parameter
    if since:
        is_valid, error = validate_journalctl_since(since)
        if not is_valid:
            return jsonify({'error': error}), 400

    try:
        # Limit lines to reasonable range
        lines = max(1, min(lines, 1000))
        cmd = ['journalctl', '-u', 'meshtasticd', '-n', str(lines), '--no-pager']
        if since:
            cmd.extend(['--since', since])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return jsonify({
            'logs': result.stdout,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)})


# ============================================================================
# HTML Templates
# ============================================================================

LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Meshtasticd Manager - Login</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-box {
            background: #fff;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.3);
            width: 100%;
            max-width: 400px;
        }
        h1 { color: #333; margin-bottom: 30px; text-align: center; }
        input[type="password"] {
            width: 100%;
            padding: 15px;
            border: 2px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
            margin-bottom: 20px;
        }
        button {
            width: 100%;
            padding: 15px;
            background: #4CAF50;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
        }
        button:hover { background: #45a049; }
        .error { color: #f44336; margin-bottom: 20px; text-align: center; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>Meshtasticd Manager</h1>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="POST">
            <input type="password" name="password" placeholder="Password" autofocus>
            <button type="submit">Login</button>
        </form>
    </div>
</body>
</html>
'''

MAIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>MeshForge</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <!-- Leaflet.js for maps -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        :root {
            --bg-dark: #1a1a2e;
            --bg-card: #16213e;
            --text: #eee;
            --text-muted: #888;
            --accent: #4CAF50;
            --accent-hover: #45a049;
            --danger: #f44336;
            --warning: #ff9800;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-dark);
            color: var(--text);
            min-height: 100vh;
        }
        .header {
            background: var(--bg-card);
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #333;
        }
        .header h1 { font-size: 1.5rem; }
        .header .version { color: var(--text-muted); font-size: 0.9rem; }
        .nav {
            display: flex;
            gap: 10px;
        }
        .nav a, .nav button {
            padding: 8px 16px;
            background: transparent;
            color: var(--text);
            border: 1px solid #444;
            border-radius: 5px;
            text-decoration: none;
            cursor: pointer;
            font-size: 14px;
        }
        .nav a:hover, .nav button:hover { background: #333; }
        .nav a.active { background: var(--accent); border-color: var(--accent); }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }

        .card {
            background: var(--bg-card);
            border-radius: 10px;
            padding: 20px;
            border: 1px solid #333;
        }
        .card h2 {
            font-size: 1.1rem;
            margin-bottom: 15px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .card .value {
            font-size: 2rem;
            font-weight: bold;
        }
        .card .value.success { color: var(--accent); }
        .card .value.error { color: var(--danger); }
        .card .value.warning { color: var(--warning); }

        .progress-bar {
            background: #333;
            border-radius: 5px;
            height: 8px;
            margin-top: 10px;
            overflow: hidden;
        }
        .progress-bar .fill {
            height: 100%;
            background: var(--accent);
            transition: width 0.3s;
        }
        .progress-bar .fill.warning { background: var(--warning); }
        .progress-bar .fill.danger { background: var(--danger); }

        .stat-row {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #333;
        }
        .stat-row:last-child { border-bottom: none; }
        .stat-label { color: var(--text-muted); }

        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            margin: 5px;
        }
        .btn-success { background: var(--accent); color: white; }
        .btn-danger { background: var(--danger); color: white; }
        .btn-warning { background: var(--warning); color: black; }
        .btn:hover { opacity: 0.9; }

        .log-box {
            background: #111;
            border-radius: 5px;
            padding: 15px;
            font-family: monospace;
            font-size: 12px;
            max-height: 300px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
        }

        .config-list {
            list-style: none;
        }
        .config-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px;
            background: #222;
            margin: 5px 0;
            border-radius: 5px;
        }
        .config-item .name { font-family: monospace; }

        .hardware-item {
            display: flex;
            align-items: center;
            padding: 10px;
            background: #222;
            margin: 5px 0;
            border-radius: 5px;
        }
        .hardware-item .badge {
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 11px;
            margin-right: 10px;
            text-transform: uppercase;
        }
        .hardware-item .badge.active { background: var(--accent); }
        .hardware-item .badge.spi { background: #2196F3; }
        .hardware-item .badge.i2c { background: #9C27B0; }
        .hardware-item .badge.info { background: #607D8B; }

        .tabs {
            display: flex;
            border-bottom: 2px solid #333;
            margin-bottom: 20px;
        }
        .tab {
            padding: 15px 25px;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            margin-bottom: -2px;
            color: var(--text-muted);
        }
        .tab:hover { color: var(--text); }
        .tab.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .radio-info {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
        }
        .radio-info .item {
            padding: 10px;
            background: #222;
            border-radius: 5px;
        }
        .radio-info .label {
            color: var(--text-muted);
            font-size: 12px;
            margin-bottom: 5px;
        }

        @media (max-width: 768px) {
            .grid { grid-template-columns: 1fr; }
            .header { flex-direction: column; gap: 10px; }
            .nav { flex-wrap: wrap; justify-content: center; }
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>Meshtasticd Manager</h1>
            <span class="version">v{{ version }} | Web UI</span>
        </div>
        <div class="nav">
            {% if auth_enabled %}
            <a href="/logout">Logout</a>
            {% endif %}
        </div>
    </div>

    <div class="container">
        <div class="tabs">
            <div class="tab active" data-tab="dashboard">Dashboard</div>
            <div class="tab" data-tab="map">Map</div>
            <div class="tab" data-tab="nodes">Nodes</div>
            <div class="tab" data-tab="messages">Messages</div>
            <div class="tab" data-tab="service">Service</div>
            <div class="tab" data-tab="config">Config</div>
            <div class="tab" data-tab="hardware">Hardware</div>
            <div class="tab" data-tab="radio">Radio</div>
            <div class="tab" data-tab="updates">Updates</div>
            <div class="tab" data-tab="system">System</div>
        </div>

        <!-- Dashboard Tab -->
        <div id="dashboard" class="tab-content active">
            <div class="grid">
                <div class="card">
                    <h2>Service Status</h2>
                    <div class="value" id="service-status">Loading...</div>
                </div>
                <div class="card">
                    <h2>CPU Usage</h2>
                    <div class="value" id="cpu-value">--</div>
                    <div class="progress-bar"><div class="fill" id="cpu-bar" style="width: 0%"></div></div>
                </div>
                <div class="card">
                    <h2>Memory</h2>
                    <div class="value" id="mem-value">--</div>
                    <div class="progress-bar"><div class="fill" id="mem-bar" style="width: 0%"></div></div>
                </div>
                <div class="card">
                    <h2>Disk Usage</h2>
                    <div class="value" id="disk-value">--</div>
                    <div class="progress-bar"><div class="fill" id="disk-bar" style="width: 0%"></div></div>
                </div>
                <div class="card">
                    <h2>Temperature</h2>
                    <div class="value" id="temp-value">--</div>
                    <div class="progress-bar"><div class="fill" id="temp-bar" style="width: 0%"></div></div>
                </div>
                <div class="card">
                    <h2>Uptime</h2>
                    <div class="value" id="uptime-value">--</div>
                </div>
            </div>

            <div class="card">
                <h2>Recent Logs</h2>
                <div class="log-box" id="logs">Loading...</div>
            </div>
        </div>

        <!-- Map Tab -->
        <div id="map" class="tab-content">
            <div class="card" style="padding: 10px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                    <h2 style="margin: 0;">Mesh Network Map</h2>
                    <div>
                        <span id="map-node-count" style="color: var(--text-muted); margin-right: 15px;">--</span>
                        <button class="btn btn-success" onclick="refreshMap()">Refresh Map</button>
                    </div>
                </div>
                <div id="mesh-map" style="height: 500px; border-radius: 8px; background: #1a1a2e;"></div>
                <div id="map-legend" style="margin-top: 10px; padding: 10px; background: #222; border-radius: 5px;">
                    <span style="margin-right: 20px;"><span style="display: inline-block; width: 12px; height: 12px; background: #4CAF50; border-radius: 50%; margin-right: 5px;"></span> My Node</span>
                    <span style="margin-right: 20px;"><span style="display: inline-block; width: 12px; height: 12px; background: #2196F3; border-radius: 50%; margin-right: 5px;"></span> Online (< 1h)</span>
                    <span style="margin-right: 20px;"><span style="display: inline-block; width: 12px; height: 12px; background: #ff9800; border-radius: 50%; margin-right: 5px;"></span> Stale (1-24h)</span>
                    <span><span style="display: inline-block; width: 12px; height: 12px; background: #607D8B; border-radius: 50%; margin-right: 5px;"></span> Offline (> 24h)</span>
                </div>
            </div>
            <div class="card" style="margin-top: 20px;">
                <h2>Nodes with Position</h2>
                <div id="map-node-list" style="max-height: 300px; overflow-y: auto;">Loading...</div>
            </div>
        </div>

        <!-- Nodes Tab -->
        <div id="nodes" class="tab-content">
            <div class="card">
                <h2>Mesh Nodes</h2>
                <button class="btn btn-success" onclick="refreshNodes()" style="margin-bottom: 15px;">Refresh Nodes</button>
                <div id="nodes-list">Loading...</div>
                <div id="nodes-raw" style="margin-top: 15px; display: none;">
                    <h3>Raw Output</h3>
                    <div class="log-box" id="nodes-raw-content" style="max-height: 200px;"></div>
                </div>
            </div>
        </div>

        <!-- Messages Tab -->
        <div id="messages" class="tab-content">
            <div class="card">
                <h2>Send Message</h2>
                <div style="margin-bottom: 15px;">
                    <label style="display: block; margin-bottom: 5px; color: var(--text-muted);">Message Text:</label>
                    <textarea id="message-text" rows="3" style="width: 100%; padding: 10px; background: #222; border: 1px solid #444; border-radius: 5px; color: var(--text); font-family: inherit;" placeholder="Type your message here..."></textarea>
                </div>
                <div style="margin-bottom: 15px;">
                    <label style="display: block; margin-bottom: 5px; color: var(--text-muted);">Destination (optional, leave blank for broadcast):</label>
                    <input type="text" id="message-dest" style="width: 100%; padding: 10px; background: #222; border: 1px solid #444; border-radius: 5px; color: var(--text);" placeholder="!abcd1234 or leave blank">
                </div>
                <button class="btn btn-success" onclick="sendMessage()">Send Message</button>
                <div id="message-status" style="margin-top: 15px;"></div>
            </div>
            <div class="card" style="margin-top: 20px;">
                <h2>Recent Messages</h2>
                <p style="color: var(--text-muted);">Message history coming soon. Check the logs in the Service tab for now.</p>
            </div>
        </div>

        <!-- Service Tab -->
        <div id="service" class="tab-content">
            <div class="card">
                <h2>Service Control</h2>
                <p style="margin-bottom: 20px; color: var(--text-muted);">
                    Control the meshtasticd service
                </p>
                <button class="btn btn-success" onclick="serviceAction('start')">Start</button>
                <button class="btn btn-danger" onclick="serviceAction('stop')">Stop</button>
                <button class="btn btn-warning" onclick="serviceAction('restart')">Restart</button>
            </div>

            <div class="card" style="margin-top: 20px;">
                <h2>Service Logs</h2>
                <button class="btn btn-success" onclick="refreshLogs()" style="margin-bottom: 15px;">Refresh Logs</button>
                <div class="log-box" id="service-logs" style="max-height: 500px;">Loading...</div>
            </div>
        </div>

        <!-- Config Tab -->
        <div id="config" class="tab-content">
            <div class="grid">
                <div class="card">
                    <h2>Active Configurations</h2>
                    <ul class="config-list" id="active-configs">Loading...</ul>
                </div>
                <div class="card">
                    <h2>Available Configurations</h2>
                    <ul class="config-list" id="available-configs">Loading...</ul>
                </div>
            </div>
        </div>

        <!-- Hardware Tab -->
        <div id="hardware" class="tab-content">
            <div class="card">
                <h2>Detected Hardware</h2>
                <button class="btn btn-success" onclick="refreshHardware()" style="margin-bottom: 15px;">Refresh</button>
                <div id="hardware-list">Loading...</div>
            </div>
        </div>

        <!-- Radio Tab -->
        <div id="radio" class="tab-content">
            <div class="card">
                <h2>Connected Radio</h2>
                <button class="btn btn-success" onclick="refreshRadio(true)" style="margin-bottom: 15px;">Refresh</button>
                <div class="radio-info" id="radio-info">Loading...</div>
            </div>
        </div>

        <!-- Updates Tab -->
        <div id="updates" class="tab-content">
            <div class="card">
                <h2>Component Versions</h2>
                <p style="color: var(--text-muted); margin-bottom: 15px;">
                    Check for updates to meshtasticd, CLI, and node firmware.
                </p>
                <button class="btn btn-success" onclick="refreshVersions()" style="margin-bottom: 15px;">Check for Updates</button>
                <div id="version-list">Loading...</div>
                <div id="version-status" style="margin-top: 15px; color: var(--text-muted);"></div>
            </div>
            <div class="card" style="margin-top: 20px;">
                <h2>Update Instructions</h2>
                <div style="color: var(--text-muted);">
                    <h3 style="color: var(--text); margin: 15px 0 10px 0;">meshtasticd</h3>
                    <pre style="background: #111; padding: 10px; border-radius: 5px; overflow-x: auto;">sudo apt update && sudo apt upgrade meshtasticd</pre>

                    <h3 style="color: var(--text); margin: 15px 0 10px 0;">Meshtastic CLI</h3>
                    <pre style="background: #111; padding: 10px; border-radius: 5px; overflow-x: auto;">pipx upgrade meshtastic</pre>

                    <h3 style="color: var(--text); margin: 15px 0 10px 0;">Node Firmware</h3>
                    <p>Use the <a href="https://flasher.meshtastic.org/" target="_blank" style="color: var(--accent);">Meshtastic Web Flasher</a> or the meshtastic-flasher tool.</p>
                </div>
            </div>
        </div>

        <!-- System Tab -->
        <div id="system" class="tab-content">
            <div class="card">
                <h2>Top Processes</h2>
                <button class="btn btn-success" onclick="refreshProcesses()" style="margin-bottom: 15px;">Refresh</button>
                <div class="log-box" id="processes">Loading...</div>
            </div>
        </div>
    </div>

    <script>
        // Security: HTML escape function to prevent XSS
        function escapeHtml(text) {
            if (text === null || text === undefined) return '';
            const div = document.createElement('div');
            div.textContent = String(text);
            return div.innerHTML;
        }

        // Tab switching with persistence
        function switchTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            const tab = document.querySelector(`.tab[data-tab="${tabName}"]`);
            if (tab) {
                tab.classList.add('active');
                document.getElementById(tabName).classList.add('active');
                localStorage.setItem('activeTab', tabName);
            }
        }

        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => switchTab(tab.dataset.tab));
        });

        // Restore last active tab
        const savedTab = localStorage.getItem('activeTab');
        if (savedTab && document.getElementById(savedTab)) {
            switchTab(savedTab);
        }

        // API calls
        async function fetchStatus() {
            try {
                const resp = await fetch('/api/status');
                const data = await resp.json();

                // Service status
                const statusEl = document.getElementById('service-status');
                statusEl.textContent = data.service.status;
                statusEl.className = 'value ' + (data.service.running ? 'success' : 'error');

                // CPU
                document.getElementById('cpu-value').textContent = data.system.cpu_percent + '%';
                document.getElementById('cpu-bar').style.width = data.system.cpu_percent + '%';

                // Memory
                document.getElementById('mem-value').textContent =
                    data.system.mem_used_mb + '/' + data.system.mem_total_mb + ' MB';
                document.getElementById('mem-bar').style.width = data.system.mem_percent + '%';

                // Disk
                document.getElementById('disk-value').textContent =
                    data.system.disk_used_gb + '/' + data.system.disk_total_gb + ' GB';
                document.getElementById('disk-bar').style.width = data.system.disk_percent + '%';

                // Temperature
                if (data.system.temperature) {
                    document.getElementById('temp-value').textContent = data.system.temperature + '°C';
                    const tempPct = Math.min(data.system.temperature / 85 * 100, 100);
                    document.getElementById('temp-bar').style.width = tempPct + '%';
                }

                // Uptime
                document.getElementById('uptime-value').textContent = data.system.uptime;
            } catch (e) {
                console.error('Error fetching status:', e);
            }
        }

        async function fetchLogs() {
            try {
                const resp = await fetch('/api/logs?lines=30');
                const data = await resp.json();
                document.getElementById('logs').textContent = data.logs;
                document.getElementById('service-logs').textContent = data.logs;
            } catch (e) {
                console.error('Error fetching logs:', e);
            }
        }

        async function refreshLogs() {
            const resp = await fetch('/api/logs?lines=100');
            const data = await resp.json();
            document.getElementById('service-logs').textContent = data.logs;
        }

        async function fetchConfigs() {
            try {
                const resp = await fetch('/api/configs');
                const data = await resp.json();

                const activeEl = document.getElementById('active-configs');
                let activeHtml = '';

                // Show main config if exists
                if (data.main_config) {
                    activeHtml += `<li class="config-item"><span class="name" style="color: var(--accent);">📄 ${data.main_config}</span></li>`;
                }

                // Show error if any
                if (data.error) {
                    activeHtml += `<li class="config-item" style="color: var(--warning);">${data.error}</li>`;
                }

                // Show active configs from config.d
                if (data.active.length > 0) {
                    activeHtml += data.active.map(c => `
                        <li class="config-item">
                            <span class="name">${c}</span>
                            <button class="btn btn-danger" onclick="deactivateConfig('${c}')">Deactivate</button>
                        </li>
                    `).join('');
                } else if (!data.main_config && !data.error) {
                    activeHtml += '<li class="config-item">No configurations in config.d/</li>';
                }

                activeEl.innerHTML = activeHtml || '<li class="config-item">No configurations found</li>';

                const availEl = document.getElementById('available-configs');
                if (data.available.length === 0) {
                    availEl.innerHTML = '<li class="config-item">No configurations in available.d/</li>';
                } else {
                    availEl.innerHTML = data.available.map(c => `
                        <li class="config-item">
                            <span class="name">${escapeHtml(c)}</span>
                            <button class="btn btn-success" onclick="activateConfig('${escapeHtml(c).replace(/'/g, "\\'")}')">Activate</button>
                        </li>
                    `).join('');
                }
            } catch (e) {
                console.error('Error fetching configs:', e);
            }
        }

        async function refreshHardware() {
            try {
                const resp = await fetch('/api/hardware');
                const data = await resp.json();

                const el = document.getElementById('hardware-list');
                el.innerHTML = data.devices.map(d => `
                    <div class="hardware-item">
                        <span class="badge ${escapeHtml(d.type).toLowerCase()}">${escapeHtml(d.type)}</span>
                        <span><strong>${escapeHtml(d.device)}</strong> - ${escapeHtml(d.description)}</span>
                    </div>
                `).join('');
            } catch (e) {
                console.error('Error fetching hardware:', e);
            }
        }

        async function refreshRadio(forceRefresh = false) {
            const el = document.getElementById('radio-info');
            const btn = document.querySelector('[onclick*="refreshRadio"]');

            // Show loading state
            el.innerHTML = '<div class="item" style="grid-column: span 2;"><em>Loading radio info... (may take up to 30s)</em></div>';
            if (btn) btn.disabled = true;

            try {
                const url = forceRefresh ? '/api/radio?refresh=1' : '/api/radio';
                const resp = await fetch(url);
                const data = await resp.json();

                if (data.error) {
                    el.innerHTML = `<div class="item" style="grid-column: span 2; color: var(--warning);">${escapeHtml(data.error)}</div>`;
                } else {
                    el.innerHTML = Object.entries(data).map(([k, v]) => `
                        <div class="item">
                            <div class="label">${escapeHtml(k.replace('_', ' ').toUpperCase())}</div>
                            <div>${escapeHtml(v)}</div>
                        </div>
                    `).join('');
                }
            } catch (e) {
                console.error('Error fetching radio:', e);
                el.innerHTML = '<div class="item" style="grid-column: span 2; color: var(--danger);">Network error fetching radio info</div>';
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        async function refreshProcesses() {
            try {
                const resp = await fetch('/api/processes');
                const data = await resp.json();
                document.getElementById('processes').textContent = data.processes.join('\\n');
            } catch (e) {
                console.error('Error fetching processes:', e);
            }
        }

        async function refreshVersions() {
            const listEl = document.getElementById('version-list');
            const statusEl = document.getElementById('version-status');

            listEl.innerHTML = '<em>Checking versions... (this may take a moment)</em>';
            statusEl.textContent = '';

            try {
                const resp = await fetch('/api/versions');
                const data = await resp.json();

                if (data.error) {
                    listEl.innerHTML = `<div style="color: var(--warning);">${escapeHtml(data.error)}</div>`;
                    return;
                }

                let html = '<table style="width: 100%; border-collapse: collapse;">';
                html += `
                    <tr style="border-bottom: 1px solid #444;">
                        <th style="padding: 10px; text-align: left;">Component</th>
                        <th style="padding: 10px; text-align: left;">Installed</th>
                        <th style="padding: 10px; text-align: left;">Latest</th>
                        <th style="padding: 10px; text-align: left;">Status</th>
                    </tr>
                `;

                data.components.forEach(c => {
                    let statusBadge = '';
                    if (c.installed === 'Not installed') {
                        statusBadge = '<span style="color: var(--text-muted);">Not installed</span>';
                    } else if (c.update_available) {
                        statusBadge = '<span style="background: var(--warning); color: black; padding: 2px 8px; border-radius: 3px; font-size: 12px;">Update Available</span>';
                    } else if (c.latest !== 'Unknown') {
                        statusBadge = '<span style="color: var(--accent);">Up to date</span>';
                    } else {
                        statusBadge = '<span style="color: var(--text-muted);">--</span>';
                    }

                    html += `
                        <tr style="border-bottom: 1px solid #333;">
                            <td style="padding: 10px;"><strong>${escapeHtml(c.name)}</strong></td>
                            <td style="padding: 10px; font-family: monospace;">${escapeHtml(c.installed)}</td>
                            <td style="padding: 10px; font-family: monospace;">${escapeHtml(c.latest)}</td>
                            <td style="padding: 10px;">${statusBadge}</td>
                        </tr>
                    `;
                });

                html += '</table>';
                listEl.innerHTML = html;

                // Status message
                if (data.updates_available > 0) {
                    statusEl.innerHTML = `<span style="color: var(--warning);">${data.updates_available} update(s) available</span>`;
                } else {
                    statusEl.innerHTML = '<span style="color: var(--accent);">All components up to date</span>';
                }

                statusEl.innerHTML += ` <span style="color: var(--text-muted);">| Last checked: ${new Date(data.checked_at).toLocaleTimeString()}</span>`;

            } catch (e) {
                console.error('Error fetching versions:', e);
                listEl.innerHTML = `<div style="color: var(--danger);">Error: ${escapeHtml(e.message)}</div>`;
            }
        }

        async function serviceAction(action) {
            if (!confirm(`Are you sure you want to ${action} the service?`)) return;

            try {
                const resp = await fetch(`/api/service/${action}`, { method: 'POST' });
                const data = await resp.json();
                alert(data.success ? data.message : data.error);
                fetchStatus();
            } catch (e) {
                alert('Error: ' + e);
            }
        }

        async function activateConfig(name) {
            try {
                const resp = await fetch('/api/config/activate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ config: name })
                });
                const data = await resp.json();
                alert(data.success ? data.message : data.error);
                fetchConfigs();
            } catch (e) {
                alert('Error: ' + e);
            }
        }

        async function deactivateConfig(name) {
            if (!confirm(`Deactivate ${name}?`)) return;
            try {
                const resp = await fetch('/api/config/deactivate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ config: name })
                });
                const data = await resp.json();
                alert(data.success ? data.message : data.error);
                fetchConfigs();
            } catch (e) {
                alert('Error: ' + e);
            }
        }

        async function refreshNodes() {
            const el = document.getElementById('nodes-list');
            const rawEl = document.getElementById('nodes-raw');
            const rawContent = document.getElementById('nodes-raw-content');

            el.innerHTML = '<em>Loading nodes... (may take up to 30s)</em>';

            try {
                const resp = await fetch('/api/nodes');
                const data = await resp.json();

                if (data.error) {
                    el.innerHTML = `<div style="color: var(--warning);">${data.error}</div>`;
                } else if (data.nodes && data.nodes.length > 0) {
                    el.innerHTML = `
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr style="border-bottom: 1px solid #444;">
                                <th style="padding: 10px; text-align: left;">Node ID</th>
                                <th style="padding: 10px; text-align: left;">Name</th>
                                <th style="padding: 10px; text-align: left;">Short</th>
                            </tr>
                            ${data.nodes.map(n => `
                                <tr style="border-bottom: 1px solid #333;">
                                    <td style="padding: 10px; font-family: monospace;">${n.id}</td>
                                    <td style="padding: 10px;">${n.name}</td>
                                    <td style="padding: 10px;">${n.short}</td>
                                </tr>
                            `).join('')}
                        </table>
                        <p style="margin-top: 10px; color: var(--text-muted);">${data.nodes.length} node(s) found</p>
                    `;
                } else {
                    el.innerHTML = '<div style="color: var(--text-muted);">No nodes found</div>';
                }

                // Show raw output
                if (data.raw) {
                    rawEl.style.display = 'block';
                    rawContent.textContent = data.raw;
                }
            } catch (e) {
                console.error('Error fetching nodes:', e);
                el.innerHTML = '<div style="color: var(--danger);">Network error fetching nodes</div>';
            }
        }

        async function sendMessage() {
            const text = document.getElementById('message-text').value;
            const dest = document.getElementById('message-dest').value;
            const statusEl = document.getElementById('message-status');

            if (!text.trim()) {
                statusEl.innerHTML = '<span style="color: var(--warning);">Please enter a message</span>';
                return;
            }

            statusEl.innerHTML = '<em>Sending...</em>';

            try {
                const resp = await fetch('/api/message', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text, destination: dest || null })
                });
                const data = await resp.json();

                if (data.success) {
                    statusEl.innerHTML = '<span style="color: var(--accent);">Message sent successfully!</span>';
                    document.getElementById('message-text').value = '';
                } else {
                    statusEl.innerHTML = `<span style="color: var(--danger);">${data.error}</span>`;
                }
            } catch (e) {
                console.error('Error sending message:', e);
                statusEl.innerHTML = '<span style="color: var(--danger);">Network error sending message</span>';
            }
        }

        // Initial load
        fetchStatus();
        fetchLogs();
        fetchConfigs();
        refreshHardware();
        refreshRadio();
        refreshNodes();
        refreshProcesses();

        // Auto-refresh status every 5 seconds
        setInterval(fetchStatus, 5000);

        // ========================================
        // Map functionality
        // ========================================
        let meshMap = null;
        let mapMarkers = [];
        let mapInitialized = false;

        function initMap() {
            if (mapInitialized) return;

            const mapContainer = document.getElementById('mesh-map');
            if (!mapContainer) return;

            // Initialize map centered on a default location
            meshMap = L.map('mesh-map').setView([20, 0], 2);

            // Add OpenStreetMap tiles with dark theme
            L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/">CARTO</a>',
                subdomains: 'abcd',
                maxZoom: 19
            }).addTo(meshMap);

            mapInitialized = true;
            refreshMap();
        }

        function getNodeColor(node) {
            if (node.is_me) return '#4CAF50';  // Green for my node

            if (!node.last_heard) return '#607D8B';  // Gray for unknown

            const lastHeard = new Date(node.last_heard);
            const now = new Date();
            const hoursSince = (now - lastHeard) / (1000 * 60 * 60);

            if (hoursSince < 1) return '#2196F3';   // Blue for online
            if (hoursSince < 24) return '#ff9800';  // Orange for stale
            return '#607D8B';                        // Gray for offline
        }

        function createNodeIcon(color, isMe) {
            const size = isMe ? 16 : 12;
            return L.divIcon({
                className: 'custom-marker',
                html: `<div style="
                    width: ${size}px;
                    height: ${size}px;
                    background: ${color};
                    border: 2px solid white;
                    border-radius: 50%;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.5);
                "></div>`,
                iconSize: [size, size],
                iconAnchor: [size/2, size/2],
                popupAnchor: [0, -size/2]
            });
        }

        async function refreshMap() {
            const countEl = document.getElementById('map-node-count');
            const listEl = document.getElementById('map-node-list');

            countEl.textContent = 'Loading...';

            try {
                const resp = await fetch('/api/nodes/full');
                const data = await resp.json();

                if (data.error) {
                    countEl.textContent = data.error;
                    listEl.innerHTML = `<div style="color: var(--warning);">${data.error}</div>`;
                    return;
                }

                // Clear existing markers
                mapMarkers.forEach(m => meshMap.removeLayer(m));
                mapMarkers = [];

                // Filter nodes with positions
                const nodesWithPos = data.nodes.filter(n => n.position && n.position.latitude && n.position.longitude);

                countEl.textContent = `${nodesWithPos.length} of ${data.total_nodes} nodes with position`;

                if (nodesWithPos.length === 0) {
                    listEl.innerHTML = '<div style="color: var(--text-muted);">No nodes with GPS position found. Nodes need to have GPS or fixed position set.</div>';
                    return;
                }

                // Add markers
                const bounds = [];
                nodesWithPos.forEach(node => {
                    const lat = node.position.latitude;
                    const lng = node.position.longitude;
                    const color = getNodeColor(node);
                    const icon = createNodeIcon(color, node.is_me);

                    const marker = L.marker([lat, lng], { icon: icon }).addTo(meshMap);

                    // Build popup content
                    let popupContent = `
                        <div style="min-width: 200px; font-family: sans-serif;">
                            <h3 style="margin: 0 0 8px 0; color: #333;">${node.name}</h3>
                            <table style="font-size: 12px; color: #666;">
                                <tr><td style="padding-right: 10px;"><strong>ID:</strong></td><td><code>${node.id}</code></td></tr>
                                <tr><td><strong>Short:</strong></td><td>${node.short || '--'}</td></tr>
                                <tr><td><strong>Hardware:</strong></td><td>${node.hardware || '--'}</td></tr>
                    `;

                    if (node.battery) {
                        popupContent += `<tr><td><strong>Battery:</strong></td><td>${node.battery}%</td></tr>`;
                    }
                    if (node.last_heard_ago) {
                        popupContent += `<tr><td><strong>Last Heard:</strong></td><td>${node.last_heard_ago}</td></tr>`;
                    }
                    if (node.snr !== null && node.snr !== undefined) {
                        popupContent += `<tr><td><strong>SNR:</strong></td><td>${node.snr} dB</td></tr>`;
                    }
                    if (node.hops !== null && node.hops !== undefined) {
                        popupContent += `<tr><td><strong>Hops:</strong></td><td>${node.hops}</td></tr>`;
                    }
                    if (node.position.altitude) {
                        popupContent += `<tr><td><strong>Altitude:</strong></td><td>${node.position.altitude}m</td></tr>`;
                    }

                    popupContent += `
                                <tr><td><strong>Position:</strong></td><td>${lat.toFixed(5)}, ${lng.toFixed(5)}</td></tr>
                            </table>
                        </div>
                    `;

                    marker.bindPopup(popupContent);
                    mapMarkers.push(marker);
                    bounds.push([lat, lng]);
                });

                // Fit map to bounds if we have markers
                if (bounds.length > 0) {
                    if (bounds.length === 1) {
                        meshMap.setView(bounds[0], 14);
                    } else {
                        meshMap.fitBounds(bounds, { padding: [50, 50] });
                    }
                }

                // Update node list
                listEl.innerHTML = nodesWithPos.map(n => `
                    <div class="hardware-item" style="cursor: pointer;" onclick="focusNode(${n.position.latitude}, ${n.position.longitude})">
                        <span class="badge" style="background: ${getNodeColor(n)};">${n.is_me ? 'ME' : n.short || '??'}</span>
                        <span>
                            <strong>${n.name}</strong>
                            <span style="color: var(--text-muted); margin-left: 10px;">
                                ${n.position.latitude.toFixed(4)}, ${n.position.longitude.toFixed(4)}
                                ${n.last_heard_ago ? ' - ' + n.last_heard_ago : ''}
                            </span>
                        </span>
                    </div>
                `).join('');

            } catch (e) {
                console.error('Error refreshing map:', e);
                countEl.textContent = 'Error loading map data';
                listEl.innerHTML = `<div style="color: var(--danger);">Error: ${e.message}</div>`;
            }
        }

        function focusNode(lat, lng) {
            if (meshMap) {
                meshMap.setView([lat, lng], 16);
            }
        }

        // Initialize map when Map tab is clicked
        document.querySelector('.tab[data-tab="map"]').addEventListener('click', () => {
            setTimeout(() => {
                initMap();
                if (meshMap) {
                    meshMap.invalidateSize();
                }
            }, 100);
        });
    </script>
</body>
</html>
'''


# ============================================================================
# Main Routes
# ============================================================================

@app.route('/favicon.ico')
def favicon():
    """Return empty favicon to avoid 404"""
    return '', 204


@app.route('/')
@login_required
def index():
    from __version__ import __version__
    return render_template_string(
        MAIN_TEMPLATE,
        version=__version__,
        auth_enabled=CONFIG['auth_enabled']
    )


# ============================================================================
# Main Entry Point
# ============================================================================

def get_web_pid():
    """Get running web UI PID if exists"""
    if WEB_PID_FILE.exists():
        try:
            pid = int(WEB_PID_FILE.read_text().strip())
            os.kill(pid, 0)  # Check if running
            return pid
        except (ValueError, ProcessLookupError):
            try:
                WEB_PID_FILE.unlink(missing_ok=True)
            except PermissionError:
                pass
        except PermissionError:
            return pid  # Can't signal, but exists
    return None


def stop_web_ui():
    """Stop running web UI"""
    pid = get_web_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to Web UI (PID: {pid})")
            import time
            time.sleep(1)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
                print("Process killed with SIGKILL")
            except ProcessLookupError:
                pass
            try:
                WEB_PID_FILE.unlink(missing_ok=True)
            except PermissionError:
                pass
            print("Web UI stopped")
            return True
        except ProcessLookupError:
            print("Process already stopped")
            try:
                WEB_PID_FILE.unlink(missing_ok=True)
            except PermissionError:
                pass
            return True
        except PermissionError:
            print("Permission denied. Try with sudo")
            return False
    else:
        print("Web UI is not running")
        return True


def main():
    # Get defaults from environment variables
    default_port = int(os.environ.get('MESHTASTICD_WEB_PORT', 8880))
    # SECURITY: Default to localhost, require explicit --host 0.0.0.0 for network access
    default_host = os.environ.get('MESHTASTICD_WEB_HOST', '127.0.0.1')

    parser = argparse.ArgumentParser(
        description='Meshtasticd Manager - Web UI',
        epilog='''
Examples:
  sudo python3 src/main_web.py                              # Localhost only (secure)
  sudo python3 src/main_web.py --host 0.0.0.0 -P secret     # Network access with auth
  sudo python3 src/main_web.py --port 9000                  # Custom port
  sudo python3 src/main_web.py --stop                       # Stop running instance

SECURITY NOTE:
  By default, binds to localhost (127.0.0.1) only.
  To expose to network, use --host 0.0.0.0 WITH --password.

Environment variables:
  MESHTASTICD_WEB_PORT=9000      # Set default port
  MESHTASTICD_WEB_PASSWORD=xxx   # Enable authentication (required for network access)
  MESHTASTICD_WEB_HOST=0.0.0.0   # Set bind address
'''
    )
    parser.add_argument('--host', default=default_host,
                        help=f'Host to bind to (default: {default_host}, env: MESHTASTICD_WEB_HOST)')
    parser.add_argument('--port', '-p', type=int, default=default_port,
                        help=f'Port to listen on (default: {default_port}, env: MESHTASTICD_WEB_PORT)')
    parser.add_argument('--password', '-P',
                        help='Enable authentication with this password (env: MESHTASTICD_WEB_PASSWORD)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode')
    parser.add_argument('--stop', action='store_true',
                        help='Stop running web UI instance')
    parser.add_argument('--status', action='store_true',
                        help='Check if web UI is running')
    args = parser.parse_args()

    # Handle --stop
    if args.stop:
        sys.exit(0 if stop_web_ui() else 1)

    # Handle --status
    if args.status:
        pid = get_web_pid()
        if pid:
            print(f"Web UI is running (PID: {pid})")
            sys.exit(0)
        else:
            print("Web UI is not running")
            sys.exit(1)

    # Check if already running
    existing_pid = get_web_pid()
    if existing_pid:
        print(f"Web UI already running (PID: {existing_pid})")
        print("Stop it first with: sudo python3 src/main_web.py --stop")
        sys.exit(1)

    # Check root
    if os.geteuid() != 0:
        print("=" * 60)
        print("WARNING: Not running as root")
        print("=" * 60)
        print("Some features (service control) require root privileges.")
        print("Run with: sudo python3 src/main_web.py")
        print()

    # Configure authentication
    if args.password:
        CONFIG['auth_enabled'] = True
        CONFIG['password'] = args.password
        print("Authentication enabled")
    elif os.environ.get('MESHTASTICD_WEB_PASSWORD'):
        CONFIG['auth_enabled'] = True
        CONFIG['password'] = os.environ.get('MESHTASTICD_WEB_PASSWORD')
        print("Authentication enabled (from environment)")

    # SECURITY: Warn if exposing to network without authentication
    if args.host in ('0.0.0.0', '::') and not CONFIG['auth_enabled']:
        print()
        print("=" * 70)
        print("⚠️  SECURITY WARNING: Network exposure without authentication!")
        print("=" * 70)
        print("You are binding to all interfaces without a password.")
        print("Anyone on your network can access and control meshtasticd.")
        print()
        print("Recommended: Add authentication with --password <secret>")
        print("  Example: sudo python3 src/main_web.py --host 0.0.0.0 -P mysecret")
        print()
        print("Or use localhost only (default):")
        print("  Example: sudo python3 src/main_web.py")
        print("=" * 70)
        print()
        # Give user 5 seconds to cancel
        print("Starting in 5 seconds... (Ctrl+C to cancel)")
        import time
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(1)

    # Get local IP for display
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = '127.0.0.1'

    print("=" * 60)
    print("Meshtasticd Manager - Web UI")
    print("=" * 60)
    print()
    print(f"Access the web interface at:")
    print(f"  http://localhost:{args.port}/")
    print(f"  http://{local_ip}:{args.port}/")
    print()
    if CONFIG['auth_enabled']:
        print("Authentication: ENABLED")
    else:
        print("Authentication: DISABLED (use --password to enable)")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 60)

    # Write PID file
    try:
        WEB_PID_FILE.write_text(str(os.getpid()))
    except Exception as e:
        print(f"Warning: Could not write PID file: {e}")

    try:
        app.run(
            host=args.host,
            port=args.port,
            debug=args.debug,
            threaded=True,
            use_reloader=False  # Prevent duplicate processes
        )
    finally:
        # Clean up on exit
        cleanup_processes()


if __name__ == '__main__':
    main()
