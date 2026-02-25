"""Tests for tactical event timeline."""

import pytest
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from tactical.models import (
    TacticalMessage, TacticalType, TacticalPriority, EncryptionMode,
    CheckIn, ZoneMarking,
)
from tactical.timeline import TacticalTimeline


@pytest.fixture
def timeline(tmp_path):
    """Create a timeline with a temporary database."""
    return TacticalTimeline(db_path=tmp_path / "test_timeline.db")


class TestTimeline:
    """Test TacticalTimeline."""

    def test_record_and_query(self, timeline):
        msg = TacticalMessage(
            id="TL001",
            tactical_type=TacticalType.CHECKIN,
            sender_id="WH6GXZ",
            content={"callsign": "WH6GXZ", "status": "ok"},
        )
        row_id = timeline.record(msg)
        assert row_id > 0

        results = timeline.query()
        assert len(results) == 1
        assert results[0].id == "TL001"
        assert results[0].sender_id == "WH6GXZ"
        assert results[0].content['callsign'] == "WH6GXZ"

    def test_query_by_type(self, timeline):
        timeline.record(TacticalMessage(
            id="T001", tactical_type=TacticalType.CHECKIN,
            content={"callsign": "A"},
        ))
        timeline.record(TacticalMessage(
            id="T002", tactical_type=TacticalType.SITREP,
            content={"situation": "B"},
        ))
        timeline.record(TacticalMessage(
            id="T003", tactical_type=TacticalType.CHECKIN,
            content={"callsign": "C"},
        ))

        checkins = timeline.query(tactical_type=TacticalType.CHECKIN)
        assert len(checkins) == 2

        sitreps = timeline.query(tactical_type=TacticalType.SITREP)
        assert len(sitreps) == 1

    def test_query_by_sender(self, timeline):
        timeline.record(TacticalMessage(
            id="S001", tactical_type=TacticalType.CHECKIN,
            sender_id="WH6GXZ", content={},
        ))
        timeline.record(TacticalMessage(
            id="S002", tactical_type=TacticalType.CHECKIN,
            sender_id="KH6ABC", content={},
        ))

        results = timeline.query(sender_id="WH6GXZ")
        assert len(results) == 1
        assert results[0].sender_id == "WH6GXZ"

    def test_query_since(self, timeline):
        old = TacticalMessage(
            id="OLD1", tactical_type=TacticalType.EVENT,
            timestamp=datetime(2026, 1, 1), content={},
        )
        new = TacticalMessage(
            id="NEW1", tactical_type=TacticalType.EVENT,
            timestamp=datetime(2026, 2, 22), content={},
        )
        timeline.record(old)
        timeline.record(new)

        results = timeline.query(since=datetime(2026, 2, 1))
        assert len(results) == 1
        assert results[0].id == "NEW1"

    def test_query_limit(self, timeline):
        for i in range(10):
            timeline.record(TacticalMessage(
                id=f"LIM{i:02d}", tactical_type=TacticalType.EVENT, content={},
            ))

        results = timeline.query(limit=3)
        assert len(results) == 3

    def test_get_count(self, timeline):
        assert timeline.get_count() == 0

        timeline.record(TacticalMessage(
            id="C001", tactical_type=TacticalType.CHECKIN, content={},
        ))
        timeline.record(TacticalMessage(
            id="C002", tactical_type=TacticalType.SITREP, content={},
        ))

        assert timeline.get_count() == 2
        assert timeline.get_count(TacticalType.CHECKIN) == 1

    def test_get_active_zones(self, timeline):
        zone_content = ZoneMarking(
            name="Test Zone", zone_type="staging",
            center_lat=21.3, center_lon=-157.8, radius_m=100.0,
        ).to_dict()

        timeline.record(TacticalMessage(
            id="Z001", tactical_type=TacticalType.ZONE, content=zone_content,
        ))

        zones = timeline.get_active_zones()
        assert len(zones) == 1
        assert zones[0].name == "Test Zone"
        assert zones[0].radius_m == 100.0

    def test_get_recent_checkins(self, timeline):
        checkin_content = CheckIn(
            callsign="WH6GXZ", status="ok",
            latitude=21.3, longitude=-157.8,
        ).to_dict()

        timeline.record(TacticalMessage(
            id="CI01", tactical_type=TacticalType.CHECKIN,
            content=checkin_content,
        ))

        checkins = timeline.get_recent_checkins(minutes=60)
        assert len(checkins) == 1
        assert checkins[0].callsign == "WH6GXZ"

    def test_purge(self, timeline):
        old = TacticalMessage(
            id="PU01", tactical_type=TacticalType.EVENT,
            timestamp=datetime(2025, 1, 1), content={},
        )
        new = TacticalMessage(
            id="PU02", tactical_type=TacticalType.EVENT,
            content={},  # Uses datetime.now()
        )
        timeline.record(old)
        timeline.record(new)

        assert timeline.get_count() == 2

        deleted = timeline.purge_older_than(days=30)
        assert deleted == 1
        assert timeline.get_count() == 1
