"""Tests for the shared total-deadline bounded read (src/utils/_bounded_read.py,
2026-06-26).

The class fix for the slow-trickle source wedge — urlopen's per-socket timeout
doesn't bound a steadily-trickling large body (meshcore took 533 s / held the
federator cold collect at HTTP 503 for ~9 min). bounded_read caps the TOTAL
wall-clock with a monotonic deadline and raises TimeoutError, which every
external map fetcher's `except (URLError, OSError, …)` already catches.
"""

from unittest.mock import MagicMock, patch

import pytest

from utils._bounded_read import bounded_read


def _resp(*chunks):
    """A file-like whose .read(n) yields the given chunks then b'' (EOF)."""
    r = MagicMock()
    r.read.side_effect = list(chunks) + [b""]
    return r


class TestBoundedRead:
    def test_reads_full_body_then_eof(self):
        assert bounded_read(_resp(b"hello ", b"world"), max_seconds=30) == b"hello world"

    def test_empty_body(self):
        r = MagicMock()
        r.read.side_effect = [b""]
        assert bounded_read(r, max_seconds=30) == b""

    def test_rejects_nonpositive_max_seconds(self):
        with pytest.raises(ValueError):
            bounded_read(MagicMock(), max_seconds=0)
        with pytest.raises(ValueError):
            bounded_read(MagicMock(), max_seconds=-1)

    def test_timeouterror_is_oserror_subclass(self):
        # The public collectors rely on `except OSError` catching the deadline,
        # so this subclass relationship is load-bearing (not incidental).
        assert issubclass(TimeoutError, OSError)

    @patch("utils._bounded_read.time.monotonic")
    def test_raises_past_deadline_after_partial_read(self, mono):
        # deadline calc -> 1000 (deadline 1005); loop check 1 -> 1000 (ok, read a
        # chunk); loop check 2 -> 1010 (> 1005 -> abort).
        mono.side_effect = [1000.0, 1000.0, 1010.0]
        r = MagicMock()
        r.read.side_effect = [b"trickle", b"more", b"more"]  # never EOF in window
        with pytest.raises(TimeoutError) as ei:
            bounded_read(r, max_seconds=5)
        assert "deadline" in str(ei.value).lower()
        assert r.read.call_count == 1  # only the pre-deadline chunk was read

    @patch("utils._bounded_read.time.monotonic")
    def test_deadline_before_any_read(self, mono):
        mono.side_effect = [1000.0, 1010.0]  # deadline 1005; first check 1010 -> abort
        r = MagicMock()
        with pytest.raises(TimeoutError):
            bounded_read(r, max_seconds=5)
        assert r.read.call_count == 0  # never read

    def test_cap_bounds_an_unbounded_body(self):
        # A source that never EOFs is stopped by the byte cap (not infinite loop).
        r = MagicMock()
        r.read.return_value = b"x" * 100  # same chunk forever
        out = bounded_read(r, max_seconds=30, cap=150, chunk=100)
        assert len(out) == 200  # stops once total >= cap (overshoots by one chunk)
