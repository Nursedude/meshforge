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
from pathlib import Path
from datetime import datetime
from functools import wraps

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

# Configuration
CONFIG = {
    'auth_enabled': False,
    'password': None,  # Set via --password or environment
    'host': '0.0.0.0',
    'port': 8080,
}

# CPU stats for delta calculation
_last_cpu = None


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
        if request.form.get('password') == CONFIG['password']:
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

def find_meshtastic_cli():
    """Find meshtastic CLI path"""
    cli_paths = [
        '/root/.local/bin/meshtastic',
        '/home/pi/.local/bin/meshtastic',
        os.path.expanduser('~/.local/bin/meshtastic'),
    ]
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user:
        cli_paths.insert(0, f'/home/{sudo_user}/.local/bin/meshtastic')

    for path in cli_paths:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path

    result = subprocess.run(['which', 'meshtastic'], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


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
        result = subprocess.run(
            [cli, '--host', 'localhost', '--info'],
            capture_output=True, text=True, timeout=30
        )
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
        result = subprocess.run(
            [cli, '--host', 'localhost', '--nodes'],
            capture_output=True, text=True, timeout=30
        )
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


def send_mesh_message(text, destination=None):
    """Send a message to the mesh"""
    cli = find_meshtastic_cli()
    if not cli:
        return {'error': 'Meshtastic CLI not found'}

    if not text or not text.strip():
        return {'error': 'Message cannot be empty'}

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
        cmd = [cli, '--host', 'localhost', '--sendtext', text.strip()]
        if destination:
            cmd.extend(['--dest', destination])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

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

    if not config_name:
        return jsonify({'error': 'No config specified'}), 400

    src = Path(f'/etc/meshtasticd/available.d/{config_name}')
    dst = Path(f'/etc/meshtasticd/config.d/{config_name}')

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

    if not config_name:
        return jsonify({'error': 'No config specified'}), 400

    config_path = Path(f'/etc/meshtasticd/config.d/{config_name}')

    if not config_path.exists():
        return jsonify({'error': 'Config not active'}), 404

    try:
        config_path.unlink()
        return jsonify({'success': True, 'message': f'{config_name} deactivated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config/content/<path:config_name>')
@login_required
def api_config_content(config_name):
    """Get config file content"""
    # Check both directories
    for base in ['/etc/meshtasticd/config.d', '/etc/meshtasticd/available.d']:
        path = Path(base) / config_name
        if path.exists():
            try:
                return jsonify({'content': path.read_text()})
            except Exception as e:
                return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'Config not found'}), 404


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

    if not config_name:
        return jsonify({'error': 'No config specified'}), 400
    if content is None:
        return jsonify({'error': 'No content provided'}), 400

    # Only allow editing in config.d or available.d
    for base in ['/etc/meshtasticd/config.d', '/etc/meshtasticd/available.d']:
        path = Path(base) / config_name
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


@app.route('/api/logs/stream')
@login_required
def api_logs_stream():
    """Stream service logs (returns last N lines, call repeatedly for updates)"""
    lines = request.args.get('lines', 100, type=int)
    since = request.args.get('since', '')

    try:
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
    <title>Meshtasticd Manager</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
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
            <div class="tab" data-tab="nodes">Nodes</div>
            <div class="tab" data-tab="messages">Messages</div>
            <div class="tab" data-tab="service">Service</div>
            <div class="tab" data-tab="config">Config</div>
            <div class="tab" data-tab="hardware">Hardware</div>
            <div class="tab" data-tab="radio">Radio</div>
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
                            <span class="name">${c}</span>
                            <button class="btn btn-success" onclick="activateConfig('${c}')">Activate</button>
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
                        <span class="badge ${d.type.toLowerCase()}">${d.type}</span>
                        <span><strong>${d.device}</strong> - ${d.description}</span>
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
                    el.innerHTML = `<div class="item" style="grid-column: span 2; color: var(--warning);">${data.error}</div>`;
                } else {
                    el.innerHTML = Object.entries(data).map(([k, v]) => `
                        <div class="item">
                            <div class="label">${k.replace('_', ' ').toUpperCase()}</div>
                            <div>${v}</div>
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

def main():
    # Get defaults from environment variables
    default_port = int(os.environ.get('MESHTASTICD_WEB_PORT', 8880))
    default_host = os.environ.get('MESHTASTICD_WEB_HOST', '0.0.0.0')

    parser = argparse.ArgumentParser(
        description='Meshtasticd Manager - Web UI',
        epilog='''
Examples:
  sudo python3 src/main_web.py                    # Default port 8880
  sudo python3 src/main_web.py --port 9000        # Custom port
  sudo python3 src/main_web.py -p 8080            # Short form

Environment variables:
  MESHTASTICD_WEB_PORT=9000      # Set default port
  MESHTASTICD_WEB_PASSWORD=xxx   # Enable authentication
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
    args = parser.parse_args()

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

    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True
    )


if __name__ == '__main__':
    main()
