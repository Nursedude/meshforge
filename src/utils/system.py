"""System utilities for OS detection and information"""

import os
import sys
import platform
import subprocess
import distro


def check_root():
    """Check if running with root privileges"""
    return os.geteuid() == 0


def get_system_info():
    """Get comprehensive system information"""
    info = {}

    # OS information
    info['os'] = distro.name() or 'Unknown Linux'
    info['os_version'] = distro.version() or 'Unknown'
    info['os_codename'] = distro.codename() or ''

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


def run_command(command, shell=False, capture_output=True):
    """Run a system command and return the result"""
    try:
        if isinstance(command, str) and not shell:
            command = command.split()

        result = subprocess.run(
            command,
            shell=shell,
            capture_output=capture_output,
            text=True,
            timeout=300
        )

        return {
            'returncode': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr,
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


def get_service_status(service_name):
    """Get systemd service status"""
    result = run_command(f'systemctl is-active {service_name}')
    return result['stdout'].strip() if result['success'] else 'unknown'


def is_service_running(service_name):
    """Check if a systemd service is running"""
    return get_service_status(service_name) == 'active'


def enable_service(service_name):
    """Enable and start a systemd service"""
    enable_result = run_command(f'systemctl enable {service_name}')
    start_result = run_command(f'systemctl start {service_name}')
    return enable_result['success'] and start_result['success']


def restart_service(service_name):
    """Restart a systemd service"""
    result = run_command(f'systemctl restart {service_name}')
    return result['success']


def check_package_installed(package_name):
    """Check if a Debian package is installed"""
    result = run_command(f'dpkg -l {package_name}')
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


def get_disk_space(path='/'):
    """Get available disk space in MB"""
    try:
        import psutil
        disk = psutil.disk_usage(path)
        return disk.free // (1024 * 1024)
    except ImportError:
        # Fallback to df command
        result = run_command(f'df -m {path}')
        if result['success']:
            lines = result['stdout'].strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 4:
                    return int(parts[3])
        return 0
