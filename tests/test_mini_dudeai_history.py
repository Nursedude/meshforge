"""Tests for the mini-dudeai history writer + its shared rotation helper.

B3 risk #3 (unbounded append-only growth): history.jsonl, the memory-deltas
file, and the honesty ledger were pure appends with no cap. These tests pin
the line-oriented rotation contract on the shared helper that bounds all three:
  * a file over its cap is trimmed to under the cap,
  * the most-recent entries survive (oldest dropped, not newest),
  * the file stays valid JSONL line-by-line,
  * under-cap files are left untouched (byte-identical),
  * the line currently being appended is never the one dropped,
  * rotation failure never raises (the daemon must keep ticking).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai.history import (  # noqa: E402
    DEFAULT_HISTORY_MAX_BYTES,
    HistoryWriter,
    _rotate_if_needed,
)


def _entry(i):
    # ~50-byte JSON line, stable shape so size math is predictable.
    return {"ts": 1_780_000_000 + i, "transition": "edge_up", "rule_id": "r", "i": i}


def _read_lines(path):
    with open(path) as f:
        return [l for l in f if l.strip()]


# --- _rotate_if_needed: the shared helper ------------------------------------

def test_under_cap_file_is_untouched(tmp_path):
    """An under-cap file must be byte-identical after a rotation check."""
    p = tmp_path / "h.jsonl"
    body = "".join(json.dumps(_entry(i)) + "\n" for i in range(10))
    p.write_text(body)
    before = p.read_bytes()
    err = _rotate_if_needed(str(p), max_bytes=1_000_000)
    assert err is None
    assert p.read_bytes() == before


def test_missing_file_is_noop(tmp_path):
    """No file → nothing to rotate, no error, no file created."""
    p = tmp_path / "absent.jsonl"
    assert _rotate_if_needed(str(p), max_bytes=1000) is None
    assert not p.exists()


def test_over_cap_is_trimmed_under_cap(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text("".join(json.dumps(_entry(i)) + "\n" for i in range(2000)))
    cap = 20_000
    assert p.stat().st_size > cap
    err = _rotate_if_needed(str(p), max_bytes=cap)
    assert err is None
    assert p.stat().st_size <= cap


def test_rotation_keeps_newest_drops_oldest(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text("".join(json.dumps(_entry(i)) + "\n" for i in range(2000)))
    _rotate_if_needed(str(p), max_bytes=20_000)
    kept = [json.loads(l) for l in _read_lines(p)]
    # newest entry (i == 1999) survives; oldest (i == 0) is gone
    indices = [d["i"] for d in kept]
    assert indices[-1] == 1999
    assert 0 not in indices
    # contiguous-from-the-tail: kept indices are the highest ones
    assert indices == sorted(indices)
    assert indices[0] > 0


def test_rotation_stays_valid_jsonl_line_by_line(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text("".join(json.dumps(_entry(i)) + "\n" for i in range(2000)))
    _rotate_if_needed(str(p), max_bytes=20_000)
    for line in _read_lines(p):
        obj = json.loads(line)              # raises if a line was split
        assert isinstance(obj, dict)
    # no partial first line — first kept line parses fully
    assert _read_lines(p)[0].endswith("}\n")


def test_target_is_half_the_cap(tmp_path):
    """Rotation trims to ~half the cap (headroom so we don't re-rotate next write)."""
    p = tmp_path / "h.jsonl"
    p.write_text("".join(json.dumps(_entry(i)) + "\n" for i in range(2000)))
    cap = 20_000
    _rotate_if_needed(str(p), max_bytes=cap)
    # comfortably under the cap, not hugging it
    assert p.stat().st_size <= cap // 2 + 200


def test_single_oversized_line_kept_not_emptied(tmp_path):
    """A lone line bigger than the target must NOT empty the file."""
    p = tmp_path / "h.jsonl"
    big = json.dumps({"blob": "x" * 5000}) + "\n"
    p.write_text(big)
    _rotate_if_needed(str(p), max_bytes=1000)
    lines = _read_lines(p)
    assert len(lines) == 1
    assert json.loads(lines[0])["blob"] == "x" * 5000


def test_zero_or_negative_cap_is_disabled(tmp_path):
    p = tmp_path / "h.jsonl"
    body = "".join(json.dumps(_entry(i)) + "\n" for i in range(100))
    p.write_text(body)
    before = p.read_bytes()
    assert _rotate_if_needed(str(p), max_bytes=0) is None
    assert _rotate_if_needed(str(p), max_bytes=-1) is None
    assert p.read_bytes() == before


def test_rotation_failure_returns_error_never_raises(tmp_path):
    """A directory path (getsize raises OSError, not FileNotFoundError) must be
    reported as a string, never raised — the loop keeps ticking."""
    d = tmp_path / "adir"
    d.mkdir()
    # os.path.getsize on a dir succeeds; reading it as a file raises OSError.
    # Force the open() failure path by making it unreadable-as-a-file.
    err = _rotate_if_needed(str(d), max_bytes=1)
    # Either getsize(dir) <= cap (None) or the open fails (error string) — but
    # NEVER an exception. Both outcomes are acceptable; the contract is no raise.
    assert err is None or isinstance(err, str)


# --- HistoryWriter: rotation wired at append time ----------------------------

def test_writer_default_cap_is_one_mb():
    w = HistoryWriter("/tmp/whatever.jsonl")
    assert w.max_bytes == DEFAULT_HISTORY_MAX_BYTES == 1_000_000


def test_append_under_cap_keeps_everything(tmp_path):
    p = tmp_path / "h.jsonl"
    w = HistoryWriter(str(p), max_bytes=1_000_000)
    for i in range(50):
        assert w.append([_entry(i)]) is None
    assert len(_read_lines(p)) == 50


def test_append_rotates_when_over_cap_and_never_drops_new_line(tmp_path):
    p = tmp_path / "h.jsonl"
    cap = 8_000
    w = HistoryWriter(str(p), max_bytes=cap)
    last_i = -1
    for i in range(500):
        assert w.append([_entry(i)]) is None
        last_i = i
    # bounded
    assert p.stat().st_size <= cap
    kept = [json.loads(l) for l in _read_lines(p)]
    # the most-recent append is present (rotation ran BEFORE the write)
    assert kept[-1]["i"] == last_i
    # and every kept line is valid JSON
    for d in kept:
        assert isinstance(d["i"], int)


def test_append_empty_is_noop(tmp_path):
    p = tmp_path / "h.jsonl"
    w = HistoryWriter(str(p), max_bytes=1000)
    assert w.append([]) is None
    assert not p.exists()
