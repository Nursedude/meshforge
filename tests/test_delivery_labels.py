"""One transport, one delivery label (review C F7, 2026-09-23).

mesh_bridge's dual-radio lanes ``primary``/``secondary`` were stamped as the
delivery ``protocol``, so Meshtastic counted under three labels. Once
Meshtastic records a CONFIRMED (as ``meshtastic``), the confirmable-only
denominator would forgive the same radio's ``secondary`` failures. Writers
now canonicalize (lane kept in the note); readers merge pre-F7 history.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway.delivery_counters import (  # noqa: E402
    DeliveryCounters, DeliveryState, DropReason)
from monitoring import traffic_pulse as tp  # noqa: E402
from utils.delivery_labels import canonical_protocol, lane_of  # noqa: E402


def _counters(tmp_path):
    return DeliveryCounters(db_path=tmp_path / "delivery_counters.db")


def _legacy(c, rows):
    """Write counter keys exactly as a pre-F7 writer left them."""
    with c._connect() as conn:
        for key, n in rows:
            conn.execute(
                "INSERT INTO counters(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = value + ?", (key, n, n))
        conn.commit()


def test_label_map():
    assert canonical_protocol("secondary") == "meshtastic"
    assert canonical_protocol("primary") == "meshtastic"
    for transport in ("meshtastic", "rns", "mqtt", "meshcore", None, ""):
        assert canonical_protocol(transport) == transport
    assert lane_of("secondary") == "secondary" and lane_of("rns") is None


def test_lane_is_recorded_under_its_transport_with_the_lane_in_the_note(tmp_path):
    c = _counters(tmp_path)
    ev = c.record(DeliveryState.SENT, "m1", protocol="secondary", note="retry=1")
    assert ev.protocol == "meshtastic"
    assert ev.note == "lane=secondary retry=1"
    snap = c.snapshot()
    assert snap["state_by_protocol"]["sent"] == {"meshtastic": 1}
    assert [e["protocol"] for e in snap["recent"]] == ["meshtastic"]


def test_a_confirming_meshtastic_does_not_forgive_its_own_secondary_failures(tmp_path):
    """THE F7 scenario: Meshtastic starts confirming (wantAck) on a
    dual-radio box. Its radio-2 failures are Meshtastic failures."""
    c = _counters(tmp_path)
    for i in range(3):
        c.record(DeliveryState.CONFIRMED, f"ack-{i}", protocol="meshtastic")
    c.record(DeliveryState.DROPPED, "m2", protocol="secondary",
             drop_reason=DropReason.RETRIES_EXHAUSTED)
    snap = c.snapshot()
    assert snap["confirmable_protocols"] == ["meshtastic"]
    assert snap["confirmation_rate"] == 0.75          # 3 / (3 + 1), not 1.0


def test_pre_f7_lane_history_merges_into_the_transport(tmp_path):
    c = _counters(tmp_path)
    _legacy(c, [("state.dropped", 5), ("state_proto.dropped.meshtastic", 2),
                ("state_proto.dropped.secondary", 3),
                ("drop.retries_exhausted", 3),
                ("drop_proto.retries_exhausted.secondary", 3),
                ("state.confirmed", 3), ("state_proto.confirmed.meshtastic", 3)])
    with c._connect() as conn:
        conn.execute("INSERT INTO events(ts, id, state, protocol, drop_reason, note) "
                     "VALUES (1.0, 'old', 'dropped', 'secondary', "
                     "'retries_exhausted', '')")
        conn.commit()
    snap = c.snapshot()
    assert snap["state_by_protocol"]["dropped"] == {"meshtastic": 5}
    assert snap["confirmation_rate"] == 0.5           # 3 / (3 + 3)
    assert {e["protocol"] for e in snap["recent"]} == {"meshtastic"}
    assert {e["protocol"] for e in snap["recent_terminal"]} == {"meshtastic"}
    assert [e.protocol for e in c.recent()] == ["meshtastic"]


def test_db_fallback_reads_the_same_labels_and_rate_as_the_served_snapshot(
        tmp_path, monkeypatch):
    c = _counters(tmp_path)
    _legacy(c, [("state_proto.dropped.secondary", 3),
                ("drop_proto.retries_exhausted.secondary", 3),
                ("drop.retries_exhausted", 3), ("state.dropped", 3)])
    for i in range(3):
        c.record(DeliveryState.CONFIRMED, f"ack-{i}", protocol="meshtastic")
    served = c.snapshot()
    monkeypatch.setattr(tp, "_db_path",
                        lambda n: c._db_path if n == "delivery_counters" else None)
    fallback = tp._parse_delivery_db()
    assert fallback["confirmation_rate"] == served["confirmation_rate"] == 0.5
    assert fallback["state_by_protocol"]["dropped"] == {"meshtastic": 3}
