"""Tests for scripts/link_test_beacon.py — the far end's TX witness.

Nothing here opens an RNS instance or a radio. The beacon's load-bearing logic
is split out so it can be pinned without one: reading the RAW TX counter from
an interface-stats dict, waiting for that counter to settle, and loading the
beacon identity without inheriting a corpse or a loose key.
"""

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

SCRIPT = _ROOT / "scripts" / "link_test_beacon.py"
_spec = importlib.util.spec_from_file_location("link_test_beacon", SCRIPT)
bc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bc)


class FakeReticulum:
    """What ``Reticulum.get_interface_stats()`` returns over the RPC: raw ints."""

    def __init__(self, txb, *, fail=False, txb_as=None):
        self.txb = txb
        self.fail = fail
        self.txb_as = txb_as

    def get_interface_stats(self):
        if self.fail:
            raise OSError("rpc connection refused")
        txb = self.txb if self.txb_as is None else self.txb_as
        return {"interfaces": [
            {"name": "RNodeInterface[Lab RNode RF]", "type": "RNodeInterface",
             "status": True, "rxb": 0, "txb": txb},
            {"name": "TCPInterface[hub]", "type": "TCPInterface",
             "status": True, "rxb": 10 ** 9, "txb": 10 ** 9},
        ]}


class TestRawTxCounter:
    """Finding 5 (2026-09-09): the witness read rnstatus TEXT, which renders
    '%.2f MB' — a 10,000 B quantum. A default 40-announce run (≤8 kB) on an
    interface past 1 MB computed delta 0 and printed 'transmitted NOTHING' for
    a radio that radiated, or a fabricated delta of exactly 10,000 B."""

    def test_a_200_byte_transmission_past_1mb_is_seen(self):
        r = FakeReticulum(1_150_400)
        before, _ = bc.rf_tx_bytes(r, "")
        r.txb = 1_150_600
        after, _ = bc.rf_tx_bytes(r, "")
        assert after - before == 200

    def test_counter_is_an_int_not_a_rendered_float(self):
        total, witness = bc.rf_tx_bytes(FakeReticulum(1_150_400), "")
        assert isinstance(total, int) and total == 1_150_400
        assert "RNodeInterface" in witness and "TCPInterface" not in witness

    def test_match_filters_by_interface_name(self):
        assert bc.rf_tx_bytes(FakeReticulum(5), "lab rnode")[0] == 5
        total, why = bc.rf_tx_bytes(FakeReticulum(5), "no-such-radio")
        assert total is None and "no RNodeInterface matching" in why

    def test_rpc_failure_is_unobservable_not_zero(self):
        total, why = bc.rf_tx_bytes(FakeReticulum(5, fail=True), "")
        assert total is None and "get_interface_stats failed" in why

    def test_a_non_integer_counter_is_unobservable_not_zero(self):
        total, why = bc.rf_tx_bytes(FakeReticulum(0, txb_as="1.15 MB"), "")
        assert total is None and "no integer txb" in why


class TestCounterSettle:
    """Finding 6 (2026-09-09): queued announces leave one per wait_time (up to
    ~24 s at SF8/125k) AFTER announce() returns, so a fixed 2 s settle read
    witnessed at most one frame. Poll until the counter stops moving."""

    @staticmethod
    def _scripted(values):
        seq = list(values)

        def read_fn():
            v = seq.pop(0) if len(seq) > 1 else seq[0]
            return v, "w"
        return read_fn

    def test_waits_through_movement_and_returns_once_quiet(self):
        sleeps = []
        # 100 -> 300 (a frame left) -> 300 x3 quiet
        read_fn = self._scripted([100, 300, 300, 300, 300, 300])
        value, settled, waited = bc.wait_for_tx_to_settle(
            read_fn, poll_s=5.0, quiet_polls=3, max_wait_s=120.0,
            sleep=sleeps.append, log=lambda s: None)
        assert (value, settled) == (300, True)
        assert waited == 20.0 and sleeps == [5.0] * 4

    def test_a_counter_that_never_stops_reports_unsettled_at_the_cap(self):
        counter = iter(range(0, 10_000, 100))
        value, settled, waited = bc.wait_for_tx_to_settle(
            lambda: (next(counter), "w"), poll_s=5.0, quiet_polls=3,
            max_wait_s=30.0, sleep=lambda s: None, log=lambda s: None)
        assert settled is False and waited >= 30.0 and value is not None

    def test_a_counter_that_becomes_unreadable_is_not_settled(self):
        seq = [(100, "w"), (100, "w"), (None, "rpc gone")]
        value, settled, _ = bc.wait_for_tx_to_settle(
            lambda: seq.pop(0), poll_s=1.0, quiet_polls=5, max_wait_s=60.0,
            sleep=lambda s: None, log=lambda s: None)
        assert settled is False and value == 100

    def test_the_quiet_window_outlasts_the_sf8_wait_time(self):
        """~24 s per queued frame at SF8/125k; a shorter window could declare
        'settled' with a frame still queued."""
        assert bc.SETTLE_POLL_S * bc.SETTLE_QUIET_POLLS > 24.0

    def test_frame_estimate_uses_an_announce_sized_unit(self):
        assert 148 + 35 <= bc.ANNOUNCE_BYTES_APPROX <= 180 + 35


class _FakeIdent:
    def __init__(self, payload=b"fake-private-key-bytes"):
        self.payload = payload

    def to_file(self, path):
        with open(path, "wb") as f:
            f.write(self.payload)
        os.chmod(path, 0o644)   # exactly what RNS.Identity.to_file does


class _FakeRNS:
    class Identity(_FakeIdent):
        @staticmethod
        def from_file(path):
            """RNS returns None — not an exception — for an invalid file."""
            try:
                data = Path(path).read_bytes()
            except OSError:
                return None
            return _FakeIdent(data) if data else None


class TestIdentityLoading:
    """Finding 13 (2026-09-09): RNS.Identity.from_file returns None for a
    zero-byte file (the 2026-08-27 power-loss class) and load_identity handed
    that None to RNS.Destination — every future run died with an
    AttributeError naming NoneType, never the corpse. And a pre-existing
    0644 key was never tightened."""

    @pytest.fixture
    def home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bc, "get_real_user_home", lambda: tmp_path)
        return tmp_path / ".config" / "meshforge"

    def test_zero_byte_identity_is_quarantined_and_regenerated(self, home, capsys):
        home.mkdir(parents=True)
        key = home / "link_test_beacon_identity"
        key.write_bytes(b"")
        ident, created = bc.load_identity(rns=_FakeRNS)
        assert ident is not None and created is True
        corpses = list(home.glob("link_test_beacon_identity.corrupt-*"))
        assert len(corpses) == 1, "the corpse must be moved aside, not overwritten"
        assert key.read_bytes() == b"fake-private-key-bytes"
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
        assert "did not load as an identity" in capsys.readouterr().out

    def test_a_loose_existing_key_is_tightened_on_load(self, home):
        home.mkdir(parents=True)
        key = home / "link_test_beacon_identity"
        key.write_bytes(b"existing-key")
        os.chmod(key, 0o644)
        ident, created = bc.load_identity(rns=_FakeRNS)
        assert created is False and ident.payload == b"existing-key"
        assert stat.S_IMODE(key.stat().st_mode) == 0o600

    def test_a_fresh_identity_is_created_0600(self, home):
        ident, created = bc.load_identity(rns=_FakeRNS)
        assert created is True
        assert stat.S_IMODE((home / "link_test_beacon_identity").stat().st_mode) == 0o600
