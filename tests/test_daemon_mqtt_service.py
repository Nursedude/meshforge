"""daemon MQTTSubscriberService against the REAL MQTTNodelessSubscriber.

Stranger drill 41253446297 (2026-10-31, 1.0 gate 7): `launcher.py --daemon
--profile monitor` failed every MQTT start with
`MQTTNodelessSubscriber.__init__() got an unexpected keyword argument
'broker'` — latent since the daemon landed (02-25), because the only test
checked `hasattr` on the service and never built the class it wraps.

These tests construct the real subscriber; only the network leg (`_connect`)
is stubbed, so a constructor/attribute mismatch fails here, not on a box.
"""
import os
import sys
from unittest.mock import patch

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from daemon import MQTTSubscriberService  # noqa: E402
from monitoring.mqtt_subscriber import MQTTNodelessSubscriber  # noqa: E402


def _svc(port=1883, connect_ok=True):
    svc = MQTTSubscriberService(broker="localhost", port=port)
    p = patch.object(MQTTNodelessSubscriber, "_connect", return_value=connect_ok)
    p.start()
    return svc, p


def test_start_builds_the_real_subscriber_for_the_configured_broker():
    svc, p = _svc()
    try:
        assert svc.start() is True
        cfg = svc._subscriber._config
        assert cfg["broker"] == "localhost"
        assert cfg["port"] == 1883
        assert cfg["use_tls"] is False
    finally:
        svc.stop()
        p.stop()


def test_tls_port_turns_tls_on():
    # the TUI's rule (handlers/mqtt.py): 8883 is the TLS port
    svc, p = _svc(port=8883)
    try:
        assert svc.start() is True
        assert svc._subscriber._config["use_tls"] is True
    finally:
        svc.stop()
        p.stop()


def test_start_reports_the_subscribers_own_failure():
    # a "Started" line must not be printed for a subscriber that did not start
    svc, p = _svc(connect_ok=False)
    try:
        assert svc.start() is False
        # nothing retries after a raised connect error — so not alive, and
        # the daemon watchdog (which owns the retry) must see it dead
        assert svc.is_alive() is False
    finally:
        svc.stop()
        p.stop()


def test_alive_after_start_and_dead_after_stop():
    # the watchdog restarts what is_alive() calls dead — it must read a REAL
    # attribute of the subscriber (it read a nonexistent `_running`)
    svc, p = _svc()
    try:
        assert svc.is_alive() is False
        svc.start()
        assert svc.is_alive() is True
        svc.stop()
        assert svc.is_alive() is False
    finally:
        p.stop()


def test_status_shows_connected_separately_from_alive():
    # alive (started, reconnecting) is not connected — never fold the two
    svc, p = _svc()
    try:
        svc.start()
        st = svc.get_status()
        assert st["alive"] is True
        assert st["connected"] is False  # _connect stubbed: never connected
    finally:
        svc.stop()
        p.stop()
