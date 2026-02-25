"""
Quick Actions Mixin — single-key shortcuts for common NOC operations.

Provides a fast-access menu where each entry maps to a common
operation, avoiding deep menu navigation for frequent tasks.

Quick Actions:
    s - Service status (all services at a glance)
    n - Node list (meshtastic --nodes)
    i - Node inventory (tracked nodes)
    G - GPS position / distance to nodes
    l - Follow logs (journalctl meshtasticd)
    r - Restart meshtasticd
    R - Restart rnsd
    p - Port/network check
    g - Generate status report
    d - Run diagnostics
    c - Channel activity scan
"""

import sys
import subprocess
import logging
from pathlib import Path
from backend import clear_screen

logger = logging.getLogger(__name__)

# Centralized service checking — first-party, always available
from utils.service_check import (
    check_systemd_service, check_process_running, check_port, check_udp_port,
    check_rns_shared_instance,
    apply_config_and_restart, restart_service, _sudo_cmd,
)

# First-party modules for quick actions
from utils.report_generator import generate_report
from utils.node_inventory import NodeInventory
from utils.gps_integration import GPSManager
from utils.diagnostic_engine import get_diagnostic_engine
from utils.channel_scan import ChannelMonitor


# Quick action definitions: (tag, description, method_name)
QUICK_ACTIONS = [
    ('s', 'Service status overview', '_qa_service_status'),
    ('n', 'Node list (meshtastic --nodes)', '_qa_node_list'),
    ('i', 'Node inventory (tracked nodes)', '_qa_node_inventory'),
    ('G', 'GPS position / distance to nodes', '_qa_gps_position'),
    ('l', 'Follow logs (meshtasticd)', '_qa_follow_logs'),
    ('r', 'Restart meshtasticd', '_qa_restart_meshtasticd'),
    ('R', 'Restart rnsd', '_qa_restart_rnsd'),
    ('p', 'Port / network check', '_qa_port_check'),
    ('g', 'Generate status report', '_qa_generate_report'),
    ('d', 'Run diagnostics', '_qa_run_diagnostics'),
    ('c', 'Channel activity scan', '_qa_channel_scan'),
]


class QuickActionsMixin:
    """Single-key shortcuts for frequent NOC operations."""

    def _quick_actions_menu(self):
        """Display quick actions menu with single-key shortcuts."""
        while True:
            choices = [(tag, desc) for tag, desc, _ in QUICK_ACTIONS]
            choices.append(('b', 'Back to main menu'))

            choice = self.dialog.menu(
                "Quick Actions",
                "Single-key shortcuts (press letter to select):",
                choices
            )

            if choice is None or choice == 'b':
                break

            # Find and execute the matching action via _safe_call
            for tag, desc, method_name in QUICK_ACTIONS:
                if choice == tag:
                    method = getattr(self, method_name, None)
                    if method:
                        self._safe_call(desc, method)
                    break

    def _qa_service_status(self):
        """Quick: show all service statuses.

        Uses centralized service_check module when available.
        """
        clear_screen()
        print("=== Quick Service Status ===\n")

        services = ['meshtasticd', 'rnsd', 'mosquitto', 'meshforge']
        warnings = []
        for svc in services:
            # MeshForge TUI IS MeshForge — if we're running, it's running.
            if svc == 'meshforge':
                is_systemd = False
                try:
                    is_running, _ = check_systemd_service(svc)
                    is_systemd = is_running
                except Exception:
                    pass
                mode = "service" if is_systemd else "interactive"
                print(f"  * {svc:<18} running ({mode})")
                continue

            try:
                is_running, is_enabled = check_systemd_service(svc)
                status = 'active' if is_running else 'inactive'

                # Check boot persistence
                boot_info = ""
                if status == 'active' and not is_enabled:
                    boot_info = "  (not enabled at boot)"
                    warnings.append(svc)

                if status == 'active':
                    # rnsd zombie detection: systemd active but shared instance not available
                    if svc == 'rnsd' and not check_rns_shared_instance():
                        print(f"  ! {svc:<18} running (shared instance not available)")
                    else:
                        print(f"  * {svc:<18} running{boot_info}")
                elif status == 'failed':
                    print(f"  ! {svc:<18} FAILED")
                else:
                    print(f"  - {svc:<18} {status}")
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("Service status check for %s failed: %s", svc, e)
                print(f"  ? {svc:<18} unknown")

        # Bridge process (not a systemd service — no boot persistence check)
        try:
            bridge_running = check_process_running('rns_bridge')
            bridge_status = "running" if bridge_running else "not running"
            sym = "*" if bridge_running else "-"
            print(f"  {sym} {'rns_bridge':<18} {bridge_status}")
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Bridge process check failed: %s", e)
            print(f"  ? {'rns_bridge':<18} unknown")

        # Surface actionable warning for services that won't survive reboot
        if warnings:
            print(f"\n  Warning: {', '.join(warnings)} running but won't start on reboot.")
            print(f"  Fix: sudo systemctl enable {' '.join(warnings)}")

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _qa_node_list(self):
        """Quick: show meshtastic node list."""
        clear_screen()
        print("=== Node List ===\n")
        try:
            cli_path = self._get_meshtastic_cli()
            subprocess.run(
                [cli_path, '--nodes'],
                timeout=30
            )
        except FileNotFoundError:
            print("Error: 'meshtastic' CLI not installed.")
            print("Install with: pipx install meshtastic[cli]")
        except subprocess.TimeoutExpired:
            print("Error: Command timed out. Is meshtasticd running?")
        except Exception as e:
            print(f"Error: {e}")
        print()
        self._wait_for_enter("Press Enter to continue...")

    def _qa_follow_logs(self):
        """Quick: follow meshtasticd journal logs."""
        clear_screen()
        print("=== meshtasticd Logs (Ctrl+C to stop, auto-exits after 2 min) ===\n")
        try:
            subprocess.run(
                ['journalctl', '-fu', 'meshtasticd', '--no-pager', '-n', '50'],
                timeout=120
            )
        except subprocess.TimeoutExpired:
            pass  # Normal exit after timeout
        except KeyboardInterrupt:
            pass  # User pressed Ctrl+C
        except Exception as e:
            print(f"Error: {e}")
            self._wait_for_enter("Press Enter to continue...")

    def _qa_restart_meshtasticd(self):
        """Quick: restart meshtasticd service."""
        clear_screen()
        print("Restarting meshtasticd...\n")
        try:
            success, msg = apply_config_and_restart('meshtasticd')
            print(msg)
            subprocess.run(
                ['systemctl', 'status', 'meshtasticd', '--no-pager', '-l'],
                timeout=10
            )
        except Exception as e:
            print(f"Error: {e}")

        # Invalidate status bar cache
        if hasattr(self, '_status_bar') and self._status_bar:
            self._status_bar.invalidate()

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _qa_restart_rnsd(self):
        """Quick: restart rnsd service."""
        clear_screen()
        print("Restarting rnsd...\n")
        try:
            success, msg = restart_service('rnsd')
            print(msg)
            subprocess.run(
                ['systemctl', 'status', 'rnsd', '--no-pager', '-l'],
                timeout=10
            )
        except Exception as e:
            print(f"Error: {e}")

        # Invalidate status bar cache
        if hasattr(self, '_status_bar') and self._status_bar:
            self._status_bar.invalidate()

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _qa_port_check(self):
        """Quick: check network ports."""
        clear_screen()
        print("=== Port Check ===\n")

        ports = [
            (4403, 'meshtasticd TCP API'),
            (9443, 'meshtasticd Web Client'),
            (37428, 'rnsd (RNS shared instance)'),
            (1883, 'MQTT broker'),
        ]

        # RNS uses abstract domain sockets on Linux (not UDP/TCP port)
        _rns_port = 37428

        for port, desc in ports:
            try:
                if port == _rns_port:
                    port_open = check_rns_shared_instance()
                else:
                    port_open = check_port(port, host='127.0.0.1', timeout=1.0)

                if port_open:
                    print(f"  * {port:<6} {desc}")
                else:
                    print(f"  - {port:<6} {desc} (not listening)")
            except (OSError, ValueError) as e:
                logger.debug("Port %d check failed: %s", port, e)
                print(f"  ? {port:<6} {desc} (check failed)")

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _qa_generate_report(self):
        """Quick: generate and display a status report."""
        clear_screen()
        print("Generating status report...\n")

        try:
            report = generate_report()
            # Print report, paginated
            lines = report.split('\n')
            for i, line in enumerate(lines):
                print(line)
                # Pause every 40 lines
                if (i + 1) % 40 == 0 and i + 1 < len(lines):
                    try:
                        resp = input("\n--- More (Enter=continue, q=quit) ---\n")
                    except (KeyboardInterrupt, EOFError):
                        print()
                        break
                    if resp.strip().lower() == 'q':
                        break
        except Exception as e:
            print(f"Error generating report: {e}")

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _qa_node_inventory(self):
        """Quick: show tracked node inventory."""
        clear_screen()
        print("=== Node Inventory ===\n")

        try:
            from utils.paths import get_real_user_home

            path = get_real_user_home() / ".config" / "meshforge" / "node_inventory.json"
            inv = NodeInventory(path=path)

            stats = inv.get_stats()
            print(f"  Total nodes:    {stats['total']}")
            print(f"  Online:         {stats['online']}")
            print(f"  Offline:        {stats['offline']}")
            print(f"  Stale (>7d):    {stats['stale']}")
            print(f"  With position:  {stats['with_position']}")

            if stats['total'] > 0:
                # Show node list (non-stale only)
                nodes = [n for n in inv.get_all_nodes() if not n.is_stale]
                if nodes:
                    print(f"\n  {'ID':<12} {'Name':<20} {'Status':<8} {'SNR':>5}  {'Hardware'}")
                    print(f"  {'-'*12} {'-'*20} {'-'*8} {'-'*5}  {'-'*12}")
                    for node in nodes[:25]:  # Cap at 25 for readability
                        name = node.display_name[:20]
                        nid = node.node_id[:12]
                        status = node.status
                        snr = f"{node.last_snr:.1f}" if node.last_snr is not None else "  -"
                        hw = node.hardware[:12] if node.hardware else "-"
                        print(f"  {nid:<12} {name:<20} {status:<8} {snr:>5}  {hw}")
                    if len(nodes) > 25:
                        print(f"\n  ... and {len(nodes) - 25} more nodes")

                # Role breakdown
                if stats['roles']:
                    roles_str = ", ".join(f"{r}: {c}" for r, c in stats['roles'].items())
                    print(f"\n  Roles: {roles_str}")
            else:
                print("\n  No nodes tracked yet.")
                print("  Nodes are added when received via MQTT or meshtastic CLI.")

        except Exception as e:
            logger.debug(f"Node inventory quick action failed: {e}")
            print(f"Error: {e}")

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _qa_gps_position(self):
        """Quick: show GPS position and distance to nodes."""
        clear_screen()
        print("=== GPS Position ===\n")

        try:
            from utils.paths import get_real_user_home

            config_path = get_real_user_home() / ".config" / "meshforge" / "operator_position.json"
            gps = GPSManager(config_path=config_path)

            # Try to get nodes for distance calculation
            nodes = []
            try:
                from utils.node_inventory import NodeInventory as _NodeInv
                inv_path = get_real_user_home() / ".config" / "meshforge" / "node_inventory.json"
                inv = _NodeInv(path=inv_path)
                for node in inv.get_all_nodes():
                    if node.has_position:
                        nodes.append({
                            'id': node.node_id,
                            'name': node.display_name,
                            'lat': node.lat,
                            'lon': node.lon,
                        })
            except Exception as e:
                logger.debug("Node inventory for GPS report unavailable: %s", e)

            # Display position report
            report = gps.format_position_report(nodes=nodes if nodes else None)
            print(f"  {report.replace(chr(10), chr(10) + '  ')}")

            # Show gpsd status
            print()
            if gps.gpsd_available:
                print("  gpsd: connected")
            else:
                print("  gpsd: not available")
                if not gps.has_position:
                    print("  Tip: Set position manually in Tools > GPS")

        except Exception as e:
            logger.debug(f"GPS quick action failed: {e}")
            print(f"Error: {e}")

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _qa_run_diagnostics(self):
        """Quick: run diagnostic engine health check."""
        clear_screen()
        print("=== Diagnostic Health Check ===\n")

        try:
            engine = get_diagnostic_engine()
            summary = engine.get_health_summary()

            print(f"  Overall Health:    {summary['overall_health']}")
            print(f"  Symptoms (1h):     {summary['symptoms_last_hour']}")
            print(f"  Total Diagnoses:   {summary['stats'].get('diagnoses_made', 0)}")
            print(f"  Auto-Recoveries:   {summary['stats'].get('auto_recoveries', 0)}")
            print(f"  Rules Loaded:      {summary['stats'].get('rules_loaded', 0)}")

            # Recent issues
            recent = engine.get_recent_diagnoses(limit=5)
            if recent:
                print(f"\n  Recent Issues ({len(recent)}):")
                for d in recent[-5:]:
                    cat = d.symptom.category.value
                    print(f"    [{cat}] {d.likely_cause[:60]}")
            else:
                print("\n  No recent issues detected.")

        except Exception as e:
            print(f"Error: {e}")

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _qa_channel_scan(self):
        """Quick: show channel activity scan."""
        clear_screen()
        print("=== Channel Activity ===\n")

        try:
            monitor = ChannelMonitor()

            # Try to query device for channel config
            channels = monitor.query_device_channels()
            if not channels:
                print("  (Could not query device channels)")
                print("  Showing activity from MQTT monitoring only.")
                print()

            # Display activity report
            report = monitor.get_activity_report()
            print(f"  {report.replace(chr(10), chr(10) + '  ')}")

        except Exception as e:
            logger.debug(f"Channel scan quick action failed: {e}")
            print(f"Error: {e}")

        print()
        self._wait_for_enter("Press Enter to continue...")
