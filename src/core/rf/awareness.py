"""
RF Awareness Module for MeshForge.

Provides SDR-based RF awareness capabilities focused on LoRa band monitoring:
- Spectrum waterfall around LoRa frequencies (868/915 MHz)
- Signal strength surveys (coverage validation)
- Channel utilization monitoring
- Interference detection

Supports Airspy R2/Mini via SoapySDR, with graceful fallback when hardware
is unavailable. Designed for adaptation to μconsole and portable hardware.

Usage:
    from utils.rf_awareness import RFAwareness, LoRaBand

    rf = RFAwareness()
    if rf.connect():
        # Get spectrum snapshot
        spectrum = rf.get_spectrum_snapshot(LoRaBand.US_915)

        # Monitor channel utilization
        util = rf.measure_channel_utilization(duration_sec=10)

        # Signal strength survey
        survey = rf.signal_survey(frequencies=[902.0e6, 915.0e6, 928.0e6])

        rf.disconnect()
"""

import logging
import threading
import time
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple, Callable, Any

# NumPy is optional - fall back to basic Python if not available
from utils.safe_import import safe_import

_np, HAS_NUMPY = safe_import('numpy')
np = _np if HAS_NUMPY else None

_SoapySDR_mod, _HAS_SOAPY = safe_import('SoapySDR')

logger = logging.getLogger(__name__)


class LoRaBand(Enum):
    """LoRa frequency bands by region."""
    US_915 = (902.0e6, 928.0e6, "US 915 MHz ISM")
    EU_868 = (863.0e6, 870.0e6, "EU 868 MHz SRD")
    EU_433 = (433.0e6, 434.0e6, "EU 433 MHz")
    AU_915 = (915.0e6, 928.0e6, "Australia 915 MHz")
    AS_923 = (920.0e6, 925.0e6, "Asia 923 MHz")
    JP_920 = (920.8e6, 923.8e6, "Japan 920 MHz")
    KR_920 = (920.0e6, 923.0e6, "Korea 920 MHz")
    IN_865 = (865.0e6, 867.0e6, "India 865 MHz")

    def __init__(self, start_freq: float, end_freq: float, description: str):
        self.start_freq = start_freq
        self.end_freq = end_freq
        self.description = description

    @property
    def center_freq(self) -> float:
        return (self.start_freq + self.end_freq) / 2

    @property
    def bandwidth(self) -> float:
        return self.end_freq - self.start_freq


class SDRBackend(Enum):
    """Available SDR backends."""
    SOAPY = auto()
    MOCK = auto()
    NONE = auto()


@dataclass
class SDRDeviceInfo:
    """Information about an SDR device."""
    driver: str
    label: str
    serial: str = ""
    hardware: str = ""
    frequency_range: Tuple[float, float] = (0, 0)
    sample_rate_range: Tuple[float, float] = (0, 0)
    gain_range: Tuple[float, float] = (0, 0)

    def supports_frequency(self, freq: float) -> bool:
        """Check if device supports a frequency."""
        return self.frequency_range[0] <= freq <= self.frequency_range[1]


@dataclass
class SpectrumSnapshot:
    """A snapshot of the RF spectrum."""
    timestamp: datetime
    center_freq: float
    bandwidth: float
    frequencies: Any  # Frequency bins in Hz (numpy array)
    power_dbm: Any    # Power in dBm for each bin (numpy array)
    noise_floor_dbm: float
    peak_freq: float
    peak_power_dbm: float
    sample_rate: float
    fft_size: int
    averaging: int = 1

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "center_freq_mhz": self.center_freq / 1e6,
            "bandwidth_mhz": self.bandwidth / 1e6,
            "noise_floor_dbm": self.noise_floor_dbm,
            "peak_freq_mhz": self.peak_freq / 1e6,
            "peak_power_dbm": self.peak_power_dbm,
            "fft_size": self.fft_size,
            "bin_count": len(self.frequencies),
        }


@dataclass
class ChannelUtilization:
    """Channel utilization measurement."""
    timestamp: datetime
    frequency: float
    duration_sec: float
    utilization_percent: float  # 0-100
    duty_cycle: float
    active_time_sec: float
    signal_count: int
    avg_signal_power_dbm: float
    peak_signal_power_dbm: float
    noise_floor_dbm: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "frequency_mhz": self.frequency / 1e6,
            "duration_sec": self.duration_sec,
            "utilization_percent": round(self.utilization_percent, 2),
            "duty_cycle": round(self.duty_cycle, 4),
            "signal_count": self.signal_count,
            "avg_signal_power_dbm": round(self.avg_signal_power_dbm, 1),
            "peak_signal_power_dbm": round(self.peak_signal_power_dbm, 1),
            "noise_floor_dbm": round(self.noise_floor_dbm, 1),
        }


@dataclass
class SignalSurveyPoint:
    """A single point in a signal survey."""
    frequency: float
    timestamp: datetime
    power_dbm: float
    snr_db: float
    noise_floor_dbm: float
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None


@dataclass
class SignalSurvey:
    """Complete signal survey result."""
    start_time: datetime
    end_time: datetime
    band: Optional[LoRaBand]
    points: List[SignalSurveyPoint] = field(default_factory=list)
    statistics: Dict[str, Any] = field(default_factory=dict)

    def calculate_statistics(self):
        """Calculate survey statistics."""
        if not self.points:
            return

        powers = [p.power_dbm for p in self.points]
        snrs = [p.snr_db for p in self.points]

        self.statistics = {
            "point_count": len(self.points),
            "duration_sec": (self.end_time - self.start_time).total_seconds(),
            "power_min_dbm": min(powers),
            "power_max_dbm": max(powers),
            "power_avg_dbm": sum(powers) / len(powers),
            "snr_min_db": min(snrs),
            "snr_max_db": max(snrs),
            "snr_avg_db": sum(snrs) / len(snrs),
        }


class RFAwareness:
    """
    RF awareness and spectrum monitoring for LoRa bands.

    Provides SDR-based RF monitoring with focus on:
    - Spectrum analysis around LoRa frequencies
    - Channel utilization monitoring
    - Signal strength surveys
    - Interference detection

    Supports Airspy R2/Mini via SoapySDR, with fallback to mock mode
    when hardware is unavailable.
    """

    # Default settings optimized for LoRa monitoring
    DEFAULT_SAMPLE_RATE = 2.5e6  # 2.5 MSPS - good for Airspy Mini
    DEFAULT_FFT_SIZE = 1024
    DEFAULT_GAIN = 40.0  # dB
    DEFAULT_AVERAGING = 10

    # LoRa signal detection threshold relative to noise floor
    SIGNAL_THRESHOLD_DB = 6.0

    def __init__(self, sample_rate: float = None, fft_size: int = None,
                 gain: float = None):
        """
        Initialize RF awareness module.

        Args:
            sample_rate: Sample rate in Hz (default: 2.5 MSPS)
            fft_size: FFT size for spectrum analysis
            gain: Receiver gain in dB
        """
        self._sample_rate = sample_rate or self.DEFAULT_SAMPLE_RATE
        self._fft_size = fft_size or self.DEFAULT_FFT_SIZE
        self._gain = gain or self.DEFAULT_GAIN

        self._backend = SDRBackend.NONE
        self._device = None
        self._device_info: Optional[SDRDeviceInfo] = None
        self._stream = None
        self._connected = False

        self._lock = threading.RLock()
        self._rx_buffer = None  # Initialized on connect when numpy is available

        # Waterfall history for display
        self._waterfall_history: deque = deque(maxlen=100)

        # Callbacks for async notifications
        self._spectrum_callbacks: List[Callable[[SpectrumSnapshot], None]] = []

    @property
    def is_connected(self) -> bool:
        """Check if SDR is connected."""
        return self._connected

    @property
    def backend(self) -> SDRBackend:
        """Get current backend type."""
        return self._backend

    @property
    def device_info(self) -> Optional[SDRDeviceInfo]:
        """Get connected device info."""
        return self._device_info

    @staticmethod
    def list_devices() -> List[SDRDeviceInfo]:
        """
        List available SDR devices.

        Returns:
            List of available SDR device info
        """
        devices = []

        if not _HAS_SOAPY:
            logger.debug("SoapySDR not available")
            return devices

        try:
            SoapySDR = _SoapySDR_mod

            results = SoapySDR.Device.enumerate()
            for result in results:
                driver = result.get("driver", "unknown")
                label = result.get("label", driver)
                serial = result.get("serial", "")

                # Filter for Airspy devices (our primary target)
                if "airspy" in driver.lower():
                    devices.append(SDRDeviceInfo(
                        driver=driver,
                        label=label,
                        serial=serial,
                        hardware="Airspy R2/Mini",
                        # Airspy R2/Mini frequency range
                        frequency_range=(24e6, 1800e6),
                        sample_rate_range=(2.5e6, 10e6),
                        gain_range=(0, 45),
                    ))
                else:
                    # Other SoapySDR-compatible devices
                    devices.append(SDRDeviceInfo(
                        driver=driver,
                        label=label,
                        serial=serial,
                    ))

        except Exception as e:
            logger.warning(f"Error enumerating devices: {e}")

        return devices

    def connect(self, device_filter: str = None, mock: bool = False) -> bool:
        """
        Connect to SDR device.

        Args:
            device_filter: Filter string for device selection (e.g., "airspy")
            mock: If True, use mock backend for testing

        Returns:
            True if connected successfully
        """
        if not HAS_NUMPY:
            logger.error("NumPy required for RF awareness. Install with: pip install numpy")
            return False

        with self._lock:
            if self._connected:
                return True

            if mock:
                return self._connect_mock()

            # Try SoapySDR first
            if self._connect_soapy(device_filter):
                return True

            # Fall back to mock if no hardware available
            logger.info("No SDR hardware found, using mock backend")
            return self._connect_mock()

    def _connect_soapy(self, device_filter: str = None) -> bool:
        """Connect via SoapySDR."""
        if not _HAS_SOAPY:
            logger.debug("SoapySDR not installed")
            return False

        try:
            SoapySDR = _SoapySDR_mod

            # Enumerate devices
            results = SoapySDR.Device.enumerate()
            if not results:
                logger.info("No SoapySDR devices found")
                return False

            # Find suitable device
            device_args = None
            for result in results:
                driver = result.get("driver", "").lower()

                # Prefer Airspy
                if "airspy" in driver:
                    device_args = result
                    break

                # Use filter if provided
                if device_filter and device_filter.lower() in driver:
                    device_args = result
                    break

            # Use first device if no preference matched
            if device_args is None:
                device_args = results[0]

            # Open device
            self._device = SoapySDR.Device(device_args)
            if self._device is None:
                return False

            # Get device info
            driver = device_args.get("driver", "unknown")
            self._device_info = SDRDeviceInfo(
                driver=driver,
                label=device_args.get("label", driver),
                serial=device_args.get("serial", ""),
                hardware=self._device.getHardwareKey() if hasattr(self._device, 'getHardwareKey') else driver,
            )

            # Get frequency range
            try:
                freq_ranges = self._device.getFrequencyRange(SoapySDR.SOAPY_SDR_RX, 0)
                if freq_ranges:
                    self._device_info.frequency_range = (
                        freq_ranges[0].minimum(),
                        freq_ranges[-1].maximum()
                    )
            except Exception:
                pass

            # Get gain range
            try:
                gain_range = self._device.getGainRange(SoapySDR.SOAPY_SDR_RX, 0)
                self._device_info.gain_range = (gain_range.minimum(), gain_range.maximum())
            except Exception:
                pass

            # Configure device
            self._device.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, self._sample_rate)
            self._device.setGain(SoapySDR.SOAPY_SDR_RX, 0, self._gain)

            self._backend = SDRBackend.SOAPY
            self._connected = True
            self._rx_buffer = np.zeros(self._fft_size, dtype=np.complex64)

            logger.info(f"Connected to {self._device_info.label} via SoapySDR")
            return True

        except Exception as e:
            logger.error(f"SoapySDR connection failed: {e}")
            return False

    def _connect_mock(self) -> bool:
        """Connect mock backend for testing."""
        self._device = MockSDR(
            sample_rate=self._sample_rate,
            fft_size=self._fft_size,
        )
        self._device_info = SDRDeviceInfo(
            driver="mock",
            label="Mock SDR (Testing)",
            hardware="Virtual",
            frequency_range=(24e6, 6000e6),
            sample_rate_range=(1e6, 20e6),
            gain_range=(0, 50),
        )
        self._backend = SDRBackend.MOCK
        self._connected = True
        self._rx_buffer = np.zeros(self._fft_size, dtype=np.complex64)

        logger.info("Connected to mock SDR backend")
        return True

    def disconnect(self):
        """Disconnect from SDR device."""
        with self._lock:
            if self._stream is not None:
                self._stop_stream()

            if self._backend == SDRBackend.SOAPY and self._device is not None:
                try:
                    self._device = None
                except Exception as e:
                    logger.debug(f"Error closing SoapySDR device: {e}")

            self._connected = False
            self._device = None
            self._device_info = None
            self._backend = SDRBackend.NONE
            logger.info("SDR disconnected")

    def set_frequency(self, freq: float) -> bool:
        """
        Set center frequency.

        Args:
            freq: Frequency in Hz

        Returns:
            True if successful
        """
        with self._lock:
            if not self._connected:
                return False

            try:
                if self._backend == SDRBackend.SOAPY:
                    import SoapySDR
                    self._device.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, freq)
                elif self._backend == SDRBackend.MOCK:
                    self._device.set_frequency(freq)

                return True

            except Exception as e:
                logger.error(f"Failed to set frequency: {e}")
                return False

    def set_gain(self, gain: float) -> bool:
        """
        Set receiver gain.

        Args:
            gain: Gain in dB

        Returns:
            True if successful
        """
        with self._lock:
            if not self._connected:
                return False

            try:
                if self._backend == SDRBackend.SOAPY:
                    import SoapySDR
                    self._device.setGain(SoapySDR.SOAPY_SDR_RX, 0, gain)
                elif self._backend == SDRBackend.MOCK:
                    self._device.set_gain(gain)

                self._gain = gain
                return True

            except Exception as e:
                logger.error(f"Failed to set gain: {e}")
                return False

    def _start_stream(self) -> bool:
        """Start the RX stream."""
        if self._stream is not None:
            return True

        try:
            if self._backend == SDRBackend.SOAPY:
                import SoapySDR
                self._stream = self._device.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32)
                self._device.activateStream(self._stream)
            elif self._backend == SDRBackend.MOCK:
                self._stream = True  # Mock uses direct method calls

            return True

        except Exception as e:
            logger.error(f"Failed to start stream: {e}")
            return False

    def _stop_stream(self):
        """Stop the RX stream."""
        if self._stream is None:
            return

        try:
            if self._backend == SDRBackend.SOAPY:
                self._device.deactivateStream(self._stream)
                self._device.closeStream(self._stream)
        except Exception as e:
            logger.debug(f"Error stopping stream: {e}")

        self._stream = None

    def _receive_samples(self, num_samples: int) -> Optional[Any]:
        """
        Receive samples from SDR.

        Args:
            num_samples: Number of complex samples to receive

        Returns:
            Complex samples array or None on error
        """
        if not self._connected:
            return None

        try:
            if self._backend == SDRBackend.SOAPY:
                import SoapySDR

                if self._stream is None:
                    if not self._start_stream():
                        return None

                buffer = np.zeros(num_samples, dtype=np.complex64)
                sr = self._device.readStream(self._stream, [buffer], num_samples)
                if sr.ret > 0:
                    return buffer[:sr.ret]
                return None

            elif self._backend == SDRBackend.MOCK:
                return self._device.receive_samples(num_samples)

        except Exception as e:
            logger.error(f"Error receiving samples: {e}")
            return None

    def get_spectrum_snapshot(self, band: LoRaBand = None,
                              center_freq: float = None,
                              averaging: int = None) -> Optional[SpectrumSnapshot]:
        """
        Get a snapshot of the RF spectrum.

        Args:
            band: LoRa band to monitor (uses center frequency)
            center_freq: Custom center frequency in Hz
            averaging: Number of FFT averages

        Returns:
            SpectrumSnapshot or None on error
        """
        with self._lock:
            if not self._connected:
                return None

            # Determine frequency
            if band is not None:
                freq = band.center_freq
            elif center_freq is not None:
                freq = center_freq
            else:
                freq = LoRaBand.US_915.center_freq

            averaging = averaging or self.DEFAULT_AVERAGING

            if not self.set_frequency(freq):
                return None

            # Collect samples and compute FFT
            power_acc = np.zeros(self._fft_size)
            valid_count = 0

            for _ in range(averaging):
                samples = self._receive_samples(self._fft_size)
                if samples is None or len(samples) < self._fft_size:
                    continue

                # Apply window and compute FFT
                window = np.hanning(self._fft_size)
                windowed = samples[:self._fft_size] * window
                fft_result = np.fft.fftshift(np.fft.fft(windowed))

                # Convert to power (dBm)
                power = 10 * np.log10(np.abs(fft_result) ** 2 + 1e-10)
                power_acc += power
                valid_count += 1

            if valid_count == 0:
                return None

            power_dbm = power_acc / valid_count

            # Generate frequency axis
            freqs = np.fft.fftshift(np.fft.fftfreq(self._fft_size, 1 / self._sample_rate))
            freqs += freq

            # Find peak and noise floor
            noise_floor = np.percentile(power_dbm, 10)
            peak_idx = np.argmax(power_dbm)
            peak_freq = freqs[peak_idx]
            peak_power = power_dbm[peak_idx]

            snapshot = SpectrumSnapshot(
                timestamp=datetime.now(),
                center_freq=freq,
                bandwidth=self._sample_rate,
                frequencies=freqs,
                power_dbm=power_dbm,
                noise_floor_dbm=noise_floor,
                peak_freq=peak_freq,
                peak_power_dbm=peak_power,
                sample_rate=self._sample_rate,
                fft_size=self._fft_size,
                averaging=averaging,
            )

            # Add to waterfall history
            self._waterfall_history.append(snapshot)

            # Notify callbacks
            for callback in self._spectrum_callbacks:
                try:
                    callback(snapshot)
                except Exception as e:
                    logger.error(f"Spectrum callback error: {e}")

            return snapshot

    def measure_channel_utilization(self, band: LoRaBand = None,
                                     center_freq: float = None,
                                     duration_sec: float = 10.0,
                                     threshold_db: float = None) -> Optional[ChannelUtilization]:
        """
        Measure channel utilization over a time period.

        Args:
            band: LoRa band to monitor
            center_freq: Custom center frequency
            duration_sec: Measurement duration in seconds
            threshold_db: Signal detection threshold above noise floor

        Returns:
            ChannelUtilization measurement or None
        """
        with self._lock:
            if not self._connected:
                return None

            # Determine frequency
            if band is not None:
                freq = band.center_freq
            elif center_freq is not None:
                freq = center_freq
            else:
                freq = LoRaBand.US_915.center_freq

            threshold_db = threshold_db or self.SIGNAL_THRESHOLD_DB

            if not self.set_frequency(freq):
                return None

            # Measurement variables
            start_time = time.time()
            active_time = 0.0
            signal_count = 0
            signal_powers = []
            noise_samples = []
            last_above_threshold = False
            samples_per_check = self._fft_size
            sample_interval = samples_per_check / self._sample_rate

            while (time.time() - start_time) < duration_sec:
                samples = self._receive_samples(samples_per_check)
                if samples is None:
                    time.sleep(0.01)
                    continue

                # Calculate instantaneous power
                power = 10 * np.log10(np.mean(np.abs(samples) ** 2) + 1e-10)

                # Estimate noise floor from quiet samples
                if power < -90:  # Likely noise
                    noise_samples.append(power)

                # Check if above threshold
                noise_floor = np.mean(noise_samples) if noise_samples else -100
                is_signal = power > (noise_floor + threshold_db)

                if is_signal:
                    active_time += sample_interval
                    signal_powers.append(power)

                    if not last_above_threshold:
                        signal_count += 1

                last_above_threshold = is_signal

            # Calculate results
            total_duration = time.time() - start_time
            utilization = (active_time / total_duration) * 100 if total_duration > 0 else 0
            duty_cycle = active_time / total_duration if total_duration > 0 else 0

            avg_signal_power = np.mean(signal_powers) if signal_powers else noise_floor
            peak_signal_power = max(signal_powers) if signal_powers else noise_floor
            final_noise_floor = np.mean(noise_samples) if noise_samples else -100

            return ChannelUtilization(
                timestamp=datetime.now(),
                frequency=freq,
                duration_sec=total_duration,
                utilization_percent=utilization,
                duty_cycle=duty_cycle,
                active_time_sec=active_time,
                signal_count=signal_count,
                avg_signal_power_dbm=avg_signal_power,
                peak_signal_power_dbm=peak_signal_power,
                noise_floor_dbm=final_noise_floor,
            )

    def signal_survey(self, band: LoRaBand = None,
                      frequencies: List[float] = None,
                      duration_per_freq_sec: float = 2.0,
                      gps_callback: Callable[[], Tuple[float, float, float]] = None) -> SignalSurvey:
        """
        Perform a signal strength survey across frequencies.

        Args:
            band: LoRa band to survey (will sample across band)
            frequencies: Specific frequencies to survey (Hz)
            duration_per_freq_sec: Time to spend on each frequency
            gps_callback: Optional callback returning (lat, lon, alt)

        Returns:
            SignalSurvey with results
        """
        survey = SignalSurvey(
            start_time=datetime.now(),
            end_time=datetime.now(),
            band=band,
        )

        # Generate frequency list
        if frequencies is not None:
            freq_list = frequencies
        elif band is not None:
            # Sample 10 points across the band
            freq_list = np.linspace(band.start_freq, band.end_freq, 10)
        else:
            # Default: US 915 band
            freq_list = np.linspace(902e6, 928e6, 10)

        with self._lock:
            if not self._connected:
                return survey

            for freq in freq_list:
                if not self.set_frequency(freq):
                    continue

                # Collect samples
                start = time.time()
                powers = []

                while (time.time() - start) < duration_per_freq_sec:
                    samples = self._receive_samples(self._fft_size)
                    if samples is not None:
                        power = 10 * np.log10(np.mean(np.abs(samples) ** 2) + 1e-10)
                        powers.append(power)
                    time.sleep(0.01)

                if not powers:
                    continue

                # Calculate statistics
                avg_power = np.mean(powers)
                noise_floor = np.percentile(powers, 10)
                snr = avg_power - noise_floor

                # Get GPS position if available
                lat, lon, alt = None, None, None
                if gps_callback:
                    try:
                        lat, lon, alt = gps_callback()
                    except Exception:
                        pass

                survey.points.append(SignalSurveyPoint(
                    frequency=freq,
                    timestamp=datetime.now(),
                    power_dbm=avg_power,
                    snr_db=snr,
                    noise_floor_dbm=noise_floor,
                    latitude=lat,
                    longitude=lon,
                    altitude=alt,
                ))

        survey.end_time = datetime.now()
        survey.calculate_statistics()

        return survey

    def get_waterfall_history(self) -> List[SpectrumSnapshot]:
        """Get recent waterfall snapshots."""
        return list(self._waterfall_history)

    def register_spectrum_callback(self, callback: Callable[[SpectrumSnapshot], None]):
        """Register callback for spectrum updates."""
        self._spectrum_callbacks.append(callback)

    def generate_ascii_spectrum(self, snapshot: SpectrumSnapshot,
                                 width: int = 70, height: int = 15) -> str:
        """
        Generate ASCII spectrum display for TUI.

        Args:
            snapshot: Spectrum snapshot to display
            width: Display width in characters
            height: Display height in lines

        Returns:
            ASCII art string
        """
        if snapshot is None:
            return "No spectrum data available"

        lines = []

        # Title
        center_mhz = snapshot.center_freq / 1e6
        bw_mhz = snapshot.bandwidth / 1e6
        lines.append(f"Center: {center_mhz:.3f} MHz | BW: {bw_mhz:.2f} MHz")
        lines.append(f"Noise: {snapshot.noise_floor_dbm:.1f} dBm | Peak: {snapshot.peak_power_dbm:.1f} dBm")
        lines.append("=" * width)

        # Resample spectrum to display width
        power = snapshot.power_dbm
        if len(power) > width:
            # Downsample
            indices = np.linspace(0, len(power) - 1, width).astype(int)
            display_power = power[indices]
        else:
            display_power = power

        # Normalize to display height
        min_db = snapshot.noise_floor_dbm - 5
        max_db = snapshot.peak_power_dbm + 5
        range_db = max_db - min_db

        # Create ASCII bars
        bar_chars = " ▁▂▃▄▅▆▇█"

        for row in range(height - 1, -1, -1):
            threshold = min_db + (row / height) * range_db
            line = ""
            for val in display_power:
                if val >= threshold + (range_db / height):
                    line += bar_chars[-1]
                elif val >= threshold:
                    idx = int((val - threshold) / (range_db / height) * (len(bar_chars) - 1))
                    idx = max(0, min(len(bar_chars) - 1, idx))
                    line += bar_chars[idx]
                else:
                    line += " "
            lines.append(line)

        # Frequency axis
        start_mhz = (snapshot.center_freq - snapshot.bandwidth / 2) / 1e6
        end_mhz = (snapshot.center_freq + snapshot.bandwidth / 2) / 1e6
        lines.append("=" * width)
        lines.append(f"{start_mhz:.1f} MHz" + " " * (width - 20) + f"{end_mhz:.1f} MHz")

        return "\n".join(lines)

    def get_status(self) -> Dict[str, Any]:
        """Get current RF awareness status."""
        return {
            "connected": self._connected,
            "backend": self._backend.name,
            "device": self._device_info.to_dict() if self._device_info else None,
            "sample_rate": self._sample_rate,
            "fft_size": self._fft_size,
            "gain": self._gain,
            "waterfall_depth": len(self._waterfall_history),
        }


class MockSDR:
    """Mock SDR for testing without hardware."""

    def __init__(self, sample_rate: float = 2.5e6, fft_size: int = 1024):
        self._sample_rate = sample_rate
        self._fft_size = fft_size
        self._frequency = 915e6
        self._gain = 40.0

    def set_frequency(self, freq: float):
        self._frequency = freq

    def set_gain(self, gain: float):
        self._gain = gain

    def receive_samples(self, num_samples: int) -> Any:
        """Generate mock samples with realistic characteristics."""
        # Generate noise
        noise = np.random.randn(num_samples) + 1j * np.random.randn(num_samples)
        noise *= 0.001  # -60 dBFS noise floor

        # Add some simulated signals (LoRa-like chirps)
        t = np.arange(num_samples) / self._sample_rate

        # Occasionally add a "signal"
        if np.random.random() < 0.3:
            # LoRa-like chirp signal
            chirp_rate = 125e3  # 125 kHz bandwidth
            freq_offset = (np.random.random() - 0.5) * self._sample_rate * 0.8
            signal = 0.01 * np.exp(1j * 2 * np.pi * (freq_offset * t + chirp_rate * t ** 2 / 2))
            noise += signal

        return noise.astype(np.complex64)


# Convenience functions

def is_available() -> bool:
    """Check if RF awareness module is fully available (numpy required)."""
    return HAS_NUMPY


def list_sdr_devices() -> List[SDRDeviceInfo]:
    """List available SDR devices."""
    return RFAwareness.list_devices()


def quick_spectrum_check(band: LoRaBand = LoRaBand.US_915,
                          mock: bool = False) -> Optional[Dict[str, Any]]:
    """
    Perform a quick spectrum check.

    Args:
        band: LoRa band to check
        mock: Use mock backend

    Returns:
        Dict with spectrum summary or None
    """
    rf = RFAwareness()

    if not rf.connect(mock=mock):
        return None

    try:
        snapshot = rf.get_spectrum_snapshot(band=band)
        if snapshot:
            return snapshot.to_dict()
        return None

    finally:
        rf.disconnect()
