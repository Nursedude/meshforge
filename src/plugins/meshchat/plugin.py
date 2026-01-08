"""
MeshChat Plugin for MeshForge

Provides integration with Reticulum MeshChat via HTTP API.
MeshChat runs as an external systemd service - this plugin connects to it.

Features:
- Service status detection
- Peer discovery (merged with MeshForge node tracker)
- LXMF messaging via MeshChat
- Diagnostic integration
"""

import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Callable

from .client import MeshChatClient, MeshChatError, MeshChatPeer
from .service import MeshChatService, ServiceStatus, ServiceState

logger = logging.getLogger(__name__)


@dataclass
class PluginMetadata:
    """Plugin metadata for registration."""
    name: str = "meshchat"
    version: str = "1.0.0"
    description: str = "MeshChat LXMF messaging integration"
    plugin_type: str = "integration"
    external_service: bool = True
    service_port: int = 8000
    optional: bool = True


class MeshChatPlugin:
    """
    MeshChat integration plugin.

    Follows MeshForge's architecture: services run independently,
    MeshForge connects to them via API.

    Usage:
        plugin = MeshChatPlugin()

        # Check if MeshChat is available
        if plugin.is_available():
            peers = plugin.get_peers()
            plugin.send_message(destination, "Hello!")

        # For UI integration
        plugin.check_status_async(callback=update_ui_status)
    """

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 8000
    ):
        self.host = host
        self.port = port
        self._service = MeshChatService(host=host, port=port)
        self._client: Optional[MeshChatClient] = None
        self._activated = False

    @staticmethod
    def get_metadata() -> PluginMetadata:
        """Return plugin metadata for registration."""
        return PluginMetadata()

    def activate(self) -> bool:
        """
        Activate the plugin.

        Returns True if MeshChat is available, False otherwise.
        Plugin can still be used in degraded mode when False.
        """
        logger.info("Activating MeshChat plugin")
        self._activated = True

        # Check if service is available
        status = self._service.check_status(blocking=True)

        if status.available:
            self._client = MeshChatClient(host=self.host, port=self.port)
            logger.info(f"MeshChat connected (v{status.version})")
            return True
        else:
            logger.info(f"MeshChat not available: {status.state.value}")
            if status.fix_hint:
                logger.debug(f"Fix hint: {status.fix_hint}")
            return False

    def deactivate(self):
        """Deactivate the plugin and cleanup resources."""
        logger.info("Deactivating MeshChat plugin")
        if self._client:
            self._client.close()
            self._client = None
        self._activated = False

    def is_available(self) -> bool:
        """Check if MeshChat service is reachable."""
        status = self._service.last_status
        if status.state == ServiceState.UNKNOWN:
            status = self._service.check_status(blocking=True)
        return status.available

    def check_status_async(
        self,
        callback: Callable[[ServiceStatus], None]
    ):
        """
        Check service status asynchronously.

        Use this for UI updates to avoid blocking.

        Args:
            callback: Function called with ServiceStatus when check completes
        """
        self._service.check_status(callback=callback)

    def get_status(self) -> ServiceStatus:
        """Get current service status (blocking)."""
        return self._service.check_status(blocking=True)

    def get_client(self) -> Optional[MeshChatClient]:
        """
        Get HTTP client for direct API access.

        Returns None if service is not available.
        """
        if not self.is_available():
            return None

        if self._client is None:
            self._client = MeshChatClient(host=self.host, port=self.port)

        return self._client

    # Convenience methods that wrap client functionality

    def get_peers(self) -> List[MeshChatPeer]:
        """Get list of discovered peers from MeshChat."""
        client = self.get_client()
        if client:
            try:
                return client.get_peers()
            except MeshChatError as e:
                logger.warning(f"Failed to get peers: {e}")
        return []

    def send_message(self, destination_hash: str, content: str) -> bool:
        """Send LXMF message via MeshChat."""
        client = self.get_client()
        if client:
            return client.send_message(destination_hash, content)
        logger.warning("Cannot send message: MeshChat not available")
        return False

    def send_announce(self) -> bool:
        """Send LXMF announce via MeshChat."""
        client = self.get_client()
        if client:
            return client.send_announce()
        return False

    def get_identity(self) -> Optional[str]:
        """Get MeshChat's RNS identity hash."""
        client = self.get_client()
        if client:
            return client.get_identity()
        return None

    # Service management (requires sudo)

    def start_service(self, callback: Optional[Callable[[bool, str], None]] = None):
        """Start MeshChat systemd service."""
        self._service.start(callback=callback)

    def stop_service(self, callback: Optional[Callable[[bool, str], None]] = None):
        """Stop MeshChat systemd service."""
        self._service.stop(callback=callback)

    # Diagnostic integration

    def get_diagnostic_check(self) -> Dict[str, Any]:
        """
        Return diagnostic check result for gateway_diagnostic.py integration.

        Returns dict compatible with CheckResult pattern:
        {
            'name': 'MeshChat',
            'status': 'pass' | 'warn' | 'fail',
            'message': 'Status message',
            'fix_hint': 'How to fix (if applicable)'
        }
        """
        status = self._service.check_status(blocking=True)

        if status.available:
            version_str = f" v{status.version}" if status.version else ""
            return {
                'name': 'MeshChat',
                'status': 'pass',
                'message': f"Running{version_str} on port {self.port}",
                'details': {
                    'pid': status.pid,
                    'service': status.service_name
                }
            }
        elif status.state == ServiceState.STOPPED:
            return {
                'name': 'MeshChat',
                'status': 'warn',
                'message': 'Installed but not running',
                'fix_hint': status.fix_hint
            }
        elif status.state == ServiceState.STARTING:
            return {
                'name': 'MeshChat',
                'status': 'warn',
                'message': 'Starting (port not ready)',
                'fix_hint': 'Wait a few seconds for service to initialize'
            }
        else:
            return {
                'name': 'MeshChat',
                'status': 'info',
                'message': 'Not installed (optional)',
                'fix_hint': status.fix_hint,
                'install_url': MeshChatService.INSTALL_URL
            }

    def get_full_diagnostic(self) -> Dict[str, Any]:
        """Get complete diagnostic information."""
        return self._service.get_diagnostic_info()

    def __repr__(self):
        status = self._service.last_status
        return f"MeshChatPlugin({self.host}:{self.port}, state={status.state.value})"


# Module-level singleton for easy access
_plugin_instance: Optional[MeshChatPlugin] = None


def get_plugin(host: str = '127.0.0.1', port: int = 8000) -> MeshChatPlugin:
    """Get or create the MeshChat plugin singleton."""
    global _plugin_instance
    if _plugin_instance is None:
        _plugin_instance = MeshChatPlugin(host=host, port=port)
    return _plugin_instance
