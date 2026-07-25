#!/usr/bin/env python3
"""
MeshForge Self-Test Script

Quick diagnostic to verify installation and dependencies.

Usage:
    python3 scripts/selftest.py
    python3 scripts/selftest.py --verbose
"""

import sys
import os
import re
import socket
from pathlib import Path

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
NC = '\033[0m'  # No Color


def ok(msg):
    print(f"  {GREEN}✓{NC} {msg}")


def fail(msg):
    print(f"  {RED}✗{NC} {msg}")


def warn(msg):
    print(f"  {YELLOW}!{NC} {msg}")


def info(msg):
    print(f"  {CYAN}i{NC} {msg}")


def check_python_version():
    """Check Python version is 3.9+"""
    version = sys.version_info
    if version.major >= 3 and version.minor >= 9:
        ok(f"Python {version.major}.{version.minor}.{version.micro}")
        return True
    else:
        fail(f"Python {version.major}.{version.minor} (need 3.9+)")
        return False


def check_import(module_name, package_name=None):
    """Check if a module can be imported."""
    try:
        __import__(module_name)
        ok(f"{package_name or module_name}")
        return True
    except ImportError:
        fail(f"{package_name or module_name} not installed")
        return False


# Distribution package name -> the module you actually import. Only needed
# where the two differ; anything absent maps to itself.
_PKG_TO_MODULE = {
    "pyyaml": "yaml",
    "pyserial": "serial",
    "pycryptodomex": "Cryptodome",
    "paho-mqtt": "paho.mqtt.client",
    "pillow": "PIL",
}

# Fallback if requirements/core.txt cannot be read. Deliberately the modules
# the codebase imports UNCONDITIONALLY — never a guess, and never wider than
# the file it stands in for.
_CORE_FALLBACK = [("rich", "rich"), ("yaml", "pyyaml"), ("requests", "requests")]


def core_dependencies(repo_root=None):
    """``[(module, package), ...]`` derived from ``requirements/core.txt``.

    The core dependency set has exactly one owner — ``requirements/core.txt``,
    the file the installer and CI both consume. This selftest used to carry its
    OWN hardcoded list, and by 2026-07-24 the two had drifted in both
    directions: it demanded ``flask`` and ``textual`` (imported NOWHERE in the
    tree, declared in no requirements file) while never checking ``distro``, and
    it filed ``meshtastic`` — which core.txt DOES declare — under "optional".
    A core dep nothing imports is worse than useless: it fails a healthy box on
    a package MeshForge does not use, which is how a green check gets ignored.
    That is honest_failure_modes #5 (two consumers of one artifact, two
    constants) in the install layer, so the cure is the same one the cron-verdict
    panel got — derive, don't restate.

    Unreadable/absent file -> ``_CORE_FALLBACK`` + a warning from the caller.
    Degrading to a NARROWER list is the safe direction: it can under-report a
    missing dep, but it can never invent a failure for a package we do not use.
    """
    root = Path(repo_root) if repo_root else Path(__file__).parent.parent
    req = root / "requirements" / "core.txt"
    try:
        text = req.read_text(encoding="utf-8")
    except OSError:
        return None  # caller decides how loudly to say "unverified"

    deps = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # strip version specifiers / extras: "meshtastic>=2.7.9", "x[extra]==1"
        pkg = re.split(r"[<>=!~\[;\s]", line, 1)[0].strip().lower()
        if not pkg:
            continue
        deps.append((_PKG_TO_MODULE.get(pkg, pkg.replace("-", "_")), pkg))
    return deps or None


def check_port(host, port, service_name):
    """Check if a port is listening."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            ok(f"{service_name} (port {port})")
            return True
        else:
            warn(f"{service_name} not running (port {port})")
            return False
    except Exception as e:
        warn(f"{service_name} check failed: {e}")
        return False


def check_file_exists(filepath, description):
    """Check if a file exists."""
    if Path(filepath).exists():
        ok(description)
        return True
    else:
        warn(f"{description} not found")
        return False


def check_directory_exists(dirpath, description):
    """Check if a directory exists."""
    if Path(dirpath).is_dir():
        ok(description)
        return True
    else:
        warn(f"{description} not found")
        return False


def main():
    verbose = '--verbose' in sys.argv or '-v' in sys.argv

    print(f"\n{CYAN}MeshForge Self-Test{NC}")
    print("=" * 40)

    results = {
        'passed': 0,
        'failed': 0,
        'warnings': 0
    }

    # 1. Python version
    print(f"\n{CYAN}Python Environment{NC}")
    if check_python_version():
        results['passed'] += 1
    else:
        results['failed'] += 1

    # 2. Core dependencies — derived from requirements/core.txt (single owner)
    print(f"\n{CYAN}Core Dependencies{NC}")
    core_deps = core_dependencies()
    if core_deps is None:
        warn("requirements/core.txt unreadable — checking a reduced fallback "
             "set; core deps NOT fully verified")
        results['warnings'] += 1
        core_deps = _CORE_FALLBACK
    for module, package in core_deps:
        if check_import(module, package):
            results['passed'] += 1
        else:
            results['failed'] += 1

    # 3. Optional dependencies — graceful-degradation deps (safe_import sites)
    # and profile extras. NOT in core.txt by design; a miss is a warning, never
    # a failure. GTK/PyGObject was dropped here 2026-07-24: GTK4 was removed in
    # v0.5.x and the TUI is the only interface, so the check could only ever
    # report a warning about an interface that no longer exists.
    print(f"\n{CYAN}Optional Dependencies{NC}")
    optional_deps = [
        ('folium', 'folium'),
        ('psutil', 'psutil'),
    ]
    for module, package in optional_deps:
        if check_import(module, package):
            results['passed'] += 1
        else:
            results['warnings'] += 1

    # 4. MeshForge modules
    print(f"\n{CYAN}MeshForge Modules{NC}")
    # Add src to path
    src_path = Path(__file__).parent.parent / 'src'
    sys.path.insert(0, str(src_path))

    meshforge_modules = [
        ('utils.diagnostic_engine', 'Diagnostic Engine'),
        ('utils.knowledge_base', 'Knowledge Base'),
        ('utils.coverage_map', 'Coverage Map'),
        ('utils.rf', 'RF Calculations'),
    ]
    for module, name in meshforge_modules:
        if check_import(module, name):
            results['passed'] += 1
        else:
            results['warnings'] += 1

    # 5. Services
    print(f"\n{CYAN}Services{NC}")
    services = [
        ('localhost', 4403, 'meshtasticd'),
        ('localhost', 37428, 'rnsd'),
        ('localhost', 8080, 'HamClock'),
        ('localhost', 1883, 'MQTT (mosquitto)'),
    ]
    for host, port, name in services:
        if check_port(host, port, name):
            results['passed'] += 1
        else:
            results['warnings'] += 1

    # 6. Configuration files
    print(f"\n{CYAN}Configuration{NC}")

    # Get real user home (handles sudo correctly)
    try:
        from utils.paths import get_real_user_home
        home = get_real_user_home()
    except ImportError:
        # Fallback with SUDO_USER handling
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            home = Path(f'/home/{sudo_user}')
        else:
            home = Path.home()

    config_checks = [
        (home / '.config' / 'meshforge', 'User config directory'),
        (Path('/etc/meshtasticd'), 'meshtasticd config'),
    ]
    for path, desc in config_checks:
        if check_directory_exists(path, desc):
            results['passed'] += 1
        else:
            results['warnings'] += 1

    # Summary
    print("\n" + "=" * 40)
    total = results['passed'] + results['failed'] + results['warnings']
    print(f"{CYAN}Results:{NC}")
    print(f"  {GREEN}Passed:{NC}   {results['passed']}/{total}")
    if results['failed'] > 0:
        print(f"  {RED}Failed:{NC}   {results['failed']}/{total}")
    if results['warnings'] > 0:
        print(f"  {YELLOW}Warnings:{NC} {results['warnings']}/{total}")

    # Overall status
    if results['failed'] == 0:
        print(f"\n{GREEN}MeshForge is ready to use!{NC}")
        print(f"\nRun: {CYAN}sudo python3 src/launcher.py{NC}")
        return 0
    else:
        print(f"\n{RED}Some required dependencies are missing.{NC}")
        print(f"Run: {CYAN}pip3 install -r requirements.txt{NC}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
