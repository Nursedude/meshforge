"""CLI utilities and helpers"""

import os
import shutil
import subprocess
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.panel import Panel
import time

console = Console()


def find_meshtastic_cli():
    """Find the meshtastic CLI executable

    Checks multiple locations where meshtastic CLI might be installed:
    - System PATH (via shutil.which)
    - pipx installation paths (/root/.local/bin, /home/pi/.local/bin, ~/.local/bin)
    - Common installation locations

    Returns:
        str: Full path to meshtastic CLI, or None if not found
    """
    # First check if it's in PATH
    cli_path = shutil.which('meshtastic')
    if cli_path:
        return cli_path

    # Check common pipx installation paths
    pipx_paths = [
        '/root/.local/bin/meshtastic',
        '/home/pi/.local/bin/meshtastic',
        os.path.expanduser('~/.local/bin/meshtastic'),
    ]

    # Also check for the original user's home if running with sudo
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user:
        pipx_paths.append(f'/home/{sudo_user}/.local/bin/meshtastic')

    for path in pipx_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    return None


def is_meshtastic_cli_installed():
    """Check if meshtastic CLI is installed

    Returns:
        bool: True if CLI is found
    """
    return find_meshtastic_cli() is not None


def run_meshtastic_command(args, connection_args=None, capture=True, timeout=60):
    """Run a meshtastic CLI command

    Args:
        args: List of command arguments (without 'meshtastic')
        connection_args: Optional connection arguments (--host, --port, etc.)
        capture: If True, capture output; if False, run interactively
        timeout: Command timeout in seconds

    Returns:
        subprocess.CompletedProcess if capture=True, None otherwise
        Returns None on error or if CLI not found
    """
    cli_path = find_meshtastic_cli()
    if not cli_path:
        console.print("[red]Meshtastic CLI not found![/red]")
        console.print("[cyan]Install with: sudo apt install pipx && pipx install 'meshtastic[cli]'[/cyan]")
        return None

    full_args = [cli_path]
    if connection_args:
        full_args.extend(connection_args)
    full_args.extend(args)

    try:
        if capture:
            return subprocess.run(full_args, capture_output=True, text=True, timeout=timeout)
        else:
            subprocess.run(full_args, timeout=timeout)
            return None
    except subprocess.TimeoutExpired:
        console.print("[red]Command timed out[/red]")
        return None
    except Exception as e:
        console.print(f"[red]Error running meshtastic command: {e}[/red]")
        return None


def get_meshtastic_install_instructions():
    """Get installation instructions for meshtastic CLI

    Returns:
        str: Installation instructions
    """
    return "sudo apt install pipx && pipx install 'meshtastic[cli]' && pipx ensurepath"


def create_progress():
    """Create a progress bar"""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    )


def show_success(message):
    """Show success message"""
    console.print(f"[bold green]✓[/bold green] {message}")


def show_error(message):
    """Show error message"""
    console.print(f"[bold red]✗[/bold red] {message}")


def show_warning(message):
    """Show warning message"""
    console.print(f"[bold yellow]⚠[/bold yellow] {message}")


def show_info(message):
    """Show info message"""
    console.print(f"[cyan]ℹ[/cyan] {message}")


def prompt_choice(message, choices, default=None):
    """Prompt user for a choice"""
    return Prompt.ask(message, choices=choices, default=default)


def prompt_confirm(message, default=True):
    """Prompt user for confirmation"""
    return Confirm.ask(message, default=default)


def show_table(title, headers, rows):
    """Display a table"""
    table = Table(title=title, show_header=True, header_style="bold magenta")

    for header in headers:
        table.add_column(header, style="cyan")

    for row in rows:
        table.add_row(*[str(item) for item in row])

    console.print(table)


def show_panel(content, title=None, style="cyan"):
    """Display a panel"""
    console.print(Panel(content, title=title, border_style=style))


def run_meshtastic_async(args, callback, host='localhost', timeout=30):
    """Run meshtastic CLI command asynchronously with callback.

    Designed for GTK applications where CLI commands should run in
    background threads. Uses common.run_cli_async_gtk if available.

    Args:
        args: Command arguments (without meshtastic prefix)
        callback: Function called with (success: bool, stdout: str, stderr: str)
        host: Meshtastic host (default: localhost)
        timeout: Command timeout in seconds

    Returns:
        The started thread
    """
    try:
        from utils.common import run_cli_async_gtk
        cli_path = find_meshtastic_cli()
        return run_cli_async_gtk(args, callback, cli_path=cli_path, host=host, timeout=timeout)
    except ImportError:
        # Fallback implementation
        import threading

        def do_run():
            cli_path = find_meshtastic_cli()
            if not cli_path:
                callback(False, "", "Meshtastic CLI not found")
                return

            cmd = [cli_path, '--host', host] + args
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                callback(result.returncode == 0, result.stdout, result.stderr)
            except subprocess.TimeoutExpired:
                callback(False, "", f"Command timed out after {timeout}s")
            except Exception as e:
                callback(False, "", str(e))

        thread = threading.Thread(target=do_run, daemon=True)
        thread.start()
        return thread
