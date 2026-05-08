"""Tests for the gateway_flow_audit recipe.

Pure functions only — service / journalctl / ss calls are exercised in
field tests, not unit tests. The point is to lock down the log-parsing
heuristics that drive the dedup-misclassification and ECONNREFUSED
detections, since those are the recipe's actual judgment calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.gateway_diagnostic import CheckStatus
from utils.gateway_flow_audit import (
    check_bridge_counters,
    check_dedup_pattern,
    check_econnrefused,
    parse_bridge_counters,
)


# ---------------------------------------------------------------------------
# Bridge counter parser
# ---------------------------------------------------------------------------


class TestParseBridgeCounters:
    def test_returns_none_on_empty_journal(self):
        assert parse_bridge_counters("") is None

    def test_returns_none_when_no_counter_line(self):
        assert parse_bridge_counters("just\nrandom\nlog lines\n") is None

    def test_extracts_canonical_line(self):
        line = (
            "May 08 07:14:47 host[123]:   "
            "attempted/delivered/dropped — M->R: 18/18/0  R->M: 6/6/0"
        )
        c = parse_bridge_counters(line)
        assert c == {
            "mr_attempted": 18, "mr_delivered": 18, "mr_dropped": 0,
            "rm_attempted": 6,  "rm_delivered": 6,  "rm_dropped": 0,
        }

    def test_returns_most_recent_when_multiple(self):
        journal = (
            "  attempted/delivered/dropped — M->R: 1/1/0  R->M: 0/0/0\n"
            "  attempted/delivered/dropped — M->R: 18/3/15  R->M: 6/6/0\n"
        )
        c = parse_bridge_counters(journal)
        # Most recent (last) line wins.
        assert c["mr_attempted"] == 18
        assert c["mr_dropped"] == 15


# ---------------------------------------------------------------------------
# Bridge counter check (drop-rate severity tiers)
# ---------------------------------------------------------------------------


class TestCheckBridgeCounters:
    def test_no_counter_line_skips(self):
        results, counters = check_bridge_counters("")
        assert counters is None
        assert len(results) == 1
        assert results[0].status == CheckStatus.SKIP

    def test_zero_attempts_skips_per_direction(self):
        line = "  attempted/delivered/dropped — M->R: 0/0/0  R->M: 0/0/0"
        results, _ = check_bridge_counters(line)
        # Both directions get a SKIP because no attempts.
        assert all(r.status == CheckStatus.SKIP for r in results)

    def test_clean_pass(self):
        line = "  attempted/delivered/dropped — M->R: 18/18/0  R->M: 6/6/0"
        results, _ = check_bridge_counters(line)
        assert all(r.status == CheckStatus.PASS for r in results)

    def test_high_drop_rate_fails(self):
        # 83% drop matches the moc anomaly that started this whole investigation.
        line = "  attempted/delivered/dropped — M->R: 6/6/0  R->M: 18/3/15"
        results, _ = check_bridge_counters(line)
        rm = next(r for r in results if r.name == "bridge.rm_counter")
        assert rm.status == CheckStatus.FAIL
        assert "83% drop" in rm.message
        assert rm.fix_hint and "dedup" in rm.fix_hint.lower()


# ---------------------------------------------------------------------------
# Dedup pattern detection
# ---------------------------------------------------------------------------


class TestCheckDedupPattern:
    def test_no_activity_skips(self):
        r = check_dedup_pattern("nothing\nto see here\n")
        assert r.status == CheckStatus.SKIP

    def test_healthy_ratio_passes(self):
        # 1 dedup retry per unique queued is normal LXMF best-effort behavior.
        journal = (
            "Bridge RNS→Mesh (queued): [RNS:abcd] msg1\n"
            "Failed to enqueue RNS→Mesh\n"
            "Bridge RNS→Mesh (queued): [RNS:abcd] msg2\n"
        )
        r = check_dedup_pattern(journal)
        assert r.status == CheckStatus.PASS

    def test_inflated_ratio_warns(self):
        # 5 dedup retries per queued = the moc 18/3/15 pattern.
        journal = (
            "Bridge RNS→Mesh (queued): [RNS:abcd] msg1\n"
            + ("Failed to enqueue RNS→Mesh\n" * 5)
            + "Bridge RNS→Mesh (queued): [RNS:abcd] msg2\n"
            + ("Failed to enqueue RNS→Mesh\n" * 5)
        )
        r = check_dedup_pattern(journal)
        assert r.status == CheckStatus.WARN
        assert "5.0×" in r.message  # the actual inflation factor


# ---------------------------------------------------------------------------
# ECONNREFUSED detection
# ---------------------------------------------------------------------------


class TestCheckEconnrefused:
    def test_no_failures_passes(self):
        r = check_econnrefused("nothing here\n")
        assert r.status == CheckStatus.PASS

    def test_counts_raised_events(self):
        # Real journal text from the field test that produced the docs memo.
        journal = (
            "boundary | WARNING | rpc[meshtasticd.toradio_put[4e959161]] raised after 0.002s\n"
            "boundary | WARNING | rpc[meshtasticd.toradio_put[4e959162]] raised after 0.002s\n"
            "boundary | WARNING | rpc[meshtasticd.toradio_put[4e959163]] raised after 0.002s\n"
            "boundary | DEBUG | rpc[meshtasticd.toradio_put] ok 0.018s\n"
        )
        r = check_econnrefused(journal)
        assert r.status == CheckStatus.WARN
        assert "3 toradio_put" in r.message

    def test_does_not_count_success_lines(self):
        # The success log "rpc[meshtasticd.toradio_put] ok 0.018s" must NOT
        # be matched — that has no packet-id bracket and is the healthy path.
        journal = "boundary | DEBUG | rpc[meshtasticd.toradio_put] ok 0.018s\n" * 10
        r = check_econnrefused(journal)
        assert r.status == CheckStatus.PASS
