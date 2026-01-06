"""Automatic Update Notifications for Meshtasticd"""

import os
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

console = Console()


class UpdateNotifier:
    """Manages automatic update checking and notifications"""

    def __init__(self):
        self.cache_dir = get_real_user_home() / '.cache' / 'meshtasticd-installer'
        self.cache_file = self.cache_dir / 'update_cache.json'
        self.check_interval_hours = 24  # Check once per day

    def _ensure_cache_dir(self):
        """Ensure cache directory exists"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _load_cache(self):
        """Load cached update information"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            from utils.logger import get_logger
            logger = get_logger()
            logger.debug(f"Could not load update cache: {e}")
        return {}

    def _save_cache(self, data):
        """Save update information to cache"""
        self._ensure_cache_dir()
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            from utils.logger import get_logger
            logger = get_logger()
            logger.warning(f"Could not save update cache: {e}")

    def should_check_updates(self):
        """Determine if we should check for updates"""
        cache = self._load_cache()
        last_check = cache.get('last_check')

        if not last_check:
            return True

        try:
            last_check_time = datetime.fromisoformat(last_check)
            time_since_check = datetime.now() - last_check_time
            return time_since_check > timedelta(hours=self.check_interval_hours)
        except Exception as e:
            from utils.logger import get_logger
            logger = get_logger()
            logger.debug(f"Error parsing last check time, will check for updates: {e}")
            return True

    def check_for_updates(self, force=False):
        """Check for available updates"""
        if not force and not self.should_check_updates():
            # Return cached result
            cache = self._load_cache()
            return cache.get('update_info')

        from installer.version import VersionManager
        vm = VersionManager()

        update_info = vm.check_for_updates()

        # Cache the result
        cache = {
            'last_check': datetime.now().isoformat(),
            'update_info': update_info
        }
        self._save_cache(cache)

        return update_info

    def show_update_notification(self, update_info=None):
        """Display update notification if available"""
        if update_info is None:
            update_info = self.check_for_updates()

        if not update_info:
            return False

        if not update_info.get('update_available'):
            return False

        current = update_info.get('current', 'Unknown')
        latest = update_info.get('latest', 'Unknown')

        notification = f"""[bold yellow]Update Available![/bold yellow]

Current version: [cyan]{current}[/cyan]
Latest version:  [green]{latest}[/green]

Run 'Update meshtasticd' from the main menu to upgrade."""

        console.print(Panel(
            notification,
            title="[bold yellow]Update Notification[/bold yellow]",
            border_style="yellow"
        ))
        console.print()

        return True

    def startup_update_check(self):
        """Perform update check on startup (non-blocking)"""
        try:
            update_info = self.check_for_updates()
            if update_info and update_info.get('update_available'):
                self.show_update_notification(update_info)
                return True
        except Exception as e:
            # Don't let update check failures interrupt startup
            from utils.logger import get_logger
            logger = get_logger()
            logger.debug(f"Startup update check failed: {e}")
        return False

    def get_update_status_line(self):
        """Get a compact update status for display in menus"""
        cache = self._load_cache()
        update_info = cache.get('update_info')

        if not update_info:
            return "[dim]Update status: Unknown[/dim]"

        if update_info.get('update_available'):
            return f"[yellow]Update available: {update_info.get('latest')}[/yellow]"
        else:
            return f"[green]Up to date: {update_info.get('current')}[/green]"

    def clear_cache(self):
        """Clear the update cache to force a fresh check"""
        try:
            if self.cache_file.exists():
                self.cache_file.unlink()
            return True
        except Exception as e:
            from utils.logger import get_logger
            logger = get_logger()
            logger.warning(f"Could not clear update cache: {e}")
            return False

    def configure_notifications(self):
        """Configure update notification settings"""
        console.print("\n[bold cyan]Update Notification Settings[/bold cyan]\n")

        cache = self._load_cache()
        settings = cache.get('settings', {})

        # Enable/disable notifications
        settings['enabled'] = Confirm.ask(
            "Enable automatic update notifications?",
            default=settings.get('enabled', True)
        )

        if settings['enabled']:
            # Check interval
            console.print("\n[cyan]How often should we check for updates?[/cyan]")
            console.print("1. Every startup")
            console.print("2. Daily")
            console.print("3. Weekly")

            from rich.prompt import Prompt
            choice = Prompt.ask("Select interval", choices=["1", "2", "3"], default="2")

            interval_map = {"1": 0, "2": 24, "3": 168}
            settings['check_interval_hours'] = interval_map[choice]

            # Include beta versions
            settings['include_beta'] = Confirm.ask(
                "Include beta versions in update checks?",
                default=settings.get('include_beta', False)
            )

        cache['settings'] = settings
        self._save_cache(cache)

        console.print("\n[green]Settings saved![/green]")

    def get_version_history(self):
        """Get version history from GitHub"""
        from installer.version import VersionManager
        vm = VersionManager()

        console.print("\n[bold cyan]Version History[/bold cyan]\n")

        with console.status("[green]Fetching version history...[/green]"):
            versions = vm.get_available_versions(include_beta=True)

        if not versions:
            console.print("[yellow]Could not fetch version history[/yellow]")
            return

        from rich.table import Table
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Version", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Released", style="blue")

        for ver in versions[:15]:  # Show last 15 versions
            ver_type = "[yellow]Beta[/yellow]" if ver['prerelease'] else "[green]Stable[/green]"
            released = ver['published_at'].split('T')[0] if ver['published_at'] else 'N/A'
            table.add_row(ver['version'], ver['name'][:30], ver_type, released)

        console.print(table)

        # Show current version
        current = vm.get_installed_version()
        if current:
            console.print(f"\n[cyan]Currently installed:[/cyan] {current}")
