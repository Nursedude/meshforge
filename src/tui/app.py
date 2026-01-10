"""
Meshtasticd Manager - Textual TUI Application

A modern terminal UI that works over SSH and on headless systems.
Uses the Textual framework for a rich, interactive experience.
"""

import sys
import os
import subprocess
import asyncio
import shlex
import logging
from pathlib import Path

# Set up logging for TUI diagnostics
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('/tmp/meshforge-tui.log'),
    ]
)
logger = logging.getLogger('tui')

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Header, Footer, Static, Button, Label, ListItem, ListView,
    Input, Log, TabbedContent, TabPane, DataTable, ProgressBar,
    Markdown, Rule
)
from textual.binding import Binding
from textual.screen import Screen
from textual import work

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from __version__ import __version__

# Import centralized service checker
try:
    from utils.service_check import check_service, check_port, ServiceStatus
except ImportError:
    check_service = None
    check_port = None
    ServiceStatus = None


class StatusWidget(Static):
    """Status bar widget showing service state"""

    def __init__(self):
        super().__init__("")
        self.service_status = "checking"
        self.update_status()

    def update_status(self):
        """Update the status display"""
        if self.service_status == "active":
            status_text = "[green]● Service: Running[/green]"
        elif self.service_status == "inactive":
            status_text = "[red]○ Service: Stopped[/red]"
        else:
            status_text = "[yellow]? Service: Unknown[/yellow]"

        self.update(status_text)

    def set_service_status(self, status: str):
        """Set the service status"""
        self.service_status = status
        self.update_status()


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
            # Try to use connection_manager
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
        import socket
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

        # Service status - use centralized check if available
        status_widget = self.query_one("#service-status", Static)
        detail_widget = self.query_one("#service-detail", Static)
        try:
            if check_service:
                # Use centralized service checker (run in thread pool)
                service_status = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: check_service('meshtasticd')
                )
                if service_status.available:
                    status_widget.update("[green]● Running[/green]")
                    detail_widget.update("TCP 4403 open")
                else:
                    status_widget.update("[red]○ Stopped[/red]")
                    detail_widget.update(service_status.message or "Not running")
            else:
                # Fallback to direct systemctl
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
                    detail_widget.update(status or "inactive")
        except Exception as e:
            status_widget.update("[red]Error[/red]")
            detail_widget.update(str(e)[:20])

        # Mesh Nodes - fetch from meshtasticd (not just cache)
        nodes_widget = self.query_one("#nodes-status", Static)
        nodes_detail = self.query_one("#nodes-detail", Static)
        try:
            try:
                from utils.connection_manager import get_nodes, is_available

                # Check if port is available first
                available = await asyncio.get_running_loop().run_in_executor(None, is_available)
                if available:
                    # Fetch fresh nodes (also updates cache)
                    nodes_detail.update("Fetching...")
                    nodes = await asyncio.get_running_loop().run_in_executor(None, get_nodes)
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
            nodes_widget.update("[red]Error[/red]")
            nodes_detail.update(str(e)[:20])

        # RNS Status
        rns_widget = self.query_one("#rns-status", Static)
        rns_detail = self.query_one("#rns-detail", Static)
        try:
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
            rns_widget.update("[yellow]N/A[/yellow]")
            rns_detail.update("Check failed")

        # Version
        version_widget = self.query_one("#version-status", Static)
        try:
            result = await asyncio.create_subprocess_exec(
                'meshtasticd', '--version',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            if result.returncode == 0:
                version = stdout.decode().strip()
                # Clean up version string
                if 'version' in version.lower():
                    version = version.split()[-1]
                version_widget.update(f"[green]{version}[/green]")
            else:
                version_widget.update("[yellow]Not installed[/yellow]")
        except FileNotFoundError:
            version_widget.update("[yellow]Not installed[/yellow]")
        except Exception:
            version_widget.update("[yellow]Unknown[/yellow]")

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

        # Show refresh timestamp in log (only on first load or manual refresh)
        from datetime import datetime
        log.write(f"[dim]Last refresh: {datetime.now().strftime('%H:%M:%S')}[/dim]")


class ServicePane(Container):
    """Service management pane"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._following = False
        self._follow_task = None

    def compose(self) -> ComposeResult:
        yield Static("# Service Management", classes="title")
        yield Rule()

        with Container(classes="card"):
            yield Static("Service Status", classes="card-title")
            yield Static("Checking...", id="svc-status", classes="card-value")
            yield Static("", id="svc-detail", classes="card-detail")

        with Horizontal(classes="button-row"):
            yield Button("Start", id="svc-start", variant="success")
            yield Button("Stop", id="svc-stop", variant="error")
            yield Button("Restart", id="svc-restart", variant="warning")
            yield Button("Reload Config", id="svc-reload")

        yield Static("## Boot Options", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Enable on Boot", id="svc-enable")
            yield Button("Disable on Boot", id="svc-disable")

        yield Static("## Service Logs", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Fetch Logs", id="svc-logs")
            yield Button("Follow Logs", id="svc-follow")
            yield Button("Stop Follow", id="svc-stop-follow")
            yield Button("Clear", id="svc-clear")

        yield Log(id="svc-log", classes="log-panel")

    async def on_mount(self):
        self.refresh_status()
        # Hide stop follow button initially
        self.query_one("#svc-stop-follow", Button).display = False

    @work(exclusive=True)
    async def refresh_status(self):
        """Refresh service status"""
        try:
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'is-active', 'meshtasticd',
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            is_active = stdout.decode().strip() == "active"

            status_widget = self.query_one("#svc-status", Static)
            if is_active:
                status_widget.update("[bold green]● Running[/bold green]")
            else:
                status_widget.update("[bold red]○ Stopped[/bold red]")

            # Get details
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'show', 'meshtasticd',
                '--property=MainPID,ActiveEnterTimestamp',
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            self.query_one("#svc-detail", Static).update(stdout.decode().strip())

        except Exception as e:
            self.query_one("#svc-status", Static).update(f"[red]Error: {e}[/red]")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        logger.info(f"[Service] Button pressed: {button_id}")
        log = self.query_one("#svc-log", Log)

        if button_id == "svc-start":
            log.write("Starting service...")
            self.run_systemctl("start")  # @work decorated - no await

        elif button_id == "svc-stop":
            log.write("Stopping service...")
            self.run_systemctl("stop")  # @work decorated - no await

        elif button_id == "svc-restart":
            log.write("Restarting service...")
            self.run_systemctl("restart")  # @work decorated - no await

        elif button_id == "svc-reload":
            log.write("Reloading daemon...")
            await self.run_command(['systemctl', 'daemon-reload'])

        elif button_id == "svc-enable":
            log.write("Enabling on boot...")
            self.run_systemctl("enable")  # @work decorated - no await

        elif button_id == "svc-disable":
            log.write("Disabling from boot...")
            self.run_systemctl("disable")  # @work decorated - no await

        elif button_id == "svc-logs":
            self.fetch_logs()  # @work decorated - no await

        elif button_id == "svc-follow":
            await self.start_following()

        elif button_id == "svc-stop-follow":
            self.stop_following()

        elif button_id == "svc-clear":
            log.clear()

    @work
    async def run_systemctl(self, action: str):
        """Run a systemctl action"""
        await self.run_command(['systemctl', action, 'meshtasticd'])
        self.refresh_status()

    async def run_command(self, cmd: list):
        """Run a command and log output"""
        log = self.query_one("#svc-log", Log)
        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()

            if stdout:
                log.write(stdout.decode())
            if stderr:
                log.write(f"[red]{stderr.decode()}[/red]")

            if result.returncode == 0:
                log.write("[green]Command completed successfully[/green]")
            else:
                log.write(f"[red]Command failed with code {result.returncode}[/red]")

        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")

    @work
    async def fetch_logs(self):
        """Fetch service logs"""
        log = self.query_one("#svc-log", Log)
        log.clear()

        result = await asyncio.create_subprocess_exec(
            'journalctl', '-u', 'meshtasticd', '-n', '50', '--no-pager',
            stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await result.communicate()
        log.write(stdout.decode())

    async def start_following(self):
        """Start following logs"""
        self._following = True
        self.query_one("#svc-follow", Button).display = False
        self.query_one("#svc-stop-follow", Button).display = True
        log = self.query_one("#svc-log", Log)
        log.write("[yellow]Following logs... Press 'Stop Follow' to stop[/yellow]\n")
        self._follow_logs()

    def stop_following(self):
        """Stop following logs"""
        self._following = False
        self.query_one("#svc-follow", Button).display = True
        self.query_one("#svc-stop-follow", Button).display = False
        log = self.query_one("#svc-log", Log)
        log.write("[yellow]Log following stopped[/yellow]\n")

    @work(exclusive=True)
    async def _follow_logs(self):
        """Worker that follows logs"""
        log = self.query_one("#svc-log", Log)
        while self._following:
            try:
                result = await asyncio.create_subprocess_exec(
                    'journalctl', '-u', 'meshtasticd', '-n', '20', '--no-pager',
                    stdout=asyncio.subprocess.PIPE
                )
                stdout, _ = await result.communicate()
                log.clear()
                log.write(stdout.decode())
            except Exception as e:
                log.write(f"[red]Error fetching logs: {e}[/red]")
            await asyncio.sleep(2)  # Refresh every 2 seconds


class ConfigPane(Container):
    """Configuration file manager pane"""

    CONFIG_BASE = Path("/etc/meshtasticd")
    AVAILABLE_D = CONFIG_BASE / "available.d"
    CONFIG_D = CONFIG_BASE / "config.d"

    def compose(self) -> ComposeResult:
        yield Static("# Config File Manager", classes="title")
        yield Static("Select configs from available.d to activate", classes="subtitle")
        yield Rule()

        with Horizontal():
            with Container(classes="list-container"):
                yield Static("Available Configs", classes="list-title")
                yield ListView(id="available-list")

            with Container(classes="list-container"):
                yield Static("Active Configs", classes="list-title")
                yield ListView(id="active-list")

        with Horizontal(classes="button-row"):
            yield Button("Activate", id="cfg-activate", variant="primary")
            yield Button("Deactivate", id="cfg-deactivate", variant="error")
            yield Button("Edit with nano", id="cfg-edit")
            yield Button("Edit config.yaml", id="cfg-main")
            yield Button("Refresh", id="cfg-refresh")

        yield Static("## Apply Changes", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Apply & Restart Service", id="cfg-apply", variant="warning")

        yield Static("## Preview", classes="section-title")
        yield Log(id="cfg-preview", classes="log-panel")

    async def on_mount(self):
        self.refresh_lists()

    def refresh_lists(self):
        """Refresh config lists"""
        available_list = self.query_one("#available-list", ListView)
        active_list = self.query_one("#active-list", ListView)

        # Clear lists
        available_list.clear()
        active_list.clear()

        # Load available configs
        if self.AVAILABLE_D.exists():
            for config in sorted(self.AVAILABLE_D.glob("*.yaml")):
                # Replace dots with underscores for ID - dots are invalid in Textual IDs
                safe_id = config.stem.replace(".", "_")
                available_list.append(ListItem(Label(config.name), id=f"avail-{safe_id}"))

        # Load active configs
        if self.CONFIG_D.exists():
            for config in sorted(self.CONFIG_D.glob("*.yaml")):
                # Replace dots with underscores for ID - dots are invalid in Textual IDs
                safe_id = config.stem.replace(".", "_")
                active_list.append(ListItem(Label(config.name), id=f"active-{safe_id}"))

    async def on_list_view_selected(self, event: ListView.Selected):
        """Handle list selection"""
        if event.item:
            item_id = event.item.id or ""
            # Get the actual filename from the Label (first child)
            label = event.item.query_one(Label)
            name = str(label.renderable) if label else ""

            if item_id.startswith("avail-"):
                path = self.AVAILABLE_D / name
            elif item_id.startswith("active-"):
                path = self.CONFIG_D / name
            else:
                return

            # Show preview
            preview = self.query_one("#cfg-preview", Log)
            preview.clear()
            try:
                content = path.read_text()
                lines = content.split('\n')[:30]
                preview.write('\n'.join(lines))
                if len(content.split('\n')) > 30:
                    preview.write("\n... (truncated)")
            except Exception as e:
                preview.write(f"Error reading file: {e}")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        preview = self.query_one("#cfg-preview", Log)

        if button_id == "cfg-refresh":
            self.refresh_lists()

        elif button_id == "cfg-activate":
            available_list = self.query_one("#available-list", ListView)
            if available_list.highlighted_child:
                # Get actual filename from Label (IDs have dots replaced with underscores)
                label = available_list.highlighted_child.query_one(Label)
                name = str(label.renderable) if label else ""
                if name:
                    await self.activate_config(name)

        elif button_id == "cfg-deactivate":
            active_list = self.query_one("#active-list", ListView)
            if active_list.highlighted_child:
                # Get actual filename from Label (IDs have dots replaced with underscores)
                label = active_list.highlighted_child.query_one(Label)
                name = str(label.renderable) if label else ""
                if name:
                    await self.deactivate_config(name)

        elif button_id == "cfg-edit":
            await self.edit_selected()

        elif button_id == "cfg-main":
            await self.edit_main_config()

        elif button_id == "cfg-apply":
            await self.apply_changes()

    async def activate_config(self, name: str):
        """Activate a configuration"""
        import shutil
        preview = self.query_one("#cfg-preview", Log)

        try:
            src = self.AVAILABLE_D / name
            dst = self.CONFIG_D / name

            self.CONFIG_D.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

            preview.write(f"[green]Activated: {name}[/green]")
            self.refresh_lists()

        except Exception as e:
            preview.write(f"[red]Error: {e}[/red]")

    async def deactivate_config(self, name: str):
        """Deactivate a configuration"""
        preview = self.query_one("#cfg-preview", Log)

        try:
            path = self.CONFIG_D / name
            path.unlink()

            preview.write(f"[yellow]Deactivated: {name}[/yellow]")
            self.refresh_lists()

        except Exception as e:
            preview.write(f"[red]Error: {e}[/red]")

    async def edit_selected(self):
        """Edit selected config with nano"""
        available_list = self.query_one("#available-list", ListView)
        active_list = self.query_one("#active-list", ListView)

        path = None
        if available_list.highlighted_child:
            # Get actual filename from Label (IDs have dots replaced with underscores)
            label = available_list.highlighted_child.query_one(Label)
            name = str(label.renderable) if label else ""
            if name:
                path = self.AVAILABLE_D / name
        elif active_list.highlighted_child:
            # Get actual filename from Label (IDs have dots replaced with underscores)
            label = active_list.highlighted_child.query_one(Label)
            name = str(label.renderable) if label else ""
            if name:
                path = self.CONFIG_D / name

        if path:
            # Suspend TUI and run nano
            self.app.suspend()
            subprocess.run(['nano', str(path)])  # Interactive - no timeout
            self.app.resume()
            self.refresh_lists()

    async def edit_main_config(self):
        """Edit main config.yaml"""
        config_path = self.CONFIG_BASE / "config.yaml"
        preview = self.query_one("#cfg-preview", Log)

        if not config_path.exists():
            # Create basic config
            try:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text("""# Meshtasticd Configuration
Lora:
  Module: sx1262
  CS: 21
  IRQ: 16
  Busy: 20
  Reset: 18

Logging:
  LogLevel: info

Webserver:
  Port: 443
""")
                preview.write("[green]Created basic config.yaml[/green]")
            except Exception as e:
                preview.write(f"[red]Error creating config: {e}[/red]")
                return

        # Suspend TUI and run nano
        self.app.suspend()
        subprocess.run(['nano', str(config_path)])  # Interactive - no timeout
        self.app.resume()

    @work
    async def apply_changes(self):
        """Apply changes and restart service"""
        preview = self.query_one("#cfg-preview", Log)
        preview.write("[yellow]Applying changes...[/yellow]")

        try:
            # Daemon reload
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'daemon-reload',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await result.communicate()

            # Restart service
            result = await asyncio.create_subprocess_exec(
                'systemctl', 'restart', 'meshtasticd',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()

            if result.returncode == 0:
                preview.write("[green]Configuration applied - service restarted[/green]")
            else:
                preview.write(f"[red]Error: {stderr.decode()}[/red]")

        except Exception as e:
            preview.write(f"[red]Error: {e}[/red]")


class CLIPane(Container):
    """Meshtastic CLI commands pane"""

    def compose(self) -> ComposeResult:
        yield Static("# Meshtastic CLI", classes="title")
        yield Rule()

        yield Static("## Connection", classes="section-title")
        with Horizontal(classes="input-row"):
            yield Static("Host:", classes="input-label")
            yield Input("127.0.0.1", id="cli-host")

        yield Static("## Quick Commands", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("--info", id="cli-info")
            yield Button("--nodes", id="cli-nodes")
            yield Button("--get all", id="cli-getall")
            yield Button("--help", id="cli-help")

        yield Static("## Custom Command", classes="section-title")
        with Horizontal(classes="input-row"):
            yield Static("meshtastic", classes="input-label")
            yield Input("--info", id="cli-custom")
            yield Button("Run", id="cli-run", variant="primary")

        yield Static("## Output", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Clear", id="cli-clear")

        yield Log(id="cli-output", classes="log-panel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        logger.info(f"[CLI] Button pressed: {button_id}")
        output = self.query_one("#cli-output", Log)
        host = self.query_one("#cli-host", Input).value

        cmd_map = {
            "cli-info": "--info",
            "cli-nodes": "--nodes",
            "cli-getall": "--get all",
            "cli-help": "--help",
        }

        if button_id == "cli-clear":
            output.clear()
            return

        if button_id == "cli-run":
            custom = self.query_one("#cli-custom", Input).value
            try:
                # Use shlex for proper command-line parsing (handles quotes, escapes)
                args = shlex.split(custom)
            except ValueError as e:
                output.write(f"[red]Invalid command syntax: {e}[/red]")
                return
        elif button_id in cmd_map:
            args = shlex.split(cmd_map[button_id])
        else:
            return

        self.run_meshtastic(host, args, output)

    @work
    async def run_meshtastic(self, host: str, args: list, output: Log):
        """Run meshtastic command"""
        # Find meshtastic CLI
        cli_path = self._find_meshtastic_cli()
        if not cli_path:
            output.write("[red]meshtastic CLI not found. Install with:[/red]")
            output.write("sudo apt install pipx && pipx install 'meshtastic[cli]'")
            return

        cmd = [cli_path, '--host', host] + args
        output.write(f"$ {' '.join(cmd)}\n")

        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()

            if stdout:
                output.write(stdout.decode())
            if stderr:
                output.write(f"[red]{stderr.decode()}[/red]")

        except FileNotFoundError:
            output.write("[red]meshtastic CLI not found. Install with:[/red]")
            output.write("sudo apt install pipx && pipx install 'meshtastic[cli]'")
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    def _find_meshtastic_cli(self):
        """Find the meshtastic CLI executable - uses centralized utils.cli"""
        try:
            from utils.cli import find_meshtastic_cli
            return find_meshtastic_cli()
        except ImportError:
            import shutil
            return shutil.which('meshtastic')


class ToolsPane(Container):
    """System Tools pane - Network, RF, MUDP"""

    def compose(self) -> ComposeResult:
        yield Static("# System Tools", classes="title")
        yield Rule()

        yield Static("## Network Tools", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Ping Test", id="tool-ping")
            yield Button("Port 4403", id="tool-port")
            yield Button("Interfaces", id="tool-ifaces")
            yield Button("Find Devices", id="tool-scan")

        yield Static("## Network Diagnostics", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("UDP Listeners", id="tool-udp")
            yield Button("TCP Listeners", id="tool-tcp")
            yield Button("RNS Ports", id="tool-rns-ports")
            yield Button("Mesh Ports", id="tool-mesh-ports")
        with Horizontal(classes="button-row"):
            yield Button("Kill Clients", id="tool-kill-clients", variant="error")
            yield Button("Stop RNS", id="tool-stop-rns", variant="error")
            yield Button("Full Diag", id="tool-full-diag", variant="primary")

        yield Static("## RF Tools", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("LoRa Presets", id="tool-presets")
            yield Button("Detect Radio", id="tool-radio")
            yield Button("SPI/GPIO", id="tool-spi")

        yield Static("## MUDP Tools", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("MUDP Status", id="tool-mudp-status")
            yield Button("Install MUDP", id="tool-mudp-install")
            yield Button("Multicast Test", id="tool-multicast")

        yield Static("## Output", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Clear", id="tool-clear")
            yield Button("Refresh", id="tool-refresh")

        yield Log(id="tool-output", classes="log-panel")

    async def on_mount(self):
        """Called when widget is mounted"""
        self._refresh_status()

    def _refresh_status(self):
        """Refresh tool status"""
        output = self.query_one("#tool-output", Log)
        output.clear()
        output.write("[cyan]System Tools Ready[/cyan]")
        output.write("Select a tool above to run diagnostics")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        logger.info(f"[Tools] Button pressed: {button_id}")
        output = self.query_one("#tool-output", Log)

        if button_id == "tool-clear":
            output.clear()
            return
        elif button_id == "tool-refresh":
            self._refresh_status()
            return
        elif button_id == "tool-ping":
            self._run_ping(output)
        elif button_id == "tool-port":
            self._test_port(output)
        elif button_id == "tool-ifaces":
            self._show_interfaces(output)
        elif button_id == "tool-scan":
            self._scan_devices(output)
        elif button_id == "tool-presets":
            self._show_presets(output)
        elif button_id == "tool-radio":
            self._detect_radio(output)
        elif button_id == "tool-spi":
            self._check_spi(output)
        elif button_id == "tool-mudp-status":
            self._mudp_status(output)
        elif button_id == "tool-mudp-install":
            self._install_mudp(output)
        elif button_id == "tool-multicast":
            self._test_multicast(output)
        # Network Diagnostics
        elif button_id == "tool-udp":
            self._show_udp_listeners(output)
        elif button_id == "tool-tcp":
            self._show_tcp_listeners(output)
        elif button_id == "tool-rns-ports":
            self._check_rns_ports(output)
        elif button_id == "tool-mesh-ports":
            self._check_mesh_ports(output)
        elif button_id == "tool-kill-clients":
            self._kill_clients(output)
        elif button_id == "tool-stop-rns":
            self._stop_rns(output)
        elif button_id == "tool-full-diag":
            self._full_diagnostics(output)

    @work
    async def _run_ping(self, output: Log):
        """Run ping test"""
        output.write("\n[cyan]Pinging 8.8.8.8...[/cyan]")
        try:
            result = await asyncio.create_subprocess_exec(
                'ping', '-c', '4', '8.8.8.8',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            output.write(stdout.decode())
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    @work
    async def _test_port(self, output: Log):
        """Test Meshtastic TCP port"""
        import socket
        output.write("\n[cyan]Testing port 4403...[/cyan]")
        for host in ['127.0.0.1', 'localhost']:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex((host, 4403))
                sock.close()
                status = "[green]OPEN[/green]" if result == 0 else "[red]CLOSED[/red]"
                output.write(f"  {host}:4403 - {status}")
            except Exception as e:
                output.write(f"  {host}:4403 - [red]Error: {e}[/red]")

    @work
    async def _show_interfaces(self, output: Log):
        """Show network interfaces"""
        output.write("\n[cyan]Network Interfaces:[/cyan]")
        try:
            result = await asyncio.create_subprocess_exec(
                'ip', '-br', 'addr',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            output.write(stdout.decode())
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    @work
    async def _scan_devices(self, output: Log):
        """Scan for Meshtastic devices - concurrent scan for speed"""
        import socket
        output.write("\n[cyan]Scanning for Meshtastic devices (port 4403)...[/cyan]")
        logger.info("Starting device scan")

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            base = '.'.join(local_ip.split('.')[:3])

            found = []

            async def check_host(ip: str):
                """Check single host - async"""
                try:
                    # Use asyncio for non-blocking connect
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip, 4403),
                        timeout=0.5
                    )
                    writer.close()
                    await writer.wait_closed()
                    return ip
                except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
                    return None

            # Scan in batches of 50 for responsiveness
            output.write(f"  Scanning {base}.1-254 ...")
            logger.debug(f"Scanning subnet {base}.0/24")

            batch_size = 50
            for batch_start in range(1, 255, batch_size):
                batch_end = min(batch_start + batch_size, 255)
                tasks = [
                    check_host(f"{base}.{i}")
                    for i in range(batch_start, batch_end)
                ]
                results = await asyncio.gather(*tasks)

                for ip in results:
                    if ip:
                        output.write(f"  [green]Found: {ip}:4403[/green]")
                        found.append(ip)
                        logger.info(f"Found Meshtastic device at {ip}:4403")

                # Update progress
                progress = (batch_end / 254) * 100
                output.write(f"  Progress: {progress:.0f}%")

            output.write(f"\n[cyan]Scan complete. Found {len(found)} device(s)[/cyan]")
            logger.info(f"Scan complete: found {len(found)} devices")

        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")
            logger.error(f"Device scan error: {e}")

    def _show_presets(self, output: Log):
        """Show LoRa presets"""
        output.write("\n[cyan]LoRa Modem Presets:[/cyan]")
        presets = [
            ("SHORT_TURBO", "21875 bps", "-108 dBm", "~3 km"),
            ("SHORT_FAST", "10937 bps", "-111 dBm", "~5 km"),
            ("MEDIUM_FAST", "3516 bps", "-117 dBm", "~12 km"),
            ("LONG_FAST", "1066 bps", "-123 dBm", "~30 km"),
            ("LONG_SLOW", "293 bps", "-129 dBm", "~80 km"),
            ("VERY_LONG_SLOW", "146 bps", "-132 dBm", "~120 km"),
        ]
        for name, rate, sens, range_ in presets:
            output.write(f"  {name}: {rate}, {sens}, {range_}")

    @work
    async def _detect_radio(self, output: Log):
        """Detect LoRa radio"""
        from pathlib import Path
        output.write("\n[cyan]Detecting LoRa Radio...[/cyan]")
        spi = list(Path('/dev').glob('spidev*'))
        if spi:
            output.write(f"  [green]SPI devices: {len(spi)}[/green]")
            for d in spi:
                output.write(f"    {d}")
        else:
            output.write("  [red]No SPI devices found[/red]")

    @work
    async def _check_spi(self, output: Log):
        """Check SPI/GPIO status"""
        from pathlib import Path
        output.write("\n[cyan]SPI/GPIO Status:[/cyan]")

        spi = Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists()
        output.write(f"  SPI: {'[green]Enabled[/green]' if spi else '[red]Disabled[/red]'}")

        i2c = Path('/dev/i2c-1').exists()
        output.write(f"  I2C: {'[green]Enabled[/green]' if i2c else '[yellow]Disabled[/yellow]'}")

        gpio = Path('/sys/class/gpio').exists()
        output.write(f"  GPIO: {'[green]Available[/green]' if gpio else '[red]Not available[/red]'}")

    @work
    async def _mudp_status(self, output: Log):
        """Check MUDP status"""
        output.write("\n[cyan]MUDP Status:[/cyan]")
        try:
            result = await asyncio.create_subprocess_exec(
                'pip', 'show', 'mudp',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            if result.returncode == 0:
                for line in stdout.decode().split('\n'):
                    if line.startswith(('Name:', 'Version:')):
                        output.write(f"  {line}")
                output.write("  [green]MUDP is installed[/green]")
            else:
                output.write("  [yellow]MUDP not installed[/yellow]")
                output.write("  Install with: pip install mudp")
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    @work
    async def _install_mudp(self, output: Log):
        """Install MUDP"""
        output.write("\n[cyan]Installing MUDP...[/cyan]")
        try:
            result = await asyncio.create_subprocess_exec(
                'pip', 'install', '--upgrade', '--break-system-packages', 'mudp',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            if result.returncode == 0:
                output.write("[green]MUDP installed successfully![/green]")
            else:
                output.write(f"[red]Install failed: {stderr.decode()}[/red]")
        except Exception as e:
            output.write(f"[red]Error: {e}[/red]")

    @work
    async def _test_multicast(self, output: Log):
        """Test multicast join"""
        import socket
        import struct
        output.write("\n[cyan]Testing multicast group 224.0.0.69...[/cyan]")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', 4403))
            mreq = struct.pack("4sl", socket.inet_aton("224.0.0.69"), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            output.write("  [green]Joined multicast group successfully[/green]")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            sock.close()
            output.write("  [green]Left multicast group[/green]")
        except OSError as e:
            if "Address already in use" in str(e):
                output.write("  [yellow]Port 4403 in use (meshtasticd running?) - OK[/yellow]")
            else:
                output.write(f"  [red]Error: {e}[/red]")
        except Exception as e:
            output.write(f"  [red]Error: {e}[/red]")

    # Network Diagnostics Methods
    def _parse_proc_net(self, protocol: str) -> list:
        """Parse /proc/net/udp or /proc/net/tcp"""
        results = []
        try:
            with open(f"/proc/net/{protocol}", 'r') as f:
                lines = f.readlines()[1:]
            for line in lines:
                parts = line.split()
                if len(parts) >= 10:
                    addr_parts = parts[1].split(':')
                    try:
                        ip_int = int(addr_parts[0], 16)
                        ip_bytes = [(ip_int >> i) & 0xFF for i in (0, 8, 16, 24)]
                        ip_str = '.'.join(str(b) for b in ip_bytes)
                        port = int(addr_parts[1], 16)
                        state_names = {'01': 'ESTABLISHED', '0A': 'LISTEN', '06': 'TIME_WAIT'}
                        results.append({
                            'ip': ip_str, 'port': port,
                            'state': state_names.get(parts[3].upper(), parts[3])
                        })
                    except (ValueError, IndexError):
                        continue
        except (FileNotFoundError, PermissionError):
            pass
        return results

    @work
    async def _show_udp_listeners(self, output: Log):
        """Show UDP listeners"""
        output.write("\n[cyan]UDP Listeners[/cyan]")
        entries = self._parse_proc_net('udp')
        output.write(f"{'IP':>15} : {'Port':>5}")
        output.write("-" * 25)
        for e in entries:
            if e['port'] != 0:
                output.write(f"{e['ip']:>15} : {e['port']:>5}")
        output.write(f"\nTotal: {len([e for e in entries if e['port'] != 0])} sockets")

    @work
    async def _show_tcp_listeners(self, output: Log):
        """Show TCP listeners"""
        output.write("\n[cyan]TCP Listeners[/cyan]")
        entries = self._parse_proc_net('tcp')
        listen = [e for e in entries if e['state'] == 'LISTEN']
        output.write(f"{'IP':>15} : {'Port':>5}  State")
        output.write("-" * 35)
        for e in listen:
            output.write(f"{e['ip']:>15} : {e['port']:>5}  {e['state']}")
        output.write(f"\nTotal: {len(listen)} listening")

    @work
    async def _check_rns_ports(self, output: Log):
        """Check RNS port 29716"""
        output.write("\n[cyan]RNS Port Check (29716)[/cyan]")
        entries = self._parse_proc_net('udp')
        found = [e for e in entries if e['port'] == 29716]
        if found:
            output.write(f"  [red]✗ Port 29716 IN USE[/red]")
        else:
            output.write(f"  [green]✓ Port 29716 FREE[/green]")
        # Check processes
        try:
            result = await asyncio.create_subprocess_exec(
                'pgrep', '-a', '-f', 'rnsd|nomadnet|lxmf',
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            if stdout:
                output.write("\nRNS Processes:")
                for line in stdout.decode().strip().split('\n'):
                    output.write(f"  {line}")
            else:
                output.write("\n  No RNS processes running")
        except Exception:
            pass

    @work
    async def _check_mesh_ports(self, output: Log):
        """Check Meshtastic ports"""
        output.write("\n[cyan]Meshtastic Port Check[/cyan]")
        tcp = self._parse_proc_net('tcp')
        for port in [4403, 9443]:
            found = [e for e in tcp if e['port'] == port and e['state'] == 'LISTEN']
            if found:
                output.write(f"  [green]✓ TCP {port} LISTENING[/green]")
            else:
                output.write(f"  [red]✗ TCP {port} NOT listening[/red]")

    @work
    async def _kill_clients(self, output: Log):
        """Kill competing clients"""
        output.write("\n[cyan]Killing competing clients...[/cyan]")
        killed = []
        for pattern in ['nomadnet', 'lxmf']:
            try:
                result = await asyncio.create_subprocess_exec(
                    'pkill', '-9', '-f', pattern,
                    stdout=asyncio.subprocess.PIPE
                )
                await result.communicate()
                if result.returncode == 0:
                    killed.append(pattern)
            except Exception:
                pass
        if killed:
            output.write(f"  [green]Killed: {', '.join(killed)}[/green]")
        else:
            output.write("  [yellow]No clients found[/yellow]")

    @work
    async def _stop_rns(self, output: Log):
        """Stop all RNS processes"""
        output.write("\n[cyan]Stopping all RNS processes...[/cyan]")
        killed = []
        for proc in ['rnsd', 'nomadnet', 'lxmf', 'RNS']:
            try:
                result = await asyncio.create_subprocess_exec(
                    'pkill', '-9', '-f', proc,
                    stdout=asyncio.subprocess.PIPE
                )
                await result.communicate()
                if result.returncode == 0:
                    killed.append(proc)
            except Exception:
                pass
        if killed:
            output.write(f"  [green]Killed: {', '.join(killed)}[/green]")
        else:
            output.write("  [yellow]No RNS processes found[/yellow]")

    @work
    async def _full_diagnostics(self, output: Log):
        """Run full network diagnostics"""
        output.write("\n[cyan]" + "=" * 40 + "[/cyan]")
        output.write("[cyan]FULL NETWORK DIAGNOSTICS[/cyan]")
        output.write("[cyan]" + "=" * 40 + "[/cyan]\n")
        await self._show_udp_listeners(output)
        await self._show_tcp_listeners(output)
        await self._check_rns_ports(output)
        await self._check_mesh_ports(output)
        output.write("\n[cyan]" + "=" * 40 + "[/cyan]")
        output.write("[green]DIAGNOSTICS COMPLETE[/green]")


class MeshtasticdTUI(App):
    """Meshtasticd Manager TUI Application"""

    CSS = """
    Screen {
        background: $surface;
    }

    .title {
        text-style: bold;
        color: $primary;
        padding: 1;
    }

    .subtitle {
        color: $text-muted;
        padding-left: 1;
    }

    .section-title {
        text-style: bold;
        margin-top: 1;
        padding-left: 1;
    }

    .card {
        border: solid $primary;
        padding: 1;
        margin: 1;
        width: 1fr;
    }

    .card-title {
        text-style: bold;
    }

    .card-value {
        margin-top: 1;
    }

    .card-detail {
        color: $text-muted;
    }

    .status-cards {
        height: auto;
    }

    .button-row {
        padding: 1;
        height: auto;
    }

    .button-row Button {
        margin-right: 1;
    }

    .input-row {
        padding: 1;
        height: auto;
    }

    .input-label {
        width: 10;
        padding-top: 1;
    }

    .input-row Input {
        width: 1fr;
    }

    .list-container {
        width: 1fr;
        height: 100%;
        border: solid $primary;
        margin: 1;
    }

    .list-title {
        text-style: bold;
        text-align: center;
        background: $primary;
        color: $text;
    }

    .log-panel {
        height: 1fr;
        min-height: 10;
        max-height: 100%;
        margin: 1;
        border: solid $surface-lighten-2;
        overflow-y: auto;
    }

    Log {
        scrollbar-gutter: stable;
    }

    TabPane {
        height: 100%;
    }

    TabPane > Container {
        height: 100%;
        overflow-y: auto;
    }

    TabbedContent {
        padding: 0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "switch_tab('dashboard')", "Dashboard"),
        Binding("s", "switch_tab('service')", "Service"),
        Binding("c", "switch_tab('config')", "Config"),
        Binding("m", "switch_tab('cli')", "CLI"),
        Binding("t", "switch_tab('tools')", "Tools"),
        Binding("r", "refresh", "Refresh"),
    ]

    TITLE = f"Meshtasticd Manager v{__version__}"

    def compose(self) -> ComposeResult:
        yield Header()

        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                yield DashboardPane()
            with TabPane("Service", id="service"):
                yield ServicePane()
            with TabPane("Config", id="config"):
                yield ConfigPane()
            with TabPane("CLI", id="cli"):
                yield CLIPane()
            with TabPane("Tools", id="tools"):
                yield ToolsPane()

        yield Footer()

    def action_switch_tab(self, tab_id: str):
        """Switch to a specific tab"""
        tabbed = self.query_one(TabbedContent)
        tabbed.active = tab_id

    def action_refresh(self):
        """Refresh current view"""
        tabbed = self.query_one(TabbedContent)
        active = tabbed.active

        if active == "dashboard":
            self.query_one(DashboardPane).refresh_data()
        elif active == "service":
            self.query_one(ServicePane).refresh_status()
        elif active == "config":
            self.query_one(ConfigPane).refresh_lists()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Global button handler"""
        logger.debug(f"[App] Global button handler: {event.button.id}")
        if event.button.id == "refresh-dashboard":
            self.query_one(DashboardPane).refresh_data()

    async def on_mount(self) -> None:
        """Called when app is mounted"""
        logger.info("MeshForge TUI started - logging to /tmp/meshforge-tui.log")


def main():
    """Main entry point"""
    # Check root
    if os.geteuid() != 0:
        print("This application requires root privileges.")
        print("Please run with: sudo python3 src/main_tui.py")
        sys.exit(1)

    # Initialize config
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.env_config import initialize_config
    initialize_config()

    # Run app
    app = MeshtasticdTUI()
    app.run()


if __name__ == '__main__':
    main()
