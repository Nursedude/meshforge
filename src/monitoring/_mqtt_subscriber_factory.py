"""
MQTT Subscriber Factory — Factory and singleton management for MQTTNodelessSubscriber.

Extracted from mqtt_subscriber.py to keep files under 1,500 lines.
Provides convenient factory functions for creating subscribers configured
for local (mosquitto) or public (mqtt.meshtastic.org) brokers.
"""

from typing import Optional


def create_local_subscriber(
    broker: str = "localhost",
    port: int = 1883,
    root_topic: str = "msh/2/e",
    channel: str = "LongFast",
):
    """
    Create an MQTT subscriber configured for a local broker (e.g., mosquitto).

    This is the recommended setup for multi-consumer architecture where
    meshtasticd publishes to a local broker.

    Args:
        broker: Local MQTT broker hostname (default: localhost)
        port: MQTT port (default: 1883, non-TLS)
        root_topic: Meshtastic root topic (default: msh/2/e)
        channel: Meshtastic channel (default: LongFast)

    Returns:
        MQTTNodelessSubscriber configured for local broker

    Example:
        subscriber = create_local_subscriber()
        subscriber.register_message_callback(my_handler)
        subscriber.start()
    """
    from monitoring.mqtt_subscriber import MQTTNodelessSubscriber, DEFAULT_KEY

    config = {
        "broker": broker,
        "port": port,
        "username": "",
        "password": "",
        "root_topic": root_topic,
        "channel": channel,
        "key": DEFAULT_KEY,
        "use_tls": False,  # Local brokers typically don't use TLS
        "regions": ["US"],
        "auto_reconnect": True,
        "reconnect_delay": 2,  # Faster reconnect for local
        "max_reconnect_delay": 30,
    }
    return MQTTNodelessSubscriber(config=config)


def create_public_subscriber(
    region: str = "US",
    channel: str = "LongFast",
):
    """
    Create an MQTT subscriber configured for the public Meshtastic broker.

    This is the "nodeless" mode - observe mesh networks without local hardware.

    Args:
        region: Region code (US, EU_868, etc.)
        channel: Meshtastic channel (default: LongFast)

    Returns:
        MQTTNodelessSubscriber configured for mqtt.meshtastic.org
    """
    from monitoring.mqtt_subscriber import (
        MQTTNodelessSubscriber, DEFAULT_BROKER, DEFAULT_PORT_TLS, DEFAULT_KEY,
    )

    config = {
        "broker": DEFAULT_BROKER,
        "port": DEFAULT_PORT_TLS,
        "username": "",
        "password": "",
        "root_topic": f"msh/{region}/2/e",
        "channel": channel,
        "key": DEFAULT_KEY,
        "use_tls": True,
        "regions": [region],
        "auto_reconnect": True,
        "reconnect_delay": 5,
        "max_reconnect_delay": 60,
    }
    return MQTTNodelessSubscriber(config=config)


# Singleton instance management

_local_subscriber = None


def get_local_subscriber():
    """
    Get or create the global local MQTT subscriber.

    Returns a singleton instance configured for local broker (localhost:1883).
    """
    global _local_subscriber
    if _local_subscriber is None:
        _local_subscriber = create_local_subscriber()
    return _local_subscriber


def start_local_subscriber() -> bool:
    """
    Start the local MQTT subscriber.

    Returns:
        True if started successfully
    """
    subscriber = get_local_subscriber()
    return subscriber.start()


def stop_local_subscriber():
    """Stop the local MQTT subscriber."""
    global _local_subscriber
    if _local_subscriber:
        _local_subscriber.stop()
        _local_subscriber = None
