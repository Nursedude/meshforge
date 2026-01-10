#!/usr/bin/env python3
"""
Minimal Textual TUI Test

Tests basic Textual functionality:
- TabbedContent structure
- ScrollableContainer with widgets
- Static widget updates
- Log widget writes
- on_mount() behavior
"""

import logging
from datetime import datetime
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('/tmp/test-tui-minimal.log'),
    ]
)
logger = logging.getLogger('test-tui')

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.widgets import Header, Footer, Static, Button, Log, TabbedContent, TabPane, Rule
from textual.binding import Binding
from textual import work


class DashboardPane(Container):
    """Minimal dashboard for testing - use Container not ScrollableContainer
    to avoid height: 1fr CSS conflicts"""

    def compose(self) -> ComposeResult:
        yield Static("# Minimal TUI Test Dashboard", classes="title")
        yield Rule()

        # Status card
        with Container(classes="card"):
            yield Static("[TEST] Test Status", classes="card-title")
            yield Static("Initializing...", id="test-status", classes="card-value")
            yield Static("", id="test-detail", classes="card-detail")

        # Action buttons
        yield Static("## Actions", classes="section-title")
        with Horizontal(classes="button-row"):
            yield Button("Update Status", id="btn-update", variant="primary")
            yield Button("Write Log", id="btn-log", variant="success")
            yield Button("Clear Log", id="btn-clear", variant="warning")

        # Log section
        yield Static("## Activity Log", classes="section-title")
        yield Log(id="activity-log", classes="log-panel")

    def on_mount(self):
        """Called when widget is mounted"""
        logger.info("DashboardPane.on_mount() called")
        # Use call_later to schedule refresh after mount completes
        self.call_later(self._do_initial_update)

    def _do_initial_update(self):
        """Initial update after mount"""
        logger.info("_do_initial_update() called")
        self.update_widgets()

    @work(exclusive=True)
    async def update_widgets(self):
        """Update all widgets - test async worker"""
        logger.info("update_widgets() worker started")

        try:
            # Get widgets
            status_widget = self.query_one("#test-status", Static)
            detail_widget = self.query_one("#test-detail", Static)
            log = self.query_one("#activity-log", Log)
            logger.info("Successfully queried widgets")

            # Update status widget
            timestamp = datetime.now().strftime('%H:%M:%S')
            status_widget.update(f"[green]● Ready[/green]")
            detail_widget.update(f"Last updated: {timestamp}")
            logger.info("Updated status widgets")

            # Write to log
            log.write_line("[cyan]TUI Initialized[/cyan]")
            log.write_line(f"[green]✓[/green] Widgets mounted successfully")
            log.write_line(f"[green]✓[/green] Static widget updates working")
            log.write_line(f"[green]✓[/green] Log widget writes working")
            log.write_line(f"Timestamp: {timestamp}")
            logger.info("Wrote to log widget")

            # Force UI refresh
            self.app.refresh()
            logger.info("Called app.refresh()")

        except Exception as e:
            logger.error(f"Error in update_widgets: {e}")
            import traceback
            logger.error(traceback.format_exc())

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        button_id = event.button.id
        logger.info(f"Button pressed: {button_id}")
        log = self.query_one("#activity-log", Log)

        if button_id == "btn-update":
            log.write_line("[yellow]Updating status...[/yellow]")
            self.update_widgets()

        elif button_id == "btn-log":
            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            log.write_line(f"[cyan]Manual log entry at {timestamp}[/cyan]")

        elif button_id == "btn-clear":
            log.clear()
            log.write_line("[yellow]Log cleared[/yellow]")


class InfoPane(Container):
    """Simple info pane for testing tabs"""

    def compose(self) -> ComposeResult:
        yield Static("# Info Pane", classes="title")
        yield Rule()
        yield Static("This is a minimal test of the Textual TUI framework.", classes="info-text")
        yield Static("")
        yield Static("Testing:", classes="section-title")
        yield Static("  • TabbedContent structure", classes="info-text")
        yield Static("  • ScrollableContainer behavior", classes="info-text")
        yield Static("  • Static widget updates", classes="info-text")
        yield Static("  • Log widget writes", classes="info-text")
        yield Static("  • @work decorator (async workers)", classes="info-text")
        yield Static("  • on_mount() lifecycle", classes="info-text")
        yield Static("")
        yield Static("Logs: /tmp/test-tui-minimal.log", classes="info-text")


class MinimalTUI(App):
    """Minimal TUI test application"""

    CSS = """
    Screen {
        background: $surface;
    }

    .title {
        text-style: bold;
        color: $primary;
        padding: 1;
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

    .button-row {
        padding: 1;
        height: auto;
    }

    .button-row Button {
        margin-right: 1;
    }

    .log-panel {
        height: 1fr;
        min-height: 10;
        max-height: 100%;
        margin: 1;
        border: solid $surface-lighten-2;
        overflow-y: auto;
    }

    .info-text {
        padding-left: 2;
        padding-top: 0;
        padding-bottom: 0;
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
        Binding("i", "switch_tab('info')", "Info"),
    ]

    TITLE = "Minimal Textual TUI Test"

    def compose(self) -> ComposeResult:
        yield Header()

        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                yield DashboardPane()
            with TabPane("Info", id="info"):
                yield InfoPane()

        yield Footer()

    def action_switch_tab(self, tab_id: str):
        """Switch to a specific tab"""
        tabbed = self.query_one(TabbedContent)
        tabbed.active = tab_id

    async def on_mount(self) -> None:
        """Called when app is mounted"""
        logger.info("=== Minimal TUI Test Started ===")
        logger.info("Logging to: /tmp/test-tui-minimal.log")


def main():
    """Main entry point"""
    logger.info("Starting minimal TUI test")
    app = MinimalTUI()
    logger.info("App instance created, calling run()")
    app.run()
    logger.info("App.run() completed")


if __name__ == '__main__':
    main()
