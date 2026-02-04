# MeshForge Persistent Issues & Resolution Patterns

> **Purpose**: Document recurring issues and their proper fixes to prevent regression.
> This serves as institutional memory for development.

---

## Issue #1: Path.home() Returns /root with sudo

### Symptom
User config files, logs, and settings created in `/root/.config/meshforge/` instead of `/home/<user>/.config/meshforge/` when MeshForge is run with sudo.

### Root Cause
`Path.home()` returns the current effective user's home directory. When running `sudo python3 src/launcher.py`, the effective user is root, so `Path.home()` returns `/root`.

### Impact
- Settings don't persist between sessions
- Logs go to wrong location
- User sees "file not found" errors
- Features appear "broken" when they work correctly in isolation

### Proper Fix
**ALWAYS use `get_real_user_home()` from `utils/paths.py`** instead of `Path.home()`:

```python
# WRONG - breaks with sudo
from pathlib import Path
config_file = Path.home() / ".config" / "meshforge" / "settings.json"

# CORRECT - works with sudo
from utils.paths import get_real_user_home
config_file = get_real_user_home() / ".config" / "meshforge" / "settings.json"
```

### Files with this pattern (50+ instances as of 2026-01-06)
Many files still use `Path.home()`. Priority fixes completed:
- [x] `utils/paths.py` - Core path utilities (FIXED 2026-01-06)
- [x] `utils/common.py` - CONFIG_DIR, get_data_dir, get_cache_dir (FIXED 2026-01-06)
- [x] `utils/logging_utils.py` - LOG_DIR (FIXED earlier)
- [x] `gtk_ui/panels/hamclock.py` - Settings fallback (FIXED 2026-01-06)

### Prevention
- Use `from utils.paths import get_real_user_home, MeshForgePaths`
- Grep for `Path.home()` before committing
- Add to code review checklist

---

## Issue #2: WebKit Disabled When Running as Root

### Symptom
Embedded web views (HamClock live view) show "Open in Browser" instead of embedded content.

### Root Cause
WebKit uses Chromium's sandbox which refuses to run as root for security reasons. Since MeshForge is launched with `sudo python3 src/launcher.py`, WebKit is always disabled.

### Impact
- HamClock embedded view never works
- Any WebKit-based features fail silently
- Users think features are "broken"

### Proper Fix
1. **Accept the limitation** - WebKit cannot run as root
2. **Provide clear UX feedback** explaining why and offering alternatives
3. **Long-term**: Consider polkit for privileged operations so app runs as user

### Implementation Pattern
```python
_is_root = os.geteuid() == 0

if _is_root:
    # WebKit won't work - explain why and provide alternative
    label = Gtk.Label(label="Embedded view disabled (running as root)")
    label.set_tooltip_text("WebKit cannot run embedded when MeshForge is started with sudo.")
    # Offer "Open in Browser" button
```

### Prevention
- Document this limitation in UI
- Test both as root and as user
- Consider alternative rendering for web content

---

## Issue #3: Services Not Started/Verified

### Symptom
Features dependent on services (rnsd, meshtasticd, HamClock) fail silently because services aren't running.

### Root Cause
Code assumes services are already running instead of checking and providing feedback.

### Examples
- RNS node tracker created but `.start()` never called
- HamClock panel connects but doesn't verify service is running
- Features fail with no actionable feedback

### Proper Fix
1. **Use centralized `check_service()` utility** for pre-flight checks
2. **Provide actionable error messages** with fix hints
3. **Offer fix suggestions** (start service button, installation link)

### Implementation Pattern (Recommended)
```python
# Use the centralized service checker
from utils.service_check import check_service, ServiceState

# Before starting gateway/feature that requires services
def _on_start(self, button):
    status = check_service('meshtasticd')
    if not status.available:
        self._show_error(status.message, status.fix_hint)
        return
    # Proceed with operation...

# Quick port check
from utils.service_check import check_port
if check_port(4403):  # meshtasticd port
    connect_to_meshtasticd()
```

### Known Services (in `utils/service_check.py`)
| Service | Port | systemd name |
|---------|------|--------------|
| meshtasticd | 4403 | meshtasticd |
| rnsd | None | rnsd |
| hamclock | 8080 | hamclock |
| mosquitto | 1883 | mosquitto |

### Legacy Pattern (for reference)
```python
def _on_connection_failed(self, error):
    error_str = str(error).lower()
    if 'connection refused' in error_str:
        self.status_label.set_label("Connection refused - is service running?")
    elif 'name or service not known' in error_str:
        self.status_label.set_label("Host not found - check URL")
```

---

## Issue #4: Silent Debug-Level Logging

### Symptom
Errors occur but user/developer sees no indication because logs are at DEBUG level.

### Root Cause
Over-cautious logging to avoid "spam" means real errors are hidden.

### Proper Fix
Use appropriate log levels:
- **ERROR**: Something broke, needs attention
- **WARNING**: Something unusual, might be a problem
- **INFO**: User-visible operations (connected, saved, etc.)
- **DEBUG**: Internal details for developers

```python
# WRONG - hides important info
logger.debug(f"Connection failed: {error}")

# CORRECT - visible in normal logging
logger.info(f"[Component] Connection failed: {error}")
```

---

## Issue #5: Duplicate Utility Functions

### Symptom
Same fix implemented multiple times in different files, then only some get updated.

### Root Cause
No single source of truth for common utilities.

### Example
`_get_real_user_home()` was defined in:
- `utils/common.py`
- `utils/logging_utils.py`
- `utils/network_diagnostics.py`
- `utils/paths.py`

When one gets fixed, others remain broken.

### Proper Fix
**Single source of truth**: Define once in `utils/paths.py`, import everywhere else.

```python
# In any file needing this utility:
from utils.paths import get_real_user_home

# NOT: def _get_real_user_home(): ...  # local copy
```

---

## Development Checklist

Before committing, verify:

- [ ] No new `Path.home()` calls added (use `get_real_user_home()`)
- [ ] Error messages are actionable, not generic
- [ ] Log levels appropriate (INFO for user actions, ERROR for failures)
- [ ] Services are verified before use (use `check_service()`)
- [ ] subprocess calls have timeout parameters (MF004)
- [ ] Utilities imported from central location, not duplicated

---

## Quick Reference: Import Patterns

```python
# Paths - use these instead of Path.home()
from utils.paths import get_real_user_home, get_real_username
from utils.paths import MeshForgePaths, ReticulumPaths

# Settings
from utils.common import SettingsManager, CONFIG_DIR

# Logging
from utils.logging_utils import get_logger

# Service availability checks - use before service-dependent operations
from utils.service_check import check_service, check_port, ServiceState
```

---

---

## Issue #6: Large Files Exceeding Guidelines

### Symptom
Files exceed the 1,500 line guideline from CLAUDE.md, making them difficult to navigate, test, and maintain.

### Current Status (2026-02-04)

**Python files over 1,500 lines (NEEDS ATTENTION):**

| File | Lines | Priority | Extraction Plan |
|------|-------|----------|-----------------|
| `src/monitoring/traffic_inspector.py` | 2,194 | HIGH | Extract dissectors → `packet_dissectors.py`, models → `traffic_models.py`, storage → `traffic_storage.py` |
| `src/gateway/rns_bridge.py` | 1,991 | HIGH | Extract Meshtastic connection handling → `meshtastic_handler.py` |
| `src/gateway/node_tracker.py` | 1,808 | HIGH | Extract data classes → `node_models.py` (Position, PKIStatus, AirQualityMetrics, HealthMetrics, etc.) |
| `src/launcher_tui/main.py` | 1,799 | HIGH | ⚠️ REGRESSED from 1,336! Extract: network tools → `network_tools_mixin.py`, web client → `web_client_mixin.py` |
| `src/core/diagnostics/engine.py` | 1,767 | LOW | Already has models.py - monitor only |
| `src/utils/metrics_export.py` | 1,762 | MEDIUM | Extract metric definitions → `metric_definitions.py` |
| `src/utils/knowledge_content.py` | 1,688 | LOW | Content file by design - no split needed |
| `src/launcher_tui/rns_menu_mixin.py` | 1,524 | LOW | Near threshold - monitor |

**Regression Alert:**
- `launcher_tui/main.py` grew from 1,336 → 1,799 lines (+463 lines)
- Methods added to main.py instead of mixins:
  - `_ping_test`, `_meshtastic_discovery`, `_dns_lookup` → should be `network_tools_mixin.py`
  - `_open_web_client`, `_launch_web_client_browser`, etc. → should be `web_client_mixin.py`
  - `_data_path_diagnostic` (160 lines) → should be `data_path_mixin.py`

**GTK files removed from tracking (GTK deprecated):**
- `src/gtk_ui/panels/tools.py`, `diagnostics.py`, `app.py`, `hamclock.py`
- GTK4 interface was removed; TUI is now the only interface

**Markdown files over 1,000 lines:**

| File | Lines | Action |
|------|-------|--------|
| `.claude/foundations/persistent_issues.md` | 1,451 | Growing - consider archiving resolved issues |
| `.claude/dude_ai_university.md` | 1,206 | Consider splitting by topic |
| `.claude/foundations/ai_development_practices.md` | 1,069 | Review for outdated content |

### Extraction Priority Order

1. **traffic_inspector.py** (2,194 lines) - Largest, clear extraction boundaries
   - Extract: `traffic_models.py` (enums, PacketField, PacketTree, MeshPacket, HopInfo) ~450 lines
   - Extract: `packet_dissectors.py` (PacketDissector, MeshtasticDissector, RNSDissector) ~500 lines
   - Extract: `traffic_storage.py` (TrafficCapture, TrafficStats, TrafficAnalyzer) ~550 lines
   - Remaining in main: DisplayFilter, TrafficLogger, TrafficInspector, globals ~700 lines

2. **launcher_tui/main.py** (1,799 lines) - Regression fix
   - Extract: `network_tools_mixin.py` (ping, discovery, DNS) ~150 lines
   - Extract: `web_client_mixin.py` (web client, browser, URLs) ~200 lines
   - Extract: `data_path_mixin.py` (_data_path_diagnostic) ~160 lines
   - Target: <1,300 lines

3. **node_tracker.py** (1,808 lines)
   - Extract: `node_models.py` (Position, PKIStatus, AirQualityMetrics, HealthMetrics, Telemetry) ~400 lines
   - Target: ~1,400 lines

4. **rns_bridge.py** (1,991 lines)
   - Extract: `meshtastic_handler.py` (Meshtastic connection/send/receive) ~400 lines
   - Target: ~1,600 lines (or split RNS handler too)

### Proper Fix

Files over 1,500 lines should be split when adding new features to them.
Previously refactored: launcher_tui (extracted 29 mixins), hamclock (extracted API client),
rns.py (extracted config editor + mixins). Web UI and Rich CLI were deleted in consolidation.

### Prevention
- Check file length before adding new features
- Split files proactively at 1,000 lines
- Use `wc -l src/**/*.py | sort -rn | head -10` to monitor
- When adding to launcher_tui/main.py, **always check if a mixin exists or should be created**

---

---

## Issue #7: Missing File References in Launchers

### Symptom
TUI or launcher crashes when selecting a menu option because the referenced file doesn't exist.

### Example
`launcher_tui.py` line 349 referenced `gateway/bridge_cli.py` which didn't exist:
```python
subprocess.run([sys.executable, str(self.src_dir / 'gateway' / 'bridge_cli.py')])
```

### Root Cause
Adding menu options that reference new scripts without creating the scripts first.

### Proper Fix
1. **Create the script before referencing it**
2. **Add verification step**: Check all file references exist before committing
3. **Use commands layer when possible**: Instead of running scripts, use commands module

### Prevention
Run this verification before committing launcher changes:
```bash
# Check all referenced files exist (post-consolidation: 2 UIs only)
for f in src/main_gtk.py src/launcher.py src/launcher_tui/main.py \
         src/standalone.py src/monitor.py; do
  [ -f "$f" ] && echo "OK: $f" || echo "MISSING: $f"
done
```

---

## Issue #8: Outdated Fallback Version Strings

### Symptom
Application shows old version number even after version bump.

### Root Cause
Fallback version strings in try/except blocks don't get updated:
```python
try:
    from __version__ import __version__
except ImportError:
    __version__ = "0.4.3"  # Outdated!
```

### Proper Fix
1. Search for hardcoded version strings when bumping version
2. Use `grep -r "0\.4\." src/` to find all occurrences

### Prevention
```bash
# Before releasing, search for version strings
grep -rn "0\.[0-9]\.[0-9]" src/*.py | grep -v __version__.py
```

---

## Issue #9: Broad Exception Swallowing

### Symptom
Real errors are hidden because `except Exception: pass` catches everything.

### Example
```python
# BAD - hides all errors
try:
    proc.communicate(input=str(percent), timeout=1)
except Exception:
    pass

# GOOD - specific exceptions with explanation
try:
    proc.communicate(input=str(percent), timeout=1)
except (subprocess.TimeoutExpired, OSError):
    # Gauge display timeout - non-critical, UI continues
    pass
```

### Proper Fix
1. **Use specific exception types**
2. **Add comment explaining why silence is acceptable**
3. **Log at DEBUG level if truly non-critical**

### Prevention
- Grep for `except.*:.*pass` before committing
- Code review should flag broad exception handlers

---

---

## Issue #10: Lambda Closure Bug in Loops

### Symptom
Clicking different buttons (like Install buttons for different RNS components) always triggers the action for the **last** item in the loop, not the intended one.

### Root Cause
When creating lambdas inside a `for` loop, the loop variable is captured **by reference**, not by value:

```python
# WRONG - classic closure bug
for component in self.COMPONENTS:
    btn = Gtk.Button(label=component['name'])
    btn.connect("clicked", lambda b: self._install(component))  # All buttons install last component!
```

By the time any button is clicked, `component` has the value from the last iteration.

### Impact
- Wrong component gets installed
- Wrong action gets triggered
- Debugging is confusing because logs show wrong item being processed

### Proper Fix
Use a **default argument** to capture the value at iteration time:

```python
# CORRECT - capture by value using default argument
for component in self.COMPONENTS:
    btn = Gtk.Button(label=component['name'])
    btn.connect("clicked", lambda b, c=component: self._install(c))  # Each button gets its own copy
```

The `c=component` creates a new binding at each iteration, capturing the current value.

### Files Fixed (2026-01-12)
- [x] `src/gtk_ui/panels/rns.py:288` - Component install buttons
- [x] `src/gtk_ui/panels/rns_mixins/components.py:107` - Component install buttons (mixin)

### Prevention
- Search for `lambda.*for.*in` or `connect.*lambda` patterns before committing
- When using lambdas in loops, ALWAYS use default argument pattern
- Code review should flag any `lambda b: self._method(loop_var)` inside loops

---

---

## Issue #11: GTK4/Libadwaita Taskbar Icon Shows Generic

### Symptom
MeshForge shows a generic application icon in the taskbar/dock instead of the custom MeshForge icon, despite multiple fix attempts.

### Root Cause
GTK4/libadwaita has strict requirements for taskbar icons:
1. Icon must be in hicolor theme structure (`~/.local/share/icons/hicolor/scalable/apps/`)
2. Icon filename must match `application_id` (e.g., `org.meshforge.app.svg`)
3. Desktop entry `StartupWMClass` must match the window's actual WM_CLASS
4. Icon cache must be updated after installation
5. Some desktop environments cache icons aggressively

### Attempts Made (2026-01-12)
- [x] Install icon to `~/.local/share/icons/hicolor/scalable/apps/org.meshforge.app.svg`
- [x] Add icon theme search path with `icon_theme.add_search_path()`
- [x] Set `Gtk.Window.set_default_icon_name("org.meshforge.app")`
- [x] Update icon cache with `gtk-update-icon-cache`
- [x] Fix ownership when running as root with sudo
- [ ] **NOT YET TRIED**: Verify WM_CLASS matches StartupWMClass in .desktop file
- [ ] **NOT YET TRIED**: Use GResource to bundle icon into application
- [ ] **NOT YET TRIED**: Check if issue is desktop-environment specific (GNOME vs KDE vs XFCE)

### Files Involved
- `src/launcher_vte.py` - VTE terminal wrapper (main GTK window)
- `src/gtk_ui/app.py` - Main GTK application
- `org.meshforge.app.desktop` - Desktop entry file
- `assets/meshforge-icon.svg` - Source icon

### Next Steps to Try
1. **Verify WM_CLASS**: Run `xprop WM_CLASS` and click the MeshForge window - ensure it matches `org.meshforge.app`
2. **Check GApplication**: Ensure `application_id='org.meshforge.app'` is set correctly in Adw.Application
3. **Try GResource**: Bundle icon as GResource instead of file system installation
4. **Desktop-specific**: Test on different DEs to isolate if it's environment-specific

### Fix (2026-01-13)

Run the desktop integration installer:

```bash
cd /opt/meshforge
sudo ./scripts/install-desktop.sh
```

This installs icons to:
- `/usr/share/icons/hicolor/scalable/apps/org.meshforge.app.svg`
- `/usr/share/icons/hicolor/{48,64,128,256}x{48,64,128,256}/apps/`
- `/usr/share/pixmaps/`

Then clear the icon cache:

```bash
gtk-update-icon-cache -f /usr/share/icons/hicolor
# Log out and back in, or restart desktop environment
```

### If Still Not Working

1. Verify icon installed: `ls /usr/share/icons/hicolor/scalable/apps/org.meshforge.app.svg`
2. Check WM_CLASS: `xprop WM_CLASS` → click MeshForge window → should show `org.meshforge.app`
3. Clear DE cache: Some DEs (GNOME, KDE) cache icons aggressively

---

## Issue #12: RNS "Address Already in Use" When Connecting as Client

### Symptom
GTK crashes or shows errors like:
```
[Error] The interface "Default Interface" could not be created
[Error] The contained exception was: [Errno 98] Address already in use
```

This happens when MeshForge tries to connect to an existing rnsd instance.

### Root Cause
`RNS.Reticulum()` reads the user's `~/.reticulum/config` which defines interfaces (like AutoInterface). Even when connecting to a shared instance, RNS tries to create these interfaces, which fails because rnsd already bound those ports.

### Wrong Fix (documented but not implemented)
The old workaround in `fresh_install_test.md` said to manually edit `~/.reticulum/config` to disable AutoInterface. This requires user intervention and doesn't scale.

### Proper Fix (2026-01-13)
MeshForge now creates a client-only config in `/tmp/meshforge_rns_client/` with:
- `share_instance = Yes`
- No interface definitions

This allows connecting to rnsd without trying to bind ports.

### Location
`src/gateway/node_tracker.py` - `_init_rns_main_thread()` method

### Prevention
- When connecting to shared RNS instances, always use a client-only config
- Never call `RNS.Reticulum()` without a configdir when rnsd is running

---

## Issue #13: Meshtastic CLI Auto-Detection Freezes GTK

### Symptom
GTK UI freezes when checking node count, especially if meshtasticd TCP is not running on port 4403.

### Root Cause
The `_get_node_count()` method in `src/gtk_ui/app.py` calls the meshtastic CLI. Without `--host localhost`, the CLI does USB/serial auto-detection which:
1. Takes 10-15+ seconds per call
2. Blocks threads
3. Causes thread pile-up when status timer fires every 5 seconds
4. Eventually exhausts resources and freezes GTK

### Wrong Approach
```python
# WRONG - causes freeze
result = subprocess.run(
    [cli_path, '--nodes'],  # No --host = auto-detection
    timeout=15  # Too long
)
```

### Proper Fix
1. **Quick port check FIRST** - Skip CLI entirely if port 4403 not reachable
2. **Use --host localhost** - Prevents USB/serial scanning
3. **Short timeout** - 10 seconds max
4. **Cache results** - 30 second TTL prevents rapid CLI calls

```python
# CORRECT pattern
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(1.0)
try:
    sock.connect(("localhost", 4403))
    port_reachable = True
except:
    port_reachable = False
finally:
    sock.close()

if not port_reachable:
    return cached_value  # Don't call CLI at all

# Only call CLI if port is reachable
result = subprocess.run(
    [cli_path, '--host', 'localhost', '--nodes'],
    timeout=10
)
```

### Files Fixed (2026-01-14)
- [x] `src/gtk_ui/app.py` - `_get_node_count()` method
- [x] Added `import shutil` (was missing, caused NameError fallback)

### Regression Tests
- `tests/test_gtk_crash_fixes.py::TestNodeCountThreadSafety`
- `tests/test_gtk_crash_fixes.py::TestNodeCountCodePattern`

### Prevention
- Always use `--host localhost` when calling meshtastic CLI in GUI contexts
- Do quick port checks before expensive subprocess calls
- Keep timeouts shorter than timer intervals to prevent thread pile-up
- Run `test_gtk_crash_fixes.py` to verify patterns are correct

---

## Issue #14: GTK Panel Lifecycle - Missing Cleanup Methods

### Symptom
- GTK freezes or crashes when closing window
- Memory growth during extended use
- Timers continue firing after panels are destroyed
- Orphaned signal handlers causing callbacks on destroyed widgets

### Root Cause
Panel cleanup was fragmented:
1. Only 9 of 24 panels had cleanup() methods
2. app.py used a hard-coded list for cleanup (missed 15 panels)
3. No standardized timer/signal tracking pattern
4. Each panel reinvented resource management

### Impact
- Timer leaks: GLib.timeout_add() handlers never cancelled
- Signal leaks: GTK signals never disconnected
- Thread races: Callbacks firing after widget destruction
- File descriptor exhaustion (errno 24)

### Architectural Fix (2026-01-14)

**1. Created PanelBase class** (`src/gtk_ui/panel_base.py`):
```python
class PanelBase(Gtk.Box):
    def __init__(self, main_window):
        self._pending_timers = []
        self._signal_handlers = {}
        self._is_destroyed = False
        self.connect("unrealize", self._on_unrealize)

    def _schedule_timer(self, delay_ms, callback, *args):
        """Timer tracking with auto-cleanup"""
        timer_id = GLib.timeout_add(delay_ms, callback, *args)
        self._pending_timers.append(timer_id)
        return timer_id

    def _connect_signal(self, widget, signal_name, callback):
        """Signal tracking with auto-cleanup"""
        handler_id = widget.connect(signal_name, callback)
        self._signal_handlers[widget].append(handler_id)
        return handler_id

    def cleanup(self):
        """Auto-called on unrealize"""
        self._cancel_all_timers()
        self._disconnect_all_signals()
```

**2. Auto-discover panels in app.py** (replaces hard-coded list):
```python
def _on_close_request(self, window):
    # Auto-discover ALL panels with cleanup()
    for attr_name in dir(self):
        if attr_name.endswith('_panel'):
            panel = getattr(self, attr_name, None)
            if panel and hasattr(panel, 'cleanup'):
                panel.cleanup()
```

**3. Added cleanup() to all 24 panels**

### Files Changed
- [NEW] `src/gtk_ui/panel_base.py` - PanelBase class with resource management
- [MOD] `src/gtk_ui/app.py` - Auto-discover cleanup
- [MOD] All 24 panel files - Added cleanup() methods

### Regression Tests
- `tests/test_gtk_crash_fixes.py::TestPanelBaseResourceManagement`
- `tests/test_gtk_crash_fixes.py::TestPanelCleanupCoverage`
- `tests/test_gtk_crash_fixes.py::TestAppAutoDiscoverCleanup`
- `tests/test_gtk_crash_fixes.py::TestTimerCleanupPatterns`

### Migration Path for New Panels
New panels should inherit from PanelBase:
```python
from gtk_ui.panel_base import PanelBase

class MyNewPanel(PanelBase):
    def __init__(self, main_window):
        super().__init__(main_window)
        # Use self._schedule_timer() instead of GLib.timeout_add()
        # Use self._connect_signal() instead of widget.connect()

    def cleanup(self):
        # Your cleanup code here
        super().cleanup()  # ALWAYS call parent cleanup
```

### Prevention
- Run `python3 -m unittest tests/test_gtk_crash_fixes.py -v` before releasing
- New panels must inherit from PanelBase or implement cleanup()
- Use `_schedule_timer()` and `_connect_signal()` for resource tracking

---

## Issue #15: GTK Startup Performance - Thundering Herd

### Symptom
- GTK app takes 5-10+ seconds to become responsive after launch
- UI renders but feels sluggish immediately after startup
- Multiple threads spawning simultaneously during initialization
- CPU spikes on startup

### Root Cause (Identified via scientific analysis 2026-01-14)
**Thundering Herd Problem**: All 21+ panels instantiated at startup, each spawning threads:

1. **Panel init triggers async work**: 12+ panels call `_refresh_data()`, `_check_status()`, `_load_*()` in `__init__`
2. **Multiple concurrent timers**: 5+ timers running (1s, 2s, 3s, 5s, 30s intervals)
3. **Status timer overhead**: app.py `_update_status` made 3-5 subprocess calls every 5 seconds
4. **No lazy loading**: Panels instantiated whether user visits them or not

### Quantified Impact
- 21 panels loaded at startup
- 12+ panels spawn threads immediately on `__init__`
- 143 `threading.Thread` calls found in GTK UI code
- 5+ recurring timers competing for CPU
- Each status update: 3-5 subprocess calls with combined 15+ second timeout

### Architectural Fix (2026-01-14)

**1. Lazy Panel Loading** - Only instantiate panels on first navigation:
```python
# Store loaders but don't call them
self._panel_loaders = {"service": self._add_service_page, ...}
self._loaded_panels = set()

# Create lightweight placeholders
for name in self._panel_loaders.keys():
    placeholder = self._create_loading_placeholder(name)
    self.content_stack.add_named(placeholder, name)

# Only load dashboard (default view) at startup
self._load_panel("dashboard")

# Lazy load on navigation
def _on_nav_selected(self, listbox, row):
    if page_name not in self._loaded_panels:
        self._load_panel(page_name)
    self.content_stack.set_visible_child_name(page_name)
```

**2. Deferred Initial Refresh** - Let UI render first:
```python
# WRONG - thread spawns immediately
def __init__(self, main_window):
    self._build_ui()
    self._refresh_data()  # Spawns thread NOW

# CORRECT - defer 500ms
def __init__(self, main_window):
    self._build_ui()
    GLib.timeout_add(500, self._initial_refresh)
```

**3. Optimized Status Timer**:
- Increased interval from 5s to 10s
- Delayed first update by 2 seconds
- Removed redundant subprocess calls (pgrep, socket check)
- Added overlap prevention flag

### Files Changed
- [MOD] `src/gtk_ui/app.py` - Lazy loading, optimized timers
- [MOD] `src/gtk_ui/panels/dashboard.py` - Deferred initial refresh

### Expected Improvement
- Startup time: 5-10s → <2s
- Initial responsiveness: Immediate
- Thread count at startup: 12+ → 1-2
- Subprocess calls at startup: 10+ → 1-2

### Prevention
- New panels must NOT spawn threads in `__init__`
- Use `GLib.timeout_add(500, self._initial_refresh)` for deferred loading
- Prefer single comprehensive status calls over multiple subprocess invocations
- Test startup performance: `time python3 src/launcher.py --gtk`

---

## Issue #16: Gateway Message Routing Reliability

### Symptom
- Messages sent via GTK panel may not reach destination
- Delivery confirmation unreliable
- No clear feedback when gateway is disconnected
- Users report "messages lost" intermittently

### Root Cause (Documented 2026-01-16)
The RNS-Meshtastic gateway bridge has connection and routing limitations:

1. **Gateway connectivity**: Gateway must be running AND connected to both networks
2. **No delivery guarantees**: Meshtastic uses best-effort delivery over LoRa
3. **Node unreachability**: Target nodes may be offline or out of range
4. **Queue overflow**: High message volume can overwhelm limited bandwidth

### Current State
- Message transmission IS implemented (commit `935d37e`)
- Gateway bridge connects RNS and Meshtastic networks
- Delivery status tracking exists but reliability varies
- User testing confirms intermittent failures

### Proper Fix
**Accept reliability limitations** - This is inherent to mesh networking:

```python
# Messaging panel should show clear status
def _send_message(self):
    result = messaging.send_message(dest, text)
    if result.get("status") == "sent":
        self._show_status("Sent (delivery not guaranteed)")
    elif result.get("status") == "queued":
        self._show_status("Queued - gateway not connected")
    else:
        self._show_status(f"Failed: {result.get('error')}")
```

### Files Involved
- `src/commands/messaging.py` - Message sending logic
- `src/gateway/rns_bridge.py` - RNS-Meshtastic bridge (lines 217-237)
- `src/gateway/mesh_bridge.py` - Meshtastic packet handling
- `src/gtk_ui/panels/messaging.py` - UI panel

### Prevention
- Document delivery as "best effort" in UI
- Provide clear gateway status feedback
- Implement retry logic for critical messages
- Consider acknowledgment timeouts
- Test with actual hardware under various conditions

### Testing Gateway
1. Connect Meshtastic node
2. Start gateway bridge: `python3 -c "from gateway import start_gateway; start_gateway()"`
3. Send test message from GTK panel
4. Verify on target node/device

---

## Issue #17: Meshtastic Connection Contention (meshtasticd Single-Client)

### Symptom
- Recurring "Connection reset by peer" and "Broken pipe" errors
- meshtasticd logs show "Force close previous TCP connection" every second
- Gateway connection drops intermittently
- Multiple components competing for TCP connection

### Root Cause (Identified 2026-01-18)
**meshtasticd only supports ONE TCP client at a time.** When multiple components create independent TCP connections:
```
Component A connects → OK
Component B connects → A disconnected by meshtasticd
Component A reconnects → B disconnected
... cycle continues
```

### Impact
- Connection thrashing every 1-2 seconds
- Messages may be lost during reconnection
- Gateway stability compromised
- External tools (Meshtastic Web UI on port 9443) also compete

### Proper Fix (Implemented 2026-01-18)
**Shared connection manager** - All components share ONE persistent connection:

```python
# message_listener.py - Check for existing connection BEFORE creating new one
def _run(self):
    conn_mgr = get_connection_manager(host=self.host)
    if conn_mgr.has_persistent():
        # Another component owns the connection - just subscribe to pub/sub
        self._interface = conn_mgr.get_interface()
        self._owns_connection = False
        logger.info(f"Using existing connection from {conn_mgr.get_persistent_owner()}")
    else:
        # No existing connection - we need to create one
        if conn_mgr.acquire_persistent(owner="message_listener"):
            self._interface = conn_mgr.get_interface()
            self._owns_connection = True
```

### Files Changed
- `src/utils/message_listener.py` - Check for existing persistent connection
- `src/gtk_ui/panels/mesh_tools_nodemap.py` - Use existing gateway connection
- `src/gtk_ui/panels/radio_config_simple.py` - Warning for config operations

### External Interference
**Meshtastic Web UI** on port 9443 can also cause connection spam:
```bash
netstat -tlnp | grep 9443  # Check if Web UI is running
```
User should disable Web UI if not needed, or accept that MeshForge will compete for the connection.

### Prevention
- Always use `get_connection_manager()` instead of creating `TCPInterface` directly
- Check `has_persistent()` before creating connections
- Use `acquire_persistent(owner="component_name")` for long-lived connections
- For short operations, use the existing interface without taking ownership

---

## Issue #18: Meshtastic Auto-Reconnect on Connection Drop

### Symptom
- Gateway stops working after meshtasticd restart
- No automatic recovery from network issues
- User must manually restart MeshForge

### Root Cause
Original implementation had no reconnection logic - once connection dropped, it stayed dropped.

### Proper Fix (Implemented 2026-01-18)
**Health monitoring + exponential backoff reconnect**:

```python
# rns_bridge.py
def _poll_meshtastic(self):
    """Poll Meshtastic for health check"""
    if self._mesh_interface:
        try:
            if hasattr(self._mesh_interface, 'isConnected'):
                if not self._mesh_interface.isConnected:
                    self._handle_connection_lost()
                    return
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.warning(f"Meshtastic connection lost: {e}")
            self._handle_connection_lost()

def _handle_connection_lost(self):
    """Cleanup and prepare for reconnect"""
    self._connected_mesh = False
    if hasattr(self, '_conn_manager') and self._conn_manager:
        self._conn_manager.release_persistent()
    # Clear subscriptions, wait for cooldown

def _meshtastic_loop(self):
    """Main loop with auto-reconnect"""
    reconnect_delay = 1
    max_reconnect_delay = 30
    while self._running:
        if not self._connected_mesh:
            self._connect_meshtastic()
            if self._connected_mesh:
                reconnect_delay = 1  # Reset on success
            else:
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
```

### Files Changed
- `src/gateway/rns_bridge.py` - Health monitoring, auto-reconnect, exponential backoff

### Prevention
- All persistent connections should have health monitoring
- Use exponential backoff (1s → 2s → 4s → ... → 30s max) to avoid hammering
- Release connection manager resources on disconnect

---

## Issue #19: RNS Node Discovery from path_table

### Symptom
- RNS gateway only discovers 2 of 6+ nodes on network
- Nodes visible in `rnstatus` but not in MeshForge
- Node count doesn't match actual network

### Root Cause (Identified 2026-01-18)
MeshForge was only checking `RNS.Transport.destinations` which is limited. The complete routing table is in `RNS.Transport.path_table` which contains ALL destinations rnsd knows about.

### Proper Fix (Implemented 2026-01-18)
**Check path_table first** for complete routing information:

```python
# node_tracker.py
def _load_known_rns_destinations(self, RNS):
    # PRIMARY: Check path_table - contains ALL destinations rnsd knows about
    if hasattr(RNS.Transport, 'path_table') and RNS.Transport.path_table:
        for dest_hash, path_data in RNS.Transport.path_table.items():
            if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                node_id = f"rns_{dest_hash.hex()[:16]}"
                if node_id not in self._nodes:
                    hops = path_data[1] if isinstance(path_data, tuple) and len(path_data) > 1 else 0
                    node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                    self.add_node(node)
                    logger.info(f"[RNS] Discovered node from path_table: {node_id} ({hops} hops)")
```

### Timing Issue
**path_table may be empty immediately after connect** - rnsd syncs data asynchronously:

```python
# Delayed check 5 seconds after connection
GLib.timeout_add(5000, self._delayed_path_table_check)

# Periodic check every 30 seconds in _rns_loop()
if current_time - last_check >= 30:
    self._check_path_table_for_new_nodes()
```

### Files Changed
- `src/gateway/node_tracker.py` - path_table discovery + delayed/periodic checks

### Prevention
- When connecting to shared RNS instances, always check path_table
- Allow time for data sync before assuming empty
- Implement periodic re-checks for dynamic networks

---

*Last updated: 2026-01-23 - Removed stale Textual/Flask/Web UI references after UI consolidation*

---

## Issue #20: Service Detection & Status Display Redesign Required

### Symptom
After multiple fix attempts, these issues persist:
1. **RNS panel shows wrong status** - Lights/indicators show running/stopped incorrectly
2. **Meshtastic detection shows "FAILED"** - Even when meshtasticd service is running and functional
3. **TX works but RX doesn't display** - Messages sent successfully, received messages not shown in UI

### Root Cause Analysis

**Problem 1: Too Many Detection Methods**
Current `service_check.py` uses 3+ fallback methods with conflicting results:
```
UDP port check → pgrep → systemctl is-active → systemctl status
```
Each method can give different answers. When they conflict, UI shows wrong state.

**Problem 2: Conflating "Service Running" with "CLI Detection"**
The meshtastic detection treats CLI failures as service failures:
```
Service: RUNNING (systemctl says active)
CLI:     FAILS (can't connect via meshtastic --export-config)
UI:      Shows "DETECTION FAILED" ← Misleading
```

**Problem 3: No Event System for RX Messages**
Messages flow: `meshtasticd → gateway → logs` but NOT to UI
- TX: User action → API call → works
- RX: Incoming packet → log entry → UI never updated

### Failed Fix Attempts (2026-01-17)
1. Added UDP port check for 0.0.0.0 in addition to 127.0.0.1 - Still fails
2. Improved pgrep with exact match and word boundaries - Still matches incorrectly
3. Added service_running flag to detection result - UI still shows "FAILED"
4. Fixed NodeTracker import (was wrong class name) - Telemetry still doesn't show

### Redesign Specification

#### Component 1: Service Detection (service_check.py)

**Current Architecture (BROKEN):**
```
check_service() {
  if check_udp_port() → return running
  if check_process_running() → return running
  if check_systemd_service() → return running
  return not_running
}
```

**Proposed Architecture:**
```
check_service() {
  # SINGLE SOURCE OF TRUTH for systemd services
  if is_systemd_service(name):
    return systemctl_is_active(name)  # That's it. No fallbacks.

  # Only use port/process for non-systemd services
  return check_port_or_process(name)
}
```

**Rationale:**
- If rnsd/meshtasticd are managed by systemd, trust systemd
- Fallback methods (port check, pgrep) are unreliable
- "Unknown" state is better than wrong state

#### Component 2: Status Display (UI panels)

**Current Architecture (BROKEN):**
```
detection = detect_meshtastic_settings()
if detection is None or detection['preset'] is None:
    show "DETECTION FAILED"  ← Wrong when service runs but CLI unavailable
```

**Proposed Architecture:**
```
# Separate service status from detection capability
service_status = check_service('meshtasticd')
detection = detect_meshtastic_settings()

# Show BOTH states clearly
"Service: Running" or "Service: Stopped"
"Preset: MEDIUM_FAST" or "Preset: Unknown (select manually)"

# Never show "FAILED" when service is running
```

#### Component 3: RX Message Display

**Current Architecture (BROKEN):**
```
gateway.rns_bridge receives packet → logger.info("Received...")
                                   → No UI notification
```

**Proposed Architecture:**
```
# Event-based message notification
class MessageEvent:
    direction: "tx" | "rx"
    content: str
    timestamp: datetime
    node_id: str

# Gateway emits events
gateway.on_message_received(packet):
    event = MessageEvent(direction="rx", ...)
    event_bus.emit("message", event)

# UI subscribes to events
panel.on_init():
    event_bus.subscribe("message", self._on_message)

def _on_message(self, event):
    GLib.idle_add(self._add_message_to_list, event)
```

### Implementation Priority

| Component | Effort | Impact | Priority |
|-----------|--------|--------|----------|
| Service Detection Simplification | LOW | HIGH | 1 - Do first |
| Status Display Separation | MEDIUM | HIGH | 2 |
| RX Message Events | HIGH | MEDIUM | 3 - Requires event bus |

### Files to Modify

**Phase 1: Service Detection**
- `src/utils/service_check.py` - Simplify to systemctl-only for systemd services
- `src/gtk_ui/panels/rns_mixins/components.py` - Use simplified check

**Phase 2: Status Display**
- `src/gtk_ui/panels/rns_mixins/rnode.py` - Separate service/detection display
- `src/utils/lora_presets.py` - Return service_status separately from preset

**Phase 3: RX Messages**
- `src/utils/event_bus.py` - NEW: Simple pub/sub event system
- `src/gateway/rns_bridge.py` - Emit message events
- `src/gtk_ui/panels/messaging.py` - Subscribe to message events

### Prevention
- Don't add more detection fallback methods - simplify instead
- Test with actual hardware in various states (running, stopped, misconfigured)
- UI should always distinguish "service state" from "detection capability"

---

## Issue #21: Meshtastic CLI Preset Settings Not Reliably Applied

### Symptom (Discovered MOC2 2026-01-20)
- User sets modem preset via CLI: `meshtastic --host localhost --set lora.modem_preset SHORT_TURBO`
- CLI reports success
- Browser UI at localhost:9443 shows LONG_FAST (not SHORT_TURBO)
- Other settings (region, owner) apply correctly

### Root Cause
**Upstream meshtastic CLI issue** - The Python meshtastic CLI doesn't always apply preset changes correctly. This is NOT a MeshForge bug.

### Impact
- Users think they're on one preset but actually on another
- Network performance expectations don't match reality
- Slot coordination fails if nodes on different presets

### Workaround
**Always verify in browser** - The Web UI at port 9443 is the source of truth:
1. Apply settings via CLI
2. Verify in browser: `http://localhost:9443`
3. If mismatch, use browser to set correct value

### MeshForge Recommendation
Add verification step after CLI config:

```python
# In device config wizard
def apply_preset(preset_name):
    result = run_meshtastic_cli(['--set', 'lora.modem_preset', preset_name])
    if result.success:
        console.print(f"[yellow]Verify preset in browser: http://localhost:9443[/yellow]")
        console.print("[dim]Note: CLI preset changes may not always apply correctly[/dim]")
```

### Files to Update
- `src/config/radio.py` - Add verification warning
- `src/launcher_tui/main.py` - Add verification step in config wizard
- Documentation - Note the CLI limitation

### Prevention
- Always recommend browser verification after CLI changes
- Consider implementing direct meshtasticd API calls instead of CLI
- Track upstream meshtastic-python issue

---

## Issue #22: MeshForge Overwriting meshtasticd's config.yaml

### Symptom
- Web client (https://localhost:9443) not working
- config.yaml contains radio parameters (Bandwidth, SpreadFactor, TXpower) instead of base config
- User's HAT works but then stops after MeshForge install/update
- "Webserver:" section missing from config.yaml

### Root Cause
MeshForge install scripts and TUI were **overwriting** `/etc/meshtasticd/config.yaml` with our own templates, even when meshtasticd package already provided a valid one.

Multiple places were creating HAT templates in `available.d/` that might conflict with meshtasticd's official templates.

### Impact
- Web client inaccessible (missing Webserver config)
- Users think meshtasticd is broken when it's a config issue
- Radio parameters (Bandwidth, SpreadFactor, TXpower) appear in config.yaml where they shouldn't be
- User has to manually fix config.yaml

### The Correct Architecture

```
/etc/meshtasticd/
├── config.yaml              # Base config (Module: auto, Webserver, Logging)
│                            # PROVIDED BY meshtasticd package - DO NOT OVERWRITE
├── available.d/             # HAT templates (GPIO pins only)
│   ├── lora-MeshAdv-900M30S.yaml
│   ├── lora-waveshare-sxxx.yaml
│   └── ...                  # PROVIDED BY meshtasticd package - DO NOT CREATE OUR OWN
└── config.d/                # User's active HAT config
    └── lora-MeshAdv-900M30S.yaml  # COPIED from available.d/ by user
```

**Radio parameters (Bandwidth, SpreadFactor, TXpower) are:**
- Set via `meshtastic --set lora.modem_preset LONG_TURBO`
- Stored in meshtasticd's internal device database
- **NEVER in yaml files**

### Proper Fix

**In install scripts:**
```bash
# CHECK if config.yaml exists and is valid BEFORE touching it
if [[ -f "$CONFIG_DIR/config.yaml" ]] && grep -q "Webserver:" "$CONFIG_DIR/config.yaml"; then
    echo "Using existing config.yaml from meshtasticd package"
else
    # Only create if missing/empty
    create_minimal_config
fi
```

**In Python code:**
```python
config_yaml = Path('/etc/meshtasticd/config.yaml')

# Check if valid config exists
if config_yaml.exists() and 'Webserver:' in config_yaml.read_text():
    # DO NOT OVERWRITE - meshtasticd provided a good one
    pass
elif not config_yaml.exists():
    # Only create if missing
    create_minimal_config(config_yaml)
```

**MeshForge's job is to:**
1. Help users SELECT their HAT from meshtasticd's available.d/
2. COPY (not create) the HAT config to config.d/
3. NEVER overwrite config.yaml if meshtasticd provided a valid one
4. NEVER create HAT templates - meshtasticd provides them

### Files Fixed (2026-01-22)
- [x] `scripts/install_noc.sh` - Don't overwrite config.yaml, don't create HAT templates
- [x] `src/launcher_tui/main.py` - _fix_spi_config(), _install_native_meshtasticd()
- [x] `templates/config.yaml` - Simplified to minimal base config
- [x] Removed `templates/available.d/` HAT configs (meshtasticd provides these)

### Prevention
- NEVER use `cp templates/config.yaml /etc/meshtasticd/config.yaml` without checking
- NEVER create HAT templates - point users to meshtasticd's available.d/
- Always CHECK for "Webserver:" in existing config before modifying
- Test fresh installs with `apt install meshtasticd` THEN run MeshForge

---

## Issue #23: No Post-Install Verification (Installation Unreliability)

### Symptom
Installation completes "successfully" but:
- meshtasticd doesn't start
- Web client (port 9443) doesn't respond
- Gateway can't connect
- User spends more time troubleshooting than manual install would take

### Root Cause (Identified 2026-01-22)
**No automated verification after installation.** The install script:
1. Installs packages ✓
2. Creates config files ✓
3. Creates systemd services ✓
4. **Does NOT verify anything actually works** ✗

### Impact
- User thinks install succeeded when it didn't
- Silent failures lead to confusion hours later
- MeshForge takes MORE time than manual install (defeats purpose)
- Support burden from "it doesn't work" reports

### The Problem Pattern
```
install_noc.sh runs...
  ✓ meshtasticd package installed
  ✓ config.yaml created
  ✓ systemd service created
  "Installation Complete!"

User runs meshforge...
  ✗ meshtasticd won't start (config invalid)
  ✗ Web client unreachable (Webserver section missing)
  ✗ Gateway fails (no HAT config selected)
```

### Proper Fix (Implemented 2026-01-22)

**1. Created `scripts/verify_post_install.sh`:**
```bash
#!/bin/bash
# Verify MeshForge installation health
# Run after install_noc.sh or anytime to check system state

# Checks performed:
# - meshtasticd binary exists and is executable
# - config.yaml exists and has required sections
# - systemd service can start (or is already running)
# - Web client port 9443 responds
# - At least one radio configured (SPI HAT or USB)
# - RNS installed and rnsd functional
```

**2. Added `meshforge --verify-install` command**

**3. Install script now calls verification automatically:**
```bash
# At end of install_noc.sh:
echo "Verifying installation..."
if bash scripts/verify_post_install.sh; then
    echo "✓ Installation verified successfully"
else
    echo "⚠ Installation needs attention - see above"
fi
```

### Required Verification Checks

| Check | What It Verifies | Failure Action |
|-------|------------------|----------------|
| meshtasticd binary | Native daemon installed | Suggest apt install |
| config.yaml exists | Base config created | Create minimal config |
| Webserver section | Web client will work | Warn, show fix command |
| Port 9443 | Web client responding | Check service status |
| Radio detected | Hardware present | Warn, suggest HAT selection |
| config.d/ populated | HAT config selected (SPI) | Prompt HAT selection |
| rnsd available | RNS tools installed | Suggest pip install rns |
| udev rules | Device permissions correct | Reload udev rules |

### Files Changed
- [NEW] `scripts/verify_post_install.sh` - Comprehensive verification script
- [MOD] `scripts/install_noc.sh` - Call verification at end
- [NEW] `src/commands/verify.py` - Python verification for CLI
- [MOD] `src/launcher.py` - Add --verify-install flag

### Prevention
- ALWAYS run verification after install changes
- CI should test verification on all supported platforms
- Verification failures should be actionable (show how to fix)
- Never mark install "complete" until verification passes

---

## Issue #24: Meshtastic Module Not Found by rnsd (Python Environment Mismatch)

### Symptom
NomadNet or rnsd fails to start with:
```
[Critical] Using this interface requires a meshtastic module to be installed.
[Critical] You can install one with the command: python3 -m pip install meshtastic
```

rnsd repeatedly crashes with exit code 255/EXCEPTION:
```
systemd[1]: rnsd.service: Main process exited, code=exited, status=255/EXCEPTION
```

This happens even when the user has previously installed `meshtastic` via CLI.

### Root Cause
**Python environment mismatch.** The `Meshtastic_Interface.py` plugin in `/etc/reticulum/interfaces/` requires the `meshtastic` Python module. However:

1. **pipx isolation**: Installing meshtastic CLI with `pipx install meshtastic` puts it in an isolated virtual environment that rnsd cannot access
2. **Different Python version**: rnsd may use `/usr/bin/python3` while user installed meshtastic to `/usr/local/bin/python3`
3. **User vs system site-packages**: `pip3 install --user meshtastic` installs to `~/.local/lib/python3.x/` which root's rnsd cannot access

### Impact
- rnsd enters restart loop (every 5 seconds per systemd restart policy)
- NomadNet refuses to launch
- RNS-Meshtastic gateway completely broken
- User thinks system is broken when it's just a module path issue

### Proper Fix

**Option 1: System-wide install (recommended for rnsd)**
```bash
# Install to system site-packages where rnsd can find it
# --break-system-packages required on Debian 12+ / Pi OS Bookworm
# --ignore-installed avoids "Cannot uninstall packaging" errors
sudo pip3 install --break-system-packages --ignore-installed meshtastic
```

**Option 2: Install to same Python that rnsd uses**
```bash
# Check which Python rnsd uses
head -1 $(which rnsd 2>/dev/null || sudo find /usr -name rnsd 2>/dev/null | head -1)

# If rnsd uses /usr/local/bin/python3:
sudo /usr/local/bin/python3 -m pip install --break-system-packages --ignore-installed meshtastic

# If rnsd uses /usr/bin/python3:
sudo /usr/bin/python3 -m pip install --break-system-packages --ignore-installed meshtastic
```

**Option 3: Disable the Meshtastic interface if not needed**
```bash
# Edit RNS config to disable the interface
sudo nano /etc/reticulum/config
# Change 'enabled = yes' to 'enabled = no' under [[Meshtastic Interface]]

# Or remove the interface file entirely
sudo rm /etc/reticulum/interfaces/Meshtastic_Interface.py
sudo systemctl restart rnsd
```

### Diagnosing the Issue
```bash
# Check if meshtastic is importable by root's Python
sudo python3 -c "import meshtastic; print(f'OK: {meshtastic.__version__}')" 2>&1

# If "No module named 'meshtastic'":
# The module is not installed in root's Python path

# Check where meshtastic is installed (if at all)
pip3 show meshtastic 2>/dev/null && echo "User install found"
sudo pip3 show meshtastic 2>/dev/null && echo "System install found"
pipx list 2>/dev/null | grep meshtastic && echo "pipx install found (isolated!)"
```

### Files Involved
- `/etc/reticulum/interfaces/Meshtastic_Interface.py` - The RNS plugin that requires meshtastic
- `/etc/reticulum/config` - RNS configuration referencing the interface
- RNS interface plugin from: https://github.com/landandair/RNS_Over_Meshtastic

### MeshForge Detection
The gateway diagnostic (`src/utils/gateway_diagnostic.py`) should be updated to:
1. Check if Meshtastic_Interface.py exists
2. If it exists, verify meshtastic is importable as root
3. Show specific fix instructions if not

### Prevention
- When installing Meshtastic_Interface plugin, always verify meshtastic module is available
- Add pre-flight check in TUI before enabling RNS-Meshtastic bridge
- Document in installation wizard that meshtastic must be installed system-wide

---
