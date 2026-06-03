"""
Tests for IdentityBinder — the Theme-A step-2 cross-protocol identity SSOT.

Covers: normalization, the bindable-name predicate, conservative
discover-and-bind semantics, write throttle, max-contacts guard, lazy DB
open, verified-flag semantics, and MagicMock config sanitization.

All tests inject db_path under tmp_path — no test may touch ~/.config.
"""

import sys
import os
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.identity_binding import (
    IdentityBinder,
    DEFAULT_THROTTLE_SEC,
    DEFAULT_MAX_CONTACTS,
)

MESH_A = "!aabb0042"
RNS_A = "ab" * 16
RNS_B = "cd" * 16
MC_A = "aabbccddeeff"


@pytest.fixture
def binder(tmp_path):
    return IdentityBinder(db_path=str(tmp_path / "contacts.db"),
                          throttle_sec=0.0001)


class TestNormalization:

    def test_name_normalization(self):
        assert IdentityBinder._norm_name("  Alice  ") == "alice"
        assert IdentityBinder._norm_name("WH6GXZ-Home") == "wh6gxz-home"
        assert IdentityBinder._norm_name(None) == ""
        assert IdentityBinder._norm_name(123) == ""

    def test_meshtastic_address(self):
        assert IdentityBinder._norm_address("meshtastic", "!AABB0042") == "!aabb0042"
        assert IdentityBinder._norm_address("meshtastic", "aabb0042") is None
        assert IdentityBinder._norm_address("meshtastic", "!xyz") is None

    def test_rns_address(self):
        assert IdentityBinder._norm_address("rns", RNS_A.upper()) == RNS_A
        assert IdentityBinder._norm_address("rns", "ab" * 8) is None
        assert IdentityBinder._norm_address("rns", "zz" * 16) is None

    def test_meshcore_address(self):
        assert IdentityBinder._norm_address("meshcore", MC_A.upper()) == MC_A
        assert IdentityBinder._norm_address("meshcore", "aabb") is None

    def test_unknown_protocol_and_junk(self):
        assert IdentityBinder._norm_address("aredn", "10.1.1.1") is None
        assert IdentityBinder._norm_address("rns", None) is None
        assert IdentityBinder._norm_address("rns", "") is None


class TestBindablePredicate:

    @pytest.mark.parametrize("bad", [
        "", "  ", "ab",                       # empty / too short
        "!aabb0042",                          # id-shaped
        "meshtastic:!aabb0042", "rns:abcd",   # auto-generated proto:addr
        "aabb0042",                           # 8-hex (mesh id without !)
        "aabbccddeeff",                       # 12-hex (meshcore pubkey)
        "ab" * 16,                            # 32-hex (rns hash)
        None, 42,
    ])
    def test_rejects(self, bad):
        assert IdentityBinder._is_bindable_name(bad) is False

    @pytest.mark.parametrize("good", [
        "Alice", "WH6GXZ-Home", "Summit Repeater", "abc",
        "deadbeefcafe1",  # 13 hex chars — not an address length
    ])
    def test_accepts(self, good):
        assert IdentityBinder._is_bindable_name(good) is True


class TestDiscoverAndBind:

    def test_unique_name_match_binds_unverified(self, binder):
        binder.populate("meshtastic", MESH_A, "Alice")
        binder.populate("rns", RNS_A, "Alice")
        contact = binder.show("rns", RNS_A)
        assert contact is not None
        assert contact.addresses.get("meshtastic") == MESH_A
        assert contact.addresses.get("rns") == RNS_A
        rows = {r["protocol"]: r for r in binder.address_rows(contact.id)}
        assert rows["rns"]["verified"] == 0  # auto-bind is unverified

    def test_name_match_case_insensitive(self, binder):
        binder.populate("meshtastic", MESH_A, "Alice")
        binder.populate("rns", RNS_A, "  ALICE ")
        contact = binder.show("rns", RNS_A)
        assert contact.addresses.get("meshtastic") == MESH_A

    def test_ambiguous_name_stays_standalone(self, binder):
        # Two distinct contacts named Alice (different mesh radios).
        table = binder._ensure_table()
        table.add_contact("Alice", {"meshtastic": MESH_A})
        table.add_contact("Alice", {"meshtastic": "!aabb0043"})
        binder._dirty()
        binder.populate("rns", RNS_A, "Alice")
        contact = binder.show("rns", RNS_A)
        assert contact is not None
        assert "meshtastic" not in contact.addresses  # no bind on ambiguity

    def test_candidate_already_has_protocol_stays_standalone(self, binder):
        binder.populate("rns", RNS_A, "Alice")
        binder.populate("rns", RNS_B, "Alice")  # Alice already has an rns
        a = binder.show("rns", RNS_A)
        b = binder.show("rns", RNS_B)
        assert a.id != b.id

    def test_non_bindable_name_stays_standalone(self, binder):
        binder.populate("meshtastic", MESH_A, "!aabb0042")
        binder.populate("rns", RNS_A, "!aabb0042")
        contact = binder.show("rns", RNS_A)
        assert "meshtastic" not in contact.addresses

    def test_existing_address_updates_last_seen_only(self, binder):
        binder.populate("meshtastic", MESH_A, "Alice")
        before = len(binder.list_all())
        binder._seen.clear()  # defeat throttle for the second observation
        binder.populate("meshtastic", MESH_A, "Alice")
        assert len(binder.list_all()) == before


class TestThrottle:

    def test_second_write_within_window_skipped(self, tmp_path, monkeypatch):
        import gateway.identity_binding as ib
        clock = [1000.0]
        monkeypatch.setattr(ib.time, "monotonic", lambda: clock[0])
        b = IdentityBinder(db_path=str(tmp_path / "c.db"), throttle_sec=900)
        b.populate("meshtastic", MESH_A, "Alice")
        table = b._ensure_table()
        spy_calls = []
        orig = table.update_last_seen
        table.update_last_seen = lambda *a: spy_calls.append(a) or orig(*a)
        clock[0] = 1100.0  # inside window
        b.populate("meshtastic", MESH_A, "Alice")
        assert spy_calls == []  # throttled before the table was touched
        clock[0] = 2000.0  # past window
        b.populate("meshtastic", MESH_A, "Alice")
        assert len(spy_calls) == 1

    def test_seen_map_capped(self, tmp_path, monkeypatch):
        import gateway.identity_binding as ib
        monkeypatch.setattr(ib, "_SEEN_CAP", 10)
        b = IdentityBinder(db_path=str(tmp_path / "c.db"), throttle_sec=0.0001)
        for i in range(30):
            b._should_write("rns", f"{i:032x}")
        assert len(b._seen) <= 10


class TestMaxContactsGuard:

    def test_skips_new_contacts_past_cap(self, tmp_path):
        b = IdentityBinder(db_path=str(tmp_path / "c.db"),
                           throttle_sec=0.0001, max_contacts=2)
        b.populate("rns", RNS_A, "Alice")
        b.populate("rns", RNS_B, "Bob")
        b.populate("meshcore", MC_A, "Carol")  # third — guarded
        assert len(b.list_all()) == 2

    def test_existing_addresses_still_update_past_cap(self, tmp_path):
        b = IdentityBinder(db_path=str(tmp_path / "c.db"),
                           throttle_sec=0.0001, max_contacts=1)
        b.populate("rns", RNS_A, "Alice")
        b._seen.clear()
        b.populate("rns", RNS_A, "Alice")  # update path, not creation
        assert len(b.list_all()) == 1


class TestLazyDB:

    def test_construction_creates_no_file(self, tmp_path):
        path = tmp_path / "lazy.db"
        b = IdentityBinder(db_path=str(path))
        assert b._table is None
        assert not path.exists()

    def test_first_use_opens_db(self, tmp_path):
        path = tmp_path / "lazy.db"
        b = IdentityBinder(db_path=str(path), throttle_sec=0.0001)
        b.populate("rns", RNS_A, "Alice")
        assert path.exists()

    def test_resolve_opens_lazily_too(self, tmp_path):
        path = tmp_path / "lazy.db"
        b = IdentityBinder(db_path=str(path))
        assert b.resolve("rns", RNS_A, "meshtastic") is None
        assert path.exists()


class TestVerifiedFlags:

    def test_operator_link_is_verified(self, binder):
        cid = binder.link("Alice", "meshtastic", MESH_A)
        assert cid
        rows = binder.address_rows(cid)
        assert rows[0]["verified"] == 1

    def test_link_extends_existing_contact(self, binder):
        binder.link("Alice", "meshtastic", MESH_A)
        cid = binder.link("alice", "rns", RNS_A)  # name matched, new proto
        contact = binder.show("rns", RNS_A)
        assert contact.addresses.get("meshtastic") == MESH_A
        rows = {r["protocol"]: r for r in binder.address_rows(cid)}
        assert rows["rns"]["verified"] == 1

    def test_link_refuses_taken_address(self, binder):
        binder.link("Alice", "rns", RNS_A)
        assert binder.link("Bob", "rns", RNS_A) is None

    def test_link_refuses_duplicate_protocol_on_contact(self, binder):
        binder.link("Alice", "rns", RNS_A)
        assert binder.link("Alice", "rns", RNS_B) is None

    def test_verify_flips_autobind(self, binder):
        binder.populate("rns", RNS_A, "Alice")
        assert binder.verify("rns", RNS_A) is True
        contact = binder.show("rns", RNS_A)
        rows = binder.address_rows(contact.id)
        assert rows[0]["verified"] == 1

    def test_unlink_removes_address_and_prunes_empty(self, binder):
        binder.link("Alice", "rns", RNS_A)
        assert binder.unlink("rns", RNS_A) is True
        assert binder.show("rns", RNS_A) is None
        assert len(binder.list_all()) == 0  # empty contact pruned

    def test_unlink_keeps_contact_with_other_addresses(self, binder):
        binder.link("Alice", "meshtastic", MESH_A)
        binder.link("Alice", "rns", RNS_A)
        assert binder.unlink("rns", RNS_A) is True
        assert len(binder.list_all()) == 1
        assert binder.show("meshtastic", MESH_A) is not None

    def test_unlink_missing_returns_false(self, binder):
        assert binder.unlink("rns", RNS_A) is False


class TestConfigSanitization:

    def test_magicmock_numerics_fall_back(self, tmp_path):
        b = IdentityBinder(db_path=str(tmp_path / "c.db"),
                           throttle_sec=MagicMock(),
                           max_contacts=MagicMock())
        assert b._throttle_sec == DEFAULT_THROTTLE_SEC
        assert b._max_contacts == DEFAULT_MAX_CONTACTS

    def test_populate_never_raises(self, tmp_path):
        b = IdentityBinder(db_path=str(tmp_path / "nope" / "c.db"))
        # Parent dir missing → table open would fail; populate must swallow.
        b.populate("rns", RNS_A, "Alice")  # no exception
