"""Port, process, and socket detection utilities.

Extracted from service_check.py for file size compliance (CLAUDE.md #6).
Re-exported from service_check.py for backward compatibility.

These are utility functions for detecting services by their network
footprint. Note: check_service() in service_check.py trusts systemctl
for systemd services (Issue #17) and does NOT use port checks for that.
"""

import logging
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from utils.boundary_timing import timed_boundary

logger = logging.getLogger(__name__)


def _detect_radio_hardware() -> dict:
    """
    Detect what Meshtastic radio hardware is present.

    Returns:
        dict with:
            has_spi: bool - SPI devices present (/dev/spidev*)
            has_usb: bool - USB serial devices present (/dev/ttyUSB*, /dev/ttyACM*)
            spi_devices: list - SPI device paths
            usb_devices: list - USB device paths
            usb_device: str - First USB device (for fix hints)
            hardware_type: str - 'spi', 'usb', 'both', or 'none'
    """
    result = {
        'has_spi': False,
        'has_usb': False,
        'spi_devices': [],
        'usb_devices': [],
        'usb_device': '/dev/ttyUSB0',
        'hardware_type': 'none'
    }

    # Check SPI devices
    spi_devices = list(Path('/dev').glob('spidev*'))
    if spi_devices:
        result['has_spi'] = True
        result['spi_devices'] = [str(d) for d in spi_devices]

    # Check USB serial devices
    usb_devices = list(Path('/dev').glob('ttyUSB*')) + list(Path('/dev').glob('ttyACM*'))
    if usb_devices:
        result['has_usb'] = True
        result['usb_devices'] = [str(d) for d in usb_devices]
        result['usb_device'] = str(usb_devices[0])

    # Determine hardware type
    if result['has_spi'] and result['has_usb']:
        result['hardware_type'] = 'both'
    elif result['has_spi']:
        result['hardware_type'] = 'spi'
    elif result['has_usb']:
        result['hardware_type'] = 'usb'

    return result


# =============================================================================
# UTILITY FUNCTIONS
# These are kept for direct use but NOT used by check_service() for systemd
# services (Issue #17: avoid conflicting detection methods)
# =============================================================================


def check_port(port: int, host: str = 'localhost', timeout: float = 2.0) -> bool:
    """
    Check if a TCP port is accepting connections.

    Args:
        port: TCP port number
        host: Hostname to check (default localhost)
        timeout: Connection timeout in seconds

    Returns:
        True if port is open, False otherwise
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        return result == 0
    except (socket.error, OSError) as e:
        logger.debug(f"Port check failed for {host}:{port}: {e}")
        return False
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass  # Socket close errors are non-critical


def tcp_port_listening(
    port: int, *, proc_paths: tuple = ('/proc/net/tcp', '/proc/net/tcp6')
) -> bool:
    """Return True iff a local socket is in the LISTEN state on ``port``.

    Reads the kernel socket table (``/proc/net/tcp`` + ``tcp6``) directly
    instead of opening a connection — the same /proc technique
    :func:`check_udp_port` uses.

    Why this exists (Issue #17/#75): meshtasticd's PhoneAPI (:4403) is a
    SINGLE-consumer stream. A ``connect_ex`` "is the port open?" probe to it
    is itself a PhoneAPI *client* connection — meshtasticd accepts it,
    logs ``Force close previous TCP connection``, and tears down whoever
    held the stream. When such a probe runs on a hot path (e.g. the
    ``/api/status`` radio summary, polled by federation + the node_map UI),
    the churn intermittently wedges the gateway's mesh-TX (the 2026-06-13→15
    moc bot-dark incident). A status read must observe the port WITHOUT
    perturbing it, so we look for a LISTEN socket (state ``0A``) on the port.

    Args:
        port: TCP port number.
        proc_paths: kernel socket-table paths (overridable for tests).

    Returns:
        True if something is LISTENing on ``port`` locally, else False.
    """
    hex_suffix = f':{port:04X}'
    for proc_path in proc_paths:
        try:
            with open(proc_path, 'r') as f:
                next(f, None)  # skip header row
                for line in f:
                    parts = line.split()
                    # cols: sl local_address rem_address st ...
                    # local_address is "ADDR:PORT_HEX"; st 0A == TCP LISTEN.
                    if (len(parts) >= 4 and parts[3] == '0A'
                            and parts[1].endswith(hex_suffix)):
                        return True
        except (OSError, ValueError) as e:
            logger.debug(f"tcp_port_listening: {proc_path}: {e}")
            continue
    return False


def check_udp_port(port: int, host: str = '127.0.0.1', timeout: float = 2.0) -> bool:
    """
    Check if a UDP port is in use.

    Primary method: read /proc/net/udp + /proc/net/udp6 (kernel socket table).
    This is reliable even when the service sets SO_REUSEADDR/SO_REUSEPORT,
    which causes bind-test false negatives.

    Fallback chain: ss → lsof → bind test.

    Args:
        port: UDP port number
        host: Host address to check (default 127.0.0.1)
        timeout: Socket timeout in seconds

    Returns:
        True if port appears to be in use (service running), False otherwise
    """
    # Primary: read /proc/net/udp directly (always available on Linux,
    # no external tool required). Port is stored as hex in column 1
    # (local_address) in the format ADDR:PORT_HEX.
    hex_port = f'{port:04X}'
    for proc_path in ('/proc/net/udp', '/proc/net/udp6'):
        try:
            with open(proc_path, 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        # local_address is "ADDR:PORT_HEX"
                        local = parts[1]
                        if local.endswith(':' + hex_port):
                            return True
        except (OSError, IOError):
            continue

    # Fallback 1: ss (not always installed — e.g., minimal containers)
    try:
        result = subprocess.run(
            ['ss', '-uln'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            port_str = str(port)
            for line in result.stdout.split('\n'):
                parts = line.split()
                if len(parts) >= 5:
                    local_addr = parts[4]
                    if local_addr.endswith(':' + port_str):
                        return True
            return False
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Fallback 2: lsof (commonly available)
    try:
        result = subprocess.run(
            ['lsof', '-i', f'UDP:{port}', '-nP'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            # Any output means something has the port open
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:  # Header + at least one entry
                return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Fallback 3: bind test (unreliable with SO_REUSEADDR, last resort)
    hosts_to_check = [host]
    if host == '127.0.0.1':
        hosts_to_check.append('0.0.0.0')

    for check_host in hosts_to_check:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.bind((check_host, port))
            sock.close()
            continue
        except OSError as e:
            if e.errno in (98, 48, 10048):  # EADDRINUSE
                return True
            logger.debug(f"UDP port check error for {check_host}:{port}: {e}")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    return False


def get_udp_port_owner(port: int) -> Optional[Tuple[str, int]]:
    """Get the process name and PID that owns a UDP port.

    Primary: ``ss -ulnp``. Fallback: ``/proc/net/udp`` inode scan.

    Args:
        port: UDP port number to check.

    Returns:
        Tuple of ``(process_name, pid)`` if found, ``None`` otherwise.
    """
    # Primary: ss -ulnp shows process info for UDP listeners
    try:
        result = subprocess.run(
            ['ss', '-ulnp', 'sport', '=', f':{port}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            # Parse users:(("process",pid=NNN,fd=N)) pattern
            m = re.search(
                r'users:\(\("([^"]+)",pid=(\d+)',
                result.stdout
            )
            if m:
                return (m.group(1), int(m.group(2)))
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Fallback: find inode in /proc/net/udp, then scan /proc/*/fd
    hex_port = f'{port:04X}'
    target_inode = None
    for proc_path in ('/proc/net/udp', '/proc/net/udp6'):
        try:
            with open(proc_path, 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 10:
                        local = parts[1]
                        if local.endswith(':' + hex_port):
                            target_inode = parts[9]
                            break
            if target_inode:
                break
        except (OSError, IOError):
            continue

    if not target_inode:
        return None

    # Scan /proc/*/fd for the inode
    proc_dir = Path('/proc')
    for pid_dir in proc_dir.iterdir():
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / 'fd'
        try:
            for fd in fd_dir.iterdir():
                try:
                    link = os.readlink(str(fd))
                    if f'socket:[{target_inode}]' in link:
                        comm_path = pid_dir / 'comm'
                        name = comm_path.read_text().strip()
                        return (name, int(pid_dir.name))
                except (OSError, ValueError):
                    continue
        except (OSError, PermissionError):
            continue

    return None


def check_rns_shared_instance(instance_name: str = 'default',
                               port: int = 37428) -> bool:
    """Check if the RNS shared instance is available.

    Uses passive detection (reads /proc files) to avoid disrupting the
    shared instance.  Safe to call in tight poll loops.

    Checks in priority order:
        1. ``/proc/net/unix`` for abstract domain socket (Linux default)
        2. TCP port via ``check_port()`` (fallback)
        3. UDP port via ``check_udp_port()`` (legacy)

    Args:
        instance_name: RNS instance name (default: ``'default'``).
        port: Shared instance port for TCP/UDP fallback (default: 37428).

    Returns:
        True if the shared instance is detected via any method.
    """
    info = get_rns_shared_instance_info(instance_name, port)
    return info['available']


def _check_proc_net_unix(socket_name: str) -> bool:
    """Check if an abstract Unix domain socket exists via /proc/net/unix.

    Passive check — reads a proc file, never connects to the service.
    Abstract sockets appear in /proc/net/unix with ``@`` prefix.

    Args:
        socket_name: Socket name WITHOUT the null byte or ``@`` prefix.
                     e.g. ``'rns/<instance>'`` to match ``@rns/<instance>``
                     (derive ``<instance>`` from the box's RNS config —
                     never hardcode ``default``).

    Returns:
        True if the socket is listed in /proc/net/unix.
    """
    target = f'@{socket_name}'
    try:
        with open('/proc/net/unix', 'r') as f:
            for line in f:
                if target in line:
                    return True
    except OSError:
        pass
    return False


def get_rns_shared_instance_info(instance_name: str = 'default',
                                  port: int = 37428) -> dict:
    """Get detailed shared instance connectivity info for diagnostics.

    Returns a dict with keys:
        - ``available`` (bool): Whether shared instance is reachable.
        - ``method`` (str): Detection method that succeeded
          (``'unix_socket'``, ``'tcp'``, ``'udp'``, or ``'none'``).
        - ``detail`` (str): Human-readable connection detail.

    Args:
        instance_name: RNS instance name (default: ``'default'``).
        port: Shared instance port for TCP/UDP fallback (default: 37428).
    """
    # Wrap the entire probe path so a stalled rnsd-availability check
    # (most often caused by the unix-socket scan or fallback TCP connect)
    # produces a forensic. Per-tier timing (proc/net/unix scan vs.
    # TCP connect) is rarely worth knowing — what matters for diagnosis
    # is "the rnsd availability check stalled".
    with timed_boundary("rnsd.shared_instance_probe"):
        return _shared_instance_info_inner(instance_name, port)


def _shared_instance_info_inner(instance_name: str, port: int) -> dict:
    # 1. Passive check: scan /proc/net/unix for the abstract domain socket.
    # RNS creates @rns/{instance_name} (LocalInterface data transport).
    # This mirrors how check_udp_port() reads /proc/net/udp — no connection
    # to the service, zero side effects, safe to call in tight poll loops.
    socket_name = f'rns/{instance_name}'
    if _check_proc_net_unix(socket_name):
        return {
            'available': True,
            'method': 'unix_socket',
            'detail': f'@rns/{instance_name} (abstract domain socket)',
        }

    # 2. TCP port (used when shared_instance_type = tcp in RNS config)
    if check_port(port):
        return {
            'available': True,
            'method': 'tcp',
            'detail': f'127.0.0.1:{port} (TCP)',
        }

    # 3. UDP port (legacy fallback)
    if check_udp_port(port):
        return {
            'available': True,
            'method': 'udp',
            'detail': f'127.0.0.1:{port} (UDP)',
        }

    # Before returning "not available", check if rnsd process exists
    # to distinguish "rnsd not running" from "rnsd running but broken"
    rnsd_running = check_process_running('rnsd')
    detail = (f'No shared instance found '
              f'(checked @rns/{instance_name}, '
              f'TCP:{port}, UDP:{port})')
    if rnsd_running:
        return {
            'available': False,
            'method': 'none',
            'detail': detail,
            'diagnostic': (
                'rnsd process running but shared instance not serving '
                '\u2014 likely config issue (check shared_instance_type '
                'and /etc/reticulum/storage/ permissions)'
            ),
        }

    return {
        'available': False,
        'method': 'none',
        'detail': detail,
    }


def _verify_process_cmdline(pid_str: str, process_name: str) -> bool:
    """Verify a PID is genuinely running the named process via /proc/cmdline.

    Reads /proc/{pid}/cmdline (null-separated argv) and checks if the
    process name appears as a script path or ``-m`` module argument.
    Eliminates false positives from shell invocations that merely
    reference the process name as a string in their command line.

    Args:
        pid_str: PID as a string.
        process_name: Expected process name (e.g. ``'rnsd'``).

    Returns:
        True if the PID genuinely runs the named process.
    """
    if not pid_str.isdigit():
        return False
    try:
        with open(f'/proc/{pid_str}/cmdline', 'rb') as f:
            cmdline = f.read().decode('utf-8', errors='replace')
        args = cmdline.split('\0')
        for i, arg in enumerate(args):
            # Match binary/script path — use basename to avoid
            # matching e.g. /usr/bin/superrnsd when checking for rnsd
            basename = arg.rsplit('/', 1)[-1]
            if basename == process_name:
                return True
            # Match python -m <process_name>
            if arg == '-m' and i + 1 < len(args):
                if args[i + 1] == process_name:
                    return True
    except (OSError, ValueError):
        pass
    return False


def check_process_running(process_name: str) -> bool:
    """
    Check if a process is running by name.

    Args:
        process_name: Name of the process to check (e.g., 'rnsd')

    Returns:
        True if process is running, False otherwise
    """
    try:
        # First try exact process name match (most reliable)
        result = subprocess.run(
            ['pgrep', '-x', process_name],  # -x = exact match
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return True

        # Also check with -f but use word boundaries + cmdline verification
        # e.g., match "rnsd" but not "myrnsd_wrapper" or shell scripts
        result = subprocess.run(
            ['pgrep', '-f', f'(^|/)({process_name})(\\s|$)'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            for pid_str in result.stdout.strip().split('\n'):
                pid_str = pid_str.strip()
                if pid_str and _verify_process_cmdline(pid_str, process_name):
                    return True

        # Fallback: Check via pgrep for python-based services (e.g., python3 -m rnsd)
        # Use tight regex + /proc/cmdline verification to prevent false positives
        # from shell invocations that merely reference the process name as a string.
        if process_name in ('rnsd', 'nomadnet'):
            result = subprocess.run(
                ['pgrep', '-f',
                 f'python3?\\s+(-m\\s+)?{process_name}(\\s|$)'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                for pid_str in result.stdout.strip().split('\n'):
                    pid_str = pid_str.strip()
                    if not pid_str:
                        continue
                    if _verify_process_cmdline(pid_str, process_name):
                        return True

        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def check_process_with_pid(process_name: str) -> Tuple[bool, Optional[str]]:
    """
    Check if a process is running and return its PID.

    Args:
        process_name: Name of the process to check (e.g., 'rnsd', 'meshtasticd')

    Returns:
        Tuple of (is_running, pid) where pid is the first matching PID or None

    Example:
        >>> running, pid = check_process_with_pid('rnsd')
        >>> if running:
        ...     print(f"rnsd is running (PID: {pid})")
    """
    try:
        # First try exact process name match (most reliable)
        result = subprocess.run(
            ['pgrep', '-x', process_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().split('\n')[0]
            return True, pid

        # Also check with -f using word boundaries + cmdline verification
        result = subprocess.run(
            ['pgrep', '-f', f'(^|/)({process_name})(\\s|$)'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            for pid_str in result.stdout.strip().split('\n'):
                pid_str = pid_str.strip()
                if pid_str and _verify_process_cmdline(pid_str, process_name):
                    return True, pid_str

        return False, None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False, None


def check_systemd_service(
    service_name: str, user: bool = False
) -> Tuple[bool, bool]:
    """
    Check if a systemd service is running and enabled.

    Args:
        service_name: Name of the systemd service
        user: When True, query the user-scope manager (``systemctl --user``).

    Returns:
        Tuple of (is_running, is_enabled)
    """
    is_running = False
    is_enabled = False

    base = ['systemctl', '--user'] if user else ['systemctl']

    try:
        result = subprocess.run(
            base + ['is-active', service_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        is_running = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        result = subprocess.run(
            base + ['is-enabled', service_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        is_enabled = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return is_running, is_enabled
