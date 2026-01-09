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

*Last updated: 2026-01-09 - Added service_check utility documentation and usage patterns*
