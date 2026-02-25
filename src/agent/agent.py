"""MeshForge Remote Management Agent Daemon.

Based on NGINX Agent architecture - provides remote management, metrics
collection, and command execution for MeshForge NOC instances.

Features:
- Connects to management plane for remote control
- Collects and reports health metrics
- Executes commands from management plane
- Supports local-only mode without management server

Example Usage:
    from agent import AgentDaemon, AgentConfig

    # Create configuration
    config = AgentConfig(
        instance_id="meshforge-001",
        management_host="mgmt.example.com",
        management_port=9443,
        auth_token="your-token-here",
    )

    # Create and start agent
    agent = AgentDaemon(config)
    agent.start()

    # Or run in standalone mode (no management server)
    config = AgentConfig(instance_id="meshforge-local", standalone=True)
    agent = AgentDaemon(config)
    agent.start()

Author: Claude Code (Opus 4.5)
Session: claude/config-api-restful-ETusS
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# First-party imports — direct per CLAUDE.md
from utils.paths import get_real_user_home

# Module-level safe imports for optional dependencies
_create_gateway_config_api, _HAS_CONFIG_API = safe_import(
    'utils.config_api', 'create_gateway_config_api'
)
_SharedHealthState, _HAS_HEALTH_STATE = safe_import(
    'utils.shared_health_state', 'SharedHealthState'
)
_create_gateway_health_probe, _HAS_HEALTH_PROBE = safe_import(
    'utils.active_health_probe', 'create_gateway_health_probe'
)
_integrate_with_active_probe, _HAS_HEALTH_INTEGRATE = safe_import(
    'utils.shared_health_state', 'integrate_with_active_probe'
)
_PrometheusExporter, _HAS_METRICS = safe_import(
    'utils.metrics_export', 'PrometheusExporter'
)


# =============================================================================
# Configuration
# =============================================================================


class AgentState(Enum):
    """Agent operational state."""
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    ERROR = auto()


@dataclass
class AgentConfig:
    """Configuration for the agent daemon.

    Attributes:
        instance_id: Unique identifier for this agent instance
        management_host: Management server hostname
        management_port: Management server port
        auth_token: Authentication token (or path to token file)
        standalone: Run without management server connection
        metrics_interval: How often to push metrics (seconds)
        health_check_interval: Health probe interval (seconds)
        config_sync_interval: Config sync interval (seconds)
        data_dir: Directory for agent data
        pid_file: PID file path
        log_level: Logging level
    """
    instance_id: str = ""
    management_host: str = "localhost"
    management_port: int = 9443
    auth_token: str = ""
    standalone: bool = True
    use_tls: bool = True
    verify_cert: bool = True
    metrics_interval: float = 60.0
    health_check_interval: float = 30.0
    config_sync_interval: float = 300.0
    data_dir: str = ""
    pid_file: str = ""
    log_level: str = "INFO"

    def __post_init__(self):
        """Initialize computed fields."""
        # Generate instance ID if not provided
        if not self.instance_id:
            import platform
            import hashlib
            host = platform.node()
            self.instance_id = f"meshforge-{hashlib.md5(host.encode()).hexdigest()[:8]}"

        # Set default data directory
        if not self.data_dir:
            # Use real user's home for sudo compatibility
            home = get_real_user_home()
            self.data_dir = str(home / ".config" / "meshforge" / "agent")

        # Set default PID file
        if not self.pid_file:
            self.pid_file = str(Path(self.data_dir) / "agent.pid")

    @staticmethod
    def from_file(path: str) -> AgentConfig:
        """Load configuration from JSON file."""
        with open(path) as f:
            data = json.load(f)
        return AgentConfig(**data)

    def to_file(self, path: str) -> None:
        """Save configuration to JSON file."""
        data = {
            "instance_id": self.instance_id,
            "management_host": self.management_host,
            "management_port": self.management_port,
            "standalone": self.standalone,
            "use_tls": self.use_tls,
            "verify_cert": self.verify_cert,
            "metrics_interval": self.metrics_interval,
            "health_check_interval": self.health_check_interval,
            "config_sync_interval": self.config_sync_interval,
            "data_dir": self.data_dir,
            "log_level": self.log_level,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


# =============================================================================
# Agent Daemon
# =============================================================================


class AgentDaemon:
    """MeshForge remote management agent daemon.

    Provides:
    - Connection to management plane (optional)
    - Metrics collection and reporting
    - Command execution
    - Health monitoring integration
    - Configuration synchronization
    """

    def __init__(self, config: AgentConfig):
        """Initialize the agent daemon.

        Args:
            config: Agent configuration
        """
        self.config = config
        self._state = AgentState.STOPPED
        self._lock = threading.RLock()

        # Components (initialized in start())
        self._protocol = None  # AgentProtocol
        self._command_registry = None  # CommandRegistry
        self._command_context = None  # CommandContext
        self._config_api = None  # ConfigurationAPI
        self._health_state = None  # SharedHealthState
        self._health_probe = None  # ActiveHealthProbe
        self._metrics_exporter = None  # PrometheusExporter

        # Background threads
        self._running = threading.Event()
        self._stop_event = threading.Event()
        self._metrics_thread: Optional[threading.Thread] = None
        self._health_thread: Optional[threading.Thread] = None

        # Callbacks
        self._state_callbacks: List[Callable[[AgentState], None]] = []

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> bool:
        """Start the agent daemon.

        Returns:
            True if started successfully
        """
        with self._lock:
            if self._state != AgentState.STOPPED:
                logger.warning(f"Agent already in state: {self._state}")
                return False

            self._set_state(AgentState.STARTING)

            try:
                # Create data directory
                data_dir = Path(self.config.data_dir)
                data_dir.mkdir(parents=True, exist_ok=True)

                # Write PID file
                self._write_pid_file()

                # Initialize components
                self._init_components()

                # Connect to management plane (if not standalone)
                if not self.config.standalone:
                    if not self._connect_management():
                        logger.error("Failed to connect to management plane")
                        self._set_state(AgentState.ERROR)
                        return False

                # Start background threads
                self._running.set()
                self._stop_event.clear()
                self._start_background_threads()

                # Register signal handlers
                self._register_signals()

                self._set_state(AgentState.RUNNING)
                logger.info(f"Agent started: {self.config.instance_id}")
                return True

            except Exception as e:
                logger.exception(f"Failed to start agent: {e}")
                self._set_state(AgentState.ERROR)
                return False

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the agent daemon.

        Args:
            timeout: Maximum time to wait for shutdown
        """
        with self._lock:
            if self._state != AgentState.RUNNING:
                return

            self._set_state(AgentState.STOPPING)

            # Stop background threads
            self._running.clear()
            self._stop_event.set()

            if self._metrics_thread:
                self._metrics_thread.join(timeout=timeout / 3)
            if self._health_thread:
                self._health_thread.join(timeout=timeout / 3)

            # Disconnect from management plane
            if self._protocol:
                self._protocol.stop(timeout=timeout / 3)

            # Stop health probe
            if self._health_probe:
                self._health_probe.stop()

            # Remove PID file
            self._remove_pid_file()

            self._set_state(AgentState.STOPPED)
            logger.info("Agent stopped")

    def run(self) -> None:
        """Run the agent (blocking).

        Blocks until agent is stopped via signal or stop().
        """
        if not self.start():
            sys.exit(1)

        # Block until stopped
        try:
            while self._running.is_set():
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        finally:
            self.stop()

    # -------------------------------------------------------------------------
    # Component Initialization
    # -------------------------------------------------------------------------

    def _init_components(self) -> None:
        """Initialize agent components."""
        # Command registry
        from agent.commands import create_default_registry, CommandContext
        self._command_registry = create_default_registry()

        # Try to initialize optional components
        self._init_config_api()
        self._init_health_state()
        self._init_health_probe()
        self._init_metrics_exporter()

        # Create command context
        self._command_context = CommandContext(
            instance_id=self.config.instance_id,
            scopes={"*"},  # Local agent has all scopes
            config_api=self._config_api,
            health_state=self._health_state,
            health_probe=self._health_probe,
            metrics_exporter=self._metrics_exporter,
        )

    def _init_config_api(self) -> None:
        """Initialize Configuration API."""
        if not _HAS_CONFIG_API:
            logger.warning("Configuration API not available: module not found")
            return
        try:
            self._config_api = _create_gateway_config_api()
            logger.info("Configuration API initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Configuration API: {e}")

    def _init_health_state(self) -> None:
        """Initialize shared health state."""
        if not _HAS_HEALTH_STATE:
            logger.warning("Shared health state not available: module not found")
            return
        try:
            self._health_state = _SharedHealthState()
            logger.info("Shared health state initialized")
        except Exception as e:
            logger.error(f"Failed to initialize shared health state: {e}")

    def _init_health_probe(self) -> None:
        """Initialize active health probe."""
        if not _HAS_HEALTH_PROBE or not _HAS_HEALTH_INTEGRATE:
            logger.warning("Active health probe not available: module not found")
            return
        try:
            self._health_probe = _create_gateway_health_probe(
                interval=int(self.config.health_check_interval),
                fails=3,
                passes=2
            )

            # Integrate with shared state if available
            if self._health_state:
                _integrate_with_active_probe(self._health_state, self._health_probe)

            # Start the probe
            self._health_probe.start()
            logger.info("Active health probe initialized and started")

        except Exception as e:
            logger.error(f"Failed to initialize health probe: {e}")

    def _init_metrics_exporter(self) -> None:
        """Initialize Prometheus metrics exporter."""
        if not _HAS_METRICS:
            logger.warning("Metrics exporter not available: module not found")
            return
        try:
            self._metrics_exporter = _PrometheusExporter()

            logger.info("Prometheus metrics exporter initialized")
        except Exception as e:
            logger.error(f"Failed to initialize metrics exporter: {e}")

    # -------------------------------------------------------------------------
    # Management Plane Connection
    # -------------------------------------------------------------------------

    def _connect_management(self) -> bool:
        """Connect to management plane."""
        try:
            from agent.protocol import AgentProtocol, AuthToken, MessageType

            # Create or load auth token
            if self.config.auth_token:
                if Path(self.config.auth_token).exists():
                    # Token is a file path
                    with open(self.config.auth_token) as f:
                        token_data = json.load(f)
                    token = AuthToken(
                        token_id=token_data["token_id"],
                        secret=token_data["secret"],
                        scopes=token_data.get("scopes", ["*"])
                    )
                else:
                    # Token is the secret itself
                    token = AuthToken(
                        token_id=self.config.instance_id,
                        secret=self.config.auth_token,
                        scopes=["*"]
                    )
            else:
                logger.error("No authentication token configured")
                return False

            # Create protocol handler
            self._protocol = AgentProtocol(
                instance_id=self.config.instance_id,
                token=token,
                host=self.config.management_host,
                port=self.config.management_port,
                use_tls=self.config.use_tls,
                verify_cert=self.config.verify_cert,
            )

            # Register message handlers
            self._protocol.on_message(MessageType.COMMAND, self._handle_command)
            self._protocol.on_message(MessageType.CONFIG_SYNC, self._handle_config_sync)

            # Connect
            if not self._protocol.start():
                return False

            logger.info(f"Connected to management plane: {self.config.management_host}:{self.config.management_port}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to management plane: {e}")
            return False

    def _handle_command(self, message) -> None:
        """Handle incoming command from management plane."""
        from agent.protocol import MessageType

        command_name = message.payload.get("command")
        command_args = message.payload.get("args", {})

        logger.debug(f"Received command: {command_name}")

        # Execute command
        result = self._command_registry.execute(
            command_name,
            command_args,
            self._command_context
        )

        # Send response
        response = message.create_response(
            MessageType.COMMAND_RESULT,
            {
                "command": command_name,
                "result": result.to_dict(),
            }
        )
        self._protocol.send(response)

    def _handle_config_sync(self, message) -> None:
        """Handle configuration sync from management plane."""
        # Extract config from message
        config_data = message.payload.get("config")
        if config_data and self._config_api:
            result = self._config_api.import_json(
                json.dumps(config_data),
                source="management"
            )
            logger.info(f"Config sync: {'success' if result.success else result.error}")

    # -------------------------------------------------------------------------
    # Background Threads
    # -------------------------------------------------------------------------

    def _start_background_threads(self) -> None:
        """Start background operation threads."""
        # Metrics reporting thread
        if not self.config.standalone and self._protocol:
            self._metrics_thread = threading.Thread(
                target=self._metrics_loop,
                name="Agent-Metrics",
                daemon=True
            )
            self._metrics_thread.start()

    def _metrics_loop(self) -> None:
        """Background loop for metrics reporting."""
        from agent.protocol import Message, MessageType

        while self._running.is_set():
            self._stop_event.wait(self.config.metrics_interval)
            if self._stop_event.is_set():
                break

            if not self._protocol or not self._protocol.is_connected:
                continue

            try:
                # Collect metrics
                metrics_data = self._collect_metrics()

                # Send to management plane
                message = Message(
                    msg_type=MessageType.METRICS,
                    payload={"metrics": metrics_data}
                )
                self._protocol.send(message)

            except Exception as e:
                logger.error(f"Metrics reporting error: {e}")

    def _collect_metrics(self) -> Dict[str, Any]:
        """Collect current metrics."""
        metrics = {
            "timestamp": time.time(),
            "instance_id": self.config.instance_id,
        }

        # Health metrics
        if self._health_state:
            try:
                health_metrics = self._health_state.get_metrics()
                metrics["health"] = health_metrics
            except Exception as e:
                logger.warning(f"Failed to collect health metrics: {e}")

        # Prometheus metrics
        if self._metrics_exporter:
            try:
                metrics["prometheus"] = self._metrics_exporter.export()
            except Exception as e:
                logger.warning(f"Failed to collect Prometheus metrics: {e}")

        return metrics

    # -------------------------------------------------------------------------
    # State Management
    # -------------------------------------------------------------------------

    def _set_state(self, state: AgentState) -> None:
        """Set agent state and fire callbacks."""
        self._state = state
        for callback in self._state_callbacks:
            try:
                callback(state)
            except Exception as e:
                logger.error(f"State callback error: {e}")

    def on_state_change(self, callback: Callable[[AgentState], None]) -> None:
        """Register a state change callback."""
        self._state_callbacks.append(callback)

    @property
    def state(self) -> AgentState:
        """Get current agent state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if agent is running."""
        return self._state == AgentState.RUNNING

    # -------------------------------------------------------------------------
    # PID File Management
    # -------------------------------------------------------------------------

    def _write_pid_file(self) -> None:
        """Write PID file."""
        pid_path = Path(self.config.pid_file)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()))
        logger.debug(f"Wrote PID file: {self.config.pid_file}")

    def _remove_pid_file(self) -> None:
        """Remove PID file."""
        try:
            Path(self.config.pid_file).unlink(missing_ok=True)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Signal Handling
    # -------------------------------------------------------------------------

    def _register_signals(self) -> None:
        """Register signal handlers."""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        atexit.register(self.stop)

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, stopping...")
        self.stop()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def execute_command(
        self,
        command: str,
        args: Dict[str, Any] = None
    ) -> Any:
        """Execute a command locally.

        Args:
            command: Command name (e.g., "config.get")
            args: Command arguments

        Returns:
            CommandResult from handler
        """
        if not self._command_registry or not self._command_context:
            raise RuntimeError("Agent not started")

        return self._command_registry.execute(
            command,
            args or {},
            self._command_context
        )

    def get_status(self) -> Dict[str, Any]:
        """Get agent status summary.

        Returns:
            Status dictionary
        """
        status = {
            "instance_id": self.config.instance_id,
            "state": self._state.name,
            "standalone": self.config.standalone,
            "pid": os.getpid(),
        }

        # Connection status
        if self._protocol:
            status["management"] = {
                "host": self.config.management_host,
                "port": self.config.management_port,
                "connected": self._protocol.is_connected,
                "connection_state": self._protocol.state.to_dict(),
            }

        # Component status
        status["components"] = {
            "config_api": self._config_api is not None,
            "health_state": self._health_state is not None,
            "health_probe": self._health_probe is not None,
            "metrics_exporter": self._metrics_exporter is not None,
        }

        return status


# =============================================================================
# CLI Entry Point
# =============================================================================


def main():
    """Agent daemon CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="MeshForge Remote Management Agent"
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to configuration file"
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Run in standalone mode (no management server)"
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Management server host"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9443,
        help="Management server port"
    )
    parser.add_argument(
        "--token",
        help="Authentication token or path to token file"
    )
    parser.add_argument(
        "--instance-id",
        help="Agent instance ID"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Load or create configuration
    if args.config and Path(args.config).exists():
        config = AgentConfig.from_file(args.config)
    else:
        config = AgentConfig(
            instance_id=args.instance_id or "",
            management_host=args.host,
            management_port=args.port,
            auth_token=args.token or "",
            standalone=args.standalone,
        )

    # Create and run agent
    agent = AgentDaemon(config)
    agent.run()


if __name__ == "__main__":
    main()
