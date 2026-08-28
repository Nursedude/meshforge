"""quarantine_corrupt_ratchets — the power-loss ratchet-truncation guard.

Born 2026-08-27 (Hurricane Lala recovery): 12 zero-byte ratchet files across
the fleet made register_delivery_identity() raise AFTER the destination was
already registered with Transport, so every reconnect retry failed with
'Attempt to register an already registered destination.' — moc's gateway sat
wedged for 8+ days behind an error naming the wrong cause. The guard
validates ratchet files (the same umsgpack read RNS itself performs) and
quarantines unreadable ones before LXMRouter setup.

Drill discipline: every test PLANTS its violation (a real corrupt file on
disk) rather than mocking the read — the 2026-07-25 lesson that a checker
must consume the real artifact.
"""

import os
import sys
import unittest
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# CI's minimal-deps profile has no RNS; the guard itself no-ops without it
# (guarded import), so without RNS there is nothing real to pin here.
pytest.importorskip("RNS", reason="RNS not installed (CI minimal profile)")
from RNS.vendor import umsgpack  # noqa: E402

from gateway._rns_bridge_connection import quarantine_corrupt_ratchets  # noqa: E402


def _make_storage(tmp: str) -> Path:
    d = Path(tmp) / "lxmf" / "ratchets"
    d.mkdir(parents=True)
    return d


class TestQuarantineCorruptRatchets(unittest.TestCase):

    def test_zero_byte_ratchet_is_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _make_storage(tmp)
            bad = d / ("a" * 32 + ".ratchets")
            bad.write_bytes(b"")  # the exact fleet corpse shape: 0 bytes
            gone = quarantine_corrupt_ratchets(tmp)
            self.assertEqual(gone, [str(bad)])
            self.assertFalse(bad.exists(), "corrupt file must be moved aside")
            corpses = list(d.glob("*.corrupt-*"))
            self.assertEqual(len(corpses), 1, "corpse kept for forensics")

    def test_garbage_bytes_ratchet_is_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _make_storage(tmp)
            bad = d / ("b" * 32 + ".ratchets")
            bad.write_bytes(b"\x00" * 64)  # NUL fill: the ext4 power-loss shape
            gone = quarantine_corrupt_ratchets(tmp)
            self.assertEqual(gone, [str(bad)])
            self.assertFalse(bad.exists())

    def test_valid_ratchet_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _make_storage(tmp)
            good = d / ("c" * 32 + ".ratchets")
            good.write_bytes(umsgpack.packb({"signature": os.urandom(64), "ratchets": umsgpack.packb([os.urandom(32)])}))
            gone = quarantine_corrupt_ratchets(tmp)
            self.assertEqual(gone, [])
            self.assertTrue(good.exists(), "valid ratchet must not be touched")

    def test_mixed_dir_quarantines_only_the_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _make_storage(tmp)
            good = d / ("d" * 32 + ".ratchets")
            good.write_bytes(umsgpack.packb({"signature": os.urandom(64), "ratchets": umsgpack.packb([os.urandom(32)])}))
            bad = d / ("e" * 32 + ".ratchets")
            bad.write_bytes(b"")
            gone = quarantine_corrupt_ratchets(tmp)
            self.assertEqual(gone, [str(bad)])
            self.assertTrue(good.exists())
            self.assertFalse(bad.exists())

    def test_missing_ratchet_dir_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(quarantine_corrupt_ratchets(tmp), [])

    def test_non_ratchet_files_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _make_storage(tmp)
            other = d / "notes.txt"
            other.write_bytes(b"not a ratchet")
            self.assertEqual(quarantine_corrupt_ratchets(tmp), [])
            self.assertTrue(other.exists())

    def test_accepts_path_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _make_storage(tmp)
            bad = d / ("f" * 32 + ".ratchets")
            bad.write_bytes(b"")
            gone = quarantine_corrupt_ratchets(Path(tmp))
            self.assertEqual(gone, [str(bad)])


if __name__ == "__main__":
    unittest.main()
