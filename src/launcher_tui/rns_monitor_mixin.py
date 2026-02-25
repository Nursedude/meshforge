"""
RNS Monitor Mixin - Live RNS status monitoring with auto-refresh.

Extracted as its own mixin per CLAUDE.md file size guidelines.
Provides a clear-screen + loop pattern similar to the log viewer,
with ANSI color-coded interface status display.
"""

import logging
import subprocess
import time

from backend import clear_screen
from utils.rns_status_parser import (
    run_rnstatus,
    InterfaceStatus,
    InterfaceMode,
    RNSStatus,
)
from utils.safe_import import safe_import

check_service, check_udp_port, check_rns_shared_instance, start_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_service', 'check_udp_port', 'check_rns_shared_instance', 'start_service',
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI colors (matching dashboard_mixin.py patterns)
# ---------------------------------------------------------------------------
_GREEN = "\033[0;32m"
_RED = "\033[0;31m"
_YELLOW = "\033[0;33m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _color_status(status: InterfaceStatus) -> str:
    """Return colored status indicator."""
    if status == InterfaceStatus.UP:
        return f"{_GREEN}\u25cf Up{_RESET}"
    elif status == InterfaceStatus.DOWN:
        return f"{_RED}\u25cf Down{_RESET}"
    return f"{_DIM}\u25cf ???{_RESET}"


def _color_dot(ok: bool) -> str:
    """Return green or red dot."""
    if ok:
        return f"{_GREEN}\u25cf{_RESET}"
    return f"{_RED}\u25cf{_RESET}"


def _truncate(text: str, width: int) -> str:
    """Truncate text to width with ellipsis if needed."""
    if len(text) <= width:
        return text
    return text[:width - 1] + "\u2026"


class RNSMonitorMixin:
    """Mixin providing live RNS status monitoring."""

    def _rns_status_monitor(self):
        """Entry point — select refresh interval and start the monitor."""
        choices = [
            ("5", "5 seconds (default)"),
            ("3", "3 seconds (fast)"),
            ("10", "10 seconds"),
            ("30", "30 seconds (low overhead)"),
        ]

        choice = self.dialog.menu(
            "Monitor Refresh Rate",
            "Select auto-refresh interval:",
            choices,
        )

        if choice is None:
            return

        try:
            interval = int(choice)
        except (ValueError, TypeError):
            interval = 5

        self._rns_monitor_loop(interval)

    def _rns_monitor_loop(self, interval: int):
        """Live display loop — clears screen and redraws on each cycle.

        Args:
            interval: Seconds between refreshes.
        """
        last_good = None  # type: Optional[RNSStatus]
        rnsd_was_failed = False

        try:
            while True:
                # Fetch fresh status
                status = run_rnstatus()
                service_state = self._check_rnsd_service_state()

                # Track last good status for stale display
                if status.parse_error is None and status.interfaces:
                    last_good = status

                # Track if rnsd was in failed state
                if service_state.get('systemd_state') == 'failed':
                    rnsd_was_failed = True

                # Render
                clear_screen()
                self._render_monitor_display(
                    status, service_state, last_good, interval,
                )

                # Countdown display
                for remaining in range(interval, 0, -1):
                    # Move cursor to last line and update countdown
                    print(
                        f"\r{_DIM}Ctrl+C to exit | "
                        f"Next refresh in {remaining}s{_RESET}  ",
                        end="", flush=True,
                    )
                    time.sleep(1)

        except KeyboardInterrupt:
            pass

        print()  # Clean line after Ctrl+C

        # Offer restart if rnsd was in failed state
        if rnsd_was_failed and _HAS_SERVICE_CHECK:
            if self.dialog.yesno(
                "rnsd Failed",
                "rnsd was in FAILED state during monitoring.\n\n"
                "Restart rnsd now?",
            ):
                success, msg = start_service('rnsd')
                if success:
                    print("rnsd restarted.")
                else:
                    print(f"Failed to restart rnsd: {msg}")

        self._wait_for_enter()

    def _check_rnsd_service_state(self) -> dict:
        """Check rnsd service health via service_check.py.

        Returns:
            Dict with keys: systemd_state, port_bound, pid
        """
        result = {
            'systemd_state': 'unknown',
            'port_bound': False,
            'pid': None,
        }

        if not _HAS_SERVICE_CHECK:
            return result

        try:
            svc = check_service('rnsd')
            result['systemd_state'] = svc.state.value
        except Exception as e:
            logger.debug("check_service('rnsd') failed: %s", e)

        try:
            result['port_bound'] = check_rns_shared_instance()
        except Exception as e:
            logger.debug("check_rns_shared_instance failed: %s", e)

        # Get PID via pgrep
        try:
            proc = subprocess.run(
                ['pgrep', '-f', 'rnsd'],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                pids = proc.stdout.strip().split('\n')
                result['pid'] = pids[0]
        except (subprocess.SubprocessError, OSError):
            pass

        return result

    def _render_monitor_display(
        self,
        status: RNSStatus,
        service: dict,
        last_good,  # Optional[RNSStatus]
        interval: int,
    ):
        """Render the ANSI-colored monitor display.

        Args:
            status: Current parsed rnstatus output.
            service: Service state dict from _check_rnsd_service_state.
            last_good: Last successful status (for stale display).
            interval: Current refresh interval.
        """
        # Header
        print(f"{_BOLD}=== RNS Live Monitor ==={_RESET}"
              f"                         {_DIM}refresh: {interval}s{_RESET}")
        print()

        # --- Service Health ---
        print(f"{_BOLD}SERVICE{_RESET}")

        # rnsd systemd state
        sd_state = service.get('systemd_state', 'unknown')
        pid = service.get('pid')
        port_bound = service.get('port_bound', False)

        if sd_state == 'available':
            pid_str = f" (PID {pid})" if pid else ""
            print(f"  rnsd:      {_color_dot(True)} active{pid_str}")
        elif sd_state == 'failed':
            print(f"  rnsd:      {_color_dot(False)} FAILED")
        elif sd_state == 'not_running':
            print(f"  rnsd:      {_DIM}\u25cb stopped{_RESET}")
        else:
            print(f"  rnsd:      {_DIM}\u25cf {sd_state}{_RESET}")

        # Port 37428
        print(f"  Port 37428: {_color_dot(port_bound)}"
              f" {'bound' if port_bound else 'NOT bound'}")

        # Surface recent errors when rnsd is active but port not bound
        if sd_state == 'available' and not port_bound:
            try:
                r = subprocess.run(
                    ['journalctl', '-u', 'rnsd', '-n', '5', '--no-pager',
                     '-p', 'err', '-q', '--no-hostname'],
                    capture_output=True, text=True, timeout=5,
                )
                if r.stdout and r.stdout.strip():
                    print(f"  {_YELLOW}Recent errors:{_RESET}")
                    for line in r.stdout.strip().splitlines()[:3]:
                        display = line.strip()[:80]
                        print(f"    {_DIM}{display}{_RESET}")
            except (subprocess.SubprocessError, OSError):
                pass

        # Transport
        if status.transport.running:
            print(f"  Transport: {_color_dot(True)} running"
                  f"    Uptime: {status.transport.uptime_str}")
        elif status.parse_error:
            print(f"  Transport: {_DIM}\u25cf unavailable{_RESET}")
        else:
            print(f"  Transport: {_color_dot(False)} not running")

        print()

        # --- Error banner ---
        if status.parse_error:
            print(f"  {_RED}ERROR: rnsd not responding{_RESET}")
            # Show concise error
            err_line = status.parse_error.split('\n')[0][:70]
            print(f"  {_DIM}{err_line}{_RESET}")

            if last_good and last_good.interfaces:
                print(f"\n  {_YELLOW}Showing last known state (STALE):{_RESET}")
                self._render_interface_table(last_good)
            else:
                print(f"\n  {_DIM}No previous status available.{_RESET}")
                print(f"  {_DIM}Check: sudo journalctl -u rnsd -n 20{_RESET}")
            print()
            return

        # --- Interface Table ---
        self._render_interface_table(status)

        # --- Warnings ---
        warnings = []

        rx_only = status.rx_only_interfaces
        if rx_only:
            for iface in rx_only:
                warnings.append(
                    f"  {_YELLOW}! {iface.full_name}: "
                    f"RX-only (link establishment failing){_RESET}"
                )

        zero = status.zero_traffic_interfaces
        if zero:
            for iface in zero:
                # Skip Shared Instance — it often has zero traffic
                if iface.type_name == "Shared Instance":
                    continue
                warnings.append(
                    f"  {_YELLOW}! {iface.full_name}: "
                    f"zero traffic{_RESET}"
                )

        if status.any_down:
            for iface in status.interfaces:
                if iface.status == InterfaceStatus.DOWN:
                    warnings.append(
                        f"  {_RED}! {iface.full_name}: DOWN{_RESET}"
                    )

        if warnings:
            print()
            print(f"{_BOLD}WARNINGS{_RESET}")
            for w in warnings:
                print(w)

        print()

    def _render_interface_table(self, status: RNSStatus):
        """Render the interface table portion of the display."""
        count = len(status.interfaces)
        print(f"{_BOLD}INTERFACES ({count}){_RESET}")

        if not status.interfaces:
            print(f"  {_DIM}No interfaces found{_RESET}")
            return

        # Header row
        print(f"  {'Name':<38} {'Status':<10} {'Rate':<12} "
              f"{'TX':<12} {'RX':<12}")
        print(f"  {'-' * 38} {'-' * 10} {'-' * 12} {'-' * 12} {'-' * 12}")

        for iface in status.interfaces:
            name = _truncate(iface.full_name, 38)

            # Status with color
            status_str = _color_status(iface.status)

            # Rate
            rate = _truncate(iface.rate, 12) if iface.rate else ""

            # Traffic
            tx_str = f"\u2191{iface.tx.bytes_total:.0f} {iface.tx.bytes_unit}"
            rx_str = f"\u2193{iface.rx.bytes_total:.0f} {iface.rx.bytes_unit}"

            # Add bps if non-zero
            if iface.tx.bps > 0:
                tx_str += f" {iface.tx.bps:.0f}{iface.tx.bps_unit}"
            if iface.rx.bps > 0:
                rx_str += f" {iface.rx.bps:.0f}{iface.rx.bps_unit}"

            tx_str = _truncate(tx_str, 12)
            rx_str = _truncate(rx_str, 12)

            # Highlight unhealthy rows
            row_prefix = ""
            row_suffix = ""
            if iface.is_rx_only:
                row_prefix = _YELLOW
                row_suffix = _RESET
            elif iface.status == InterfaceStatus.DOWN:
                row_prefix = _RED
                row_suffix = _RESET

            # The status_str already contains ANSI codes which affect alignment.
            # Print name separately to control alignment.
            print(
                f"  {row_prefix}{name:<38}{row_suffix} "
                f"{status_str:<20} "
                f"{row_prefix}{rate:<12} {tx_str:<12} {rx_str:<12}{row_suffix}"
            )

            # Extra info line for AutoInterface peers or Shared Instance serving
            if iface.peers is not None:
                print(f"  {_DIM}{'':38}   Peers: {iface.peers}{_RESET}")
            if iface.serving is not None:
                print(f"  {_DIM}{'':38}   Serving: {iface.serving} program(s){_RESET}")
