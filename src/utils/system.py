"""
System utilities for OS detection, cross-platform support, and security.

Version: 0.4.3-beta
Updated: 2026-01-06

Patterns adopted from RNS_Over_Meshtastic_Gateway for cross-platform reliability.
"""

import os
import sys
import platform
import subprocess
from pathlib import Path
from typing import Optional, Callable, List, Tuple, Dict, Any

try:
    import distro
except ImportError:
    distro = None  # Handle Windows where distro isn't available


def check_root() -> bool:
    """
    Check if running with root privileges.

    Returns:
        True if running as root (euid 0), False otherwise
    """
    return os.geteuid() == 0


def require_root(exit_on_fail: bool = True,
                 message: str = None,
                 print_func: Callable[[str], None] = None) -> bool:
    """
    Require root privileges, optionally exiting if not root.

    Args:
        exit_on_fail: If True, exit the program when not root
        message: Custom error message
        print_func: Optional custom print function (e.g., console.print)

    Returns:
        True if running as root

    Raises:
        SystemExit: If exit_on_fail is True and not running as root
    """
    if check_root():
        return True

    error_msg = message or "This operation requires root privileges. Please run with sudo."

    if print_func:
        print_func(f"[error]{error_msg}[/error]")
    else:
        print(f"Error: {error_msg}", file=sys.stderr)

    if exit_on_fail:
        sys.exit(1)

    return False


def get_real_user() -> str:
    """
    Get the real username, even when running with sudo.

    Returns:
        The real username (SUDO_USER or USER)
    """
    return os.environ.get('SUDO_USER') or os.environ.get('USER') or 'unknown'


def get_real_uid() -> int:
    """
    Get the real user ID, even when running with sudo.

    Returns:
        The real user ID
    """
    sudo_uid = os.environ.get('SUDO_UID')
    if sudo_uid:
        return int(sudo_uid)
    return os.getuid()


def get_real_gid() -> int:
    """
    Get the real group ID, even when running with sudo.

    Returns:
        The real group ID
    """
    sudo_gid = os.environ.get('SUDO_GID')
    if sudo_gid:
        return int(sudo_gid)
    return os.getgid()


def drop_privileges() -> bool:
    """
    Drop root privileges back to the original user (if running via sudo).

    Returns:
        True if privileges were dropped successfully
    """
    if not check_root():
        return True  # Already non-root

    try:
        uid = get_real_uid()
        gid = get_real_gid()

        if uid == 0:
            return False  # Would still be root

        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
        return True
    except (OSError, PermissionError):
        return False


# =============================================================================
# Admin Mode - Elevated Command Execution
# =============================================================================

def run_admin_command(
    cmd: List[str],
    use_gui: bool = True,
    timeout: int = 30,
    capture_output: bool = True
) -> Tuple[bool, str, str]:
    """
    Run a command with elevated privileges.

    Uses pkexec (GUI password dialog) when available and use_gui=True,
    falls back to sudo for terminal use.

    Args:
        cmd: Command and arguments as list (e.g., ['systemctl', 'start', 'meshtasticd'])
        use_gui: If True, prefer pkexec for GUI password prompt
        timeout: Command timeout in seconds
        capture_output: If True, capture stdout/stderr

    Returns:
        Tuple of (success: bool, stdout: str, stderr: str)

    Example:
        success, out, err = run_admin_command(['systemctl', 'restart', 'meshtasticd'])
        if not success:
            print(f"Failed: {err}")
    """
    import shutil
    import logging
    logger = logging.getLogger(__name__)

    # Already root? Run directly
    if check_root():
        logger.debug(f"Running as root: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                timeout=timeout
            )
            return result.returncode == 0, result.stdout or "", result.stderr or ""
        except subprocess.TimeoutExpired:
            return False, "", "Command timed out"
        except Exception as e:
            return False, "", str(e)

    # Try pkexec for GUI (shows password dialog)
    pkexec_path = shutil.which('pkexec')
    if use_gui and pkexec_path and os.environ.get('DISPLAY'):
        logger.debug(f"Using pkexec for: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                [pkexec_path] + cmd,
                capture_output=capture_output,
                text=True,
                timeout=timeout
            )
            # pkexec returns 126 if user cancelled, 127 if command not found
            if result.returncode == 126:
                return False, "", "Authentication cancelled by user"
            return result.returncode == 0, result.stdout or "", result.stderr or ""
        except subprocess.TimeoutExpired:
            return False, "", "Command timed out"
        except Exception as e:
            logger.debug(f"pkexec failed: {e}, trying sudo")

    # Fall back to sudo
    sudo_path = shutil.which('sudo')
    if sudo_path:
        logger.debug(f"Using sudo for: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                [sudo_path] + cmd,
                capture_output=capture_output,
                text=True,
                timeout=timeout
            )
            return result.returncode == 0, result.stdout or "", result.stderr or ""
        except subprocess.TimeoutExpired:
            return False, "", "Command timed out"
        except Exception as e:
            return False, "", str(e)

    return False, "", "No privilege escalation method available (need pkexec or sudo)"


def run_admin_command_async(
    cmd: List[str],
    callback: Callable[[bool, str, str], None],
    use_gui: bool = True,
    timeout: int = 30
) -> None:
    """
    Run an admin command asynchronously (for GTK/GUI apps).

    Args:
        cmd: Command and arguments as list
        callback: Function to call with (success, stdout, stderr) when done
        use_gui: If True, prefer pkexec for GUI password prompt
        timeout: Command timeout in seconds

    Example:
        def on_done(success, out, err):
            if success:
                print("Service restarted!")
            else:
                print(f"Failed: {err}")

        run_admin_command_async(['systemctl', 'restart', 'meshtasticd'], on_done)
    """
    import threading

    def do_run():
        success, stdout, stderr = run_admin_command(cmd, use_gui, timeout)
        # Use GLib.idle_add if available (GTK apps)
        try:
            from gi.repository import GLib
            GLib.idle_add(callback, success, stdout, stderr)
        except ImportError:
            callback(success, stdout, stderr)

    thread = threading.Thread(target=do_run, daemon=True)
    thread.start()


def systemctl_admin(action: str, service: str, callback: Callable[[bool, str, str], None] = None) -> Tuple[bool, str, str]:
    """
    Convenience function for systemctl admin operations.

    Args:
        action: systemctl action (start, stop, restart, enable, disable)
        service: Service name (e.g., 'meshtasticd')
        callback: If provided, runs async and calls callback when done

    Returns:
        If callback is None: Tuple of (success, stdout, stderr)
        If callback provided: None (result passed to callback)

    Example:
        # Synchronous
        success, _, err = systemctl_admin('restart', 'meshtasticd')

        # Async for GTK
        systemctl_admin('restart', 'meshtasticd', callback=on_done)
    """
    cmd = ['systemctl', action, service]

    if callback:
        run_admin_command_async(cmd, callback)
        return None
    else:
        return run_admin_command(cmd)


def get_system_info():
    """Get comprehensive system information"""
    info = {}

    # OS information (handle Windows where distro isn't available)
    if distro:
        info['os'] = distro.name() or 'Unknown Linux'
        info['os_version'] = distro.version() or 'Unknown'
        info['os_codename'] = distro.codename() or ''
    else:
        info['os'] = platform.system()
        info['os_version'] = platform.release()
        info['os_codename'] = ''

    # Architecture
    info['arch'] = platform.machine()
    info['platform'] = platform.system()

    # Python version
    info['python'] = platform.python_version()

    # Kernel
    info['kernel'] = platform.release()

    # Check if Raspberry Pi
    info['is_pi'] = is_raspberry_pi()

    # Get detailed board model
    board_model = get_board_model()
    if board_model:
        info['board_model'] = board_model

    # Check Linux native compatibility
    info['meshtastic_compatible'] = is_linux_native_compatible()

    # Determine if 32-bit or 64-bit
    info['bits'] = get_architecture_bits()

    return info


def is_raspberry_pi():
    """Check if system is a Raspberry Pi"""
    try:
        # Check device tree model first (most reliable)
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().strip('\x00').lower()
            if 'raspberry' in model:
                return True
    except FileNotFoundError:
        pass

    try:
        # Fallback to cpuinfo
        with open('/proc/cpuinfo', 'r') as f:
            cpuinfo = f.read()
            return 'Raspberry Pi' in cpuinfo or 'BCM' in cpuinfo
    except FileNotFoundError:
        pass

    return False


def get_board_model():
    """Get detailed board model information"""
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().strip('\x00').strip()
            return model
    except FileNotFoundError:
        return None


def is_linux_native_compatible():
    """Check if system is compatible with Meshtastic Linux native"""
    # Raspberry Pi is always compatible
    if is_raspberry_pi():
        return True

    # Check for other compatible boards
    arch = platform.machine().lower()
    compatible_arches = ['aarch64', 'arm64', 'armv7', 'armhf', 'armv6', 'x86_64', 'amd64']

    if any(a in arch for a in compatible_arches):
        # Check if it's a Linux system
        if platform.system() == 'Linux':
            return True

    return False


def get_architecture_bits():
    """Determine if system is 32-bit or 64-bit"""
    arch = platform.machine().lower()

    if 'aarch64' in arch or 'arm64' in arch:
        return 64
    elif 'armv7' in arch or 'armhf' in arch or 'armv6' in arch:
        return 32
    elif 'x86_64' in arch or 'amd64' in arch:
        return 64
    elif 'i386' in arch or 'i686' in arch:
        return 32
    else:
        # Default to checking architecture
        return 64 if sys.maxsize > 2**32 else 32


def get_os_type():
    """Get OS type for installation (armhf, arm64, or other)"""
    info = get_system_info()

    if not info['is_pi']:
        return 'unknown'

    if info['bits'] == 64:
        return 'arm64'
    elif info['bits'] == 32:
        return 'armhf'
    else:
        return 'unknown'


def run_command(command, shell=False, capture_output=True, stream_output=False, stderr_to_null=False):
    """Run a system command and return the result

    Args:
        command: Command to run (string or list)
        shell: Use shell execution (avoid for security)
        capture_output: Capture stdout/stderr
        stream_output: Print output in real-time (for interactive commands)
        stderr_to_null: Redirect stderr to /dev/null (suppress errors)
    """
    try:
        if isinstance(command, str) and not shell:
            command = command.split()

        if stream_output:
            # Stream output in real-time for better user feedback
            process = subprocess.Popen(
                command,
                shell=shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            stdout_lines = []
            for line in process.stdout:
                print(line, end='')
                stdout_lines.append(line)

            process.wait(timeout=300)
            stdout = ''.join(stdout_lines)

            return {
                'returncode': process.returncode,
                'stdout': stdout,
                'stderr': '',
                'success': process.returncode == 0
            }
        else:
            # Build subprocess arguments
            run_kwargs = {
                'shell': shell,
                'text': True,
                'timeout': 300
            }

            if stderr_to_null:
                # Redirect stderr to /dev/null
                run_kwargs['stdout'] = subprocess.PIPE
                run_kwargs['stderr'] = subprocess.DEVNULL
            elif capture_output:
                run_kwargs['capture_output'] = True

            result = subprocess.run(command, **run_kwargs)

            return {
                'returncode': result.returncode,
                'stdout': result.stdout if result.stdout else '',
                'stderr': '' if stderr_to_null else (result.stderr if result.stderr else ''),
                'success': result.returncode == 0
            }
    except subprocess.TimeoutExpired:
        return {
            'returncode': -1,
            'stdout': '',
            'stderr': 'Command timed out',
            'success': False
        }
    except Exception as e:
        return {
            'returncode': -1,
            'stdout': '',
            'stderr': str(e),
            'success': False
        }


def check_internet_connection():
    """Check if internet connection is available"""
    try:
        result = run_command('ping -c 1 8.8.8.8')
        return result['success']
    except Exception:
        return False


def get_service_status(service_name: str) -> str:
    """Get systemd service status

    Args:
        service_name: Name of the systemd service (validated, no shell chars)
    """
    # Use list form to prevent command injection
    result = run_command(['systemctl', 'is-active', service_name])
    return result['stdout'].strip() if result['success'] else 'unknown'


def is_service_running(service_name: str) -> bool:
    """Check if a systemd service is running"""
    return get_service_status(service_name) == 'active'


def enable_service(service_name: str) -> bool:
    """Enable and start a systemd service

    Args:
        service_name: Name of the systemd service (validated, no shell chars)
    """
    # Use list form to prevent command injection
    enable_result = run_command(['systemctl', 'enable', service_name])
    start_result = run_command(['systemctl', 'start', service_name])
    return enable_result['success'] and start_result['success']


def restart_service(service_name: str) -> bool:
    """Restart a systemd service

    Args:
        service_name: Name of the systemd service (validated, no shell chars)
    """
    # Use list form to prevent command injection
    result = run_command(['systemctl', 'restart', service_name])
    return result['success']


def check_package_installed(package_name: str) -> bool:
    """Check if a Debian package is installed

    Args:
        package_name: Name of the package (validated, no shell chars)
    """
    # Use list form to prevent command injection
    result = run_command(['dpkg', '-l', package_name])
    return result['success']


def get_available_memory():
    """Get available system memory in MB"""
    try:
        import psutil
        mem = psutil.virtual_memory()
        return mem.available // (1024 * 1024)
    except ImportError:
        # Fallback to reading /proc/meminfo
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemAvailable' in line:
                        return int(line.split()[1]) // 1024
        except Exception:
            return 0


def get_disk_space(path: str = '/') -> int:
    """Get available disk space in MB

    Args:
        path: Filesystem path to check (validated, no shell chars)
    """
    try:
        import psutil
        disk = psutil.disk_usage(path)
        return disk.free // (1024 * 1024)
    except ImportError:
        # Fallback to df command - use list form for security
        result = run_command(['df', '-m', path])
        if result['success']:
            lines = result['stdout'].strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 4:
                    return int(parts[3])
        return 0


# =============================================================================
# Cross-Platform Serial Port Detection
# Pattern from RNS_Over_Meshtastic_Gateway for Windows compatibility
# =============================================================================

def get_serial_ports() -> List[str]:
    """
    Get available serial ports - cross-platform.

    Uses pyserial when available, falls back to filesystem scan on Linux/macOS.
    Pattern adopted from RNS Gateway for Windows support.

    Returns:
        List of available serial port paths (e.g., ['COM3'] or ['/dev/ttyUSB0'])
    """
    ports = []

    # Try pyserial first (most reliable, cross-platform)
    try:
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
        return sorted(ports)
    except ImportError:
        pass

    # Fallback for Linux/macOS without pyserial
    if platform.system() == 'Linux':
        dev_path = Path('/dev')
        patterns = ['ttyUSB*', 'ttyACM*', 'ttyAMA*']
        for pattern in patterns:
            ports.extend([str(p) for p in dev_path.glob(pattern)])
    elif platform.system() == 'Darwin':
        dev_path = Path('/dev')
        ports.extend([str(p) for p in dev_path.glob('cu.*')])

    return sorted(ports)


def get_config_dir(app_name: str = 'meshforge') -> Path:
    """
    Get configuration directory - cross-platform.

    Args:
        app_name: Application name for the config folder

    Returns:
        Path to configuration directory
    """
    if platform.system() == 'Windows':
        base = Path(os.environ.get('APPDATA', Path.home()))
        return base / app_name
    elif platform.system() == 'Darwin':
        return Path.home() / 'Library' / 'Application Support' / app_name
    else:
        # Linux / Unix
        xdg_config = os.environ.get('XDG_CONFIG_HOME')
        if xdg_config:
            return Path(xdg_config) / app_name
        return Path.home() / '.config' / app_name


def get_rns_config_dir() -> Path:
    """
    Get Reticulum configuration directory - cross-platform.

    Returns:
        Path to ~/.reticulum or %APPDATA%/Reticulum
    """
    if platform.system() == 'Windows':
        return Path(os.environ.get('APPDATA', Path.home())) / 'Reticulum'
    return Path.home() / '.reticulum'


def get_rns_interfaces_dir() -> Path:
    """Get RNS interfaces directory for custom interfaces."""
    return get_rns_config_dir() / 'interfaces'


# =============================================================================
# Safe Subprocess Execution
# Enhanced with patterns from MeshForge security audit
# =============================================================================

def safe_run(
    cmd: List[str],
    timeout: int = 30,
    capture: bool = True
) -> Tuple[bool, str, str]:
    """
    Run subprocess safely with timeout and proper exception handling.

    NEVER uses shell=True. All commands must be passed as list.

    Args:
        cmd: Command as list (e.g., ['meshtastic', '--info'])
        timeout: Timeout in seconds (default 30)
        capture: Whether to capture output

    Returns:
        Tuple of (success, stdout, stderr)
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout
        )
        return (
            result.returncode == 0,
            result.stdout or '',
            result.stderr or ''
        )
    except subprocess.TimeoutExpired:
        return False, '', f'Command timed out after {timeout}s'
    except FileNotFoundError:
        return False, '', f'Command not found: {cmd[0]}'
    except (OSError, subprocess.SubprocessError) as e:
        return False, '', str(e)


def check_command_exists(command: str) -> bool:
    """
    Check if a command exists in PATH.

    Args:
        command: Command name to check

    Returns:
        True if command exists
    """
    import shutil
    return shutil.which(command) is not None


# =============================================================================
# Dependency Checking
# Pattern from RNS Gateway's graceful degradation
# =============================================================================

def check_dependency(module_name: str) -> bool:
    """
    Check if a Python module is available.

    Args:
        module_name: Name of module to check (e.g., 'meshtastic')

    Returns:
        True if module can be imported
    """
    import importlib.util
    return importlib.util.find_spec(module_name) is not None


def get_dependency_status() -> Dict[str, bool]:
    """
    Get status of all optional dependencies.

    Returns:
        Dict mapping dependency name to availability
    """
    deps = {
        'meshtastic': check_dependency('meshtastic'),
        'rns': check_dependency('RNS'),
        'lxmf': check_dependency('LXMF'),
        'flask': check_dependency('flask'),
        'textual': check_dependency('textual'),
        'rich': check_dependency('rich'),
        'pyserial': check_dependency('serial'),
    }

    # GTK4 requires special handling
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gtk
        deps['gtk4'] = True
    except (ImportError, ValueError):
        deps['gtk4'] = False

    return deps


# =============================================================================
# LoRa Speed Presets
# Mapping from RNS_Over_Meshtastic_Gateway
# =============================================================================

LORA_SPEED_PRESETS = {
    8: {'name': 'SHORT_TURBO', 'delay': 0.4, 'desc': 'Fastest, recommended for RNS'},
    6: {'name': 'SHORT_FAST', 'delay': 1.0, 'desc': 'High speed, good for dense networks'},
    5: {'name': 'SHORT_SLOW', 'delay': 3.0, 'desc': 'Better range than fast'},
    7: {'name': 'LONG_MODERATE', 'delay': 12.0, 'desc': 'Long range, moderate speed'},
    4: {'name': 'MEDIUM_FAST', 'delay': 4.0, 'desc': 'Slowest recommended for RNS'},
    3: {'name': 'MEDIUM_SLOW', 'delay': 6.0, 'desc': 'Extended range'},
    1: {'name': 'LONG_SLOW', 'delay': 15.0, 'desc': 'Very long range'},
    0: {'name': 'LONG_FAST', 'delay': 8.0, 'desc': 'Default Meshtastic'},
}


def get_lora_delay(speed: int) -> float:
    """
    Get transmission delay for a LoRa speed preset.

    Args:
        speed: Speed preset ID (0-8)

    Returns:
        Delay in seconds between transmissions
    """
    return LORA_SPEED_PRESETS.get(speed, {}).get('delay', 7.0)
