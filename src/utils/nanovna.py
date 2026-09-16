"""
NanoVNA antenna analyzer — serial driver and S-parameter math.

Talks to NanoVNA / NanoVNA-H / -H4 hardware over USB serial and turns a
frequency sweep into the numbers a HAM actually reads off an analyzer: SWR,
return loss, complex impedance (R +/- jX), phase, and the best-match
frequency across the sweep.

MOVED 2026-09-15 from ``plugins/nanovna_analyzer/nanovna_device.py``. It had
sat for months in a GTK-era plugin tree that NOTHING loads — the TUI's
``handlers/extensions.py`` contains zero references to ``plugins/`` — so a
complete, working instrument driver was unreachable from the app, next door
to an "Antenna Analysis" menu item that only compares antenna types from a
static table. Its GTK panel was deleted in the same change; this module never
had any GTK in it.

Pure-Python and hardware-free except for pyserial, which is optional: with no
pyserial the module imports fine and every entry point reports that honestly
rather than raising at import time.

Usage:
    from utils.nanovna import NanoVNADevice, format_swr

    ports = NanoVNADevice.find_devices()
    vna = NanoVNADevice(ports[0])
    if vna.connect():
        result = vna.sweep(144_000_000, 148_000_000, points=101)
        swr, freq = result.min_swr
        print(f"best match {format_swr(swr)} at {freq:.3f} MHz")
        vna.disconnect()
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import cmath
import math

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# External dep -> safe_import (CLAUDE.md: safe_import is for external deps
# only). pyserial is genuinely optional: the sweep math below is useful
# without hardware, and every box in the fleet should not need it.
serial, _HAS_SERIAL = safe_import('serial')
_serial_list_ports, _HAS_LIST_PORTS = safe_import('serial.tools.list_ports')

#: True only when BOTH halves are importable. Port enumeration lives in a
#: separate submodule, and importing `serial` does NOT bring it in — treating
#: them as one flag is how "found no devices" starts meaning "cannot look".
HAS_SERIAL = _HAS_SERIAL and _HAS_LIST_PORTS


class NanoVNAUnavailable(RuntimeError):
    """Raised when the VNA cannot be used, with the reason in the message.

    Deliberately NOT an empty return: "no devices found" and "I cannot look
    for devices" are different facts and must not share a value.
    """


@dataclass
class SweepPoint:
    """Single point in a frequency sweep."""
    frequency_hz: int
    s11_real: float
    s11_imag: float

    @property
    def gamma(self) -> complex:
        """Reflection coefficient (Gamma)."""
        return complex(self.s11_real, self.s11_imag)

    @property
    def gamma_magnitude(self) -> float:
        """Magnitude of reflection coefficient."""
        return abs(self.gamma)

    @property
    def swr(self) -> float:
        """Standing Wave Ratio."""
        mag = self.gamma_magnitude
        if mag >= 1.0:
            return float('inf')
        return (1 + mag) / (1 - mag)

    @property
    def return_loss_db(self) -> float:
        """Return loss in dB."""
        mag = self.gamma_magnitude
        if mag <= 0:
            return float('inf')
        return -20 * math.log10(mag)

    @property
    def impedance(self) -> complex:
        """Complex impedance (assuming Z0=50 ohms)."""
        z0 = 50.0
        gamma = self.gamma
        if abs(1 - gamma) < 1e-10:
            return complex(float('inf'), 0)
        return z0 * (1 + gamma) / (1 - gamma)

    @property
    def resistance(self) -> float:
        """Real part of impedance (R)."""
        return self.impedance.real

    @property
    def reactance(self) -> float:
        """Imaginary part of impedance (X)."""
        return self.impedance.imag

    @property
    def phase_degrees(self) -> float:
        """Phase angle of reflection coefficient in degrees."""
        return math.degrees(cmath.phase(self.gamma))

    @property
    def frequency_mhz(self) -> float:
        """Frequency in MHz."""
        return self.frequency_hz / 1e6


@dataclass
class SweepResult:
    """Result of a complete frequency sweep."""
    points: List[SweepPoint] = field(default_factory=list)
    timestamp: float = 0.0
    device_info: str = ""

    @property
    def frequency_range(self) -> Tuple[float, float]:
        """Start and stop frequencies in MHz."""
        if not self.points:
            return (0.0, 0.0)
        return (self.points[0].frequency_mhz, self.points[-1].frequency_mhz)

    @property
    def min_swr(self) -> Tuple[float, float]:
        """Minimum SWR and its frequency in MHz."""
        if not self.points:
            return (float('inf'), 0.0)
        min_point = min(self.points, key=lambda p: p.swr)
        return (min_point.swr, min_point.frequency_mhz)

    @property
    def best_match_frequency(self) -> float:
        """Frequency with best impedance match (lowest SWR) in MHz."""
        return self.min_swr[1]

    def get_swr_at_frequency(self, freq_mhz: float) -> Optional[float]:
        """Get SWR at closest measured frequency."""
        if not self.points:
            return None
        closest = min(self.points, key=lambda p: abs(p.frequency_mhz - freq_mhz))
        return closest.swr


class NanoVNADevice:
    """Interface for NanoVNA antenna analyzer devices."""

    # NanoVNA USB identifiers
    #: USB signatures we recognise. 0483:5740 is the STMicroelectronics
    #: Virtual COM Port that the hugen79 NanoVNA-H / -H4 designs and their
    #: resellers (SEESII among them) present, so it is the one that matters
    #: in practice. The 04B4 pairs are kept for older/other builds.
    #:
    #: ⚠️ Clones with an unlisted VID:PID exist. `find_devices()` therefore
    #: ALSO matches on a description containing "nanovna" or "stm32", and the
    #: TUI's Detect screen prints every port it saw with its VID:PID so an
    #: unrecognised device can be identified and added here rather than
    #: silently reading as "no analyzer connected".
    VID_PID_PAIRS = [
        (0x0483, 0x5740),  # STM32 VCP — NanoVNA, NanoVNA-H, NanoVNA-H4 (SEESII)
        (0x04B4, 0x0008),  # Cypress-based variants
        (0x04B4, 0x000A),
    ]

    #: The shell protocol used below ("version", "sweep", "frequencies",
    #: "data 0") is the common NanoVNA command set and is what the -H4 /
    #: DiSlord firmware speaks. `data 0` is S11 (reflection), which is what
    #: antenna work needs; `data 1` would be S21 (through).

    DEFAULT_BAUD = 115200
    TIMEOUT = 2.0

    def __init__(self, port: Optional[str] = None, baud_rate: int = DEFAULT_BAUD):
        """Initialize NanoVNA device.

        Args:
            port: Serial port path. If None, auto-detect.
            baud_rate: Serial baud rate.
        """
        self.port = port
        self.baud_rate = baud_rate
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._device_version = ""

        if not HAS_SERIAL:
            logger.warning(
                "[NanoVNA] pyserial not installed — this object can hold and "
                "format sweep data but cannot talk to hardware")

    @classmethod
    def find_devices(cls) -> List[str]:
        """Find connected NanoVNA devices.

        Returns:
            List of serial port paths.
        """
        if not HAS_SERIAL:
            # Callers cannot tell [] "no VNA plugged in" from [] "pyserial
            # missing", so refuse to answer at all rather than answer wrong
            # (honest_failure_modes #1: the degraded value must not overlap
            # the healthy domain). The TUI catches this and says which it is.
            raise NanoVNAUnavailable(
                "pyserial is not installed, so USB serial ports cannot be "
                "enumerated. Install it with: pipx inject meshforge pyserial "
                "(or apt install python3-serial)."
            )

        devices = []
        try:
            ports = _serial_list_ports.comports()
            for port in ports:
                # Check for NanoVNA by VID/PID
                if port.vid and port.pid:
                    if (port.vid, port.pid) in cls.VID_PID_PAIRS:
                        devices.append(port.device)
                        continue

                # Check by description
                desc = (port.description or "").lower()
                if "nanovna" in desc or "stm32" in desc:
                    devices.append(port.device)

        except Exception as e:
            logger.error(f"[NanoVNA] Error scanning ports: {e}")

        logger.debug(f"[NanoVNA] Found devices: {devices}")
        return devices

    def connect(self) -> bool:
        """Connect to NanoVNA device.

        Returns:
            True if connected successfully.
        """
        if not HAS_SERIAL:
            raise NanoVNAUnavailable(
                "pyserial is not installed — cannot open the serial port."
            )

        with self._lock:
            # Auto-detect port if not specified
            if not self.port:
                devices = self.find_devices()
                if not devices:
                    logger.error("[NanoVNA] No NanoVNA device found")
                    return False
                self.port = devices[0]
                logger.info(f"[NanoVNA] Auto-detected device at {self.port}")

            try:
                self._serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baud_rate,
                    timeout=self.TIMEOUT,
                    write_timeout=self.TIMEOUT
                )

                # Clear buffers
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()

                # Get device version
                time.sleep(0.1)
                self._device_version = self._get_version()

                logger.info(f"[NanoVNA] Connected to {self.port}: {self._device_version}")
                return True

            except serial.SerialException as e:
                logger.error(f"[NanoVNA] Connection failed: {e}")
                self._serial = None
                return False

    def disconnect(self) -> None:
        """Disconnect from device."""
        with self._lock:
            if self._serial:
                try:
                    self._serial.close()
                except (OSError, AttributeError) as e:
                    # Closing a port that is already gone (device unplugged
                    # mid-session) is not an error worth raising, but it is
                    # worth a witness.
                    logger.debug("[NanoVNA] close() on a dead port: %s", e)
                self._serial = None
                logger.info("[NanoVNA] Disconnected")

    @property
    def is_connected(self) -> bool:
        """Check if device is connected."""
        return self._serial is not None and self._serial.is_open

    @property
    def device_version(self) -> str:
        """Device version string."""
        return self._device_version

    def _send_command(self, cmd: str) -> List[str]:
        """Send command and read response lines.

        Args:
            cmd: Command string to send.

        Returns:
            List of response lines.
        """
        if not self._serial:
            return []

        try:
            # Send command with newline
            self._serial.write(f"{cmd}\r\n".encode())
            self._serial.flush()

            # Read response lines until we get 'ch>' prompt or timeout
            lines = []
            deadline = time.time() + self.TIMEOUT

            while time.time() < deadline:
                if self._serial.in_waiting > 0:
                    line = self._serial.readline().decode('utf-8', errors='ignore').strip()
                    if line.startswith('ch>') or line.endswith('ch>'):
                        break
                    if line and not line.startswith(cmd):  # Skip echo
                        lines.append(line)
                else:
                    time.sleep(0.01)

            return lines

        except Exception as e:
            logger.error(f"[NanoVNA] Command '{cmd}' failed: {e}")
            return []

    def _get_version(self) -> str:
        """Get device version string."""
        lines = self._send_command("version")
        if lines:
            return lines[0]
        return "Unknown"

    def sweep(self, start_hz: int, stop_hz: int, points: int = 101) -> SweepResult:
        """Perform frequency sweep.

        Args:
            start_hz: Start frequency in Hz.
            stop_hz: Stop frequency in Hz.
            points: Number of measurement points.

        Returns:
            SweepResult with measurement data.
        """
        result = SweepResult(timestamp=time.time(), device_info=self._device_version)

        if not self.is_connected:
            logger.warning("[NanoVNA] Not connected")
            return result

        with self._lock:
            try:
                # Configure sweep
                self._send_command(f"sweep {start_hz} {stop_hz} {points}")
                time.sleep(0.2)  # Allow sweep to complete

                # Get frequencies
                freq_lines = self._send_command("frequencies")
                frequencies = []
                for line in freq_lines:
                    try:
                        frequencies.append(int(float(line)))
                    except ValueError:
                        continue

                # Get S11 data (channel 0)
                data_lines = self._send_command("data 0")

                # Parse data points
                for i, line in enumerate(data_lines):
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            real = float(parts[0])
                            imag = float(parts[1])
                            freq = frequencies[i] if i < len(frequencies) else 0

                            point = SweepPoint(
                                frequency_hz=freq,
                                s11_real=real,
                                s11_imag=imag
                            )
                            result.points.append(point)
                        except (ValueError, IndexError) as e:
                            logger.debug(f"[NanoVNA] Parse error at point {i}: {e}")

                logger.info(f"[NanoVNA] Sweep complete: {len(result.points)} points")

            except Exception as e:
                logger.error(f"[NanoVNA] Sweep failed: {e}")

        return result

    def quick_swr(self, frequency_hz: int) -> Optional[float]:
        """Get SWR at a single frequency.

        Args:
            frequency_hz: Target frequency in Hz.

        Returns:
            SWR value or None if failed.
        """
        # Do a narrow sweep around the target frequency
        span = 100000  # 100 kHz span
        result = self.sweep(
            frequency_hz - span,
            frequency_hz + span,
            points=11
        )

        if result.points:
            return result.get_swr_at_frequency(frequency_hz / 1e6)
        return None


def format_impedance(z: complex) -> str:
    """Format complex impedance for display.

    Args:
        z: Complex impedance value.

    Returns:
        Formatted string like "50.0 + j12.3" or "50.0 - j12.3"
    """
    r = z.real
    x = z.imag

    if abs(r) > 9999:
        return "Open"

    if x >= 0:
        return f"{r:.1f} + j{x:.1f}"
    else:
        return f"{r:.1f} - j{abs(x):.1f}"


def format_swr(swr: float) -> str:
    """Format SWR for display.

    Args:
        swr: SWR value.

    Returns:
        Formatted string like "1.5:1" or ">10:1"
    """
    # ORDER MATTERS. `swr > 10` is True for inf, so testing it first made the
    # "Inf:1" branch unreachable and rendered an OPEN or SHORTED antenna
    # identically to a merely-bad match. For antenna work those are different
    # faults: >10:1 means "tune it", Inf:1 means "nothing is connected —
    # check the connector". Found by a test, 2026-09-15.
    if swr == float('inf') or math.isnan(swr):
        return "Inf:1"
    if swr > 10:
        return ">10:1"
    return f"{swr:.2f}:1"
