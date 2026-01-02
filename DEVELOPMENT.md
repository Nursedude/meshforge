# Meshtasticd Installer - Development Guide

This document contains critical development methods, patterns, and lessons learned for contributing to this project.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [UI Frameworks](#ui-frameworks)
3. [Meshtastic Integration](#meshtastic-integration)
4. [Critical Patterns](#critical-patterns)
5. [Common Pitfalls](#common-pitfalls)

---

## Project Structure

```
src/
├── cli/                    # Rich CLI interface
│   └── meshtastic_cli.py   # Main CLI application
├── tui/                    # Textual TUI interface
│   └── app.py              # Main TUI application
├── gtk_ui/                 # GTK4/libadwaita GUI
│   ├── app.py              # Main GTK application
│   └── panels/             # UI panels
│       ├── dashboard.py    # System status overview
│       ├── radio_config.py # Radio configuration
│       ├── hardware.py     # Hardware detection/config
│       └── ...
├── monitoring/             # Node monitoring module (no sudo required)
│   ├── __init__.py
│   └── node_monitor.py     # Core NodeMonitor class
└── monitor.py              # Entry point for monitoring
```

---

## UI Frameworks

### Textual TUI - @work Decorator

**CRITICAL**: Methods decorated with `@work` must NOT be called with `await`.

The `@work` decorator automatically handles async execution. Calling with `await` causes:
```
TypeError: object Worker can't be used in 'await' expression
```

```python
# ❌ WRONG - Do not await @work decorated methods
@work(exclusive=True)
async def run_meshtastic(self, host, args, output):
    ...

async def some_handler(self):
    await self.run_meshtastic(host, args, output)  # WRONG!

# ✅ CORRECT - Call directly without await
async def some_handler(self):
    self.run_meshtastic(host, args, output)  # Correct!
```

**Location**: `src/tui/app.py`

### GTK4 - Thread-Safe UI Updates

**CRITICAL**: UI updates from background threads MUST use `GLib.idle_add()`.

GTK is not thread-safe. Updating UI elements from background threads causes crashes or undefined behavior.

```python
# ❌ WRONG - Direct UI update from thread
def _fetch_data(self):
    # Running in background thread
    result = subprocess.run(...)
    self.label.set_label(result.stdout)  # WRONG! Crashes or undefined

# ✅ CORRECT - Use GLib.idle_add()
def _fetch_data(self):
    # Running in background thread
    result = subprocess.run(...)
    GLib.idle_add(self._update_label, result.stdout)  # Correct!

def _update_label(self, text):
    self.label.set_label(text)
    return False  # Return False to run only once
```

**Location**: `src/gtk_ui/app.py`, `src/gtk_ui/panels/*.py`

### GTK4 - Delayed Initialization

Use `GLib.timeout_add()` for actions that need UI to be fully ready:

```python
def __init__(self, main_window):
    super().__init__(...)
    self._build_ui()
    # Delay auto-load until UI is ready
    GLib.timeout_add(500, self._auto_load_config)

def _auto_load_config(self):
    self._load_current_config(None)
    return False  # Return False to run only once
```

---

## Meshtastic Integration

### CLI Paths

The meshtastic CLI may be installed in different locations:

```python
CLI_PATHS = [
    '/root/.local/bin/meshtastic',
    '/home/pi/.local/bin/meshtastic',
    '/usr/local/bin/meshtastic',
    '/usr/bin/meshtastic',
    'meshtastic'  # Fallback to PATH
]

def find_meshtastic_cli():
    for path in CLI_PATHS:
        if Path(path).exists() or path == 'meshtastic':
            return path
    return None
```

### CLI Commands

Common meshtastic CLI commands:

```bash
# Connect to local meshtasticd
meshtastic --host localhost

# Get node list
meshtastic --host localhost --nodes

# Get full info
meshtastic --host localhost --info

# Set position (correct format)
meshtastic --host localhost --setlat 19.435175 --setlon -155.213842 --setalt 100

# Reboot device
meshtastic --host localhost --reboot
```

### TCP Interface (Python API)

For sudo-free monitoring, use the meshtastic Python API directly:

```python
from meshtastic.tcp_interface import TCPInterface
from pubsub import pub

def on_receive(packet, interface):
    """Handle received packets"""
    print(f"Received: {packet}")

def on_connection(interface, topic=pub.AUTO_TOPIC):
    """Handle connection events"""
    print(f"Connected to {interface.myInfo}")

# Subscribe to events
pub.subscribe(on_receive, "meshtastic.receive")
pub.subscribe(on_connection, "meshtastic.connection.established")

# Connect (no sudo required!)
interface = TCPInterface(hostname="localhost", portNumber=4403)

# Access node info
my_node = interface.myInfo
nodes = interface.nodes

# Clean up
interface.close()
```

### Configuration Paths

```python
CONFIG_PATHS = {
    'main': Path('/etc/meshtasticd/config.yaml'),
    'config_d': Path('/etc/meshtasticd/config.d'),      # Active configs
    'available_d': Path('/etc/meshtasticd/available.d'), # Available configs
}

# Check for both .yaml and .yml extensions
active_configs = list(config_d.glob('*.yaml')) + list(config_d.glob('*.yml'))
```

---

## Critical Patterns

### Subprocess with sudo

For operations requiring root (hardware config, service control):

```python
# Hardware enable (requires sudo)
subprocess.run(['sudo', 'raspi-config', 'nonint', 'do_spi', '0'], check=True)

# Service control (requires sudo)
subprocess.run(['sudo', 'systemctl', 'restart', 'meshtasticd'], check=True)

# Reading service status (no sudo needed)
subprocess.run(['systemctl', 'is-active', 'meshtasticd'], capture_output=True)
```

### Node Count from CLI

Parse node IDs from meshtastic --nodes output:

```python
import re

result = subprocess.run(
    [cli_path, '--host', 'localhost', '--nodes'],
    capture_output=True, text=True, timeout=15
)

# Extract unique node IDs (format: !xxxxxxxx)
node_ids = re.findall(r'!([0-9a-fA-F]{8})', result.stdout)
unique_nodes = set(node_ids)
node_count = len(unique_nodes)
```

### Uptime Parsing

Handle multiple timestamp formats from journalctl:

```python
import re
from datetime import datetime

def parse_uptime(log_output):
    """Parse service start time from logs"""
    patterns = [
        r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})',  # "Jan 02 15:30:45"
        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', # "2026-01-02 15:30:45"
        r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})',   # ISO format
    ]

    for pattern in patterns:
        match = re.search(pattern, log_output)
        if match:
            # Parse and calculate uptime
            ...
```

---

## Common Pitfalls

### 1. Awaiting @work Methods (Textual)

**Symptom**: `TypeError: object Worker can't be used in 'await' expression`

**Solution**: Remove `await` from calls to `@work` decorated methods.

### 2. UI Updates from Threads (GTK)

**Symptom**: Random crashes, frozen UI, undefined behavior

**Solution**: Always use `GLib.idle_add()` for UI updates from threads.

### 3. Missing sudo for Hardware

**Symptom**: Permission denied errors when enabling SPI/I2C

**Solution**: Use `subprocess.run(['sudo', ...])` for hardware configuration.

### 4. Config File Extensions

**Symptom**: Dashboard shows "0 configs" when configs exist

**Solution**: Check both `.yaml` AND `.yml` extensions.

### 5. CLI Path Not Found

**Symptom**: "meshtastic not found" or FileNotFoundError

**Solution**: Check multiple possible installation paths.

---

## Node Monitoring Module

The `src/monitoring/` module provides sudo-free node monitoring via TCP interface.

### Quick Start

```bash
# Run the monitor (no sudo required!)
python3 -m src.monitor

# Continuous monitoring
python3 -m src.monitor --watch

# JSON output for scripting
python3 -m src.monitor --json

# Connect to remote node
python3 -m src.monitor --host 192.168.1.100
```

### Using NodeMonitor in Code

```python
from src.monitoring import NodeMonitor

# Create and connect
monitor = NodeMonitor(host="localhost", port=4403)

# Set up callbacks (optional)
monitor.on_node_update = lambda node: print(f"Updated: {node.short_name}")
monitor.on_node_added = lambda node: print(f"New node: {node.short_name}")

# Connect
if monitor.connect():
    # Get all nodes
    nodes = monitor.get_nodes()
    for node in nodes:
        print(f"{node.short_name}: {node.metrics.battery_level}%")

    # Get node count
    count = monitor.get_node_count()

    # Get my node
    my_node = monitor.get_my_node()

    # Clean up
    monitor.disconnect()
```

### Key Classes

| Class | Description |
|-------|-------------|
| `NodeMonitor` | Main monitor class with callbacks |
| `NodeInfo` | Complete node information |
| `NodeMetrics` | Telemetry (battery, voltage, etc.) |
| `NodePosition` | GPS position data |
| `ConnectionState` | Enum for connection states |

### Why No Sudo?

The monitoring module uses the meshtastic Python API's TCP interface which:
- Connects to port 4403 (no privileged port)
- Uses user-space networking
- Reads only, no hardware access required

This allows running lightweight monitoring on any user account.

---

## Daemon Mode Pattern

To run GTK applications in background while returning terminal control:

```python
def daemonize():
    """Fork process to run in background and return terminal control"""
    # First fork
    pid = os.fork()
    if pid > 0:
        # Parent exits, returning terminal to user
        print(f"App started in background (PID: {pid})")
        sys.exit(0)

    # Create new session
    os.setsid()

    # Second fork to prevent zombie processes
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # Redirect standard file descriptors to /dev/null
    sys.stdout.flush()
    sys.stderr.flush()
    with open('/dev/null', 'r') as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())
```

**Usage**: `sudo python3 src/main_gtk.py --daemon` or `-d`

**Location**: `src/main_gtk.py`

---

## Parsing Meshtastic CLI JSON Output

**CRITICAL**: The meshtastic CLI outputs JSON in Python dict format (single quotes, True/False).

### Extracting Values from JSON Output

Use direct regex extraction BEFORE fallback line-by-line parsing:

```python
import re
import json

def _parse_radio_info(self, output):
    """Parse --info output correctly"""

    # 1. Try direct regex extraction for JSON values first
    # This handles: { "firmwareVersion": "2.7.15", "hwModel": "PORTDUINO", ... }
    fw_match = re.search(r'"firmwareVersion":\s*"([^"]+)"', output)
    if fw_match:
        firmware = fw_match.group(1)  # Returns "2.7.15"

    hw_match = re.search(r'"hwModel":\s*"([^"]+)"', output)
    if hw_match:
        hardware = hw_match.group(1)  # Returns "PORTDUINO"

    # 2. Try parsing Python dict format JSON blocks
    metadata_match = re.search(r"Metadata:\s*(\{[^}]+\})", output, re.DOTALL)
    if metadata_match:
        try:
            # Convert Python dict format to JSON
            meta_str = metadata_match.group(1)
            meta_str = meta_str.replace("'", '"')
            meta_str = meta_str.replace("True", "true")
            meta_str = meta_str.replace("False", "false")
            meta = json.loads(meta_str)
        except json.JSONDecodeError:
            pass

    # 3. Fallback line-by-line parsing - SKIP JSON lines!
    for line in output.strip().split('\n'):
        # Skip lines that look like JSON
        if line.strip().startswith('{') or '"firmwareVersion"' in line:
            continue

        # Now safe to parse key: value format
        if 'firmware' in line.lower() and 'version' in line.lower():
            match = re.search(r':\s*(.+)', line)
            if match:
                firmware = match.group(1).strip()
```

### Common Parsing Mistakes

1. **JSON in Field Display**: If regex extracts entire JSON line `{ "firmwareVersion": "2.7.15", ... }`
   - Solution: Use specific pattern `r'"key":\s*"([^"]+)"'` to extract just the value

2. **Fallback Overwrites Good Data**: Line-by-line fallback runs after JSON extraction
   - Solution: Check if value already set before fallback: `if self.field.get_label() == "--"`

3. **Python Dict vs JSON**: Meshtastic outputs `{'key': 'value', 'bool': True}`
   - Solution: Replace `'` with `"`, `True/False` with `true/false` before `json.loads()`

**Location**: `src/gtk_ui/panels/radio_config.py`

---

## Version History

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-02 | 3.2.4 | JSON parsing patterns, daemon mode, Connected Radio section |
| 2026-01-02 | 3.2.3 | Added Node Monitoring module, @work decorator patterns |
| 2026-01-02 | 3.2.2 | Initial development guide |

---

## See Also

- [RESEARCH.md](RESEARCH.md) - Technical research and references
- [README.md](README.md) - Project overview and usage
