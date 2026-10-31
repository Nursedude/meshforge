"""1.0 gate 8 (resource budget) — the baseline instrument's JUDGMENT.

The capture is a read of /proc + /sys; what can lie is turning two samples
into a per-day rate. Every degraded input must read UNKNOWN, never a number
(honest_failure_modes #1/#2/#6): a reboot resets the block counters, a clock
step forges wall time (so the window is measured on /proc/uptime), and a
short window inflates or hides a daily rate.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import resource_baseline as rb  # noqa: E402

SAMPLE_TEXT = """boot_id=abc-123
uptime_s=1000.50
model=Raspberry Pi Zero 2 W Rev 1.0
memtotal=425000
memavailable=125000
swaptotal=102396
swapfree=51198
root_disk=mmcblk0
sectors_written=2000000
proc=90000 meshtasticd
proc=40000 python3
"""


def _sample(**over):
    s = rb.parse_sample(SAMPLE_TEXT)
    s.update(over)
    return s


def test_parse_sample_reads_every_field():
    s = rb.parse_sample(SAMPLE_TEXT)
    assert s["boot_id"] == "abc-123"
    assert s["uptime_s"] == 1000.5
    assert s["model"] == "Raspberry Pi Zero 2 W Rev 1.0"
    assert s["mem_used_mb"] == round((425000 - 125000) / 1024, 1)
    assert s["swap_used_mb"] == round((102396 - 51198) / 1024, 1)
    assert s["root_disk"] == "mmcblk0"
    assert s["sectors_written"] == 2000000
    assert s["top_rss"][0] == {"rss_mb": round(90000 / 1024, 1), "comm": "meshtasticd"}


def test_parse_sample_missing_counter_is_none_not_zero():
    # a box where the root disk could not be resolved must not read "0 writes"
    s = rb.parse_sample("boot_id=x\nuptime_s=5\nroot_disk=\n")
    assert s["sectors_written"] is None
    assert s["mem_used_mb"] is None


def test_judge_healthy_window_gives_mb_per_day():
    t0 = _sample(uptime_s=1000.0, sectors_written=2_000_000)
    t1 = _sample(uptime_s=1000.0 + 86400, sectors_written=2_000_000 + 2_000_000)
    v = rb.judge_window(t0, t1)
    assert v["status"] == rb.OK
    # 2,000,000 sectors * 512 B = 1024 MB over exactly one day
    assert v["writes_mb_per_day"] == 1024.0
    assert v["window_h"] == 24.0


def test_judge_reboot_inside_window_is_unknown():
    t0 = _sample(boot_id="a")
    t1 = _sample(boot_id="b", uptime_s=999999.0, sectors_written=9_000_000)
    v = rb.judge_window(t0, t1)
    assert v["status"] == rb.UNKNOWN
    assert v["writes_mb_per_day"] is None
    assert "reboot" in v["reason"]


def test_judge_uptime_going_backward_is_unknown():
    v = rb.judge_window(_sample(uptime_s=5000.0), _sample(uptime_s=4000.0))
    assert v["status"] == rb.UNKNOWN
    assert v["writes_mb_per_day"] is None


def test_judge_counter_going_backward_is_unknown():
    t0 = _sample(uptime_s=0.0, sectors_written=10)
    t1 = _sample(uptime_s=86400.0, sectors_written=5)
    assert rb.judge_window(t0, t1)["status"] == rb.UNKNOWN


def test_judge_short_window_is_unknown():
    t0 = _sample(uptime_s=0.0, sectors_written=0)
    t1 = _sample(uptime_s=3600.0, sectors_written=1000)
    v = rb.judge_window(t0, t1)
    assert v["status"] == rb.UNKNOWN
    assert "short" in v["reason"]


def test_judge_missing_counter_is_unknown():
    v = rb.judge_window(_sample(sectors_written=None),
                        _sample(uptime_s=1000.0 + 86400))
    assert v["status"] == rb.UNKNOWN


def test_judge_unreachable_sample_is_unknown():
    v = rb.judge_window({"error": "ssh rc=255"}, _sample())
    assert v["status"] == rb.UNKNOWN
    assert v["writes_mb_per_day"] is None
