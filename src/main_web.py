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
                   capture_output=True, timeout=120)
    from flask import Flask, render_template_string, jsonify, request, redirect, url_for, session

# Import centralized service checker
try:
    from utils.service_check import check_service as _check_service, check_port
except ImportError:
    _check_service = None
    check_port = None

# Import meshtastic connection manager for resilient TCP handling
try:
    from utils.meshtastic_connection import get_connection_manager, MeshtasticConnectionManager
    _meshtastic_mgr = None
except ImportError:
    get_connection_manager = None
    MeshtasticConnectionManager = None
    _meshtastic_mgr = None

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Register modular blueprints (new architecture)
try:
    from web.blueprints import register_blueprints
    register_blueprints(app)
except ImportError:
    pass  # Blueprints not yet installed, use legacy routes

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
    # CSP - allow Leaflet maps and external resources needed for the app
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: https://*.basemaps.cartocdn.com https://*.tile.openstreetmap.org; "
        "connect-src 'self' https://*.basemaps.cartocdn.com https://*.tile.openstreetmap.org; "
        "font-src 'self' data:"
    )
    return response

# CPU stats for delta calculation
_last_cpu = None

# PID file for tracking
WEB_PID_FILE = Path('/tmp/meshtasticd-web.pid')


def cleanup_processes():
    """Kill any lingering subprocesses and close connections gracefully"""
    global _shutdown_flag, _meshtastic_mgr, _node_monitor
    _shutdown_flag = True

    # Close meshtastic connection manager gracefully
    try:
        if _meshtastic_mgr is not None:
            _meshtastic_mgr.close()
            _meshtastic_mgr = None
    except Exception:
        pass

    # Close node monitor gracefully
    try:
        if _node_monitor is not None:
            _node_monitor.disconnect()
            _node_monitor = None
    except Exception:
        pass

    # Terminate subprocesses
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

    print("MeshForge Web UI shutdown complete.")


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print(f"\nReceived signal {signum}, shutting down...")
    cleanup_processes()
    sys.exit(0)


def check_port_available(host: str, port: int) -> tuple:
    """
    Check if a port is available for binding.

    Returns:
        (is_available, process_info) - process_info is populated if port is in use
    """
    # First check if we can bind to the port
    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test_socket.bind((host, port))
        test_socket.close()
        return (True, None)
    except OSError as e:
        test_socket.close()
        # Port is in use - try to identify what's using it
        process_info = None
        try:
            # Try lsof to identify the process
            result = subprocess.run(
                ['lsof', '-i', f':{port}', '-t'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                # Get process name for first PID
                pid = pids[0]
                ps_result = subprocess.run(
                    ['ps', '-p', pid, '-o', 'comm='],
                    capture_output=True, text=True, timeout=5
                )
                if ps_result.returncode == 0:
                    proc_name = ps_result.stdout.strip()
                    process_info = f"{proc_name} (PID: {pid})"
        except Exception:
            pass

        return (False, process_info)


def find_available_port(host: str, preferred_port: int, max_tries: int = 10) -> int:
    """
    Find an available port, starting with the preferred port.

    Returns:
        Available port number, or 0 if none found
    """
    for offset in range(max_tries):
        port = preferred_port + offset
        is_available, _ = check_port_available(host, port)
        if is_available:
            return port
    return 0


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

# Only register signal handlers in main thread (avoids error when imported from blueprints)
try:
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
except (ValueError, RuntimeError):
    pass  # Signal handlers already set or not in main thread


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
    """Check meshtasticd service status using centralized checker"""
    # Use centralized service checker if available
    if _check_service:
        status = _check_service('meshtasticd')
        return status.available, status.message

    # Fallback to manual checks if centralized checker not available
    is_running = False
    status_detail = "Stopped"

    # Method 1: systemctl
    try:
        result = subprocess.run(['systemctl', 'is-active', 'meshtasticd'],
                               capture_output=True, text=True, timeout=5)
        if result.stdout.strip() == 'active':
            is_running = True
            status_detail = "Running (systemd)"
    except Exception:
        pass

    # Method 2: pgrep
    if not is_running:
        try:
            result = subprocess.run(['pgrep', '-f', 'meshtasticd'],
                                   capture_output=True, text=True, timeout=5)
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
                                   capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and 'temp=' in result.stdout:
                temp_parts = result.stdout.split('=')
                if len(temp_parts) >= 2:
                    temp_str = temp_parts[1].replace("'C", "").strip()
                    try:
                        temp = float(temp_str)
                    except ValueError:
                        temp = None
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

    # Check meshtasticd service status with error handling
    try:
        is_running, status = check_service_status()
        if is_running:
            try:
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
            except Exception as e:
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
    except Exception as e:
        detected.append({
            'type': 'Warning',
            'device': 'meshtasticd',
            'description': f'Status check failed: {str(e)}'
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
    """Get mesh nodes - tries connection manager first, falls back to CLI"""
    global _meshtastic_mgr

    # Try using the connection manager first (more resilient)
    if get_connection_manager is not None:
        try:
            if _meshtastic_mgr is None:
                _meshtastic_mgr = get_connection_manager()

            # Check availability first
            if not _meshtastic_mgr.is_available():
                return {'error': 'meshtasticd not running (port 4403)', 'nodes': []}

            nodes = _meshtastic_mgr.get_nodes()
            if nodes:
                return {'nodes': nodes}
            # Fall through to CLI if connection manager returns empty
        except Exception as e:
            # Log and fall through to CLI method
            pass

    # Fallback to CLI method
    cli = find_meshtastic_cli()
    if not cli:
        return {'error': 'Meshtastic CLI not found', 'nodes': []}

    # Check if port is reachable
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        if sock.connect_ex(('localhost', 4403)) != 0:
            sock.close()
            return {'error': 'meshtasticd not running (port 4403)', 'nodes': []}
        sock.close()
    except Exception:
        return {'error': 'Cannot connect to meshtasticd', 'nodes': []}

    try:
        result = run_subprocess(
            [cli, '--host', 'localhost', '--nodes'],
            timeout=30
        )
        if result is None:
            return {'error': 'Server shutting down', 'nodes': []}
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

        return {'error': result.stderr or 'Failed to get nodes', 'nodes': [], 'raw': result.stdout}

    except subprocess.TimeoutExpired:
        return {'error': 'Timeout getting nodes (30s)', 'nodes': []}
    except Exception as e:
        return {'error': str(e), 'nodes': []}


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

            # Try to add RNS nodes from gateway
            rns_nodes_added = 0
            try:
                from gateway.node_tracker import UnifiedNodeTracker
                # Check if there's a running tracker instance we can query
                # This is a singleton-ish pattern - try to get cached instance
                tracker_file = '/tmp/meshforge_rns_nodes.json'
                import json
                import os
                if os.path.exists(tracker_file):
                    with open(tracker_file) as f:
                        rns_data = json.load(f)
                        for rnode in rns_data.get('nodes', []):
                            # Only add if not already in list (by matching name or RNS hash)
                            existing_ids = {n.get('id') for n in nodes}
                            if rnode.get('rns_hash') and rnode.get('rns_hash') not in existing_ids:
                                node_data = {
                                    'id': rnode.get('rns_hash', '')[:16],
                                    'name': rnode.get('name', 'RNS Node'),
                                    'short': rnode.get('short_name', 'RNS'),
                                    'hardware': 'RNS',
                                    'network': 'rns',
                                    'is_me': False,
                                }
                                if rnode.get('position'):
                                    pos = rnode['position']
                                    if pos.get('latitude') and pos.get('longitude'):
                                        node_data['position'] = {
                                            'latitude': pos['latitude'],
                                            'longitude': pos['longitude'],
                                            'altitude': pos.get('altitude', 0),
                                        }
                                if rnode.get('last_seen'):
                                    node_data['last_heard'] = rnode['last_seen']
                                nodes.append(node_data)
                                rns_nodes_added += 1
            except Exception as e:
                logger.debug(f"Could not load RNS nodes: {e}")

            # Mark meshtastic nodes with network type
            for node in nodes:
                if 'network' not in node:
                    node['network'] = 'meshtastic'

            # Count nodes with positions
            nodes_with_position = sum(1 for n in nodes if 'position' in n)

            return {
                'nodes': nodes,
                'my_node_id': _node_monitor.my_node_id,
                'total_nodes': len(nodes),
                'nodes_with_position': nodes_with_position,
                'rns_nodes': rns_nodes_added,
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
# Network Diagnostics API
# ============================================================================

def parse_proc_net(protocol: str) -> list:
    """Parse /proc/net/udp or /proc/net/tcp"""
    results = []
    proc_file = f"/proc/net/{protocol}"
    try:
        with open(proc_file, 'r') as f:
            lines = f.readlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 10:
                local_addr = parts[1]
                state = parts[3]
                inode = parts[9]
                addr_parts = local_addr.split(':')
                hex_ip = addr_parts[0]
                hex_port = addr_parts[1]
                try:
                    ip_int = int(hex_ip, 16)
                    ip_bytes = [(ip_int >> i) & 0xFF for i in (0, 8, 16, 24)]
                    ip_str = '.'.join(str(b) for b in ip_bytes)
                    port = int(hex_port, 16)
                    state_names = {
                        '01': 'ESTABLISHED', '0A': 'LISTEN', '06': 'TIME_WAIT',
                        '08': 'CLOSE_WAIT', '07': 'CLOSE'
                    }
                    results.append({
                        'ip': ip_str, 'port': port,
                        'state': state_names.get(state.upper(), state),
                        'inode': inode
                    })
                except (ValueError, IndexError):
                    continue
    except (FileNotFoundError, PermissionError):
        pass
    return results


def parse_proc_net_v6(protocol: str) -> list:
    """Parse /proc/net/udp6 or /proc/net/tcp6 for IPv6"""
    results = []
    proc_file = f"/proc/net/{protocol}"
    try:
        with open(proc_file, 'r') as f:
            lines = f.readlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 10:
                local_addr = parts[1]
                state = parts[3]
                inode = parts[9]
                addr_parts = local_addr.split(':')
                hex_ip = addr_parts[0]
                hex_port = addr_parts[1]
                try:
                    port = int(hex_port, 16)
                    ip_str = hex_ip if len(hex_ip) != 32 else ':'.join(
                        hex_ip[i:i+4] for i in range(0, 32, 4)
                    ).lower()
                    results.append({
                        'ip': ip_str, 'port': port,
                        'state': state, 'inode': inode
                    })
                except (ValueError, IndexError):
                    continue
    except (FileNotFoundError, PermissionError):
        pass
    return results


@app.route('/api/network/udp')
@login_required
def api_network_udp():
    """Get UDP listeners (IPv4 and IPv6)"""
    return jsonify({
        'ipv4': [e for e in parse_proc_net('udp') if e['port'] != 0],
        'ipv6': [e for e in parse_proc_net_v6('udp6') if e['port'] != 0]
    })


@app.route('/api/network/tcp')
@login_required
def api_network_tcp():
    """Get TCP listeners (IPv4 and IPv6)"""
    return jsonify({
        'ipv4': parse_proc_net('tcp'),
        'ipv6': parse_proc_net_v6('tcp6')
    })


@app.route('/api/network/rns-ports')
@login_required
def api_network_rns_ports():
    """Check RNS AutoInterface port 29716"""
    rns_port = 29716
    udp_v4 = parse_proc_net('udp')
    udp_v6 = parse_proc_net_v6('udp6')

    in_use_v4 = [e for e in udp_v4 if e['port'] == rns_port]
    in_use_v6 = [e for e in udp_v6 if e['port'] == rns_port]

    # Check RNS processes
    try:
        result = subprocess.run(
            ['pgrep', '-a', '-f', 'rnsd|nomadnet|lxmf'],
            capture_output=True, text=True, timeout=5
        )
        processes = result.stdout.strip().split('\n') if result.stdout.strip() else []
    except Exception:
        processes = []

    return jsonify({
        'port': rns_port,
        'in_use_v4': in_use_v4,
        'in_use_v6': in_use_v6,
        'free': len(in_use_v4) == 0 and len(in_use_v6) == 0,
        'processes': processes
    })


@app.route('/api/network/meshtastic-ports')
@login_required
def api_network_meshtastic_ports():
    """Check meshtasticd ports 4403, 9443"""
    tcp_ports = [4403, 9443]
    tcp_entries = parse_proc_net('tcp')

    results = {}
    for port in tcp_ports:
        listening = [e for e in tcp_entries if e['port'] == port and e['state'] == 'LISTEN']
        results[str(port)] = {
            'listening': len(listening) > 0,
            'entries': listening
        }

    # Check meshtasticd process
    try:
        result = subprocess.run(
            ['pgrep', '-a', '-f', 'meshtasticd'],
            capture_output=True, text=True, timeout=5
        )
        process = result.stdout.strip() if result.stdout.strip() else None
    except Exception:
        process = None

    return jsonify({
        'ports': results,
        'process': process
    })


@app.route('/api/network/multicast')
@login_required
def api_network_multicast():
    """Get multicast group memberships"""
    groups = []

    # Parse /proc/net/igmp
    try:
        with open('/proc/net/igmp', 'r') as f:
            lines = f.readlines()
        current_device = None
        for line in lines[1:]:
            if line[0].isdigit():
                parts = line.split()
                if len(parts) >= 2:
                    current_device = parts[1].rstrip(':')
            elif line.strip() and current_device:
                parts = line.split()
                if parts:
                    try:
                        group_int = int(parts[0], 16)
                        group_bytes = [(group_int >> i) & 0xFF for i in (0, 8, 16, 24)]
                        group_ip = '.'.join(str(b) for b in group_bytes)
                        groups.append({'device': current_device, 'group': group_ip})
                    except ValueError:
                        pass
    except (FileNotFoundError, PermissionError):
        pass

    return jsonify({'groups': groups})


@app.route('/api/network/process-ports')
@login_required
def api_network_process_ports():
    """Get process-to-port mapping using ss"""
    try:
        result = subprocess.run(
            ['ss', '-tulnp'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return jsonify({'output': result.stdout, 'tool': 'ss'})
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # Fallback to netstat
    try:
        result = subprocess.run(
            ['netstat', '-tulnp'],
            capture_output=True, text=True, timeout=10
        )
        return jsonify({'output': result.stdout, 'tool': 'netstat'})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/network/full-diagnostics')
@login_required
def api_network_full_diagnostics():
    """Run full network diagnostics"""
    return jsonify({
        'udp': {
            'ipv4': [e for e in parse_proc_net('udp') if e['port'] != 0],
            'ipv6': [e for e in parse_proc_net_v6('udp6') if e['port'] != 0]
        },
        'tcp': {
            'ipv4': [e for e in parse_proc_net('tcp') if e['state'] == 'LISTEN'],
            'ipv6': [e for e in parse_proc_net_v6('tcp6')]
        },
        'rns_port_29716_free': len([e for e in parse_proc_net('udp') if e['port'] == 29716]) == 0,
        'meshtastic_4403_listening': len([e for e in parse_proc_net('tcp') if e['port'] == 4403 and e['state'] == 'LISTEN']) > 0,
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/network/kill-clients', methods=['POST'])
@login_required
def api_network_kill_clients():
    """Kill competing RNS/Meshtastic clients"""
    killed = []
    for pattern in ['nomadnet', 'python.*meshtastic', 'lxmf']:
        try:
            result = subprocess.run(
                ['pkill', '-9', '-f', pattern],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                killed.append(pattern)
        except Exception:
            pass
    return jsonify({'killed': killed, 'success': True})


@app.route('/api/network/stop-rns', methods=['POST'])
@login_required
def api_network_stop_rns():
    """Stop all RNS processes"""
    killed = []
    for proc in ['rnsd', 'nomadnet', 'lxmf', 'RNS']:
        try:
            result = subprocess.run(
                ['pkill', '-9', '-f', proc],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                killed.append(proc)
        except Exception:
            pass
    return jsonify({'killed': killed, 'success': True})


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
        <h1><svg viewBox="0 0 128 128" width="40" height="40" style="vertical-align: middle; margin-right: 10px;"><defs><linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#1a1a2e"/><stop offset="100%" style="stop-color:#16213e"/></linearGradient><linearGradient id="handGrad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#4CAF50"/><stop offset="100%" style="stop-color:#2E7D32"/></linearGradient></defs><circle cx="64" cy="64" r="60" fill="url(#bgGrad)"/><circle cx="64" cy="64" r="56" fill="none" stroke="#4CAF50" stroke-width="2" opacity="0.6"/><g fill="url(#handGrad)" stroke="#2E7D32" stroke-width="1.5"><path d="M64 92 C52 92 42 82 42 66 L42 56 C42 50 46 46 54 46 L74 46 C82 46 86 50 86 56 L86 66 C86 82 76 92 64 92 Z"/><path d="M42 60 C34 56 26 52 22 56 C18 60 18 68 22 72 C26 76 34 76 42 72"/><path d="M78 46 L82 28 C82 24 86 20 90 20 C94 20 98 24 98 28 L94 46"/></g></svg> MeshForge</h1>
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
            --bg-card-hover: #1e2a4a;
            --text: #eee;
            --text-muted: #888;
            --accent: #4CAF50;
            --accent-hover: #45a049;
            --accent-glow: rgba(76, 175, 80, 0.3);
            --danger: #f44336;
            --warning: #ff9800;
            --info: #2196F3;
            --shadow-sm: 0 2px 4px rgba(0,0,0,0.2);
            --shadow-md: 0 4px 12px rgba(0,0,0,0.3);
            --shadow-lg: 0 8px 24px rgba(0,0,0,0.4);
            --transition-fast: 0.15s ease;
            --transition-normal: 0.25s ease;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, var(--bg-dark) 0%, #0f0f1a 100%);
            color: var(--text);
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(90deg, var(--bg-card) 0%, #1a2744 100%);
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--accent);
            box-shadow: var(--shadow-md);
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .header h1 {
            font-size: 1.5rem;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .header h1 .logo {
            font-size: 1.8rem;
        }
        .header .version { color: var(--text-muted); font-size: 0.85rem; }
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
            transition: all var(--transition-fast);
        }
        .nav a:hover, .nav button:hover {
            background: #333;
            border-color: var(--accent);
            transform: translateY(-1px);
        }
        .nav a.active {
            background: var(--accent);
            border-color: var(--accent);
            box-shadow: 0 0 10px var(--accent-glow);
        }

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
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #333;
            box-shadow: var(--shadow-sm);
            transition: all var(--transition-normal);
        }
        .card:hover {
            border-color: #444;
            box-shadow: var(--shadow-md);
            transform: translateY(-2px);
        }
        .card h2 {
            font-size: 0.85rem;
            margin-bottom: 15px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 1.5px;
            font-weight: 600;
        }
        .card .value {
            font-size: 2rem;
            font-weight: bold;
            transition: color var(--transition-fast);
        }
        .card .value.success { color: var(--accent); text-shadow: 0 0 10px var(--accent-glow); }
        .card .value.error { color: var(--danger); }
        .card .value.warning { color: var(--warning); }

        .progress-bar {
            background: #222;
            border-radius: 6px;
            height: 8px;
            margin-top: 10px;
            overflow: hidden;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.3);
        }
        .progress-bar .fill {
            height: 100%;
            background: linear-gradient(90deg, var(--accent) 0%, #66BB6A 100%);
            transition: width 0.5s ease-out;
            border-radius: 6px;
            box-shadow: 0 0 8px var(--accent-glow);
        }
        .progress-bar .fill.warning { background: linear-gradient(90deg, var(--warning) 0%, #FFB74D 100%); }
        .progress-bar .fill.danger { background: linear-gradient(90deg, var(--danger) 0%, #EF5350 100%); }

        .stat-row {
            display: flex;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid #333;
            transition: background var(--transition-fast);
        }
        .stat-row:hover { background: rgba(255,255,255,0.02); }
        .stat-row:last-child { border-bottom: none; }
        .stat-label { color: var(--text-muted); }

        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            margin: 5px;
            transition: all var(--transition-fast);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .btn-success {
            background: linear-gradient(135deg, var(--accent) 0%, #45a049 100%);
            color: white;
            box-shadow: 0 2px 8px var(--accent-glow);
        }
        .btn-success:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px var(--accent-glow);
        }
        .btn-danger {
            background: linear-gradient(135deg, var(--danger) 0%, #d32f2f 100%);
            color: white;
        }
        .btn-danger:hover { transform: translateY(-1px); }
        .btn-warning {
            background: linear-gradient(135deg, var(--warning) 0%, #f57c00 100%);
            color: black;
        }
        .btn-warning:hover { transform: translateY(-1px); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none !important; }

        .log-box {
            background: #0d0d0d;
            border-radius: 8px;
            padding: 15px;
            font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
            font-size: 12px;
            line-height: 1.5;
            max-height: 300px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
            border: 1px solid #222;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.3);
        }
        .log-box::-webkit-scrollbar { width: 8px; }
        .log-box::-webkit-scrollbar-track { background: #111; }
        .log-box::-webkit-scrollbar-thumb { background: #444; border-radius: 4px; }
        .log-box::-webkit-scrollbar-thumb:hover { background: #555; }

        .config-list {
            list-style: none;
        }
        .config-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 15px;
            background: #1a1a2a;
            margin: 8px 0;
            border-radius: 8px;
            border: 1px solid #333;
            transition: all var(--transition-fast);
        }
        .config-item:hover {
            background: #222;
            border-color: #444;
        }
        .config-item .name { font-family: monospace; font-size: 0.95rem; }

        .hardware-item {
            display: flex;
            align-items: center;
            padding: 12px 15px;
            background: #1a1a2a;
            margin: 8px 0;
            border-radius: 8px;
            border: 1px solid #333;
            transition: all var(--transition-fast);
        }
        .hardware-item:hover {
            background: #222;
            border-color: #444;
        }
        .hardware-item .badge {
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            margin-right: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .hardware-item .badge.active { background: var(--accent); color: white; }
        .hardware-item .badge.spi { background: var(--info); color: white; }
        .hardware-item .badge.i2c { background: #9C27B0; color: white; }
        .hardware-item .badge.info { background: #607D8B; color: white; }

        .tabs {
            display: flex;
            gap: 5px;
            border-bottom: 2px solid #333;
            margin-bottom: 25px;
            padding-bottom: 0;
            overflow-x: auto;
            scrollbar-width: none;
        }
        .tabs::-webkit-scrollbar { display: none; }
        .tab {
            padding: 12px 20px;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            margin-bottom: -2px;
            color: var(--text-muted);
            font-weight: 500;
            font-size: 0.9rem;
            transition: all var(--transition-fast);
            white-space: nowrap;
            border-radius: 8px 8px 0 0;
        }
        .tab:hover {
            color: var(--text);
            background: rgba(255,255,255,0.03);
        }
        .tab.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
            background: rgba(76,175,80,0.1);
        }
        .tab-content { display: none; animation: fadeIn 0.3s ease; }
        .tab-content.active { display: block; }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .radio-info {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
        }
        .radio-info .item {
            padding: 15px;
            background: #1a1a2a;
            border-radius: 8px;
            border: 1px solid #333;
            transition: all var(--transition-fast);
        }
        .radio-info .item:hover {
            background: #222;
            border-color: #444;
        }
        .radio-info .label {
            color: var(--text-muted);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }
        .radio-info .value {
            font-size: 1.1rem;
            font-weight: 500;
        }

        /* Input styling */
        input, textarea, select {
            transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
        }
        input:focus, textarea:focus, select:focus {
            outline: none;
            border-color: var(--accent) !important;
            box-shadow: 0 0 0 3px var(--accent-glow);
        }

        /* Link styling */
        a {
            color: var(--accent);
            text-decoration: none;
            transition: color var(--transition-fast);
        }
        a:hover {
            color: #66BB6A;
            text-decoration: underline;
        }

        /* Table styling */
        table {
            border-collapse: collapse;
            width: 100%;
        }
        th {
            text-align: left;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            font-size: 0.8rem;
            letter-spacing: 1px;
        }
        tr {
            transition: background var(--transition-fast);
        }
        tr:hover {
            background: rgba(255,255,255,0.02);
        }

        /* Footer branding */
        .footer {
            text-align: center;
            padding: 20px;
            color: var(--text-muted);
            font-size: 0.85rem;
            margin-top: 40px;
            border-top: 1px solid #333;
        }

        @media (max-width: 768px) {
            .grid { grid-template-columns: 1fr; }
            .header { flex-direction: column; gap: 10px; }
            .nav { flex-wrap: wrap; justify-content: center; }
            .tabs { gap: 2px; }
            .tab { padding: 10px 15px; font-size: 0.85rem; }
            .radio-info { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1><span class="logo"><svg viewBox="0 0 128 128" width="32" height="32" style="vertical-align: middle; margin-right: 8px;"><defs><linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#1a1a2e"/><stop offset="100%" style="stop-color:#16213e"/></linearGradient><linearGradient id="handGrad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#4CAF50"/><stop offset="100%" style="stop-color:#2E7D32"/></linearGradient></defs><circle cx="64" cy="64" r="60" fill="url(#bgGrad)"/><circle cx="64" cy="64" r="56" fill="none" stroke="#4CAF50" stroke-width="2" opacity="0.6"/><circle cx="24" cy="32" r="4" fill="#2196F3" opacity="0.5"/><circle cx="104" cy="32" r="4" fill="#2196F3" opacity="0.5"/><circle cx="24" cy="96" r="4" fill="#2196F3" opacity="0.5"/><circle cx="104" cy="96" r="4" fill="#2196F3" opacity="0.5"/><line x1="24" y1="32" x2="64" y2="64" stroke="#2196F3" stroke-width="1" opacity="0.3"/><line x1="104" y1="32" x2="64" y2="64" stroke="#2196F3" stroke-width="1" opacity="0.3"/><line x1="24" y1="96" x2="64" y2="64" stroke="#2196F3" stroke-width="1" opacity="0.3"/><line x1="104" y1="96" x2="64" y2="64" stroke="#2196F3" stroke-width="1" opacity="0.3"/><g fill="url(#handGrad)" stroke="#2E7D32" stroke-width="1.5"><path d="M64 92 C52 92 42 82 42 66 L42 56 C42 50 46 46 54 46 L74 46 C82 46 86 50 86 56 L86 66 C86 82 76 92 64 92 Z"/><path d="M42 60 C34 56 26 52 22 56 C18 60 18 68 22 72 C26 76 34 76 42 72"/><path d="M78 46 L82 28 C82 24 86 20 90 20 C94 20 98 24 98 28 L94 46"/><path d="M54 46 L54 40 C54 36 58 34 62 36 L62 46" opacity="0.8"/><path d="M62 46 L62 36 C62 32 66 30 70 32 L70 46" opacity="0.8"/><path d="M70 46 L70 38 C70 34 74 32 78 34 L78 46" opacity="0.8"/><path d="M52 92 L52 104 L76 104 L76 92"/></g></svg></span> MeshForge</h1>
            <span class="version">v{{ version }} | Mesh Network Operations Center</span>
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
            <div class="tab" data-tab="gateway">Gateway</div>
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

        <!-- Gateway Tab -->
        <div id="gateway" class="tab-content">
            <div class="grid">
                <div class="card" style="grid-column: span 2;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <h2 style="margin: 0;">Meshtastic ↔ RNS Gateway Bridge</h2>
                        <div>
                            <button class="btn btn-success" onclick="startGateway()" id="gateway-start-btn">Start</button>
                            <button class="btn btn-danger" onclick="stopGateway()" id="gateway-stop-btn">Stop</button>
                            <button class="btn" onclick="refreshGateway()">Refresh</button>
                        </div>
                    </div>
                    <div class="grid" style="grid-template-columns: repeat(4, 1fr); gap: 15px;">
                        <div style="text-align: center; padding: 15px; background: var(--card-bg); border-radius: 8px;">
                            <div style="font-size: 0.9em; color: var(--text-muted);">Gateway Status</div>
                            <div id="gateway-status" style="font-size: 1.5em; font-weight: bold; margin-top: 5px;">--</div>
                        </div>
                        <div style="text-align: center; padding: 15px; background: var(--card-bg); border-radius: 8px;">
                            <div style="font-size: 0.9em; color: var(--text-muted);">meshtasticd</div>
                            <div id="gateway-mesh-status" style="font-size: 1.5em; font-weight: bold; margin-top: 5px;">--</div>
                        </div>
                        <div style="text-align: center; padding: 15px; background: var(--card-bg); border-radius: 8px;">
                            <div style="font-size: 0.9em; color: var(--text-muted);">rnsd</div>
                            <div id="gateway-rns-status" style="font-size: 1.5em; font-weight: bold; margin-top: 5px;">--</div>
                        </div>
                        <div style="text-align: center; padding: 15px; background: var(--card-bg); border-radius: 8px;">
                            <div style="font-size: 0.9em; color: var(--text-muted);">Tracked Nodes</div>
                            <div id="gateway-nodes" style="font-size: 1.5em; font-weight: bold; margin-top: 5px;">--</div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="grid" style="margin-top: 20px;">
                <div class="card">
                    <h2>Bridge Statistics</h2>
                    <div id="gateway-stats" style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                        <div class="item"><div class="label">Mesh → RNS</div><div id="stat-mesh-to-rns">0</div></div>
                        <div class="item"><div class="label">RNS → Mesh</div><div id="stat-rns-to-mesh">0</div></div>
                        <div class="item"><div class="label">Meshtastic Nodes</div><div id="stat-mesh-nodes">0</div></div>
                        <div class="item"><div class="label">RNS Nodes</div><div id="stat-rns-nodes">0</div></div>
                        <div class="item"><div class="label">Errors</div><div id="stat-errors">0</div></div>
                        <div class="item"><div class="label">Last Activity</div><div id="stat-last-activity">--</div></div>
                    </div>
                </div>
                <div class="card">
                    <h2>Diagnostics</h2>
                    <button class="btn btn-success" onclick="runGatewayDiagnostic()" style="margin-bottom: 15px;">Run Diagnostic</button>
                    <button class="btn" onclick="testGatewayConnections()" style="margin-bottom: 15px;">Test Connections</button>
                    <div id="gateway-diagnostic" class="log-box" style="max-height: 200px; overflow-y: auto;">Click "Run Diagnostic" to check gateway prerequisites</div>
                </div>
            </div>

            <div class="card" style="margin-top: 20px;">
                <h2>Tracked Nodes</h2>
                <button class="btn btn-success" onclick="refreshGatewayNodes()" style="margin-bottom: 15px;">Refresh</button>
                <div id="gateway-node-list">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #444;">
                            <th style="padding: 8px; text-align: left;">Network</th>
                            <th style="padding: 8px; text-align: left;">ID</th>
                            <th style="padding: 8px; text-align: left;">Name</th>
                            <th style="padding: 8px; text-align: left;">Last Seen</th>
                        </tr>
                    </table>
                    <div style="color: var(--text-muted); padding: 10px;">No nodes tracked yet</div>
                </div>
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
                const availEl = document.getElementById('available-configs');
                let activeHtml = '';

                // Ensure arrays exist
                const activeConfigs = Array.isArray(data.active) ? data.active : [];
                const availableConfigs = Array.isArray(data.available) ? data.available : [];

                // Show main config if exists
                if (data.main_config) {
                    activeHtml += `<li class="config-item"><span class="name" style="color: var(--accent);">📄 ${escapeHtml(data.main_config)}</span></li>`;
                }

                // Show error if any
                if (data.error) {
                    activeHtml += `<li class="config-item" style="color: var(--warning);">${escapeHtml(data.error)}</li>`;
                }

                // Show active configs from config.d
                if (activeConfigs.length > 0) {
                    activeHtml += activeConfigs.map(c => `
                        <li class="config-item">
                            <span class="name">${escapeHtml(c)}</span>
                            <button class="btn btn-danger" onclick="deactivateConfig('${escapeHtml(c).replace(/'/g, "\\'")}')">Deactivate</button>
                        </li>
                    `).join('');
                } else if (!data.main_config && !data.error) {
                    activeHtml += '<li class="config-item">No configurations in config.d/</li>';
                }

                activeEl.innerHTML = activeHtml || '<li class="config-item">No configurations found</li>';

                if (availableConfigs.length === 0) {
                    availEl.innerHTML = '<li class="config-item">No configurations in available.d/</li>';
                } else {
                    availEl.innerHTML = availableConfigs.map(c => `
                        <li class="config-item">
                            <span class="name">${escapeHtml(c)}</span>
                            <button class="btn btn-success" onclick="activateConfig('${escapeHtml(c).replace(/'/g, "\\'")}')">Activate</button>
                        </li>
                    `).join('');
                }
            } catch (e) {
                console.error('Error fetching configs:', e);
                document.getElementById('active-configs').innerHTML = '<li class="config-item" style="color: var(--warning);">Error loading configs</li>';
                document.getElementById('available-configs').innerHTML = '<li class="config-item" style="color: var(--warning);">Error loading configs</li>';
            }
        }

        async function refreshHardware() {
            const el = document.getElementById('hardware-list');
            try {
                const resp = await fetch('/api/hardware');
                if (!resp.ok) {
                    el.innerHTML = '<div class="hardware-item"><span class="badge warning">ERROR</span><span>Failed to fetch hardware status</span></div>';
                    return;
                }
                const data = await resp.json();

                // Handle error response
                if (data.error) {
                    el.innerHTML = `<div class="hardware-item"><span class="badge warning">ERROR</span><span>${escapeHtml(data.error)}</span></div>`;
                    return;
                }

                const devices = Array.isArray(data.devices) ? data.devices : [];

                if (devices.length === 0) {
                    el.innerHTML = '<div class="hardware-item"><span class="badge info">INFO</span><span>No hardware devices detected (check if meshtasticd is running)</span></div>';
                    return;
                }

                el.innerHTML = devices.map(d => `
                    <div class="hardware-item">
                        <span class="badge ${escapeHtml(d.type || 'info').toLowerCase()}">${escapeHtml(d.type || 'DEVICE')}</span>
                        <span><strong>${escapeHtml(d.device || 'Unknown')}</strong> - ${escapeHtml(d.description || '')}</span>
                    </div>
                `).join('');
            } catch (e) {
                console.error('Error fetching hardware:', e);
                el.innerHTML = '<div class="hardware-item"><span class="badge danger">ERROR</span><span>Network error fetching hardware</span></div>';
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
                if (data.error) {
                    document.getElementById('processes').textContent = 'Error: ' + data.error;
                } else if (data.processes && Array.isArray(data.processes)) {
                    document.getElementById('processes').textContent = data.processes.join('\\n');
                } else {
                    document.getElementById('processes').textContent = 'No process data available';
                }
            } catch (e) {
                console.error('Error fetching processes:', e);
                document.getElementById('processes').textContent = 'Error fetching processes';
            }
        }

        // ========================================
        // Gateway functions
        // ========================================
        async function refreshGateway() {
            try {
                const resp = await fetch('/api/gateway/status');
                const data = await resp.json();

                // Update status displays
                const statusEl = document.getElementById('gateway-status');
                const meshEl = document.getElementById('gateway-mesh-status');
                const rnsEl = document.getElementById('gateway-rns-status');
                const nodesEl = document.getElementById('gateway-nodes');

                // Gateway status
                if (data.running) {
                    statusEl.innerHTML = '<span style="color: var(--accent);">Running</span>';
                } else if (data.enabled) {
                    statusEl.innerHTML = '<span style="color: var(--warning);">Stopped</span>';
                } else {
                    statusEl.innerHTML = '<span style="color: var(--text-muted);">Disabled</span>';
                }

                // Service status
                meshEl.innerHTML = data.services?.meshtasticd
                    ? '<span style="color: var(--accent);">Online</span>'
                    : '<span style="color: var(--danger);">Offline</span>';

                rnsEl.innerHTML = data.services?.rnsd
                    ? '<span style="color: var(--accent);">Online</span>'
                    : '<span style="color: var(--danger);">Offline</span>';

                // Node count
                nodesEl.textContent = data.stats?.total_nodes || 0;

                // Update statistics
                document.getElementById('stat-mesh-to-rns').textContent = data.stats?.mesh_to_rns || 0;
                document.getElementById('stat-rns-to-mesh').textContent = data.stats?.rns_to_mesh || 0;
                document.getElementById('stat-mesh-nodes').textContent = data.stats?.meshtastic_nodes || 0;
                document.getElementById('stat-rns-nodes').textContent = data.stats?.rns_nodes || 0;
                document.getElementById('stat-errors').textContent = data.stats?.errors || 0;
                document.getElementById('stat-last-activity').textContent = data.stats?.last_activity || '--';

                // Update button states
                document.getElementById('gateway-start-btn').disabled = data.running;
                document.getElementById('gateway-stop-btn').disabled = !data.running;

            } catch (e) {
                console.error('Error fetching gateway status:', e);
            }
        }

        async function startGateway() {
            try {
                document.getElementById('gateway-start-btn').disabled = true;
                const resp = await fetch('/api/gateway/start', { method: 'POST' });
                const data = await resp.json();
                if (data.success) {
                    alert(data.message || 'Gateway started');
                } else {
                    alert('Error: ' + (data.error || 'Failed to start gateway'));
                }
                refreshGateway();
            } catch (e) {
                alert('Error starting gateway: ' + e);
                document.getElementById('gateway-start-btn').disabled = false;
            }
        }

        async function stopGateway() {
            try {
                document.getElementById('gateway-stop-btn').disabled = true;
                const resp = await fetch('/api/gateway/stop', { method: 'POST' });
                const data = await resp.json();
                if (data.success) {
                    alert(data.message || 'Gateway stopped');
                } else {
                    alert('Error: ' + (data.error || 'Failed to stop gateway'));
                }
                refreshGateway();
            } catch (e) {
                alert('Error stopping gateway: ' + e);
                document.getElementById('gateway-stop-btn').disabled = false;
            }
        }

        async function runGatewayDiagnostic() {
            const diagEl = document.getElementById('gateway-diagnostic');
            diagEl.innerHTML = '<em>Running diagnostic checks...</em>';

            try {
                const resp = await fetch('/api/gateway/diagnostic', { method: 'POST' });
                const data = await resp.json();

                let html = '';
                for (const result of data.results || []) {
                    const color = result.status === 'PASS' ? 'var(--accent)' :
                                  result.status === 'FAIL' ? 'var(--danger)' : 'var(--warning)';
                    html += `<div style="margin: 5px 0;"><span style="color: ${color}; font-weight: bold;">[${result.status}]</span> ${escapeHtml(result.check)}: ${escapeHtml(result.message)}</div>`;
                }

                if (data.summary) {
                    html += `<hr style="border-color: #444; margin: 10px 0;">`;
                    html += `<div><strong>Summary:</strong> ${data.summary.passed} passed, ${data.summary.failed} failed, ${data.summary.warnings} warnings</div>`;
                    if (data.summary.ready) {
                        html += `<div style="color: var(--accent); margin-top: 5px;">✓ Gateway ready to start</div>`;
                    } else {
                        html += `<div style="color: var(--danger); margin-top: 5px;">✗ Fix issues before starting gateway</div>`;
                    }
                }

                diagEl.innerHTML = html;
            } catch (e) {
                diagEl.innerHTML = `<span style="color: var(--danger);">Error running diagnostic: ${e}</span>`;
            }
        }

        async function testGatewayConnections() {
            const diagEl = document.getElementById('gateway-diagnostic');
            diagEl.innerHTML = '<em>Testing connections...</em>';

            try {
                const resp = await fetch('/api/gateway/test', { method: 'POST' });
                const data = await resp.json();

                let html = '<div style="font-weight: bold; margin-bottom: 10px;">Connection Test Results:</div>';

                // Meshtastic
                const meshColor = data.meshtastic?.success ? 'var(--accent)' : 'var(--danger)';
                html += `<div style="margin: 5px 0;"><span style="color: ${meshColor};">[${data.meshtastic?.success ? 'OK' : 'FAIL'}]</span> Meshtastic: ${escapeHtml(data.meshtastic?.message || 'Unknown')}</div>`;

                // RNS
                const rnsColor = data.rns?.success ? 'var(--accent)' : 'var(--danger)';
                html += `<div style="margin: 5px 0;"><span style="color: ${rnsColor};">[${data.rns?.success ? 'OK' : 'FAIL'}]</span> RNS: ${escapeHtml(data.rns?.message || 'Unknown')}</div>`;

                diagEl.innerHTML = html;
            } catch (e) {
                diagEl.innerHTML = `<span style="color: var(--danger);">Error testing connections: ${e}</span>`;
            }
        }

        async function refreshGatewayNodes() {
            const listEl = document.getElementById('gateway-node-list');
            listEl.innerHTML = '<em>Loading nodes...</em>';

            try {
                const resp = await fetch('/api/gateway/nodes');
                const data = await resp.json();

                if (!data.nodes || data.nodes.length === 0) {
                    listEl.innerHTML = '<div style="color: var(--text-muted); padding: 10px;">No nodes tracked yet. Start the gateway to begin tracking.</div>';
                    return;
                }

                let html = `<table style="width: 100%; border-collapse: collapse;">
                    <tr style="border-bottom: 1px solid #444;">
                        <th style="padding: 8px; text-align: left;">Network</th>
                        <th style="padding: 8px; text-align: left;">ID</th>
                        <th style="padding: 8px; text-align: left;">Name</th>
                        <th style="padding: 8px; text-align: left;">Last Seen</th>
                    </tr>`;

                for (const node of data.nodes) {
                    const networkBadge = node.network === 'meshtastic'
                        ? '<span style="background: #2196F3; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.8em;">MESH</span>'
                        : '<span style="background: #9C27B0; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.8em;">RNS</span>';

                    html += `<tr style="border-bottom: 1px solid #333;">
                        <td style="padding: 8px;">${networkBadge}</td>
                        <td style="padding: 8px; font-family: monospace;">${escapeHtml(node.id || '')}</td>
                        <td style="padding: 8px;">${escapeHtml(node.name || '--')}</td>
                        <td style="padding: 8px;">${escapeHtml(node.last_seen || '--')}</td>
                    </tr>`;
                }

                html += '</table>';
                html += `<div style="color: var(--text-muted); padding: 10px;">Total: ${data.total} nodes (${data.meshtastic_count} Meshtastic, ${data.rns_count} RNS)</div>`;

                listEl.innerHTML = html;
            } catch (e) {
                listEl.innerHTML = `<span style="color: var(--danger);">Error loading nodes: ${e}</span>`;
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
                // Use full nodes endpoint for detailed information
                const resp = await fetch('/api/nodes/full');
                const data = await resp.json();

                if (data.error) {
                    el.innerHTML = `<div style="color: var(--warning);">${data.error}</div>`;
                } else if (data.nodes && Array.isArray(data.nodes) && data.nodes.length > 0) {
                    el.innerHTML = `
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr style="border-bottom: 1px solid #444;">
                                <th style="padding: 8px; text-align: left;">Node ID</th>
                                <th style="padding: 8px; text-align: left;">Name</th>
                                <th style="padding: 8px; text-align: left;">Hardware</th>
                                <th style="padding: 8px; text-align: left;">Battery</th>
                                <th style="padding: 8px; text-align: left;">SNR</th>
                                <th style="padding: 8px; text-align: left;">Hops</th>
                                <th style="padding: 8px; text-align: left;">Last Heard</th>
                            </tr>
                            ${data.nodes.map(n => `
                                <tr style="border-bottom: 1px solid #333;">
                                    <td style="padding: 8px; font-family: monospace;">${escapeHtml(n.id || '')}</td>
                                    <td style="padding: 8px;">${escapeHtml(n.name || n.short || '--')}</td>
                                    <td style="padding: 8px;">${escapeHtml(n.hardware || '--')}</td>
                                    <td style="padding: 8px;">${n.battery ? n.battery + '%' : '--'}</td>
                                    <td style="padding: 8px;">${n.snr != null ? n.snr + ' dB' : '--'}</td>
                                    <td style="padding: 8px;">${n.hops != null ? n.hops : '--'}</td>
                                    <td style="padding: 8px;">${escapeHtml(n.last_heard_ago || '--')}</td>
                                </tr>
                            `).join('')}
                        </table>
                        <p style="margin-top: 10px; color: var(--text-muted);">${data.total_nodes || data.nodes.length} node(s) found${data.nodes_with_position ? `, ${data.nodes_with_position} with GPS` : ''}</p>
                    `;
                } else {
                    el.innerHTML = '<div style="color: var(--text-muted);">No nodes found. Make sure meshtasticd is running and connected to a radio.</div>';
                }

                // Show raw output if available
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
        refreshGateway();

        // Auto-refresh status every 5 seconds
        setInterval(fetchStatus, 5000);
        setInterval(refreshGateway, 10000);  // Refresh gateway status every 10s

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

            // Check if Leaflet is loaded
            if (typeof L === 'undefined') {
                console.error('Leaflet not loaded yet');
                mapContainer.innerHTML = '<div style="color: var(--warning); padding: 20px;">Map library loading... Refresh the page if this persists.</div>';
                return;
            }

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

            // Check if map is initialized
            if (!meshMap || typeof L === 'undefined') {
                countEl.textContent = 'Map not initialized';
                listEl.innerHTML = '<div style="color: var(--warning);">Click the Map tab to initialize the map first.</div>';
                return;
            }

            countEl.textContent = 'Loading...';

            try {
                // Add timeout to prevent infinite loading
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 30000);

                const resp = await fetch('/api/nodes/full', { signal: controller.signal });
                clearTimeout(timeoutId);
                const data = await resp.json();

                if (data.error) {
                    countEl.textContent = data.error;
                    listEl.innerHTML = `<div style="color: var(--warning);">${data.error}</div>`;
                    return;
                }

                // Ensure nodes is an array
                if (!data.nodes || !Array.isArray(data.nodes)) {
                    countEl.textContent = 'No nodes available';
                    listEl.innerHTML = '<div style="color: var(--text-muted);">No node data available. Connect to meshtasticd first.</div>';
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
                if (e.name === 'AbortError') {
                    countEl.textContent = 'Timeout - try again';
                    listEl.innerHTML = '<div style="color: var(--warning);">Connection timed out. Click Refresh Map to try again.</div>';
                } else {
                    countEl.textContent = 'Error loading map data';
                    listEl.innerHTML = `<div style="color: var(--danger);">Error: ${e.message}</div>`;
                }
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
    parser.add_argument('--auto-port', action='store_true',
                        help='Automatically find an available port if default is in use')
    parser.add_argument('--list-ports', action='store_true',
                        help='List what processes are using common ports and exit')
    args = parser.parse_args()

    # Handle --list-ports
    if args.list_ports:
        print("Checking common ports...")
        ports_to_check = [8080, 8081, 4403, 8880, 5000, 9000]
        for p in ports_to_check:
            is_available, process_info = check_port_available(args.host, p)
            if is_available:
                print(f"  Port {p}: Available")
            else:
                print(f"  Port {p}: In use by {process_info or 'unknown process'}")
        sys.exit(0)

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

    # Check if port is available
    actual_port = args.port
    is_available, process_info = check_port_available(args.host, actual_port)
    if not is_available:
        if args.auto_port:
            # Try to find an available port
            print(f"Port {actual_port} is in use, searching for available port...")
            new_port = find_available_port(args.host, actual_port + 1, max_tries=20)
            if new_port:
                print(f"Found available port: {new_port}")
                actual_port = new_port
            else:
                print(f"ERROR: Could not find available port in range {actual_port+1}-{actual_port+20}")
                sys.exit(1)
        else:
            print()
            print("=" * 60)
            print(f"ERROR: Port {actual_port} is already in use")
            print("=" * 60)
            if process_info:
                print(f"Process using port: {process_info}")
            else:
                print("Could not identify process using the port.")
                print(f"Check with: sudo lsof -i :{actual_port}")
            print()

            # Known services that commonly use certain ports
            known_services = {
                8080: "AREDN web UI, HamClock API, or other web services",
                4403: "meshtasticd TCP interface",
                8081: "HamClock live port",
            }
            if actual_port in known_services:
                print(f"Note: Port {actual_port} is commonly used by: {known_services[actual_port]}")
                print()

            # Suggest alternatives
            print("Options:")
            print(f"  --port 9000      Use a specific port")
            print(f"  --auto-port      Auto-find an available port")
            print(f"  --list-ports     Show what's using common ports")
            alt_port = find_available_port(args.host, actual_port + 1, max_tries=10)
            if alt_port:
                print()
                print(f"Suggested: sudo python3 src/main_web.py --port {alt_port}")
            print("=" * 60)
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
    print(f"  http://localhost:{actual_port}/")
    print(f"  http://{local_ip}:{actual_port}/")
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
            port=actual_port,
            debug=args.debug,
            threaded=True,
            use_reloader=False  # Prevent duplicate processes
        )
    finally:
        # Clean up on exit
        cleanup_processes()


if __name__ == '__main__':
    main()
