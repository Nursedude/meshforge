"""Service management for meshtasticd"""

import subprocess
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.table import Table

console = Console()


class ServiceManager:
    """Manage meshtasticd systemd service"""

    SERVICE_NAME = "meshtasticd"

    def __init__(self):
        self._return_to_main = False

    def _run_systemctl(self, action, capture=True):
        """Run systemctl command"""
        try:
            result = subprocess.run(
                ['systemctl', action, self.SERVICE_NAME],
                capture_output=capture,
                text=True
            )
            return result
        except Exception as e:
            console.print(f"[red]Error running systemctl: {e}[/red]")
            return None

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

    def get_status(self):
        """Get service status"""
        result = self._run_systemctl('status')
        if result:
            return {
                'running': result.returncode == 0,
                'output': result.stdout,
                'error': result.stderr
            }
        return None

    def start(self):
        """Start the service"""
        console.print("[cyan]Starting meshtasticd...[/cyan]")
        result = self._run_systemctl('start')
        if result and result.returncode == 0:
            console.print("[green]Service started successfully[/green]")
            return True
        else:
            console.print(f"[red]Failed to start service[/red]")
            if result:
                console.print(result.stderr)
            return False

    def stop(self):
        """Stop the service"""
        console.print("[cyan]Stopping meshtasticd...[/cyan]")
        result = self._run_systemctl('stop')
        if result and result.returncode == 0:
            console.print("[green]Service stopped successfully[/green]")
            return True
        else:
            console.print(f"[red]Failed to stop service[/red]")
            if result:
                console.print(result.stderr)
            return False

    def restart(self):
        """Restart the service"""
        console.print("[cyan]Restarting meshtasticd...[/cyan]")
        result = self._run_systemctl('restart')
        if result and result.returncode == 0:
            console.print("[green]Service restarted successfully[/green]")
            return True
        else:
            console.print(f"[red]Failed to restart service[/red]")
            if result:
                console.print(result.stderr)
            return False

    def enable(self):
        """Enable service to start on boot"""
        console.print("[cyan]Enabling meshtasticd service...[/cyan]")
        result = self._run_systemctl('enable')
        if result and result.returncode == 0:
            console.print("[green]Service enabled for boot[/green]")
            return True
        else:
            console.print(f"[red]Failed to enable service[/red]")
            return False

    def disable(self):
        """Disable service from starting on boot"""
        console.print("[cyan]Disabling meshtasticd service...[/cyan]")
        result = self._run_systemctl('disable')
        if result and result.returncode == 0:
            console.print("[yellow]Service disabled from boot[/yellow]")
            return True
        else:
            console.print(f"[red]Failed to disable service[/red]")
            return False

    def show_status(self):
        """Display detailed service status"""
        status = self.get_status()
        if status:
            if status['running']:
                console.print(Panel(status['output'], title="[green]Service Status (Running)[/green]",
                                   border_style="green"))
            else:
                console.print(Panel(status['output'] or status['error'],
                                   title="[red]Service Status (Stopped)[/red]",
                                   border_style="red"))
        else:
            console.print("[yellow]Could not get service status[/yellow]")

    def view_logs(self, lines=50, follow=False):
        """View service logs"""
        try:
            cmd = ['journalctl', '-u', self.SERVICE_NAME, '-b', '-n', str(lines)]
            if follow:
                cmd.append('-f')
                console.print("[dim]Press Ctrl+C to stop following logs[/dim]\n")
                subprocess.run(cmd)
            else:
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.stdout:
                    console.print(Panel(result.stdout, title="[cyan]Service Logs[/cyan]",
                                       border_style="cyan"))
                else:
                    console.print("[yellow]No logs available[/yellow]")
        except KeyboardInterrupt:
            console.print("\n[dim]Log following stopped[/dim]")
        except Exception as e:
            console.print(f"[red]Error viewing logs: {e}[/red]")

    def view_logs_since(self, since="1h"):
        """View logs since a specific time"""
        try:
            cmd = ['journalctl', '-u', self.SERVICE_NAME, '--since', since]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.stdout:
                console.print(Panel(result.stdout, title=f"[cyan]Logs since {since}[/cyan]",
                                   border_style="cyan"))
            else:
                console.print("[yellow]No logs for this period[/yellow]")
        except Exception as e:
            console.print(f"[red]Error viewing logs: {e}[/red]")

    def interactive_menu(self):
        """Interactive service management menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            # Get current status
            status = self.get_status()
            is_running = status and status['running']
            status_text = "[green]Running[/green]" if is_running else "[red]Stopped[/red]"

            console.print("\n[bold cyan]═══════════════ Service Management ═══════════════[/bold cyan]\n")
            console.print(f"[dim]Service:[/dim] meshtasticd  [dim]Status:[/dim] {status_text}\n")

            console.print("[dim cyan]── Service Control ──[/dim cyan]")
            console.print(f"  [bold]1[/bold]. Start Service")
            console.print(f"  [bold]2[/bold]. Stop Service")
            console.print(f"  [bold]3[/bold]. Restart Service")
            console.print(f"  [bold]4[/bold]. View Status (detailed)")

            console.print("\n[dim cyan]── Boot Configuration ──[/dim cyan]")
            console.print(f"  [bold]5[/bold]. Enable on Boot")
            console.print(f"  [bold]6[/bold]. Disable on Boot")

            console.print("\n[dim cyan]── Logs ──[/dim cyan]")
            console.print(f"  [bold]7[/bold]. View Recent Logs (50 lines)")
            console.print(f"  [bold]8[/bold]. Follow Logs (live)")
            console.print(f"  [bold]9[/bold]. View Logs by Time Period")

            choices = self._prompt_back(["1", "2", "3", "4", "5", "6", "7", "8", "9"])
            choice = Prompt.ask("\n[cyan]Select option[/cyan]", choices=choices, default="0")

            if self._handle_back(choice):
                return

            if choice == "1":
                self.start()
            elif choice == "2":
                if Confirm.ask("[yellow]Stop meshtasticd service?[/yellow]", default=False):
                    self.stop()
            elif choice == "3":
                self.restart()
            elif choice == "4":
                self.show_status()
                Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            elif choice == "5":
                self.enable()
            elif choice == "6":
                if Confirm.ask("[yellow]Disable service from boot?[/yellow]", default=False):
                    self.disable()
            elif choice == "7":
                lines = Prompt.ask("Number of lines", default="50")
                self.view_logs(lines=int(lines))
                Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            elif choice == "8":
                self.view_logs(follow=True)
            elif choice == "9":
                self._view_logs_time_menu()

    def _view_logs_time_menu(self):
        """Menu for viewing logs by time"""
        console.print("\n[bold]View Logs Since:[/bold]")
        console.print("  [bold]1[/bold]. Last 10 minutes")
        console.print("  [bold]2[/bold]. Last 1 hour")
        console.print("  [bold]3[/bold]. Last 4 hours")
        console.print("  [bold]4[/bold]. Last 24 hours")
        console.print("  [bold]5[/bold]. Since boot")
        console.print("  [bold]6[/bold]. Custom time")

        choice = Prompt.ask("\n[cyan]Select[/cyan]", choices=["1", "2", "3", "4", "5", "6", "0"], default="0")

        time_map = {
            "1": "10 minutes ago",
            "2": "1 hour ago",
            "3": "4 hours ago",
            "4": "24 hours ago",
            "5": "today",
        }

        if choice in time_map:
            self.view_logs_since(time_map[choice])
        elif choice == "6":
            custom = Prompt.ask("Enter time (e.g., '2 hours ago', '2024-01-01')")
            self.view_logs_since(custom)

        if choice != "0":
            Prompt.ask("\n[dim]Press Enter to continue[/dim]")
