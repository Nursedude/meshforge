#!/usr/bin/env python3
"""
Meshtasticd Web-Based Installer
A simple web interface for installing meshtasticd
Run: sudo python3 web_installer.py
Then visit: http://<raspberry-pi-ip>:8080
"""

import os
import sys
import subprocess
import json
import logging
import socket
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import threading

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/meshtasticd-web-installer.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('web_installer')

# Check root
if os.geteuid() != 0:
    print("Error: This script must be run as root")
    print("Please run: sudo python3 web_installer.py")
    sys.exit(1)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Meshtasticd Interactive Installer</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            padding: 40px;
            max-width: 800px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        h1 {
            color: #667eea;
            margin-bottom: 10px;
            font-size: 2.5em;
            text-align: center;
        }
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
            font-size: 1.1em;
        }
        .system-info {
            background: #f7f9fc;
            border-left: 4px solid #667eea;
            padding: 15px;
            margin-bottom: 30px;
            border-radius: 5px;
        }
        .system-info h3 {
            color: #667eea;
            margin-bottom: 10px;
        }
        .install-option {
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            padding: 25px;
            margin-bottom: 20px;
            cursor: pointer;
            transition: all 0.3s;
        }
        .install-option:hover {
            border-color: #667eea;
            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.2);
            transform: translateY(-2px);
        }
        .install-option h3 {
            color: #333;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
        }
        .install-option h3::before {
            content: "→";
            margin-right: 10px;
            color: #667eea;
            font-weight: bold;
        }
        .install-option p {
            color: #666;
            line-height: 1.6;
        }
        .install-option .command {
            background: #2d3748;
            color: #a0aec0;
            padding: 10px 15px;
            border-radius: 5px;
            font-family: 'Courier New', monospace;
            margin-top: 10px;
            font-size: 0.9em;
        }
        .btn {
            background: #667eea;
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 1.1em;
            width: 100%;
            margin-top: 10px;
            transition: background 0.3s;
            font-weight: 600;
        }
        .btn:hover {
            background: #5568d3;
        }
        .btn-secondary {
            background: #48bb78;
        }
        .btn-secondary:hover {
            background: #38a169;
        }
        .status {
            background: #f7fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 20px;
            margin-top: 20px;
            display: none;
        }
        .status.active {
            display: block;
        }
        .status h3 {
            color: #667eea;
            margin-bottom: 15px;
        }
        .status pre {
            background: #2d3748;
            color: #a0aec0;
            padding: 15px;
            border-radius: 5px;
            overflow-x: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.85em;
            max-height: 400px;
            overflow-y: auto;
        }
        .spinner {
            border: 3px solid #f3f3f3;
            border-top: 3px solid #667eea;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 20px auto;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .badge {
            background: #667eea;
            color: white;
            padding: 5px 10px;
            border-radius: 15px;
            font-size: 0.8em;
            margin-left: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ Meshtasticd Installer</h1>
        <p class="subtitle">Interactive Installation for Raspberry Pi OS</p>

        <div class="system-info">
            <h3>📋 System Information</h3>
            <div id="sysinfo">Loading...</div>
        </div>

        <div class="install-option" onclick="installStable()">
            <h3>Stable Installation <span class="badge">Recommended</span></h3>
            <p>Install the latest stable version of meshtasticd with all dependencies. Best for production use.</p>
            <button class="btn btn-secondary">Install Stable Version</button>
        </div>

        <div class="install-option" onclick="installBeta()">
            <h3>Beta Installation</h3>
            <p>Install the latest beta version with cutting-edge features. For testing and development.</p>
            <button class="btn">Install Beta Version</button>
        </div>

        <div class="install-option" onclick="showManual()">
            <h3>Manual Installation</h3>
            <p>Copy and paste these commands to install manually:</p>
            <div class="command">
# Quick install (one-liner)<br>
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo bash<br>
<br>
# Or manual clone and install<br>
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git<br>
cd Meshtasticd_interactive_UI<br>
sudo bash install.sh
            </div>
        </div>

        <div class="status" id="status">
            <h3>📦 Installation Progress</h3>
            <div class="spinner"></div>
            <pre id="output">Starting installation...</pre>
        </div>
    </div>

    <script>
        // Load system info
        fetch('/api/sysinfo')
            .then(r => r.json())
            .then(data => {
                document.getElementById('sysinfo').innerHTML = `
                    <strong>OS:</strong> ${data.os}<br>
                    <strong>Architecture:</strong> ${data.arch}<br>
                    <strong>Python:</strong> ${data.python}
                `;
            });

        function installStable() {
            install('stable');
        }

        function installBeta() {
            install('beta');
        }

        function install(version) {
            const status = document.getElementById('status');
            const output = document.getElementById('output');
            status.classList.add('active');
            output.textContent = `Installing ${version} version...\\n`;

            fetch(`/api/install?version=${version}`)
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        output.textContent += '\\n✓ Installation completed successfully!\\n';
                        output.textContent += '\\nNext steps:\\n';
                        output.textContent += '1. Run: sudo meshtasticd-installer\\n';
                        output.textContent += '2. Or configure: sudo meshtasticd-installer --configure\\n';
                    } else {
                        output.textContent += '\\n✗ Installation failed:\\n' + data.error;
                    }
                })
                .catch(err => {
                    output.textContent += '\\n✗ Error: ' + err;
                });
        }

        function showManual() {
            alert('Manual installation commands are shown above. Copy and paste them into your terminal.');
        }
    </script>
</body>
</html>
"""

class InstallerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """Log HTTP requests using our logger"""
        logger.debug("%s - - [%s] %s" % (self.address_string(),
                                          self.log_date_time_string(),
                                          format % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        logger.debug(f"GET request: {parsed.path}")

        if parsed.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode())

        elif parsed.path == '/api/sysinfo':
            import platform
            info = {
                'os': f"{platform.system()} {platform.release()}",
                'arch': platform.machine(),
                'python': platform.python_version()
            }
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(info).encode())

        elif parsed.path.startswith('/api/install'):
            params = parse_qs(parsed.query)
            version = params.get('version', ['stable'])[0]
            logger.info(f"Installation requested via web interface (version: {version})")

            try:
                # Run installation in background
                script_path = os.path.join(os.path.dirname(__file__), 'install.sh')
                if os.path.exists(script_path):
                    logger.debug(f"Starting installation script: {script_path}")
                    subprocess.Popen(['bash', script_path],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   start_new_session=True)
                    result = {'success': True, 'message': 'Installation started'}
                    logger.info("Installation started successfully")
                else:
                    logger.error(f"Installation script not found: {script_path}")
                    result = {'success': False, 'error': 'install.sh not found'}

            except Exception as e:
                logger.exception(f"Installation failed: {e}")
                result = {'success': False, 'error': str(e)}

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        else:
            self.send_error(404)

def check_port_available(host: str, port: int) -> tuple:
    """
    Check if a port is available for binding.

    Returns:
        (is_available, process_info) - process_info is populated if port is in use
    """
    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test_socket.bind((host, port))
        test_socket.close()
        return (True, None)
    except OSError:
        test_socket.close()
        # Try to identify what's using the port
        process_info = None
        try:
            result = subprocess.run(
                ['lsof', '-i', f':{port}', '-t'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                pid = result.stdout.strip().split('\n')[0]
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


def find_available_port(host: str, start_port: int, max_tries: int = 20) -> int:
    """Find an available port starting from start_port."""
    for offset in range(max_tries):
        port = start_port + offset
        is_available, _ = check_port_available(host, port)
        if is_available:
            return port
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Meshtasticd Web-Based Installer',
        epilog='''
Examples:
  sudo python3 web_installer.py                # Default port 8080
  sudo python3 web_installer.py --port 9000    # Custom port
  sudo python3 web_installer.py --auto-port    # Auto-find available port
  sudo python3 web_installer.py --list-ports   # Show what's using common ports

Environment variables:
  WEB_INSTALLER_PORT=9000  # Set default port
'''
    )
    parser.add_argument('--port', '-p', type=int,
                        default=int(os.environ.get('WEB_INSTALLER_PORT', '8080')),
                        help='Port to listen on (default: 8080, env: WEB_INSTALLER_PORT)')
    parser.add_argument('--host', default='0.0.0.0',
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--auto-port', action='store_true',
                        help='Automatically find an available port if default is in use')
    parser.add_argument('--list-ports', action='store_true',
                        help='List what processes are using common ports and exit')
    args = parser.parse_args()

    # Handle --list-ports
    if args.list_ports:
        print("Checking common ports...")
        ports_to_check = [8080, 8081, 4403, 8880, 5000]
        for p in ports_to_check:
            is_available, process_info = check_port_available(args.host, p)
            if is_available:
                print(f"  Port {p}: Available")
            else:
                print(f"  Port {p}: In use by {process_info or 'unknown process'}")
        sys.exit(0)

    host = args.host
    port = args.port

    # Check if port is available before starting
    is_available, process_info = check_port_available(host, port)
    if not is_available:
        if args.auto_port:
            # Try to find an available port
            print(f"Port {port} is in use, searching for available port...")
            new_port = find_available_port(host, port + 1, max_tries=20)
            if new_port:
                print(f"Found available port: {new_port}")
                port = new_port
            else:
                print(f"ERROR: Could not find available port in range {port+1}-{port+20}")
                sys.exit(1)
        else:
            print()
            print("=" * 60)
            print(f"ERROR: Port {port} is already in use")
            print("=" * 60)
            if process_info:
                print(f"Process using port: {process_info}")
            else:
                print("Could not identify process using the port.")
                print(f"Check with: sudo lsof -i :{port}")
            print()
            print("Note: Port 8080 is commonly used by AREDN web UI or HamClock API")
            print()
            print("Options:")
            print(f"  --port 9000      Use a specific port")
            print(f"  --auto-port      Auto-find an available port")
            print(f"  --list-ports     Show what's using common ports")
            print("=" * 60)
            sys.exit(1)

    logger.info(f"Starting web installer on {host}:{port}")
    server = HTTPServer((host, port), InstallerHandler)

    print("╔═══════════════════════════════════════════════════════════╗")
    print("║   Meshtasticd Web-Based Installer                        ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()
    print(f"🌐 Web installer running at:")
    print(f"   http://localhost:{port}")

    # Try to get local IP
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print(f"   http://{local_ip}:{port}")
        logger.debug(f"Local IP address: {local_ip}")
    except (OSError, socket.error) as e:
        # Network unavailable or connection failed - skip showing local IP
        logger.debug(f"Could not determine local IP address: {e}")

    print()
    print("📱 Open this URL in your browser to start installation")
    print("Press Ctrl+C to stop the server")
    print()

    try:
        logger.info("Web installer server started, serving requests")
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped by user (Ctrl+C)")
        print("\n\n✓ Server stopped")
        sys.exit(0)

if __name__ == '__main__':
    main()
