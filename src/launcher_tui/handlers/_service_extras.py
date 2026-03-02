"""Service extras — OpenHamClock Docker, MQTT wizard, install, and service actions.

Extracted from service_menu.py for file size compliance (CLAUDE.md #6).
Functions receive the ServiceMenuHandler instance for access to ctx and utility methods.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from backend import clear_screen
from utils.service_check import (
    check_systemd_service, check_process_running, check_service,
    enable_service, start_service, stop_service, restart_service,
    ServiceState, check_rns_shared_instance,
)
from utils.paths import get_real_user_home
from commands import propagation

logger = logging.getLogger(__name__)


# ---- OpenHamClock Docker Management --------------------------------------

def manage_openhamclock_docker(handler):
    """Manage OpenHamClock as a Docker container."""
    docker_bin = shutil.which('docker')
    if not docker_bin:
        handler.ctx.dialog.msgbox(
            "Docker Not Found",
            "Docker is required for OpenHamClock.\n\n"
            "Install Docker:\n"
            "  curl -fsSL https://get.docker.com | sh\n"
            "  sudo usermod -aG docker $USER"
        )
        return

    def _hamclock_choices():
        running = is_openhamclock_running(handler)
        status_str = "Running" if running else "Stopped"
        return [
            ("status", f"Status: {status_str}"),
            ("start", "Start OpenHamClock"),
            ("stop", "Stop OpenHamClock"),
            ("logs", "View Logs"),
            ("configure", "Configure in MeshForge"),
        ]

    dispatch = {
        "status": ("OpenHamClock Status", lambda: openhamclock_docker_status(handler)),
        "start": ("Start OpenHamClock", lambda: start_openhamclock_docker(handler)),
        "stop": ("Stop OpenHamClock", lambda: stop_openhamclock_docker(handler)),
        "logs": ("OpenHamClock Logs", lambda: openhamclock_docker_logs(handler)),
        "configure": ("Configure OpenHamClock", lambda: configure_openhamclock_via_settings(handler)),
    }
    handler.run_menu_loop(
        "OpenHamClock (Docker)",
        "Manage OpenHamClock container.\n"
        "Community replacement for HamClock.\n"
        "https://github.com/accius/openhamclock",
        _hamclock_choices, dispatch,
    )

def configure_openhamclock_via_settings(handler):
    """Delegate OpenHamClock configuration to SettingsHandler."""
    settings = handler.ctx.registry.get_handler("settings")
    if settings:
        settings._configure_openhamclock()
    else:
        handler.ctx.dialog.msgbox("Not Available", "Settings handler not loaded.")

def is_openhamclock_running(handler) -> bool:
    """Check if OpenHamClock Docker container is running."""
    try:
        result = subprocess.run(
            ['docker', 'ps', '--filter', 'name=openhamclock',
             '--filter', 'status=running', '--format', '{{.Names}}'],
            capture_output=True, text=True, timeout=10
        )
        return 'openhamclock' in result.stdout.lower()
    except (subprocess.SubprocessError, OSError):
        return False


def openhamclock_docker_status(handler):
    """Show OpenHamClock Docker container status."""
    clear_screen()
    print("=== OpenHamClock Docker Status ===\n")

    try:
        result = subprocess.run(
            ['docker', 'ps', '-a', '--filter', 'name=openhamclock',
             '--format', 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            print(result.stdout)
        else:
            print("No OpenHamClock container found.\n")
            print("Start with: docker run -d --name openhamclock -p 3000:3000 openhamclock")
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Error checking status: {e}")

    handler.ctx.wait_for_enter()


def start_openhamclock_docker(handler):
    """Start OpenHamClock Docker container."""
    clear_screen()
    print("=== Starting OpenHamClock ===\n")

    try:
        result = subprocess.run(
            ['docker', 'ps', '-a', '--filter', 'name=openhamclock',
             '--format', '{{.Names}}'],
            capture_output=True, text=True, timeout=10
        )

        if 'openhamclock' in result.stdout.lower():
            print("Starting existing container...")
            result = subprocess.run(
                ['docker', 'start', 'openhamclock'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                print("\033[0;32m✓\033[0m OpenHamClock started on port 3000")
            else:
                print(f"\033[0;31mError:\033[0m {result.stderr}")
        else:
            print("Pulling and starting OpenHamClock...")
            print("(This may take a moment on first run)\n")
            result = subprocess.run(
                ['docker', 'run', '-d',
                 '--name', 'openhamclock',
                 '-p', '3000:3000',
                 '--restart', 'unless-stopped',
                 'ghcr.io/accius/openhamclock:latest'],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                print("\033[0;32m✓\033[0m OpenHamClock started on port 3000")
                print("\nAuto-configuring MeshForge...")
                propagation.configure_source(
                    propagation.DataSource.OPENHAMCLOCK,
                    host="localhost", port=3000
                )
                print("\033[0;32m✓\033[0m MeshForge configured for OpenHamClock")
            else:
                print(f"\033[0;31mError:\033[0m {result.stderr}")

    except subprocess.TimeoutExpired:
        print("\033[0;31mError:\033[0m Docker operation timed out.")
    except (subprocess.SubprocessError, OSError) as e:
        print(f"\033[0;31mError:\033[0m {e}")

    handler.ctx.wait_for_enter()


def stop_openhamclock_docker(handler):
    """Stop OpenHamClock Docker container."""
    clear_screen()
    print("=== Stopping OpenHamClock ===\n")

    try:
        result = subprocess.run(
            ['docker', 'stop', 'openhamclock'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print("\033[0;32m✓\033[0m OpenHamClock stopped.")
        else:
            print(f"\033[0;31mError:\033[0m {result.stderr}")
    except subprocess.TimeoutExpired:
        print("\033[0;31mError:\033[0m Stop operation timed out.")
    except (subprocess.SubprocessError, OSError) as e:
        print(f"\033[0;31mError:\033[0m {e}")

    handler.ctx.wait_for_enter()


def openhamclock_docker_logs(handler):
    """Show OpenHamClock Docker container logs."""
    clear_screen()
    print("=== OpenHamClock Logs (last 30) ===\n")

    try:
        subprocess.run(
            ['docker', 'logs', '--tail', '30', 'openhamclock'],
            timeout=15
        )
    except subprocess.TimeoutExpired:
        print("Log retrieval timed out.")
    except (subprocess.SubprocessError, OSError) as e:
        print(f"Error: {e}")

    handler.ctx.wait_for_enter()


# ---- MQTT Setup Wizard ----------------------------------------------------

def mqtt_setup_wizard(handler):
    """MQTT setup wizard - install mosquitto and configure meshtasticd."""
    if not handler.ctx.dialog.yesno(
        "MQTT Setup Wizard",
        "This wizard will set up local MQTT architecture:\n\n"
        "1. Install mosquitto MQTT broker\n"
        "2. Configure meshtasticd to publish to local broker\n"
        "3. Enable uplink on primary channel\n\n"
        "Benefits:\n"
        "• Multiple apps can receive mesh messages\n"
        "• No more TCP one-client limitation\n"
        "• Works with meshing-around, Grafana, etc.\n\n"
        "Continue with setup?"
    ):
        return

    handler.ctx.dialog.infobox("MQTT Setup", "Step 1/3: Checking mosquitto...")

    if not is_mosquitto_installed(handler):
        if handler.ctx.dialog.yesno(
            "Install Mosquitto",
            "Mosquitto MQTT broker is not installed.\n\n"
            "Install it now?\n\n"
            "This will run: apt install mosquitto mosquitto-clients"
        ):
            if not install_mosquitto(handler):
                return
        else:
            handler.ctx.dialog.msgbox(
                "Setup Cancelled",
                "MQTT setup requires mosquitto.\n\n"
                "Install manually with:\n"
                "  sudo apt install mosquitto mosquitto-clients"
            )
            return
    else:
        handler.ctx.dialog.infobox("MQTT Setup", "Mosquitto is already installed.")

    handler.ctx.dialog.infobox("MQTT Setup", "Step 2/3: Starting mosquitto service...")
    if not ensure_mosquitto_running(handler):
        handler.ctx.dialog.msgbox(
            "Warning",
            "Could not start mosquitto service.\n\n"
            "Check: sudo systemctl status mosquitto"
        )

    handler.ctx.dialog.infobox("MQTT Setup", "Step 3/3: Configuring meshtasticd...")

    channel_name = auto_detect_primary_channel(handler)

    if not configure_meshtasticd_mqtt_local(handler, channel_name):
        handler.ctx.dialog.msgbox(
            "Warning",
            "Could not fully configure meshtasticd MQTT.\n\n"
            "You may need to configure manually:\n"
            "  meshtastic --set mqtt.enabled true\n"
            "  meshtastic --set mqtt.address localhost\n"
            "  meshtastic --set mqtt.json_enabled true\n"
            "  meshtastic --ch-index 0 --ch-set uplink_enabled true"
        )
        return

    topic_pattern = f"msh/2/json/{channel_name}/#" if channel_name else "msh/2/json/+/#"
    handler.ctx.dialog.msgbox(
        "MQTT Setup Complete",
        "Local MQTT architecture is ready!\n\n"
        "Services:\n"
        f"  • Mosquitto: localhost:1883\n"
        f"  • Topic: {topic_pattern}\n\n"
        "Test with:\n"
        f"  mosquitto_sub -h localhost -t 'msh/#' -v\n\n"
        "MeshForge will now receive messages via MQTT\n"
        "alongside other consumers like meshing-around."
    )


def is_mosquitto_installed(handler) -> bool:
    """Check if mosquitto is installed."""
    try:
        result = subprocess.run(
            ['which', 'mosquitto'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("mosquitto install check failed: %s", e)
        return False


def install_mosquitto(handler) -> bool:
    """Install mosquitto MQTT broker."""
    clear_screen()
    print("=== Installing Mosquitto MQTT Broker ===\n")

    try:
        print("Updating package list...")
        subprocess.run(['apt-get', 'update'], timeout=120)

        print("\nInstalling mosquitto and mosquitto-clients...")
        result = subprocess.run(
            ['apt-get', 'install', '-y', 'mosquitto', 'mosquitto-clients'],
            timeout=300
        )

        if result.returncode != 0:
            print("\n\033[0;31mError:\033[0m Installation failed.")
            handler.ctx.wait_for_enter()
            return False

        print("\n\033[0;32m✓\033[0m Mosquitto installed successfully.")
        handler.ctx.wait_for_enter()
        return True

    except subprocess.TimeoutExpired:
        print("\n\033[0;31mError:\033[0m Installation timed out.")
        handler.ctx.wait_for_enter()
        return False
    except Exception as e:
        print(f"\n\033[0;31mError:\033[0m {e}")
        handler.ctx.wait_for_enter()
        return False


def ensure_mosquitto_running(handler) -> bool:
    """Ensure mosquitto service is running and enabled."""
    try:
        success, msg = enable_service('mosquitto', start=True)
        return success
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("mosquitto start/verify failed: %s", e)
        return False


def auto_detect_primary_channel(handler) -> Optional[str]:
    """Auto-detect primary channel name from meshtasticd."""
    try:
        cli = shutil.which('meshtastic') or 'meshtastic'
        result = subprocess.run(
            [cli, '--host', 'localhost', '--ch-index', '0', '--info'],
            capture_output=True, text=True, timeout=15
        )

        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'name' in line.lower():
                    parts = line.split(':')
                    if len(parts) >= 2:
                        name = parts[1].strip().strip('"\'')
                        if name and name.lower() != 'none':
                            return name

    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.debug("Channel auto-detect failed: %s", e)

    return None


def configure_meshtasticd_mqtt_local(handler, channel_name: Optional[str] = None) -> bool:
    """Configure meshtasticd to use local mosquitto broker."""
    clear_screen()
    print("=== Configuring meshtasticd for Local MQTT ===\n")

    cli = shutil.which('meshtastic') or 'meshtastic'
    success = True

    try:
        print("Enabling MQTT...")
        result = subprocess.run(
            [cli, '--host', 'localhost', '--set', 'mqtt.enabled', 'true'],
            timeout=15
        )
        if result.returncode != 0:
            success = False

        print("Setting broker to localhost...")
        result = subprocess.run(
            [cli, '--host', 'localhost', '--set', 'mqtt.address', 'localhost'],
            timeout=15
        )
        if result.returncode != 0:
            success = False

        print("Enabling JSON mode...")
        result = subprocess.run(
            [cli, '--host', 'localhost', '--set', 'mqtt.json_enabled', 'true'],
            timeout=15
        )
        if result.returncode != 0:
            success = False

        print("Enabling uplink on primary channel...")
        result = subprocess.run(
            [cli, '--host', 'localhost',
             '--ch-index', '0', '--ch-set', 'uplink_enabled', 'true'],
            timeout=15
        )
        if result.returncode != 0:
            success = False

        if success:
            print(f"\n\033[0;32m✓\033[0m Configuration complete!")
            if channel_name:
                print(f"  Channel: {channel_name}")
            print(f"  Broker: localhost:1883")
            print(f"  JSON mode: enabled")
            print(f"  Uplink: enabled (channel 0)")
        else:
            print("\n\033[0;33mWarning:\033[0m Some settings may have failed.")
            print("Check meshtasticd is running: sudo systemctl status meshtasticd")

        handler.ctx.wait_for_enter()
        return success

    except Exception as e:
        print(f"\n\033[0;31mError:\033[0m {e}")
        handler.ctx.wait_for_enter()
        return False


# ---- Service Status Display -----------------------------------------------

def show_all_service_status(handler):
    """Show status of all mesh services."""
    clear_screen()
    print("=== Service Status ===\n")
    warnings = []
    failed_services = []
    use_direct_rnsd = not handler._has_systemd_unit('rnsd')

    for svc in ['meshtasticd', 'rnsd', 'meshforge']:
        if svc == 'meshforge':
            is_systemd = False
            try:
                svc_status = check_service(svc)
                is_systemd = svc_status.available
            except Exception:
                pass

            if is_systemd:
                print(f"  \033[0;32m●\033[0m {svc:<18} running (service)")
            else:
                print(f"  \033[0;32m●\033[0m {svc:<18} running (interactive)")
            continue

        if svc == 'rnsd' and use_direct_rnsd:
            if check_process_running('rnsd'):
                print(f"  \033[0;32m●\033[0m {svc:<18} running (process)")
            else:
                print(f"  \033[2m○\033[0m {svc:<18} stopped")
            continue

        try:
            svc_status = check_service(svc)
            _, is_enabled = check_systemd_service(svc)

            boot_info = ""
            if svc_status.available and not is_enabled:
                boot_info = "  (not enabled at boot)"
                warnings.append(svc)

            if svc_status.available:
                if svc == 'rnsd' and not check_rns_shared_instance():
                    print(f"  \033[0;33m●\033[0m {svc:<18} running (shared instance not available)")
                else:
                    print(f"  \033[0;32m●\033[0m {svc:<18} running{boot_info}")
            elif svc_status.state in (ServiceState.FAILED, ServiceState.DEGRADED):
                print(f"  \033[0;31m●\033[0m {svc:<18} FAILED")
                failed_services.append(svc)
            elif svc_status.state == ServiceState.NOT_RUNNING:
                print(f"  \033[2m○\033[0m {svc:<18} stopped")
            else:
                print(f"  \033[2m○\033[0m {svc:<18} {svc_status.state.value}")
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Service status check for %s failed: %s", svc, e)
            print(f"  ? {svc:<18} unknown")
    print()

    if warnings:
        print(f"  \033[0;33mWarning:\033[0m {', '.join(warnings)} won't start on reboot.")
        print(f"  Fix: sudo systemctl enable {' '.join(warnings)}\n")

    for svc in failed_services:
        try:
            print(f"\033[0;31m{svc} failure:\033[0m")
            subprocess.run(
                ['journalctl', '-u', svc, '-n', '5', '--no-pager'],
                timeout=10
            )
            print()
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Failure log check for %s failed: %s", svc, e)
    handler.ctx.wait_for_enter()


# ---- Native meshtasticd Installation --------------------------------------

def install_native_meshtasticd(handler):
    """Install native meshtasticd for SPI HAT."""
    handler.ctx.dialog.infobox("Installing", "Installing native meshtasticd...")

    try:
        result = subprocess.run(['which', 'meshtasticd'], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            handler.ctx.dialog.infobox("Installing", "Adding Meshtastic repository...")

            os_repo = "Raspbian_12"
            if Path('/etc/os-release').exists():
                os_info = {}
                with open('/etc/os-release') as f:
                    for line in f:
                        if '=' in line:
                            key, val = line.strip().split('=', 1)
                            os_info[key] = val.strip('"')

                os_id = os_info.get('ID', '')
                version_id = os_info.get('VERSION_ID', '')

                if os_id == 'raspbian':
                    os_repo = f"Raspbian_{version_id.split('.')[0]}" if version_id else "Raspbian_12"
                elif os_id == 'debian':
                    os_repo = f"Debian_{version_id.split('.')[0]}" if version_id else "Debian_12"
                elif os_id == 'ubuntu':
                    os_repo = f"xUbuntu_{version_id}" if version_id else "xUbuntu_24.04"

            repo_url = f"https://download.opensuse.org/repositories/network:/Meshtastic:/beta/{os_repo}/"

            subprocess.run(
                ['tee', '/etc/apt/sources.list.d/meshtastic.list'],
                input=f"deb {repo_url} /\n",
                text=True, timeout=30, check=False
            )

            key_result = subprocess.run(
                ['curl', '-fsSL', f'{repo_url}Release.key'],
                capture_output=True, timeout=30, check=False
            )
            if key_result.returncode == 0:
                subprocess.run(
                    ['gpg', '--dearmor', '-o', '/etc/apt/trusted.gpg.d/meshtastic.gpg'],
                    input=key_result.stdout, timeout=30, check=False
                )

            handler.ctx.dialog.infobox("Installing", "Updating package list...")
            subprocess.run(['apt-get', 'update'], timeout=120, check=False)

            handler.ctx.dialog.infobox("Installing", "Installing meshtasticd...")
            result = subprocess.run(['apt-get', 'install', '-y', 'meshtasticd'], timeout=300, capture_output=True, text=True)

            if result.returncode != 0:
                handler.ctx.dialog.msgbox("Error", f"Failed to install meshtasticd:\n{result.stderr[:500]}")
                return

        result = subprocess.run(['which', 'meshtasticd'], capture_output=True, text=True, timeout=5)
        meshtasticd_bin = result.stdout.strip() if result.returncode == 0 else '/usr/bin/meshtasticd'

        config_dir = Path('/etc/meshtasticd')
        config_yaml = config_dir / 'config.yaml'
        from core.meshtasticd_config import MeshtasticdConfig
        MeshtasticdConfig().ensure_structure()
        if config_yaml.exists():
            handler.ctx.dialog.infobox("Installing", "Config structure ready")

        usb_config = config_dir / 'config.d' / 'usb-serial.yaml'
        if usb_config.exists():
            usb_config.unlink()
            handler.ctx.dialog.infobox("Installing", "Removed incorrect USB config")

        service_content = f"""[Unit]
Description=Meshtastic Daemon (Native SPI)
Documentation=https://meshtastic.org
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/etc/meshtasticd
ExecStart={meshtasticd_bin} -c /etc/meshtasticd/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        Path('/etc/systemd/system/meshtasticd.service').write_text(service_content)

        success, msg = enable_service('meshtasticd', start=True)
        if not success:
            handler.ctx.dialog.msgbox("Warning", f"Service setup issue: {msg}")

        handler.ctx.dialog.msgbox(
            "Success",
            "Native meshtasticd installed!\n\n"
            "NEXT STEP: Select your HAT config:\n"
            "  meshtasticd → Hardware Config\n\n"
            "Or manually:\n"
            "  ls /etc/meshtasticd/available.d/\n"
            "  sudo cp /etc/meshtasticd/available.d/<your-hat>.yaml \\\n"
            "         /etc/meshtasticd/config.d/\n"
            "  sudo systemctl restart meshtasticd"
        )

    except Exception as e:
        handler.ctx.dialog.msgbox("Error", f"Installation failed:\n{e}")


# ---- Service Action Dispatch ----------------------------------------------

def service_action(handler, service_name: str, action: str):
    """Perform service action using systemctl or direct process control."""
    clear_screen()

    use_direct_rnsd = (service_name == 'rnsd' and
                      not handler._has_systemd_unit('rnsd'))

    if action == "status":
        print(f"=== {service_name} status ===\n")
        if use_direct_rnsd:
            if check_process_running('rnsd'):
                print(f"\033[0;32m●\033[0m rnsd is \033[0;32mrunning\033[0m")
                try:
                    subprocess.run(
                        ['pgrep', '-a', '-x', 'rnsd'],
                        timeout=5
                    )
                except (subprocess.SubprocessError, OSError) as e:
                    logger.debug("rnsd process info display failed: %s", e)
            else:
                print(f"\033[0;31m○\033[0m rnsd is \033[0;31mnot running\033[0m")
                print("\nTo start: Select 'Start Service' from the menu")
        else:
            subprocess.run(
                ['systemctl', 'status', service_name, '--no-pager', '-l'],
                timeout=10
            )
        handler.ctx.wait_for_enter()

    elif action == "start":
        print(f"Starting {service_name}...\n")
        if use_direct_rnsd:
            handler._start_rnsd_direct()
        else:
            success, msg = start_service(service_name)
            print(msg)
            subprocess.run(
                ['systemctl', 'status', service_name, '--no-pager', '-l'],
                timeout=10
            )
        handler.ctx.wait_for_enter()

    elif action == "stop":
        if handler.ctx.dialog.yesno("Confirm", f"Stop {service_name}?", default_no=True):
            clear_screen()
            print(f"Stopping {service_name}...\n")
            if use_direct_rnsd:
                handler._stop_rnsd_direct()
            else:
                success, msg = stop_service(service_name)
                print(msg)
            handler.ctx.wait_for_enter()

    elif action == "restart":
        print(f"Restarting {service_name}...\n")
        if use_direct_rnsd:
            handler._stop_rnsd_direct()
            import time
            time.sleep(0.5)
            handler._start_rnsd_direct()
        else:
            success, msg = restart_service(service_name)
            print(msg)
            subprocess.run(
                ['systemctl', 'status', service_name, '--no-pager', '-l'],
                timeout=10
            )
        handler.ctx.wait_for_enter()

    elif action == "logs":
        print(f"=== {service_name} logs (last 30) ===\n")
        if use_direct_rnsd:
            try:
                log_path = get_real_user_home() / '.reticulum' / 'logfile'
                if log_path.exists():
                    print(f"Log file: {log_path}\n")
                    subprocess.run(
                        ['tail', '-n', '30', str(log_path)],
                        timeout=10
                    )
                else:
                    print("No log file found at ~/.reticulum/logfile")
                    print("rnsd may log to stdout or syslog depending on config.")
            except Exception as e:
                print(f"Could not read logs: {e}")
        else:
            subprocess.run(
                ['journalctl', '-u', service_name, '-n', '30', '--no-pager'],
                timeout=15
            )
        handler.ctx.wait_for_enter()
