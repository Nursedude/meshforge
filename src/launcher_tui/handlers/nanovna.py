"""
NanoVNA Handler -- NanoVNA antenna analyzer tools.

Converted from nanovna_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import time

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# NanoVNA plugin - optional dependency
NanoVNADevice, SweepResult, SweepPoint, format_swr, format_impedance, HAS_NANOVNA, _ok1 = safe_import(
    'plugins.nanovna.nanovna_device',
    'NanoVNADevice', 'SweepResult', 'SweepPoint', 'format_swr', 'format_impedance', 'HAS_NANOVNA',
)

SweepStore, _ok2 = safe_import(
    'plugins.nanovna.sweep_store', 'SweepStore',
)

swr_to_mismatch_loss_db, antenna_efficiency_from_swr, sweep_summary_for_band, BANDS, _ok3 = safe_import(
    'plugins.nanovna.rf_integration',
    'swr_to_mismatch_loss_db', 'antenna_efficiency_from_swr', 'sweep_summary_for_band', 'BANDS',
)


class NanoVNAHandler(BaseHandler):
    """TUI handler for NanoVNA antenna analyzer tools."""

    handler_id = "nanovna"
    menu_section = "rf_sdr"

    def menu_items(self):
        return [
            ("nanovna", "NanoVNA             Antenna analyzer tools", None),
        ]

    def execute(self, action):
        if action == "nanovna":
            self._nanovna_menu()

    def _nanovna_menu(self):
        """NanoVNA antenna analyzer tools."""
        while True:
            choices = [
                ("sweep", "Run Frequency Sweep"),
                ("quick_swr", "Quick SWR Check"),
                ("scan", "Scan for Devices"),
                ("history", "Sweep History"),
                ("export", "Export Sweep Data"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "NanoVNA Analyzer",
                "Antenna analysis tools (requires NanoVNA connected via USB):",
                choices,
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "sweep": ("Frequency Sweep", self._nanovna_sweep),
                "quick_swr": ("Quick SWR", self._nanovna_quick_swr),
                "scan": ("Scan Devices", self._nanovna_scan),
                "history": ("Sweep History", self._nanovna_history),
                "export": ("Export Data", self._nanovna_export),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _nanovna_check_available(self) -> bool:
        """Check if NanoVNA functionality is available."""
        if not _ok1 or not HAS_NANOVNA:
            print("NanoVNA support not available.\n")
            print("Install: pip install pynanovna")
            print("  or:    pip install pyserial")
            self.ctx.wait_for_enter()
            return False
        return True

    def _nanovna_scan(self):
        """Scan for connected NanoVNA devices."""
        clear_screen()
        print("=== NanoVNA Device Scan ===\n")

        if not self._nanovna_check_available():
            return

        devices = NanoVNADevice.find_devices()
        if devices:
            print(f"Found {len(devices)} NanoVNA device(s):\n")
            for i, dev in enumerate(devices, 1):
                print(f"  {i}. {dev}")

            # Try to connect to first device
            print(f"\nConnecting to {devices[0]}...")
            device = NanoVNADevice(port=devices[0])
            if device.connect():
                print(f"  Connected ({device.backend} backend)")
                print(f"  Version: {device.device_version}")
                device.disconnect()
            else:
                print("  Connection failed")
        else:
            print("No NanoVNA devices found.\n")
            print("Check:")
            print("  - Device is connected via USB")
            print("  - Device is powered on")
            print("  - User has permission for serial ports")
            print("    (try: sudo usermod -a -G dialout $USER)")

        self.ctx.wait_for_enter()

    def _nanovna_sweep(self):
        """Run a frequency sweep with configurable parameters."""
        clear_screen()
        print("=== NanoVNA Frequency Sweep ===\n")

        if not self._nanovna_check_available():
            return

        # Get sweep parameters via dialog
        start_mhz = self.ctx.dialog.inputbox(
            "Start Frequency",
            "Enter start frequency in MHz:",
            "906",
        )
        if start_mhz is None:
            return

        stop_mhz = self.ctx.dialog.inputbox(
            "Stop Frequency",
            "Enter stop frequency in MHz:",
            "924",
        )
        if stop_mhz is None:
            return

        points = self.ctx.dialog.inputbox(
            "Sweep Points",
            "Number of measurement points (11-401):",
            "101",
        )
        if points is None:
            return

        try:
            start = float(start_mhz)
            stop = float(stop_mhz)
            num_points = int(points)
        except ValueError:
            self.ctx.dialog.msgbox("Error", "Invalid numeric input.")
            return

        if start >= stop:
            self.ctx.dialog.msgbox("Error", "Start frequency must be less than stop frequency.")
            return

        num_points = max(11, min(401, num_points))

        clear_screen()
        print(f"Sweeping {start:.1f} - {stop:.1f} MHz ({num_points} points)...\n")

        device = NanoVNADevice()
        if not device.connect():
            print("Failed to connect to NanoVNA.")
            self.ctx.wait_for_enter()
            return

        try:
            result = device.sweep(
                start_hz=int(start * 1e6),
                stop_hz=int(stop * 1e6),
                points=num_points,
            )
        finally:
            device.disconnect()

        if not result.points:
            print("Sweep returned no data.")
            self.ctx.wait_for_enter()
            return

        # Display results
        self._nanovna_display_sweep(result)

        # Offer to save
        save = self.ctx.dialog.yesno("Save Sweep", "Save this sweep to history?")
        if save and _ok2:
            label = self.ctx.dialog.inputbox(
                "Label",
                "Enter a label for this sweep (optional):",
                "",
            ) or ""
            try:
                store = SweepStore()
                sweep_id = store.save_sweep(result, label=label)
                print(f"\nSaved as sweep #{sweep_id}")
            except Exception as e:
                print(f"\nSave failed: {e}")
            self.ctx.wait_for_enter()

    def _nanovna_display_sweep(self, result):
        """Display sweep results as formatted text."""
        min_swr_val, min_swr_freq = result.min_swr

        print(f"Sweep: {result.frequency_range[0]:.1f} - {result.frequency_range[1]:.1f} MHz")
        print(f"Points: {len(result.points)}")
        print(f"Best SWR: {format_swr(min_swr_val)} at {min_swr_freq:.3f} MHz")

        # Show impedance at best match
        best_point = min(result.points, key=lambda p: p.swr)
        print(f"Impedance: {format_impedance(best_point.impedance)} ohm")
        print(f"Return Loss: {best_point.return_loss_db:.1f} dB")

        if _ok3:
            loss = swr_to_mismatch_loss_db(min_swr_val)
            eff = antenna_efficiency_from_swr(min_swr_val)
            print(f"Mismatch Loss: {loss:.2f} dB")
            print(f"Efficiency: {eff*100:.1f}%")

        # Table of points (show up to 20)
        print(f"\n{'Freq (MHz)':>12s}  {'SWR':>8s}  {'Return Loss':>12s}  {'Impedance':>20s}")
        print("-" * 58)

        step = max(1, len(result.points) // 20)
        for i in range(0, len(result.points), step):
            p = result.points[i]
            swr_str = format_swr(p.swr)
            z_str = format_impedance(p.impedance)
            rl_str = f"{p.return_loss_db:.1f} dB"
            print(f"{p.frequency_mhz:>12.3f}  {swr_str:>8s}  {rl_str:>12s}  {z_str:>20s}")

        print()

    def _nanovna_quick_swr(self):
        """Quick SWR measurement at common frequencies."""
        clear_screen()
        print("=== Quick SWR Check ===\n")

        if not self._nanovna_check_available():
            return

        choices = [
            ("915", "915 MHz (LoRa US)"),
            ("868", "868 MHz (LoRa EU)"),
            ("433", "433 MHz (LoRa 433)"),
            ("144", "144 MHz (2m)"),
            ("430", "430 MHz (70cm)"),
            ("custom", "Custom Frequency"),
        ]

        choice = self.ctx.dialog.menu(
            "Quick SWR",
            "Select frequency for SWR measurement:",
            choices,
        )

        if choice is None:
            return

        if choice == "custom":
            freq_str = self.ctx.dialog.inputbox(
                "Frequency",
                "Enter frequency in MHz:",
                "915",
            )
            if freq_str is None:
                return
            try:
                freq_mhz = float(freq_str)
            except ValueError:
                self.ctx.dialog.msgbox("Error", "Invalid frequency.")
                return
        else:
            freq_mhz = float(choice)

        clear_screen()
        print(f"Measuring SWR at {freq_mhz:.1f} MHz...\n")

        device = NanoVNADevice()
        if not device.connect():
            print("Failed to connect to NanoVNA.")
            self.ctx.wait_for_enter()
            return

        try:
            swr = device.quick_swr(int(freq_mhz * 1e6))
        finally:
            device.disconnect()

        if swr is not None:
            print(f"  Frequency:    {freq_mhz:.3f} MHz")
            print(f"  SWR:          {format_swr(swr)}")

            if _ok3:
                loss = swr_to_mismatch_loss_db(swr)
                eff = antenna_efficiency_from_swr(swr)
                print(f"  Mismatch Loss: {loss:.2f} dB")
                print(f"  Efficiency:   {eff*100:.1f}%")

                if swr < 1.5:
                    print("\n  Assessment: Excellent match")
                elif swr < 2.0:
                    print("\n  Assessment: Good match")
                elif swr < 3.0:
                    print("\n  Assessment: Acceptable")
                else:
                    print("\n  Assessment: Poor match - antenna may need tuning")
        else:
            print("  Measurement failed. Check device connection.")

        self.ctx.wait_for_enter()

    def _nanovna_history(self):
        """Browse saved sweep history."""
        clear_screen()
        print("=== Sweep History ===\n")

        if not _ok2:
            print("Sweep storage not available.")
            self.ctx.wait_for_enter()
            return

        try:
            store = SweepStore()
            sweeps = store.list_sweeps(limit=20)
        except Exception as e:
            print(f"Error loading history: {e}")
            self.ctx.wait_for_enter()
            return

        if not sweeps:
            print("No saved sweeps.")
            self.ctx.wait_for_enter()
            return

        choices = []
        for s in sweeps:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.timestamp))
            label = f" [{s.label}]" if s.label else ""
            desc = f"{ts} {s.start_mhz:.0f}-{s.stop_mhz:.0f}MHz SWR:{s.min_swr:.2f}{label}"
            choices.append((str(s.id), desc))

        choice = self.ctx.dialog.menu(
            "Sweep History",
            f"{len(sweeps)} saved sweep(s):",
            choices,
        )

        if choice is None:
            return

        sweep_id = int(choice)
        result = store.get_sweep_result(sweep_id)
        if result:
            clear_screen()
            meta = store.get_sweep(sweep_id)
            if meta and meta.label:
                print(f"Label: {meta.label}\n")
            self._nanovna_display_sweep(result)
            self.ctx.wait_for_enter()

    def _nanovna_export(self):
        """Export sweep data to CSV."""
        clear_screen()
        print("=== Export Sweep Data ===\n")

        if not _ok2:
            print("Sweep storage not available.")
            self.ctx.wait_for_enter()
            return

        try:
            store = SweepStore()
            sweeps = store.list_sweeps(limit=20)
        except Exception as e:
            print(f"Error: {e}")
            self.ctx.wait_for_enter()
            return

        if not sweeps:
            print("No saved sweeps to export.")
            self.ctx.wait_for_enter()
            return

        choices = []
        for s in sweeps:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.timestamp))
            label = f" [{s.label}]" if s.label else ""
            desc = f"{ts} {s.start_mhz:.0f}-{s.stop_mhz:.0f}MHz{label}"
            choices.append((str(s.id), desc))

        choice = self.ctx.dialog.menu(
            "Export Sweep",
            "Select sweep to export:",
            choices,
        )

        if choice is None:
            return

        sweep_id = int(choice)
        csv_data = store.export_csv(sweep_id)
        if not csv_data:
            print("No data to export.")
            self.ctx.wait_for_enter()
            return

        # Write to file (MF001: always use get_real_user_home, never Path.home)
        from utils.paths import get_real_user_home
        home = get_real_user_home()

        export_dir = home / ".config" / "meshforge" / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"nanovna_sweep_{sweep_id}_{int(time.time())}.csv"
        export_path = export_dir / filename

        export_path.write_text(csv_data)
        print(f"Exported to: {export_path}")
        print(f"Data points: {csv_data.count(chr(10))}")

        self.ctx.wait_for_enter()
