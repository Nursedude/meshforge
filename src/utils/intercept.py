"""
Intercept SIGINT Integration - Bridge to Signal Intelligence Platform

Integrates with the Intercept project for RTL-SDR based signal intelligence:
- Pager decoding (POCSAG/FLEX)
- 433MHz sensor monitoring (weather, TPMS, IoT)
- Aircraft tracking (ADS-B via dump1090)
- ACARS messaging
- Frequency scanning
- Satellite tracking

Reference: https://github.com/smittix/intercept

MeshForge can launch Intercept as a companion service or embed
specific SIGINT tools directly.
"""

import logging
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from enum import Enum
import json

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class InterceptModule(Enum):
    """Intercept SIGINT modules"""
    PAGER = "pager"           # POCSAG/FLEX pager decoding
    ISM_433 = "ism_433"       # 433MHz sensors (rtl_433)
    ADSB = "adsb"             # Aircraft tracking (dump1090)
    ACARS = "acars"           # Aircraft datalink
    SCANNER = "scanner"        # Frequency scanner
    SATELLITE = "satellite"    # Satellite pass prediction
    WIFI = "wifi"             # WiFi reconnaissance
    BLUETOOTH = "bluetooth"    # Bluetooth discovery


@dataclass
class InterceptStatus:
    """Status of Intercept installation and services"""
    installed: bool = False
    running: bool = False
    version: str = ""
    web_url: str = ""
    rtl_sdr_available: bool = False
    active_modules: List[InterceptModule] = None

    def __post_init__(self):
        if self.active_modules is None:
            self.active_modules = []


class InterceptBridge:
    """
    Bridge between MeshForge and Intercept SIGINT platform.

    Provides:
    - Installation detection
    - Service management
    - Configuration
    - Direct tool access (rtl_433, dump1090, etc.)
    """

    # Default Intercept paths - computed at instance creation time
    # to properly resolve user home even under sudo (MF001)
    @property
    def INTERCEPT_PATHS(self):
        return [
            get_real_user_home() / "intercept",
            Path("/opt/intercept"),
            Path("/usr/local/intercept"),
        ]

    # Default web port
    WEB_PORT = 5000

    # Required tools for each module
    MODULE_TOOLS = {
        InterceptModule.PAGER: ["rtl_fm", "multimon-ng"],
        InterceptModule.ISM_433: ["rtl_433"],
        InterceptModule.ADSB: ["dump1090"],
        InterceptModule.ACARS: ["acarsdec"],
        InterceptModule.SCANNER: ["rtl_fm"],
        InterceptModule.WIFI: ["aircrack-ng"],
        InterceptModule.BLUETOOTH: ["bluetoothctl", "hcitool"],
    }

    def __init__(self):
        self._install_path: Optional[Path] = None
        self._status: Optional[InterceptStatus] = None

    def check_status(self) -> InterceptStatus:
        """
        Check Intercept installation and running status.

        Returns InterceptStatus with current state.
        """
        status = InterceptStatus()

        # Find installation
        for path in self.INTERCEPT_PATHS:
            if (path / "app.py").exists() or (path / "intercept.py").exists():
                self._install_path = path
                status.installed = True
                break

        # Check if running
        if status.installed:
            status.running = self._is_running()
            status.web_url = f"http://localhost:{self.WEB_PORT}"

            # Try to get version
            try:
                version_file = self._install_path / "version.txt"
                if version_file.exists():
                    status.version = version_file.read_text().strip()
            except Exception:
                pass

        # Check RTL-SDR availability
        status.rtl_sdr_available = self._check_rtlsdr()

        # Check which modules are available
        status.active_modules = self._check_available_modules()

        self._status = status
        return status

    def _is_running(self) -> bool:
        """Check if Intercept web service is running"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', self.WEB_PORT))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _check_rtlsdr(self) -> bool:
        """Check if RTL-SDR tools are available"""
        return shutil.which("rtl_test") is not None

    def _check_available_modules(self) -> List[InterceptModule]:
        """Check which Intercept modules have required tools"""
        available = []

        for module, tools in self.MODULE_TOOLS.items():
            if all(shutil.which(tool) for tool in tools):
                available.append(module)

        return available

    def launch(self, background: bool = True) -> bool:
        """
        Launch Intercept web interface.

        Args:
            background: Run in background (default True)

        Returns:
            True if launched successfully
        """
        if not self._install_path:
            self.check_status()
            if not self._install_path:
                logger.error("Intercept not installed")
                return False

        if self._is_running():
            logger.info("Intercept already running")
            return True

        try:
            app_file = self._install_path / "app.py"
            if not app_file.exists():
                app_file = self._install_path / "intercept.py"

            cmd = ["python3", str(app_file)]

            if background:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self._install_path),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                # Verify process started successfully
                import time
                time.sleep(1)
                if proc.poll() is not None:
                    logger.error(f"Intercept exited immediately: rc={proc.returncode}")
                    return False
                logger.info(f"Intercept launched at http://localhost:{self.WEB_PORT}")
            else:
                subprocess.run(cmd, cwd=str(self._install_path), timeout=3600)

            return True

        except Exception as e:
            logger.error(f"Failed to launch Intercept: {e}")
            return False

    def stop(self) -> bool:
        """Stop Intercept service"""
        try:
            # Find and kill the process
            result = subprocess.run(
                ['pkill', '-f', 'intercept'],
                capture_output=True, timeout=10
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Failed to stop Intercept: {e}")
            return False

    # =========================================================================
    # Direct Tool Access (for embedding specific SIGINT functions)
    # =========================================================================

    def run_rtl_433(self, frequency: float = 433.92, timeout: int = 30,
                    output_json: bool = True) -> List[Dict]:
        """
        Run rtl_433 to capture 433MHz sensor data.

        Args:
            frequency: Frequency in MHz (default 433.92)
            timeout: Capture duration in seconds
            output_json: Return parsed JSON data

        Returns:
            List of decoded sensor readings
        """
        if not shutil.which("rtl_433"):
            logger.error("rtl_433 not installed")
            return []

        try:
            cmd = [
                "rtl_433",
                "-f", f"{int(frequency * 1e6)}",
                "-T", str(timeout),
            ]

            if output_json:
                cmd.extend(["-F", "json"])

            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=timeout + 10
            )

            if output_json:
                readings = []
                for line in result.stdout.split('\n'):
                    if line.strip():
                        try:
                            readings.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                return readings

            return [{"raw": result.stdout}]

        except subprocess.TimeoutExpired:
            logger.warning("rtl_433 timed out")
            return []
        except Exception as e:
            logger.error(f"rtl_433 error: {e}")
            return []

    def run_adsb_capture(self, duration: int = 60) -> List[Dict]:
        """
        Capture ADS-B aircraft data.

        Args:
            duration: Capture duration in seconds

        Returns:
            List of aircraft positions
        """
        dump1090 = shutil.which("dump1090") or shutil.which("dump1090-fa")
        if not dump1090:
            logger.error("dump1090 not installed")
            return []

        try:
            cmd = [
                dump1090,
                "--net",
                "--quiet",
            ]

            # Run dump1090 briefly to collect data
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )

            import time
            time.sleep(duration)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

            # Parse aircraft.json if available
            aircraft_file = Path("/run/dump1090-fa/aircraft.json")
            if aircraft_file.exists():
                try:
                    data = json.loads(aircraft_file.read_text())
                    return data.get("aircraft", [])
                except Exception:
                    pass

            return []

        except Exception as e:
            logger.error(f"ADS-B capture error: {e}")
            return []

    def scan_frequencies(self, start_mhz: float, end_mhz: float,
                        step_khz: float = 25.0) -> List[Dict]:
        """
        Scan frequency range for active signals.

        Args:
            start_mhz: Start frequency in MHz
            end_mhz: End frequency in MHz
            step_khz: Step size in kHz

        Returns:
            List of frequencies with signal strength
        """
        if not shutil.which("rtl_power"):
            logger.error("rtl_power not installed")
            return []

        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
                output_file = f.name

            cmd = [
                "rtl_power",
                "-f", f"{start_mhz}M:{end_mhz}M:{step_khz}k",
                "-i", "1s",
                "-1",
                output_file
            ]

            subprocess.run(cmd, capture_output=True, timeout=30)

            # Parse CSV output
            results = []
            with open(output_file) as f:
                for line in f:
                    parts = line.strip().split(',')
                    if len(parts) >= 7:
                        freq_hz = float(parts[2])
                        power_db = max(float(p) for p in parts[6:])
                        results.append({
                            'frequency_mhz': freq_hz / 1e6,
                            'power_db': power_db
                        })

            Path(output_file).unlink(missing_ok=True)
            return results

        except Exception as e:
            logger.error(f"Frequency scan error: {e}")
            return []

    def get_installation_instructions(self) -> str:
        """Get Intercept installation instructions"""
        return """
Intercept Installation (Debian/Ubuntu/Raspberry Pi OS)
========================================================

1. Clone the repository:
   git clone https://github.com/smittix/intercept.git ~/intercept

2. Install dependencies:
   cd ~/intercept
   pip install -r requirements.txt

3. Install RTL-SDR tools:
   sudo apt install rtl-sdr librtlsdr-dev rtl-433

4. Install additional tools (optional):
   sudo apt install dump1090-fa multimon-ng acarsdec

5. Run Intercept:
   cd ~/intercept && python3 app.py

6. Access web interface:
   http://localhost:5000

MeshForge Integration:
   from utils.intercept import InterceptBridge
   bridge = InterceptBridge()
   bridge.launch()  # Starts Intercept
   data = bridge.run_rtl_433()  # Direct 433MHz capture
"""


def get_intercept_status() -> InterceptStatus:
    """Convenience function to check Intercept status"""
    bridge = InterceptBridge()
    return bridge.check_status()


def launch_intercept() -> bool:
    """Convenience function to launch Intercept"""
    bridge = InterceptBridge()
    return bridge.launch()
