"""
Tests for scripts/gateway_sessions.py — the session-layer operator CLI.

Drives main() directly with --db pointing at tmp_path (no subprocess, no
~/.config writes; --db also disables the best-effort contact-name lookup).
"""

import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

_SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'scripts',
                       'gateway_sessions.py')
_spec = importlib.util.spec_from_file_location("gateway_sessions", _SCRIPT)
gateway_sessions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gateway_sessions)

from gateway.session_store import SessionStore  # noqa: E402

MESH_A = "!aabb0042"
MESH_B = "!aabb0043"
PEER_A = "ab" * 16
PEER_B = "cd" * 16


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "sessions.db")


def run(db, *argv):
    return gateway_sessions.main(["--db", db, *argv])


class TestSessionsCli:

    def test_empty_list(self, db, capsys):
        assert run(db, "list") == 0
        assert "no active sessions" in capsys.readouterr().out

    def test_list_after_seed(self, db, capsys):
        SessionStore(db_path=db).touch(MESH_A, PEER_A)
        assert run(db, "list") == 0
        out = capsys.readouterr().out
        assert MESH_A in out
        assert PEER_A[:8] in out
        assert "1 active session(s)" in out

    def test_expire_all(self, db, capsys):
        s = SessionStore(db_path=db)
        s.touch(MESH_A, PEER_A)
        s.touch(MESH_B, PEER_B)
        assert run(db, "expire") == 0
        assert "expired 2 session(s)" in capsys.readouterr().out
        run(db, "list")
        assert "no active sessions" in capsys.readouterr().out

    def test_expire_one_mesh(self, db, capsys):
        s = SessionStore(db_path=db)
        s.touch(MESH_A, PEER_A)
        s.touch(MESH_B, PEER_B)
        assert run(db, "expire", MESH_A) == 0
        run(db, "list")
        out = capsys.readouterr().out
        assert MESH_B in out
        assert MESH_A + " " not in out

    def test_expire_missing_ok(self, db, capsys):
        assert run(db, "expire", "!00000000") == 0
        assert "expired 0 session(s)" in capsys.readouterr().out
