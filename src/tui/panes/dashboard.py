"""Dashboard Pane - System status overview."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static, Button, Log, Rule
from textual import work

logger = logging.getLogger('tui')

# Import centralized service checker
try:
    from utils.service_check import check_service
except ImportError:
    check_service = None


class DashboardPane(Container):
    """Dashboard showing system status"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._auto_refresh = False
        self._refresh_interval = 5  # seconds

    def compose(self) -> ComposeResult:
        yield Static("# MeshForge Dashboard", classes="title")
        yield Rule()

        # Row 1: Service & Mesh Status
        with Horizontal(classes="status-cards"):
            with Container(classes="card"):
                yield Static("[SIG] Meshtasticd", classes="card-title")
                yield Static("Checking...", id="service-status", classes="card-value")
                yield Static("", id="service-detail", classes="card-detail")

            with Container(classes="card"):
                yield Static("[MESH] Mesh Nodes", classes="card-title")
                yield Static("Checking...", id="nodes-status", classes="card-value")
                yield Static("", id="nodes-detail", classes="card-detail")

        # Row 2: RNS & Hardware
        with Horizontal(classes="status-cards"):
            with Container(classes="card"):
                yield Static("[NET] Reticulum", classes="card-title")
                yield Static("Checking...", id="rns-status", classes="card-value")
                yield Static("", id="rns-detail", classes="card-detail")

            with Container(classes="card"):
                yield Static("[HW] Hardware", classes="card-title")
                yield Static("Checking...", id="hw-status", classes="card-value")
                yield Static("", id="hw-detail", classes="card-detail")

        # Row 3: Version & Config
        with Horizontal(classes="status-cards"):
            with Container(classes="card"):
                yield Static("[PKG] Version", classes="card-title")
                yield Static("Checking...", id="version-status", classes="card-value")

            with Container(classes="card"):
                yield Static("[CFG] Config", classes="card-title")
                yield Static("Checking...", id="config-status", classes="card-value")

        # Quick Actions
        yield Static("## Quick Actions", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Restart Service", id="dash-restart", variant="warning")
            yield Button("View Nodes", id="dash-nodes", variant="primary")
            yield Button("View Logs", id="dash-logs")
            yield Button("Full Diagnostics", id="dash-diag")

        # Logs section
        yield Static("## Recent Activity", classes="section-title")
        yield Log(id="dashboard-log", classes="log-panel")

        with Horizontal(classes="button-row"):
            yield Button("Refresh", id="refresh-dashboard", variant="primary")
            yield Button("Auto-Refresh: OFF", id="toggle-auto-refresh")
            yield Button("Clear", id="dash-clear")

    async def on_mount(self):
        """Called when widget is mounted"""
        self.refresh_data()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle dashboard button presses"""
        button_id = event.button.id
        log = self.query_one("#dashboard-log", Log)

        if button_id == "refresh-dashboard":
            self.refresh_data()
        elif button_id == "toggle-auto-refresh":
            self._auto_refresh = not self._auto_refresh
            btn = self.query_one("#toggle-auto-refresh", Button)
            if self._auto_refresh:
                btn.label = "Auto-Refresh: ON"
                self._start_auto_refresh()
            else:
                btn.label = "Auto-Refresh: OFF"
        elif button_id == "dash-clear":
            log.clear()
        elif button_id == "dash-restart":
            log.write("[yellow]Restarting meshtasticd...[/yellow]")
            self._restart_service()
        elif button_id == "dash-nodes":
            log.write("[cyan]Fetching node list...[/cyan]")
            self._fetch_nodes()
        elif button_id == "dash-logs":
            log.write("[cyan]Fetching recent logs...[/cyan]")
            self._fetch_logs()
        elif button_id == "dash-diag":
            log.write("[cyan]Running diagnostics...[/cyan]")
            self._run_diagnostics()

    @work
    async def _restart_service(self):
        """Restart meshtasticd service"""
        log = self.query_one("#dashboard-log", Log)
        try:
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'restart', 'meshtasticd',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await result.communicate()
            if result.returncode == 0:
                log.write("[green]Service restarted successfully[/green]")
                await asyncio.sleep(2)
                self.refresh_data()
            else:
                log.write(f"[red]Error: {stderr.decode()}[/red]")
        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

    @work
    async def _fetch_nodes(self):
        """Fetch and display node list"""
        log = self.query_one("#dashboard-log", Log)
        try:
            try:
                from utils.connection_manager import get_nodes
                loop = asyncio.get_event_loop()
                nodes = await loop.run_in_executor(None, get_nodes)
                if nodes:
                    log.write(f"\n[cyan]Found {len(nodes)} node(s):[/cyan]")
                    for node in nodes:
                        name = node.get('name', 'Unknown')
                        short = node.get('short', '????')
                        node_id = node.get('id', 'N/A')
                        log.write(f"  [{short}] {name} - {node_id}")
                else:
                    log.write("[yellow]No nodes found or service not available[/yellow]")
                return
            except ImportError:
                pass

            # Fallback to meshtastic CLI
            result = await asyncio.create_subprocess_exec(
                'meshtastic', '--host', '127.0.0.1', '--nodes',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            if stdout:
                log.write(stdout.decode())
            if stderr and result.returncode != 0:
                log.write(f"[red]{stderr.decode()}[/red]")
        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

    @work
    async def _fetch_logs(self):
        """Fetch recent logs"""
        log = self.query_one("#dashboard-log", Log)
        log.clear()
        try:
            result = await asyncio.create_subprocess_exec(
                'journalctl', '-u', 'meshtasticd', '-n', '30', '--no-pager',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            log.write(stdout.decode())
        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

    @work
    async def _run_diagnostics(self):
        """Run quick diagnostics"""
        import socket
        log = self.query_one("#dashboard-log", Log)
        log.clear()
        log.write("[cyan]== MeshForge Diagnostics ==[/cyan]\n")

        # Check meshtasticd
        try:
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'is-active', 'meshtasticd',
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            status = stdout.decode().strip()
            if status == "active":
                log.write("[green][OK][/green] meshtasticd running")
            else:
                log.write("[red][X][/red] meshtasticd not running")
        except Exception:
            log.write("[red][X][/red] Could not check meshtasticd")

        # Check TCP port
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', 4403))
            sock.close()
            if result == 0:
                log.write("[green][OK][/green] TCP port 4403 open")
            else:
                log.write("[red][X][/red] TCP port 4403 closed")
        except Exception:
            log.write("[red][X][/red] Could not check port 4403")

        # Check RNS
        try:
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'is-active', 'rnsd',
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            status = stdout.decode().strip()
            if status == "active":
                log.write("[green][OK][/green] rnsd running")
            else:
                log.write("[yellow][~][/yellow] rnsd not running")
        except Exception:
            log.write("[yellow][~][/yellow] rnsd not found")

        # Check SPI
        if Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists():
            log.write("[green][OK][/green] SPI enabled")
        else:
            log.write("[red][X][/red] SPI not enabled")

        # Check I2C
        if Path('/dev/i2c-1').exists():
            log.write("[green][OK][/green] I2C enabled")
        else:
            log.write("[yellow][~][/yellow] I2C not enabled")

        log.write("\n[cyan]== Diagnostics Complete ==[/cyan]")

    @work(exclusive=True)
    async def _start_auto_refresh(self):
        """Auto-refresh loop"""
        while self._auto_refresh:
            await asyncio.sleep(self._refresh_interval)
            if self._auto_refresh:
                self.refresh_data()

    @work(exclusive=True)
    async def refresh_data(self):
        """Refresh dashboard data"""
        log = self.query_one("#dashboard-log", Log)

        # Service status
        try:
            status_widget = self.query_one("#service-status", Static)
            detail_widget = self.query_one("#service-detail", Static)

            if check_service:
                loop = asyncio.get_event_loop()
                service_status = await loop.run_in_executor(
                    None, lambda: check_service('meshtasticd')
                )
                if service_status.available:
                    status_widget.update("[green]● Running[/green]")
                    detail_widget.update("TCP 4403 open")
                else:
                    status_widget.update("[red]○ Stopped[/red]")
                    detail_widget.update(service_status.message)
            else:
                result = await asyncio.create_subprocess_exec(
                    'systemctl', 'is-active', 'meshtasticd',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await result.communicate()
                status = stdout.decode().strip()
                if status == "active":
                    status_widget.update("[green]● Running[/green]")
                    detail_widget.update("Active")
                else:
                    status_widget.update("[red]○ Stopped[/red]")
                    detail_widget.update(status)
        except Exception:
            self.query_one("#service-status", Static).update("[red]Error[/red]")

        # Mesh Nodes
        try:
            nodes_widget = self.query_one("#nodes-status", Static)
            nodes_detail = self.query_one("#nodes-detail", Static)
            try:
                from utils.connection_manager import get_nodes, is_available
                loop = asyncio.get_event_loop()
                available = await loop.run_in_executor(None, is_available)
                if available:
                    nodes_detail.update("Fetching...")
                    nodes = await loop.run_in_executor(None, get_nodes)
                    if nodes:
                        count = len(nodes) if isinstance(nodes, list) else 0
                        nodes_widget.update(f"[green]{count} nodes[/green]")
                        nodes_detail.update("Live")
                    else:
                        nodes_widget.update("[yellow]0 nodes[/yellow]")
                        nodes_detail.update("No nodes found")
                else:
                    nodes_widget.update("[yellow]N/A[/yellow]")
                    nodes_detail.update("Port 4403 closed")
            except ImportError:
                nodes_widget.update("[yellow]N/A[/yellow]")
                nodes_detail.update("No manager")
        except Exception as e:
            logger.debug(f"Node fetch error: {e}")

        # RNS Status
        try:
            rns_widget = self.query_one("#rns-status", Static)
            rns_detail = self.query_one("#rns-detail", Static)
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'is-active', 'rnsd',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            status = stdout.decode().strip()
            if status == "active":
                rns_widget.update("[green]● Running[/green]")
                rns_detail.update("rnsd active")
            else:
                rns_widget.update("[yellow]○ Inactive[/yellow]")
                rns_detail.update("Optional")
        except Exception:
            self.query_one("#rns-status", Static).update("[yellow]N/A[/yellow]")

        # Version
        try:
            result = await asyncio.create_subprocess_exec(
                'meshtasticd', '--version',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            if result.returncode == 0:
                version = stdout.decode().strip()
                if 'version' in version.lower():
                    version = version.split()[-1]
                self.query_one("#version-status", Static).update(f"[green]{version}[/green]")
            else:
                self.query_one("#version-status", Static).update("[yellow]Not installed[/yellow]")
        except FileNotFoundError:
            self.query_one("#version-status", Static).update("[yellow]Not installed[/yellow]")
        except Exception:
            pass

        # Config status
        config_path = Path('/etc/meshtasticd/config.yaml')
        config_d = Path('/etc/meshtasticd/config.d')
        if config_path.exists():
            active = len(list(config_d.glob('*.yaml'))) if config_d.exists() else 0
            self.query_one("#config-status", Static).update(f"[green]{active} active[/green]")
        else:
            self.query_one("#config-status", Static).update("[yellow]Not configured[/yellow]")

        # Hardware
        hw_widget = self.query_one("#hw-status", Static)
        hw_detail = self.query_one("#hw-detail", Static)
        spi = Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists()
        i2c = Path('/dev/i2c-1').exists()
        gpio = Path('/sys/class/gpio').exists()
        hw_parts = []
        if spi:
            hw_parts.append("SPI")
        if i2c:
            hw_parts.append("I2C")
        if gpio:
            hw_parts.append("GPIO")
        if hw_parts:
            hw_widget.update(f"[green]{', '.join(hw_parts)}[/green]")
            hw_detail.update("Ready")
        else:
            hw_widget.update("[yellow]Check config[/yellow]")
            hw_detail.update("Enable in raspi-config")

        # Timestamp
        log.write(f"[dim]Last refresh: {datetime.now().strftime('%H:%M:%S')}[/dim]")
