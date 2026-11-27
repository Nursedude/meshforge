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


# P2 (11-27 Fable 3-gate, PRE_EXISTING): both transforms gated the stamp on
# `isinstance(ts, (int, float))`, off the ONE finite-stamp rule. A spooled
# `NaN` stamp read ok=True age 0 (NaN > X is False both ways -> HEALTHY);
# `10**400` (valid JSON) raised OverflowError out of the cross-box fetch;
# `inf` read stale-not-future. Each is UNOBSERVABLE: not-ok, no raise, and
# the block never echoes a non-finite ts (NaN in /api/status is not JSON).
import json  # noqa: E402
import math  # noqa: E402

import pytest  # noqa: E402

_NON_FINITE = [("nan", float("nan")), ("inf", float("inf")),
               ("-inf", float("-inf")), ("10**400", 10 ** 400), ("true", True)]


@pytest.mark.parametrize("ts", [v for _, v in _NON_FINITE],
                         ids=[i for i, _ in _NON_FINITE])
def test_a_non_finite_watchdog_stamp_is_unobservable(ts):
    b = sb.watchdog_block_from_payload({"ts": ts, "ok": True, "signals": []},
                                       now=time.time())
    assert b["ok"] is False, b
    assert "no_tick_timestamp" in b["reason"], b
    assert b["age_s"] is None and b["ts"] is None, b
    json.dumps(b, allow_nan=False)


@pytest.mark.parametrize("ts", [v for _, v in _NON_FINITE],
                         ids=[i for i, _ in _NON_FINITE])
def test_a_non_finite_mini_stamp_is_unobservable(ts):
    b = sb.mini_block_from_payload({"last_tick_ts": ts, "rules": {}},
                                   now=time.time())
    assert b["ok"] is False, b
    assert "no_tick_timestamp" in b["reason"], b
    assert b["age_s"] is None and b["ts"] is None, b
    json.dumps(b, allow_nan=False)


@pytest.mark.parametrize("now", [float("nan"), float("inf"), 10 ** 400],
                         ids=["nan", "inf", "10**400"])
def test_a_non_finite_reader_clock_is_unobservable(now):
    for fn, key in ((sb.watchdog_block_from_payload, "ts"),
                    (sb.mini_block_from_payload, "last_tick_ts")):
        b = fn({key: time.time(), "ok": True, "rules": {}}, now=now)
        assert b["ok"] is False and b["age_s"] is None, (fn.__name__, b)


def test_a_finite_stamp_still_reads_fresh_and_keeps_its_value():
    now = time.time()
    b = sb.watchdog_block_from_payload({"ts": int(now) - 10, "ok": True}, now=now)
    assert b["ok"] is True and b["ts"] == int(now) - 10 and not math.isnan(b["age_s"])
