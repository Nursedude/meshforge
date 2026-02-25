"""
NanoVNA Device Communication Module

Handles communication with NanoVNA and NanoVNA-H devices using the
pynanovna library as the primary backend, with fallback to raw pyserial.

Data model (SweepPoint, SweepResult) provides SWR, impedance, return loss,
and phase calculations from raw S11 reflection coefficient data.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import cmath
import math

logger = logging.getLogger(__name__)

# Try to import pynanovna (preferred backend)
try:
    from pynanovna import NanoVNA as PyNanoVNA
    HAS_PYNANOVNA = True
except ImportError:
    HAS_PYNANOVNA = False
    PyNanoVNA = None

# Fallback to raw pyserial
try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    serial = None

# Device is available if either backend works
HAS_NANOVNA = HAS_PYNANOVNA or HAS_SERIAL


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
    """Interface for NanoVNA antenna analyzer devices.

    Uses pynanovna library when available (preferred), falls back to raw
    pyserial communication for direct serial protocol access.
    """

    # NanoVNA USB identifiers (used for fallback serial detection)
    VID_PID_PAIRS = [
        (0x0483, 0x5740),  # NanoVNA original
        (0x04B4, 0x0008),  # NanoVNA-H
        (0x04B4, 0x000A),  # NanoVNA-H4
    ]

    DEFAULT_BAUD = 115200
    TIMEOUT = 2.0

    def __init__(self, port: Optional[str] = None, baud_rate: int = DEFAULT_BAUD):
        """Initialize NanoVNA device.

        Args:
            port: Serial port path. If None, auto-detect.
            baud_rate: Serial baud rate (used for fallback serial mode).
        """
        self.port = port
        self.baud_rate = baud_rate
        self._lock = threading.Lock()
        self._device_version = ""
        self._connected = False

        # pynanovna backend
        self._vna = None

        # Fallback serial backend
        self._serial = None

        if not HAS_NANOVNA:
            logger.warning("[NanoVNA] Neither pynanovna nor pyserial installed")

    @classmethod
    def find_devices(cls) -> List[str]:
        """Find connected NanoVNA devices.

        Returns:
            List of serial port paths.
        """
        if not HAS_SERIAL:
            return []

        devices = []
        try:
            ports = serial.tools.list_ports.comports()
            for port in ports:
                if port.vid and port.pid:
                    if (port.vid, port.pid) in cls.VID_PID_PAIRS:
                        devices.append(port.device)
                        continue

                desc = (port.description or "").lower()
                if "nanovna" in desc or "stm32" in desc:
                    devices.append(port.device)

        except Exception as e:
            logger.error(f"[NanoVNA] Error scanning ports: {e}")

        logger.debug(f"[NanoVNA] Found devices: {devices}")
        return devices

    def connect(self) -> bool:
        """Connect to NanoVNA device.

        Tries pynanovna first, falls back to raw serial.

        Returns:
            True if connected successfully.
        """
        if HAS_PYNANOVNA:
            return self._connect_pynanovna()
        if HAS_SERIAL:
            return self._connect_serial()

        logger.error("[NanoVNA] No backend available")
        return False

    def _connect_pynanovna(self) -> bool:
        """Connect using pynanovna library."""
        with self._lock:
            try:
                if self.port:
                    self._vna = PyNanoVNA(self.port)
                else:
                    self._vna = PyNanoVNA()  # Auto-detect

                self._device_version = "pynanovna"
                self._connected = True
                logger.info("[NanoVNA] Connected via pynanovna")
                return True

            except Exception as e:
                logger.error(f"[NanoVNA] pynanovna connection failed: {e}")
                self._vna = None
                return False

    def _connect_serial(self) -> bool:
        """Connect using raw pyserial (fallback)."""
        with self._lock:
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
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
                time.sleep(0.1)
                self._device_version = self._get_version_serial()
                self._connected = True
                logger.info(f"[NanoVNA] Connected via serial: {self._device_version}")
                return True

            except serial.SerialException as e:
                logger.error(f"[NanoVNA] Serial connection failed: {e}")
                self._serial = None
                return False

    def disconnect(self) -> None:
        """Disconnect from device."""
        with self._lock:
            if self._vna is not None:
                try:
                    self._vna.close()
                except Exception:
                    pass
                self._vna = None

            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None

            self._connected = False
            logger.info("[NanoVNA] Disconnected")

    @property
    def is_connected(self) -> bool:
        """Check if device is connected."""
        if self._vna is not None:
            return True
        if self._serial is not None:
            return self._serial.is_open
        return False

    @property
    def device_version(self) -> str:
        """Device version string."""
        return self._device_version

    @property
    def backend(self) -> str:
        """Active backend name."""
        if self._vna is not None:
            return "pynanovna"
        if self._serial is not None:
            return "serial"
        return "none"

    def sweep(self, start_hz: int, stop_hz: int, points: int = 101) -> SweepResult:
        """Perform frequency sweep.

        Args:
            start_hz: Start frequency in Hz.
            stop_hz: Stop frequency in Hz.
            points: Number of measurement points.

        Returns:
            SweepResult with measurement data.
        """
        if self._vna is not None:
            return self._sweep_pynanovna(start_hz, stop_hz, points)
        if self._serial is not None:
            return self._sweep_serial(start_hz, stop_hz, points)

        logger.warning("[NanoVNA] Not connected")
        return SweepResult(timestamp=time.time())

    def _sweep_pynanovna(self, start_hz: int, stop_hz: int, points: int) -> SweepResult:
        """Sweep using pynanovna backend."""
        result = SweepResult(timestamp=time.time(), device_info=self._device_version)

        with self._lock:
            try:
                self._vna.set_sweep(start=start_hz, stop=stop_hz, points=points)
                data = self._vna.single_sweep()

                # Convert pynanovna data to SweepPoints
                if hasattr(data, 's11') and hasattr(data, 'freqs'):
                    for freq, s11 in zip(data.freqs, data.s11):
                        point = SweepPoint(
                            frequency_hz=int(freq),
                            s11_real=s11.real,
                            s11_imag=s11.imag,
                        )
                        result.points.append(point)

                logger.info(f"[NanoVNA] pynanovna sweep: {len(result.points)} points")

            except Exception as e:
                logger.error(f"[NanoVNA] pynanovna sweep failed: {e}")

        return result

    def _sweep_serial(self, start_hz: int, stop_hz: int, points: int) -> SweepResult:
        """Sweep using raw serial backend."""
        result = SweepResult(timestamp=time.time(), device_info=self._device_version)

        with self._lock:
            try:
                self._send_command(f"sweep {start_hz} {stop_hz} {points}")
                time.sleep(0.2)

                freq_lines = self._send_command("frequencies")
                frequencies = []
                for line in freq_lines:
                    try:
                        frequencies.append(int(float(line)))
                    except ValueError:
                        continue

                data_lines = self._send_command("data 0")

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
                                s11_imag=imag,
                            )
                            result.points.append(point)
                        except (ValueError, IndexError) as e:
                            logger.debug(f"[NanoVNA] Parse error at point {i}: {e}")

                logger.info(f"[NanoVNA] Serial sweep: {len(result.points)} points")

            except Exception as e:
                logger.error(f"[NanoVNA] Serial sweep failed: {e}")

        return result

    def quick_swr(self, frequency_hz: int) -> Optional[float]:
        """Get SWR at a single frequency.

        Args:
            frequency_hz: Target frequency in Hz.

        Returns:
            SWR value or None if failed.
        """
        span = 100000  # 100 kHz span
        result = self.sweep(
            frequency_hz - span,
            frequency_hz + span,
            points=11,
        )

        if result.points:
            return result.get_swr_at_frequency(frequency_hz / 1e6)
        return None

    # ── Raw serial helpers (fallback only) ──────────────────────────────────

    def _send_command(self, cmd: str) -> List[str]:
        """Send command and read response lines (serial backend only)."""
        if not self._serial:
            return []

        try:
            self._serial.write(f"{cmd}\r\n".encode())
            self._serial.flush()

            lines = []
            deadline = time.time() + self.TIMEOUT

            while time.time() < deadline:
                if self._serial.in_waiting > 0:
                    line = self._serial.readline().decode('utf-8', errors='ignore').strip()
                    if line.startswith('ch>') or line.endswith('ch>'):
                        break
                    if line and not line.startswith(cmd):
                        lines.append(line)
                else:
                    time.sleep(0.01)

            return lines

        except Exception as e:
            logger.error(f"[NanoVNA] Command '{cmd}' failed: {e}")
            return []

    def _get_version_serial(self) -> str:
        """Get device version string (serial backend only)."""
        lines = self._send_command("version")
        if lines:
            return lines[0]
        return "Unknown"


def format_impedance(z: complex) -> str:
    """Format complex impedance for display.

    Returns:
        Formatted string like "50.0 + j12.3 ohm" or "50.0 - j12.3 ohm"
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

    Returns:
        Formatted string like "1.50:1" or ">10:1"
    """
    if swr > 10:
        return ">10:1"
    if swr == float('inf'):
        return "Inf:1"
    return f"{swr:.2f}:1"
