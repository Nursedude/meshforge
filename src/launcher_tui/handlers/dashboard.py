"""
Dashboard Handler — Service status, node counts, data path diagnostics, alerts.

Converted from dashboard_mixin.py as part of the mixin-to-registry migration.
Also absorbs _dashboard_space_weather() from main.py.
"""

import logging
import subprocess

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import
from gateway.circuit_breaker import get_all_registries

logger = logging.getLogger(__name__)

# --- Module-level safe imports (replaces try/except ImportError blocks) ---
ServiceRunState, _HAS_STARTUP_CHECKS = safe_import('startup_checks', 'ServiceRunState')
get_http_client, reset_http_client, _HAS_MESHTASTIC_HTTP = safe_import(
    'utils.meshtastic_http', 'get_http_client', 'reset_http_client'
)
# utils.map_data_collector is imported where used (self-test): at module
# level it pulls the meshtastic collector stack (~280 ms) into TUI startup.
generate_report, generate_and_save, _HAS_REPORT_GEN = safe_import(
    'utils.report_generator', 'generate_report', 'generate_and_save'
)
from utils.health_score import get_health_scorer
from plugins.eas_alerts import EASAlertsPlugin
pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')
try:
    from gateway.radio_failover import FailoverManager
    _HAS_FAILOVER = True
except ImportError:
    _HAS_FAILOVER = False

try:
    from gateway.radio_failover import RadioLoadBalancer
    _HAS_LOAD_BALANCER = True
except ImportError:
    _HAS_LOAD_BALANCER = False


class DashboardHandler(BaseHandler):
    """TUI handler for dashboard display methods."""

    handler_id = "dashboard"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("status", "Service Status      All services with health", None),
            ("weather", "Space Weather       SFI, Kp, bands at a glance", None),
            ("nodes", "Node Count          Meshtastic + RNS nodes", None),
            ("score", "Health Score        Network health snapshot", None),
            ("datapath", "Data Path Check     Test all data sources", None),
            ("reports", "Reports             Generate status report", None),
            ("alerts", "View Alerts         Current warnings", None),
        ]

    def execute(self, action):
        dispatch = {
            "status": ("Service Status", self._service_status_display),
            "weather": ("Space Weather", self._dashboard_space_weather),
            "nodes": ("Node Count", self._show_node_counts),
            "score": ("Health Score", self._health_score_display),
            "datapath": ("Data Path Check", self._data_path_diagnostic),
            "reports": ("Reports", self._reports_menu),
            "alerts": ("View Alerts", self._show_alerts),
        }
        entry = dispatch.get(action)
        if entry:
            self.ctx.safe_call(*entry)

    @staticmethod
    def _meshtasticd_api_reachable() -> bool:
        """2 s TCP probe of meshtasticd's HTTPS API port.

        A meshtasticd daemon with no HAT configured (the classic
        meshadv-30s-on-clean-trixie install) stays systemd-active but
        never binds :9443. Without this probe, the Service Status row
        shows "running" while the rest of MeshForge can't talk to the
        daemon. Mirrors the rnsd zombie pattern in
        ``status_bar._check_systemd_active``.
        """
        try:
            from utils.service_check import check_port
            from utils.ports import MESHTASTICD_WEB_PORT
            return check_port(MESHTASTICD_WEB_PORT, host='127.0.0.1', timeout=2.0)
        except Exception:
            return False

    @staticmethod
    def _classify_http_unavailable(client) -> tuple:
        """Classify an unavailable meshtastic_http client for the data-path check.

        Issue #76: meshtasticd never serves ``/json/*`` (that API is ESP32
        firmware only), so the probe's 'absent' state — a live webserver
        404ing the JSON path — is structural on every meshtasticd box, not a
        fault. Reporting it FAIL sent operators hunting through meshtasticd
        logs for a webserver that was serving 200s. Only a genuinely dead
        webserver (no port answered at all) stays a FAIL.

        Returns ``(status, detail)`` where status is ``"N/A"`` or ``"FAIL"``.
        """
        if getattr(client, 'json_api_absent', False):
            return ("N/A", "meshtasticd serves no /json/* (ESP32-only, "
                           "Issue #76); webserver alive")
        try:
            from utils.meshtastic_http import PROBE_PORTS
            tried = [client.port] + [p for p in PROBE_PORTS
                                     if p != client.port]
            ports = ",".join(str(p) for p in tried)
            return ("FAIL", f"No webserver answered (tried ports {ports}).")
        except Exception:
            return ("FAIL", "No webserver answered.")

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

        # Degraded services collected here → offered as in-app fixes below, so
        # the operator fixes what they see without leaving this view.
        # Each entry: (service_name, running_bool). running=True ⇒ up-but-unhealthy.
        degraded = []

        if self.ctx.env_state and _HAS_STARTUP_CHECKS:
            for name, info in self.ctx.env_state.services.items():
                if info.state == ServiceRunState.RUNNING:
                    if name == 'meshtasticd' and not self._meshtasticd_api_reachable():
                        print(f"  \033[0;33m◐\033[0m {name:<18} active but unreachable (no radio? see Diagnostics)")
                        degraded.append((name, True))
                    else:
                        print(f"  \033[0;32m●\033[0m {name:<18} running")
                elif info.state == ServiceRunState.FAILED:
                    print(f"  \033[0;31m●\033[0m {name:<18} FAILED")
                    degraded.append((name, False))
                else:
                    print(f"  \033[2m○\033[0m {name:<18} stopped")
                    degraded.append((name, False))
        else:
            # MF008: service state via check_service(), not raw systemctl —
            # mirrors the primary branch's RUNNING/FAILED/stopped distinction.
            from utils.service_check import check_service, ServiceState
            for svc in ['meshtasticd', 'rnsd', 'mosquitto']:
                try:
                    svc_status = check_service(svc)
                    if svc_status.available:
                        if svc == 'meshtasticd' and not self._meshtasticd_api_reachable():
                            print(f"  \033[0;33m◐\033[0m {svc:<18} active but unreachable (no radio? see Diagnostics)")
                            degraded.append((svc, True))
                        else:
                            print(f"  \033[0;32m●\033[0m {svc:<18} running")
                    elif svc_status.state in (ServiceState.FAILED, ServiceState.DEGRADED):
                        print(f"  \033[0;31m●\033[0m {svc:<18} FAILED")
                        degraded.append((svc, False))
                    else:
                        print(f"  \033[2m○\033[0m {svc:<18} {svc_status.state.value}")
                        degraded.append((svc, False))
                except Exception:
                    print(f"  ? {svc:<18} unknown")

        # Circuit breaker status — show any open circuits
        try:
            registries = get_all_registries()
            open_circuits = []
            for svc_name, registry in registries.items():
                for dest, info in registry.get_open_circuits().items():
                    open_circuits.append((svc_name, dest, info.get("state", "open")))
            if open_circuits:
                print("  CIRCUIT BREAKERS")
                for svc, dest, state in open_circuits:
                    print(f"  \033[0;33m⚡\033[0m {svc}/{dest}: {state}")
                print()
        except Exception:
            pass  # Circuit breaker info is advisory, never block status display

        # Dual-radio failover status
        if _HAS_FAILOVER and hasattr(self.ctx, 'failover_manager') and self.ctx.failover_manager:
            try:
                status = self.ctx.failover_manager.get_status()
                if status.get('enabled'):
                    state = status['state']
                    active = status['active_port']
                    p = status['primary']
                    s = status['secondary']

                    state_colors = {
                        'primary_active': '\033[0;32m',     # Green
                        'secondary_active': '\033[0;33m',   # Yellow
                        'failover_pending': '\033[0;33m',   # Yellow
                        'recovery_pending': '\033[0;36m',   # Cyan
                        'disabled': '\033[2m',              # Dim
                    }
                    color = state_colors.get(state, '')
                    reset = '\033[0m'

                    print(f"\n  DUAL-RADIO FAILOVER")
                    print(f"  {color}State: {state}{reset}  |  Active TX: port {active}")
                    p_icon = '\033[0;32m●\033[0m' if p['reachable'] else '\033[0;31m●\033[0m'
                    s_icon = '\033[0;32m●\033[0m' if s['reachable'] else '\033[0;31m●\033[0m'
                    print(f"  {p_icon} Primary  :{p['port']}  ch_util={p['channel_utilization']:.1f}%  tx={p['tx_utilization']:.1f}%")
                    print(f"  {s_icon} Secondary:{s['port']}  ch_util={s['channel_utilization']:.1f}%  tx={s['tx_utilization']:.1f}%")
                    if status.get('last_event'):
                        print(f"  Last event: {status['last_event']}")
            except Exception:
                pass  # Failover info is advisory

        # TX load balancer status
        if _HAS_LOAD_BALANCER and hasattr(self.ctx, 'load_balancer') and self.ctx.load_balancer:
            try:
                status = self.ctx.load_balancer.get_status()
                if status.get('enabled'):
                    state = status['state']
                    p = status['primary']
                    s = status['secondary']
                    p_w = status['primary_weight']
                    s_w = status['secondary_weight']
                    tx_counts = status.get('tx_counts', {})

                    state_colors = {
                        'idle': '\033[0;32m',        # Green
                        'balancing': '\033[0;33m',   # Yellow
                        'saturated': '\033[0;31m',   # Red
                        'disabled': '\033[2m',       # Dim
                    }
                    color = state_colors.get(state, '')
                    reset = '\033[0m'

                    print(f"\n  TX LOAD BALANCER")
                    print(f"  {color}State: {state}{reset}")
                    p_icon = '\033[0;32m●\033[0m' if p['reachable'] else '\033[0;31m●\033[0m'
                    s_icon = '\033[0;32m●\033[0m' if s['reachable'] else '\033[0;31m●\033[0m'
                    print(f"  {p_icon} Primary  :{p['port']}  tx={p['tx_utilization']:.1f}%  weight={p_w:.0f}%  sent={tx_counts.get('primary', 0)}")
                    print(f"  {s_icon} Secondary:{s['port']}  tx={s['tx_utilization']:.1f}%  weight={s_w:.0f}%  sent={tx_counts.get('secondary', 0)}")

                    # Congested nodes — top talkers causing utilization
                    congested = status.get('congested_nodes', [])
                    if congested:
                        print(f"  \033[0;33mCongested nodes:\033[0m")
                        for node in congested[:5]:
                            name = node.get('name', node.get('id', '?'))
                            ch = node.get('channel_util', 0)
                            tx = node.get('tx_airtime', 0)
                            print(f"    {name}: ch_util={ch:.1f}% tx={tx:.1f}%")

                    if status.get('last_event'):
                        print(f"  Last: {status['last_event']}")
            except Exception:
                pass  # Load balancer info is advisory

        print()
        # In-Domain: the fix comes to the operator, here, where they saw the
        # problem — no hunting through Mesh Networks / System / Configuration.
        if degraded:
            self._offer_service_fixes(degraded)
        else:
            self.ctx.wait_for_enter()

    def _offer_service_fixes(self, degraded):
        """Offer in-app fixes for degraded services via the shared surface.

        ``degraded`` is a list of (service_name, running_bool). Delegates to the
        shared, profile-gated chooser so the operator never has to navigate to
        the fix (In-Domain Principle). Falls back to a plain wait when nothing on
        this box is fixable.
        """
        from service_remediation import offer_service_fix_chooser
        if not offer_service_fix_chooser(self.ctx, degraded):
            self.ctx.wait_for_enter()

    def _dashboard_space_weather(self):
        """Quick-look space weather for the Dashboard.

        Shows a compact summary with SFI, Kp, band conditions, and
        a link to the full propagation suite under RF & SDR.
        """
        from commands import propagation as prop_mod

        result = prop_mod.get_space_weather()
        if not result.success:
            self.ctx.dialog.msgbox(
                "Space Weather",
                f"Could not fetch space weather data:\n{result.message}\n\n"
                "Ensure internet connectivity is available.\n"
                "NOAA SWPC is the primary data source."
            )
            return

        d = result.data
        lines = [
            "SPACE WEATHER SNAPSHOT",
            "=" * 40,
            "",
            f"Solar Flux (SFI):  {d.get('solar_flux', 'N/A')} SFU",
            f"Kp Index:          {d.get('k_index', 'N/A')}",
            f"A Index:           {d.get('a_index', 'N/A')}",
            f"X-ray Flux:        {d.get('xray_flux', 'N/A')}",
            f"Geomagnetic:       {d.get('geomag_storm', 'Quiet')}",
            "",
        ]

        bands = d.get('band_conditions', {})
        if bands:
            lines.append("HF BAND CONDITIONS")
            lines.append("-" * 40)
            for band, cond in bands.items():
                lines.append(f"  {band:<12s} {cond}")
            lines.append("")

        lines.append("-" * 40)
        lines.append("Full propagation tools: RF & SDR > Space Weather")
        lines.append(f"Source: {d.get('source', 'NOAA SWPC')}")

        self.ctx.dialog.msgbox("Space Weather", "\n".join(lines), width=50, height=22)

    def _show_node_counts(self):
        """Show node counts from all sources."""
        clear_screen()
        print("=== Node Counts ===\n")

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

        try:
            result = subprocess.run(
                ['rnstatus', '-a'],
                capture_output=True, text=True, timeout=10
            )
            dest_count = len([line for line in result.stdout.splitlines()
                             if line.strip().startswith('<')])
            print(f"  RNS destinations: {dest_count}")
        except Exception:
            print("  RNS: unavailable")

        print()
        self.ctx.wait_for_enter()

    def _data_path_diagnostic(self):
        """Test all data collection paths to diagnose zero-data issues."""
        clear_screen()
        print("=== Data Path Diagnostic ===\n")
        print("Testing all data sources...\n")

        results = []

        # Test 1: meshtasticd TCP connection
        print("[1/6] Testing meshtasticd TCP (port 4403)...")
        try:
            from utils.service_check import check_port
            if check_port(4403, timeout=3):
                results.append(("meshtasticd TCP", "OK", "Port 4403 accepting connections"))
                print("      \033[0;32mOK\033[0m - Port 4403 reachable")
            else:
                results.append(("meshtasticd TCP", "FAIL", "Connection refused"))
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
                reset_http_client()
                client = get_http_client()
                if client.is_available:
                    nodes = client.get_nodes()
                    results.append(("meshtasticd HTTP", "OK",
                                   f"{len(nodes)} nodes via {client._base_url}"))
                    print(f"      \033[0;32mOK\033[0m - {len(nodes)} nodes at {client._base_url}")
                else:
                    status, detail = self._classify_http_unavailable(client)
                    if status == "N/A":
                        results.append(("meshtasticd HTTP", "N/A", detail))
                        print(f"      \033[2mN/A \033[0m - {detail}")
                    else:
                        hint = self._check_webserver_config()
                        results.append(("meshtasticd HTTP", "FAIL",
                                        f"{detail} {hint}"))
                        print("      \033[0;31mFAIL\033[0m - HTTP API not reachable")
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
                topic = pub.getDefaultTopicMgr().getTopic('meshtastic.receive', okIfNone=True)
                if topic:
                    count = len(list(topic.getListeners()))
                    results.append(("pubsub", "OK", f"{count} listener(s) on meshtastic.receive"))
                    print(f"      \033[0;32mOK\033[0m - {count} listener(s) registered")
                else:
                    # No topic in THIS process is the normal state outside the
                    # daemon (listeners live in gateway/monitor processes) —
                    # not a warning.
                    results.append(("pubsub", "OK",
                                    "pubsub importable; no live-capture topic "
                                    "in this process (normal outside the daemon)"))
                    print("      \033[0;32mOK\033[0m - importable; no topic in "
                          "this process (normal outside the daemon)")
            except Exception as e:
                results.append(("pubsub", "WARN", str(e)[:50]))
                print(f"      \033[0;33mWARN\033[0m - {e}")

        # Test 5: MapDataCollector
        print("[5/6] Testing MapDataCollector...")
        try:
            from utils.map_data_collector import MapDataCollector
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
        self.ctx.wait_for_enter()

    def _reports_menu(self):
        """Network status reports: generate, view, save."""
        while True:
            choices = [
                ("generate", "Generate & View     Full status report"),
                ("save", "Generate & Save     Save to file"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Reports",
                "Network status report generation:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "generate":
                self.ctx.safe_call("Generate Report", self._generate_and_view_report)
            elif choice == "save":
                self.ctx.safe_call("Save Report", self._generate_and_save_report)

    def _generate_and_view_report(self):
        """Generate a full status report and display it."""
        import subprocess as _sp
        _sp.run(['clear'], check=False, timeout=5)
        print("=== Generating Network Status Report ===\n")
        print("Collecting data from all subsystems...\n")

        if not _HAS_REPORT_GEN:
            print("  Report generator module not available.")
            print("  File: src/utils/report_generator.py")
            self.ctx.wait_for_enter()
            return

        report = generate_report()
        print(report)
        print()
        self.ctx.wait_for_enter()

    def _generate_and_save_report(self):
        """Generate a report and save it to a file."""
        import subprocess as _sp
        _sp.run(['clear'], check=False, timeout=5)
        print("=== Generating & Saving Report ===\n")

        if not _HAS_REPORT_GEN:
            print("  Report generator module not available.")
            print("  File: src/utils/report_generator.py")
            self.ctx.wait_for_enter()
            return

        saved_path = generate_and_save()
        print(f"Report saved to:\n  {saved_path}\n")
        self.ctx.wait_for_enter()

    def _health_score_display(self):
        """Show comprehensive network health score with category breakdown."""
        import subprocess as _sp
        _sp.run(['clear'], check=False, timeout=5)
        print("=== Network Health Score ===\n")

        scorer = get_health_scorer()
        snapshot = scorer.get_snapshot()

        score = snapshot.overall_score
        bar_len = 30
        filled = int(score / 100 * bar_len)
        bar = "\033[0;32m" + "=" * filled + "\033[0m" + "-" * (bar_len - filled)

        if score >= 80:
            color = "\033[0;32m"
        elif score >= 60:
            color = "\033[0;33m"
        elif score >= 40:
            color = "\033[0;31m"
        else:
            color = "\033[1;31m"

        print(f"  Overall: {color}{score:.0f}/100\033[0m ({snapshot.status})")
        print(f"  [{bar}]\n")

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

        print(f"\n  Nodes reporting:  {snapshot.node_count}")
        print(f"  Services tracked: {snapshot.service_count}")

        trend = scorer.get_trend()
        trend_icons = {
            'improving': '\033[0;32m  improving\033[0m',
            'declining': '\033[0;31m  declining\033[0m',
            'stable': '  stable',
        }
        print(f"  Trend:           {trend_icons.get(trend, trend)}")

        print()
        self.ctx.wait_for_enter()

    # Known alert patterns mapped to remediation guidance.
    _REMEDIATION_HINTS = {
        "meshtasticd": "Configuration > meshtasticd > Restart Service",
        "rnsd": "Mesh Networks > RNS > RNS Diagnostics (auto-repair)",
        "port": "System > Network Tools > Port Listening",
        "mqtt": "Mesh Networks > MQTT > Broker Profiles",
        "bridge": "Mesh Networks > Gateway Bridge > Configure",
        "connection": "Configuration > meshtasticd > Connection Test",
        "identity": "Mesh Networks > Gateway Bridge > Configure",
    }

    def _show_alerts(self):
        """Show current alerts from environment state, mesh alerts, and EAS."""
        clear_screen()
        print("=== Current Alerts ===\n")

        # Demo mode indicator
        if self.ctx.env.get('demo_mode'):
            print("  \033[0;36m[DEMO MODE ACTIVE]\033[0m\n")

        has_system_alerts = False
        alert_texts = []

        if self.ctx.env_state:
            alerts = self.ctx.env_state.get_alerts()
            if alerts:
                has_system_alerts = True
                print("SYSTEM ALERTS:")
                for alert in alerts:
                    print(f"  \033[0;33m!\033[0m {alert}")
                    alert_texts.append(alert.lower())
            else:
                print("  System: No alerts - healthy")
        else:
            print("  Environment state not available")

        # Mesh alerts from alert engine
        print()
        try:
            from utils.mesh_alert_engine import get_alert_engine
            engine = get_alert_engine()
            mesh_alerts = engine.get_active_alerts()
            if mesh_alerts:
                print(f"MESH ALERTS ({len(mesh_alerts)}):")
                severity_colors = {
                    1: "\033[0;34m",   # Blue
                    2: "\033[0;33m",   # Yellow
                    3: "\033[0;31m",   # Red
                    4: "\033[1;31m",   # Bold Red
                }
                reset = "\033[0m"
                for alert in mesh_alerts[:10]:
                    color = severity_colors.get(alert.severity, "")
                    print(f"  {color}[{alert.severity_label}]{reset} {alert.title}")
                    print(f"           {alert.message}")
                if len(mesh_alerts) > 10:
                    print(f"  ... and {len(mesh_alerts) - 10} more")
            else:
                print("  Mesh: No active alerts")
        except Exception as e:
            logger.debug("Mesh alert check failed: %s", e)

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

        # Show remediation hints for system alerts
        if has_system_alerts:
            combined = " ".join(alert_texts)
            hints = []
            for keyword, action in self._REMEDIATION_HINTS.items():
                if keyword in combined:
                    hints.append(f"  -> {action}")
            if hints:
                print("\nSUGGESTED ACTIONS:")
                for hint in dict.fromkeys(hints):  # deduplicate, preserve order
                    print(hint)

        print()
        self.ctx.wait_for_enter()
