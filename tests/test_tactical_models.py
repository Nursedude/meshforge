"""Tests for tactical message models."""

import pytest
from datetime import datetime

from tactical.models import (
    TacticalType, TacticalPriority, EncryptionMode,
    TacticalMessage, SITREP, TaskAssignment, CheckIn,
    ZoneMarking, Resource, Mission, Event, Asset,
    generate_message_id, TEMPLATE_CLASSES,
)


class TestTacticalType:
    """Test TacticalType enum."""

    def test_all_types_have_values(self):
        assert TacticalType.SITREP.value == 1
        assert TacticalType.TASK.value == 2
        assert TacticalType.RESOURCE.value == 3
        assert TacticalType.CHECKIN.value == 4
        assert TacticalType.ZONE.value == 5
        assert TacticalType.MISSION.value == 6
        assert TacticalType.EVENT.value == 7
        assert TacticalType.ASSET.value == 8

    def test_type_from_value(self):
        assert TacticalType(1) == TacticalType.SITREP
        assert TacticalType(4) == TacticalType.CHECKIN


class TestTacticalPriority:
    """Test TacticalPriority enum."""

    def test_priority_values(self):
        assert TacticalPriority.ROUTINE.value == "R"
        assert TacticalPriority.PRIORITY.value == "P"
        assert TacticalPriority.IMMEDIATE.value == "O"
        assert TacticalPriority.FLASH.value == "F"


class TestEncryptionMode:
    """Test EncryptionMode enum."""

    def test_mode_values(self):
        assert EncryptionMode.CLEAR.value == "C"
        assert EncryptionMode.SECURE.value == "S"


class TestGenerateMessageId:
    """Test Crockford Base32 message ID generation."""

    def test_default_length(self):
        msg_id = generate_message_id()
        assert len(msg_id) == 5

    def test_custom_length(self):
        msg_id = generate_message_id(length=8)
        assert len(msg_id) == 8

    def test_valid_characters(self):
        valid = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        for _ in range(100):
            msg_id = generate_message_id()
            assert all(c in valid for c in msg_id)

    def test_uniqueness(self):
        ids = {generate_message_id() for _ in range(100)}
        # With 32^5 ≈ 33M possible IDs, collisions in 100 are negligible
        assert len(ids) >= 99


class TestTacticalMessage:
    """Test TacticalMessage dataclass."""

    def test_default_creation(self):
        msg = TacticalMessage()
        assert len(msg.id) == 5
        assert msg.tactical_type == TacticalType.CHECKIN
        assert msg.priority == TacticalPriority.ROUTINE
        assert msg.encryption_mode == EncryptionMode.CLEAR
        assert isinstance(msg.timestamp, datetime)
        assert msg.content == {}

    def test_full_creation(self):
        msg = TacticalMessage(
            id="TEST1",
            tactical_type=TacticalType.SITREP,
            priority=TacticalPriority.FLASH,
            encryption_mode=EncryptionMode.SECURE,
            sender_id="WH6GXZ",
            content={"situation": "All clear"},
        )
        assert msg.id == "TEST1"
        assert msg.tactical_type == TacticalType.SITREP
        assert msg.priority == TacticalPriority.FLASH
        assert msg.encryption_mode == EncryptionMode.SECURE
        assert msg.sender_id == "WH6GXZ"
        assert msg.content["situation"] == "All clear"

    def test_to_dict_roundtrip(self):
        msg = TacticalMessage(
            id="RT123",
            tactical_type=TacticalType.TASK,
            priority=TacticalPriority.PRIORITY,
            sender_id="KH6ABC",
            content={"description": "Deploy antenna"},
        )
        d = msg.to_dict()
        restored = TacticalMessage.from_dict(d)
        assert restored.id == msg.id
        assert restored.tactical_type == msg.tactical_type
        assert restored.priority == msg.priority
        assert restored.sender_id == msg.sender_id
        assert restored.content == msg.content

    def test_str_representation(self):
        msg = TacticalMessage(
            id="X1234",
            tactical_type=TacticalType.CHECKIN,
            sender_id="WH6GXZ",
        )
        s = str(msg)
        assert "CHECKIN" in s
        assert "WH6GXZ" in s
        assert "[C]" in s


class TestSITREP:
    """Test SITREP template."""

    def test_to_dict(self):
        sitrep = SITREP(
            situation="Fire on north ridge",
            actions_taken="Evacuated camp",
            latitude=21.3,
            longitude=-157.8,
        )
        d = sitrep.to_dict()
        assert d['situation'] == "Fire on north ridge"
        assert d['actions_taken'] == "Evacuated camp"
        assert d['latitude'] == 21.3
        assert 'casualties' not in d  # Empty string omitted

    def test_from_dict(self):
        d = {'situation': 'Road blocked', 'resources_needed': 'Heavy equipment'}
        sitrep = SITREP.from_dict(d)
        assert sitrep.situation == 'Road blocked'
        assert sitrep.resources_needed == 'Heavy equipment'
        assert sitrep.actions_taken == ''


class TestCheckIn:
    """Test CheckIn template."""

    def test_to_dict_minimal(self):
        checkin = CheckIn(callsign="WH6GXZ", status="ok")
        d = checkin.to_dict()
        assert d['callsign'] == "WH6GXZ"
        assert d['status'] == "ok"
        assert d['personnel_count'] == 1
        assert 'latitude' not in d

    def test_to_dict_with_position(self):
        checkin = CheckIn(
            callsign="KH6ABC",
            status="needs_help",
            latitude=21.3069,
            longitude=-157.8583,
        )
        d = checkin.to_dict()
        assert d['latitude'] == 21.3069
        assert d['longitude'] == -157.8583

    def test_from_dict(self):
        d = {'callsign': 'N0CALL', 'status': 'injured', 'personnel_count': 3}
        checkin = CheckIn.from_dict(d)
        assert checkin.callsign == 'N0CALL'
        assert checkin.status == 'injured'
        assert checkin.personnel_count == 3


class TestZoneMarking:
    """Test ZoneMarking template."""

    def test_circle_zone(self):
        zone = ZoneMarking(
            name="Staging Area A",
            zone_type="staging",
            center_lat=21.3,
            center_lon=-157.8,
            radius_m=500.0,
        )
        assert zone.is_circle()
        assert not zone.is_polygon()
        d = zone.to_dict()
        assert d['radius_m'] == 500.0

    def test_polygon_zone(self):
        zone = ZoneMarking(
            name="Exclusion Zone",
            zone_type="exclusion",
            vertices=[(21.3, -157.8), (21.31, -157.79), (21.3, -157.79)],
        )
        assert zone.is_polygon()
        assert not zone.is_circle()

    def test_from_dict(self):
        d = {
            'name': 'Test Zone',
            'zone_type': 'hazard',
            'center_lat': 21.3,
            'center_lon': -157.8,
            'radius_m': 100.0,
        }
        zone = ZoneMarking.from_dict(d)
        assert zone.name == 'Test Zone'
        assert zone.radius_m == 100.0


class TestTaskAssignment:
    """Test TaskAssignment template."""

    def test_to_dict(self):
        task = TaskAssignment(
            description="Set up repeater",
            assignee="WH6GXZ",
            status="assigned",
        )
        d = task.to_dict()
        assert d['description'] == "Set up repeater"
        assert d['assignee'] == "WH6GXZ"
        assert d['status'] == "assigned"


class TestResource:
    """Test Resource template."""

    def test_to_dict(self):
        resource = Resource(
            name="First Aid Kit",
            resource_type="medical",
            quantity=5,
            status="available",
        )
        d = resource.to_dict()
        assert d['name'] == "First Aid Kit"
        assert d['quantity'] == 5


class TestTemplateRegistry:
    """Test TEMPLATE_CLASSES registry."""

    def test_all_types_registered(self):
        for tt in TacticalType:
            assert tt in TEMPLATE_CLASSES

    def test_correct_mapping(self):
        assert TEMPLATE_CLASSES[TacticalType.SITREP] is SITREP
        assert TEMPLATE_CLASSES[TacticalType.CHECKIN] is CheckIn
        assert TEMPLATE_CLASSES[TacticalType.ZONE] is ZoneMarking
