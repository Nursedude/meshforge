# TUI Architecture Guide

> Code-level documentation for MeshForge's Terminal UI (launcher_tui)

**Date**: 2026-03-09
**Status**: Living Document
**Audience**: Developers working on the TUI codebase

---

## Overview

The TUI is MeshForge's primary interface, using whiptail/dialog for a raspi-config style experience. The architecture uses a **handler registry pattern** where each feature is a self-contained handler class dispatched through a central registry.

### Key Design Decisions

1. **Handler Registry**: Features are self-contained `BaseHandler` subclasses registered in `handler_registry.py` and dispatched by handler ID
2. **TUIContext**: Shared state dataclass passed to all handlers — replaces implicit `self.*` access from the old mixin pattern
3. **CommandHandler Protocol**: Type-safe interface defining `handler_id`, `menu_section`, `menu_items()`, `execute()`
4. **DialogBackend Abstraction**: Whiptail/dialog commands wrapped in a clean Python API
5. **Status Bar**: Persistent service status displayed via `--backtitle`
6. **Startup Checks**: Environment detection and conflict resolution before menu loop

---

## Directory Structure

```
src/launcher_tui/
├── Entry Points
│   ├── __init__.py           # Package exports
│   ├── __main__.py           # python -m launcher_tui
│   └── main.py               # MeshForgeLauncher class + orchestration (~1,160 lines)
│
├── Core Infrastructure
│   ├── handler_protocol.py   # CommandHandler Protocol + TUIContext + BaseHandler
│   ├── handler_registry.py   # HandlerRegistry — register/lookup/dispatch
│   ├── backend.py            # DialogBackend — whiptail/dialog wrapper
│   ├── startup_checks.py     # Environment detection, hardware scan
│   ├── conflict_resolver.py  # Interactive port conflict resolution
│   └── status_bar.py         # Persistent status line
│
├── Handlers (~70 registered classes across 12+ batches)
│   ├── __init__.py               # get_all_handlers() — all imports
│   │
│   ├── Batch 1 — Pilot handlers
│   │   ├── latency.py            # Network latency testing
│   │   ├── classifier.py         # Traffic classifier
│   │   ├── amateur_radio.py      # Ham radio tools
│   │   ├── analytics.py          # Network analytics
│   │   └── rf_tools.py           # RF calculators
│   │
│   ├── Batch 2 — Core features
│   │   ├── node_health.py        # Node health monitoring
│   │   ├── metrics.py            # Historical metrics
│   │   ├── propagation.py        # Space weather & HF propagation
│   │   ├── site_planner.py       # Site planning
│   │   ├── sdr.py                # SDR monitoring
│   │   ├── link_quality.py       # Link analysis
│   │   ├── webhooks.py           # Webhook management
│   │   └── network_tools.py      # Network utilities
│   │
│   ├── Batch 3 — Device & data
│   │   ├── favorites.py          # Favorite nodes
│   │   ├── messaging.py          # Messaging
│   │   ├── aredn.py              # AREDN integration
│   │   ├── rnode.py              # RNode tools
│   │   ├── device_backup.py      # Backup/restore
│   │   ├── logs.py               # Log viewing
│   │   ├── hardware.py           # Hardware detection
│   │   └── service_discovery.py  # Service discovery
│   │
│   ├── Batch 4 — Config & control
│   │   ├── channel_config.py     # Channel configuration
│   │   ├── gateway.py            # Gateway bridge control
│   │   ├── radio_menu.py         # Meshtastic radio menu
│   │   ├── settings.py           # App settings
│   │   ├── meshcore.py           # MeshCore menu
│   │   └── updates.py            # Update management
│   │
│   ├── Batch 5 — Dashboard & ops
│   │   ├── dashboard.py          # Main dashboard
│   │   ├── quick_actions.py      # Single-key shortcuts
│   │   └── emergency_mode.py     # Field operations
│   │
│   ├── Batch 6 — Topology & tactical
│   │   ├── topology.py           # Network topology
│   │   ├── traffic_inspector.py  # Packet capture
│   │   └── tactical_ops.py       # XTOC/ATAK/CoT ops
│   │
│   ├── Batch 7 — RNS (5 sub-handlers + dispatcher)
│   │   ├── rns_config.py         # RNS configuration
│   │   ├── rns_diagnostics.py    # RNS diagnostics
│   │   ├── rns_interfaces.py     # RNS interface management
│   │   ├── rns_monitor.py        # RNS monitoring
│   │   ├── rns_sniffer.py        # RNS packet sniffer
│   │   └── rns_menu.py           # RNS menu dispatcher
│   │
│   ├── Batch 8 — Services & MQTT
│   │   ├── service_menu.py       # Service management
│   │   ├── mqtt.py               # MQTT monitoring
│   │   ├── broker.py             # Broker management
│   │   └── web_client.py         # Web client
│   │
│   ├── Batch 9 — AI & system
│   │   ├── ai_tools.py           # AI assistant
│   │   ├── auto_review.py        # Self-audit
│   │   ├── system_tools.py       # System utilities
│   │   ├── nomadnet.py           # NomadNet client
│   │   └── first_run.py          # Setup wizard
│   │
│   ├── Batch 10 — Meshtasticd
│   │   ├── meshtasticd_config.py   # Config management
│   │   ├── meshtasticd_radio.py    # Radio settings
│   │   ├── meshtasticd_lora.py     # LoRa parameters
│   │   ├── meshtasticd_mqtt.py     # Device MQTT
│   │   └── meshtasticd_nodedb.py   # Node database
│   │
│   ├── Batch 11 — QA cleanup
│   │   ├── about.py              # About screen
│   │   ├── daemon.py             # Daemon control
│   │   ├── reboot.py             # Reboot handler
│   │   ├── diagnostics.py        # Diagnostics
│   │   └── config_api.py         # Config API
│   │
│   ├── Batch 12 — Automation & load balancing
│   │   ├── automation.py         # Auto-ping, auto-traceroute, auto-welcome
│   │   └── load_balancer.py      # TX load balancer (dual-radio)
│   │
│   └── Utility Modules (not handlers)
│       ├── _lxmf_utils.py            # LXMF port conflict detection
│       ├── _nomadnet_rns_checks.py   # NomadNet/RNS pre-flight checks
│       ├── _rns_diagnostics_engine.py # RNS diagnostic engine
│       ├── _rns_interface_mgr.py     # RNS interface manager
│       └── _rns_repair.py           # RNS repair utilities
```

---

## Handler Registry Pattern

### TUIContext (`handler_protocol.py`)

Shared state dataclass created once in `MeshForgeLauncher.__init__()`:

```python
@dataclass
class TUIContext:
    dialog: DialogBackend          # UI abstraction
    env_state: Optional[EnvironmentState]  # From startup checks
    startup_checker: Optional[StartupChecker]
    status_bar: Optional[StatusBar]
    feature_flags: dict            # Deployment profile flags
    profile: Optional[Any]         # Active profile
    src_dir: Path                  # src/ directory
    env: dict                      # Environment detection
    registry: Optional[HandlerRegistry]  # Back-reference
    daemon_active: bool            # meshforged ownership

    # Utility methods
    def feature_enabled(feature: str) -> bool  # Profile check
    def wait_for_enter(msg: str) -> None       # Pause + clear
    def get_meshtastic_cli() -> str            # CLI path (cached)
    def validate_hostname(host: str) -> bool   # Input validation
    def validate_port(port_str: str) -> bool   # Port validation
    def log_error(context, exc) -> None        # Error logging
```

### CommandHandler Protocol

```python
@runtime_checkable
class CommandHandler(Protocol):
    handler_id: str                                    # Unique identifier
    menu_section: str                                  # Menu grouping
    def menu_items(self) -> List[Tuple[str, str]]: ... # (tag, description)
    def execute(self, action: str) -> None: ...        # Dispatch action
```

### BaseHandler

Concrete base class implementing the Protocol with TUIContext access:

```python
class BaseHandler:
    handler_id: str = ""
    menu_section: str = ""

    def __init__(self):
        self.ctx: Optional[TUIContext] = None  # Set by registry

    def bind(self, ctx: TUIContext) -> None:
        self.ctx = ctx

    def menu_items(self) -> List[Tuple[str, str]]:
        return []

    def execute(self, action: str) -> None:
        pass
```

### HandlerRegistry (`handler_registry.py`)

Central dispatch:

```python
class HandlerRegistry:
    def register(self, handler: BaseHandler) -> None
    def get_handler(self, handler_id: str) -> Optional[BaseHandler]
    def get_menu_items(self, section: str) -> List[Tuple[str, str]]
    def dispatch(self, handler_id: str, action: str) -> None
```

### Why Handler Registry?

- **No MRO complexity**: The old 49-mixin chain caused Python MRO conflicts and made state debugging painful
- **Self-contained handlers**: Each handler is a complete unit with `handler_id`, `menu_section`, `menu_items()`, `execute()`
- **Explicit state sharing**: `TUIContext` makes dependencies visible — no hidden `self.*` coupling
- **Easy to add/remove**: Add a handler class, import in `__init__.py`, done
- **Testable**: Handlers can be tested in isolation with a mock `TUIContext`

---

## Core Components

### DialogBackend (`backend.py`)

Terminal UI abstraction for whiptail/dialog:

```python
class DialogBackend:
    def menu(self, title, text, choices) -> Optional[str]   # Selection menu
    def msgbox(self, title, text) -> None                   # Info message
    def yesno(self, title, text, default_no=False) -> bool  # Confirmation
    def inputbox(self, title, text, init="") -> Optional[str]  # Text input
    def infobox(self, title, text) -> None                  # Transient message
    def set_status_bar(self, status_bar) -> None            # Attach status bar
```

**Implementation Details**:
- Detects whiptail (preferred) or dialog at runtime
- Redirects stderr to temp file to capture selection
- 3600s timeout (allows long interactive sessions)
- Status bar injected as `--backtitle` on every dialog

### StartupChecker (`startup_checks.py`)

Environment detection at launch:

```python
@dataclass
class EnvironmentState:
    services: Dict[str, ServiceInfo]   # meshtasticd, rnsd, mosquitto
    conflicts: List[PortConflict]      # Port usage conflicts
    hardware: HardwareInfo             # SPI/I2C/USB/GPIO
    is_root: bool
    has_display: bool
    is_ssh: bool
    is_first_run: bool
    config_exists: bool
```

### StatusBar (`status_bar.py`)

Persistent status shown at top of every dialog:

```
MeshForge v0.5.7 | meshtasticd: ● | rnsd: ○ | mqtt: ○ | Conflicts: 0
```

---

## Entry Flow

```
python -m launcher_tui
    ↓
__main__.py → main.main()
    ↓
MeshForgeLauncher()
    ├── __init__()
    │   ├── self.dialog = DialogBackend()
    │   ├── self._setup_status_bar()
    │   ├── self._startup_checker = StartupChecker()
    │   ├── self.env = self._detect_environment()
    │   ├── ctx = TUIContext(dialog, env_state, ...)
    │   └── registry = HandlerRegistry()
    │       └── for handler in get_all_handlers():
    │               handler.bind(ctx)
    │               registry.register(handler)
    │
    └── run()
        ├── Check root privilege (exit if not)
        ├── Check dialog available (fallback if not)
        ├── _run_startup_checks()
        ├── _check_first_run()
        ├── _check_service_misconfig()
        ├── _maybe_auto_start_map()
        └── _run_main_menu()  ← Main loop (dispatches to handlers)
```

---

## Handler Pattern

### Anatomy of a Handler

```python
"""My Feature handler for MeshForge TUI."""

from handler_protocol import BaseHandler


class MyFeatureHandler(BaseHandler):
    """Provides my feature functionality."""

    handler_id = "my_feature"
    menu_section = "tools"  # Groups with other tool handlers

    def menu_items(self):
        return [
            ("my_action1", "First action description"),
            ("my_action2", "Second action description"),
        ]

    def execute(self, action):
        if action == "my_action1":
            self._handle_action1()
        elif action == "my_action2":
            self._handle_action2()

    def _handle_action1(self):
        """Implements specific action."""
        result = self.ctx.dialog.inputbox("Input", "Enter value:", "default")
        if result:
            self.ctx.dialog.msgbox("Success", f"You entered: {result}")

    def _handle_action2(self):
        """Another action using TUIContext."""
        self.ctx.dialog.msgbox("Action 2", "You selected action 2")
```

### Available via TUIContext (`self.ctx`)

All handlers access shared state through `self.ctx`:

```python
# UI
self.ctx.dialog                    # DialogBackend instance
self.ctx.wait_for_enter(msg)       # Pause for user after terminal output

# Validation
self.ctx.validate_hostname(host)   # True if valid hostname
self.ctx.validate_port(port_str)   # True if 1-65535

# Environment
self.ctx.env_state                 # Current EnvironmentState
self.ctx.startup_checker           # StartupChecker instance
self.ctx.env                       # Environment dict
self.ctx.feature_enabled("rns")    # Profile feature check

# Paths
self.ctx.src_dir                   # src/ directory path

# CLI
self.ctx.get_meshtastic_cli()      # Get meshtastic CLI path (cached)

# Registry
self.ctx.registry                  # HandlerRegistry (for cross-handler dispatch)

# Error logging
self.ctx.log_error(context, exc)   # Write to TUI error log
```

---

## Common Patterns

### 1. Submenu Loop Pattern

Handlers that present their own submenu:

```python
def execute(self, action):
    if action == "my_submenu":
        self._my_submenu()

def _my_submenu(self):
    while True:
        choice = self.ctx.dialog.menu("Title", "Subtitle", [
            ("action1", "Do something"),
            ("action2", "Do another thing"),
            ("back", "Back"),
        ])
        if choice is None or choice == "back":
            break
        elif choice == "action1":
            self._action1()
```

### 2. Terminal Output Pattern

For commands that produce text output:

```python
def _show_logs(self):
    subprocess.run(['clear'], check=False, timeout=5)
    print("=== Recent Logs ===\n")
    subprocess.run(['journalctl', '-u', 'meshtasticd', '-n', '50'], timeout=30)
    self.ctx.wait_for_enter("Press Enter to continue...")
```

### 3. Cross-Handler Dispatch

Handlers can invoke other handlers via the registry:

```python
def _delegate_to_rns(self):
    handler = self.ctx.registry.get_handler("rns_diagnostics")
    if handler:
        handler.execute("run_diagnostics")
```

### 4. Error Handling with Fallback

```python
def _optional_feature(self):
    try:
        from utils.optional_module import feature
        feature()
    except ImportError:
        self.ctx.dialog.msgbox("Unavailable", "Feature requires optional dependency")
```

### 5. Subprocess with Timeout

Always include timeouts:

```python
result = subprocess.run(
    ['meshtastic', '--info'],
    capture_output=True,
    text=True,
    timeout=30  # REQUIRED
)
```

---

## Adding New Features

### Step 1: Create Handler File

Create `src/launcher_tui/handlers/my_feature.py` with handler class extending `BaseHandler`.

### Step 2: Register in `__init__.py`

Add import to appropriate batch in `handlers/__init__.py`:

```python
# In the appropriate batch section:
from handlers.my_feature import MyFeatureHandler
handlers.append(MyFeatureHandler)
```

The handler auto-registers via `get_all_handlers()` at startup.

### Step 3: Wire to Menu

Set `menu_section` to match an existing section (e.g., `"tools"`, `"network"`, `"config"`) or create a new one. The main menu in `main.py` queries handlers by section.

---

## Security Considerations

### Input Validation

```python
# Use TUIContext validation methods
if not self.ctx.validate_hostname(host):
    self.ctx.dialog.msgbox("Error", "Invalid hostname")
    return
```

### Path Security

Always use `get_real_user_home()` instead of `Path.home()`:

```python
from utils.paths import get_real_user_home
config = get_real_user_home() / ".config" / "meshforge"
```

### Subprocess Safety

Never use `shell=True`:

```python
# CORRECT
subprocess.run(['meshtastic', '--info', node_id], timeout=30)

# WRONG - command injection risk
subprocess.run(f'meshtastic --info {node_id}', shell=True)
```

---

## Testing

### Running Tests

```bash
python3 -m pytest tests/ -v
```

### Mocking TUIContext for Handler Tests

```python
from unittest.mock import MagicMock
from handler_protocol import TUIContext, BaseHandler

def test_my_handler():
    ctx = MagicMock(spec=TUIContext)
    ctx.dialog = MagicMock()
    ctx.dialog.menu.return_value = "action1"

    handler = MyFeatureHandler()
    handler.bind(ctx)
    handler.execute("action1")

    ctx.dialog.msgbox.assert_called_once()
```

---

## File Size Guidelines

Per `.claude/foundations/persistent_issues.md`, files should stay under 1,500 lines:

| File | Target | Purpose |
|------|--------|---------|
| main.py | <1,500 | Menu orchestration + startup |
| handlers/*.py | <500 | Single handler implementation |
| backend.py | <300 | Dialog abstraction |
| handler_protocol.py | <200 | Protocol + TUIContext |
| handler_registry.py | <150 | Registry dispatch |

When a handler exceeds 500 lines, split into:
- Handler file (menu + dispatch)
- Underscore-prefixed utility module (e.g., `_rns_diagnostics_engine.py`)

---

## Related Documents

- `.claude/research/tui_menu_redesign.md` - UI/UX design research
- `.claude/foundations/noc_architecture.md` - High-level NOC architecture
- `.claude/foundations/domain_architecture.md` - Core vs Plugin model
- `.claude/foundations/persistent_issues.md` - Known issues (Path.home() bug, etc.)

---

*Architecture maintained by the mesh community*
