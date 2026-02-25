"""CLI utilities and helpers"""

import os
import shutil
import subprocess
import time
from pathlib import Path

# rich is an external/optional dependency — guard the import
try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    from rich.panel import Panel
    console = Console()
except ImportError:
    Console = Progress = SpinnerColumn = TextColumn = BarColumn = None
    Prompt = Confirm = Table = Panel = None
    console = None


def find_meshtastic_cli():
    """Find the meshtastic CLI executable

    Checks multiple locations where meshtastic CLI might be installed:
    - System PATH (via shutil.which)
    - SUDO_USER's ~/.local/bin (pip/pipx install as user, run with sudo)
    - /root/.local/bin (pip install as root)
    - All /home/*/.local/bin directories (fallback scan)
    - /usr/local/bin (system-wide pip install)

    Returns:
        str: Full path to meshtastic CLI, or None if not found
    """
    # First check if it's in PATH
    cli_path = shutil.which('meshtastic')
    if cli_path:
        return cli_path

    # Priority: check the real user's home first (handles sudo case)
    # When running with sudo, the CLI is usually in the invoking user's ~/.local/bin
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        user_path = f'/home/{sudo_user}/.local/bin/meshtastic'
        if os.path.isfile(user_path) and os.access(user_path, os.X_OK):
            return user_path

    # Check common known locations
    from utils.paths import get_real_user_home
    known_paths = [
        '/root/.local/bin/meshtastic',
        str(get_real_user_home() / '.local' / 'bin' / 'meshtastic'),
        '/usr/local/bin/meshtastic',
    ]

    for path in known_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # Fallback: scan all user home directories in /home/
    try:
        home_base = Path('/home')
        if home_base.is_dir():
            for user_dir in home_base.iterdir():
                if user_dir.is_dir():
                    candidate = user_dir / '.local' / 'bin' / 'meshtastic'
                    if candidate.is_file() and os.access(str(candidate), os.X_OK):
                        return str(candidate)
    except (PermissionError, OSError):
        pass

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

    NOTE: pipx install should NOT be run with sudo, as it installs to
    the user's ~/.local/bin which is inaccessible to other users.
    Only apt install needs sudo.

    Returns:
        str: Installation instructions
    """
    return "sudo apt install pipx && pipx install 'meshtastic[cli]' && eval \"$(pipx ensurepath)\""


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

    Args:
        args: Command arguments (without meshtastic prefix)
        callback: Function called with (success: bool, stdout: str, stderr: str)
        host: Meshtastic host (default: localhost)
        timeout: Command timeout in seconds

    Returns:
        The started thread
    """
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
