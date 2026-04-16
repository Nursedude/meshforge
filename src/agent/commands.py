"""Command handlers for MeshForge Agent.

Provides command registration and execution for remote management operations.

Supported command categories:
- Configuration: Get/set/reset configuration via ConfigurationAPI
- Service: Start/stop/restart systemd services
- Health: Query health state and trigger probes
- Metrics: Export Prometheus metrics
- System: System info and log access

Author: Claude Code (Opus 4.5)
Session: claude/config-api-restful-ETusS
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Set, Type

from utils.safe_import import safe_import

# Module-level safe imports
_version_mod, _HAS_VERSION = safe_import('__version__', '__version__')

logger = logging.getLogger(__name__)

# =============================================================================
# Service Allowlist (SEC-CRITICAL: prevents command injection via systemctl)
# =============================================================================
# Only these service names may be passed to systemctl/journalctl.  Any name
# not on this list is rejected before it reaches subprocess.  Add new managed
# services here as they are deployed.

ALLOWED_SERVICE_NAMES = frozenset({
    "meshforge",
    "meshtasticd",
    "rnsd",
    "nomadnet",
    "mosquitto",
    "meshforge-maps",
    "meshforge-agent",
    "meshanchor",
    "meshing-around",
})


def _validate_service_name(name: str) -> Optional[str]:
    """Validate a service name against the allowlist.

    Returns the name if valid, or None if rejected.  This prevents arbitrary
    arguments from being passed to systemctl/journalctl via the remote
    management protocol.
    """
    if not name or not isinstance(name, str):
        return None
    # Strip .service suffix if present for matching
    clean = name.removesuffix(".service")
    if clean in ALLOWED_SERVICE_NAMES:
        return clean
    logger.warning("Rejected disallowed service name from agent command: %r", name)
    return None


# =============================================================================
# Data Classes
# =============================================================================


class CommandStatus(Enum):
    """Status of command execution."""
    SUCCESS = "success"
    ERROR = "error"
    UNAUTHORIZED = "unauthorized"
    INVALID_ARGS = "invalid_args"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"


@dataclass
class CommandResult:
    """Result of command execution."""
    status: CommandStatus
    data: Any = None
    error_msg: Optional[str] = None
    execution_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for protocol transmission."""
        result = {
            "status": self.status.value,
            "execution_time_ms": self.execution_time_ms,
        }
        if self.data is not None:
            result["data"] = self.data
        if self.error_msg:
            result["error"] = self.error_msg
        return result

    @staticmethod
    def success(data: Any = None) -> CommandResult:
        """Create a successful result."""
        return CommandResult(status=CommandStatus.SUCCESS, data=data)

    @staticmethod
    def error(message: str, status: CommandStatus = CommandStatus.ERROR) -> CommandResult:
        """Create an error result."""
        return CommandResult(status=status, error_msg=message)


@dataclass
class CommandContext:
    """Context for command execution."""
    instance_id: str
    scopes: Set[str] = field(default_factory=set)
    config_api: Any = None  # ConfigurationAPI
    health_state: Any = None  # SharedHealthState
    health_probe: Any = None  # ActiveHealthProbe
    metrics_exporter: Any = None  # PrometheusExporter

    def has_scope(self, scope: str) -> bool:
        """Check if context has required scope."""
        return "*" in self.scopes or scope in self.scopes


# Type alias for command handlers
CommandHandlerFunc = Callable[[Dict[str, Any], CommandContext], CommandResult]


# =============================================================================
# Command Handler Decorator
# =============================================================================


def command_handler(
    name: str,
    required_scopes: List[str] = None,
    required_args: List[str] = None,
    description: str = ""
):
    """Decorator to register a function as a command handler.

    Args:
        name: Command name (e.g., "config.get")
        required_scopes: Scopes required to execute this command
        required_args: Required argument names
        description: Human-readable description

    Example:
        @command_handler("config.get", required_args=["path"])
        def handle_config_get(args, context):
            return CommandResult.success(context.config_api.get(args["path"]))
    """
    def decorator(func: CommandHandlerFunc) -> CommandHandlerFunc:
        @wraps(func)
        def wrapper(args: Dict[str, Any], context: CommandContext) -> CommandResult:
            start_time = time.time()

            # Check scopes
            if required_scopes:
                for scope in required_scopes:
                    if not context.has_scope(scope):
                        return CommandResult.error(
                            f"Missing required scope: {scope}",
                            CommandStatus.UNAUTHORIZED
                        )

            # Check required args
            if required_args:
                missing = [arg for arg in required_args if arg not in args]
                if missing:
                    return CommandResult.error(
                        f"Missing required arguments: {', '.join(missing)}",
                        CommandStatus.INVALID_ARGS
                    )

            # Execute handler
            try:
                result = func(args, context)
            except Exception as e:
                logger.exception(f"Command {name} failed: {e}")
                result = CommandResult.error(str(e))

            result.execution_time_ms = (time.time() - start_time) * 1000
            return result

        # Attach metadata
        wrapper._command_name = name
        wrapper._required_scopes = required_scopes or []
        wrapper._required_args = required_args or []
        wrapper._description = description

        return wrapper
    return decorator


# =============================================================================
# Command Registry
# =============================================================================


class CommandRegistry:
    """Registry for command handlers.

    Provides command registration, lookup, and execution.
    """

    def __init__(self):
        """Initialize the command registry."""
        self._handlers: Dict[str, CommandHandlerFunc] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}

    def register(self, handler: CommandHandlerFunc) -> None:
        """Register a command handler.

        Args:
            handler: Function decorated with @command_handler
        """
        if not hasattr(handler, "_command_name"):
            raise ValueError("Handler must be decorated with @command_handler")

        name = handler._command_name
        self._handlers[name] = handler
        self._metadata[name] = {
            "name": name,
            "scopes": handler._required_scopes,
            "args": handler._required_args,
            "description": handler._description,
        }
        logger.debug(f"Registered command handler: {name}")

    def unregister(self, name: str) -> bool:
        """Unregister a command handler.

        Args:
            name: Command name

        Returns:
            True if handler was registered and removed
        """
        if name in self._handlers:
            del self._handlers[name]
            del self._metadata[name]
            return True
        return False

    def execute(
        self,
        name: str,
        args: Dict[str, Any],
        context: CommandContext
    ) -> CommandResult:
        """Execute a command.

        Args:
            name: Command name
            args: Command arguments
            context: Execution context

        Returns:
            CommandResult from handler
        """
        if name not in self._handlers:
            return CommandResult.error(
                f"Unknown command: {name}",
                CommandStatus.NOT_FOUND
            )

        handler = self._handlers[name]
        return handler(args, context)

    def list_commands(self) -> List[Dict[str, Any]]:
        """List all registered commands with metadata.

        Returns:
            List of command metadata dictionaries
        """
        return list(self._metadata.values())

    def get_handler(self, name: str) -> Optional[CommandHandlerFunc]:
        """Get handler for a command.

        Args:
            name: Command name

        Returns:
            Handler function or None
        """
        return self._handlers.get(name)


# =============================================================================
# Built-in Command Handlers
# =============================================================================


class CommandHandler:
    """Collection of built-in command handlers."""

    # -------------------------------------------------------------------------
    # Configuration Commands
    # -------------------------------------------------------------------------

    @staticmethod
    @command_handler(
        "config.get",
        required_scopes=["config:read"],
        description="Get configuration value at path"
    )
    def config_get(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get configuration value."""
        if not context.config_api:
            return CommandResult.error("Configuration API not available")

        path = args.get("path", "")
        value = context.config_api.get(path)
        return CommandResult.success({"path": path, "value": value})

    @staticmethod
    @command_handler(
        "config.set",
        required_scopes=["config:write"],
        required_args=["path", "value"],
        description="Set configuration value at path"
    )
    def config_set(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Set configuration value."""
        if not context.config_api:
            return CommandResult.error("Configuration API not available")

        result = context.config_api.put(args["path"], args["value"], source="agent")
        if result.success:
            return CommandResult.success(result.to_dict())
        else:
            return CommandResult.error(result.error)

    @staticmethod
    @command_handler(
        "config.delete",
        required_scopes=["config:write"],
        required_args=["path"],
        description="Delete configuration value at path"
    )
    def config_delete(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Delete configuration value."""
        if not context.config_api:
            return CommandResult.error("Configuration API not available")

        result = context.config_api.delete(args["path"], source="agent")
        if result.success:
            return CommandResult.success(result.to_dict())
        else:
            return CommandResult.error(result.error)

    @staticmethod
    @command_handler(
        "config.reset",
        required_scopes=["config:write"],
        description="Reset configuration to defaults"
    )
    def config_reset(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Reset configuration."""
        if not context.config_api:
            return CommandResult.error("Configuration API not available")

        path = args.get("path", "")
        result = context.config_api.reset(path, source="agent")
        if result.success:
            return CommandResult.success(result.to_dict())
        else:
            return CommandResult.error(result.error)

    @staticmethod
    @command_handler(
        "config.export",
        required_scopes=["config:read"],
        description="Export entire configuration as JSON"
    )
    def config_export(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Export configuration."""
        if not context.config_api:
            return CommandResult.error("Configuration API not available")

        config_json = context.config_api.export_json(pretty=False)
        return CommandResult.success({"config": config_json})

    @staticmethod
    @command_handler(
        "config.import",
        required_scopes=["config:write"],
        required_args=["config"],
        description="Import configuration from JSON"
    )
    def config_import(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Import configuration."""
        if not context.config_api:
            return CommandResult.error("Configuration API not available")

        result = context.config_api.import_json(args["config"], source="agent")
        if result.success:
            return CommandResult.success({"imported": True})
        else:
            return CommandResult.error(result.error)

    @staticmethod
    @command_handler(
        "config.list",
        required_scopes=["config:read"],
        description="List all configuration paths"
    )
    def config_list(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """List configuration paths."""
        if not context.config_api:
            return CommandResult.error("Configuration API not available")

        prefix = args.get("prefix", "")
        paths = context.config_api.list_paths(prefix)
        return CommandResult.success({"paths": paths})

    @staticmethod
    @command_handler(
        "config.audit",
        required_scopes=["config:read"],
        description="Get configuration audit log"
    )
    def config_audit(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get audit log."""
        if not context.config_api:
            return CommandResult.error("Configuration API not available")

        limit = args.get("limit", 50)
        path_filter = args.get("path")
        log = context.config_api.get_audit_log(limit=limit, path_filter=path_filter)
        return CommandResult.success({
            "count": len(log),
            "changes": [c.to_dict() for c in log]
        })

    # -------------------------------------------------------------------------
    # Service Commands
    # -------------------------------------------------------------------------

    @staticmethod
    @command_handler(
        "service.status",
        required_scopes=["service:read"],
        required_args=["name"],
        description="Get systemd service status"
    )
    def service_status(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get service status."""
        service_name = _validate_service_name(args["name"])
        if not service_name:
            return CommandResult.error("Service not in allowlist", CommandStatus.INVALID_ARGS)

        try:
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            is_active = result.returncode == 0
            state = result.stdout.strip()

            # Get more details
            result = subprocess.run(
                ["systemctl", "show", service_name,
                 "--property=LoadState,ActiveState,SubState,MainPID"],
                capture_output=True,
                text=True,
                timeout=10
            )

            props = {}
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    props[key] = value

            return CommandResult.success({
                "name": service_name,
                "active": is_active,
                "state": state,
                "load_state": props.get("LoadState"),
                "active_state": props.get("ActiveState"),
                "sub_state": props.get("SubState"),
                "main_pid": int(props.get("MainPID", 0)),
            })

        except subprocess.TimeoutExpired:
            return CommandResult.error("Timeout checking service status", CommandStatus.TIMEOUT)
        except FileNotFoundError:
            return CommandResult.error("systemctl not found")
        except Exception as e:
            return CommandResult.error(str(e))

    @staticmethod
    @command_handler(
        "service.start",
        required_scopes=["service:control"],
        required_args=["name"],
        description="Start a systemd service"
    )
    def service_start(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Start a service."""
        service_name = _validate_service_name(args["name"])
        if not service_name:
            return CommandResult.error("Service not in allowlist", CommandStatus.INVALID_ARGS)

        try:
            result = subprocess.run(
                ["systemctl", "start", service_name],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                return CommandResult.success({"started": service_name})
            else:
                return CommandResult.error(result.stderr.strip())

        except subprocess.TimeoutExpired:
            return CommandResult.error("Timeout starting service", CommandStatus.TIMEOUT)
        except Exception as e:
            return CommandResult.error(str(e))

    @staticmethod
    @command_handler(
        "service.stop",
        required_scopes=["service:control"],
        required_args=["name"],
        description="Stop a systemd service"
    )
    def service_stop(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Stop a service."""
        service_name = _validate_service_name(args["name"])
        if not service_name:
            return CommandResult.error("Service not in allowlist", CommandStatus.INVALID_ARGS)

        try:
            result = subprocess.run(
                ["systemctl", "stop", service_name],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                return CommandResult.success({"stopped": service_name})
            else:
                return CommandResult.error(result.stderr.strip())

        except subprocess.TimeoutExpired:
            return CommandResult.error("Timeout stopping service", CommandStatus.TIMEOUT)
        except Exception as e:
            return CommandResult.error(str(e))

    @staticmethod
    @command_handler(
        "service.restart",
        required_scopes=["service:control"],
        required_args=["name"],
        description="Restart a systemd service"
    )
    def service_restart(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Restart a service."""
        service_name = _validate_service_name(args["name"])
        if not service_name:
            return CommandResult.error("Service not in allowlist", CommandStatus.INVALID_ARGS)

        try:
            result = subprocess.run(
                ["systemctl", "restart", service_name],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                return CommandResult.success({"restarted": service_name})
            else:
                return CommandResult.error(result.stderr.strip())

        except subprocess.TimeoutExpired:
            return CommandResult.error("Timeout restarting service", CommandStatus.TIMEOUT)
        except Exception as e:
            return CommandResult.error(str(e))

    # -------------------------------------------------------------------------
    # Health Commands
    # -------------------------------------------------------------------------

    @staticmethod
    @command_handler(
        "health.status",
        required_scopes=["health:read"],
        description="Get current health status for all services"
    )
    def health_status(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get health status."""
        if not context.health_state:
            return CommandResult.error("Health state not available")

        services = context.health_state.get_all_services()
        metrics = context.health_state.get_metrics()

        return CommandResult.success({
            "services": [s.to_dict() for s in services],
            "metrics": metrics,
        })

    @staticmethod
    @command_handler(
        "health.service",
        required_scopes=["health:read"],
        required_args=["name"],
        description="Get health status for specific service"
    )
    def health_service(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get service health."""
        if not context.health_state:
            return CommandResult.error("Health state not available")

        service = context.health_state.get_service(args["name"])
        if service:
            percentiles = context.health_state.get_latency_percentiles(args["name"])
            return CommandResult.success({
                "service": service.to_dict(),
                "latency_percentiles": percentiles,
            })
        else:
            return CommandResult.error(f"Service not found: {args['name']}", CommandStatus.NOT_FOUND)

    @staticmethod
    @command_handler(
        "health.probe",
        required_scopes=["health:control"],
        description="Trigger immediate health probe"
    )
    def health_probe(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Trigger health probe."""
        if not context.health_probe:
            return CommandResult.error("Health probe not available")

        service_name = args.get("name")
        if service_name:
            result = context.health_probe.check_now(service_name)
            if result:
                return CommandResult.success({
                    "service": service_name,
                    "result": {
                        "healthy": result.healthy,
                        "reason": result.reason,
                        "latency_ms": result.latency_ms,
                    }
                })
            else:
                return CommandResult.error(f"Service not registered: {service_name}")
        else:
            # Check all services
            results = {}
            for name in context.health_probe.list_services():
                result = context.health_probe.check_now(name)
                if result:
                    results[name] = {
                        "healthy": result.healthy,
                        "reason": result.reason,
                        "latency_ms": result.latency_ms,
                    }
            return CommandResult.success({"results": results})

    @staticmethod
    @command_handler(
        "health.events",
        required_scopes=["health:read"],
        description="Get recent health events"
    )
    def health_events(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get health events."""
        if not context.health_state:
            return CommandResult.error("Health state not available")

        limit = args.get("limit", 50)
        hours = args.get("hours", 24)
        service = args.get("service")

        events = context.health_state.get_recent_events(
            service=service,
            limit=limit,
            hours=hours
        )

        return CommandResult.success({
            "count": len(events),
            "events": [e.to_dict() for e in events],
        })

    # -------------------------------------------------------------------------
    # Metrics Commands
    # -------------------------------------------------------------------------

    @staticmethod
    @command_handler(
        "metrics.get",
        required_scopes=["metrics:read"],
        description="Get current metrics summary"
    )
    def metrics_get(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get metrics summary."""
        if not context.health_state:
            return CommandResult.error("Metrics not available")

        metrics = context.health_state.get_metrics()
        return CommandResult.success(metrics)

    @staticmethod
    @command_handler(
        "metrics.export",
        required_scopes=["metrics:read"],
        description="Export metrics in Prometheus format"
    )
    def metrics_export(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Export Prometheus metrics."""
        if not context.metrics_exporter:
            return CommandResult.error("Metrics exporter not available")

        prometheus_output = context.metrics_exporter.export()
        return CommandResult.success({
            "format": "prometheus",
            "metrics": prometheus_output,
        })

    # -------------------------------------------------------------------------
    # Agent Commands
    # -------------------------------------------------------------------------

    @staticmethod
    @command_handler(
        "agent.status",
        required_scopes=["agent:read"],
        description="Get agent status"
    )
    def agent_status(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get agent status."""
        return CommandResult.success({
            "instance_id": context.instance_id,
            "scopes": list(context.scopes),
            "components": {
                "config_api": context.config_api is not None,
                "health_state": context.health_state is not None,
                "health_probe": context.health_probe is not None,
                "metrics_exporter": context.metrics_exporter is not None,
            }
        })

    @staticmethod
    @command_handler(
        "agent.ping",
        description="Ping agent (no authentication required)"
    )
    def agent_ping(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Simple ping response."""
        return CommandResult.success({
            "pong": True,
            "timestamp": time.time(),
            "instance_id": context.instance_id,
        })

    # -------------------------------------------------------------------------
    # System Commands
    # -------------------------------------------------------------------------

    @staticmethod
    @command_handler(
        "system.info",
        required_scopes=["system:read"],
        description="Get system information"
    )
    def system_info(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get system information."""
        try:
            # Get version
            version = _version_mod if _HAS_VERSION else "unknown"

            return CommandResult.success({
                "meshforge_version": version,
                "python_version": platform.python_version(),
                "platform": platform.system(),
                "platform_release": platform.release(),
                "platform_version": platform.version(),
                "hostname": platform.node(),
                "architecture": platform.machine(),
                "processor": platform.processor(),
                "pid": os.getpid(),
            })
        except Exception as e:
            return CommandResult.error(str(e))

    @staticmethod
    @command_handler(
        "system.logs",
        required_scopes=["system:logs"],
        description="Get recent log entries"
    )
    def system_logs(args: Dict[str, Any], context: CommandContext) -> CommandResult:
        """Get recent log entries."""
        service = _validate_service_name(args.get("service", "meshforge"))
        if not service:
            return CommandResult.error("Service not in allowlist", CommandStatus.INVALID_ARGS)

        raw_lines = args.get("lines", 100)
        # Reject obviously abusive input before int() (arbitrarily long digit
        # strings cost O(n^2) to parse and waste CPU before the clamp).
        if isinstance(raw_lines, str) and len(raw_lines) > 8:
            return CommandResult.error(
                "lines value too long", CommandStatus.INVALID_ARGS
            )
        try:
            lines = int(raw_lines)
        except (TypeError, ValueError):
            return CommandResult.error(
                "lines must be an integer", CommandStatus.INVALID_ARGS
            )
        lines = min(max(lines, 1), 10000)

        try:
            result = subprocess.run(
                ["journalctl", "-u", service, "-n", str(lines), "--no-pager", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                return CommandResult.error(result.stderr.strip())

            # Parse journalctl JSON output
            import json
            entries = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

            return CommandResult.success({
                "service": service,
                "count": len(entries),
                "entries": entries,
            })

        except subprocess.TimeoutExpired:
            return CommandResult.error("Timeout fetching logs", CommandStatus.TIMEOUT)
        except FileNotFoundError:
            return CommandResult.error("journalctl not found")
        except Exception as e:
            return CommandResult.error(str(e))


# =============================================================================
# Registry Initialization
# =============================================================================


def create_default_registry() -> CommandRegistry:
    """Create a CommandRegistry with all built-in handlers.

    Returns:
        Configured CommandRegistry instance
    """
    registry = CommandRegistry()

    # Register all built-in handlers
    handlers = [
        # Configuration
        CommandHandler.config_get,
        CommandHandler.config_set,
        CommandHandler.config_delete,
        CommandHandler.config_reset,
        CommandHandler.config_export,
        CommandHandler.config_import,
        CommandHandler.config_list,
        CommandHandler.config_audit,
        # Service
        CommandHandler.service_status,
        CommandHandler.service_start,
        CommandHandler.service_stop,
        CommandHandler.service_restart,
        # Health
        CommandHandler.health_status,
        CommandHandler.health_service,
        CommandHandler.health_probe,
        CommandHandler.health_events,
        # Metrics
        CommandHandler.metrics_get,
        CommandHandler.metrics_export,
        # Agent
        CommandHandler.agent_status,
        CommandHandler.agent_ping,
        # System
        CommandHandler.system_info,
        CommandHandler.system_logs,
    ]

    for handler in handlers:
        registry.register(handler)

    return registry
