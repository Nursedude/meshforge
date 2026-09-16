"""NanoVNA antenna analyzer — detect, sweep, and compare against a baseline.

Why this exists (2026-09-15): the driver behind it
(``src/utils/nanovna.py``, 411 lines, complete and working) spent months in
``plugins/nanovna_analyzer/`` — a GTK-era tree that NOTHING loads, since
``handlers/extensions.py`` has zero references to ``plugins/``. So MeshForge
shipped a real antenna analyzer that no operator could reach, one menu row
away from "Antenna Analysis", which only compares antenna *types* from a
static table. This handler is the missing surface.

THE WORKFLOW IT IS BUILT FOR (operator, 2026-09-15: *"field testing ant- is a
real thing"*, and the VNA will ride in the ecomm kit on kiai): sweep an
antenna, SAVE it as a baseline while you know it is good, then re-sweep in
the field and COMPARE. "Is this antenna still what I mounted?" is a question
a number answers and a memory does not — and answering it before you blame
the link budget is the whole point of carrying a VNA.
"""

import json
import logging
import time

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.paths import get_real_user_home
from utils.safe_import import safe_import

# First-party -> DIRECT import (MF006). utils.nanovna imports cleanly with or
# without pyserial; the optional dependency is handled inside that module, so
# there is nothing here to guard against.
from utils import nanovna as _nanovna

logger = logging.getLogger(__name__)

#: Band presets. The mesh bands come FIRST because this is a mesh NOC — the
#: antenna an operator most often needs to trust is the one on the radio in
#: front of them, not an HF wire.
BAND_PRESETS = [
    ("lora915", "LoRa 902-928 MHz (US/ITU-2)", 902_000_000, 928_000_000),
    ("lora868", "LoRa 863-870 MHz (EU)", 863_000_000, 870_000_000),
    ("lora433", "LoRa 433 MHz (ITU-1/3)", 433_000_000, 435_000_000),
    ("vhf2m", "2 m ham 144-148 MHz", 144_000_000, 148_000_000),
    ("uhf70cm", "70 cm ham 420-450 MHz", 420_000_000, 450_000_000),
    ("wifi24", "2.4 GHz (AREDN/WiFi)", 2_400_000_000, 2_500_000_000),
]

#: Sweep resolution. 101 is the NanoVNA's native default and is plenty for
#: "is this antenna healthy" — more points cost sweep time in the field.
DEFAULT_POINTS = 101

#: SWR bands used for the verdict line. 1.5 is the usual "happy" bar for a
#: mesh whip; past 3.0 most radios are folding back power.
SWR_GOOD = 1.5
SWR_USABLE = 3.0


class NanoVNAHandler(BaseHandler):
    """TUI handler for a NanoVNA antenna analyzer."""

    handler_id = "nanovna"
    menu_section = "rf_sdr"

    def menu_items(self):
        return [
            ("vna", "Antenna Analyzer    NanoVNA sweep, SWR, baselines", None),
        ]

    def execute(self, action):
        if action == "vna":
            self._vna_menu()
        else:
            self.ctx.notify_unwired(action, "NanoVNAHandler.execute")

    # ------------------------------------------------------------------
    # storage
    # ------------------------------------------------------------------

    def _baseline_dir(self):
        """Where saved sweeps live (MF001: never Path.home())."""
        return get_real_user_home() / ".local" / "share" / "meshforge" / "vna"

    def _list_baselines(self):
        d = self._baseline_dir()
        if not d.exists():
            return []
        return sorted(p for p in d.glob("*.json"))

    def _save_baseline(self, name, result):
        """Persist a sweep. Returns (ok, message) — never a bare bool."""
        safe = "".join(c for c in name if c.isalnum() or c in "-_")[:48]
        if not safe:
            return False, "Name must contain at least one letter or digit."
        d = self._baseline_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            payload = {
                "name": safe,
                "saved_at": time.time(),
                "device": result.device_info,
                "points": [
                    {"hz": p.frequency_hz, "re": p.s11_real, "im": p.s11_imag}
                    for p in result.points
                ],
            }
            path = d / f"{safe}.json"
            tmp = path.with_suffix(f".tmp.{id(self)}")
            tmp.write_text(json.dumps(payload))
            tmp.replace(path)
            return True, str(path)
        except (OSError, ValueError, TypeError) as e:
            return False, f"{type(e).__name__}: {e}"

    def _load_baseline(self, path):
        """Rebuild a SweepResult from disk. Returns (result, error)."""
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            return None, f"{type(e).__name__}: {e}"
        try:
            result = _nanovna.SweepResult(
                timestamp=float(data.get("saved_at") or 0.0),
                device_info=str(data.get("device") or ""),
            )
            for row in data.get("points") or []:
                result.points.append(_nanovna.SweepPoint(
                    frequency_hz=int(row["hz"]),
                    s11_real=float(row["re"]),
                    s11_imag=float(row["im"]),
                ))
        except (KeyError, TypeError, ValueError) as e:
            return None, f"malformed baseline: {type(e).__name__}: {e}"
        if not result.points:
            return None, "baseline contains no sweep points"
        return result, ""

    # ------------------------------------------------------------------
    # menu
    # ------------------------------------------------------------------

    def _vna_menu(self):
        self._last_result = getattr(self, "_last_result", None)

        while True:
            have = "yes" if self._last_result else "no"
            choices = [
                ("detect", "Detect Device       Find a connected NanoVNA"),
                ("sweep", "Sweep a Band        Measure SWR across a band"),
                ("quick", "Quick SWR           One frequency, fast"),
                ("save", f"Save as Baseline    (sweep in memory: {have})"),
                ("compare", "Compare to Baseline Field check vs a known-good"),
                ("list", "Saved Baselines     List / delete"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "Antenna Analyzer (NanoVNA)",
                "Measure an antenna, and prove it is still the one you mounted:",
                choices)

            if choice is None or choice == "back":
                break

            dispatch = {
                "detect": self._detect,
                "sweep": self._sweep_band,
                "quick": self._quick_swr,
                "save": self._save_current,
                "compare": self._compare,
                "list": self._manage_baselines,
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(f"nanovna:{choice}", entry)
            else:
                self.ctx.notify_unwired(choice, "NanoVNAHandler._vna_menu")

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def _find_ports(self):
        """Return (ports, error). Never conflates 'none' with 'cannot look'."""
        try:
            return _nanovna.NanoVNADevice.find_devices(), ""
        except Exception as e:
            # NanoVNAUnavailable carries the pyserial-missing message; any
            # other exception is still "we could not look", not "none found".
            return None, str(e)

    @staticmethod
    def _print_all_ports():
        """Show every serial port with VID:PID — what we actually saw.

        Deliberately separate from find_devices(): that answers "which of
        these is a NanoVNA", this answers "what is on the bus at all". An
        operator holding a clone needs the second question answered.
        """
        lp, ok = safe_import('serial.tools.list_ports')
        if not ok:
            print("  (cannot list ports — pyserial is not installed)")
            return
        try:
            ports = list(lp.comports())
        except Exception as e:
            print(f"  (port scan failed: {type(e).__name__}: {e})")
            return
        if not ports:
            print("  No serial ports on this box at all.")
            return
        print(f"  All {len(ports)} serial port(s) currently present:")
        for prt in ports:
            vid = f"{prt.vid:04x}" if prt.vid else "----"
            pid = f"{prt.pid:04x}" if prt.pid else "----"
            print(f"    {prt.device:<16} {vid}:{pid}  {prt.description}")

    def _detect(self):
        clear_screen()
        print("=== NANOVNA DETECT ===\n")
        ports, err = self._find_ports()

        if ports is None:
            print(f"  Cannot enumerate serial ports: {err}\n")
            print("  This is NOT 'no analyzer connected' — we could not look.")
            self.ctx.wait_for_enter()
            return

        if not ports:
            print("  No port matched a known NanoVNA signature.\n")
            print("  Checked USB IDs: 0483:5740 (STM32 VCP — NanoVNA / -H /")
            print("                   -H4 incl. SEESII), 04B4:0008, 04B4:000A,")
            print("                   plus any description mentioning")
            print("                   'nanovna' or 'stm32'.\n")
            # "No NanoVNA found" is a dead end; the ports that ARE present is
            # actionable. A clone with an unlisted VID:PID is indistinguishable
            # from an empty USB bus unless we show what we actually saw.
            self._print_all_ports()
            print("\n  If it IS plugged in:")
            print("    - the cable must carry DATA, not charge-only")
            print("    - your user must be in the 'dialout' group")
            print("      (groups | grep dialout; then log out and back in)")
            print("    - if a port above looks like your VNA but was not")
            print("      matched, note its VID:PID — it needs adding to")
            print("      NanoVNADevice.VID_PID_PAIRS in utils/nanovna.py.")
            self.ctx.wait_for_enter()
            return

        print(f"  {len(ports)} candidate port(s):\n")
        for p in ports:
            print(f"    {p}")

        # ⚠️ kiai (the ecomm kit's NOC) will carry BOTH this and a VK-162 GPS,
        # and both enumerate as /dev/ttyACM*. Naming the collision here beats
        # debugging it in a truck.
        if len(ports) > 1:
            print("\n  More than one candidate. If this box also carries a USB")
            print("  GPS (the ecomm kit's VK-162 is /dev/ttyACM*, u-blox")
            print("  1546:01a7), pin BOTH with udev rules by VID:PID so the")
            print("  names cannot swap on the next boot.")

        print("\n  Connecting to the first candidate...")
        dev = _nanovna.NanoVNADevice(ports[0])
        try:
            if dev.connect():
                print(f"  OK: {ports[0]} -> {dev.device_version or 'no version string'}")
            else:
                print(f"  Could not open {ports[0]} (busy, or not a NanoVNA).")
        except Exception as e:
            print(f"  Connect failed: {type(e).__name__}: {e}")
        finally:
            dev.disconnect()

        self.ctx.wait_for_enter()

    def _open_device(self):
        """Connect to the first NanoVNA. Returns (device, error)."""
        ports, err = self._find_ports()
        if ports is None:
            return None, f"cannot enumerate serial ports: {err}"
        if not ports:
            return None, "no NanoVNA found (run Detect Device for what was checked)"
        dev = _nanovna.NanoVNADevice(ports[0])
        try:
            if not dev.connect():
                return None, f"could not open {ports[0]}"
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"
        return dev, ""

    def _sweep_band(self):
        choices = [(tag, label) for tag, label, _, _ in BAND_PRESETS]
        choices.append(("custom", "Custom range (MHz)"))
        choices.append(("back", "Back"))
        pick = self.ctx.dialog.menu("Sweep a Band", "Which band?", choices)
        if pick is None or pick == "back":
            return

        if pick == "custom":
            raw = self.ctx.dialog.inputbox(
                "Custom Range", "Start and stop in MHz, e.g. 902 928", "")
            if not raw:
                return
            try:
                start_mhz, stop_mhz = (float(x) for x in raw.split()[:2])
                start_hz, stop_hz = int(start_mhz * 1e6), int(stop_mhz * 1e6)
            except (ValueError, TypeError):
                self.ctx.dialog.msgbox(
                    "Bad Range", f"Could not read '{raw}' as two MHz numbers.")
                return
            if stop_hz <= start_hz:
                self.ctx.dialog.msgbox(
                    "Bad Range", "Stop frequency must be above start.")
                return
            label = f"{start_mhz:g}-{stop_mhz:g} MHz"
        else:
            entry = next(b for b in BAND_PRESETS if b[0] == pick)
            label, start_hz, stop_hz = entry[1], entry[2], entry[3]

        clear_screen()
        print(f"=== SWEEP: {label} ===\n")
        dev, err = self._open_device()
        if dev is None:
            print(f"  {err}")
            self.ctx.wait_for_enter()
            return

        try:
            print(f"  Sweeping {start_hz/1e6:g}-{stop_hz/1e6:g} MHz, "
                  f"{DEFAULT_POINTS} points...")
            result = dev.sweep(start_hz, stop_hz, DEFAULT_POINTS)
        finally:
            dev.disconnect()

        if not result.points:
            print("\n  The sweep returned NO points.")
            print("  The device answered but produced no data — treat this as")
            print("  UNKNOWN, not as a bad antenna. Re-seat the USB cable and")
            print("  retry; if it persists the device may need a firmware reset.")
            self.ctx.wait_for_enter()
            return

        self._last_result = result
        self._print_sweep(result, label)
        self.ctx.wait_for_enter()

    def _print_sweep(self, result, label):
        swr, freq = result.min_swr
        lo, hi = result.frequency_range
        print(f"\n  {len(result.points)} points, {lo:.3f}-{hi:.3f} MHz")
        print(f"  Best match: {_nanovna.format_swr(swr)} at {freq:.3f} MHz")
        print(f"  Verdict:    {self._verdict(swr)}\n")

        print("     MHz        SWR     RL(dB)   Z (ohms)")
        print("  " + "-" * 46)
        for p in self._sample(result.points, 12):
            rl = p.return_loss_db
            rl_s = "  inf " if rl == float('inf') else f"{rl:6.1f}"
            print(f"  {p.frequency_mhz:9.3f}  {_nanovna.format_swr(p.swr):>7}  "
                  f"{rl_s}   {_nanovna.format_impedance(p.impedance)}")

    @staticmethod
    def _sample(points, n):
        """Evenly sample n points for display — never truncate to the first n,
        which would hide the top of the band."""
        if len(points) <= n:
            return points
        step = (len(points) - 1) / (n - 1)
        return [points[round(i * step)] for i in range(n)]

    @staticmethod
    def _verdict(swr):
        if swr == float('inf'):
            return "OPEN or SHORT — nothing resonant is connected"
        if swr <= SWR_GOOD:
            return f"good (<= {SWR_GOOD}:1)"
        if swr <= SWR_USABLE:
            return f"usable ({SWR_GOOD}-{SWR_USABLE}:1) — tune if you can"
        return f"POOR (> {SWR_USABLE}:1) — check connector, feedline, element"

    def _quick_swr(self):
        raw = self.ctx.dialog.inputbox(
            "Quick SWR", "Frequency in MHz (e.g. 915):", "915")
        if not raw:
            return
        try:
            freq_hz = int(float(raw) * 1e6)
        except (ValueError, TypeError):
            self.ctx.dialog.msgbox("Bad Frequency",
                                   f"Could not read '{raw}' as MHz.")
            return

        clear_screen()
        print(f"=== QUICK SWR @ {float(raw):g} MHz ===\n")
        dev, err = self._open_device()
        if dev is None:
            print(f"  {err}")
            self.ctx.wait_for_enter()
            return
        try:
            swr = dev.quick_swr(freq_hz)
        finally:
            dev.disconnect()

        if swr is None:
            print("  No reading — the sweep returned no points.")
            print("  UNKNOWN, not an all-clear.")
        else:
            print(f"  SWR: {_nanovna.format_swr(swr)}")
            print(f"  {self._verdict(swr)}")
        self.ctx.wait_for_enter()

    def _save_current(self):
        if not self._last_result:
            self.ctx.dialog.msgbox(
                "Nothing to Save",
                "Run 'Sweep a Band' first — only a full sweep can be saved as "
                "a baseline (Quick SWR measures too narrow a span to compare).")
            return
        name = self.ctx.dialog.inputbox(
            "Save Baseline",
            "Name this sweep — something you will recognise in a truck,\n"
            "e.g. kiai-whip-mounted or moc3-yagi-good:", "")
        if not name:
            return
        ok, detail = self._save_baseline(name, self._last_result)
        if ok:
            self.ctx.dialog.msgbox("Baseline Saved", f"Written to:\n{detail}")
        else:
            self.ctx.dialog.msgbox("Not Saved", f"Could not save:\n\n{detail}")

    def _pick_baseline(self, title):
        paths = self._list_baselines()
        if not paths:
            self.ctx.dialog.msgbox(
                "No Baselines",
                "Nothing saved yet. Sweep a known-good antenna and use\n"
                "'Save as Baseline' while you still trust it.")
            return None
        choices = [(p.stem, p.stem) for p in paths]
        choices.append(("back", "Back"))
        pick = self.ctx.dialog.menu(title, "Which saved sweep?", choices)
        if pick is None or pick == "back":
            return None
        return next((p for p in paths if p.stem == pick), None)

    def _compare(self):
        """The field check: today's antenna against a known-good sweep."""
        if not self._last_result:
            self.ctx.dialog.msgbox(
                "Sweep First",
                "Compare needs a fresh sweep in memory.\n\n"
                "Run 'Sweep a Band' on the antenna under test, then compare.")
            return
        path = self._pick_baseline("Compare to Baseline")
        if path is None:
            return
        base, err = self._load_baseline(path)
        if base is None:
            self.ctx.dialog.msgbox(
                "Baseline Unreadable",
                f"{path.name} could not be loaded:\n\n{err}")
            return

        clear_screen()
        print(f"=== COMPARE: now vs '{path.stem}' ===\n")
        now_swr, now_f = self._last_result.min_swr
        was_swr, was_f = base.min_swr
        age_d = (time.time() - base.timestamp) / 86400 if base.timestamp else None

        print(f"  baseline : {_nanovna.format_swr(was_swr)} at {was_f:.3f} MHz"
              + (f"   ({age_d:.1f} days ago)" if age_d is not None else ""))
        print(f"  now      : {_nanovna.format_swr(now_swr)} at {now_f:.3f} MHz")

        lo_n, hi_n = self._last_result.frequency_range
        lo_b, hi_b = base.frequency_range
        if abs(lo_n - lo_b) > 0.5 or abs(hi_n - hi_b) > 0.5:
            print(f"\n  ⚠ DIFFERENT SPANS — baseline {lo_b:.1f}-{hi_b:.1f} MHz,"
                  f" now {lo_n:.1f}-{hi_n:.1f} MHz.")
            print("    These are not comparable. Re-sweep the same band.")
            self.ctx.wait_for_enter()
            return

        print()
        if now_swr == float('inf') and was_swr != float('inf'):
            print("  VERDICT: the antenna reads OPEN/SHORT and did not before.")
            print("           Check the connector and feedline FIRST.")
        else:
            d_swr = now_swr - was_swr
            d_f = now_f - was_f
            print(f"  delta SWR      : {d_swr:+.2f}")
            print(f"  delta best-match: {d_f:+.3f} MHz")
            print()
            if abs(d_swr) < 0.2 and abs(d_f) < 1.0:
                print("  VERDICT: unchanged — this is the antenna you saved.")
            elif d_swr > 0.5:
                print("  VERDICT: WORSE. Water in the connector, a cracked")
                print("           element, or a loose ground are the usual three.")
            elif d_f < -2.0:
                print("  VERDICT: resonance moved DOWN — often added length,")
                print("           ice/water loading, or a nearby metal object.")
            elif d_f > 2.0:
                print("  VERDICT: resonance moved UP — often a shortened or")
                print("           broken element.")
            else:
                print("  VERDICT: changed, but within a band you may not care")
                print("           about. Read the deltas and judge.")

        print("\n  (A comparison is only as good as the baseline. If the")
        print("   baseline was taken on a different mount or ground plane,")
        print("   the difference is the install, not the antenna.)")
        self.ctx.wait_for_enter()

    def _manage_baselines(self):
        while True:
            paths = self._list_baselines()
            if not paths:
                self.ctx.dialog.msgbox("No Baselines", "Nothing saved yet.")
                return
            choices = [(p.stem, f"{p.stem}") for p in paths]
            choices.append(("back", "Back"))
            pick = self.ctx.dialog.menu(
                "Saved Baselines",
                f"{len(paths)} saved in ~/.local/share/meshforge/vna/\n"
                "Select one to view or delete:", choices)
            if pick is None or pick == "back":
                return
            path = next((p for p in paths if p.stem == pick), None)
            if path is None:
                continue
            result, err = self._load_baseline(path)
            if result is None:
                self.ctx.dialog.msgbox("Unreadable", f"{path.name}: {err}")
                continue
            swr, freq = result.min_swr
            lo, hi = result.frequency_range
            if self.ctx.dialog.yesno(
                    f"Baseline: {path.stem}",
                    f"{len(result.points)} points, {lo:.3f}-{hi:.3f} MHz\n"
                    f"Best match {_nanovna.format_swr(swr)} at {freq:.3f} MHz\n"
                    f"Device: {result.device_info or 'unknown'}\n\n"
                    f"Delete this baseline?", default_no=True):
                try:
                    path.unlink()
                    self.ctx.dialog.msgbox("Deleted", f"{path.name} removed.")
                except OSError as e:
                    self.ctx.dialog.msgbox(
                        "Not Deleted", f"Could not remove {path.name}:\n\n{e}")
