"""
LXMF Exclusivity Utility — Shared between MeshChat and NomadNet handlers.

MeshChat and NomadNet both use LXMF and can conflict on port 37428
when connecting to rnsd.  Only one should run at a time.

Extracted from meshchat_client_mixin.py as part of the Batch 8 migration.
"""

import subprocess
import time
from typing import Callable, Optional

from utils.safe_import import safe_import

check_process_running, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running'
)

MESHCHAT_SERVICE_NAME = "reticulum-meshchat"


def ensure_lxmf_exclusive(
    dialog,
    starting_app: str,
    is_meshchat_running_fn: Optional[Callable[[], bool]] = None,
) -> bool:
    """Ensure only one LXMF app runs at a time.

    Args:
        dialog: DialogBackend instance for user prompts.
        starting_app: ``"meshchat"`` or ``"nomadnet"``.
        is_meshchat_running_fn: Optional callable returning True when MeshChat
            is running.  If *None*, a pgrep fallback is used.

    Returns:
        True if OK to proceed, False if the user cancelled.
    """
    if starting_app == "meshchat":
        # Check if NomadNet is running
        nomadnet_running = False
        if _HAS_SERVICE_CHECK and check_process_running:
            nomadnet_running = check_process_running('nomadnet')
        if not nomadnet_running:
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'bin/nomadnet'],
                    capture_output=True, text=True, timeout=5,
                )
                nomadnet_running = (
                    result.returncode == 0 and
                    bool(result.stdout.strip())
                )
            except (subprocess.SubprocessError, OSError):
                pass

        if nomadnet_running:
            if not dialog.yesno(
                "NomadNet Running",
                "NomadNet is currently running.\n\n"
                "Only one LXMF app should run at a time\n"
                "to avoid port 37428 conflicts.\n\n"
                "Stop NomadNet and start MeshChat?",
            ):
                return False
            # Stop NomadNet
            try:
                subprocess.run(
                    ['pkill', '-f', 'bin/nomadnet'],
                    capture_output=True, timeout=10,
                )
                time.sleep(2)
            except (subprocess.SubprocessError, OSError):
                pass

    elif starting_app == "nomadnet":
        # Check if MeshChat is running
        meshchat_running = False
        if is_meshchat_running_fn is not None:
            meshchat_running = is_meshchat_running_fn()
        else:
            # Fallback: pgrep
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'meshchat.py'],
                    capture_output=True, text=True, timeout=5,
                )
                meshchat_running = (
                    result.returncode == 0 and
                    bool(result.stdout.strip())
                )
            except (subprocess.SubprocessError, OSError):
                pass

        if meshchat_running:
            if not dialog.yesno(
                "MeshChat Running",
                "MeshChat is currently running.\n\n"
                "Only one LXMF app should run at a time\n"
                "to avoid port 37428 conflicts.\n\n"
                "Stop MeshChat and start NomadNet?",
            ):
                return False
            # Stop MeshChat
            try:
                subprocess.run(
                    ['systemctl', 'stop', MESHCHAT_SERVICE_NAME],
                    capture_output=True, timeout=15,
                )
            except (subprocess.SubprocessError, OSError):
                pass
            try:
                subprocess.run(
                    ['pkill', '-f', 'meshchat.py'],
                    capture_output=True, timeout=5,
                )
            except (subprocess.SubprocessError, OSError):
                pass
            time.sleep(2)

    return True
