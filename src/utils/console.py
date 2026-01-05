"""
MeshForge Console Manager

Provides a singleton Rich Console instance for consistent output formatting
across the application. This reduces memory usage and ensures consistent
styling throughout the codebase.

Usage:
    from utils.console import console
    console.print("[green]Success![/green]")

Or for explicit access:
    from utils.console import get_console
    c = get_console()
"""

from rich.console import Console
from rich.theme import Theme
from typing import Optional
import threading

# Thread-safe singleton
_console: Optional[Console] = None
_lock = threading.Lock()

# MeshForge custom theme
MESHFORGE_THEME = Theme({
    "info": "cyan",
    "success": "green",
    "warning": "yellow",
    "error": "red bold",
    "heading": "bold magenta",
    "highlight": "bold cyan",
    "dim": "dim white",
    "mesh": "bold blue",
    "rf": "bold yellow",
    "node": "green",
    "callsign": "bold cyan",
})


def get_console(force_terminal: bool = None,
                no_color: bool = None,
                width: int = None) -> Console:
    """
    Get the singleton Console instance.

    Args:
        force_terminal: Force terminal mode (for testing)
        no_color: Disable color output
        width: Override console width

    Returns:
        The shared Console instance
    """
    global _console

    if _console is None:
        with _lock:
            # Double-check locking
            if _console is None:
                _console = Console(
                    theme=MESHFORGE_THEME,
                    force_terminal=force_terminal,
                    no_color=no_color,
                    width=width,
                    highlight=True,
                    markup=True,
                )

    return _console


def reset_console():
    """
    Reset the console singleton (useful for testing).
    """
    global _console
    with _lock:
        _console = None


# Default console instance for direct import
console = get_console()


# Convenience functions
def print_success(message: str):
    """Print a success message"""
    console.print(f"[success]✓ {message}[/success]")


def print_error(message: str):
    """Print an error message"""
    console.print(f"[error]✗ {message}[/error]")


def print_warning(message: str):
    """Print a warning message"""
    console.print(f"[warning]⚠ {message}[/warning]")


def print_info(message: str):
    """Print an info message"""
    console.print(f"[info]ℹ {message}[/info]")


def print_heading(message: str):
    """Print a heading"""
    console.print(f"\n[heading]{message}[/heading]")
    console.print("[dim]" + "─" * len(message) + "[/dim]")


def print_mesh_status(node_id: str, status: str, rssi: int = None):
    """Print mesh node status"""
    rssi_str = f" (RSSI: {rssi})" if rssi is not None else ""
    console.print(f"[mesh]⬡[/mesh] [node]{node_id}[/node]: {status}{rssi_str}")


def print_rf_info(frequency: float, power: float = None, sf: int = None):
    """Print RF information"""
    parts = [f"[rf]{frequency:.3f} MHz[/rf]"]
    if power is not None:
        parts.append(f"{power} dBm")
    if sf is not None:
        parts.append(f"SF{sf}")
    console.print(" | ".join(parts))
