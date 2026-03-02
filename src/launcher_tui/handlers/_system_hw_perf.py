"""Hardware, performance, storage, service, and log operations for SystemToolsHandler.

Extracted from system_tools.py for file size compliance (CLAUDE.md #6).
Functions take the SystemToolsHandler instance for TUI interaction via handler.ctx.

Operations in this module:
  hardware_info_menu        - Hardware info submenu
  run_hardware_command      - Hardware command runner
  gpio_status               - GPIO status (Pi/SBC)
  spi_i2c_status            - SPI/I2C bus status
  performance_tools_menu    - Performance monitoring submenu
  run_performance_command   - Performance command runner
  stress_test_menu          - CPU/memory stress test
  storage_tools_menu        - Storage & disk submenu
  run_storage_command       - Storage command runner
  service_management_menu   - SystemD service submenu
  run_service_command       - Service command runner
  advanced_logs_menu        - Advanced log analysis submenu
  run_advanced_log_command  - Log command runner
"""

import subprocess
import shutil
from pathlib import Path
import logging

from backend import clear_screen
from utils.service_check import check_service, ServiceState, apply_config_and_restart
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Hardware Information
# ------------------------------------------------------------------

def hardware_info_menu(handler):
    """Hardware information tools."""
    choices = [
        ("lscpu", "lscpu (CPU Info)"),
        ("lsmem", "lsmem (Memory Layout)"),
        ("lsusb", "lsusb (USB Devices)"),
        ("lsusb_v", "lsusb -v (USB Verbose)"),
        ("lspci", "lspci (PCI Devices)"),
        ("lsblk", "lsblk (Block Devices)"),
        ("lshw", "lshw (Full Hardware Summary)"),
        ("dmidecode", "dmidecode (BIOS/System Info)"),
        ("sensors", "sensors (Temperature/Voltage)"),
        ("gpio", "GPIO Status (Pi/SBC)"),
        ("spi_i2c", "SPI/I2C Status"),
        ("uname", "uname -a (Kernel Info)"),
    ]
    handler.run_menu_loop(
        "Hardware Information", "System hardware details:",
        choices, default_handler=lambda cmd_type: run_hardware_command(handler, cmd_type),
    )


def run_hardware_command(handler, cmd_type: str):
    """Run hardware info command."""
    clear_screen()

    simple_commands = {
        'lscpu': (['lscpu'], "CPU Information"),
        'lsmem': (['lsmem'], "Memory Layout"),
        'lsusb': (['lsusb'], "USB Devices"),
        'lsusb_v': (['lsusb', '-v'], "USB Devices (Verbose)"),
        'lspci': (['lspci', '-v'], "PCI Devices"),
        'lsblk': (['lsblk', '-f'], "Block Devices"),
        'lshw': (['sudo', 'lshw', '-short'], "Hardware Summary"),
        'dmidecode': (['sudo', 'dmidecode', '-t', 'system'], "System Info (BIOS)"),
        'sensors': (['sensors'], "Sensors"),
        'uname': (['uname', '-a'], "Kernel Info"),
    }

    if cmd_type in simple_commands:
        cmd, title = simple_commands[cmd_type]
        print(f"=== {title} ===\n")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            print(result.stdout)
            if result.stderr and 'not found' in result.stderr.lower():
                print("\nTool not found. Install required package.")
        except FileNotFoundError:
            print("Command not found. Install required package.")
        except Exception as e:
            print(f"Error: {e}")

        print("\n" + "=" * 60)
        handler.ctx.wait_for_enter()

    elif cmd_type == 'gpio':
        gpio_status(handler)
    elif cmd_type == 'spi_i2c':
        spi_i2c_status(handler)


def gpio_status(handler):
    """Show GPIO status for Pi/SBC."""
    clear_screen()
    print("=== GPIO Status ===\n")

    # Try raspi-gpio first, then gpioinfo
    if shutil.which('raspi-gpio'):
        print("--- raspi-gpio get ---")
        subprocess.run(['raspi-gpio', 'get'], timeout=10)
    elif shutil.which('gpioinfo'):
        print("--- gpioinfo ---")
        subprocess.run(['gpioinfo'], timeout=10)
    elif Path('/sys/class/gpio').exists():
        print("--- /sys/class/gpio ---")
        for gpio_dir in Path('/sys/class/gpio').glob('gpio*'):
            if gpio_dir.is_dir() and gpio_dir.name.startswith('gpio') and gpio_dir.name[4:].isdigit():
                try:
                    direction = (gpio_dir / 'direction').read_text().strip()
                    value = (gpio_dir / 'value').read_text().strip()
                    print(f"{gpio_dir.name}: direction={direction}, value={value}")
                except OSError as e:
                    logger.debug("GPIO %s read failed: %s", gpio_dir.name, e)
    else:
        print("No GPIO interface found.")

    print("\n" + "=" * 60)
    handler.ctx.wait_for_enter()


def spi_i2c_status(handler):
    """Show SPI and I2C status."""
    clear_screen()
    print("=== SPI/I2C Status ===\n")

    # SPI
    print("--- SPI Devices ---")
    spi_devs = list(Path('/dev').glob('spidev*'))
    if spi_devs:
        for dev in spi_devs:
            print(f"  {dev}")
    else:
        print("  No SPI devices found (enable with raspi-config or modprobe)")

    # I2C
    print("\n--- I2C Devices ---")
    i2c_devs = list(Path('/dev').glob('i2c-*'))
    if i2c_devs:
        for dev in i2c_devs:
            print(f"  {dev}")
            # Try i2cdetect
            if shutil.which('i2cdetect'):
                bus = dev.name.split('-')[1]
                print(f"    Scanning bus {bus}...")
                result = subprocess.run(
                    ['i2cdetect', '-y', bus],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.split('\n'):
                    if line.strip():
                        print(f"    {line}")
    else:
        print("  No I2C devices found")

    print("\n" + "=" * 60)
    handler.ctx.wait_for_enter()


# ------------------------------------------------------------------
# Performance Tools
# ------------------------------------------------------------------

def performance_tools_menu(handler):
    """Performance monitoring tools."""
    choices = [
        ("free", "free -h (Memory Usage)"),
        ("vmstat", "vmstat (Virtual Memory Stats)"),
        ("vmstat_live", "vmstat 1 (Live - 1s interval)"),
        ("iostat", "iostat (I/O Statistics)"),
        ("mpstat", "mpstat (CPU per Core)"),
        ("uptime", "uptime (Load Average)"),
        ("sar", "sar (System Activity)"),
        ("stress", "Stress Test (careful!)"),
    ]
    handler.run_menu_loop(
        "Performance Tools", "System performance analysis:",
        choices, default_handler=lambda cmd_type: run_performance_command(handler, cmd_type),
    )


def run_performance_command(handler, cmd_type: str):
    """Run performance monitoring command."""
    clear_screen()

    simple_commands = {
        'free': (['free', '-h'], "Memory Usage"),
        'vmstat': (['vmstat', '-w'], "Virtual Memory Stats"),
        'iostat': (['iostat', '-x', '1', '3'], "I/O Statistics"),
        'mpstat': (['mpstat', '-P', 'ALL'], "CPU per Core"),
        'uptime': (['uptime'], "System Uptime & Load"),
        'sar': (['sar', '-u', '1', '5'], "CPU Activity (5 samples)"),
    }

    if cmd_type in simple_commands:
        cmd, title = simple_commands[cmd_type]
        print(f"=== {title} ===\n")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            print(result.stdout)
        except FileNotFoundError:
            print("Command not found. Install: sudo apt install sysstat")
        except Exception as e:
            print(f"Error: {e}")

        print("\n" + "=" * 60)
        handler.ctx.wait_for_enter()

    elif cmd_type == 'vmstat_live':
        print("=== vmstat 1 (Live - Ctrl+C to stop) ===\n")
        try:
            subprocess.run(['vmstat', '1'], timeout=None)
        except KeyboardInterrupt:
            print("\n\nStopped.")
        handler.ctx.wait_for_enter()

    elif cmd_type == 'stress':
        stress_test_menu(handler)


def stress_test_menu(handler):
    """CPU/Memory stress test (careful!)."""
    confirm = handler.ctx.dialog.yesno(
        "Stress Test",
        "WARNING: This will stress your system!\n\n"
        "Only use for testing stability.\n"
        "System may become unresponsive.\n\n"
        "Continue?"
    )

    if not confirm:
        return

    if not shutil.which('stress'):
        handler.ctx.dialog.msgbox(
            "Not Installed",
            "stress tool not found.\n\n"
            "Install: sudo apt install stress"
        )
        return

    duration = handler.ctx.dialog.inputbox(
        "Duration",
        "Stress test duration in seconds:",
        "10"
    )

    try:
        duration = int(duration) if duration else 10
    except ValueError:
        duration = 10

    clear_screen()
    print(f"=== Stress Test ({duration}s) ===\n")
    print("Running CPU stress test...\n")

    try:
        subprocess.run(
            ['stress', '--cpu', '2', '--timeout', str(duration)],
            timeout=duration + 10
        )
        print("\nStress test completed!")
    except Exception as e:
        print(f"Error: {e}")

    handler.ctx.wait_for_enter()


# ------------------------------------------------------------------
# Storage Tools
# ------------------------------------------------------------------

def storage_tools_menu(handler):
    """Storage and disk tools."""
    choices = [
        ("df", "df -h (Disk Free Space)"),
        ("du_home", "du (Home Directory Usage)"),
        ("du_etc", "du /etc/meshtasticd (Config Size)"),
        ("mount", "mount (Mounted Filesystems)"),
        ("findmnt", "findmnt (Mount Tree)"),
        ("fdisk", "fdisk -l (Partition Table)"),
        ("smartctl", "smartctl (Disk Health)"),
        ("ncdu", "ncdu (Interactive Disk Usage)"),
    ]
    handler.run_menu_loop(
        "Storage Tools", "Disk and storage analysis:",
        choices, default_handler=lambda cmd_type: run_storage_command(handler, cmd_type),
    )


def run_storage_command(handler, cmd_type: str):
    """Run storage command."""
    clear_screen()

    if cmd_type == 'df':
        print("=== Disk Free Space ===\n")
        subprocess.run(['df', '-h'], timeout=10)

    elif cmd_type == 'du_home':
        print("=== Home Directory Usage (top 20) ===\n")
        try:
            result = subprocess.run(
                ['du', '-h', '--max-depth=1', str(get_real_user_home())],
                capture_output=True, text=True, timeout=60
            )
            lines = sorted(result.stdout.strip().split('\n'), key=lambda x: x.split()[0] if x else '0')
            for line in lines[-20:]:
                print(line)
        except Exception as e:
            print(f"Error: {e}")

    elif cmd_type == 'du_etc':
        print("=== /etc/meshtasticd Size ===\n")
        subprocess.run(['du', '-ah', '/etc/meshtasticd'], timeout=30)

    elif cmd_type == 'mount':
        print("=== Mounted Filesystems ===\n")
        subprocess.run(['mount'], timeout=10)

    elif cmd_type == 'findmnt':
        print("=== Mount Tree ===\n")
        subprocess.run(['findmnt'], timeout=10)

    elif cmd_type == 'fdisk':
        print("=== Partition Table ===\n")
        subprocess.run(['sudo', 'fdisk', '-l'], timeout=10)

    elif cmd_type == 'smartctl':
        print("=== Disk Health (SMART) ===\n")
        if not shutil.which('smartctl'):
            print("smartctl not found. Install: sudo apt install smartmontools")
        else:
            # Find first disk
            result = subprocess.run(['lsblk', '-d', '-o', 'NAME'], capture_output=True, text=True, timeout=10)
            disks = [l.strip() for l in result.stdout.split('\n')[1:] if l.strip() and not l.strip().startswith('loop')]
            if disks:
                subprocess.run(['sudo', 'smartctl', '-H', f'/dev/{disks[0]}'], timeout=30)
            else:
                print("No disks found")

    elif cmd_type == 'ncdu':
        if shutil.which('ncdu'):
            print("=== Interactive Disk Usage (q to quit) ===\n")
            subprocess.run(['ncdu', '/'], timeout=None)
        else:
            print("ncdu not found. Install: sudo apt install ncdu")

    print("\n" + "=" * 60)
    handler.ctx.wait_for_enter()


# ------------------------------------------------------------------
# Service Management
# ------------------------------------------------------------------

def service_management_menu(handler):
    """SystemD service management."""
    choices = [
        ("status_all", "All Services Status"),
        ("status_mesh", "Mesh Services Status"),
        ("failed", "Failed Services"),
        ("timers", "System Timers"),
        ("recent", "Recently Changed"),
        ("analyze", "Boot Time Analysis"),
        ("logs_unit", "Logs for Specific Unit"),
        ("restart", "Restart a Service"),
    ]
    handler.run_menu_loop(
        "Service Management", "SystemD service control:",
        choices, default_handler=lambda cmd_type: run_service_command(handler, cmd_type),
    )


def run_service_command(handler, cmd_type: str):
    """Run service management command."""
    clear_screen()

    if cmd_type == 'status_all':
        print("=== All Services Status ===\n")
        subprocess.run(['systemctl', 'list-units', '--type=service', '--no-pager'], timeout=30)

    elif cmd_type == 'status_mesh':
        print("=== Mesh Services Status ===\n")
        for svc in ['meshtasticd', 'rnsd', 'lxmf.delivery', 'nomadnetd']:
            print(f"\n--- {svc} ---")
            result = subprocess.run(
                ['systemctl', 'status', svc, '--no-pager', '-l'],
                capture_output=True, text=True, timeout=10
            )
            print(result.stdout[:1000] if result.stdout else "Not found")

    elif cmd_type == 'failed':
        print("=== Failed Services ===\n")
        subprocess.run(['systemctl', '--failed', '--no-pager'], timeout=10)

    elif cmd_type == 'timers':
        print("=== System Timers ===\n")
        subprocess.run(['systemctl', 'list-timers', '--no-pager'], timeout=10)

    elif cmd_type == 'recent':
        print("=== Recently Changed Services ===\n")
        subprocess.run(
            ['systemctl', 'list-units', '--type=service', '--state=activating,deactivating,reloading', '--no-pager'],
            timeout=10
        )

    elif cmd_type == 'analyze':
        print("=== Boot Time Analysis ===\n")
        subprocess.run(['systemd-analyze'], timeout=10)
        print("\n--- Blame (slowest units) ---")
        subprocess.run(['systemd-analyze', 'blame', '--no-pager'], timeout=10)

    elif cmd_type == 'logs_unit':
        unit = handler.ctx.dialog.inputbox(
            "Service Logs",
            "Enter service/unit name:",
            "meshtasticd"
        )
        if unit:
            print(f"\n=== Logs for {unit} ===\n")
            subprocess.run(['journalctl', '-u', unit, '-n', '100', '--no-pager'], timeout=30)

    elif cmd_type == 'restart':
        unit = handler.ctx.dialog.inputbox(
            "Restart Service",
            "Enter service to restart:",
            "meshtasticd"
        )
        if unit:
            confirm = handler.ctx.dialog.yesno(
                "Confirm Restart",
                f"Restart {unit}?"
            )
            if confirm:
                print(f"\n=== Restarting {unit} ===\n")
                success, msg = apply_config_and_restart(unit)
                print(msg)
                subprocess.run(['systemctl', 'status', unit, '--no-pager'], timeout=10)

    print("\n" + "=" * 60)
    handler.ctx.wait_for_enter()


# ------------------------------------------------------------------
# Advanced Log Analysis
# ------------------------------------------------------------------

def advanced_logs_menu(handler):
    """Advanced log analysis tools."""
    choices = [
        ("journal_size", "Journal Disk Usage"),
        ("journal_boots", "Previous Boots"),
        ("priority", "Filter by Priority"),
        ("since", "Logs Since Time"),
        ("grep_logs", "Search Logs (grep)"),
        ("export", "Export Logs to File"),
        ("rotate", "Rotate Logs Now"),
        ("clear_journal", "Clear Old Journals"),
    ]
    handler.run_menu_loop(
        "Advanced Log Analysis", "Deep log inspection:",
        choices, default_handler=lambda cmd_type: run_advanced_log_command(handler, cmd_type),
    )


def run_advanced_log_command(handler, cmd_type: str):
    """Run advanced log command."""
    clear_screen()

    if cmd_type == 'journal_size':
        print("=== Journal Disk Usage ===\n")
        subprocess.run(['journalctl', '--disk-usage'], timeout=10)

    elif cmd_type == 'journal_boots':
        print("=== Previous Boots ===\n")
        subprocess.run(['journalctl', '--list-boots'], timeout=10)
        boot = handler.ctx.dialog.inputbox(
            "View Boot",
            "Enter boot number (0=current, -1=previous):",
            "0"
        )
        if boot:
            subprocess.run(['journalctl', '-b', boot, '-n', '50', '--no-pager'], timeout=30)

    elif cmd_type == 'priority':
        choices = [
            ("0", "emerg - System is unusable"),
            ("1", "alert - Action must be taken"),
            ("2", "crit - Critical conditions"),
            ("3", "err - Error conditions"),
            ("4", "warning - Warning conditions"),
            ("5", "notice - Normal but significant"),
            ("6", "info - Informational"),
            ("7", "debug - Debug messages"),
        ]
        prio = handler.ctx.dialog.menu("Log Priority", "Show logs at priority level and above:", choices)
        if prio:
            subprocess.run(['journalctl', '-p', prio, '-n', '100', '--no-pager'], timeout=30)

    elif cmd_type == 'since':
        since = handler.ctx.dialog.inputbox(
            "Logs Since",
            "Enter time (e.g., '1 hour ago', '2024-01-20', 'today'):",
            "1 hour ago"
        )
        if since:
            subprocess.run(['journalctl', '--since', since, '-n', '100', '--no-pager'], timeout=30)

    elif cmd_type == 'grep_logs':
        pattern = handler.ctx.dialog.inputbox(
            "Search Logs",
            "Enter search pattern:",
            "error"
        )
        if pattern:
            print(f"=== Searching for: {pattern} ===\n")
            subprocess.run(['journalctl', '-g', pattern, '-n', '50', '--no-pager'], timeout=30)

    elif cmd_type == 'export':
        filename = handler.ctx.dialog.inputbox(
            "Export Logs",
            "Export filename:",
            "/tmp/meshforge_logs.txt"
        )
        if filename:
            export_path = Path(filename).resolve()
            safe_dirs = [Path('/tmp'), get_real_user_home(), Path('/var/log')]
            if not any(str(export_path).startswith(str(d.resolve())) for d in safe_dirs):
                handler.ctx.dialog.msgbox(
                    "Error",
                    "Export path must be under /tmp, home directory, or /var/log"
                )
                return
            print(f"=== Exporting to {export_path} ===\n")
            with open(str(export_path), 'w') as f:
                subprocess.run(
                    ['journalctl', '-u', 'meshtasticd', '-u', 'rnsd', '-n', '1000', '--no-pager'],
                    stdout=f, timeout=60
                )
            print(f"Logs exported to: {export_path}")

    elif cmd_type == 'rotate':
        print("=== Rotating Logs ===\n")
        subprocess.run(['sudo', 'journalctl', '--rotate'], timeout=30)
        print("Done!")

    elif cmd_type == 'clear_journal':
        confirm = handler.ctx.dialog.yesno(
            "Clear Journals",
            "Clear journal logs older than 2 days?\n\n"
            "This frees disk space but removes history."
        )
        if confirm:
            subprocess.run(['sudo', 'journalctl', '--vacuum-time=2d'], timeout=60)

    print("\n" + "=" * 60)
    handler.ctx.wait_for_enter()
