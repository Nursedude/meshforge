"""utils.status_blocks — shared with MeshAnchor (parity BYTE_IDENTICAL).
Step-8 non-author pass (2026-10-15, F2): the watchdog transform lacked the
no-timestamp / future-timestamp legs the mini transform had, and now feeds
BOTH centers' cross-box spool view — a frozen, future-stamped watchdog.json
on an RTC-less target read green for the whole skew window."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import status_blocks as sb  # noqa: E402


def test_a_future_stamped_watchdog_is_not_ok():
    b = sb.watchdog_block_from_payload({"ts": time.time() + 30 * 86400, "ok": True})
    assert b["ok"] is False and "future" in b["reason"]


def test_a_watchdog_without_a_timestamp_is_not_ok():
    b = sb.watchdog_block_from_payload({"ok": True, "signals": []})
    assert b["ok"] is False and "no_tick_timestamp" in b["reason"]


def test_only_a_real_true_is_ok():
    now = time.time()
    assert sb.watchdog_block_from_payload({"ts": now, "ok": "false"})["ok"] is False
    assert sb.watchdog_block_from_payload({"ts": now, "ok": True})["ok"] is True
    assert sb.watchdog_block_from_payload({"ts": now})["ok"] is True   # legacy writer: absent ok


def test_ordinary_ntp_jitter_is_still_fresh():
    b = sb.watchdog_block_from_payload({"ts": time.time() + 30, "ok": True})
    assert b["ok"] is True
