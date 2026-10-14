"""BIC-15 F1: an unreadable LXMF identity is REFUSED, never silently replaced.

An identity file IS the box's address on the mesh. A power-loss zero-byte
truncation (13 corpses fleet-wide 2026-08-27) must not turn into a new
address with the old key overwritten: operators would message a dead hash
and nothing in-app would say why. Wanted: refuse, keep the corpse
byte-for-byte, leave a witness (status field + counter + ERROR log).
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fake_rns_identity import KEY_BYTES, make_identity_class  # noqa: E402
from gateway.identity_store import IdentityUnreadable, load_or_create_identity  # noqa: E402
from gateway.meshtastic_broadcast_bridge import (  # noqa: E402
    MeshtasticBroadcastBridge,
    MeshtasticBroadcastConfig,
)
from gateway.operator_line import OperatorLine  # noqa: E402
from gateway.operator_line_config import OperatorLineConfig  # noqa: E402

OP = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _allow_rns_tx_in_this_file():
    """Mocked routers only; the fail-closed RNS egress guard needs the intent declared."""
    from utils import tx_guard
    with tx_guard.allow_rns_egress():
        yield


def _rns():
    rns = MagicMock(name="RNS")
    rns.Identity = make_identity_class()
    return rns


def _corpse(path: Path, data: bytes) -> tuple:
    path.write_bytes(data)
    st = path.stat()
    return data, st.st_ino, st.st_mtime_ns


def _untouched(path: Path, before: tuple) -> bool:
    data, ino, mtime = before
    st = path.stat()
    return path.read_bytes() == data and st.st_ino == ino and st.st_mtime_ns == mtime


CORPSES = {
    "zero_byte": b"",
    "truncated_half_key": b"\x01" * 32,
    "too_long": b"\x02" * (KEY_BYTES + 1),
}


# ── the helper ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(CORPSES))
def test_unreadable_identity_is_refused_and_corpse_kept(tmp_path, name):
    path = tmp_path / "identity"
    before = _corpse(path, CORPSES[name])
    with pytest.raises(IdentityUnreadable) as exc:
        load_or_create_identity(_rns(), path, label="operator line")
    assert _untouched(path, before)
    msg = str(exc.value)
    assert str(path) in msg and "NEW address" in msg      # says what to do
    assert sorted(p.name for p in tmp_path.iterdir()) == ["identity"]  # no temp left


def test_from_file_raising_is_also_refused(tmp_path):
    path = tmp_path / "identity"
    before = _corpse(path, b"\x03" * KEY_BYTES)
    rns = _rns()
    rns.Identity.from_file = classmethod(lambda cls, p: (_ for _ in ()).throw(OSError("EIO")))
    with pytest.raises(IdentityUnreadable):
        load_or_create_identity(rns, path, label="x")
    assert _untouched(path, before)


def test_dangling_symlink_is_refused_not_replaced(tmp_path):
    path = tmp_path / "identity"
    path.symlink_to(tmp_path / "gone")
    with pytest.raises(IdentityUnreadable):
        load_or_create_identity(_rns(), path, label="x")
    assert path.is_symlink() and not (tmp_path / "gone").exists()


def test_readable_identity_loads_unchanged(tmp_path):
    rns = _rns()
    path = tmp_path / "identity"
    first = load_or_create_identity(rns, path, label="x")
    again = load_or_create_identity(rns, path, label="x")
    assert again.hash == first.hash


def test_absent_identity_is_created_private_whole_and_verified(tmp_path):
    rns = _rns()
    path = tmp_path / "sub" / "identity"
    ident = load_or_create_identity(rns, path, label="x")
    assert path.read_bytes() == ident.get_private_key()
    assert len(path.read_bytes()) == KEY_BYTES
    assert stat.S_IMODE(path.stat().st_mode) == 0o600      # it is a private key
    assert sorted(p.name for p in path.parent.iterdir()) == ["identity"]


def test_create_that_does_not_read_back_is_refused(tmp_path):
    """Measure the write, don't assert it: a key that does not round-trip
    is refused before anything registers it."""
    rns = _rns()
    rns.Identity.from_file = classmethod(lambda cls, p: None)
    with pytest.raises(IdentityUnreadable):
        load_or_create_identity(rns, tmp_path / "identity", label="x")


def test_real_rns_zero_byte_identity_is_refused(tmp_path):
    """The library itself, not a fake: what from_file really returns."""
    RNS = pytest.importorskip("RNS")
    if not isinstance(getattr(RNS.Identity, "KEYSIZE", None), int):
        pytest.skip("RNS is stubbed in this environment")
    path = tmp_path / "identity"
    before = _corpse(path, b"")
    with pytest.raises(IdentityUnreadable):
        load_or_create_identity(RNS, path, label="x")
    assert _untouched(path, before)
    good = tmp_path / "good"
    ident = load_or_create_identity(RNS, good, label="x")
    assert RNS.Identity.from_file(str(good)).hash == ident.hash


# ── the consumers: operator line + broadcast bridge ───────────────────────

def _fake_lxmf():
    lxmf = MagicMock(name="LXMF")
    router = MagicMock(name="LXMRouter")
    src = MagicMock()
    src.hash = bytes.fromhex(OP)
    router.register_delivery_identity.return_value = src
    lxmf.LXMRouter.return_value = router
    return lxmf, router


def test_operator_line_refuses_to_start_on_unreadable_identity(tmp_path):
    lxmf, router = _fake_lxmf()
    path = tmp_path / "id"
    before = _corpse(path, b"")
    cfg = OperatorLineConfig.from_dict({"enabled": True, "operators": [OP],
                                        "announce_interval_sec": 0,
                                        "alerts_enabled": False})
    line = OperatorLine(cfg, snapshot_fn=lambda: None, rns_module=_rns(),
                        lxmf_module=lxmf, identity_path=path,
                        storage_path=tmp_path / "store")
    assert line.start() is False
    assert line.is_running is False
    router.register_delivery_identity.assert_not_called()
    assert _untouched(path, before)
    status = line.get_status()
    assert status["identity_error"] and str(path) in status["identity_error"]
    assert status["stats"]["identity_refused"] == 1
    # a reconnect retry refuses again — it never "heals" by recreating
    assert line.start() is False
    assert _untouched(path, before)
    assert line.get_status()["stats"]["identity_refused"] == 2


def test_operator_line_creates_identity_when_absent(tmp_path):
    lxmf, router = _fake_lxmf()
    path = tmp_path / "id"
    cfg = OperatorLineConfig.from_dict({"enabled": True, "operators": [OP],
                                        "announce_interval_sec": 0,
                                        "alerts_enabled": False})
    line = OperatorLine(cfg, snapshot_fn=lambda: None, rns_module=_rns(),
                        lxmf_module=lxmf, identity_path=path,
                        storage_path=tmp_path / "store")
    try:
        assert line.start() is True
        assert len(path.read_bytes()) == KEY_BYTES
        assert line.get_status()["identity_error"] is None
    finally:
        line.stop()


def test_broadcast_bridge_refuses_to_start_on_unreadable_identity(tmp_path):
    lxmf, router = _fake_lxmf()
    path = tmp_path / "identity"
    before = _corpse(path, b"")
    cfg = MeshtasticBroadcastConfig(enabled=True, channels=[0],
                                    display_name="t", announce_interval_sec=0)
    b = MeshtasticBroadcastBridge(broadcast_config=cfg, rns_module=_rns(),
                                  lxmf_module=lxmf, identity_path=path,
                                  db_path=tmp_path / "subs.db",
                                  storage_path=tmp_path / "storage")
    assert b.start() is False
    router.register_delivery_identity.assert_not_called()
    assert _untouched(path, before)
    status = b.get_status()
    assert status["identity_error"] and str(path) in status["identity_error"]
    assert status["stats"]["identity_refused"] == 1


# ── the gateway's own identity (_rns_bridge_connection._setup_lxmf) ───────

def test_gateway_setup_lxmf_refuses_unreadable_identity(tmp_path):
    from unittest.mock import patch
    from gateway._rns_bridge_connection import RNSConnectionMixin

    class _Host(RNSConnectionMixin):
        def __init__(self):
            self._lxmf_router = None
            self._lxmf_source = None
            self._identity = None
            self._connected_rns = False
            self.config = MagicMock()
            self.config.rns.gateway_name = "TestGW"
            self.config.rns.propagation_node = ""

        def _notify_status(self, event):
            pass

        def _on_lxmf_receive(self, message):
            pass

        def _on_rns_announce(self, *a):
            pass

    path = tmp_path / ".config" / "meshforge" / "gateway_identity"
    path.parent.mkdir(parents=True)
    before = _corpse(path, b"")
    lxmf, router = _fake_lxmf()
    host = _Host()
    with patch("gateway._rns_bridge_connection.get_real_user_home", return_value=tmp_path), \
         patch("gateway._rns_bridge_connection.quarantine_corrupt_ratchets"):
        with pytest.raises(IdentityUnreadable):
            host._setup_lxmf(_rns(), lxmf)
    router.register_delivery_identity.assert_not_called()
    assert host._identity is None and host._connected_rns is False
    assert _untouched(path, before)
    assert str(path) in host._lxmf_identity_error           # the witness
