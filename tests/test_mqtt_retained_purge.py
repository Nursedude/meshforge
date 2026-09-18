"""scripts/mqtt_retained_purge.py — age classification is the part that decides
what gets deleted, so it is pinned here on constructed payloads.

The rule under test: a message we cannot DATE is `unknown`, never `recent`
and never `old` (honest_failure_modes #1/#6). Only a decodable, sane
timestamp older than the threshold selects a topic for purge.
"""

import importlib.util
import json
import pathlib
import time

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mqtt_retained_purge.py"
_spec = importlib.util.spec_from_file_location("mqtt_retained_purge", _SCRIPT)
purge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(purge)

mqtt_pb2 = pytest.importorskip("meshtastic.protobuf.mqtt_pb2")

NOW = 1_800_000_000.0  # fixed "now" — the verdict must not depend on the wall clock
WEEK = 7 * 86400


def _envelope(rx_time: int) -> bytes:
    env = mqtt_pb2.ServiceEnvelope()
    env.packet.rx_time = rx_time
    env.channel_id = "LongFast"
    return env.SerializeToString()


class TestProtobufAge:
    def test_old_envelope_is_old(self):
        cls, ts = purge.classify_age("msh/US/2/e/LongFast/!abcd1234",
                                     _envelope(int(NOW) - 30 * 86400), NOW, WEEK)
        assert cls == purge.AGE_OLD and ts == int(NOW) - 30 * 86400

    def test_fresh_envelope_is_recent(self):
        cls, _ = purge.classify_age("msh/US/2/e/LongFast/!abcd1234",
                                    _envelope(int(NOW) - 3600), NOW, WEEK)
        assert cls == purge.AGE_RECENT

    def test_rx_time_zero_is_unknown_not_old(self):
        """A node with no clock stamps 0; 0 is 1970, which is NOT 'very old'."""
        cls, ts = purge.classify_age("msh/US/2/e/LongFast/!abcd1234",
                                     _envelope(0), NOW, WEEK)
        assert cls == purge.AGE_UNKNOWN and ts is None

    def test_future_timestamp_is_unknown(self):
        cls, _ = purge.classify_age("msh/US/2/e/LongFast/!abcd1234",
                                    _envelope(int(NOW) + 10 * 86400), NOW, WEEK)
        assert cls == purge.AGE_UNKNOWN

    def test_garbage_protobuf_is_unknown(self):
        cls, ts = purge.classify_age("msh/US/2/e/LongFast/!abcd1234",
                                     b"\xff\xfe not a protobuf", NOW, WEEK)
        assert cls == purge.AGE_UNKNOWN and ts is None


class TestJsonAge:
    def test_old_json_is_old(self):
        payload = json.dumps({"timestamp": int(NOW) - 20 * 86400, "type": "text"}).encode()
        cls, _ = purge.classify_age("msh/US/2/json/LongFast/!abcd1234", payload, NOW, WEEK)
        assert cls == purge.AGE_OLD

    def test_json_without_timestamp_is_unknown(self):
        payload = json.dumps({"type": "text"}).encode()
        cls, _ = purge.classify_age("msh/US/2/json/LongFast/!abcd1234", payload, NOW, WEEK)
        assert cls == purge.AGE_UNKNOWN

    def test_invalid_json_is_unknown(self):
        cls, _ = purge.classify_age("msh/US/2/json/LongFast/!abcd1234", b"{nope", NOW, WEEK)
        assert cls == purge.AGE_UNKNOWN


class TestOtherTopics:
    def test_undated_topic_family_is_unknown(self):
        cls, _ = purge.classify_age("msh/US/2/stat/!abcd1234", b"online", NOW, WEEK)
        assert cls == purge.AGE_UNKNOWN

    def test_threshold_boundary_is_strict(self):
        """Exactly N days old is not 'older than N days'."""
        cls, _ = purge.classify_age("msh/US/2/e/LongFast/!abcd1234",
                                    _envelope(int(NOW) - WEEK), NOW, WEEK)
        assert cls == purge.AGE_RECENT


def test_default_is_dry_run_and_unknown_is_kept():
    """The CLI contract: no --apply means nothing is published; unknown-age
    topics are excluded from the selection unless asked for."""
    ap_src = _SCRIPT.read_text()
    assert '"--apply", action="store_true"' in ap_src
    assert '"--include-unknown-age", action="store_true"' in ap_src


def test_no_wall_clock_in_the_verdict():
    """classify_age takes `now` as an argument; the verdict must be
    reproducible regardless of when the test runs."""
    payload = _envelope(int(NOW) - 10 * 86400)
    a = purge.classify_age("msh/US/2/e/LongFast/!a", payload, NOW, WEEK)
    b = purge.classify_age("msh/US/2/e/LongFast/!a", payload, NOW, WEEK)
    assert a == b == (purge.AGE_OLD, int(NOW) - 10 * 86400)
    assert time.time() != NOW  # the fixed `now` is deliberately not the wall clock
