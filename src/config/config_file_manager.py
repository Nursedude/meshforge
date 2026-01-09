"""Configuration file manager - select yaml from available.d and edit with nano"""

import os
import subprocess
import shutil
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.panel import Panel

console = Console()


class ConfigFileManager:
    """Manage meshtasticd configuration files"""

    CONFIG_BASE = Path("/etc/meshtasticd")
    AVAILABLE_D = CONFIG_BASE / "available.d"
    CONFIG_D = CONFIG_BASE / "config.d"
    MAIN_CONFIG = CONFIG_BASE / "config.yaml"

    def __init__(self):
        self._return_to_main = False

    def _prompt_back(self, additional_choices=None):
        """Standard prompt with back options"""
        choices = list(additional_choices) if additional_choices else []
        console.print(f"\n  [bold]0[/bold]. Back")
        console.print(f"  [bold]m[/bold]. Main Menu")
        return choices + ["0", "m"]

    def _handle_back(self, choice):
        """Handle back navigation"""
        if choice == "m":
            self._return_to_main = True
            return True
        if choice == "0":
            return True
        return False

    def list_available_configs(self):
        """List all yaml files in available.d"""
        if not self.AVAILABLE_D.exists():
            return []
        return sorted([f.name for f in self.AVAILABLE_D.glob("*.yaml")])

    def list_active_configs(self):
        """List all yaml files in config.d"""
        if not self.CONFIG_D.exists():
            return []
        return sorted([f.name for f in self.CONFIG_D.glob("*.yaml")])

    def _daemon_reload(self):
        """Run systemctl daemon-reload"""
        console.print("[cyan]Running systemctl daemon-reload...[/cyan]")
        try:
            subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=30)
            console.print("[green]Daemon reloaded[/green]")
            return True
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to reload daemon: {e}[/red]")
            return False

    def _restart_service(self):
        """Restart meshtasticd service"""
        console.print("[cyan]Restarting meshtasticd service...[/cyan]")
        try:
            subprocess.run(["systemctl", "restart", "meshtasticd"], check=True, timeout=30)
            console.print("[green]Service restarted[/green]")
            return True
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to restart service: {e}[/red]")
            return False

    def _open_nano(self, file_path):
        """Open a file in nano editor"""
        console.print(f"\n[cyan]Opening {file_path} in nano...[/cyan]")
        console.print("[dim]Press Ctrl+X to exit, Y to save changes[/dim]\n")
        try:
            subprocess.run(["nano", str(file_path)])  # Interactive, no timeout
            return True
        except FileNotFoundError:
            console.print("[red]nano not found. Install with: sudo apt install nano[/red]")
            return False
        except Exception as e:
            console.print(f"[red]Error opening nano: {e}[/red]")
            return False

    def interactive_menu(self):
        """Main configuration file management menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Configuration File Manager ═══════════════[/bold cyan]\n")

            # Show current status
            available = self.list_available_configs()
            active = self.list_active_configs()

            console.print(f"[dim]Available configs: {len(available)} files in {self.AVAILABLE_D}[/dim]")
            console.print(f"[dim]Active configs: {len(active)} files in {self.CONFIG_D}[/dim]")
            console.print(f"[dim]Main config: {self.MAIN_CONFIG}[/dim]\n")

            console.print("[dim cyan]── Actions ──[/dim cyan]")
            console.print(f"  [bold]1[/bold]. [green]Select & Activate Hardware Config[/green]")
            console.print(f"  [bold]2[/bold]. Edit Main config.yaml (nano)")
            console.print(f"  [bold]3[/bold]. Edit Active Config File (nano)")
            console.print(f"  [bold]4[/bold]. View Available Configurations")
            console.print(f"  [bold]5[/bold]. View Active Configurations")
            console.print(f"  [bold]6[/bold]. Deactivate Config (remove from config.d)")

            console.print("\n[dim cyan]── Service ──[/dim cyan]")
            console.print(f"  [bold]7[/bold]. Apply Changes (daemon-reload + restart)")
            console.print(f"  [bold]8[/bold]. View Current Config (cat config.yaml)")

            choices = self._prompt_back(["1", "2", "3", "4", "5", "6", "7", "8"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                self._select_and_activate()
            elif choice == "2":
                self._edit_main_config()
            elif choice == "3":
                self._edit_active_config()
            elif choice == "4":
                self._view_available()
            elif choice == "5":
                self._view_active()
            elif choice == "6":
                self._deactivate_config()
            elif choice == "7":
                self._apply_changes()
            elif choice == "8":
                self._view_current_config()

    def _select_and_activate(self):
        """Select a config from available.d and copy to config.d"""
        available = self.list_available_configs()

        if not available:
            console.print("[yellow]No configuration files found in available.d[/yellow]")
            console.print(f"[dim]Directory: {self.AVAILABLE_D}[/dim]")
            console.print("[dim]Install meshtasticd to get configuration templates[/dim]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        console.print("\n[bold cyan]Available Hardware Configurations:[/bold cyan]\n")

        # Group configs by type
        lora_configs = [f for f in available if f.startswith("lora-") or "lora" in f.lower()]
        display_configs = [f for f in available if f.startswith("display-")]
        preset_configs = [f for f in available if any(x in f.lower() for x in ["mtnmesh", "emergency", "urban", "repeater"])]
        other_configs = [f for f in available if f not in lora_configs + display_configs + preset_configs]

        all_configs = []
        idx = 1

        if lora_configs:
            console.print("[dim cyan]── LoRa Hardware ──[/dim cyan]")
            for cfg in lora_configs:
                console.print(f"  [bold]{idx:2}[/bold]. {cfg}")
                all_configs.append(cfg)
                idx += 1

        if display_configs:
            console.print("\n[dim cyan]── Displays ──[/dim cyan]")
            for cfg in display_configs:
                console.print(f"  [bold]{idx:2}[/bold]. {cfg}")
                all_configs.append(cfg)
                idx += 1

        if preset_configs:
            console.print("\n[dim cyan]── Network Presets ──[/dim cyan]")
            for cfg in preset_configs:
                console.print(f"  [bold]{idx:2}[/bold]. {cfg}")
                all_configs.append(cfg)
                idx += 1

        if other_configs:
            console.print("\n[dim cyan]── Other ──[/dim cyan]")
            for cfg in other_configs:
                console.print(f"  [bold]{idx:2}[/bold]. {cfg}")
                all_configs.append(cfg)
                idx += 1

        console.print(f"\n  [bold]0[/bold]. Cancel")

        valid = [str(i) for i in range(len(all_configs) + 1)]
        choice = Prompt.ask("\n[cyan]Select configuration to activate[/cyan]", choices=valid, default="0")

        if choice == "0":
            return

        selected = all_configs[int(choice) - 1]
        src = self.AVAILABLE_D / selected
        dst = self.CONFIG_D / selected

        # Show preview
        console.print(f"\n[cyan]Preview of {selected}:[/cyan]")
        try:
            with open(src, 'r') as f:
                content = f.read()
                lines = content.split('\n')[:30]
                for line in lines:
                    console.print(f"[dim]{line}[/dim]")
                if len(content.split('\n')) > 30:
                    console.print("[dim]... (truncated)[/dim]")
        except Exception as e:
            console.print(f"[red]Could not read file: {e}[/red]")
            return

        # Confirm and copy
        if Confirm.ask(f"\n[yellow]Activate {selected}?[/yellow]", default=True):
            try:
                self.CONFIG_D.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                console.print(f"[green]Activated: {dst}[/green]")

                if Confirm.ask("\n[cyan]Edit this config now?[/cyan]", default=True):
                    self._open_nano(dst)

                if Confirm.ask("\n[cyan]Apply changes (daemon-reload + restart)?[/cyan]", default=True):
                    self._apply_changes()

            except Exception as e:
                console.print(f"[red]Failed to activate config: {e}[/red]")

    def _edit_main_config(self):
        """Edit the main config.yaml"""
        if not self.MAIN_CONFIG.exists():
            console.print("[yellow]Main config.yaml does not exist[/yellow]")
            if Confirm.ask("Create a basic config.yaml?", default=True):
                self._create_basic_config()
            else:
                return

        self._open_nano(self.MAIN_CONFIG)

        if Confirm.ask("\n[cyan]Apply changes?[/cyan]", default=True):
            self._apply_changes()

    def _create_basic_config(self):
        """Create a basic config.yaml"""
        basic_config = """# Meshtasticd Configuration
# See /etc/meshtasticd/available.d for hardware-specific configs

Lora:
  Module: auto

Logging:
  LogLevel: info

Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web

General:
  MaxNodes: 200
  MaxMessageQueue: 100
  ConfigDirectory: /etc/meshtasticd/config.d/
  AvailableDirectory: /etc/meshtasticd/available.d/
"""
        try:
            self.CONFIG_BASE.mkdir(parents=True, exist_ok=True)
            with open(self.MAIN_CONFIG, 'w') as f:
                f.write(basic_config)
            console.print(f"[green]Created: {self.MAIN_CONFIG}[/green]")
        except Exception as e:
            console.print(f"[red]Failed to create config: {e}[/red]")

    def _edit_active_config(self):
        """Edit an active config from config.d"""
        active = self.list_active_configs()

        if not active:
            console.print("[yellow]No active configs in config.d[/yellow]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        console.print("\n[bold cyan]Active Configurations:[/bold cyan]\n")
        for i, cfg in enumerate(active, 1):
            console.print(f"  [bold]{i}[/bold]. {cfg}")
        console.print(f"  [bold]0[/bold]. Cancel")

        valid = [str(i) for i in range(len(active) + 1)]
        choice = Prompt.ask("\n[cyan]Select config to edit[/cyan]", choices=valid, default="0")

        if choice == "0":
            return

        selected = active[int(choice) - 1]
        self._open_nano(self.CONFIG_D / selected)

        if Confirm.ask("\n[cyan]Apply changes?[/cyan]", default=True):
            self._apply_changes()

    def _view_available(self):
        """View available configurations"""
        available = self.list_available_configs()

        if not available:
            console.print("[yellow]No configuration files found[/yellow]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        table = Table(title=f"Available Configs ({self.AVAILABLE_D})")
        table.add_column("#", style="cyan", width=4)
        table.add_column("Filename", style="green")
        table.add_column("Description", style="dim")

        for i, cfg in enumerate(available, 1):
            # Try to get a description from the file
            desc = ""
            try:
                with open(self.AVAILABLE_D / cfg, 'r') as f:
                    first_line = f.readline().strip()
                    if first_line.startswith("#"):
                        desc = first_line[1:].strip()[:50]
            except (OSError, UnicodeDecodeError):
                pass
            table.add_row(str(i), cfg, desc)

        console.print(table)
        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _view_active(self):
        """View active configurations"""
        active = self.list_active_configs()

        if not active:
            console.print("[yellow]No active configs in config.d[/yellow]")
        else:
            table = Table(title=f"Active Configs ({self.CONFIG_D})")
            table.add_column("#", style="cyan", width=4)
            table.add_column("Filename", style="green")

            for i, cfg in enumerate(active, 1):
                table.add_row(str(i), cfg)

            console.print(table)

        Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def _deactivate_config(self):
        """Remove a config from config.d"""
        active = self.list_active_configs()

        if not active:
            console.print("[yellow]No active configs to deactivate[/yellow]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        console.print("\n[bold cyan]Active Configurations:[/bold cyan]\n")
        for i, cfg in enumerate(active, 1):
            console.print(f"  [bold]{i}[/bold]. {cfg}")
        console.print(f"  [bold]0[/bold]. Cancel")

        valid = [str(i) for i in range(len(active) + 1)]
        choice = Prompt.ask("\n[cyan]Select config to deactivate[/cyan]", choices=valid, default="0")

        if choice == "0":
            return

        selected = active[int(choice) - 1]
        cfg_path = self.CONFIG_D / selected

        if Confirm.ask(f"[yellow]Remove {selected} from config.d?[/yellow]", default=False):
            try:
                cfg_path.unlink()
                console.print(f"[green]Deactivated: {selected}[/green]")

                if Confirm.ask("\n[cyan]Apply changes?[/cyan]", default=True):
                    self._apply_changes()
            except Exception as e:
                console.print(f"[red]Failed to deactivate: {e}[/red]")

    def _apply_changes(self):
        """Apply configuration changes"""
        console.print("\n[bold cyan]Applying Configuration Changes[/bold cyan]\n")

        self._daemon_reload()

        if Confirm.ask("\n[cyan]Restart meshtasticd service?[/cyan]", default=True):
            self._restart_service()

            # Show status
            console.print("\n[cyan]Service status:[/cyan]")
            subprocess.run(["systemctl", "status", "meshtasticd", "--no-pager", "-l"], check=False, timeout=15)

    def _view_current_config(self):
        """View the current main config"""
        if not self.MAIN_CONFIG.exists():
            console.print("[yellow]No config.yaml found[/yellow]")
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            return

        try:
            with open(self.MAIN_CONFIG, 'r') as f:
                content = f.read()
            console.print(Panel(content, title=f"[cyan]{self.MAIN_CONFIG}[/cyan]", border_style="cyan"))
        except Exception as e:
            console.print(f"[red]Error reading config: {e}[/red]")

        Prompt.ask("\n[dim]Press Enter to continue[/dim]")
