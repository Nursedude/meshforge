"""
SDR Handler — RF awareness and SDR monitoring tools.

Converted from rf_awareness_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import time

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

RFAwareness, LoRaBand, _HAS_RF_AWARENESS = safe_import(
    'utils.rf_awareness', 'RFAwareness', 'LoRaBand'
)


class SDRHandler(BaseHandler):
    """TUI handler for RF awareness / SDR monitoring."""

    handler_id = "sdr"
    menu_section = "rf_sdr"

    def __init__(self):
        super().__init__()
        self._rf_awareness = None

    def menu_items(self):
        return [
            ("sdr", "SDR Monitor         RF awareness (Airspy)", None),
        ]

    def execute(self, action):
        if action == "sdr":
            self._rf_awareness_menu()

    def _get_rf_awareness(self):
        if not _HAS_RF_AWARENESS:
            return None
        if self._rf_awareness is None:
            self._rf_awareness = RFAwareness()
        return self._rf_awareness

    def _rf_awareness_menu(self):
        choices = [
            ("status", "SDR Status & Connection"),
            ("spectrum", "Spectrum Snapshot"),
            ("waterfall", "Spectrum Waterfall (ASCII)"),
            ("utilization", "Channel Utilization Monitor"),
            ("survey", "Signal Strength Survey"),
            ("interference", "Interference Detection"),
            ("settings", "SDR Settings"),
            ("back", "Back"),
        ]
        while True:
            choice = self.ctx.dialog.menu("RF Awareness (SDR)", "LoRa band monitoring with Airspy SDR:", choices)
            if choice is None or choice == "back":
                break
            dispatch = {
                "status": ("RF Status", self._rf_status),
                "spectrum": ("Spectrum Snapshot", self._rf_spectrum_snapshot),
                "waterfall": ("Waterfall Display", self._rf_waterfall),
                "utilization": ("Channel Utilization", self._rf_utilization),
                "survey": ("Signal Survey", self._rf_survey),
                "interference": ("Interference Detection", self._rf_interference),
                "settings": ("SDR Settings", self._rf_settings),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _rf_status(self):
        rf = self._get_rf_awareness()
        if rf is None:
            self.ctx.dialog.msgbox("Unavailable", "RF Awareness module not loaded.\n\nRequired: numpy, SoapySDR (optional)")
            return
        try:
            from utils.rf_awareness import list_sdr_devices
            devices = list_sdr_devices()
        except Exception:
            devices = []
        lines = ["SDR STATUS", "=" * 50, ""]
        status = rf.get_status()
        lines.append(f"Connected: {'Yes' if status['connected'] else 'No'}")
        lines.append(f"Backend: {status['backend']}")
        if status['device']:
            lines.extend(["", "DEVICE INFO:", f"  Label: {status['device'].get('label', 'N/A')}", f"  Driver: {status['device'].get('driver', 'N/A')}", f"  Hardware: {status['device'].get('hardware', 'N/A')}"])
        lines.extend(["", "SETTINGS:", f"  Sample Rate: {status['sample_rate'] / 1e6:.2f} MSPS", f"  FFT Size: {status['fft_size']}", f"  Gain: {status['gain']:.1f} dB"])
        lines.extend(["", "AVAILABLE DEVICES:"])
        if devices:
            for dev in devices:
                lines.append(f"  • {dev.label} ({dev.driver})")
        else:
            lines.extend(["  No SoapySDR devices found", "  (Mock mode available for testing)"])
        self.ctx.dialog.msgbox("SDR Status", "\n".join(lines))
        if not rf.is_connected:
            if self.ctx.dialog.yesno("Connect SDR", "Connect to SDR device?\n\nIf no hardware found, will use mock mode for testing."):
                self.ctx.dialog.infobox("Connecting...", "Connecting to SDR...")
                has_airspy = any("airspy" in d.driver.lower() for d in devices)
                if rf.connect(device_filter="airspy" if has_airspy else None):
                    self.ctx.dialog.msgbox("Connected", f"Connected to: {rf.device_info.label}\nBackend: {rf.backend.name}")
                else:
                    self.ctx.dialog.msgbox("Error", "Failed to connect to SDR")
        else:
            if self.ctx.dialog.yesno("Disconnect", "Disconnect from SDR?", default_no=True):
                rf.disconnect()
                self.ctx.dialog.msgbox("Disconnected", "SDR disconnected")

    def _rf_spectrum_snapshot(self):
        rf = self._get_rf_awareness()
        if rf is None:
            self.ctx.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return
        if not rf.is_connected:
            if not rf.connect():
                self.ctx.dialog.msgbox("Error", "Failed to connect to SDR")
                return
        if _HAS_RF_AWARENESS:
            band_choices = [("US_915", "US 915 MHz (902-928 MHz)"), ("EU_868", "EU 868 MHz (863-870 MHz)"), ("EU_433", "EU 433 MHz (433-434 MHz)"), ("AS_923", "Asia 923 MHz (920-925 MHz)"), ("custom", "Custom Frequency"), ("back", "Back")]
        else:
            band_choices = [("custom", "Custom Frequency"), ("back", "Back")]
        band_choice = self.ctx.dialog.menu("Select Band", "Choose frequency band to monitor:", band_choices)
        if not band_choice or band_choice == "back":
            return
        band = None
        center_freq = None
        if band_choice == "custom":
            freq_str = self.ctx.dialog.inputbox("Center Frequency", "Enter center frequency (MHz):", "915.0")
            if freq_str:
                try:
                    center_freq = float(freq_str) * 1e6
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid frequency")
                    return
        else:
            band = LoRaBand[band_choice]
        self.ctx.dialog.infobox("Scanning...", "Capturing spectrum snapshot...")
        snapshot = rf.get_spectrum_snapshot(band=band, center_freq=center_freq)
        if snapshot is None:
            self.ctx.dialog.msgbox("Error", "Failed to capture spectrum")
            return
        ascii_spectrum = rf.generate_ascii_spectrum(snapshot, width=70, height=12)
        lines = [ascii_spectrum, "", f"Timestamp: {snapshot.timestamp.strftime('%H:%M:%S')}", f"Noise Floor: {snapshot.noise_floor_dbm:.1f} dBm", f"Peak: {snapshot.peak_power_dbm:.1f} dBm at {snapshot.peak_freq / 1e6:.3f} MHz"]
        self.ctx.dialog.msgbox("Spectrum Snapshot", "\n".join(lines))

    def _rf_waterfall(self):
        rf = self._get_rf_awareness()
        if rf is None:
            self.ctx.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return
        if not rf.is_connected:
            if not rf.connect():
                self.ctx.dialog.msgbox("Error", "Failed to connect to SDR")
                return
        if not _HAS_RF_AWARENESS:
            self.ctx.dialog.msgbox("Error", "LoRaBand not available")
            return
        band = LoRaBand.US_915
        clear_screen()
        print("=== RF Waterfall Display ===")
        print(f"Band: {band.description}")
        print(f"Center: {band.center_freq / 1e6:.3f} MHz")
        print("\nPress Ctrl+C to stop\n")
        try:
            while True:
                snapshot = rf.get_spectrum_snapshot(band=band, averaging=3)
                if snapshot:
                    import numpy as np
                    power = snapshot.power_dbm
                    indices = np.linspace(0, len(power) - 1, 60).astype(int)
                    display = power[indices]
                    min_db = snapshot.noise_floor_dbm - 5
                    max_db = snapshot.peak_power_dbm + 5
                    bar_chars = " ▁▂▃▄▅▆▇█"
                    line = ""
                    for val in display:
                        level = (val - min_db) / (max_db - min_db)
                        level = max(0, min(1, level))
                        idx = int(level * (len(bar_chars) - 1))
                        line += bar_chars[idx]
                    timestamp = snapshot.timestamp.strftime('%H:%M:%S')
                    print(f"{timestamp} |{line}| {snapshot.peak_power_dbm:.0f}dBm")
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n\nWaterfall stopped.")
        self.ctx.wait_for_enter()

    def _rf_utilization(self):
        rf = self._get_rf_awareness()
        if rf is None:
            self.ctx.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return
        if not rf.is_connected:
            if not rf.connect():
                self.ctx.dialog.msgbox("Error", "Failed to connect to SDR")
                return
        duration_str = self.ctx.dialog.inputbox("Measurement Duration", "Enter measurement duration (seconds):", "10")
        if not duration_str:
            return
        try:
            duration = float(duration_str)
            if duration < 1 or duration > 300:
                raise ValueError("Duration out of range")
        except ValueError:
            self.ctx.dialog.msgbox("Error", "Invalid duration (1-300 seconds)")
            return
        band = LoRaBand.US_915 if _HAS_RF_AWARENESS else None
        self.ctx.dialog.infobox("Measuring...", f"Measuring channel utilization for {duration:.0f} seconds...")
        util = rf.measure_channel_utilization(band=band, duration_sec=duration)
        if util is None:
            self.ctx.dialog.msgbox("Error", "Measurement failed")
            return
        lines = [
            "CHANNEL UTILIZATION", "=" * 50, "",
            f"Frequency: {util.frequency / 1e6:.3f} MHz", f"Duration: {util.duration_sec:.1f} seconds", "",
            "RESULTS:", f"  Utilization: {util.utilization_percent:.1f}%", f"  Duty Cycle: {util.duty_cycle:.4f}",
            f"  Active Time: {util.active_time_sec:.2f} sec", "",
            "SIGNALS:", f"  Signal Count: {util.signal_count}", f"  Avg Power: {util.avg_signal_power_dbm:.1f} dBm",
            f"  Peak Power: {util.peak_signal_power_dbm:.1f} dBm", "", f"Noise Floor: {util.noise_floor_dbm:.1f} dBm",
        ]
        if util.utilization_percent < 5:
            assessment = "Very Low - Channel is mostly clear"
        elif util.utilization_percent < 20:
            assessment = "Low - Occasional activity"
        elif util.utilization_percent < 50:
            assessment = "Moderate - Regular activity"
        elif util.utilization_percent < 80:
            assessment = "High - Heavy usage"
        else:
            assessment = "Very High - Channel congested"
        lines.extend(["", f"Assessment: {assessment}"])
        self.ctx.dialog.msgbox("Channel Utilization", "\n".join(lines))

    def _rf_survey(self):
        rf = self._get_rf_awareness()
        if rf is None:
            self.ctx.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return
        if not rf.is_connected:
            if not rf.connect():
                self.ctx.dialog.msgbox("Error", "Failed to connect to SDR")
                return
        if not _HAS_RF_AWARENESS:
            self.ctx.dialog.msgbox("Error", "Module not available")
            return
        band_choices = [("US_915", "US 915 MHz Band"), ("EU_868", "EU 868 MHz Band"), ("back", "Back")]
        band_choice = self.ctx.dialog.menu("Signal Survey", "Select band to survey:", band_choices)
        if not band_choice or band_choice == "back":
            return
        band = LoRaBand[band_choice]
        self.ctx.dialog.infobox("Surveying...", f"Surveying {band.description}...\nThis will take about 20 seconds.")
        survey = rf.signal_survey(band=band, duration_per_freq_sec=2.0)
        if not survey.points:
            self.ctx.dialog.msgbox("Error", "Survey failed - no data collected")
            return
        lines = ["SIGNAL STRENGTH SURVEY", "=" * 50, "", f"Band: {band.description}", f"Duration: {survey.statistics.get('duration_sec', 0):.1f} seconds", f"Points: {survey.statistics.get('point_count', 0)}", "", "FREQUENCY SCAN:"]
        for point in survey.points:
            freq_mhz = point.frequency / 1e6
            power_bar = self._power_bar(point.power_dbm, -100, -40)
            lines.append(f"  {freq_mhz:7.2f} MHz: {power_bar} {point.power_dbm:.1f} dBm")
        stats = survey.statistics
        lines.extend(["", "STATISTICS:", f"  Min Power: {stats.get('power_min_dbm', 0):.1f} dBm", f"  Max Power: {stats.get('power_max_dbm', 0):.1f} dBm", f"  Avg Power: {stats.get('power_avg_dbm', 0):.1f} dBm", f"  Avg SNR: {stats.get('snr_avg_db', 0):.1f} dB"])
        self.ctx.dialog.msgbox("Survey Results", "\n".join(lines))

    @staticmethod
    def _power_bar(power_dbm, min_dbm, max_dbm, width=15):
        level = (power_dbm - min_dbm) / (max_dbm - min_dbm)
        level = max(0, min(1, level))
        filled = int(level * width)
        return "█" * filled + "░" * (width - filled)

    def _rf_interference(self):
        rf = self._get_rf_awareness()
        if rf is None:
            self.ctx.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return
        if not rf.is_connected:
            if not rf.connect():
                self.ctx.dialog.msgbox("Error", "Failed to connect to SDR")
                return
        if not _HAS_RF_AWARENESS:
            self.ctx.dialog.msgbox("Error", "Module not available")
            return
        band = LoRaBand.US_915
        self.ctx.dialog.infobox("Scanning...", "Scanning for interference sources...")
        import numpy as np
        snapshots = []
        for _ in range(5):
            snapshot = rf.get_spectrum_snapshot(band=band, averaging=5)
            if snapshot:
                snapshots.append(snapshot)
            time.sleep(0.2)
        if len(snapshots) < 3:
            self.ctx.dialog.msgbox("Error", "Insufficient data collected")
            return
        lines = ["INTERFERENCE ANALYSIS", "=" * 50, "", f"Band: {band.description}", f"Snapshots: {len(snapshots)}", ""]
        avg_power = np.mean([s.power_dbm for s in snapshots], axis=0)
        noise_floor = np.percentile(avg_power, 10)
        threshold = noise_floor + 10
        interference_bins = np.where(avg_power > threshold)[0]
        if len(interference_bins) > 0:
            lines.append("DETECTED SIGNALS:")
            signals = []
            current_signal = [interference_bins[0]]
            for i in range(1, len(interference_bins)):
                if interference_bins[i] - interference_bins[i - 1] <= 2:
                    current_signal.append(interference_bins[i])
                else:
                    signals.append(current_signal)
                    current_signal = [interference_bins[i]]
            signals.append(current_signal)
            freqs = snapshots[0].frequencies
            for i, signal_bins in enumerate(signals[:5]):
                center_bin = signal_bins[len(signal_bins) // 2]
                freq = freqs[center_bin] / 1e6
                power = avg_power[center_bin]
                width_khz = len(signal_bins) * (snapshots[0].bandwidth / len(freqs)) / 1e3
                lines.extend([f"  Signal {i + 1}:", f"    Frequency: {freq:.3f} MHz", f"    Power: {power:.1f} dBm", f"    Width: ~{width_khz:.0f} kHz", ""])
            if len(signals) > 5:
                lines.append(f"  ... and {len(signals) - 5} more signals")
        else:
            lines.extend(["No significant interference detected.", "Channel appears clear."])
        lines.extend(["", f"Noise Floor: {noise_floor:.1f} dBm"])
        self.ctx.dialog.msgbox("Interference Analysis", "\n".join(lines))

    def _rf_settings(self):
        rf = self._get_rf_awareness()
        if rf is None:
            self.ctx.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return
        settings_choices = [
            ("gain", f"Gain (current: {rf._gain:.0f} dB)"),
            ("sample_rate", f"Sample Rate (current: {rf._sample_rate / 1e6:.1f} MSPS)"),
            ("fft_size", f"FFT Size (current: {rf._fft_size})"),
            ("back", "Back"),
        ]
        while True:
            choice = self.ctx.dialog.menu("SDR Settings", "Configure SDR parameters:", settings_choices)
            if choice is None or choice == "back":
                break
            if choice == "gain":
                gain_str = self.ctx.dialog.inputbox("Gain", "Enter gain in dB (0-45 for Airspy):", str(rf._gain))
                if gain_str:
                    try:
                        gain = float(gain_str)
                        if 0 <= gain <= 50:
                            rf._gain = gain
                            if rf.is_connected:
                                rf.set_gain(gain)
                            self.ctx.dialog.msgbox("Updated", f"Gain set to {gain:.0f} dB")
                        else:
                            self.ctx.dialog.msgbox("Error", "Gain must be 0-50 dB")
                    except ValueError:
                        self.ctx.dialog.msgbox("Error", "Invalid gain value")
            elif choice == "sample_rate":
                rate_choices = [("2.5", "2.5 MSPS (recommended)"), ("5.0", "5.0 MSPS"), ("10.0", "10.0 MSPS (Airspy R2 only)")]
                rate_choice = self.ctx.dialog.menu("Sample Rate", "Select sample rate:", rate_choices)
                if rate_choice:
                    rf._sample_rate = float(rate_choice) * 1e6
                    self.ctx.dialog.msgbox("Updated", f"Sample rate set to {rate_choice} MSPS\n(Reconnect to apply)")
            elif choice == "fft_size":
                fft_choices = [("512", "512 (fast, lower resolution)"), ("1024", "1024 (balanced)"), ("2048", "2048 (high resolution)"), ("4096", "4096 (very high resolution)")]
                fft_choice = self.ctx.dialog.menu("FFT Size", "Select FFT size:", fft_choices)
                if fft_choice:
                    rf._fft_size = int(fft_choice)
                    self.ctx.dialog.msgbox("Updated", f"FFT size set to {fft_choice}")
            settings_choices = [
                ("gain", f"Gain (current: {rf._gain:.0f} dB)"),
                ("sample_rate", f"Sample Rate (current: {rf._sample_rate / 1e6:.1f} MSPS)"),
                ("fft_size", f"FFT Size (current: {rf._fft_size})"),
                ("back", "Back"),
            ]
