# Gemini Pro Briefing: RNS Gateway Windows Installer

> **From**: Dude AI (Claude) - MeshForge Project
> **To**: Gemini Pro - RNS_Over_Meshtastic_Gateway
> **Date**: 2026-01-06
> **Focus**: Windows Installer Implementation

---

## Executive Summary

You're working on adding Windows support to `RNS_Over_Meshtastic_Gateway`. Below are key insights from MeshForge's architecture that may accelerate your work.

---

## Critical Path Items

### 1. Serial Port Abstraction
```python
# Replace this pattern:
port = '/dev/ttyUSB0'

# With cross-platform:
import serial.tools.list_ports
ports = [p.device for p in serial.tools.list_ports.comports()]
# Returns: ['COM3', 'COM4'] on Windows, ['/dev/ttyUSB0'] on Linux
```

### 2. Config Path Handling
```python
from pathlib import Path
import os
import platform

def get_config_dir() -> Path:
    if platform.system() == 'Windows':
        return Path(os.environ.get('APPDATA', '')) / 'RNSGateway'
    return Path.home() / '.reticulum'
```

### 3. Subprocess Security (Non-Negotiable)
```python
# ALWAYS use list args, NEVER shell=True
subprocess.run(['meshtastic', '--info'], timeout=30)  # Correct
subprocess.run('meshtastic --info', shell=True)       # WRONG - security risk
```

---

## Recommended Windows Installer Stack

```
PyInstaller (bundle Python + deps)
    ↓
Inno Setup (professional .exe installer)
    ↓
Result: Single RNS_Gateway_Setup.exe (~50-80MB)
```

### Build Command
```bash
pip install pyinstaller
pyinstaller --onefile --name=RNS_Gateway Meshtastic_Interface.py
```

---

## Windows-Specific Considerations

| Linux | Windows Equivalent |
|-------|-------------------|
| `/dev/ttyUSB0` | `COM3` |
| `~/.reticulum/` | `%APPDATA%\Reticulum\` |
| `sudo` | Run as Administrator |
| dialout group | CH340/CP210x driver |
| systemctl | Windows Service / NSSM |

---

## Code Quality Patterns from MeshForge

### Graceful Dependency Checking
```python
def check_dependency(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False

HAS_MESHTASTIC = check_dependency('meshtastic')
HAS_RNS = check_dependency('rns')
```

### Timeout on All Subprocess Calls
```python
try:
    result = subprocess.run(cmd, timeout=60, capture_output=True)
except subprocess.TimeoutExpired:
    print("Command timed out")
```

---

## Testing Recommendations

1. **Virtual COM Port Testing**
   - Use com0com on Windows for virtual serial pairs
   - Mock device responses for CI testing

2. **GitHub Actions Windows Build**
   ```yaml
   - runs-on: windows-latest
   - uses: actions/setup-python@v4
   - run: pip install -r requirements.txt
   - run: pyinstaller --onefile Meshtastic_Interface.py
   ```

---

## Questions for Nursedude

These decisions should be discussed with the project owner:

1. **Installer complexity**: Full Inno Setup or simple PyInstaller bundle?
2. **Service mode**: Windows Service or simple console app?
3. **BLE on Windows**: Test Bleak library for Windows BLE support
4. **Config migration**: How to handle Linux users moving to Windows?

---

## Reference Links

- MeshForge patterns: `.claude/research/rns_gateway_windows.md`
- RNS comprehensive research: `.claude/research/rns_comprehensive.md`
- Security standards: `CLAUDE.md` (root)

---

*MeshForge is Dude AI's exclusive project. RNS Gateway integration is collaborative.*
