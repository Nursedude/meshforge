"""Port lockdown and file write utilities using iptables and sudo.

Extracted from service_check.py for file size compliance (CLAUDE.md #6).

Provides:
  _sudo_write()           — Write file content with sudo tee privilege elevation
  lock_port_external()    — Block external access to a TCP port via iptables
  unlock_port_external()  — Remove iptables block on a port
  check_port_locked()     — Check if iptables rule exists for a port
  persist_iptables()      — Save iptables rules to survive reboot
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Tuple

from utils.service_check import _sudo_cmd

logger = logging.getLogger(__name__)


def _sudo_write(file_path: str, content: str, timeout: int = 10) -> Tuple[bool, str]:
    """
    Write content to a file, using sudo tee for privilege elevation when needed.

    Use this for writing to system paths (/etc/, /boot/, /etc/systemd/system/)
    where the current user may not have write access.

    When already running as root, writes directly. When running as a normal user,
    uses 'sudo tee' to elevate privileges for the write.

    Args:
        file_path: Absolute path to the file to write
        content: String content to write
        timeout: Timeout in seconds for the sudo tee command (default: 10)

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        from utils.service_check import _sudo_write

        service_content = '''[Unit]
        Description=My Service
        ...
        '''
        success, msg = _sudo_write('/etc/systemd/system/my.service', service_content)
        if not success:
            show_error(msg)
    """
    try:
        if os.geteuid() == 0:
            # Already root — write directly
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w') as f:
                f.write(content)
            logger.debug(f"Wrote {file_path} (as root)")
            return True, f"Wrote {file_path}"

        # Not root — use sudo tee to write with elevation
        result = subprocess.run(
            ['sudo', 'tee', file_path],
            input=content,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"Failed to write {file_path}"
            logger.error(f"sudo tee failed for {file_path}: {error_msg}")
            return False, f"Failed to write {file_path}: {error_msg}"

        logger.debug(f"Wrote {file_path} (via sudo tee)")
        return True, f"Wrote {file_path}"

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout writing {file_path}")
        return False, f"Timeout writing {file_path}"
    except PermissionError:
        logger.error(f"Permission denied writing {file_path}")
        return False, f"Permission denied: {file_path}"
    except OSError as e:
        logger.error(f"OS error writing {file_path}: {e}")
        return False, f"OS error: {e}"
    except Exception as e:
        logger.error(f"Error writing {file_path}: {e}")
        return False, f"Error: {e}"


# ─────────────────────────────────────────────────────────────────────
# Port lockdown — MeshForge owns the browser
# ─────────────────────────────────────────────────────────────────────

def lock_port_external(port: int = 9443, timeout: int = 10) -> Tuple[bool, str]:
    """Block external access to a port, allowing only localhost.

    Used to prevent users from accessing meshtasticd's web server directly
    at port 9443.  MeshForge serves the web client at port 5000/mesh/
    with multiplexed API proxying and phantom node filtering.

    This adds an iptables INPUT rule that rejects non-localhost traffic
    to the specified port.  The rule is idempotent — calling multiple
    times won't create duplicate rules.

    Args:
        port: TCP port to lock down (default: 9443 for meshtasticd)
        timeout: subprocess timeout in seconds

    Returns:
        Tuple of (success, message)
    """
    rule_args = ['-p', 'tcp', '--dport', str(port),
                 '!', '-s', '127.0.0.1', '-j', 'REJECT']

    try:
        # Check if rule already exists (idempotent)
        check = subprocess.run(
            _sudo_cmd(['iptables', '-C', 'INPUT'] + rule_args),
            capture_output=True, text=True, timeout=timeout
        )
        if check.returncode == 0:
            logger.info("iptables rule for port %d already in place", port)
            return True, f"Port {port} already locked to localhost"

        # Add the rule
        result = subprocess.run(
            _sudo_cmd(['iptables', '-A', 'INPUT'] + rule_args),
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            logger.info("Locked external access to port %d (localhost only)", port)
            return True, f"Port {port} locked — external access blocked"
        else:
            error = result.stderr.strip() or "iptables command failed"
            logger.error("Failed to lock port %d: %s", port, error)
            return False, f"iptables error: {error}"

    except FileNotFoundError:
        logger.warning("iptables not found — port lockdown unavailable")
        return False, "iptables not found (install iptables package)"
    except subprocess.TimeoutExpired:
        return False, "iptables command timed out"
    except Exception as e:
        logger.error("Port lockdown error: %s", e)
        return False, f"Error: {e}"


def unlock_port_external(port: int = 9443, timeout: int = 10) -> Tuple[bool, str]:
    """Remove the iptables rule blocking external access to a port.

    Args:
        port: TCP port to unlock (default: 9443)
        timeout: subprocess timeout in seconds

    Returns:
        Tuple of (success, message)
    """
    rule_args = ['-p', 'tcp', '--dport', str(port),
                 '!', '-s', '127.0.0.1', '-j', 'REJECT']

    try:
        result = subprocess.run(
            _sudo_cmd(['iptables', '-D', 'INPUT'] + rule_args),
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            logger.info("Unlocked external access to port %d", port)
            return True, f"Port {port} unlocked — external access restored"
        else:
            # Rule may not exist — that's fine
            return True, f"Port {port} was already unlocked"

    except FileNotFoundError:
        return False, "iptables not found"
    except subprocess.TimeoutExpired:
        return False, "iptables command timed out"
    except Exception as e:
        return False, f"Error: {e}"


def check_port_locked(port: int = 9443, timeout: int = 10) -> bool:
    """Check if the iptables rule blocking external access exists.

    Args:
        port: TCP port to check (default: 9443)
        timeout: subprocess timeout in seconds

    Returns:
        True if the port is locked to localhost, False otherwise.
    """
    rule_args = ['-p', 'tcp', '--dport', str(port),
                 '!', '-s', '127.0.0.1', '-j', 'REJECT']
    try:
        result = subprocess.run(
            _sudo_cmd(['iptables', '-C', 'INPUT'] + rule_args),
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return False


def persist_iptables(timeout: int = 30) -> Tuple[bool, str]:
    """Save current iptables rules so they survive reboot.

    Tries netfilter-persistent first, then falls back to iptables-save
    to /etc/iptables/rules.v4.

    Returns:
        Tuple of (success, message)
    """
    # Method 1: netfilter-persistent (Debian/Ubuntu with iptables-persistent)
    try:
        result = subprocess.run(
            _sudo_cmd(['netfilter-persistent', 'save']),
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            logger.info("iptables rules saved via netfilter-persistent")
            return True, "Rules saved (netfilter-persistent)"
    except FileNotFoundError:
        pass  # Not installed, try fallback
    except subprocess.TimeoutExpired:
        return False, "netfilter-persistent save timed out"

    # Method 2: Manual iptables-save to rules.v4
    import shutil
    if not shutil.which('iptables-save'):
        return False, (
            "No persistence tool found.\n"
            "Install: sudo apt install iptables-persistent"
        )

    try:
        rules_dir = Path('/etc/iptables')
        rules_dir.mkdir(parents=True, exist_ok=True)
        rules_file = rules_dir / 'rules.v4'

        save_result = subprocess.run(
            _sudo_cmd(['iptables-save']),
            capture_output=True, text=True, timeout=timeout
        )
        if save_result.returncode != 0:
            return False, f"iptables-save failed: {save_result.stderr.strip()}"

        rules_file.write_text(save_result.stdout)
        logger.info("iptables rules saved to %s", rules_file)
        return True, f"Rules saved to {rules_file}"

    except subprocess.TimeoutExpired:
        return False, "iptables-save timed out"
    except OSError as e:
        return False, f"Failed to write rules file: {e}"
    except Exception as e:
        logger.error("persist_iptables error: %s", e)
        return False, f"Error: {e}"
