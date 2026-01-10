#!/usr/bin/env python3
"""
MeshForge Web Monitor - Lightweight NOC Dashboard

A simple, reliable status monitor for MeshForge deployments.
View from your phone, do real work via SSH.

Build. Test. Deploy. Bridge. Monitor.
"""

import json
import logging
import os
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from functools import wraps

# Flask with minimal dependencies
try:
    from flask import Flask, jsonify, request, Response
except ImportError:
    print("Flask required: pip install flask")
    exit(1)

# Version
try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.4.3"

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger('web_monitor')

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_PORT = 5000
DEFAULT_HOST = "0.0.0.0"

# Service ports to check
MESHTASTICD_PORT = 4403
RNSD_PORT = 37428

# Cache settings
CACHE_TTL = 5  # seconds

# =============================================================================
# Flask App
# =============================================================================

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Simple auth (optional)
AUTH_ENABLED = os.environ.get('MESHFORGE_AUTH', '').lower() == 'true'
AUTH_USER = os.environ.get('MESHFORGE_USER', 'admin')
AUTH_PASS = os.environ.get('MESHFORGE_PASS', 'meshforge')


def check_auth(username, password):
    return username == AUTH_USER and password == AUTH_PASS


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not AUTH_ENABLED:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return Response(
                'Authentication required', 401,
                {'WWW-Authenticate': 'Basic realm="MeshForge Monitor"'}
            )
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# Status Checks (Cached)
# =============================================================================

_cache = {}
_cache_lock = threading.Lock()


def cached(ttl=CACHE_TTL):
    """Simple cache decorator"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = func.__name__
            now = time.time()
            with _cache_lock:
                if key in _cache:
                    value, timestamp = _cache[key]
                    if now - timestamp < ttl:
                        return value
            result = func(*args, **kwargs)
            with _cache_lock:
                _cache[key] = (result, now)
            return result
        return wrapper
    return decorator


def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is listening"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def check_systemd_service(name: str) -> dict:
    """Check systemd service status"""
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', name],
            capture_output=True, text=True, timeout=5
        )
        active = result.stdout.strip() == 'active'
        return {'running': active, 'status': result.stdout.strip()}
    except FileNotFoundError:
        return {'running': False, 'status': 'systemctl not found'}
    except Exception as e:
        return {'running': False, 'status': str(e)}


@cached(ttl=5)
def get_services_status() -> dict:
    """Get status of core services"""
    return {
        'meshtasticd': {
            'port_open': check_port('127.0.0.1', MESHTASTICD_PORT),
            'systemd': check_systemd_service('meshtasticd')
        },
        'rnsd': {
            'port_open': check_port('127.0.0.1', RNSD_PORT),
            'systemd': check_systemd_service('rnsd')
        }
    }


@cached(ttl=10)
def get_system_health() -> dict:
    """Get system resource info"""
    health = {
        'cpu_percent': 0,
        'memory_percent': 0,
        'memory_used_mb': 0,
        'memory_total_mb': 0,
        'disk_percent': 0,
        'temperature': None,
        'uptime': None
    }

    # Memory from /proc/meminfo
    try:
        with open('/proc/meminfo') as f:
            meminfo = f.read()
            total = available = 0
            for line in meminfo.split('\n'):
                if 'MemTotal' in line:
                    total = int(line.split()[1]) // 1024
                elif 'MemAvailable' in line:
                    available = int(line.split()[1]) // 1024
            used = total - available
            health['memory_total_mb'] = total
            health['memory_used_mb'] = used
            health['memory_percent'] = int((used / total * 100)) if total > 0 else 0
    except Exception:
        pass

    # CPU load average
    try:
        with open('/proc/loadavg') as f:
            load = float(f.read().split()[0])
            # Approximate CPU usage from load average
            cpu_count = os.cpu_count() or 1
            health['cpu_percent'] = min(100, int(load / cpu_count * 100))
    except Exception:
        pass

    # Disk usage
    try:
        result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                health['disk_percent'] = int(parts[4].replace('%', ''))
    except Exception:
        pass

    # Temperature (Raspberry Pi)
    try:
        temp_file = Path('/sys/class/thermal/thermal_zone0/temp')
        if temp_file.exists():
            health['temperature'] = round(int(temp_file.read_text().strip()) / 1000, 1)
    except Exception:
        pass

    # Uptime
    try:
        with open('/proc/uptime') as f:
            uptime_seconds = float(f.read().split()[0])
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            mins = int((uptime_seconds % 3600) // 60)
            health['uptime'] = f"{days}d {hours}h {mins}m"
    except Exception:
        pass

    return health


@cached(ttl=10)
def get_gateway_stats() -> dict:
    """Get gateway bridge statistics"""
    stats = {
        'running': False,
        'mesh_to_rns': 0,
        'rns_to_mesh': 0,
        'total_bridged': 0,
        'last_activity': None,
        'meshtastic_nodes': 0,
        'rns_nodes': 0
    }

    # Try to load from gateway state file
    try:
        from utils.paths import get_real_user_home
        state_file = get_real_user_home() / '.local' / 'share' / 'meshforge' / 'gateway_state.json'
        if state_file.exists():
            data = json.loads(state_file.read_text())
            stats.update({
                'running': data.get('running', False),
                'mesh_to_rns': data.get('stats', {}).get('mesh_to_rns', 0),
                'rns_to_mesh': data.get('stats', {}).get('rns_to_mesh', 0),
                'last_activity': data.get('stats', {}).get('last_activity'),
            })
            stats['total_bridged'] = stats['mesh_to_rns'] + stats['rns_to_mesh']
    except Exception:
        pass

    return stats


@cached(ttl=30)
def get_node_counts() -> dict:
    """Get node counts from both networks"""
    counts = {
        'meshtastic': 0,
        'rns': 0,
        'total': 0,
        'error': None
    }

    # Try Meshtastic nodes via meshtastic CLI
    try:
        result = subprocess.run(
            ['meshtastic', '--host', '127.0.0.1', '--info'],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            # Count "Node" lines in output
            nodes = result.stdout.count('Node ')
            counts['meshtastic'] = max(1, nodes)  # At least 1 (self)
    except Exception as e:
        counts['error'] = str(e)

    # RNS nodes would require RNS API - skip for now
    counts['total'] = counts['meshtastic'] + counts['rns']

    return counts


# =============================================================================
# HTML Template - Single Page Dashboard
# =============================================================================

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MeshForge Monitor</title>
    <style>
        :root {
            --bg: #1a1a2e;
            --card: #16213e;
            --accent: #00d4aa;
            --warning: #f39c12;
            --danger: #e74c3c;
            --text: #eee;
            --muted: #888;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            padding: 20px;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        .header h1 {
            font-size: 1.8em;
            color: var(--accent);
            margin-bottom: 5px;
        }
        .header .subtitle {
            color: var(--muted);
            font-size: 0.9em;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            max-width: 1200px;
            margin: 0 auto;
        }
        .card {
            background: var(--card);
            border-radius: 12px;
            padding: 20px;
        }
        .card h2 {
            font-size: 1em;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 15px;
            border-bottom: 1px solid #333;
            padding-bottom: 10px;
        }
        .status-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 0;
            border-bottom: 1px solid #222;
        }
        .status-row:last-child { border-bottom: none; }
        .status-label { color: var(--text); }
        .status-value { font-weight: bold; font-family: monospace; }
        .status-ok { color: var(--accent); }
        .status-warn { color: var(--warning); }
        .status-error { color: var(--danger); }
        .status-muted { color: var(--muted); }
        .big-number {
            font-size: 2.5em;
            font-weight: bold;
            color: var(--accent);
            text-align: center;
            padding: 20px 0;
        }
        .big-number .label {
            font-size: 0.3em;
            color: var(--muted);
            display: block;
            margin-top: 5px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            text-align: center;
        }
        .stat-box {
            background: rgba(0,212,170,0.1);
            border-radius: 8px;
            padding: 15px;
        }
        .stat-box .value {
            font-size: 1.5em;
            font-weight: bold;
            color: var(--accent);
        }
        .stat-box .label {
            font-size: 0.8em;
            color: var(--muted);
            margin-top: 5px;
        }
        .health-bar {
            height: 8px;
            background: #333;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 5px;
        }
        .health-bar .fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s;
        }
        .health-bar .fill.ok { background: var(--accent); }
        .health-bar .fill.warn { background: var(--warning); }
        .health-bar .fill.error { background: var(--danger); }
        .refresh-info {
            text-align: center;
            color: var(--muted);
            font-size: 0.8em;
            margin-top: 30px;
        }
        .footer {
            text-align: center;
            color: var(--muted);
            font-size: 0.8em;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #333;
        }
        @media (max-width: 600px) {
            body { padding: 10px; }
            .grid { gap: 15px; }
            .card { padding: 15px; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>MeshForge Monitor</h1>
        <div class="subtitle">Build. Test. Deploy. Bridge. Monitor.</div>
    </div>

    <div class="grid">
        <!-- Services Status -->
        <div class="card">
            <h2>Services</h2>
            <div id="services">
                <div class="status-row">
                    <span class="status-label">meshtasticd</span>
                    <span class="status-value status-muted">...</span>
                </div>
                <div class="status-row">
                    <span class="status-label">rnsd</span>
                    <span class="status-value status-muted">...</span>
                </div>
            </div>
        </div>

        <!-- Gateway Stats -->
        <div class="card">
            <h2>Gateway Bridge</h2>
            <div id="gateway">
                <div class="big-number">
                    <span id="bridged-count">--</span>
                    <span class="label">Messages Bridged</span>
                </div>
                <div class="stats-grid">
                    <div class="stat-box">
                        <div class="value" id="mesh-to-rns">--</div>
                        <div class="label">Mesh → RNS</div>
                    </div>
                    <div class="stat-box">
                        <div class="value" id="rns-to-mesh">--</div>
                        <div class="label">RNS → Mesh</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Node Counts -->
        <div class="card">
            <h2>Network Nodes</h2>
            <div id="nodes">
                <div class="stats-grid">
                    <div class="stat-box">
                        <div class="value" id="mesh-nodes">--</div>
                        <div class="label">Meshtastic</div>
                    </div>
                    <div class="stat-box">
                        <div class="value" id="rns-nodes">--</div>
                        <div class="label">Reticulum</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- System Health -->
        <div class="card">
            <h2>System Health</h2>
            <div id="health">
                <div class="status-row">
                    <span class="status-label">CPU</span>
                    <span class="status-value" id="cpu-pct">--%</span>
                </div>
                <div class="health-bar"><div class="fill ok" id="cpu-bar" style="width: 0%"></div></div>

                <div class="status-row" style="margin-top: 15px;">
                    <span class="status-label">Memory</span>
                    <span class="status-value" id="mem-pct">--%</span>
                </div>
                <div class="health-bar"><div class="fill ok" id="mem-bar" style="width: 0%"></div></div>

                <div class="status-row" style="margin-top: 15px;">
                    <span class="status-label">Disk</span>
                    <span class="status-value" id="disk-pct">--%</span>
                </div>
                <div class="health-bar"><div class="fill ok" id="disk-bar" style="width: 0%"></div></div>

                <div class="status-row" style="margin-top: 15px;">
                    <span class="status-label">Temperature</span>
                    <span class="status-value" id="temp">--</span>
                </div>
                <div class="status-row">
                    <span class="status-label">Uptime</span>
                    <span class="status-value status-muted" id="uptime">--</span>
                </div>
            </div>
        </div>
    </div>

    <div class="refresh-info">
        Auto-refresh every 10 seconds | Last update: <span id="last-update">--</span>
    </div>

    <div class="footer">
        MeshForge v''' + __version__ + ''' |
        <a href="https://github.com/Nursedude/meshforge" style="color: var(--accent);">GitHub</a>
    </div>

    <script>
        function updateStatus(elementId, isOk, text) {
            const el = document.getElementById(elementId);
            if (!el) return;
            el.textContent = text;
            el.className = 'status-value ' + (isOk ? 'status-ok' : 'status-error');
        }

        function updateBar(barId, percent) {
            const bar = document.getElementById(barId);
            if (!bar) return;
            bar.style.width = percent + '%';
            bar.className = 'fill ' + (percent < 70 ? 'ok' : percent < 90 ? 'warn' : 'error');
        }

        async function refresh() {
            try {
                const resp = await fetch('/api/status');
                const data = await resp.json();

                // Services
                const servicesEl = document.getElementById('services');
                if (data.services) {
                    let html = '';
                    for (const [name, status] of Object.entries(data.services)) {
                        const isOk = status.port_open;
                        const statusText = isOk ? 'Online' : 'Offline';
                        const statusClass = isOk ? 'status-ok' : 'status-error';
                        html += `<div class="status-row">
                            <span class="status-label">${name}</span>
                            <span class="status-value ${statusClass}">${statusText}</span>
                        </div>`;
                    }
                    servicesEl.innerHTML = html;
                }

                // Gateway
                if (data.gateway) {
                    document.getElementById('bridged-count').textContent = data.gateway.total_bridged || 0;
                    document.getElementById('mesh-to-rns').textContent = data.gateway.mesh_to_rns || 0;
                    document.getElementById('rns-to-mesh').textContent = data.gateway.rns_to_mesh || 0;
                }

                // Nodes
                if (data.nodes) {
                    document.getElementById('mesh-nodes').textContent = data.nodes.meshtastic || 0;
                    document.getElementById('rns-nodes').textContent = data.nodes.rns || 0;
                }

                // Health
                if (data.health) {
                    document.getElementById('cpu-pct').textContent = data.health.cpu_percent + '%';
                    updateBar('cpu-bar', data.health.cpu_percent);

                    document.getElementById('mem-pct').textContent = data.health.memory_percent + '%';
                    updateBar('mem-bar', data.health.memory_percent);

                    document.getElementById('disk-pct').textContent = data.health.disk_percent + '%';
                    updateBar('disk-bar', data.health.disk_percent);

                    document.getElementById('temp').textContent =
                        data.health.temperature ? data.health.temperature + '°C' : '--';
                    document.getElementById('uptime').textContent = data.health.uptime || '--';
                }

                document.getElementById('last-update').textContent = new Date().toLocaleTimeString();

            } catch (e) {
                console.error('Refresh failed:', e);
            }
        }

        // Initial load
        refresh();

        // Auto-refresh
        setInterval(refresh, 10000);
    </script>
</body>
</html>
'''


# =============================================================================
# Routes
# =============================================================================

@app.route('/')
@requires_auth
def index():
    """Dashboard page"""
    return DASHBOARD_HTML


@app.route('/api/status')
@requires_auth
def api_status():
    """Combined status API endpoint"""
    return jsonify({
        'services': get_services_status(),
        'gateway': get_gateway_stats(),
        'nodes': get_node_counts(),
        'health': get_system_health(),
        'timestamp': datetime.now().isoformat(),
        'version': __version__
    })


@app.route('/api/services')
@requires_auth
def api_services():
    """Service status only"""
    return jsonify(get_services_status())


@app.route('/api/health')
@requires_auth
def api_health():
    """System health only"""
    return jsonify(get_system_health())


@app.route('/api/gateway')
@requires_auth
def api_gateway():
    """Gateway stats only"""
    return jsonify(get_gateway_stats())


@app.route('/health')
def health_check():
    """Simple health check for load balancers"""
    return jsonify({'status': 'ok', 'version': __version__})


# =============================================================================
# Main
# =============================================================================

def main():
    """Run the web monitor"""
    import argparse

    parser = argparse.ArgumentParser(
        description='MeshForge Web Monitor - Lightweight NOC Dashboard',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
    python3 web_monitor.py                    # Start on port 5000
    python3 web_monitor.py -p 8080            # Custom port
    python3 web_monitor.py --host 127.0.0.1   # Localhost only

Environment variables:
    MESHFORGE_AUTH=true     Enable basic auth
    MESHFORGE_USER=admin    Auth username
    MESHFORGE_PASS=secret   Auth password
'''
    )
    parser.add_argument('-p', '--port', type=int, default=DEFAULT_PORT,
                        help=f'Port to listen on (default: {DEFAULT_PORT})')
    parser.add_argument('--host', default=DEFAULT_HOST,
                        help=f'Host to bind to (default: {DEFAULT_HOST})')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode')

    args = parser.parse_args()

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║           MeshForge Web Monitor v{__version__}               ║
║     Build. Test. Deploy. Bridge. Monitor.                 ║
╠═══════════════════════════════════════════════════════════╣
║  Dashboard: http://{args.host}:{args.port}/
║  API:       http://{args.host}:{args.port}/api/status
║  Health:    http://{args.host}:{args.port}/health
╠═══════════════════════════════════════════════════════════╣
║  Auth: {'Enabled' if AUTH_ENABLED else 'Disabled (set MESHFORGE_AUTH=true)'}
╚═══════════════════════════════════════════════════════════╝
    """)

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == '__main__':
    main()
