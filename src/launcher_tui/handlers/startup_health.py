"""
Startup Health Handler — Pre-main-menu config sanity checks.

Extracted from MeshForgeLauncher (main.py) to follow the dispatcher principle:
the TUI selects what to run, handlers contain the logic.

Runs during startup (called explicitly like FirstRunHandler) to detect and
fix service misconfigurations before the main menu appears.
"""

import logging
import subprocess
from pathlib import Path

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

try:
    from utils.service_check import apply_config_and_restart
    _HAS_SERVICE_CHECK = True
except ImportError:
    _HAS_SERVICE_CHECK = False

class StartupHealthHandler(BaseHandler):
    """Pre-main-menu config sanity checks.

    Called explicitly during startup (like FirstRunHandler), not via
    startup_all(), because these checks must run before daemon detection.
    """

    handler_id = "startup_health"
    menu_section = "system"

    def menu_items(self):
        return []

    def execute(self, action):
        pass

    # -- Lifecycle: called explicitly from main.py --

    def on_startup(self):
        """Run pre-main-menu health checks."""
        self._patch_rns_transport_race()
        self._check_service_misconfig()

    def _patch_rns_transport_race(self):
        """Apply RNS Transport.owner race condition fix if needed.

        RNS <= 1.1.4 crashes ~12h after rnsd start because __jobs
        calls persist_data() before Transport.owner is set. This
        runs the patch script silently on startup so it survives
        pip upgrades.
        """
        patch_script = Path(__file__).parent.parent.parent / 'scripts' / 'patch_rns_transport_race.py'
        if not patch_script.exists():
            return
        try:
            subprocess.run(
                ['python3', str(patch_script)],
                capture_output=True, timeout=10,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("RNS transport race patch: %s", e)

    # -- Internal --

    def _check_service_misconfig(self):
        """Check for service misconfiguration and offer to fix."""
        config_d = Path('/etc/meshtasticd/config.d')
        if not config_d.exists():
            return

        # Check what configs are active
        active_configs = list(config_d.glob('*.yaml'))
        usb_config = config_d / 'usb-serial.yaml'

        # Check for SPI configs
        spi_config_names = [
            'meshadv', 'waveshare', 'rak-hat', 'meshtoad', 'sx126', 'sx127', 'lora',
        ]
        has_spi_config = any(
            any(name in cfg.name.lower() for name in spi_config_names)
            for cfg in active_configs
        )

        # If SPI config exists AND usb-serial.yaml also exists, that's wrong
        if has_spi_config and usb_config.exists():
            spi_configs = [
                c.name for c in active_configs
                if any(n in c.name.lower() for n in spi_config_names)
            ]

            msg = "CONFLICTING CONFIGURATIONS!\n\n"
            msg += "Both SPI HAT and USB configs are active:\n\n"
            msg += f"  SPI: {', '.join(spi_configs)}\n"
            msg += f"  USB: usb-serial.yaml (WRONG)\n\n"
            msg += "Remove the USB config?"

            if self.ctx.dialog.yesno("Config Conflict", msg):
                try:
                    usb_config.unlink()
                    # Honest-signal: report whether the restart actually succeeded.
                    restart_note = ""
                    if _HAS_SERVICE_CHECK:
                        ok, rmsg = apply_config_and_restart('meshtasticd')
                        if ok:
                            restart_note = "Restarted meshtasticd\n"
                        else:
                            restart_note = (
                                f"meshtasticd restart FAILED: {rmsg}\n"
                                "Use the Service menu to restart meshtasticd.\n"
                            )
                    self.ctx.dialog.msgbox(
                        "Fixed",
                        "Removed usb-serial.yaml\n"
                        f"{restart_note}\n"
                        "Check meshtasticd in Service Control."
                    )
                except Exception as e:
                    self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")
            return

        # Check: SPI hardware present but USB config active (wrong)
        spi_devices = list(Path('/dev').glob('spidev*'))
        if not spi_devices:
            return

        if not usb_config.exists():
            return

        result = subprocess.run(
            ['which', 'meshtasticd'], capture_output=True, timeout=5,
        )
        has_native = result.returncode == 0

        msg = "CONFIGURATION MISMATCH!\n\n"
        msg += "SPI HAT detected but USB config active.\n\n"
        msg += f"SPI: {', '.join(d.name for d in spi_devices)}\n"
        msg += "Config: usb-serial.yaml (WRONG)\n"
        if not has_native:
            msg += "Native meshtasticd: NOT INSTALLED\n"
        msg += "\nFix this now?"

        if self.ctx.dialog.yesno("Service Misconfiguration", msg):
            # Delegate to ServiceMenuHandler which owns _fix_spi_config
            svc_handler = self.ctx.registry.get_handler("service_menu")
            if svc_handler and hasattr(svc_handler, '_fix_spi_config'):
                svc_handler._fix_spi_config(has_native)
            else:
                self.ctx.dialog.msgbox(
                    "Manual Fix Needed",
                    "Remove /etc/meshtasticd/config.d/usb-serial.yaml\n"
                    "Then restart: sudo systemctl restart meshtasticd"
                )
