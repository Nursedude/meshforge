"""
MeshForge Setup Wizard - Interactive First-Run Configuration

Detects existing services, shows status, guides user through setup decisions,
and logs all choices for troubleshooting.

Usage:
    python3 -m src.setup_wizard          # Run wizard
    python3 -m src.setup_wizard --check  # Just check status, no prompts
"""

import os
import sys
import shutil
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum

# Setup logging
logger = logging.getLogger(__name__)


class ServiceState(Enum):
    """Service states"""
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class ServiceStatus:
    """Status of a detected service"""
    name: str
    display_name: str
    state: ServiceState
    version: Optional[str] = None
    path: Optional[str] = None
    systemd_unit: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class WizardDecision:
    """A logged decision made during setup"""
    timestamp: str
    category: str
    question: str
    choice: str
    result: str


class SetupWizard:
    """
    Interactive setup wizard for MeshForge first-run configuration.

    Detects services, shows status, asks for user input, logs decisions.
    """

    SERVICES = [
        {
            'name': 'meshtasticd',
            'display': 'Meshtastic Daemon',
            'check_cmd': ['meshtasticd', '--version'],
            'systemd': 'meshtasticd.service',
            'install_hint': 'See: https://meshtastic.org/docs/software/linux-native',
        },
        {
            'name': 'rnsd',
            'display': 'Reticulum Network Stack',
            'check_cmd': ['rnsd', '--version'],
            'systemd': 'rnsd.service',
            'install_hint': 'pip install rns',
        },
        {
            'name': 'nomadnet',
            'display': 'NomadNet',
            'check_cmd': ['nomadnet', '--version'],
            'systemd': None,
            'install_hint': 'pip install nomadnet',
        },
        {
            'name': 'meshchat',
            'display': 'MeshChat',
            'check_cmd': ['meshchat', '--version'],
            'systemd': None,
            'install_hint': 'pip install meshchat',
        },
        {
            'name': 'lxmf',
            'display': 'LXMF (messaging)',
            'check_cmd': ['lxmd', '--version'],
            'systemd': None,
            'install_hint': 'pip install lxmf',
        },
    ]

    def __init__(self, log_dir: Optional[Path] = None, interactive: bool = True):
        """
        Initialize the setup wizard.

        Args:
            log_dir: Directory for setup logs. Defaults to ~/.meshforge/logs/
            interactive: If True, prompt for user input. If False, just report.
        """
        self.interactive = interactive
        self.decisions: List[WizardDecision] = []
        self.service_status: Dict[str, ServiceStatus] = {}

        # Setup log directory
        if log_dir:
            self.log_dir = Path(log_dir)
        else:
            home = self._get_real_home()
            self.log_dir = home / ".meshforge" / "logs"

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / f"setup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        # Setup file logging
        self._setup_logging()

    def _get_real_home(self) -> Path:
        """Get real user home even when running as sudo"""
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user:
            return Path(f"/home/{sudo_user}")
        return Path.home()

    def _setup_logging(self):
        """Setup file logging for the wizard"""
        file_handler = logging.FileHandler(self.log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s'
        ))
        logger.addHandler(file_handler)
        logger.setLevel(logging.DEBUG)

    def _log(self, message: str, level: str = "INFO"):
        """Log a message to file and optionally console"""
        if level == "INFO":
            logger.info(message)
        elif level == "WARNING":
            logger.warning(message)
        elif level == "ERROR":
            logger.error(message)
        else:
            logger.debug(message)

    def _print(self, message: str, style: str = "normal"):
        """Print to console with optional styling"""
        styles = {
            "normal": "",
            "header": "\033[1;36m",  # Bold cyan
            "success": "\033[0;32m",  # Green
            "warning": "\033[1;33m",  # Yellow
            "error": "\033[0;31m",    # Red
            "dim": "\033[0;90m",      # Gray
        }
        reset = "\033[0m"
        prefix = styles.get(style, "")
        print(f"{prefix}{message}{reset}")

    def _ask(self, question: str, options: List[str] = None, default: str = None) -> str:
        """
        Ask user a question and log the decision.

        Args:
            question: The question to ask
            options: List of valid options (e.g., ['y', 'n'])
            default: Default value if user presses Enter

        Returns:
            User's response
        """
        if not self.interactive:
            return default or ""

        prompt = question
        if options:
            prompt += f" [{'/'.join(options)}]"
        if default:
            prompt += f" (default: {default})"
        prompt += ": "

        while True:
            try:
                response = input(prompt).strip().lower()
                if not response and default:
                    response = default
                if options and response not in [o.lower() for o in options]:
                    self._print(f"Please enter one of: {', '.join(options)}", "warning")
                    continue
                return response
            except (EOFError, KeyboardInterrupt):
                self._print("\nSetup cancelled.", "warning")
                sys.exit(1)

    def _record_decision(self, category: str, question: str, choice: str, result: str):
        """Record a decision for logging"""
        decision = WizardDecision(
            timestamp=datetime.now().isoformat(),
            category=category,
            question=question,
            choice=choice,
            result=result
        )
        self.decisions.append(decision)
        self._log(f"DECISION: [{category}] {question} -> {choice} = {result}")

    def detect_services(self) -> Dict[str, ServiceStatus]:
        """Detect all services and their status"""
        self._print("\n=== Detecting Services ===", "header")
        self._log("Starting service detection...")

        for svc in self.SERVICES:
            status = self._check_service(svc)
            self.service_status[svc['name']] = status

            # Print status
            state_icons = {
                ServiceState.RUNNING: ("RUNNING", "success"),
                ServiceState.INSTALLED: ("INSTALLED", "warning"),
                ServiceState.STOPPED: ("STOPPED", "warning"),
                ServiceState.NOT_INSTALLED: ("NOT FOUND", "dim"),
                ServiceState.ERROR: ("ERROR", "error"),
            }
            icon, style = state_icons.get(status.state, ("?", "normal"))

            version_str = f" v{status.version}" if status.version else ""
            self._print(f"  {status.display_name}: {icon}{version_str}", style)

            for note in status.notes:
                self._print(f"    {note}", "dim")

        return self.service_status

    def _check_service(self, svc: dict) -> ServiceStatus:
        """Check status of a single service"""
        status = ServiceStatus(
            name=svc['name'],
            display_name=svc['display'],
            state=ServiceState.NOT_INSTALLED
        )

        # Check if command exists
        cmd_path = shutil.which(svc['name'])
        if not cmd_path:
            # Try common paths
            for path in ['/usr/bin', '/usr/local/bin', str(self._get_real_home() / '.local/bin')]:
                test_path = Path(path) / svc['name']
                if test_path.exists():
                    cmd_path = str(test_path)
                    break

        if cmd_path:
            status.path = cmd_path
            status.state = ServiceState.INSTALLED

            # Try to get version
            try:
                result = subprocess.run(
                    svc['check_cmd'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    # Extract version from output
                    output = result.stdout.strip() or result.stderr.strip()
                    if output:
                        # Try to find version number
                        import re
                        match = re.search(r'(\d+\.\d+\.?\d*)', output)
                        if match:
                            status.version = match.group(1)
            except Exception as e:
                status.notes.append(f"Could not get version: {e}")

        # Check systemd service if applicable
        if svc.get('systemd'):
            status.systemd_unit = svc['systemd']
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', svc['systemd']],
                    capture_output=True, text=True, timeout=5
                )
                if result.stdout.strip() == 'active':
                    status.state = ServiceState.RUNNING
                    status.notes.append("Running as systemd service")
                elif status.state == ServiceState.INSTALLED:
                    status.state = ServiceState.STOPPED
            except Exception:
                pass

        # Check if running as process (fallback)
        if status.state != ServiceState.RUNNING:
            try:
                result = subprocess.run(
                    ['pgrep', '-f', svc['name']],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    # Verify it's not just our check
                    pids = result.stdout.strip().split('\n')
                    our_pid = str(os.getpid())
                    other_pids = [p for p in pids if p != our_pid]
                    if other_pids:
                        status.state = ServiceState.RUNNING
                        status.notes.append(f"Running as process (PID: {other_pids[0]})")
            except Exception:
                pass

        self._log(f"Service {svc['name']}: {status.state.value}")
        return status

    def show_summary(self):
        """Show summary of detected services"""
        self._print("\n=== Current Status ===", "header")

        running = [s for s in self.service_status.values() if s.state == ServiceState.RUNNING]
        installed = [s for s in self.service_status.values() if s.state in [ServiceState.INSTALLED, ServiceState.STOPPED]]
        missing = [s for s in self.service_status.values() if s.state == ServiceState.NOT_INSTALLED]

        if running:
            self._print(f"\nRunning ({len(running)}):", "success")
            for s in running:
                self._print(f"  - {s.display_name}", "success")

        if installed:
            self._print(f"\nInstalled but not running ({len(installed)}):", "warning")
            for s in installed:
                self._print(f"  - {s.display_name}", "warning")

        if missing:
            self._print(f"\nNot installed ({len(missing)}):", "dim")
            for s in missing:
                self._print(f"  - {s.display_name}", "dim")

    def run_interactive_setup(self):
        """Run the interactive setup wizard"""
        self._print("\n" + "="*60, "header")
        self._print("  MeshForge Setup Wizard", "header")
        self._print("="*60, "header")
        self._print("\nThis wizard will detect installed services and guide you")
        self._print("through configuration options.\n")
        self._log("Starting interactive setup wizard")

        # Detect services
        self.detect_services()
        self.show_summary()

        # Interactive configuration
        self._print("\n=== Configuration ===", "header")

        # Check meshtasticd
        mesh_status = self.service_status.get('meshtasticd')
        if mesh_status:
            if mesh_status.state == ServiceState.NOT_INSTALLED:
                self._print("\nmeshtasticd is not installed.", "warning")
                self._print("This is required for Meshtastic radio communication.", "dim")
                choice = self._ask("Would you like installation instructions?", ['y', 'n'], 'y')
                self._record_decision("meshtasticd", "Show install instructions?", choice,
                                      "Shown" if choice == 'y' else "Skipped")
                if choice == 'y':
                    self._print("\nInstall meshtasticd:", "header")
                    self._print("  See: https://meshtastic.org/docs/software/linux-native", "dim")
                    self._print("  Or use the Meshtastic Flasher for Raspberry Pi", "dim")
            elif mesh_status.state == ServiceState.STOPPED:
                choice = self._ask("\nmeshtasticd is installed but not running. Start it?", ['y', 'n'], 'y')
                self._record_decision("meshtasticd", "Start service?", choice, "")
                if choice == 'y':
                    self._start_service('meshtasticd')

        # Check rnsd
        rns_status = self.service_status.get('rnsd')
        if rns_status:
            if rns_status.state == ServiceState.NOT_INSTALLED:
                choice = self._ask("\nrnsd (Reticulum) is not installed. Install now?", ['y', 'n'], 'n')
                self._record_decision("rnsd", "Install?", choice, "")
                if choice == 'y':
                    self._install_package('rns', 'rnsd')
            elif rns_status.state == ServiceState.STOPPED:
                choice = self._ask("\nrnsd is installed but not running. Start it?", ['y', 'n'], 'y')
                self._record_decision("rnsd", "Start service?", choice, "")
                if choice == 'y':
                    self._start_service('rnsd')
            elif rns_status.state == ServiceState.RUNNING:
                # Check if systemd service exists
                if not self._check_systemd_exists('rnsd'):
                    choice = self._ask("\nrnsd is running but no systemd service. Create service for auto-start?", ['y', 'n'], 'y')
                    self._record_decision("rnsd", "Create systemd service?", choice, "")
                    if choice == 'y':
                        self._create_rnsd_service()

        # Save decisions log
        self._save_decisions()

        self._print("\n=== Setup Complete ===", "header")
        self._print(f"Log saved to: {self.log_file}", "dim")
        self._print("\nRun 'meshforge' to start the application.\n", "success")

    def _start_service(self, name: str):
        """Start a service"""
        self._print(f"Starting {name}...", "dim")
        try:
            # Try systemd first
            result = subprocess.run(
                ['systemctl', 'start', name],
                capture_output=True, timeout=30
            )
            if result.returncode == 0:
                self._print(f"  {name} started successfully", "success")
                self._record_decision(name, "Start service", "attempted", "success")
                return True
        except Exception:
            pass

        # Try direct start
        try:
            subprocess.Popen(
                [name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            self._print(f"  {name} started as background process", "success")
            self._record_decision(name, "Start service", "attempted", "success (process)")
            return True
        except Exception as e:
            self._print(f"  Failed to start {name}: {e}", "error")
            self._record_decision(name, "Start service", "attempted", f"failed: {e}")
            return False

    def _install_package(self, package: str, name: str):
        """Install a pip package"""
        self._print(f"Installing {package}...", "dim")
        try:
            cmd = [sys.executable, '-m', 'pip', 'install',
                   '--break-system-packages', '--ignore-installed', package]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                self._print(f"  {package} installed successfully", "success")
                self._record_decision(name, "Install package", package, "success")
                return True
            else:
                self._print(f"  Install failed: {result.stderr[:100]}", "error")
                self._record_decision(name, "Install package", package, f"failed: {result.stderr[:50]}")
                return False
        except Exception as e:
            self._print(f"  Install error: {e}", "error")
            self._record_decision(name, "Install package", package, f"error: {e}")
            return False

    def _check_systemd_exists(self, name: str) -> bool:
        """Check if a systemd service file exists"""
        return Path(f'/etc/systemd/system/{name}.service').exists()

    def _create_rnsd_service(self):
        """Create rnsd systemd service"""
        self._print("Creating rnsd systemd service...", "dim")
        try:
            rnsd_path = shutil.which('rnsd') or '/usr/local/bin/rnsd'
            service_content = f'''[Unit]
Description=Reticulum Network Stack Daemon
After=network.target

[Service]
Type=simple
ExecStart={rnsd_path}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
'''
            service_path = '/etc/systemd/system/rnsd.service'
            with open(service_path, 'w') as f:
                f.write(service_content)

            subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
            subprocess.run(['systemctl', 'enable', 'rnsd'], check=True, timeout=30)

            self._print("  rnsd service created and enabled", "success")
            self._record_decision("rnsd", "Create systemd service", "created", "success")
            return True
        except PermissionError:
            self._print("  Permission denied - run as root", "error")
            self._record_decision("rnsd", "Create systemd service", "attempted", "permission denied")
            return False
        except Exception as e:
            self._print(f"  Failed: {e}", "error")
            self._record_decision("rnsd", "Create systemd service", "attempted", f"failed: {e}")
            return False

    def _save_decisions(self):
        """Save all decisions to log file"""
        with open(self.log_file, 'a') as f:
            f.write("\n" + "="*60 + "\n")
            f.write("SETUP DECISIONS SUMMARY\n")
            f.write("="*60 + "\n")
            for d in self.decisions:
                f.write(f"{d.timestamp} | {d.category} | {d.question} | {d.choice} | {d.result}\n")
            f.write("="*60 + "\n")

    def check_first_run(self) -> bool:
        """Check if this is a first run (no marker file exists)"""
        marker = self._get_real_home() / ".meshforge" / ".setup_complete"
        return not marker.exists()

    def mark_setup_complete(self):
        """Mark setup as complete"""
        marker = self._get_real_home() / ".meshforge" / ".setup_complete"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now().isoformat())
        self._log("Setup marked as complete")


def run_wizard(interactive: bool = True, force: bool = False):
    """
    Run the setup wizard.

    Args:
        interactive: If True, prompt for user input
        force: If True, run even if setup was already completed
    """
    wizard = SetupWizard(interactive=interactive)

    if not force and not wizard.check_first_run():
        print("Setup already completed. Use --force to run again.")
        return

    wizard.run_interactive_setup()

    if interactive:
        wizard.mark_setup_complete()


def check_status():
    """Just check and display status without prompts"""
    wizard = SetupWizard(interactive=False)
    wizard.detect_services()
    wizard.show_summary()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MeshForge Setup Wizard")
    parser.add_argument('--check', action='store_true', help="Just check status, no prompts")
    parser.add_argument('--force', action='store_true', help="Run wizard even if already completed")
    args = parser.parse_args()

    if args.check:
        check_status()
    else:
        run_wizard(interactive=True, force=args.force)
