"""System diagnostics module for meshtasticd-installer

Provides comprehensive system health checks including:
- Network connectivity (ping, DNS, internet)
- Mesh network diagnostics
- Hardware health (CPU, memory, disk, temperature)
- Service diagnostics
- RF/LoRa diagnostics
"""

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from utils import emoji as em

console = Console()


class SystemDiagnostics:
    """Comprehensive system diagnostics for meshtasticd"""

    def __init__(self):
        self._return_to_main = False

    def interactive_menu(self):
        """Main diagnostics menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ System Diagnostics ═══════════════[/bold cyan]\n")

            console.print("[dim cyan]── Network ──[/dim cyan]")
            console.print(f"  [bold]1[/bold]. {em.get('🌐')} Network Connectivity Test")
            console.print(f"  [bold]2[/bold]. {em.get('📡')} Mesh Network Diagnostics")
            console.print(f"  [bold]3[/bold]. {em.get('🔗')} MQTT Connection Test")

            console.print("\n[dim cyan]── Hardware ──[/dim cyan]")
            console.print(f"  [bold]4[/bold]. {em.get('🌡️')} System Health (CPU, Memory, Temp)")
            console.print(f"  [bold]5[/bold]. {em.get('📻')} LoRa/Radio Diagnostics")
            console.print(f"  [bold]6[/bold]. {em.get('🔌')} GPIO/SPI/I2C Status")

            console.print("\n[dim cyan]── Service ──[/dim cyan]")
            console.print(f"  [bold]7[/bold]. {em.get('⚙️')} Meshtasticd Service Check")
            console.print(f"  [bold]8[/bold]. {em.get('📜')} Log Analysis")

            console.print("\n[dim cyan]── Full Report ──[/dim cyan]")
            console.print(f"  [bold]9[/bold]. {em.get('📋')} [yellow]Run All Diagnostics[/yellow]")

            console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')} Back")
            console.print(f"  [bold]m[/bold]. Main Menu")

            choice = Prompt.ask(
                "\n[cyan]Select diagnostic[/cyan]",
                choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "m"],
                default="0"
            )

            if choice == "0":
                return
            elif choice == "m":
                self._return_to_main = True
                return
            elif choice == "1":
                self.network_connectivity_test()
            elif choice == "2":
                self.mesh_network_diagnostics()
            elif choice == "3":
                self.mqtt_connection_test()
            elif choice == "4":
                self.system_health_check()
            elif choice == "5":
                self.lora_diagnostics()
            elif choice == "6":
                self.gpio_spi_i2c_status()
            elif choice == "7":
                self.service_diagnostics()
            elif choice == "8":
                self.log_analysis()
            elif choice == "9":
                self.run_all_diagnostics()

            Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def network_connectivity_test(self):
        """Test network connectivity"""
        console.print("\n[bold cyan]Network Connectivity Test[/bold cyan]\n")

        results = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            # Test localhost
            task = progress.add_task("Testing localhost...", total=None)
            localhost_ok = self._ping("127.0.0.1", count=1)
            results.append(("Localhost (127.0.0.1)", localhost_ok, "Loopback interface"))
            progress.remove_task(task)

            # Test gateway
            task = progress.add_task("Testing default gateway...", total=None)
            gateway = self._get_default_gateway()
            if gateway:
                gateway_ok = self._ping(gateway, count=2)
                results.append((f"Gateway ({gateway})", gateway_ok, "Local network"))
            else:
                results.append(("Gateway", False, "No default gateway found"))
            progress.remove_task(task)

            # Test DNS resolution
            task = progress.add_task("Testing DNS resolution...", total=None)
            dns_ok = self._dns_resolve("meshtastic.org")
            results.append(("DNS Resolution", dns_ok, "meshtastic.org"))
            progress.remove_task(task)

            # Test internet (ping common servers)
            task = progress.add_task("Testing internet connectivity...", total=None)
            internet_ok = self._ping("8.8.8.8", count=2) or self._ping("1.1.1.1", count=2)
            results.append(("Internet (8.8.8.8)", internet_ok, "Google DNS"))
            progress.remove_task(task)

            # Test HTTPS connectivity
            task = progress.add_task("Testing HTTPS connectivity...", total=None)
            https_ok = self._test_https("https://meshtastic.org")
            results.append(("HTTPS", https_ok, "meshtastic.org"))
            progress.remove_task(task)

            # Test GitHub API (for updates)
            task = progress.add_task("Testing GitHub API...", total=None)
            github_ok = self._test_https("https://api.github.com")
            results.append(("GitHub API", github_ok, "For update checks"))
            progress.remove_task(task)

        # Display results
        self._display_diagnostic_results("Network Connectivity", results)

    def _ping(self, host: str, count: int = 3, timeout: int = 5) -> bool:
        """Ping a host"""
        try:
            result = subprocess.run(
                ['ping', '-c', str(count), '-W', str(timeout), host],
                capture_output=True,
                timeout=timeout * count + 5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _get_default_gateway(self) -> Optional[str]:
        """Get default gateway IP"""
        try:
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout:
                parts = result.stdout.split()
                if 'via' in parts:
                    idx = parts.index('via')
                    if idx + 1 < len(parts):
                        return parts[idx + 1]
        except Exception:
            pass
        return None

    def _dns_resolve(self, hostname: str) -> bool:
        """Test DNS resolution"""
        try:
            socket.gethostbyname(hostname)
            return True
        except socket.gaierror:
            return False

    def _test_https(self, url: str) -> bool:
        """Test HTTPS connectivity"""
        try:
            result = subprocess.run(
                ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10', url],
                capture_output=True, text=True, timeout=15
            )
            return result.returncode == 0 and result.stdout.startswith(('2', '3'))
        except Exception:
            return False

    def mesh_network_diagnostics(self):
        """Mesh network diagnostics"""
        console.print("\n[bold cyan]Mesh Network Diagnostics[/bold cyan]\n")

        results = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            # Check meshtasticd API
            task = progress.add_task("Checking meshtasticd API...", total=None)
            api_ok = self._check_meshtasticd_api()
            results.append(("Meshtasticd API", api_ok, "localhost:4403"))
            progress.remove_task(task)

            # Get node count
            task = progress.add_task("Getting node information...", total=None)
            nodes = self._get_mesh_nodes()
            if nodes is not None:
                results.append(("Mesh Nodes Visible", True, f"{nodes} node(s)"))
            else:
                results.append(("Mesh Nodes", False, "Could not query"))
            progress.remove_task(task)

            # Check last message time
            task = progress.add_task("Checking message activity...", total=None)
            activity = self._check_mesh_activity()
            results.append(("Mesh Activity", activity, "Recent messages"))
            progress.remove_task(task)

        self._display_diagnostic_results("Mesh Network", results)

        # Show additional info if API is available
        if api_ok:
            console.print("\n[dim]Tip: Use 'Meshtastic CLI' menu for detailed node info[/dim]")

    def _check_meshtasticd_api(self) -> bool:
        """Check if meshtasticd API is responding"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('127.0.0.1', 4403))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _get_mesh_nodes(self) -> Optional[int]:
        """Get count of visible mesh nodes"""
        from utils.cli import find_meshtastic_cli
        cli_path = find_meshtastic_cli()
        if not cli_path:
            return None

        try:
            result = subprocess.run(
                [cli_path, '--host', 'localhost', '--nodes'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                # Count node entries
                lines = result.stdout.split('\n')
                count = sum(1 for line in lines if '!' in line or 'Node' in line)
                return max(count, 1)  # At least this node
        except Exception:
            pass
        return None

    def _check_mesh_activity(self) -> bool:
        """Check for recent mesh activity in logs"""
        try:
            result = subprocess.run(
                ['journalctl', '-u', 'meshtasticd', '--since', '5 min ago', '-n', '10'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return 'received' in result.stdout.lower() or 'packet' in result.stdout.lower()
        except Exception:
            pass
        return False

    def mqtt_connection_test(self):
        """Test MQTT broker connection"""
        console.print("\n[bold cyan]MQTT Connection Test[/bold cyan]\n")

        # Read MQTT settings from config
        mqtt_config = self._get_mqtt_config()

        results = []

        if not mqtt_config.get('enabled'):
            console.print("[yellow]MQTT is not enabled in meshtasticd config[/yellow]")
            console.print("[dim]Enable MQTT in device configuration to use this test[/dim]")
            return

        broker = mqtt_config.get('address', 'mqtt.meshtastic.org')
        port = mqtt_config.get('port', 1883)

        console.print(f"[dim]Testing MQTT broker: {broker}:{port}[/dim]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            # Test DNS resolution of broker
            task = progress.add_task(f"Resolving {broker}...", total=None)
            dns_ok = self._dns_resolve(broker)
            results.append(("DNS Resolution", dns_ok, broker))
            progress.remove_task(task)

            # Test TCP connection to broker
            task = progress.add_task(f"Connecting to {broker}:{port}...", total=None)
            tcp_ok = self._test_tcp_connection(broker, port)
            results.append(("TCP Connection", tcp_ok, f"Port {port}"))
            progress.remove_task(task)

            # Test with mosquitto_pub if available
            task = progress.add_task("Testing MQTT protocol...", total=None)
            mqtt_ok = self._test_mqtt_protocol(broker, port)
            results.append(("MQTT Protocol", mqtt_ok, "Connection test"))
            progress.remove_task(task)

        self._display_diagnostic_results("MQTT Connection", results)

    def _get_mqtt_config(self) -> dict:
        """Get MQTT configuration from meshtasticd config"""
        config_path = Path('/etc/meshtasticd/config.yaml')
        if not config_path.exists():
            return {}

        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
                return config.get('mqtt', {})
        except Exception:
            return {}

    def _test_tcp_connection(self, host: str, port: int) -> bool:
        """Test TCP connection"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _test_mqtt_protocol(self, broker: str, port: int) -> bool:
        """Test MQTT protocol with mosquitto_pub"""
        import shutil
        if not shutil.which('mosquitto_pub'):
            # Try basic TCP test as fallback
            return self._test_tcp_connection(broker, port)

        try:
            result = subprocess.run(
                ['mosquitto_pub', '-h', broker, '-p', str(port),
                 '-t', 'test', '-m', 'test', '-q', '0'],
                capture_output=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False

    def system_health_check(self):
        """Check system health - CPU, memory, temperature"""
        console.print("\n[bold cyan]System Health Check[/bold cyan]\n")

        # Create table
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_column("Status", style="white")

        # CPU usage
        cpu_usage = self._get_cpu_usage()
        cpu_status = "[green]OK[/green]" if cpu_usage < 80 else "[yellow]High[/yellow]" if cpu_usage < 95 else "[red]Critical[/red]"
        table.add_row("CPU Usage", f"{cpu_usage:.1f}%", cpu_status)

        # CPU temperature
        cpu_temp = self._get_cpu_temperature()
        if cpu_temp:
            temp_status = "[green]OK[/green]" if cpu_temp < 60 else "[yellow]Warm[/yellow]" if cpu_temp < 80 else "[red]Hot![/red]"
            table.add_row("CPU Temperature", f"{cpu_temp:.1f}°C", temp_status)

        # Memory usage
        mem_total, mem_used, mem_percent = self._get_memory_info()
        mem_status = "[green]OK[/green]" if mem_percent < 80 else "[yellow]High[/yellow]" if mem_percent < 95 else "[red]Critical[/red]"
        table.add_row("Memory", f"{mem_used}MB / {mem_total}MB ({mem_percent:.1f}%)", mem_status)

        # Disk usage
        disk_total, disk_used, disk_percent = self._get_disk_info()
        disk_status = "[green]OK[/green]" if disk_percent < 80 else "[yellow]Low Space[/yellow]" if disk_percent < 95 else "[red]Critical[/red]"
        table.add_row("Disk (root)", f"{disk_used}GB / {disk_total}GB ({disk_percent:.1f}%)", disk_status)

        # Uptime
        uptime = self._get_uptime()
        table.add_row("System Uptime", uptime, "[green]Running[/green]")

        # Load average
        load_1, load_5, load_15 = self._get_load_average()
        cores = os.cpu_count() or 1
        load_status = "[green]OK[/green]" if load_1 < cores else "[yellow]High[/yellow]" if load_1 < cores * 2 else "[red]Overloaded[/red]"
        table.add_row("Load Average", f"{load_1:.2f}, {load_5:.2f}, {load_15:.2f}", load_status)

        console.print(table)

        # Throttling check for Raspberry Pi
        throttle_status = self._check_throttling()
        if throttle_status:
            console.print(f"\n[yellow]Throttling Status:[/yellow] {throttle_status}")

    def _get_cpu_usage(self) -> float:
        """Get CPU usage percentage"""
        try:
            result = subprocess.run(
                ['grep', 'cpu ', '/proc/stat'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                values = result.stdout.split()[1:5]
                values = [int(v) for v in values]
                idle = values[3]
                total = sum(values)
                # Need two samples for accurate reading
                time.sleep(0.1)
                result2 = subprocess.run(
                    ['grep', 'cpu ', '/proc/stat'],
                    capture_output=True, text=True
                )
                values2 = result2.stdout.split()[1:5]
                values2 = [int(v) for v in values2]
                idle2 = values2[3]
                total2 = sum(values2)

                idle_delta = idle2 - idle
                total_delta = total2 - total
                if total_delta > 0:
                    return (1 - idle_delta / total_delta) * 100
        except Exception:
            pass
        return 0.0

    def _get_cpu_temperature(self) -> Optional[float]:
        """Get CPU temperature"""
        try:
            # Try thermal zone
            temp_path = Path('/sys/class/thermal/thermal_zone0/temp')
            if temp_path.exists():
                temp = int(temp_path.read_text().strip())
                return temp / 1000.0

            # Try vcgencmd (Raspberry Pi)
            result = subprocess.run(
                ['vcgencmd', 'measure_temp'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                # Parse "temp=XX.X'C"
                temp_str = result.stdout.strip()
                temp = float(temp_str.split('=')[1].replace("'C", ""))
                return temp
        except Exception:
            pass
        return None

    def _get_memory_info(self) -> tuple:
        """Get memory info in MB"""
        try:
            with open('/proc/meminfo') as f:
                lines = f.readlines()
                mem_info = {}
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem_info[parts[0].rstrip(':')] = int(parts[1])

                total = mem_info.get('MemTotal', 0) // 1024
                available = mem_info.get('MemAvailable', 0) // 1024
                used = total - available
                percent = (used / total * 100) if total > 0 else 0
                return total, used, percent
        except Exception:
            return 0, 0, 0

    def _get_disk_info(self) -> tuple:
        """Get disk info in GB"""
        try:
            statvfs = os.statvfs('/')
            total = (statvfs.f_frsize * statvfs.f_blocks) // (1024 ** 3)
            free = (statvfs.f_frsize * statvfs.f_bavail) // (1024 ** 3)
            used = total - free
            percent = (used / total * 100) if total > 0 else 0
            return total, used, percent
        except Exception:
            return 0, 0, 0

    def _get_uptime(self) -> str:
        """Get system uptime"""
        try:
            with open('/proc/uptime') as f:
                uptime_seconds = float(f.read().split()[0])
                days = int(uptime_seconds // 86400)
                hours = int((uptime_seconds % 86400) // 3600)
                minutes = int((uptime_seconds % 3600) // 60)
                if days > 0:
                    return f"{days}d {hours}h {minutes}m"
                elif hours > 0:
                    return f"{hours}h {minutes}m"
                else:
                    return f"{minutes}m"
        except Exception:
            return "Unknown"

    def _get_load_average(self) -> tuple:
        """Get load average"""
        try:
            with open('/proc/loadavg') as f:
                parts = f.read().split()
                return float(parts[0]), float(parts[1]), float(parts[2])
        except Exception:
            return 0.0, 0.0, 0.0

    def _check_throttling(self) -> Optional[str]:
        """Check Raspberry Pi throttling status"""
        try:
            result = subprocess.run(
                ['vcgencmd', 'get_throttled'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                throttle = result.stdout.strip()
                value = int(throttle.split('=')[1], 16)
                if value == 0:
                    return None  # No throttling

                issues = []
                if value & 0x1:
                    issues.append("Under-voltage detected")
                if value & 0x2:
                    issues.append("Arm frequency capped")
                if value & 0x4:
                    issues.append("Currently throttled")
                if value & 0x8:
                    issues.append("Soft temp limit active")
                if value & 0x10000:
                    issues.append("Under-voltage has occurred")
                if value & 0x20000:
                    issues.append("Arm frequency capping has occurred")
                if value & 0x40000:
                    issues.append("Throttling has occurred")
                if value & 0x80000:
                    issues.append("Soft temp limit has occurred")

                return ", ".join(issues) if issues else None
        except Exception:
            pass
        return None

    def lora_diagnostics(self):
        """LoRa/Radio diagnostics"""
        console.print("\n[bold cyan]LoRa/Radio Diagnostics[/bold cyan]\n")

        results = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            # Check SPI interface
            task = progress.add_task("Checking SPI interface...", total=None)
            spi_ok = self._check_spi_enabled()
            results.append(("SPI Interface", spi_ok, "Required for LoRa HATs"))
            progress.remove_task(task)

            # Check for LoRa device
            task = progress.add_task("Detecting LoRa device...", total=None)
            lora_device = self._detect_lora_device()
            results.append(("LoRa Device", lora_device is not None, lora_device or "Not detected"))
            progress.remove_task(task)

            # Check meshtasticd radio status
            task = progress.add_task("Checking radio status...", total=None)
            radio_ok = self._check_radio_status()
            results.append(("Radio Status", radio_ok, "Via meshtasticd"))
            progress.remove_task(task)

        self._display_diagnostic_results("LoRa/Radio", results)

    def _check_spi_enabled(self) -> bool:
        """Check if SPI is enabled"""
        return Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists()

    def _detect_lora_device(self) -> Optional[str]:
        """Detect LoRa device type"""
        # Check USB devices
        try:
            result = subprocess.run(['lsusb'], capture_output=True, text=True)
            if result.returncode == 0:
                output = result.stdout.lower()
                if 'ch340' in output or 'cp210' in output or 'ft232' in output:
                    return "USB Serial LoRa Module"
        except Exception:
            pass

        # Check SPI devices
        if Path('/dev/spidev0.0').exists():
            return "SPI LoRa HAT"

        return None

    def _check_radio_status(self) -> bool:
        """Check radio status via meshtasticd"""
        from utils.cli import find_meshtastic_cli
        cli_path = find_meshtastic_cli()
        if not cli_path:
            return False

        try:
            result = subprocess.run(
                [cli_path, '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=15
            )
            return result.returncode == 0 and 'lora' in result.stdout.lower()
        except Exception:
            return False

    def gpio_spi_i2c_status(self):
        """Check GPIO, SPI, I2C status"""
        console.print("\n[bold cyan]GPIO/SPI/I2C Status[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Interface", style="cyan")
        table.add_column("Status", style="white")
        table.add_column("Details", style="dim")

        # SPI
        spi0 = Path('/dev/spidev0.0').exists()
        spi1 = Path('/dev/spidev0.1').exists()
        if spi0 or spi1:
            table.add_row("SPI", "[green]Enabled[/green]", f"spidev0.0: {'Yes' if spi0 else 'No'}, spidev0.1: {'Yes' if spi1 else 'No'}")
        else:
            table.add_row("SPI", "[yellow]Disabled[/yellow]", "Enable with: sudo raspi-config")

        # I2C
        i2c1 = Path('/dev/i2c-1').exists()
        i2c0 = Path('/dev/i2c-0').exists()
        if i2c1 or i2c0:
            devices = self._scan_i2c_devices()
            table.add_row("I2C", "[green]Enabled[/green]", f"Devices: {devices if devices else 'None detected'}")
        else:
            table.add_row("I2C", "[yellow]Disabled[/yellow]", "Enable with: sudo raspi-config")

        # GPIO access
        gpio_ok = Path('/dev/gpiochip0').exists() or Path('/dev/mem').exists()
        table.add_row("GPIO", "[green]Available[/green]" if gpio_ok else "[yellow]Limited[/yellow]",
                      "gpiochip0 present" if gpio_ok else "May need permissions")

        # Serial ports
        serial_ports = self._list_serial_ports()
        table.add_row("Serial Ports", f"[green]{len(serial_ports)} found[/green]" if serial_ports else "[dim]None[/dim]",
                      ", ".join(serial_ports[:3]) if serial_ports else "")

        console.print(table)

    def _scan_i2c_devices(self) -> str:
        """Scan for I2C devices"""
        try:
            result = subprocess.run(
                ['i2cdetect', '-y', '1'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                # Parse output for device addresses
                devices = []
                for line in result.stdout.split('\n')[1:]:
                    parts = line.split(':')
                    if len(parts) > 1:
                        for addr in parts[1].split():
                            if addr != '--' and addr != 'UU':
                                devices.append(f"0x{addr}")
                return ", ".join(devices) if devices else ""
        except Exception:
            pass
        return ""

    def _list_serial_ports(self) -> list:
        """List available serial ports"""
        ports = []
        for port in Path('/dev').glob('ttyUSB*'):
            ports.append(str(port))
        for port in Path('/dev').glob('ttyACM*'):
            ports.append(str(port))
        for port in Path('/dev').glob('ttyAMA*'):
            ports.append(str(port))
        return sorted(ports)

    def service_diagnostics(self):
        """Meshtasticd service diagnostics"""
        console.print("\n[bold cyan]Meshtasticd Service Diagnostics[/bold cyan]\n")

        results = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            # Check if installed
            task = progress.add_task("Checking installation...", total=None)
            installed = self._check_meshtasticd_installed()
            results.append(("Meshtasticd Installed", installed, "Package check"))
            progress.remove_task(task)

            if installed:
                # Check service status
                task = progress.add_task("Checking service status...", total=None)
                running = self._check_service_running()
                results.append(("Service Running", running, "systemd status"))
                progress.remove_task(task)

                # Check enabled on boot
                task = progress.add_task("Checking boot configuration...", total=None)
                enabled = self._check_service_enabled()
                results.append(("Enabled on Boot", enabled, "systemd enabled"))
                progress.remove_task(task)

                # Check config file
                task = progress.add_task("Checking configuration...", total=None)
                config_ok = Path('/etc/meshtasticd/config.yaml').exists()
                results.append(("Config File", config_ok, "/etc/meshtasticd/config.yaml"))
                progress.remove_task(task)

                # Check for recent errors
                task = progress.add_task("Checking for errors...", total=None)
                errors = self._check_recent_errors()
                results.append(("No Recent Errors", not errors, "Last 5 minutes"))
                progress.remove_task(task)

        self._display_diagnostic_results("Service Status", results)

        # Show recent errors if any
        if installed and errors:
            console.print("\n[yellow]Recent Errors:[/yellow]")
            for error in errors[:5]:
                console.print(f"  [red]• {error}[/red]")

    def _check_meshtasticd_installed(self) -> bool:
        """Check if meshtasticd is installed"""
        try:
            result = subprocess.run(
                ['dpkg', '-l', 'meshtasticd'],
                capture_output=True, text=True
            )
            return 'ii' in result.stdout
        except Exception:
            return False

    def _check_service_running(self) -> bool:
        """Check if meshtasticd service is running"""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True
            )
            return result.stdout.strip() == 'active'
        except Exception:
            return False

    def _check_service_enabled(self) -> bool:
        """Check if meshtasticd service is enabled on boot"""
        try:
            result = subprocess.run(
                ['systemctl', 'is-enabled', 'meshtasticd'],
                capture_output=True, text=True
            )
            return result.stdout.strip() == 'enabled'
        except Exception:
            return False

    def _check_recent_errors(self) -> list:
        """Check for recent errors in service logs"""
        try:
            result = subprocess.run(
                ['journalctl', '-u', 'meshtasticd', '--since', '5 min ago',
                 '-p', 'err', '--no-pager', '-q'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split('\n')
        except Exception:
            pass
        return []

    def log_analysis(self):
        """Analyze meshtasticd logs"""
        console.print("\n[bold cyan]Log Analysis[/bold cyan]\n")

        console.print("[dim]Analyzing last 100 log entries...[/dim]\n")

        stats = {
            'total': 0,
            'errors': 0,
            'warnings': 0,
            'packets_sent': 0,
            'packets_received': 0,
            'nodes_seen': set()
        }

        try:
            result = subprocess.run(
                ['journalctl', '-u', 'meshtasticd', '-n', '100', '--no-pager', '-q'],
                capture_output=True, text=True, timeout=15
            )

            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    stats['total'] += 1
                    line_lower = line.lower()

                    if 'error' in line_lower:
                        stats['errors'] += 1
                    if 'warning' in line_lower or 'warn' in line_lower:
                        stats['warnings'] += 1
                    if 'sent' in line_lower and 'packet' in line_lower:
                        stats['packets_sent'] += 1
                    if 'received' in line_lower or 'recv' in line_lower:
                        stats['packets_received'] += 1
                    # Look for node IDs
                    if '!' in line:
                        import re
                        nodes = re.findall(r'![a-f0-9]{8}', line_lower)
                        stats['nodes_seen'].update(nodes)

        except Exception as e:
            console.print(f"[red]Error analyzing logs: {e}[/red]")
            return

        # Display stats
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("Log Entries Analyzed", str(stats['total']))
        table.add_row("Errors", f"[red]{stats['errors']}[/red]" if stats['errors'] else "[green]0[/green]")
        table.add_row("Warnings", f"[yellow]{stats['warnings']}[/yellow]" if stats['warnings'] else "[green]0[/green]")
        table.add_row("Packets Sent", str(stats['packets_sent']))
        table.add_row("Packets Received", str(stats['packets_received']))
        table.add_row("Unique Nodes Seen", str(len(stats['nodes_seen'])))

        console.print(table)

        if stats['nodes_seen']:
            console.print(f"\n[dim]Nodes: {', '.join(sorted(stats['nodes_seen']))[:5]}...[/dim]")

    def run_all_diagnostics(self):
        """Run all diagnostics and generate report"""
        console.print("\n[bold cyan]═══════════════ Full System Diagnostic Report ═══════════════[/bold cyan]\n")

        all_results = {}

        # Network
        console.print("[bold]1/6 Network Connectivity...[/bold]")
        all_results['network'] = self._run_network_tests()

        # System Health
        console.print("[bold]2/6 System Health...[/bold]")
        self.system_health_check()

        # Service
        console.print("\n[bold]3/6 Service Status...[/bold]")
        all_results['service'] = self._run_service_tests()

        # LoRa
        console.print("[bold]4/6 LoRa/Radio...[/bold]")
        all_results['lora'] = self._run_lora_tests()

        # GPIO/SPI/I2C
        console.print("\n[bold]5/6 Interfaces...[/bold]")
        self.gpio_spi_i2c_status()

        # Summary
        console.print("\n[bold]6/6 Generating Summary...[/bold]")

        # Calculate overall health
        total_tests = 0
        passed_tests = 0
        for category, results in all_results.items():
            for name, passed, _ in results:
                total_tests += 1
                if passed:
                    passed_tests += 1

        health_percent = (passed_tests / total_tests * 100) if total_tests > 0 else 0

        console.print("\n" + "═" * 60)
        if health_percent >= 90:
            console.print(f"[bold green]System Health: {health_percent:.0f}% - Excellent[/bold green]")
        elif health_percent >= 70:
            console.print(f"[bold yellow]System Health: {health_percent:.0f}% - Good (some issues)[/bold yellow]")
        else:
            console.print(f"[bold red]System Health: {health_percent:.0f}% - Needs Attention[/bold red]")
        console.print("═" * 60)

    def _run_network_tests(self) -> list:
        """Run network tests and return results"""
        results = []
        results.append(("Localhost", self._ping("127.0.0.1", 1), ""))
        gateway = self._get_default_gateway()
        if gateway:
            results.append(("Gateway", self._ping(gateway, 1), gateway))
        results.append(("Internet", self._ping("8.8.8.8", 1), ""))
        results.append(("DNS", self._dns_resolve("meshtastic.org"), ""))
        self._display_diagnostic_results("Network", results)
        return results

    def _run_service_tests(self) -> list:
        """Run service tests and return results"""
        results = []
        results.append(("Installed", self._check_meshtasticd_installed(), ""))
        results.append(("Running", self._check_service_running(), ""))
        results.append(("Enabled", self._check_service_enabled(), ""))
        self._display_diagnostic_results("Service", results)
        return results

    def _run_lora_tests(self) -> list:
        """Run LoRa tests and return results"""
        results = []
        results.append(("SPI", self._check_spi_enabled(), ""))
        lora = self._detect_lora_device()
        results.append(("Device", lora is not None, lora or ""))
        self._display_diagnostic_results("LoRa", results)
        return results

    def _display_diagnostic_results(self, category: str, results: list):
        """Display diagnostic results in a table"""
        table = Table(show_header=True, header_style="bold magenta", title=f"{category} Diagnostics")
        table.add_column("Test", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Details", style="dim")

        for name, passed, details in results:
            status = f"[green]{em.get('✓', 'PASS')}[/green]" if passed else f"[red]{em.get('✗', 'FAIL')}[/red]"
            table.add_row(name, status, str(details))

        console.print(table)
