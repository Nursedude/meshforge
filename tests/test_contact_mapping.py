"""
Tests for cross-protocol contact mapping table (Feature #3).
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def db_path(tmp_path):
    """Temporary database path."""
    return str(tmp_path / "test_contacts.db")


@pytest.fixture
def mapping(db_path):
    """Create a fresh ContactMappingTable."""
    from gateway.contact_mapping import ContactMappingTable
    return ContactMappingTable(db_path=db_path)


class TestContactMappingTable:

    def test_add_contact(self, mapping):
        contact_id = mapping.add_contact("Alice", {
            "meshtastic": "!aabb1234",
            "rns": "deadbeef01234567",
        })
        assert contact_id is not None
        assert len(contact_id) == 36  # UUID format

    def test_lookup_by_meshtastic_address(self, mapping):
        mapping.add_contact("Alice", {
            "meshtastic": "!aabb1234",
            "rns": "deadbeef01234567",
        })

        contact = mapping.lookup_by_address("meshtastic", "!aabb1234")
        assert contact is not None
        assert contact.display_name == "Alice"
        assert contact.addresses["meshtastic"] == "!aabb1234"
        assert contact.addresses["rns"] == "deadbeef01234567"

    def test_lookup_by_rns_address(self, mapping):
        mapping.add_contact("Bob", {
            "rns": "1234567890abcdef",
        })

        contact = mapping.lookup_by_address("rns", "1234567890abcdef")
        assert contact is not None
        assert contact.display_name == "Bob"

    def test_lookup_nonexistent(self, mapping):
        contact = mapping.lookup_by_address("meshtastic", "!nonexist")
        assert contact is None

    def test_resolve_destination(self, mapping):
        mapping.add_contact("Alice", {
            "meshtastic": "!aabb1234",
            "rns": "deadbeef01234567",
            "meshcore": "010203040506",
        })

        # Resolve from Meshtastic to RNS
        rns_addr = mapping.resolve_destination(
            "meshtastic", "!aabb1234", "rns"
        )
        assert rns_addr == "deadbeef01234567"

        # Resolve from RNS to MeshCore
        mc_addr = mapping.resolve_destination(
            "rns", "deadbeef01234567", "meshcore"
        )
        assert mc_addr == "010203040506"

    def test_resolve_no_target_protocol(self, mapping):
        mapping.add_contact("Alice", {
            "meshtastic": "!aabb1234",
        })

        # Alice has no RNS address
        result = mapping.resolve_destination(
            "meshtastic", "!aabb1234", "rns"
        )
        assert result is None

    def test_add_address_to_existing(self, mapping):
        contact_id = mapping.add_contact("Alice", {
            "meshtastic": "!aabb1234",
        })

        success = mapping.add_address(contact_id, "rns", "deadbeef01234567")
        assert success is True

        contact = mapping.lookup_by_address("rns", "deadbeef01234567")
        assert contact is not None
        assert contact.display_name == "Alice"
        assert "meshtastic" in contact.addresses
        assert "rns" in contact.addresses

    def test_add_address_invalid_protocol(self, mapping):
        contact_id = mapping.add_contact("Alice")
        success = mapping.add_address(contact_id, "invalid_proto", "addr")
        assert success is False

    def test_add_address_nonexistent_contact(self, mapping):
        success = mapping.add_address("nonexistent-id", "meshtastic", "!1234")
        assert success is False

    def test_auto_discover_new(self, mapping):
        contact_id = mapping.auto_discover("meshtastic", "!aabb1234", "Alice")
        assert contact_id

        contact = mapping.lookup_by_address("meshtastic", "!aabb1234")
        assert contact is not None
        assert contact.display_name == "Alice"

    def test_auto_discover_existing(self, mapping):
        id1 = mapping.auto_discover("meshtastic", "!aabb1234", "Alice")
        id2 = mapping.auto_discover("meshtastic", "!aabb1234", "Alice Updated")

        # Should return same contact ID (not create duplicate)
        assert id1 == id2

    def test_merge_contacts(self, mapping):
        id_a = mapping.add_contact("Alice (Meshtastic)", {
            "meshtastic": "!aabb1234",
        })
        id_b = mapping.add_contact("Alice (RNS)", {
            "rns": "deadbeef01234567",
        })

        result = mapping.merge_contacts(id_a, id_b)
        assert result == id_a

        # id_a should now have both addresses
        contact = mapping.lookup_by_address("meshtastic", "!aabb1234")
        assert contact is not None
        assert "rns" in contact.addresses
        assert contact.addresses["rns"] == "deadbeef01234567"

        # id_b should be deleted
        contact_b = mapping.lookup_by_address("rns", "deadbeef01234567")
        assert contact_b is not None
        assert contact_b.id == id_a  # Should resolve to merged contact

    def test_delete_contact(self, mapping):
        contact_id = mapping.add_contact("ToDelete", {
            "meshtastic": "!delete",
        })

        assert mapping.delete_contact(contact_id) is True
        assert mapping.lookup_by_address("meshtastic", "!delete") is None

    def test_list_contacts(self, mapping):
        mapping.add_contact("Alice", {"meshtastic": "!aabb1234"})
        mapping.add_contact("Bob", {"rns": "1234567890abcdef"})

        contacts = mapping.list_contacts()
        assert len(contacts) == 2
        names = {c.display_name for c in contacts}
        assert "Alice" in names
        assert "Bob" in names

    def test_get_address(self, mapping):
        contact_id = mapping.add_contact("Alice", {
            "meshtastic": "!aabb1234",
            "rns": "deadbeef01234567",
        })

        assert mapping.get_address(contact_id, "meshtastic") == "!aabb1234"
        assert mapping.get_address(contact_id, "rns") == "deadbeef01234567"
        assert mapping.get_address(contact_id, "meshcore") is None

    def test_unique_address_constraint(self, mapping):
        mapping.add_contact("Alice", {"meshtastic": "!aabb1234"})

        # Same address on different contact should fail silently
        id2 = mapping.add_contact("Bob", {"meshtastic": "!aabb1234"})
        # The address was already taken, so Bob has it OR it was ignored
        bob = mapping.lookup_by_address("meshtastic", "!aabb1234")
        # Should still resolve to Alice (first to claim it)
        assert bob.display_name == "Alice"

    def test_auto_discover_default_name(self, mapping):
        contact_id = mapping.auto_discover("meshtastic", "!aabb1234")
        contact = mapping.lookup_by_address("meshtastic", "!aabb1234")
        assert contact is not None
        # Default name should include protocol:address prefix
        assert "meshtastic:" in contact.display_name
