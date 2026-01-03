"""
RNS-Meshtastic Bridge Service
Bridges Reticulum Network Stack and Meshtastic networks
"""

import threading
import time
import logging
import subprocess
from queue import Queue, Empty
from datetime import datetime
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass
from pathlib import Path

from .config import GatewayConfig
from .node_tracker import UnifiedNodeTracker, UnifiedNode

logger = logging.getLogger(__name__)


@dataclass
class BridgedMessage:
    """Represents a message being bridged between networks"""
    source_network: str  # "meshtastic" or "rns"
    source_id: str
    destination_id: Optional[str]
    content: str
    title: Optional[str] = None
    timestamp: datetime = None
    is_broadcast: bool = False
    metadata: dict = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if self.metadata is None:
            self.metadata = {}


class RNSMeshtasticBridge:
    """
    Main gateway bridge between RNS and Meshtastic networks.

    Supports two modes:
    1. RNS Over Meshtastic - Uses Meshtastic as RNS transport layer
    2. Message Bridge - Translates messages between separate networks
    """

    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig.load()
        self.node_tracker = UnifiedNodeTracker()

        # State
        self._running = False
        self._connected_mesh = False
        self._connected_rns = False

        # Message queues
        self._mesh_to_rns_queue = Queue()
        self._rns_to_mesh_queue = Queue()

        # Threads
        self._mesh_thread = None
        self._rns_thread = None
        self._bridge_thread = None

        # Callbacks
        self._message_callbacks = []
        self._status_callbacks = []

        # RNS components (lazy loaded)
        self._reticulum = None
        self._lxmf_router = None
        self._identity = None
        self._lxmf_source = None

        # Meshtastic interface
        self._mesh_interface = None

        # Statistics
        self.stats = {
            'messages_mesh_to_rns': 0,
            'messages_rns_to_mesh': 0,
            'errors': 0,
            'start_time': None,
        }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._connected_mesh or self._connected_rns

    def start(self) -> bool:
        """Start the gateway bridge"""
        if self._running:
            logger.warning("Bridge already running")
            return True

        logger.info("Starting RNS-Meshtastic bridge...")
        self._running = True
        self.stats['start_time'] = datetime.now()

        # Start node tracker
        self.node_tracker.start()

        # Start network threads
        if self.config.enabled:
            self._mesh_thread = threading.Thread(
                target=self._meshtastic_loop,
                daemon=True,
                name="MeshtasticBridge"
            )
            self._mesh_thread.start()

            self._rns_thread = threading.Thread(
                target=self._rns_loop,
                daemon=True,
                name="RNSBridge"
            )
            self._rns_thread.start()

            self._bridge_thread = threading.Thread(
                target=self._bridge_loop,
                daemon=True,
                name="MessageBridge"
            )
            self._bridge_thread.start()

        logger.info("Bridge started")
        self._notify_status("started")
        return True

    def stop(self):
        """Stop the gateway bridge"""
        if not self._running:
            return

        logger.info("Stopping bridge...")
        self._running = False

        # Stop node tracker
        self.node_tracker.stop()

        # Close connections
        self._disconnect_meshtastic()
        self._disconnect_rns()

        # Wait for threads
        for thread in [self._mesh_thread, self._rns_thread, self._bridge_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=5)

        logger.info("Bridge stopped")
        self._notify_status("stopped")

    def get_status(self) -> dict:
        """Get current bridge status"""
        uptime = None
        if self.stats['start_time']:
            uptime = (datetime.now() - self.stats['start_time']).total_seconds()

        return {
            'running': self._running,
            'enabled': self.config.enabled,
            'meshtastic_connected': self._connected_mesh,
            'rns_connected': self._connected_rns,
            'uptime_seconds': uptime,
            'statistics': self.stats.copy(),
            'node_stats': self.node_tracker.get_stats(),
        }

    def send_to_meshtastic(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send a message to Meshtastic network"""
        if not self._connected_mesh:
            logger.warning("Not connected to Meshtastic")
            return False

        try:
            if self._mesh_interface:
                self._mesh_interface.sendText(
                    message,
                    destinationId=destination,
                    channelIndex=channel
                )
                return True
            else:
                # Fallback to CLI
                return self._send_via_cli(message, destination, channel)
        except Exception as e:
            logger.error(f"Failed to send to Meshtastic: {e}")
            self.stats['errors'] += 1
            return False

    def send_to_rns(self, message: str, destination_hash: bytes = None) -> bool:
        """Send a message to RNS network via LXMF"""
        if not self._connected_rns:
            logger.warning("Not connected to RNS")
            return False

        try:
            import RNS
            import LXMF

            if destination_hash:
                # Direct message
                if not RNS.Transport.has_path(destination_hash):
                    RNS.Transport.request_path(destination_hash)
                    # Wait briefly for path
                    for _ in range(50):
                        if RNS.Transport.has_path(destination_hash):
                            break
                        time.sleep(0.1)

                if not RNS.Transport.has_path(destination_hash):
                    logger.warning("No path to destination")
                    return False

                dest_identity = RNS.Identity.recall(destination_hash)
                destination = RNS.Destination(
                    dest_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf",
                    "delivery"
                )
            else:
                # Broadcast not directly supported in LXMF
                # Would need group destination or propagation
                logger.warning("Broadcast to RNS requires propagation node")
                return False

            lxm = LXMF.LXMessage(
                destination,
                self._lxmf_source,
                message,
                "MeshForge Gateway"
            )
            self._lxmf_router.handle_outbound(lxm)
            return True

        except Exception as e:
            logger.error(f"Failed to send to RNS: {e}")
            self.stats['errors'] += 1
            return False

    def register_message_callback(self, callback: Callable):
        """Register callback for bridged messages"""
        self._message_callbacks.append(callback)

    def register_status_callback(self, callback: Callable):
        """Register callback for status changes"""
        self._status_callbacks.append(callback)

    def test_connection(self) -> dict:
        """Test connectivity to both networks"""
        results = {
            'meshtastic': {'connected': False, 'error': None},
            'rns': {'connected': False, 'error': None},
        }

        # Test Meshtastic
        try:
            if self._test_meshtastic():
                results['meshtastic']['connected'] = True
        except Exception as e:
            results['meshtastic']['error'] = str(e)

        # Test RNS
        try:
            if self._test_rns():
                results['rns']['connected'] = True
        except Exception as e:
            results['rns']['error'] = str(e)

        return results

    # ========================================
    # Private Methods
    # ========================================

    def _meshtastic_loop(self):
        """Main loop for Meshtastic connection"""
        while self._running:
            try:
                if not self._connected_mesh:
                    self._connect_meshtastic()

                if self._connected_mesh:
                    # Process incoming messages
                    self._poll_meshtastic()

                time.sleep(1)

            except Exception as e:
                logger.error(f"Meshtastic loop error: {e}")
                self._connected_mesh = False
                time.sleep(5)

    def _rns_loop(self):
        """Main loop for RNS connection"""
        while self._running:
            try:
                if not self._connected_rns:
                    self._connect_rns()

                if self._connected_rns:
                    # RNS handles its own event loop
                    # Just keep the connection alive
                    time.sleep(1)

            except Exception as e:
                logger.error(f"RNS loop error: {e}")
                self._connected_rns = False
                time.sleep(5)

    def _bridge_loop(self):
        """Main loop for message bridging"""
        while self._running:
            try:
                # Process Meshtastic → RNS queue
                try:
                    msg = self._mesh_to_rns_queue.get(timeout=0.1)
                    self._process_mesh_to_rns(msg)
                except Empty:
                    pass

                # Process RNS → Meshtastic queue
                try:
                    msg = self._rns_to_mesh_queue.get(timeout=0.1)
                    self._process_rns_to_mesh(msg)
                except Empty:
                    pass

            except Exception as e:
                logger.error(f"Bridge loop error: {e}")
                time.sleep(1)

    def _connect_meshtastic(self):
        """Connect to Meshtastic via TCP or CLI"""
        try:
            import meshtastic
            import meshtastic.tcp_interface
            from pubsub import pub

            host = self.config.meshtastic.host
            port = self.config.meshtastic.port

            logger.info(f"Connecting to Meshtastic at {host}:{port}")

            self._mesh_interface = meshtastic.tcp_interface.TCPInterface(
                hostname=host
            )

            # Subscribe to messages
            def on_receive(packet, interface):
                self._on_meshtastic_receive(packet)

            pub.subscribe(on_receive, "meshtastic.receive")

            # Get initial node list
            self._update_meshtastic_nodes()

            self._connected_mesh = True
            logger.info("Connected to Meshtastic")
            self._notify_status("meshtastic_connected")

        except ImportError:
            logger.warning("Meshtastic library not installed, using CLI fallback")
            self._connected_mesh = self._test_meshtastic_cli()
        except Exception as e:
            logger.error(f"Failed to connect to Meshtastic: {e}")
            self._connected_mesh = False

    def _disconnect_meshtastic(self):
        """Disconnect from Meshtastic"""
        if self._mesh_interface:
            try:
                self._mesh_interface.close()
            except:
                pass
            self._mesh_interface = None
        self._connected_mesh = False

    def _connect_rns(self):
        """Initialize RNS and LXMF"""
        try:
            import RNS
            import LXMF

            # Initialize Reticulum
            config_dir = self.config.rns.config_dir or None
            self._reticulum = RNS.Reticulum(configdir=config_dir)

            # Create or load identity
            identity_path = Path.home() / ".config" / "meshforge" / "gateway_identity"
            if identity_path.exists():
                self._identity = RNS.Identity.from_file(str(identity_path))
            else:
                self._identity = RNS.Identity()
                identity_path.parent.mkdir(parents=True, exist_ok=True)
                self._identity.to_file(str(identity_path))

            # Create LXMF router
            storage_path = Path.home() / ".config" / "meshforge" / "lxmf_storage"
            storage_path.mkdir(parents=True, exist_ok=True)
            self._lxmf_router = LXMF.LXMRouter(storagepath=str(storage_path))

            # Register delivery callback
            self._lxmf_router.register_delivery_callback(self._on_lxmf_receive)

            # Create source identity
            self._lxmf_source = self._lxmf_router.register_delivery_identity(
                self._identity,
                display_name="MeshForge Gateway"
            )

            # Announce presence
            self._lxmf_router.announce(self._lxmf_source.hash)

            # Register announce handler for node discovery
            class AnnounceHandler:
                def __init__(self, bridge):
                    self.aspect_filter = "lxmf.delivery"
                    self.bridge = bridge

                def received_announce(self, dest_hash, announced_identity, app_data):
                    self.bridge._on_rns_announce(dest_hash, announced_identity, app_data)

            RNS.Transport.register_announce_handler(AnnounceHandler(self))

            self._connected_rns = True
            logger.info("Connected to RNS")
            self._notify_status("rns_connected")

        except ImportError:
            logger.warning("RNS library not installed")
            self._connected_rns = False
        except Exception as e:
            logger.error(f"Failed to initialize RNS: {e}")
            self._connected_rns = False

    def _disconnect_rns(self):
        """Disconnect from RNS"""
        self._lxmf_router = None
        self._lxmf_source = None
        self._identity = None
        self._reticulum = None
        self._connected_rns = False

    def _on_meshtastic_receive(self, packet: dict):
        """Handle incoming Meshtastic message"""
        try:
            decoded = packet.get('decoded', {})
            portnum = decoded.get('portnum')

            # Update node info
            from_id = packet.get('fromId')
            if from_id:
                node = UnifiedNode.from_meshtastic({
                    'num': int(from_id[1:], 16) if from_id.startswith('!') else 0,
                    'snr': packet.get('rxSnr'),
                    'hopsAway': packet.get('hopStart', 0) - packet.get('hopLimit', 0),
                })
                self.node_tracker.add_node(node)

            # Handle text messages
            if portnum == 'TEXT_MESSAGE_APP':
                payload = decoded.get('payload', b'')
                if isinstance(payload, bytes):
                    text = payload.decode('utf-8', errors='ignore')
                else:
                    text = str(payload)

                msg = BridgedMessage(
                    source_network="meshtastic",
                    source_id=from_id,
                    destination_id=packet.get('toId'),
                    content=text,
                    is_broadcast=packet.get('toId') == '!ffffffff',
                    metadata={
                        'channel': packet.get('channel', 0),
                        'snr': packet.get('rxSnr'),
                    }
                )

                # Queue for bridging if enabled
                if self._should_bridge(msg):
                    self._mesh_to_rns_queue.put(msg)

                # Notify callbacks
                self._notify_message(msg)

        except Exception as e:
            logger.error(f"Error processing Meshtastic message: {e}")

    def _on_lxmf_receive(self, message):
        """Handle incoming LXMF message"""
        try:
            # Update node info
            source_hash = message.source_hash
            node = UnifiedNode.from_rns(source_hash)
            self.node_tracker.add_node(node)

            msg = BridgedMessage(
                source_network="rns",
                source_id=source_hash.hex(),
                destination_id=None,
                content=message.content,
                title=message.title,
                metadata={
                    'lxmf_stamp': message.stamp,
                }
            )

            # Queue for bridging if enabled
            if self._should_bridge(msg):
                self._rns_to_mesh_queue.put(msg)

            # Notify callbacks
            self._notify_message(msg)

        except Exception as e:
            logger.error(f"Error processing LXMF message: {e}")

    def _on_rns_announce(self, dest_hash, announced_identity, app_data):
        """Handle RNS announce for node discovery"""
        try:
            node = UnifiedNode.from_rns(dest_hash, app_data=app_data)
            self.node_tracker.add_node(node)
            logger.debug(f"Discovered RNS node: {dest_hash.hex()[:8]}")
        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")

    def _should_bridge(self, msg: BridgedMessage) -> bool:
        """Check if message should be bridged based on routing rules"""
        if not self.config.enabled:
            return False

        # Check routing rules
        for rule in self.config.routing_rules:
            if not rule.enabled:
                continue

            # Check direction
            if msg.source_network == "meshtastic" and rule.direction == "rns_to_mesh":
                continue
            if msg.source_network == "rns" and rule.direction == "mesh_to_rns":
                continue

            # Apply filters (TODO: implement regex matching)
            return True

        # Use default route
        return self.config.default_route in ("bidirectional", f"{msg.source_network}_to_*")

    def _process_mesh_to_rns(self, msg: BridgedMessage):
        """Process message from Meshtastic to RNS"""
        try:
            prefix = f"[Mesh:{msg.source_id[-4:]}] " if not msg.is_broadcast else "[Mesh] "
            content = prefix + msg.content

            # For now, log the bridged message
            # Full LXMF routing requires destination mapping
            logger.info(f"Bridge Mesh→RNS: {content[:50]}...")
            self.stats['messages_mesh_to_rns'] += 1

        except Exception as e:
            logger.error(f"Error bridging Mesh→RNS: {e}")
            self.stats['errors'] += 1

    def _process_rns_to_mesh(self, msg: BridgedMessage):
        """Process message from RNS to Meshtastic"""
        try:
            prefix = f"[RNS:{msg.source_id[:4]}] "
            content = prefix + msg.content

            if self.send_to_meshtastic(content, channel=self.config.meshtastic.channel):
                logger.info(f"Bridge RNS→Mesh: {content[:50]}...")
                self.stats['messages_rns_to_mesh'] += 1
            else:
                logger.warning("Failed to bridge RNS→Mesh")
                self.stats['errors'] += 1

        except Exception as e:
            logger.error(f"Error bridging RNS→Mesh: {e}")
            self.stats['errors'] += 1

    def _update_meshtastic_nodes(self):
        """Update node tracker with Meshtastic nodes"""
        if not self._mesh_interface:
            return

        try:
            my_info = self._mesh_interface.getMyNodeInfo()
            my_id = my_info.get('num', 0)

            for node_id, node_data in self._mesh_interface.nodes.items():
                is_local = node_data.get('num') == my_id
                node = UnifiedNode.from_meshtastic(node_data, is_local=is_local)
                self.node_tracker.add_node(node)

        except Exception as e:
            logger.error(f"Error updating Meshtastic nodes: {e}")

    def _poll_meshtastic(self):
        """Poll Meshtastic for updates (when not using pub/sub)"""
        # The pub/sub handles real-time messages
        # Periodically refresh node list
        pass

    def _test_meshtastic(self) -> bool:
        """Test Meshtastic connection"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((
                self.config.meshtastic.host,
                self.config.meshtastic.port
            ))
            sock.close()
            return result == 0
        except:
            return False

    def _test_meshtastic_cli(self) -> bool:
        """Test Meshtastic CLI availability"""
        try:
            result = subprocess.run(
                ['meshtastic', '--info'],
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except:
            return False

    def _test_rns(self) -> bool:
        """Test RNS availability"""
        try:
            import RNS
            return True
        except ImportError:
            return False

    def _send_via_cli(self, message: str, destination: str = None, channel: int = 0) -> bool:
        """Send via Meshtastic CLI as fallback"""
        try:
            cmd = ['meshtastic', '--host', self.config.meshtastic.host, '--sendtext', message]
            if destination:
                cmd.extend(['--dest', destination])
            if channel > 0:
                cmd.extend(['--ch-index', str(channel)])

            result = subprocess.run(cmd, capture_output=True, timeout=30)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"CLI send failed: {e}")
            return False

    def _notify_message(self, msg: BridgedMessage):
        """Notify message callbacks"""
        for callback in self._message_callbacks:
            try:
                callback(msg)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

    def _notify_status(self, status: str):
        """Notify status callbacks"""
        for callback in self._status_callbacks:
            try:
                callback(status, self.get_status())
            except Exception as e:
                logger.error(f"Status callback error: {e}")
