"""daemon MQTTSubscriberService against the REAL MQTTNodelessSubscriber.

Stranger drill 41253446297 (2026-10-31, 1.0 gate 7): `launcher.py --daemon
--profile monitor` failed every MQTT start with
`MQTTNodelessSubscriber.__init__() got an unexpected keyword argument
'broker'` — latent since the daemon landed (02-25), because the only test
checked `hasattr` on the service and never built the class it wraps.

These tests construct the real subscriber; only the network leg (`_connect`)
is stubbed, so a constructor/attribute mismatch fails here, not on a box.
Liveness goes through the subscriber's PUBLIC `is_running()` — the bug was
the service reading a private attribute that did not exist.
"""
import os
import sys
from unittest.mock import patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from daemon import MQTTSubscriberService  # noqa: E402
from monitoring.mqtt_subscriber import (  # noqa: E402
    MQTTNodelessSubscriber, create_local_subscriber,
)


@pytest.fixture
def connect_ok():
    # context-managed: the patch can never outlive the test (it used to be
    # started by hand before any try/finally)
    with patch.object(MQTTNodelessSubscriber, "_connect", return_value=True):
        yield


@pytest.fixture
def connect_fails():
    with patch.object(MQTTNodelessSubscriber, "_connect", return_value=False):
        yield


@pytest.fixture
def svc(tmp_path):
    # never the real saved file on the box running the suite
    s = MQTTSubscriberService(broker="localhost", port=1883,
                              saved_dir=tmp_path)
    yield s
    s.stop()


def test_start_builds_the_real_subscriber_for_the_configured_broker(svc, connect_ok):
    assert svc.start() is True
    cfg = svc._subscriber._config
    assert cfg["broker"] == "localhost"
    assert cfg["port"] == 1883
    assert cfg["use_tls"] is False


def test_tls_port_turns_tls_on(connect_ok, tmp_path):
    # the TUI's rule (handlers/mqtt.py): 8883 is the TLS port
    s = MQTTSubscriberService(broker="localhost", port=8883,
                              saved_dir=tmp_path)
    try:
        assert s.start() is True
        assert s._subscriber._config["use_tls"] is True
    finally:
        s.stop()


def test_local_factory_takes_tls_as_a_parameter():
    # TLS is a factory argument, not a write into private state afterwards
    assert create_local_subscriber(use_tls=True)._config["use_tls"] is True
    assert create_local_subscriber()._config["use_tls"] is False


def test_start_reports_the_subscribers_own_failure(svc, connect_fails):
    # a "Started" line must not be printed for a subscriber that did not start
    assert svc.start() is False
    # nothing retries after a raised connect error — so not alive, and
    # the daemon watchdog (which owns the retry) must see it dead
    assert svc.is_alive() is False


def test_start_that_raises_is_not_alive(svc):
    # an exception escaping the subscriber's start() must not leave a
    # half-built subscriber reading alive (the watchdog would never retry)
    with patch.object(MQTTNodelessSubscriber, "start", side_effect=RuntimeError("boom")):
        assert svc.start() is False
    assert svc.is_alive() is False


def test_alive_after_start_and_dead_after_stop(svc, connect_ok):
    # the watchdog restarts what is_alive() calls dead
    assert svc.is_alive() is False
    svc.start()
    assert svc.is_alive() is True
    svc.stop()
    assert svc.is_alive() is False


def test_status_shows_connected_separately_from_alive(svc, connect_ok):
    # alive (started, reconnecting) is not connected — never fold the two
    svc.start()
    st = svc.get_status()
    assert st["alive"] is True
    assert st["connected"] is False  # _connect stubbed: never connected


def test_subscriber_is_running_lifecycle(connect_ok):
    s = create_local_subscriber()
    assert s.is_running() is False          # built, never started
    assert s.start() is True
    assert s.is_running() is True
    s.stop()
    assert s.is_running() is False


def test_subscriber_failed_start_is_not_running(connect_fails):
    s = create_local_subscriber()
    assert s.start() is False
    assert s.is_running() is False
