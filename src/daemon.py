#!/usr/bin/env python3
"""
MeshForge Daemon — Headless NOC Service Manager

Runs MeshForge core services without TUI dependency.
Designed for long-running unattended operation (days/weeks).

Usage:
    python3 src/daemon.py start [--profile <name>] [--foreground]
    python3 src/daemon.py stop
    python3 src/daemon.py status [--json]
    python3 src/daemon.py restart
    python3 src/daemon.py reload

Via systemd:
    systemctl start meshforge-daemon
    systemctl status meshforge-daemon

Services managed (each toggleable via daemon.yaml):
    - Gateway bridge (RNS <-> Meshtastic)
    - Health monitoring (ActiveHealthProbe)
    - MQTT subscriber (nodeless monitoring)
    - Config API server (REST on localhost)
    - Map server (coverage HTTP server)
    - Telemetry poller (silent node detection)
    - Node tracker (discovery and monitoring)
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Ensure src/ is in path
_src_dir = Path(__file__).parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from __version__ import __version__
from daemon_config import DaemonConfig
from utils.event_bus import event_bus
from utils.paths import get_real_user_home
from utils.safe_import import safe_import

# Optional imports — daemon degrades gracefully if modules missing
load_or_detect_profile, get_profile_by_name, _HAS_PROFILES = safe_import(
    'utils.deployment_profiles', 'load_or_detect_profile', 'get_profile_by_name'
)

logger = logging.getLogger("meshforged")


# =============================================================================
# DaemonService Protocol
# =============================================================================

class DaemonService:
    """Base class for services managed by the daemon.

    Subclasses must implement start(), stop(), is_alive(), get_status().
    """

    name: str = "unnamed"

    def start(self) -> bool:
        """Start the service. Returns True on success."""
        raise NotImplementedError

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the service gracefully."""
        raise NotImplementedError

    def is_alive(self) -> bool:
        """Check if the service is still running."""
        raise NotImplementedError

    def get_status(self) -> dict:
        """Get service status for reporting."""
        return {"name": self.name, "alive": self.is_alive()}


# =============================================================================
# Service Wrappers
# =============================================================================

class GatewayBridgeService(DaemonService):
    """Wraps gateway_cli.py headless bridge API."""

    name = "gateway_bridge"

    def start(self) -> bool:
        try:
            from gateway.gateway_cli import start_gateway_headless
            return start_gateway_headless()
        except Exception as e:
            logger.error(f"Gateway start failed: {e}")
            return False

    def stop(self, timeout: float = 5.0) -> None:
        try:
            from gateway.gateway_cli import stop_gateway_headless
            stop_gateway_headless()
        except Exception as e:
            logger.error(f"Gateway stop error: {e}")

    def is_alive(self) -> bool:
        try:
            from gateway.gateway_cli import is_gateway_running
            return is_gateway_running()
        except Exception:
            return False

    def get_status(self) -> dict:
        try:
            from gateway.gateway_cli import get_gateway_stats
            stats = get_gateway_stats()
            stats["name"] = self.name
            return stats
        except Exception:
            return {"name": self.name, "alive": False, "status": "error"}


class HealthProbeService(DaemonService):
    """Wraps ActiveHealthProbe singleton."""

    name = "health_probe"

    def __init__(self, interval: int = 30):
        self._interval = interval
        self._probe = None

    def start(self) -> bool:
        try:
            from utils.active_health_probe import get_health_probe
            self._probe = get_health_probe(interval=self._interval)
            self._probe.start()
            return True
        except Exception as e:
            logger.error(f"Health probe start failed: {e}")
            return False

    def stop(self, timeout: float = 5.0) -> None:
        if self._probe:
            self._probe.stop(timeout=timeout)

    def is_alive(self) -> bool:
        if not self._probe or not self._probe._thread:
            return False
        return self._probe._thread.is_alive()

    def get_status(self) -> dict:
        if not self._probe:
            return {"name": self.name, "alive": False}
        return {
            "name": self.name,
            "alive": self.is_alive(),
            "checks": self._probe.get_all_status(),
        }


class MQTTSubscriberService(DaemonService):
    """Wraps MQTTNodelessSubscriber for packet monitoring."""

    name = "mqtt_subscriber"

    def __init__(self, broker: str = "localhost", port: int = 1883):
        self._broker = broker
        self._port = port
        self._subscriber = None

    def start(self) -> bool:
        try:
            from monitoring.mqtt_subscriber import MQTTNodelessSubscriber
            self._subscriber = MQTTNodelessSubscriber(
                broker=self._broker, port=self._port
            )
            self._subscriber.start()
            return True
        except Exception as e:
            logger.error(f"MQTT subscriber start failed: {e}")
            return False

    def stop(self, timeout: float = 5.0) -> None:
        if self._subscriber:
            try:
                self._subscriber.stop()
            except Exception as e:
                logger.error(f"MQTT subscriber stop error: {e}")

    def is_alive(self) -> bool:
        if not self._subscriber:
            return False
        return getattr(self._subscriber, '_running', False)

    def get_status(self) -> dict:
        status = {"name": self.name, "alive": self.is_alive()}
        if self._subscriber and hasattr(self._subscriber, 'get_stats'):
            status["stats"] = self._subscriber.get_stats()
        return status


class ConfigAPIService(DaemonService):
    """Wraps ConfigAPIServer HTTP server."""

    name = "config_api"

    def __init__(self, port: int = 8081):
        self._port = port
        self._server = None

    def start(self) -> bool:
        try:
            from utils import config_api as config_api_mod
            if hasattr(config_api_mod, 'create_gateway_config_api'):
                api = config_api_mod.create_gateway_config_api()
            else:
                from utils.config_api import ConfigurationAPI
                from utils.common import SettingsManager
                settings = SettingsManager("gateway")
                api = ConfigurationAPI(settings)

            from utils.config_api import ConfigAPIServer
            self._server = ConfigAPIServer(
                api, host="127.0.0.1", port=self._port
            )
            self._server.start()
            return True
        except Exception as e:
            logger.error(f"Config API start failed: {e}")
            return False

    def stop(self, timeout: float = 5.0) -> None:
        if self._server:
            try:
                self._server.stop()
            except Exception as e:
                logger.error(f"Config API stop error: {e}")

    def is_alive(self) -> bool:
        if not self._server:
            return False
        return getattr(self._server, '_running', False)

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "alive": self.is_alive(),
            "port": self._port,
        }


class MapServerService(DaemonService):
    """Wraps map server subprocess."""

    name = "map_server"

    def __init__(self, port: int = 5000):
        self._port = port
        self._process = None

    def start(self) -> bool:
        import subprocess
        try:
            map_script = Path(__file__).parent / "utils" / "coverage_map.py"
            if not map_script.exists():
                logger.warning("Map server script not found")
                return False

            self._process = subprocess.Popen(
                [sys.executable, str(map_script), "--serve",
                 "--port", str(self._port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            logger.error(f"Map server start failed: {e}")
            return False

    def stop(self, timeout: float = 5.0) -> None:
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=timeout)
            except Exception as e:
                logger.error(f"Map server stop error: {e}")
                if self._process:
                    self._process.kill()

    def is_alive(self) -> bool:
        if not self._process:
            return False
        return self._process.poll() is None

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "alive": self.is_alive(),
            "port": self._port,
        }


class TelemetryPollerService(DaemonService):
    """Wraps TelemetryPoller for silent node polling."""

    name = "telemetry_poller"

    def __init__(self, poll_interval_minutes: int = 30):
        self._interval = poll_interval_minutes
        self._poller = None

    def start(self) -> bool:
        try:
            from utils.telemetry_poller import TelemetryPoller
            self._poller = TelemetryPoller(
                poll_interval_minutes=self._interval
            )
            self._poller.start()
            return True
        except Exception as e:
            logger.error(f"Telemetry poller start failed: {e}")
            return False

    def stop(self, timeout: float = 5.0) -> None:
        if self._poller:
            try:
                self._poller.stop()
            except Exception as e:
                logger.error(f"Telemetry poller stop error: {e}")

    def is_alive(self) -> bool:
        if not self._poller:
            return False
        thread = getattr(self._poller, '_thread', None)
        return thread is not None and thread.is_alive()

    def get_status(self) -> dict:
        return {"name": self.name, "alive": self.is_alive()}


class NodeTrackerService(DaemonService):
    """Wraps UnifiedNodeTracker singleton."""

    name = "node_tracker"

    def __init__(self):
        self._tracker = None

    def start(self) -> bool:
        try:
            from gateway.node_tracker import get_node_tracker
            self._tracker = get_node_tracker()
            return True
        except Exception as e:
            logger.error(f"Node tracker start failed: {e}")
            return False

    def stop(self, timeout: float = 5.0) -> None:
        if self._tracker and hasattr(self._tracker, 'stop'):
            try:
                self._tracker.stop(timeout=timeout)
            except Exception as e:
                logger.error(f"Node tracker stop error: {e}")

    def is_alive(self) -> bool:
        # Node tracker is event-driven, always alive if initialized
        return self._tracker is not None

    def get_status(self) -> dict:
        status = {"name": self.name, "alive": self.is_alive()}
        if self._tracker:
            try:
                nodes = self._tracker.get_all_nodes()
                status["node_count"] = len(nodes) if nodes else 0
            except Exception:
                status["node_count"] = 0
        return status


# =============================================================================
# Service Registry
# =============================================================================

class ServiceRegistry:
    """Manages the lifecycle of all daemon services."""

    def __init__(self):
        self._services: Dict[str, DaemonService] = {}
        self._start_order: List[str] = []

    def register(self, service: DaemonService) -> None:
        """Register a service. Start order follows registration order."""
        self._services[service.name] = service
        self._start_order.append(service.name)
        logger.debug(f"Registered service: {service.name}")

    def start_all(self) -> Dict[str, bool]:
        """Start all services in registration order.

        Returns dict of service_name -> success boolean.
        """
        results = {}
        for name in self._start_order:
            service = self._services[name]
            try:
                success = service.start()
                results[name] = success
                if success:
                    logger.info(f"Started: {name}")
                else:
                    logger.warning(f"Failed to start: {name}")
            except Exception as e:
                logger.error(f"Error starting {name}: {e}")
                results[name] = False
        return results

    def stop_all(self, timeout: float = 5.0) -> None:
        """Stop all services in reverse registration order."""
        for name in reversed(self._start_order):
            service = self._services.get(name)
            if service:
                try:
                    logger.info(f"Stopping: {name}")
                    service.stop(timeout=timeout)
                except Exception as e:
                    logger.error(f"Error stopping {name}: {e}")

    def get_all_status(self) -> Dict[str, dict]:
        """Get status of all registered services."""
        result = {}
        for name, service in self._services.items():
            try:
                result[name] = service.get_status()
            except Exception as e:
                result[name] = {"name": name, "alive": False, "error": str(e)}
        return result

    def restart_service(self, name: str) -> bool:
        """Restart a single service by name."""
        service = self._services.get(name)
        if not service:
            return False
        try:
            service.stop()
            return service.start()
        except Exception as e:
            logger.error(f"Error restarting {name}: {e}")
            return False

    def get_service(self, name: str) -> Optional[DaemonService]:
        """Get a service by name."""
        return self._services.get(name)


# =============================================================================
# Thread Watchdog
# =============================================================================

class ThreadWatchdog:
    """Monitors service threads and restarts dead ones.

    Runs on a configurable interval. For each registered service,
    calls is_alive(). If a service has died, logs the event and
    attempts restart with exponential backoff.
    """

    def __init__(
        self,
        registry: ServiceRegistry,
        interval: int = 60,
        max_restarts: int = 5,
    ):
        self._registry = registry
        self._interval = interval
        self._max_restarts = max_restarts
        self._restart_counts: Dict[str, int] = {}
        self._backoff_until: Dict[str, float] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the watchdog background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="watchdog"
        )
        self._thread.start()
        logger.info(f"Watchdog started (interval={self._interval}s)")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the watchdog."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        """Watchdog main loop."""
        while not self._stop_event.is_set():
            self._check_services()
            self._stop_event.wait(self._interval)

    def _check_services(self) -> None:
        """Check all services and restart dead ones."""
        now = time.time()
        for name, status in self._registry.get_all_status().items():
            if status.get("alive", False):
                # Service alive — reset restart counter
                if name in self._restart_counts:
                    self._restart_counts[name] = 0
                continue

            # Service dead — check restart eligibility
            count = self._restart_counts.get(name, 0)
            if count >= self._max_restarts:
                # Already exhausted restarts — log periodically
                if count == self._max_restarts:
                    logger.critical(
                        f"Watchdog: {name} exceeded max restarts "
                        f"({self._max_restarts}), giving up"
                    )
                    self._restart_counts[name] = count + 1
                continue

            # Check backoff timer
            backoff_end = self._backoff_until.get(name, 0)
            if now < backoff_end:
                continue

            # Attempt restart
            logger.warning(
                f"Watchdog: {name} is dead, restarting "
                f"(attempt {count + 1}/{self._max_restarts})"
            )
            success = self._registry.restart_service(name)

            if success:
                logger.info(f"Watchdog: {name} restarted successfully")
                self._restart_counts[name] = 0
            else:
                self._restart_counts[name] = count + 1
                # Exponential backoff: 10s, 20s, 40s, 80s, 160s
                backoff = min(10 * (2 ** count), 300)
                self._backoff_until[name] = now + backoff
                logger.warning(
                    f"Watchdog: {name} restart failed, "
                    f"backoff {backoff}s"
                )

    def get_restart_counts(self) -> Dict[str, int]:
        """Get restart counts for status reporting."""
        return dict(self._restart_counts)


# =============================================================================
# Daemon Controller
# =============================================================================

class DaemonController:
    """Main daemon lifecycle manager."""

    def __init__(self):
        self._stop_event = threading.Event()
        self._registry: Optional[ServiceRegistry] = None
        self._watchdog: Optional[ThreadWatchdog] = None
        self._config: Optional[DaemonConfig] = None
        self._started_at: Optional[datetime] = None
        self._profile_name: str = "auto"

    def start(
        self,
        profile_name: Optional[str] = None,
        config_path: Optional[Path] = None,
        foreground: bool = True,
    ) -> int:
        """Start the daemon.

        Args:
            profile_name: Deployment profile name (or auto-detect).
            config_path: Explicit daemon config YAML path.
            foreground: Run in foreground (for systemd Type=simple).

        Returns:
            Exit code (0 = success).
        """
        # Load deployment profile
        profile = None
        if profile_name and _HAS_PROFILES:
            profile = get_profile_by_name(profile_name)
            self._profile_name = profile_name or "auto"
        elif _HAS_PROFILES:
            profile = load_or_detect_profile()
            self._profile_name = getattr(profile, 'name', 'auto')

        # Load config
        self._config = DaemonConfig.load(
            config_path=config_path, profile=profile
        )

        # Check for existing daemon
        pid_file = self._pid_file_path()
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text().strip())
                os.kill(old_pid, 0)  # Check if process exists
                logger.error(
                    f"Daemon already running (PID {old_pid}). "
                    f"Use 'meshforged stop' first."
                )
                return 1
            except (ProcessLookupError, ValueError):
                # Stale PID file — clean up
                pid_file.unlink(missing_ok=True)
            except PermissionError:
                logger.error(f"Daemon running as different user (PID file: {pid_file})")
                return 1

        # Setup logging
        self._setup_logging()

        logger.info(f"MeshForge Daemon v{__version__} starting")
        logger.info(f"Profile: {self._profile_name}")
        logger.info(f"Config: {self._config.to_dict()}")

        # Write PID file
        self._write_pid_file()

        # Register signal handlers
        self._setup_signals()

        # Create and populate service registry
        self._registry = ServiceRegistry()
        self._register_services()

        # Start all services
        self._started_at = datetime.now(timezone.utc)
        results = self._registry.start_all()

        started = sum(1 for v in results.values() if v)
        failed = sum(1 for v in results.values() if not v)
        logger.info(
            f"Services: {started} started, {failed} failed "
            f"({len(results)} total)"
        )

        # Start watchdog
        self._watchdog = ThreadWatchdog(
            self._registry,
            interval=self._config.watchdog_interval,
            max_restarts=self._config.max_restarts,
        )
        self._watchdog.start()

        # Write initial status
        self._write_status_file()

        logger.info("Daemon ready — entering main loop")

        # Main loop
        try:
            self._main_loop()
        except Exception as e:
            logger.error(f"Main loop error: {e}")

        # Shutdown
        return self._shutdown()

    def stop_remote(self) -> int:
        """Send stop signal to running daemon.

        Returns:
            Exit code (0 = success).
        """
        pid_file = self._pid_file_path()
        if not pid_file.exists():
            print("No daemon running (PID file not found)")
            return 1

        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"Stop signal sent to daemon (PID {pid})")
            return 0
        except ProcessLookupError:
            print("Daemon not running (stale PID file)")
            pid_file.unlink(missing_ok=True)
            return 1
        except ValueError:
            print("Invalid PID file")
            return 1
        except PermissionError:
            print("Permission denied — try with sudo")
            return 1

    def status(self, as_json: bool = False) -> int:
        """Display daemon status.

        Args:
            as_json: Output raw JSON instead of formatted text.

        Returns:
            Exit code (0 = running, 1 = not running).
        """
        status_file = self._status_file_path()
        pid_file = self._pid_file_path()

        # Check PID
        running = False
        pid = None
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                running = True
            except (ProcessLookupError, ValueError, PermissionError):
                pass

        if not running:
            if as_json:
                print(json.dumps({"status": "stopped"}, indent=2))
            else:
                print("MeshForge Daemon: stopped")
            return 1

        # Read status file
        if status_file.exists():
            try:
                with open(status_file, 'r') as f:
                    data = json.load(f)

                if as_json:
                    print(json.dumps(data, indent=2))
                else:
                    self._print_status(data, pid)
                return 0
            except (json.JSONDecodeError, OSError) as e:
                if as_json:
                    print(json.dumps({"status": "running", "pid": pid}))
                else:
                    print(f"MeshForge Daemon: running (PID {pid})")
                    print(f"  Status file error: {e}")
                return 0
        else:
            if as_json:
                print(json.dumps({"status": "running", "pid": pid}))
            else:
                print(f"MeshForge Daemon: running (PID {pid})")
            return 0

    def reload_remote(self) -> int:
        """Send SIGHUP to running daemon for config reload.

        Returns:
            Exit code (0 = success).
        """
        pid_file = self._pid_file_path()
        if not pid_file.exists():
            print("No daemon running")
            return 1

        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGHUP)
            print(f"Reload signal sent to daemon (PID {pid})")
            return 0
        except (ProcessLookupError, ValueError, PermissionError) as e:
            print(f"Failed to send reload signal: {e}")
            return 1

    # --- Internal Methods ---

    def _main_loop(self) -> None:
        """Main event loop — wait, write status, ping watchdog."""
        interval = self._config.status_file_interval if self._config else 30
        while not self._stop_event.is_set():
            self._stop_event.wait(interval)
            if not self._stop_event.is_set():
                self._write_status_file()

    def _shutdown(self) -> int:
        """Graceful shutdown sequence."""
        logger.info("Daemon shutting down...")

        # Stop watchdog
        if self._watchdog:
            self._watchdog.stop(timeout=5)

        # Stop services in reverse order
        if self._registry:
            self._registry.stop_all(timeout=5)

        # Shutdown EventBus thread pool
        event_bus.shutdown()

        # Write final status
        self._write_status_file(status="stopped")

        # Remove PID file
        self._remove_pid_file()

        # Flush logging
        logging.shutdown()

        return 0

    def _register_services(self) -> None:
        """Register services based on config and profile."""
        cfg = self._config

        # Order matters — dependencies first
        if cfg.health_probe_enabled:
            self._registry.register(
                HealthProbeService(interval=cfg.health_probe_interval)
            )

        if cfg.node_tracker_enabled:
            self._registry.register(NodeTrackerService())

        if cfg.gateway_enabled:
            self._registry.register(GatewayBridgeService())

        if cfg.mqtt_enabled:
            self._registry.register(
                MQTTSubscriberService(
                    broker=cfg.mqtt_broker, port=cfg.mqtt_port
                )
            )

        if cfg.config_api_enabled:
            self._registry.register(
                ConfigAPIService(port=cfg.config_api_port)
            )

        if cfg.map_server_enabled:
            self._registry.register(
                MapServerService(port=cfg.map_server_port)
            )

        if cfg.telemetry_enabled:
            self._registry.register(
                TelemetryPollerService(
                    poll_interval_minutes=cfg.telemetry_poll_interval_minutes
                )
            )

    def _setup_logging(self) -> None:
        """Configure logging for daemon mode."""
        level = getattr(
            logging, (self._config.log_level or "INFO").upper(), logging.INFO
        )
        root = logging.getLogger()
        root.setLevel(level)

        # Remove existing handlers
        root.handlers.clear()

        formatter = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        if self._config.log_file:
            # File-based logging with rotation (10MB, 3 backups)
            log_path = Path(self._config.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                str(log_path),
                maxBytes=10_485_760,
                backupCount=3,
            )
            handler.setFormatter(formatter)
            root.addHandler(handler)
        else:
            # stderr (for journald capture)
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(formatter)
            root.addHandler(handler)

    def _setup_signals(self) -> None:
        """Register signal handlers."""
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGHUP, self._handle_reload)

    def _handle_stop(self, signum, frame) -> None:
        """Handle SIGTERM/SIGINT — trigger graceful shutdown."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name} — shutting down")
        self._stop_event.set()

    def _handle_reload(self, signum, frame) -> None:
        """Handle SIGHUP — reload configuration."""
        logger.info("Received SIGHUP — reloading configuration")
        try:
            new_config = DaemonConfig.load()
            self._config = new_config
            logger.info(f"Configuration reloaded: {new_config.to_dict()}")
        except Exception as e:
            logger.error(f"Config reload failed: {e}")

    def _pid_file_path(self) -> Path:
        """Get PID file path."""
        pid_dir = Path(self._config.pid_dir if self._config else "/run/meshforge")
        return pid_dir / "meshforged.pid"

    def _status_file_path(self) -> Path:
        """Get status file path."""
        return get_real_user_home() / ".config" / "meshforge" / "daemon_status.json"

    def _write_pid_file(self) -> None:
        """Write PID file."""
        pid_file = self._pid_file_path()
        try:
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(str(os.getpid()))
            logger.debug(f"PID file written: {pid_file}")
        except OSError as e:
            logger.warning(f"Could not write PID file: {e}")

    def _remove_pid_file(self) -> None:
        """Remove PID file."""
        pid_file = self._pid_file_path()
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _write_status_file(self, status: str = "running") -> None:
        """Write JSON status file for meshforged status command."""
        now = datetime.now(timezone.utc)
        uptime = (
            (now - self._started_at).total_seconds()
            if self._started_at else 0
        )

        data = {
            "daemon": {
                "pid": os.getpid(),
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "uptime_seconds": int(uptime),
                "version": __version__,
                "profile": self._profile_name,
                "status": status,
            },
            "services": (
                self._registry.get_all_status() if self._registry else {}
            ),
            "watchdog": {
                "last_check": now.isoformat(),
                "restarts": (
                    self._watchdog.get_restart_counts()
                    if self._watchdog else {}
                ),
            },
            "updated_at": now.isoformat(),
        }

        status_path = self._status_file_path()
        try:
            status_path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write — write to temp then rename
            tmp_path = status_path.with_suffix('.tmp')
            with open(tmp_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            tmp_path.replace(status_path)
        except OSError as e:
            logger.debug(f"Status file write failed: {e}")

    def _print_status(self, data: dict, pid: int) -> None:
        """Print formatted status to terminal."""
        daemon = data.get("daemon", {})
        uptime = daemon.get("uptime_seconds", 0)
        hours = uptime // 3600
        minutes = (uptime % 3600) // 60

        print(f"MeshForge Daemon v{daemon.get('version', '?')}")
        print(f"  Status:  {daemon.get('status', 'unknown')}")
        print(f"  PID:     {pid}")
        print(f"  Profile: {daemon.get('profile', '?')}")
        print(f"  Uptime:  {hours}h {minutes}m")
        print()

        services = data.get("services", {})
        if services:
            print("Services:")
            for name, svc in services.items():
                alive = svc.get("alive", False)
                marker = "*" if alive else "-"
                print(f"  {marker} {name}")
                # Show extra stats if available
                stats = svc.get("stats", svc.get("statistics", {}))
                if isinstance(stats, dict):
                    for k, v in list(stats.items())[:3]:
                        print(f"      {k}: {v}")

        watchdog = data.get("watchdog", {})
        restarts = watchdog.get("restarts", {})
        if any(v > 0 for v in restarts.values()):
            print()
            print("Watchdog restarts:")
            for name, count in restarts.items():
                if count > 0:
                    print(f"  {name}: {count}")


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    """CLI entry point for meshforged."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="meshforged",
        description="MeshForge Daemon — Headless NOC Service Manager",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # start
    start_p = subparsers.add_parser("start", help="Start daemon")
    start_p.add_argument(
        "--profile", type=str, default=None,
        help="Deployment profile (gateway, monitor, full, etc.)",
    )
    start_p.add_argument(
        "--config", type=str, default=None,
        help="Path to daemon.yaml config file",
    )
    start_p.add_argument(
        "--foreground", action="store_true",
        help="Run in foreground (for systemd Type=simple)",
    )

    # stop
    subparsers.add_parser("stop", help="Stop daemon")

    # status
    status_p = subparsers.add_parser("status", help="Show daemon status")
    status_p.add_argument("--json", action="store_true", help="JSON output")

    # restart
    subparsers.add_parser("restart", help="Restart daemon")

    # reload
    subparsers.add_parser("reload", help="Reload configuration (SIGHUP)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    controller = DaemonController()

    if args.command == "start":
        config_path = Path(args.config) if args.config else None
        return controller.start(
            profile_name=args.profile,
            config_path=config_path,
            foreground=args.foreground,
        )

    elif args.command == "stop":
        return controller.stop_remote()

    elif args.command == "status":
        return controller.status(as_json=args.json)

    elif args.command == "restart":
        controller.stop_remote()
        time.sleep(2)
        return controller.start()

    elif args.command == "reload":
        return controller.reload_remote()

    return 0


if __name__ == "__main__":
    sys.exit(main())
