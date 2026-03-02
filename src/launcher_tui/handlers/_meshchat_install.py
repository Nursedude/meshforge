"""MeshChat install, uninstall, and RNS preflight operations.

Extracted from meshchat.py for file size compliance (CLAUDE.md #6).
Functions take the MeshChatHandler instance for TUI interaction via handler.ctx.

Operations in this module:
  get_meshchat_install_dir  — Install directory path
  get_pip_command           — Pip command builder (venv/system/PEP 668)
  install_meshchat          — Full automated install flow
  install_meshchat_prerequisites — git, nodejs, npm install
  install_meshchat_clone    — git clone/pull
  install_meshchat_pip      — pip install dependencies
  install_meshchat_npm      — npm install + build frontend
  install_meshchat_service  — systemd service creation
  uninstall_meshchat        — Stop + disable service
  get_rnsd_user             — Cross-handler rnsd user lookup
  fix_rnsd_user             — Cross-handler rnsd user fix
  check_rns_for_meshchat    — RNS availability preflight
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from backend import clear_screen
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Install directory & pip helpers
# ------------------------------------------------------------------

def get_meshchat_install_dir(handler) -> Path:
    """Return the MeshChat install directory under user home."""
    return get_real_user_home() / 'reticulum-meshchat'


def get_pip_command(handler) -> list:
    """Return the pip command appropriate for this install.

    Prefers MeshForge's venv pip, falls back to system pip3
    with --break-system-packages for PEP 668 compatibility.
    """
    venv_pip = Path('/opt/meshforge/venv/bin/pip')
    if venv_pip.exists():
        return [str(venv_pip)]

    # Check for PEP 668 (externally-managed Python)
    import glob
    if glob.glob('/usr/lib/python3*/EXTERNALLY-MANAGED'):
        return ['pip3', 'install', '--break-system-packages']

    return ['pip3']


# ------------------------------------------------------------------
# Full install flow
# ------------------------------------------------------------------

def install_meshchat(handler):
    """Automated MeshChat installation.

    Steps:
    1. Confirm with user
    2. Check/install prerequisites (git, nodejs, npm)
    3. git clone reticulum-meshchat
    4. pip install -r requirements.txt
    5. npm install && npm run build-frontend
    6. Create systemd service
    7. Enable + start service
    """
    if handler._is_meshchat_installed():
        handler.ctx.dialog.msgbox(
            "Already Installed",
            "MeshChat is already installed.\n\n"
            "Use Start/Stop from the menu to manage it.",
        )
        return

    if not handler.ctx.dialog.yesno(
        "Install MeshChat",
        "Install MeshChat (Reticulum MeshChat)?\n\n"
        "This will:\n"
        "  1. Install Node.js/npm (if needed)\n"
        "  2. Clone the MeshChat repository\n"
        "  3. Install Python dependencies\n"
        "  4. Build the web frontend (npm)\n"
        "  5. Create a systemd service\n\n"
        "MeshChat provides LXMF messaging with a\n"
        "web UI at http://127.0.0.1:8000\n\n"
        "Source: github.com/liamcottle/reticulum-meshchat\n\n"
        "Install now?",
    ):
        return

    # LXMF exclusivity check — stop NomadNet if running
    if not handler._ensure_lxmf_exclusive("meshchat"):
        return

    clear_screen()
    print("=== Installing MeshChat ===\n")

    install_dir = get_meshchat_install_dir(handler)
    sudo_user = os.environ.get('SUDO_USER')
    run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

    try:
        # Step 1: Prerequisites
        if not install_meshchat_prerequisites(handler):
            handler.ctx.wait_for_enter()
            return

        # Step 2: Git clone
        if not install_meshchat_clone(handler, install_dir, run_as_user):
            handler.ctx.wait_for_enter()
            return

        # Step 3: pip install
        if not install_meshchat_pip(handler, install_dir, run_as_user):
            handler.ctx.wait_for_enter()
            return

        # Step 4: npm build
        if not install_meshchat_npm(handler, install_dir, run_as_user):
            handler.ctx.wait_for_enter()
            return

        # Step 5: systemd service
        if not install_meshchat_service(handler, install_dir, run_as_user):
            print("\nSystemd service creation failed.")
            print("You can still run MeshChat manually:")
            print(f"  cd {install_dir} && python3 meshchat.py")

        # Step 6: Start service
        print("\nStarting MeshChat service...")
        try:
            subprocess.run(
                ['systemctl', 'start', handler.MESHCHAT_SERVICE_NAME],
                capture_output=True, timeout=15,
            )
            time.sleep(3)
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Service start failed: %s", e)

        # Verify
        if handler._is_meshchat_running():
            print("\nMeshChat is running!")
            print("Web UI: http://127.0.0.1:8000")
        else:
            print("\nMeshChat installed but may not be running yet.")
            print(f"Check: systemctl status {handler.MESHCHAT_SERVICE_NAME}")

        print("\nInstallation complete.")

    except KeyboardInterrupt:
        print("\n\nInstallation cancelled.")
    except Exception as e:
        print(f"\nInstallation error: {e}")
        logger.exception("MeshChat install failed")

    try:
        handler.ctx.wait_for_enter()
    except (EOFError, KeyboardInterrupt):
        pass


# ------------------------------------------------------------------
# Install sub-steps
# ------------------------------------------------------------------

def install_meshchat_prerequisites(handler) -> bool:
    """Check and install git, nodejs, npm. Returns True on success."""
    # git
    if not shutil.which('git'):
        print("Installing git...")
        result = subprocess.run(
            ['apt-get', 'install', '-y', '-qq', 'git'],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            print("Failed to install git.")
            print("Try: sudo apt install git")
            return False

    # Node.js + npm
    if not shutil.which('node') or not shutil.which('npm'):
        print("Installing Node.js and npm...")
        result = subprocess.run(
            ['apt-get', 'install', '-y', '-qq', 'nodejs', 'npm'],
            capture_output=True, timeout=180,
        )
        if result.returncode != 0:
            print("Failed to install Node.js/npm.")
            print("Try: sudo apt install nodejs npm")
            return False
        print("Node.js and npm installed.")

    # Verify
    for tool in ['git', 'node', 'npm']:
        if not shutil.which(tool):
            print(f"Error: {tool} not found after install.")
            return False

    print("Prerequisites OK (git, node, npm)\n")
    return True


def install_meshchat_clone(handler, install_dir: Path, run_as_user: str = None) -> bool:
    """Clone the MeshChat repository. Returns True on success."""
    if install_dir.exists():
        print(f"Directory exists: {install_dir}")
        # Pull latest instead of clone
        print("Pulling latest changes...")
        cmd = ['git', '-C', str(install_dir), 'pull', '--ff-only']
        if run_as_user:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"Git pull failed: {result.stderr.strip()}")
            print("Continuing with existing checkout.")
        else:
            print("Repository updated.")
        return True

    print(f"Cloning MeshChat to {install_dir}...")
    cmd = ['git', 'clone', handler.MESHCHAT_REPO, str(install_dir)]
    if run_as_user:
        cmd = ['sudo', '-H', '-u', run_as_user] + cmd

    result = subprocess.run(cmd, timeout=120)
    if result.returncode != 0:
        print("Git clone failed.")
        print(f"Try: git clone {handler.MESHCHAT_REPO} {install_dir}")
        return False

    print("Repository cloned.\n")
    return True


def install_meshchat_pip(handler, install_dir: Path, run_as_user: str = None) -> bool:
    """Install MeshChat Python dependencies. Returns True on success."""
    req_file = install_dir / 'requirements.txt'
    if not req_file.exists():
        print("No requirements.txt found — skipping pip install.")
        return True

    print("Installing Python dependencies...")
    pip_cmd = get_pip_command(handler)

    # Build install command
    if len(pip_cmd) > 1 and pip_cmd[1] == 'install':
        # pip3 install --break-system-packages case
        cmd = pip_cmd + ['--timeout', '60', '-r', str(req_file)]
    else:
        cmd = pip_cmd + ['install', '--timeout', '60', '-r', str(req_file)]

    if run_as_user:
        cmd = ['sudo', '-H', '-u', run_as_user] + cmd

    result = subprocess.run(cmd, timeout=300)
    if result.returncode != 0:
        print("pip install failed.")
        print(f"Try: pip3 install -r {req_file}")
        return False

    print("Python dependencies installed.\n")
    return True


def install_meshchat_npm(handler, install_dir: Path, run_as_user: str = None) -> bool:
    """Build MeshChat web frontend with npm. Returns True on success."""
    pkg_json = install_dir / 'package.json'
    if not pkg_json.exists():
        print("No package.json found — skipping npm build.")
        return True

    print("Installing npm dependencies...")
    cmd = ['npm', 'install', '--omit=dev']
    if run_as_user:
        cmd = ['sudo', '-H', '-u', run_as_user] + cmd

    result = subprocess.run(cmd, cwd=str(install_dir), timeout=300)
    if result.returncode != 0:
        print("npm install failed.")
        print(f"Try: cd {install_dir} && npm install --omit=dev")
        return False

    print("Building web frontend...")
    cmd = ['npm', 'run', 'build-frontend']
    if run_as_user:
        cmd = ['sudo', '-H', '-u', run_as_user] + cmd

    result = subprocess.run(cmd, cwd=str(install_dir), timeout=300)
    if result.returncode != 0:
        print("npm build failed.")
        print(f"Try: cd {install_dir} && npm run build-frontend")
        return False

    print("Web frontend built.\n")
    return True


def install_meshchat_service(handler, install_dir: Path, run_as_user: str = None) -> bool:
    """Create systemd service for MeshChat. Returns True on success."""
    service_user = run_as_user or 'root'
    user_home = get_real_user_home()
    python_path = shutil.which('python3') or '/usr/bin/python3'
    meshchat_py = install_dir / 'meshchat.py'

    service_content = (
        f"[Unit]\n"
        f"Description=Reticulum MeshChat LXMF Client\n"
        f"After=network.target rnsd.service\n"
        f"Wants=rnsd.service\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"User={service_user}\n"
        f"WorkingDirectory={install_dir}\n"
        f"ExecStart={python_path} {meshchat_py}\n"
        f"Restart=on-failure\n"
        f"RestartSec=5\n"
        f"StartLimitBurst=5\n"
        f"StartLimitIntervalSec=60\n"
        f"Environment=HOME={user_home}\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=multi-user.target\n"
    )

    service_path = f"/etc/systemd/system/{handler.MESHCHAT_SERVICE_NAME}.service"
    print(f"Creating systemd service: {service_path}")

    try:
        # Write service file
        with open(service_path, 'w') as f:
            f.write(service_content)

        # Reload systemd and enable
        subprocess.run(
            ['systemctl', 'daemon-reload'],
            capture_output=True, timeout=15,
        )
        subprocess.run(
            ['systemctl', 'enable', handler.MESHCHAT_SERVICE_NAME],
            capture_output=True, timeout=15,
        )
        print("Service created and enabled.\n")
        return True

    except (IOError, OSError, subprocess.SubprocessError) as e:
        print(f"Failed to create service: {e}")
        return False


# ------------------------------------------------------------------
# Uninstall (stop + disable)
# ------------------------------------------------------------------

def uninstall_meshchat(handler):
    """Stop and disable MeshChat service.

    Leaves files in place for easy re-enable. Does not delete
    the cloned repository or configuration.
    """
    if not handler.ctx.dialog.yesno(
        "Disable MeshChat",
        "Stop and disable the MeshChat service?\n\n"
        "This will:\n"
        "  - Stop MeshChat if running\n"
        "  - Disable auto-start on boot\n\n"
        "Files remain at ~/reticulum-meshchat\n"
        "for easy re-enable later.\n\n"
        "Disable now?",
    ):
        return

    clear_screen()
    print("=== Disabling MeshChat ===\n")

    # Stop service
    try:
        print("Stopping MeshChat...")
        subprocess.run(
            ['systemctl', 'stop', handler.MESHCHAT_SERVICE_NAME],
            capture_output=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("Service stop: %s", e)

    # Kill any running process
    try:
        subprocess.run(
            ['pkill', '-f', 'meshchat.py'],
            capture_output=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        pass

    time.sleep(1)

    # Disable service
    try:
        print("Disabling auto-start...")
        subprocess.run(
            ['systemctl', 'disable', handler.MESHCHAT_SERVICE_NAME],
            capture_output=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("Service disable: %s", e)

    install_dir = get_meshchat_install_dir(handler)
    if handler._is_meshchat_running():
        print("\nMeshChat may still be running.")
        print("Try: sudo pkill -f meshchat.py")
    else:
        print("\nMeshChat stopped and disabled.")

    print(f"\nFiles remain at: {install_dir}")
    print("To re-enable: systemctl enable --now reticulum-meshchat")

    handler.ctx.wait_for_enter()


# ------------------------------------------------------------------
# Cross-handler helpers
# ------------------------------------------------------------------

def get_rnsd_user(handler):
    """Get the OS user running rnsd, via the rns_diagnostics handler.

    Returns None if the handler is unavailable or rnsd is not running.
    """
    if handler.ctx.registry:
        diag = handler.ctx.registry.get_handler("rns_diagnostics")
        if diag and hasattr(diag, '_get_rnsd_user'):
            return diag._get_rnsd_user()
    return None


def fix_rnsd_user(handler, target_user: str) -> bool:
    """Fix rnsd to run as the specified user, via the rns_diagnostics handler.

    Returns False if the handler is unavailable.
    """
    if handler.ctx.registry:
        diag = handler.ctx.registry.get_handler("rns_diagnostics")
        if diag and hasattr(diag, '_fix_rnsd_user'):
            return diag._fix_rnsd_user(target_user)
    return False


# ------------------------------------------------------------------
# Preflight: RNS check
# ------------------------------------------------------------------

def check_rns_for_meshchat(handler) -> bool:
    """Check that RNS is available for MeshChat.

    MeshChat can run with or without rnsd:
    - With rnsd: connects as shared instance client (recommended)
    - Without: starts its own RNS instance

    Returns True to proceed, False if user cancelled.
    """
    rnsd_user = get_rnsd_user(handler)

    if not rnsd_user:
        # rnsd not running — warn but allow proceeding
        return handler.ctx.dialog.yesno(
            "rnsd Not Running",
            "The RNS daemon (rnsd) is not running.\n\n"
            "MeshChat can start its own RNS instance,\n"
            "but for Meshtastic bridging you should run rnsd\n"
            "with share_instance = Yes in the Reticulum config.\n\n"
            "Continue anyway?",
        )

    # rnsd is running — check for root mismatch
    sudo_user = os.environ.get('SUDO_USER', '')
    if rnsd_user == 'root' and sudo_user and sudo_user != 'root':
        choice = handler.ctx.dialog.menu(
            "rnsd Running as Root",
            f"rnsd is running as root, but MeshChat should\n"
            f"use the same RNS identity as '{sudo_user}'.\n\n"
            "This may cause RPC authentication failures.",
            [
                ("continue", "Continue anyway"),
                ("fix", f"Fix rnsd to run as {sudo_user}"),
                ("cancel", "Cancel"),
            ],
        )
        if choice == "fix":
            fix_rnsd_user(handler, sudo_user)
            return True
        elif choice == "cancel" or choice is None:
            return False
        # "continue" falls through

    return True
