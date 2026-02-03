"""
MeshForge Lightweight Message Listener

Standalone message receiver that doesn't require the full gateway bridge.
Supports two modes:
  - TCP: Direct connection to meshtasticd via pubsub (default)
  - MQTT: Subscribe to local MQTT broker (for multi-consumer architecture)

Usage:
    from utils.message_listener import MessageListener

    # TCP mode (default) - requires exclusive TCP access
    listener = MessageListener()
    listener.start()

    # MQTT mode - works alongside other TCP consumers
    listener = MessageListener(mode="mqtt")
    listener.start()

    # Messages automatically stored via messaging.store_incoming()

    listener.stop()
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional, Callable, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Connection states
DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED = "connected"
ERROR = "error"

# Listener modes
MODE_TCP = "tcp"
MODE_MQTT = "mqtt"


@dataclass
class ListenerStatus:
    """Current listener status."""
    state: str
    connected_since: Optional[datetime] = None
    messages_received: int = 0
    last_message_time: Optional[datetime] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            'state': self.state,
            'connected_since': self.connected_since.isoformat() if self.connected_since else None,
            'messages_received': self.messages_received,
            'last_message_time': self.last_message_time.isoformat() if self.last_message_time else None,
            'error': self.error,
        }


class MessageListener:
    """
    Lightweight Meshtastic message listener.

    Subscribes to meshtastic.receive pubsub events and stores incoming
    messages without requiring the full RNS bridge.

    If another component (like the gateway bridge) already has a persistent
    connection, this listener will share it via pub/sub instead of creating
    a new connection. meshtasticd only supports ONE TCP connection at a time.
    """

    def __init__(
        self,
        host: str = "localhost",
        store_messages: bool = True,
        mode: str = MODE_TCP,
        mqtt_broker: str = "localhost",
        mqtt_port: int = 1883,
    ):
        """
        Initialize the listener.

        Args:
            host: Meshtastic host for TCP mode (localhost for meshtasticd)
            store_messages: Whether to store messages via messaging.store_incoming()
            mode: Listener mode - "tcp" (default) or "mqtt"
            mqtt_broker: MQTT broker hostname for mqtt mode
            mqtt_port: MQTT broker port for mqtt mode (1883 for non-TLS)
        """
        self.host = host
        self.store_messages = store_messages
        self.mode = mode
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self._status = ListenerStatus(state=DISCONNECTED)
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._interface = None
        self._mqtt_subscriber = None  # For MQTT mode
        self._owns_connection = False  # Track if we created the connection
        self._callbacks: List[Callable] = []
        self._lock = threading.Lock()

    def add_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Register a callback for incoming messages.

        Callback receives dict with: from_id, to_id, content, channel, snr, rssi, timestamp
        """
        with self._lock:
            self._callbacks.append(callback)

    def remove_callback(self, callback: Callable):
        """Remove a registered callback."""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def get_status(self) -> ListenerStatus:
        """Get current listener status."""
        return self._status

    def start(self) -> bool:
        """
        Start listening for messages.

        Returns:
            True if started successfully
        """
        if self._running:
            logger.warning("Listener already running")
            return True

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="meshforge-message-listener"
        )
        self._thread.start()

        # Wait briefly for connection
        time.sleep(1)
        return self._status.state == CONNECTED

    def stop(self):
        """Stop listening for messages."""
        self._running = False
        self._stop_event.set()

        if self.mode == MODE_MQTT:
            # Stop MQTT subscriber
            if self._mqtt_subscriber:
                try:
                    self._mqtt_subscriber.stop()
                except Exception as e:
                    logger.debug(f"Cleanup: MQTT stop: {e}")
                self._mqtt_subscriber = None
        else:
            # TCP mode cleanup
            # Unsubscribe from pubsub
            try:
                from pubsub import pub
                pub.unsubscribe(self._on_receive, "meshtastic.receive")
            except Exception as e:
                logger.debug(f"Cleanup: pubsub unsubscribe: {e}")

            # Only close interface if we own it (not borrowing from gateway)
            if self._interface and self._owns_connection:
                try:
                    from utils.meshtastic_connection import safe_close_interface, get_connection_manager
                    safe_close_interface(self._interface)
                    # Release persistent connection if we acquired it
                    get_connection_manager().release_persistent()
                except Exception as e:
                    logger.debug(f"Cleanup: interface close: {e}")
                self._interface = None

        self._owns_connection = False
        self._status.state = DISCONNECTED
        logger.info("Message listener stopped")

    def _run(self):
        """Main listener loop."""
        self._status.state = CONNECTING

        if self.mode == MODE_MQTT:
            self._run_mqtt_mode()
        else:
            self._run_tcp_mode()

    def _run_mqtt_mode(self):
        """Run listener in MQTT mode - subscribe to local MQTT broker."""
        try:
            from monitoring.mqtt_subscriber import create_local_subscriber
        except ImportError as e:
            self._status.state = ERROR
            self._status.error = f"MQTT module not available: {e}"
            logger.error(f"Cannot start MQTT listener: {e}")
            return

        try:
            # Create local MQTT subscriber
            logger.info(f"Connecting to MQTT broker {self.mqtt_broker}:{self.mqtt_port}")
            self._mqtt_subscriber = create_local_subscriber(
                broker=self.mqtt_broker,
                port=self.mqtt_port,
            )

            # Register callback for text messages
            self._mqtt_subscriber.register_message_callback(self._on_mqtt_message)

            # Start the subscriber
            if self._mqtt_subscriber.start():
                self._status.state = CONNECTED
                self._status.connected_since = datetime.now()
                self._status.error = None
                logger.info("MQTT message listener connected")
            else:
                # Wait for async connection
                for _ in range(10):
                    time.sleep(0.5)
                    if self._mqtt_subscriber.is_connected():
                        self._status.state = CONNECTED
                        self._status.connected_since = datetime.now()
                        self._status.error = None
                        logger.info("MQTT message listener connected")
                        break
                else:
                    self._status.state = ERROR
                    self._status.error = "MQTT connection timeout"
                    logger.warning("MQTT connection pending - will retry in background")

            # Keep thread alive while running
            while self._running:
                if self._stop_event.wait(1):
                    break

                # Check MQTT connection health
                if not self._mqtt_subscriber.is_connected():
                    if self._status.state == CONNECTED:
                        logger.warning("MQTT connection lost")
                        self._status.state = CONNECTING
                elif self._status.state != CONNECTED:
                    self._status.state = CONNECTED
                    self._status.connected_since = datetime.now()
                    logger.info("MQTT reconnected")

        except Exception as e:
            self._status.state = ERROR
            self._status.error = str(e)
            logger.error(f"MQTT listener error: {e}")

    def _run_tcp_mode(self):
        """Run listener in TCP mode - direct meshtasticd connection."""
        try:
            # Import dependencies
            try:
                from pubsub import pub
                from utils.meshtastic_connection import get_connection_manager
            except ImportError as e:
                self._status.state = ERROR
                self._status.error = f"Missing dependency: {e}"
                logger.error(f"Cannot start listener: {e}")
                return

            # Check if another component (like gateway) already has a connection
            # meshtasticd only supports ONE TCP connection at a time
            conn_mgr = get_connection_manager(host=self.host)

            if conn_mgr.has_persistent():
                # Another component owns the connection - just subscribe to pub/sub
                self._interface = conn_mgr.get_interface()
                self._owns_connection = False
                owner = conn_mgr.get_persistent_owner()
                logger.info(f"Using existing connection from {owner} (pub/sub only)")
            else:
                # No existing connection - we need to create one
                logger.info(f"Connecting to meshtastic at {self.host}...")
                if conn_mgr.acquire_persistent(owner="message_listener"):
                    self._interface = conn_mgr.get_interface()
                    self._owns_connection = True
                    logger.info("Message listener acquired connection")
                else:
                    self._status.state = ERROR
                    self._status.error = "Failed to acquire connection"
                    logger.error("Failed to acquire meshtastic connection")
                    return

            # Subscribe to messages (works regardless of who owns connection)
            pub.subscribe(self._on_receive, "meshtastic.receive")

            self._status.state = CONNECTED
            self._status.connected_since = datetime.now()
            self._status.error = None
            logger.info("Message listener connected and subscribed")

            # Keep thread alive while running (interruptible via stop_event)
            while self._running:
                if self._stop_event.wait(1):
                    break

                # Only check connection health if we own it
                if self._owns_connection and self._interface:
                    if not getattr(self._interface, 'isConnected', True):
                        logger.warning("Connection lost, attempting reconnect...")
                        self._reconnect()

        except Exception as e:
            self._status.state = ERROR
            self._status.error = str(e)
            logger.error(f"Listener error: {e}")

    def _reconnect(self):
        """Attempt to reconnect after connection loss."""
        # Only reconnect if we own the connection
        if not self._owns_connection:
            logger.debug("Connection lost but we don't own it - waiting for owner to reconnect")
            return

        self._status.state = CONNECTING

        try:
            from utils.meshtastic_connection import (
                get_connection_manager, safe_close_interface, wait_for_cooldown
            )
            conn_mgr = get_connection_manager(host=self.host)

            # Release old connection properly
            conn_mgr.release_persistent()
            self._interface = None

            # Wait for meshtasticd to cleanup
            wait_for_cooldown()

            # Exponential backoff for reconnection
            for attempt in range(5):
                if not self._running:
                    return

                try:
                    if conn_mgr.acquire_persistent(owner="message_listener"):
                        self._interface = conn_mgr.get_interface()
                        self._status.state = CONNECTED
                        self._status.connected_since = datetime.now()
                        logger.info("Reconnected to meshtastic")
                        return
                except Exception as e:
                    wait_time = 2 ** attempt
                    logger.warning(f"Reconnect attempt {attempt + 1} failed: {e}, waiting {wait_time}s")
                    time.sleep(wait_time)

            self._status.state = ERROR
            self._status.error = "Failed to reconnect after 5 attempts"
            self._owns_connection = False

        except Exception as e:
            logger.error(f"Reconnect error: {e}")
            self._status.state = ERROR
            self._status.error = str(e)

    def _on_receive(self, packet, interface=None):
        """Handle incoming meshtastic packet."""
        try:
            decoded = packet.get('decoded', {})
            portnum = decoded.get('portnum')
            from_id = packet.get('fromId', packet.get('from'))

            # Handle different packet types
            if portnum == 'TEXT_MESSAGE_APP':
                self._handle_text_message(packet, decoded, from_id)
            elif portnum == 'TELEMETRY_APP':
                self._handle_telemetry(packet, decoded, from_id)
            elif portnum == 'POSITION_APP':
                self._handle_position(packet, decoded, from_id)
            # Silently ignore other packet types (NODEINFO, ROUTING, etc.)

        except Exception as e:
            logger.error(f"Error processing received packet: {e}")

    def _handle_text_message(self, packet, decoded, from_id):
        """Handle TEXT_MESSAGE_APP packets."""
        to_id = packet.get('toId', packet.get('to'))
        channel = packet.get('channel', 0)

        # Extract text content
        payload = decoded.get('payload', b'')
        if isinstance(payload, bytes):
            content = payload.decode('utf-8', errors='ignore')
        else:
            content = str(payload)

        if not content:
            return

        # Signal quality
        snr = packet.get('rxSnr')
        rssi = packet.get('rxRssi')

        # Hop info
        hop_start = packet.get('hopStart', 0)
        hop_limit = packet.get('hopLimit', 0)
        hops_away = hop_start - hop_limit if hop_start else 0

        # Update status
        self._status.messages_received += 1
        self._status.last_message_time = datetime.now()

        # Build message dict
        msg_data = {
            'from_id': from_id,
            'to_id': to_id if to_id not in ('!ffffffff', '^all') else None,
            'content': content,
            'channel': channel,
            'snr': snr,
            'rssi': rssi,
            'hops_away': hops_away,
            'hop_start': hop_start,
            'hop_limit': hop_limit,
            'timestamp': datetime.now().isoformat(),
            'is_broadcast': to_id in ('!ffffffff', '^all', None),
        }

        logger.info(
            f"RX: {from_id} -> {to_id or 'broadcast'} "
            f"[ch={channel}, hops={hops_away}, SNR={snr}]: {content[:50]}..."
        )

        # Store message if enabled
        if self.store_messages:
            try:
                from commands import messaging
                messaging.store_incoming(
                    from_id=from_id,
                    content=content,
                    network="meshtastic",
                    to_id=msg_data['to_id'],
                    channel=channel,
                    snr=snr,
                    rssi=rssi,
                )
            except Exception as e:
                logger.debug(f"Could not store message: {e}")

        # Notify callbacks
        with self._lock:
            for callback in self._callbacks:
                try:
                    callback(msg_data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

    def _handle_telemetry(self, packet, decoded, from_id):
        """Handle TELEMETRY_APP packets - sensor data."""
        try:
            telemetry = decoded.get('telemetry', {})

            # Environment metrics (BME280, BME680, etc.)
            env_metrics = telemetry.get('environmentMetrics', {})
            if env_metrics:
                temp = env_metrics.get('temperature')
                humidity = env_metrics.get('relativeHumidity')
                pressure = env_metrics.get('barometricPressure')

                if temp or humidity or pressure:
                    logger.info(
                        f"SENSOR [{from_id}]: Temp={temp}°C, "
                        f"Humidity={humidity}%, Pressure={pressure}hPa"
                    )

                    # Update node tracker if available
                    try:
                        from gateway.node_tracker import get_node_tracker, Telemetry
                        tracker = get_node_tracker()
                        node = tracker.get_node(from_id)
                        if node:
                            if not node.telemetry:
                                node.telemetry = Telemetry()
                            node.telemetry.temperature = temp
                            node.telemetry.humidity = humidity
                            node.telemetry.barometric_pressure = pressure
                            tracker.add_node(node)
                    except ImportError:
                        pass

            # Air quality metrics (PMSA003I, SCD4X, etc.)
            aq_metrics = telemetry.get('airQualityMetrics', {})
            if aq_metrics:
                pm25 = aq_metrics.get('pm25Standard')
                pm10 = aq_metrics.get('pm10Standard')
                co2 = aq_metrics.get('co2')
                iaq = aq_metrics.get('iaq')

                if pm25 or pm10 or co2:
                    logger.info(
                        f"AIR QUALITY [{from_id}]: PM2.5={pm25}, "
                        f"PM10={pm10}, CO2={co2}ppm, IAQ={iaq}"
                    )

                    # Update node tracker
                    try:
                        from gateway.node_tracker import (
                            get_node_tracker, AirQualityMetrics
                        )
                        tracker = get_node_tracker()
                        node = tracker.get_node(from_id)
                        if node and node.telemetry:
                            node.telemetry.air_quality = AirQualityMetrics(
                                pm10_standard=pm10,
                                pm25_standard=pm25,
                                co2=co2,
                                iaq=iaq,
                            )
                            tracker.add_node(node)
                    except ImportError:
                        pass

            # Device metrics (battery, voltage, channel utilization)
            device_metrics = telemetry.get('deviceMetrics', {})
            if device_metrics:
                battery = device_metrics.get('batteryLevel')
                voltage = device_metrics.get('voltage')
                ch_util = device_metrics.get('channelUtilization')
                air_util = device_metrics.get('airUtilTx')

                if battery is not None:
                    logger.debug(
                        f"DEVICE [{from_id}]: Battery={battery}%, "
                        f"Voltage={voltage}V, ChUtil={ch_util}%"
                    )

                    try:
                        from gateway.node_tracker import get_node_tracker, Telemetry
                        tracker = get_node_tracker()
                        node = tracker.get_node(from_id)
                        if node:
                            if not node.telemetry:
                                node.telemetry = Telemetry()
                            node.telemetry.battery_level = battery
                            node.telemetry.voltage = voltage
                            node.telemetry.channel_utilization = ch_util
                            node.telemetry.air_util_tx = air_util
                            tracker.add_node(node)
                    except ImportError:
                        pass

        except Exception as e:
            logger.debug(f"Error processing telemetry: {e}")

    def _handle_position(self, packet, decoded, from_id):
        """Handle POSITION_APP packets."""
        try:
            position = decoded.get('position', {})
            lat = position.get('latitudeI', 0) / 1e7 if position.get('latitudeI') else None
            lon = position.get('longitudeI', 0) / 1e7 if position.get('longitudeI') else None
            alt = position.get('altitude')

            if lat and lon:
                logger.debug(f"POSITION [{from_id}]: {lat:.4f}, {lon:.4f}, alt={alt}m")

                # Update node tracker
                try:
                    from gateway.node_tracker import get_node_tracker, Position
                    tracker = get_node_tracker()
                    node = tracker.get_node(from_id)
                    if node:
                        node.position = Position(
                            latitude=lat,
                            longitude=lon,
                            altitude=alt or 0,
                        )
                        tracker.add_node(node)
                except ImportError:
                    pass

        except Exception as e:
            logger.debug(f"Error processing position: {e}")

    def _on_mqtt_message(self, mqtt_msg):
        """Handle message received via MQTT subscriber.

        Args:
            mqtt_msg: MQTTMessage from mqtt_subscriber
        """
        try:
            # Update status
            self._status.messages_received += 1
            self._status.last_message_time = datetime.now()

            # Build message dict matching TCP format
            msg_data = {
                'from_id': mqtt_msg.from_id,
                'to_id': mqtt_msg.to_id if mqtt_msg.to_id not in ('!ffffffff', '^all') else None,
                'content': mqtt_msg.text,
                'channel': mqtt_msg.channel,
                'snr': mqtt_msg.snr,
                'rssi': mqtt_msg.rssi,
                'hops_away': None,  # Not available in MQTT JSON
                'hop_start': mqtt_msg.hop_start,
                'hop_limit': None,
                'timestamp': mqtt_msg.timestamp.isoformat(),
                'is_broadcast': mqtt_msg.to_id in ('!ffffffff', '^all', None),
                'via_mqtt': True,  # Flag that this came from MQTT
            }

            logger.info(
                f"MQTT RX: {mqtt_msg.from_id} -> {mqtt_msg.to_id or 'broadcast'} "
                f"[ch={mqtt_msg.channel}, SNR={mqtt_msg.snr}]: {mqtt_msg.text[:50]}..."
            )

            # Store message if enabled
            if self.store_messages:
                try:
                    from commands import messaging
                    messaging.store_incoming(
                        from_id=mqtt_msg.from_id,
                        content=mqtt_msg.text,
                        network="meshtastic",
                        to_id=msg_data['to_id'],
                        channel=mqtt_msg.channel,
                        snr=mqtt_msg.snr,
                        rssi=mqtt_msg.rssi,
                    )
                except Exception as e:
                    logger.debug(f"Could not store message: {e}")

            # Notify callbacks
            with self._lock:
                for callback in self._callbacks:
                    try:
                        callback(msg_data)
                    except Exception as e:
                        logger.error(f"Callback error: {e}")

        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")


# Singleton instance
_listener: Optional[MessageListener] = None


def get_listener() -> MessageListener:
    """Get or create the global message listener."""
    global _listener
    if _listener is None:
        _listener = MessageListener()
    return _listener


def start_listener(host: str = "localhost", mode: str = MODE_TCP) -> bool:
    """
    Start the global message listener.

    Args:
        host: Meshtastic host for TCP mode
        mode: "tcp" (default) or "mqtt"

    Returns:
        True if started successfully
    """
    global _listener
    listener = get_listener()

    # Check if we need to recreate with different settings
    if listener.host != host or listener.mode != mode:
        listener.stop()
        _listener = MessageListener(host=host, mode=mode)
        listener = _listener

    return listener.start()


def start_mqtt_listener(
    broker: str = "localhost",
    port: int = 1883,
) -> bool:
    """
    Start the global message listener in MQTT mode.

    This is the recommended mode when the TCP port is occupied by another
    component (like rnsd or Gateway Bridge).

    Args:
        broker: MQTT broker hostname (default: localhost)
        port: MQTT broker port (default: 1883)

    Returns:
        True if started successfully
    """
    global _listener
    if _listener:
        _listener.stop()

    _listener = MessageListener(
        mode=MODE_MQTT,
        mqtt_broker=broker,
        mqtt_port=port,
    )
    return _listener.start()


def stop_listener():
    """Stop the global message listener."""
    if _listener:
        _listener.stop()


def get_listener_status() -> dict:
    """Get status of the global listener."""
    if _listener:
        return _listener.get_status().to_dict()
    return {'state': DISCONNECTED, 'error': 'Listener not initialized'}


def diagnose_pubsub() -> dict:
    """
    Diagnose pubsub connection status.

    Returns:
        Dict with diagnostic info
    """
    result = {
        'pubsub_available': False,
        'meshtastic_available': False,
        'subscriptions': [],
        'errors': [],
    }

    # Check pubsub
    try:
        from pubsub import pub
        result['pubsub_available'] = True

        # Get current subscriptions for meshtastic topics
        try:
            # pubsub.core gives access to topic tree
            from pubsub.core import TopicManager
            tm = pub.getDefaultTopicMgr()

            # Check if meshtastic.receive topic exists
            if tm.getTopic('meshtastic.receive', okIfNone=True):
                topic = tm.getTopic('meshtastic.receive')
                listeners = topic.getListeners()
                result['subscriptions'].append({
                    'topic': 'meshtastic.receive',
                    'listener_count': len(listeners),
                })
            else:
                result['subscriptions'].append({
                    'topic': 'meshtastic.receive',
                    'listener_count': 0,
                    'note': 'Topic not created yet'
                })
        except Exception as e:
            result['errors'].append(f"Could not inspect topics: {e}")

    except ImportError as e:
        result['errors'].append(f"pubsub not available: {e}")

    # Check meshtastic
    try:
        import meshtastic
        result['meshtastic_available'] = True
        result['meshtastic_version'] = getattr(meshtastic, '__version__', 'unknown')
    except ImportError as e:
        result['errors'].append(f"meshtastic not available: {e}")

    # Check if meshtasticd is reachable
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock_result = sock.connect_ex(('localhost', 4403))
        sock.close()
        result['meshtasticd_port_open'] = sock_result == 0
    except Exception as e:
        result['meshtasticd_port_open'] = False
        result['errors'].append(f"Port check failed: {e}")

    return result
