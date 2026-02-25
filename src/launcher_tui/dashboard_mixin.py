"""
Dashboard Mixin — Service status, node counts, data path diagnostics, alerts.

Extracted from main.py to keep file size under 1,500 lines.
Provides the display methods used by the Dashboard submenu.
"""

import logging
import subprocess
from backend import clear_screen
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# --- Module-level safe imports (replaces try/except ImportError blocks) ---
ServiceRunState, _HAS_STARTUP_CHECKS = safe_import('startup_checks', 'ServiceRunState')
get_http_client, reset_http_client, _HAS_MESHTASTIC_HTTP = safe_import(
    'utils.meshtastic_http', 'get_http_client', 'reset_http_client'
)
from utils.map_data_collector import MapDataCollector
generate_report, generate_and_save, _HAS_REPORT_GEN = safe_import(
    'utils.report_generator', 'generate_report', 'generate_and_save'
)
from utils.health_score import get_health_scorer
from plugins.eas_alerts import EASAlertsPlugin
pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')


class DashboardMixin:
    """TUI mixin for dashboard display methods."""

    @staticmethod
    def _check_webserver_config() -> str:
        """Check if meshtasticd config.yaml has Webserver section enabled."""
        from pathlib import Path
        config_path = Path("/etc/meshtasticd/config.yaml")
        if not config_path.exists():
            return "Fix: /etc/meshtasticd/config.yaml not found"
        try:
            content = config_path.read_text()
            if 'Webserver:' not in content:
                return "Fix: Add 'Webserver: Port: 443' to /etc/meshtasticd/config.yaml"
            # Webserver section exists but might be commented out
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue
                if 'Webserver:' in stripped:
                    return "Config has Webserver section — check meshtasticd logs"
            return "Fix: Webserver section may be commented out in config.yaml"
        except PermissionError:
            return "Cannot read config (try running with sudo)"
        except Exception:
            return "Fix: Check Webserver section in /etc/meshtasticd/config.yaml"

    def _service_status_display(self):
        """Show comprehensive service status."""
        clear_screen()
        print("=== Service Status ===\n")

        if self._env_state and _HAS_STARTUP_CHECKS:
            for name, info in self._env_state.services.items():
                if info.state == ServiceRunState.RUNNING:
                    print(f"  \033[0;32m●\033[0m {name:<18} running")
                elif info.state == ServiceRunState.FAILED:
                    print(f"  \033[0;31m●\033[0m {name:<18} FAILED")
                else:
                    print(f"  \033[2m○\033[0m {name:<18} stopped")
        else:
            # Fallback to systemctl
            for svc in ['meshtasticd', 'rnsd', 'mosquitto']:
                try:
                    result = subprocess.run(
                        ['systemctl', 'is-active', svc],
                        capture_output=True, text=True, timeout=5
                    )
                    status = result.stdout.strip()
                    if status == 'active':
                        print(f"  \033[0;32m●\033[0m {svc:<18} running")
                    else:
                        print(f"  \033[2m○\033[0m {svc:<18} {status}")
                except Exception:
                    print(f"  ? {svc:<18} unknown")

        print()
        self._wait_for_enter()

    def _show_node_counts(self):
        """Show node counts from all sources."""
        clear_screen()
        print("=== Node Counts ===\n")

        # Meshtastic nodes via HTTP API
        if not _HAS_MESHTASTIC_HTTP:
            print("  Meshtastic: meshtastic_http module not available")
        else:
            try:
                client = get_http_client()
                if client.is_available:
                    nodes = client.get_nodes()
                    print(f"  Meshtastic nodes: {len(nodes)}")
                else:
                    print("  Meshtastic: HTTP API unavailable")
            except Exception as e:
                print(f"  Meshtastic: unavailable ({e})")

        # RNS destinations
        try:
            result = subprocess.run(
                ['rnstatus', '-a'],
                capture_output=True, text=True, timeout=10
            )
            # Count lines that look like destinations
            dest_count = len([line for line in result.stdout.splitlines()
                             if line.strip().startswith('<')])
            print(f"  RNS destinations: {dest_count}")
        except Exception:
            print("  RNS: unavailable")

        print()
        self._wait_for_enter()

    def _data_path_diagnostic(self):
        """Test all data collection paths to diagnose zero-data issues."""
        clear_screen()
        print("=== Data Path Diagnostic ===\n")
        print("Testing all data sources...\n")

        results = []

        # Test 1: meshtasticd TCP connection
        print("[1/6] Testing meshtasticd TCP (port 4403)...")
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('localhost', 4403))
            sock.close()
            if result == 0:
                results.append(("meshtasticd TCP", "OK", "Port 4403 accepting connections"))
                print("      \033[0;32mOK\033[0m - Port 4403 reachable")
            else:
                results.append(("meshtasticd TCP", "FAIL", f"Connection refused (code {result})"))
                print("      \033[0;31mFAIL\033[0m - Connection refused")
        except Exception as e:
            results.append(("meshtasticd TCP", "FAIL", str(e)))
            print(f"      \033[0;31mFAIL\033[0m - {e}")

        # Test 2: meshtastic CLI node count
        print("[2/6] Testing meshtastic CLI...")
        try:
            result = subprocess.run(
                ['meshtastic', '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                node_lines = [line for line in result.stdout.split('\n')
                             if 'Node' in line or '!' in line]
                results.append(("meshtastic CLI", "OK", f"Responded, ~{len(node_lines)} node refs"))
                print("      \033[0;32mOK\033[0m - CLI responded")
            else:
                results.append(("meshtastic CLI", "WARN",
                               result.stderr[:50] if result.stderr else "No output"))
                print("      \033[0;33mWARN\033[0m - Non-zero exit")
        except FileNotFoundError:
            results.append(("meshtastic CLI", "SKIP", "CLI not installed"))
            print("      \033[0;33mSKIP\033[0m - CLI not found")
        except subprocess.TimeoutExpired:
            results.append(("meshtastic CLI", "FAIL", "Timeout after 15s"))
            print("      \033[0;31mFAIL\033[0m - Timeout")
        except Exception as e:
            results.append(("meshtastic CLI", "FAIL", str(e)[:50]))
            print(f"      \033[0;31mFAIL\033[0m - {e}")

        # Test 3: meshtasticd HTTP API
        print("[3/6] Testing meshtasticd HTTP API...")
        if not _HAS_MESHTASTIC_HTTP:
            results.append(("meshtasticd HTTP", "SKIP", "meshtastic_http module not available"))
            print("      \033[0;33mSKIP\033[0m - Module not available")
        else:
            try:
                # Reset singleton so we get a fresh probe (not stale cached state)
                reset_http_client()
                client = get_http_client()
                if client.is_available:
                    nodes = client.get_nodes()
                    results.append(("meshtasticd HTTP", "OK",
                                   f"{len(nodes)} nodes via {client._base_url}"))
                    print(f"      \033[0;32mOK\033[0m - {len(nodes)} nodes at {client._base_url}")
                else:
                    # Provide actionable fix guidance
                    hint = self._check_webserver_config()
                    detail = f"Not reachable (tried ports 9443,443,80,4403). {hint}"
                    results.append(("meshtasticd HTTP", "FAIL", detail))
                    print(f"      \033[0;31mFAIL\033[0m - HTTP API not reachable")
                    print(f"      \033[2m{hint}\033[0m")
            except Exception as e:
                err_msg = str(e)[:50]
                results.append(("meshtasticd HTTP", "FAIL", err_msg))
                print(f"      \033[0;31mFAIL\033[0m - {err_msg}")

        # Test 4: pubsub availability
        print("[4/6] Testing pubsub (for live capture)...")
        if not _HAS_PUBSUB:
            results.append(("pubsub", "SKIP", "pubsub module not installed"))
            print("      \033[0;33mSKIP\033[0m - Module not installed")
        else:
            try:
                listeners = pub.getDefaultTopicMgr().getTopic('meshtastic.receive', okIfNone=True)
                if listeners:
                    count = len(list(listeners.getListeners()))
                    results.append(("pubsub", "OK", f"{count} listener(s) on meshtastic.receive"))
                    print(f"      \033[0;32mOK\033[0m - {count} listener(s) registered")
                else:
                    results.append(("pubsub", "WARN", "Topic exists but no listeners"))
                    print("      \033[0;33mWARN\033[0m - No listeners registered")
            except Exception as e:
                results.append(("pubsub", "WARN", str(e)[:50]))
                print(f"      \033[0;33mWARN\033[0m - {e}")

        # Test 5: MapDataCollector
        print("[5/6] Testing MapDataCollector...")
        try:
            collector = MapDataCollector(enable_history=False)
            geojson = collector.collect(max_age_seconds=30)
            props = geojson.get('properties', {})
            total = props.get('total_nodes', 0)
            with_gps = props.get('nodes_with_position', 0)
            sources = props.get('sources', {})
            active_sources = [k for k, v in sources.items() if isinstance(v, (int, float)) and v > 0]
            if total > 0:
                results.append(("MapDataCollector", "OK", f"{total} nodes ({with_gps} with GPS)"))
                print(f"      \033[0;32mOK\033[0m - {total} nodes, sources: {active_sources}")
            else:
                results.append(("MapDataCollector", "WARN", "0 nodes returned"))
                print("      \033[0;33mWARN\033[0m - 0 nodes (check meshtasticd connection)")
        except Exception as e:
            results.append(("MapDataCollector", "FAIL", str(e)[:50]))
            print(f"      \033[0;31mFAIL\033[0m - {e}")

        # Test 6: RNS path table
        print("[6/6] Testing RNS path table...")
        try:
            result = subprocess.run(
                ['rnpath', '-t'],
                capture_output=True, text=True, timeout=10
            )
            lines = [line for line in result.stdout.splitlines()
                     if line.strip() and not line.startswith('Path')]
            path_count = len(lines)
            if path_count > 0:
                results.append(("RNS paths", "OK", f"{path_count} known paths"))
                print(f"      \033[0;32mOK\033[0m - {path_count} paths in table")
            else:
                results.append(("RNS paths", "WARN", "Path table empty"))
                print("      \033[0;33mWARN\033[0m - No paths (normal if no RNS traffic yet)")
        except FileNotFoundError:
            results.append(("RNS paths", "SKIP", "rnpath not installed"))
            print("      \033[0;33mSKIP\033[0m - rnpath not found")
        except Exception as e:
            results.append(("RNS paths", "WARN", str(e)[:50]))
            print(f"      \033[0;33mWARN\033[0m - {e}")

        # Summary
        print("\n" + "=" * 50)
        print("SUMMARY")
        print("=" * 50)
        ok_count = len([r for r in results if r[1] == "OK"])
        fail_count = len([r for r in results if r[1] == "FAIL"])
        warn_count = len([r for r in results if r[1] == "WARN"])

        for test, status, detail in results:
            if status == "OK":
                print(f"  \033[0;32m✓\033[0m {test:<20} {detail}")
            elif status == "FAIL":
                print(f"  \033[0;31m✗\033[0m {test:<20} {detail}")
            elif status == "WARN":
                print(f"  \033[0;33m!\033[0m {test:<20} {detail}")
            else:
                print(f"  \033[2m-\033[0m {test:<20} {detail}")

        print()
        if fail_count > 0:
            print(f"Result: {fail_count} FAILED - check service connections")
        elif warn_count > 0 and ok_count == 0:
            print("Result: No data sources working - check meshtasticd")
        elif ok_count > 0:
            print(f"Result: {ok_count} sources OK - data should be flowing")
        print()
        self._wait_for_enter()

    def _reports_menu(self):
        """Network status reports: generate, view, save."""
        while True:
            choices = [
                ("generate", "Generate & View     Full status report"),
                ("save", "Generate & Save     Save to file"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Reports",
                "Network status report generation:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "generate":
                self._safe_call("Generate Report", self._generate_and_view_report)
            elif choice == "save":
                self._safe_call("Save Report", self._generate_and_save_report)

    def _generate_and_view_report(self):
        """Generate a full status report and display it."""
        import subprocess as _sp
        _sp.run(['clear'], check=False, timeout=5)
        print("=== Generating Network Status Report ===\n")
        print("Collecting data from all subsystems...\n")

        if not _HAS_REPORT_GEN:
            print("  Report generator module not available.")
            print("  File: src/utils/report_generator.py")
            self._wait_for_enter()
            return

        report = generate_report()
        # Display the markdown report as plain text in terminal
        print(report)
        print()
        self._wait_for_enter()

    def _generate_and_save_report(self):
        """Generate a report and save it to a file."""
        import subprocess as _sp
        _sp.run(['clear'], check=False, timeout=5)
        print("=== Generating & Saving Report ===\n")

        if not _HAS_REPORT_GEN:
            print("  Report generator module not available.")
            print("  File: src/utils/report_generator.py")
            self._wait_for_enter()
            return

        saved_path = generate_and_save()
        print(f"Report saved to:\n  {saved_path}\n")
        self._wait_for_enter()

    def _health_score_display(self):
        """Show comprehensive network health score with category breakdown."""
        import subprocess as _sp
        _sp.run(['clear'], check=False, timeout=5)
        print("=== Network Health Score ===\n")

        scorer = get_health_scorer()
        snapshot = scorer.get_snapshot()

        # Overall score with visual bar
        score = snapshot.overall_score
        bar_len = 30
        filled = int(score / 100 * bar_len)
        bar = "\033[0;32m" + "=" * filled + "\033[0m" + "-" * (bar_len - filled)

        if score >= 80:
            color = "\033[0;32m"  # green
        elif score >= 60:
            color = "\033[0;33m"  # yellow
        elif score >= 40:
            color = "\033[0;31m"  # red
        else:
            color = "\033[1;31m"  # bold red

        print(f"  Overall: {color}{score:.0f}/100\033[0m ({snapshot.status})")
        print(f"  [{bar}]\n")

        # Category breakdown
        print(f"  {'Category':<18} {'Score':>6}  Status")
        print(f"  {'-'*42}")
        for cat, cat_score in snapshot.category_scores.items():
            if cat_score >= 80:
                status = "Good"
                c = "\033[0;32m"
            elif cat_score >= 60:
                status = "Fair"
                c = "\033[0;33m"
            elif cat_score >= 40:
                status = "Degraded"
                c = "\033[0;31m"
            else:
                status = "Critical"
                c = "\033[1;31m"
            print(f"  {cat.title():<18} {c}{cat_score:>5.0f}\033[0m  {status}")

        # Stats
        print(f"\n  Nodes reporting:  {snapshot.node_count}")
        print(f"  Services tracked: {snapshot.service_count}")

        # Trend
        trend = scorer.get_trend()
        trend_icons = {
            'improving': '\033[0;32m  improving\033[0m',
            'declining': '\033[0;31m  declining\033[0m',
            'stable': '  stable',
        }
        print(f"  Trend:           {trend_icons.get(trend, trend)}")

        print()
        self._wait_for_enter()

    def _show_alerts(self):
        """Show current alerts from environment state and EAS."""
        clear_screen()
        print("=== Current Alerts ===\n")

        # System/environment alerts
        if self._env_state:
            alerts = self._env_state.get_alerts()
            if alerts:
                print("SYSTEM ALERTS:")
                for alert in alerts:
                    print(f"  \033[0;33m!\033[0m {alert}")
            else:
                print("  System: No alerts - healthy")
        else:
            print("  Environment state not available")

        # EAS / Weather alerts
        print()
        try:
            plugin = EASAlertsPlugin()
            eas_alerts = plugin.get_weather_alerts()
            if eas_alerts:
                print(f"WEATHER ALERTS ({len(eas_alerts)}):")
                for alert in eas_alerts[:5]:
                    severity = getattr(alert, 'severity', 'Unknown')
                    headline = getattr(alert, 'headline', str(alert))
                    if len(headline) > 65:
                        headline = headline[:62] + "..."
                    print(f"  \033[0;31m!\033[0m [{severity}] {headline}")
            else:
                print("  Weather: No active alerts")
        except Exception as e:
            logger.debug("EAS alert check failed: %s", e)

        print()
        self._wait_for_enter()
