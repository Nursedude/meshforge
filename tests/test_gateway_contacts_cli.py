"""
Tests for scripts/gateway_contacts.py — the identity-SSOT operator CLI.

Drives main() directly with --db pointing at tmp_path (no subprocess, no
~/.config writes).
"""

import importlib.util
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

_SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'scripts',
                       'gateway_contacts.py')
_spec = importlib.util.spec_from_file_location("gateway_contacts", _SCRIPT)
gateway_contacts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gateway_contacts)

MESH_A = "meshtastic:!aabb0042"
RNS_A = "rns:" + "ab" * 16


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "contacts.db")


def run(db, *argv):
    return gateway_contacts.main(["--db", db, *argv])


class TestContactsCli:

    def test_link_show_list_roundtrip(self, db, capsys):
        assert run(db, "link", "Alice", MESH_A) == 0
        assert run(db, "link", "Alice", RNS_A) == 0
        assert run(db, "show", MESH_A) == 0
        out = capsys.readouterr().out
        assert "Alice" in out
        assert "!aabb0042" in out
        assert "ab" * 16 in out
        assert run(db, "list") == 0
        out = capsys.readouterr().out
        assert "1 contact(s)" in out
        assert "[VV]" in out  # both addresses operator-verified

    def test_unlink(self, db, capsys):
        run(db, "link", "Alice", MESH_A)
        assert run(db, "unlink", MESH_A) == 0
        assert run(db, "show", MESH_A) == 1  # gone

    def test_unlink_missing_fails(self, db):
        assert run(db, "unlink", MESH_A) == 1

    def test_verify(self, db, capsys):
        # Seed an unverified entry through the binder (auto-discover path).
        from gateway.identity_binding import IdentityBinder
        IdentityBinder(db_path=db, throttle_sec=0.0001).populate(
            "meshtastic", "!aabb0042", "Alice")
        assert run(db, "verify", MESH_A) == 0
        run(db, "list")
        out = capsys.readouterr().out
        assert "[V]" in out

    def test_malformed_token(self, db, capsys):
        assert run(db, "show", "not-a-token") == 1
        assert "malformed token" in capsys.readouterr().out

    def test_invalid_address_for_protocol(self, db, capsys):
        assert run(db, "link", "Alice", "meshtastic:nothex") == 1
        assert "not a valid meshtastic address" in capsys.readouterr().out

    def test_unknown_protocol(self, db, capsys):
        assert run(db, "link", "Alice", "aredn:10.1.1.1") == 1
        out = capsys.readouterr().out
        assert "not a valid" in out

    def test_link_taken_address_fails(self, db, capsys):
        run(db, "link", "Alice", RNS_A)
        assert run(db, "link", "Bob", RNS_A) == 1

    def test_empty_list(self, db, capsys):
        assert run(db, "list") == 0
        assert "no contacts" in capsys.readouterr().out
