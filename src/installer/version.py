"""Version management for meshtasticd"""

import re
import requests
from packaging import version
from rich.console import Console
from rich.table import Table

from utils.system import run_command
from utils.logger import log, log_exception

console = Console()


def sanitize_version(ver_str):
    """Sanitize version string to be PEP 440 compatible

    Handles non-standard versions like '2.7.15.567b8ea' by:
    1. Removing git hashes (hex strings after version numbers)
    2. Keeping only major.minor.patch format

    Args:
        ver_str: Version string that may be non-standard

    Returns:
        Sanitized version string or None if unparseable
    """
    if not ver_str:
        return None

    # Remove 'v' prefix if present
    ver_str = ver_str.lstrip('v')

    # Try to extract semantic version (X.Y.Z) pattern
    # This handles versions like "2.7.15.567b8ea" -> "2.7.15"
    match = re.match(r'^(\d+\.\d+\.\d+)', ver_str)
    if match:
        return match.group(1)

    # Try simpler patterns (X.Y)
    match = re.match(r'^(\d+\.\d+)', ver_str)
    if match:
        return match.group(1)

    # Return original if no pattern matches
    return ver_str


def safe_version_parse(ver_str):
    """Safely parse a version string, handling non-standard formats

    Returns:
        Parsed version object or None if parsing fails
    """
    sanitized = sanitize_version(ver_str)
    if not sanitized:
        return None

    try:
        return version.parse(sanitized)
    except Exception:
        return None


class VersionManager:
    """Manage meshtasticd versions"""

    def __init__(self):
        self.github_api_url = "https://api.github.com/repos/meshtastic/firmware/releases"
        self.current_version = None

    def get_installed_version(self):
        """Get currently installed version"""
        if self.current_version:
            return self.current_version

        result = run_command('meshtasticd --version')

        if result['success']:
            # Parse version from output
            version_str = result['stdout'].strip()
            # Extract version number (format may vary)
            # Example: "meshtasticd v2.3.4" or "2.3.4"
            parts = version_str.split()
            for part in parts:
                if part.startswith('v'):
                    part = part[1:]  # Remove 'v' prefix
                try:
                    # Validate version format
                    version.parse(part)
                    self.current_version = part
                    return part
                except Exception:
                    continue

        return None

    def get_available_versions(self, include_beta=False):
        """Get available versions from GitHub releases"""
        log("Fetching available versions from GitHub")

        try:
            response = requests.get(self.github_api_url, timeout=10)
            response.raise_for_status()

            releases = response.json()
            versions = []

            for release in releases:
                tag_name = release.get('tag_name', '')
                is_prerelease = release.get('prerelease', False)
                is_draft = release.get('draft', False)

                # Skip drafts
                if is_draft:
                    continue

                # Skip prereleases unless requested
                if is_prerelease and not include_beta:
                    continue

                versions.append({
                    'version': tag_name,
                    'name': release.get('name', tag_name),
                    'prerelease': is_prerelease,
                    'published_at': release.get('published_at', ''),
                    'url': release.get('html_url', '')
                })

            return versions

        except requests.RequestException as e:
            log_exception(e, "Failed to fetch versions from GitHub")
            console.print(f"[yellow]Could not fetch versions from GitHub: {str(e)}[/yellow]")
            return []

    def get_latest_version(self, include_beta=False):
        """Get the latest version"""
        versions = self.get_available_versions(include_beta=include_beta)

        if not versions:
            return None

        # Filter by prerelease status
        if not include_beta:
            versions = [v for v in versions if not v['prerelease']]

        if not versions:
            return None

        # Return the first one (GitHub API returns them sorted by date)
        return versions[0]

    def check_for_updates(self):
        """Check if an update is available"""
        current = self.get_installed_version()

        if not current:
            console.print("[yellow]Could not determine installed version[/yellow]")
            return None

        latest = self.get_latest_version()

        if not latest:
            console.print("[yellow]Could not determine latest version[/yellow]")
            return None

        try:
            # Use safe version parsing to handle non-standard versions
            current_ver = safe_version_parse(current)
            latest_ver = safe_version_parse(latest['version'])

            if current_ver is None or latest_ver is None:
                log(f"Could not parse versions: current={current}, latest={latest['version']}", 'warning')
                # Return info without comparison if parsing fails
                return {
                    'update_available': False,
                    'current': current,
                    'latest': latest['version'],
                    'parse_error': True
                }

            if latest_ver > current_ver:
                return {
                    'update_available': True,
                    'current': current,
                    'latest': latest['version'],
                    'release_info': latest
                }
            else:
                return {
                    'update_available': False,
                    'current': current,
                    'latest': latest['version']
                }

        except Exception as e:
            log_exception(e, "Version comparison")
            return {
                'update_available': False,
                'current': current,
                'latest': latest['version'] if latest else 'Unknown',
                'error': str(e)
            }

    def show_version_info(self):
        """Display version information"""
        console.print("\n[bold cyan]Version Information[/bold cyan]\n")

        current = self.get_installed_version()

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Type", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Status", style="yellow")

        if current:
            table.add_row("Installed", current, "")
        else:
            table.add_row("Installed", "Not found", "[red]Not installed[/red]")

        latest = self.get_latest_version()
        if latest:
            table.add_row("Latest Stable", latest['version'], "")

        latest_beta = self.get_latest_version(include_beta=True)
        if latest_beta and latest_beta.get('prerelease'):
            table.add_row("Latest Beta", latest_beta['version'], "[yellow]Prerelease[/yellow]")

        console.print(table)

        # Check for updates
        if current:
            update_info = self.check_for_updates()
            if update_info and update_info['update_available']:
                console.print(f"\n[bold green]Update available:[/bold green] {update_info['latest']}")
                console.print("Run with --update to upgrade")
            else:
                console.print("\n[green]You are running the latest version[/green]")

    def show_available_versions(self, include_beta=False):
        """Display all available versions"""
        console.print("\n[bold cyan]Available Versions[/bold cyan]\n")

        versions = self.get_available_versions(include_beta=include_beta)

        if not versions:
            console.print("[yellow]No versions found[/yellow]")
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Version", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Released", style="blue")

        for ver in versions[:10]:  # Show top 10
            ver_type = "Beta" if ver['prerelease'] else "Stable"
            released = ver['published_at'].split('T')[0] if ver['published_at'] else ''
            table.add_row(ver['version'], ver['name'], ver_type, released)

        console.print(table)
