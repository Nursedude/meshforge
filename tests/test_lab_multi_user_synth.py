"""Tests for src/lab/lxmf_multi_user_synth.py.

Boundary-only — RNS / LXMF are patched so no real transport is ever
brought up. run_synth() is not tested end-to-end here; instead we
cover its decomposed pieces:

- pattern offsets (sustained / burst / ramp)
- ACK dispatch under multi-user (orig, seq) keyspace
- aggregation across pending + early-failure paths
- PairResult derived metrics (mean / p95 / fail %)
- send-path early-failure shape (no-route, send-error)
- sender_loop respects deadline + stop_event
- markdown / json renderers don't blow up on empty + populated reports

The test patches mirror lxmf_echo / lxmf_tracer test patterns: import
from src/lab/ via sys.path prepend (matches conftest.py's shape).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------- pattern offsets


class TestComputePatternOffsets:
    def test_sustained_staggers_across_interval(self):
        from lab.lxmf_multi_user_synth import compute_pattern_offsets

        offsets = compute_pattern_offsets("sustained", 5, 10.0, 60.0)
        assert offsets == [0.0, 2.0, 4.0, 6.0, 8.0]

    def test_burst_all_zero(self):
        from lab.lxmf_multi_user_synth import compute_pattern_offsets

        offsets = compute_pattern_offsets("burst", 4, 5.0, 60.0)
        assert offsets == [0.0, 0.0, 0.0, 0.0]

    def test_ramp_staggers_across_duration(self):
        from lab.lxmf_multi_user_synth import compute_pattern_offsets

        offsets = compute_pattern_offsets("ramp", 4, 5.0, 60.0)
        assert offsets == [0.0, 15.0, 30.0, 45.0]

    def test_single_user_offset_zero_all_patterns(self):
        from lab.lxmf_multi_user_synth import compute_pattern_offsets

        for pattern in ("sustained", "burst", "ramp"):
            assert compute_pattern_offsets(pattern, 1, 5.0, 60.0) == [0.0]

    def test_zero_users_returns_empty(self):
        from lab.lxmf_multi_user_synth import compute_pattern_offsets

        assert compute_pattern_offsets("sustained", 0, 5.0, 60.0) == []

    def test_unknown_pattern_raises(self):
        from lab.lxmf_multi_user_synth import compute_pattern_offsets

        with pytest.raises(ValueError, match="unknown pattern"):
            compute_pattern_offsets("flood", 5, 5.0, 60.0)


# ------------------------------------------------------ ACK dispatch (n-user)


def _make_pending(user_short, seq, peer="some-peer"):
    from lab.lxmf_multi_user_synth import _PendingPing

    return _PendingPing(
        user_short=user_short, seq=seq, peer_name=peer,
        sent_at_monotonic=time.monotonic(),
    )


class TestDispatchAck:
    def test_routes_ack_by_orig_and_seq(self):
        """Two users firing seq=1 must each get their own ACK back."""
        from lab.lxmf_multi_user_synth import dispatch_ack_to_pending

        p_u0 = _make_pending("synth-u0", 1, peer="moc")
        p_u1 = _make_pending("synth-u1", 1, peer="moc")
        pending = {
            ("synth-u0", 1): p_u0,
            ("synth-u1", 1): p_u1,
        }
        lock = threading.Lock()

        result = dispatch_ack_to_pending(
            "ACK seq=1 orig=synth-u0 recv_at=2026-05-12T03:00:00Z",
            pending, lock,
        )
        assert result is p_u0
        assert result is not p_u1

    def test_unknown_user_returns_none(self):
        from lab.lxmf_multi_user_synth import dispatch_ack_to_pending

        pending = {("synth-u0", 1): _make_pending("synth-u0", 1)}
        result = dispatch_ack_to_pending(
            "ACK seq=1 orig=other-user recv_at=2026-05-12T03:00:00Z",
            pending, threading.Lock(),
        )
        assert result is None

    def test_unknown_seq_returns_none(self):
        from lab.lxmf_multi_user_synth import dispatch_ack_to_pending

        pending = {("synth-u0", 1): _make_pending("synth-u0", 1)}
        result = dispatch_ack_to_pending(
            "ACK seq=99 orig=synth-u0 recv_at=2026-05-12T03:00:00Z",
            pending, threading.Lock(),
        )
        assert result is None

    def test_garbage_body_returns_none(self):
        from lab.lxmf_multi_user_synth import dispatch_ack_to_pending

        pending = {("synth-u0", 1): _make_pending("synth-u0", 1)}
        assert dispatch_ack_to_pending(
            "PING seq=1 from=moc", pending, threading.Lock(),
        ) is None
        assert dispatch_ack_to_pending(
            "", pending, threading.Lock(),
        ) is None


# --------------------------------------------------------------- aggregation


class TestAggregate:
    def test_classifies_ok_vs_timeout(self):
        from lab.lxmf_multi_user_synth import _aggregate

        ok = _make_pending("synth-u0", 1, peer="moc")
        ok.ack_event.set()
        ok.rtt_ms = 250

        timed_out = _make_pending("synth-u0", 2, peer="moc")
        # ack_event NOT set → timeout

        pending = {
            ("synth-u0", 1): ok,
            ("synth-u0", 2): timed_out,
        }
        results = _aggregate(1, [], pending, [])
        assert len(results) == 1
        r = results[0]
        assert r.user_short == "synth-u0"
        assert r.peer_name == "moc"
        assert r.samples == 2
        assert r.ok == 1
        assert r.timeout == 1
        assert r.rtts_ms == [250]

    def test_folds_early_failures_into_same_pair(self):
        """A user that timed out on one PING AND no-routed on another to
        the same peer should aggregate into one PairResult row."""
        from lab.lxmf_multi_user_synth import _aggregate, PairResult

        timed_out = _make_pending("synth-u0", 1, peer="moc")
        pending = {("synth-u0", 1): timed_out}

        early = [
            PairResult(
                user_short="synth-u0", peer_name="moc",
                samples=1, ok=0, timeout=0, no_route=1, send_error=0,
            ),
        ]

        results = _aggregate(1, [], pending, early)
        assert len(results) == 1
        r = results[0]
        assert r.samples == 2
        assert r.timeout == 1
        assert r.no_route == 1
        assert r.ok == 0

    def test_separates_users_and_peers(self):
        from lab.lxmf_multi_user_synth import _aggregate

        u0_moc = _make_pending("synth-u0", 1, peer="moc")
        u0_moc.ack_event.set()
        u0_moc.rtt_ms = 100
        u1_moc = _make_pending("synth-u1", 1, peer="moc")
        u1_moc.ack_event.set()
        u1_moc.rtt_ms = 200
        u0_moc1 = _make_pending("synth-u0", 2, peer="moc1")
        u0_moc1.ack_event.set()
        u0_moc1.rtt_ms = 150

        pending = {
            ("synth-u0", 1): u0_moc,
            ("synth-u1", 1): u1_moc,
            ("synth-u0", 2): u0_moc1,
        }
        results = _aggregate(2, [], pending, [])
        assert len(results) == 3

        pairs = {(r.user_short, r.peer_name): r for r in results}
        assert pairs[("synth-u0", "moc")].rtts_ms == [100]
        assert pairs[("synth-u1", "moc")].rtts_ms == [200]
        assert pairs[("synth-u0", "moc1")].rtts_ms == [150]

    def test_sort_order_by_user_index_then_peer(self):
        """synth-u0 rows precede synth-u10 (numeric, not lexicographic)."""
        from lab.lxmf_multi_user_synth import _aggregate

        pending = {}
        for u in (0, 10, 2):
            p = _make_pending(f"synth-u{u}", 1, peer="moc")
            p.ack_event.set()
            p.rtt_ms = u * 10
            pending[(f"synth-u{u}", 1)] = p

        results = _aggregate(11, [], pending, [])
        names = [r.user_short for r in results]
        assert names == ["synth-u0", "synth-u2", "synth-u10"]


# -------------------------------------------------------- derived metrics


class TestPairResultMetrics:
    def _r(self, rtts):
        from lab.lxmf_multi_user_synth import PairResult

        return PairResult(
            user_short="synth-u0", peer_name="moc",
            samples=len(rtts), ok=len(rtts),
            timeout=0, no_route=0, send_error=0,
            rtts_ms=list(rtts),
        )

    def test_mean_ms_basic(self):
        assert self._r([100, 200, 300]).mean_ms == pytest.approx(200.0)

    def test_p95_nearest_rank(self):
        # 20 samples → p95 = sample at index 18 (0-indexed) after sort
        rtts = list(range(1, 21))  # 1..20
        assert self._r(rtts).p95_ms == 19.0

    def test_fail_pct_with_failures(self):
        from lab.lxmf_multi_user_synth import PairResult

        r = PairResult(
            user_short="u", peer_name="p",
            samples=10, ok=7, timeout=2, no_route=1, send_error=0,
        )
        assert r.fail_pct == pytest.approx(30.0)

    def test_empty_rtts_returns_none(self):
        from lab.lxmf_multi_user_synth import PairResult

        r = PairResult(
            user_short="u", peer_name="p",
            samples=3, ok=0, timeout=3, no_route=0, send_error=0,
        )
        assert r.mean_ms is None
        assert r.p95_ms is None

    def test_zero_samples_zero_fail_pct(self):
        from lab.lxmf_multi_user_synth import PairResult

        r = PairResult(
            user_short="u", peer_name="p",
            samples=0, ok=0, timeout=0, no_route=0, send_error=0,
        )
        assert r.fail_pct == 0.0


# ---------------------------------------------- send-path early-failure shape


@pytest.mark.usefixtures("allow_rns_tx")
class TestSendOnePingEarlyFailure:
    def _common(self):
        from lab.lxmf_multi_user_synth import _VirtualUser

        user = _VirtualUser(index=0, short_name="synth-u0")
        pending = {}
        lock = threading.Lock()
        return user, pending, lock

    def test_no_route_when_recall_returns_none(self):
        from lab.lxmf_multi_user_synth import _send_one_ping_with_router

        user, pending, lock = self._common()
        RNS = MagicMock()
        RNS.Identity.recall.return_value = None
        LXMF = MagicMock()
        router = MagicMock()
        source = MagicMock()

        fail = _send_one_ping_with_router(
            RNS, LXMF, router, source, user,
            "moc", bytes.fromhex("ab" * 16),
            pending, lock,
        )

        assert fail is not None
        assert fail.no_route == 1
        assert fail.samples == 1
        assert fail.ok == 0
        # The send was NOT enqueued, so pending stays empty
        assert pending == {}
        # And we never built an LXMessage
        LXMF.LXMessage.assert_not_called()

    def test_send_error_when_handle_outbound_raises(self):
        from lab.lxmf_multi_user_synth import _send_one_ping_with_router

        user, pending, lock = self._common()
        RNS = MagicMock()
        RNS.Identity.recall.return_value = MagicMock()
        LXMF = MagicMock()
        router = MagicMock()
        router.handle_outbound.side_effect = RuntimeError("rnsd EOF")
        source = MagicMock()

        fail = _send_one_ping_with_router(
            RNS, LXMF, router, source, user,
            "moc", bytes.fromhex("ab" * 16),
            pending, lock,
        )

        assert fail is not None
        assert fail.send_error == 1
        assert fail.samples == 1
        # Pending dict was reverted after the failure
        assert pending == {}

    def test_seq_advances_even_on_failure(self):
        """A no-route doesn't burn a seq slot we'll never use again —
        seq must be monotonic per-user."""
        from lab.lxmf_multi_user_synth import _send_one_ping_with_router

        user, pending, lock = self._common()
        RNS = MagicMock()
        RNS.Identity.recall.return_value = None
        LXMF = MagicMock()
        router = MagicMock()
        source = MagicMock()

        assert user.next_seq == 1
        _send_one_ping_with_router(
            RNS, LXMF, router, source, user,
            "moc", bytes.fromhex("ab" * 16),
            pending, lock,
        )
        # seq was consumed
        assert user.next_seq == 2

    def test_success_enqueues_pending_and_returns_none(self):
        from lab.lxmf_multi_user_synth import _send_one_ping_with_router

        user, pending, lock = self._common()
        RNS = MagicMock()
        RNS.Identity.recall.return_value = MagicMock()
        LXMF = MagicMock()
        router = MagicMock()
        source = MagicMock()

        fail = _send_one_ping_with_router(
            RNS, LXMF, router, source, user,
            "moc", bytes.fromhex("ab" * 16),
            pending, lock,
        )

        assert fail is None
        assert len(pending) == 1
        key = ("synth-u0", 1)
        assert key in pending
        assert pending[key].peer_name == "moc"


# --------------------------------------------------- sender loop boundaries


class TestSenderLoop:
    def test_stops_at_deadline(self):
        """Sender must exit when deadline passes — bounds run time."""
        from lab.lxmf_multi_user_synth import _sender_loop, _VirtualUser, Peer

        user = _VirtualUser(index=0, short_name="synth-u0")
        peers = [Peer(name="moc", hash_hex="aa" * 16)]
        pending = {}
        pending_lock = threading.Lock()
        early = []
        early_lock = threading.Lock()
        stop_event = threading.Event()

        RNS = MagicMock()
        RNS.Identity.recall.return_value = MagicMock()
        LXMF = MagicMock()
        router = MagicMock()
        source = MagicMock()

        # Deadline in the past → loop exits before first send.
        deadline = time.monotonic() - 1.0

        start = time.monotonic()
        _sender_loop(
            RNS, LXMF, router, source, user, peers, "sustained",
            interval_s=5.0, user_start_offset_s=0.0,
            deadline_monotonic=deadline,
            pending=pending, pending_lock=pending_lock,
            early_failures=early, early_failures_lock=early_lock,
            stop_event=stop_event,
        )
        elapsed = time.monotonic() - start

        # Sub-second — never entered the send loop.
        assert elapsed < 1.0
        # Nothing was sent.
        assert len(pending) == 0

    def test_stops_on_event_set(self):
        """Setting stop_event mid-run aborts cleanly without hanging."""
        from lab.lxmf_multi_user_synth import _sender_loop, _VirtualUser, Peer

        user = _VirtualUser(index=0, short_name="synth-u0")
        peers = [Peer(name="moc", hash_hex="aa" * 16)]
        pending = {}
        pending_lock = threading.Lock()
        early = []
        early_lock = threading.Lock()
        stop_event = threading.Event()
        stop_event.set()  # already stopped

        RNS = MagicMock()
        LXMF = MagicMock()
        router = MagicMock()
        source = MagicMock()

        deadline = time.monotonic() + 100.0  # would normally run long

        start = time.monotonic()
        _sender_loop(
            RNS, LXMF, router, source, user, peers, "sustained",
            interval_s=5.0, user_start_offset_s=0.0,
            deadline_monotonic=deadline,
            pending=pending, pending_lock=pending_lock,
            early_failures=early, early_failures_lock=early_lock,
            stop_event=stop_event,
        )
        elapsed = time.monotonic() - start
        assert elapsed < 1.0


# ------------------------------------------------------ user index helpers


class TestUserShortName:
    def test_format(self):
        from lab.lxmf_multi_user_synth import _user_short_name

        assert _user_short_name(0) == "synth-u0"
        assert _user_short_name(9) == "synth-u9"
        assert _user_short_name(42) == "synth-u42"

    def test_create_users_count_and_names(self):
        from lab.lxmf_multi_user_synth import _create_users

        users = _create_users(3)
        assert [u.short_name for u in users] == [
            "synth-u0", "synth-u1", "synth-u2",
        ]
        assert [u.index for u in users] == [0, 1, 2]
        # All start at seq 1
        assert all(u.next_seq == 1 for u in users)


# --------------------------------------------------------------- renderers


def _stub_report(pair_results=None):
    """Build a SynthReport for renderer tests without driving run_synth."""
    from lab.lxmf_multi_user_synth import PairResult, SynthReport

    if pair_results is None:
        pair_results = [
            PairResult(
                user_short="synth-u0", peer_name="moc",
                samples=12, ok=12, timeout=0, no_route=0, send_error=0,
                rtts_ms=[100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210],
            ),
            PairResult(
                user_short="synth-u0", peer_name="moc3",
                samples=12, ok=0, timeout=0, no_route=12, send_error=0,
            ),
        ]

    total = sum(r.samples for r in pair_results)
    ok = sum(r.ok for r in pair_results)
    return SynthReport(
        started_at_iso="2026-05-12T07:00:00Z",
        finished_at_iso="2026-05-12T07:01:30Z",
        pattern="sustained",
        n_users=10, interval_s=5.0, duration_s=60.0,
        ack_timeout_s=30.0,
        pair_results=pair_results,
        total_samples=total, total_ok=ok,
        ok_ratio_threshold=0.95,
        pass_envelope=(total > 0 and ok / total >= 0.95),
    )


class TestRenderers:
    def test_markdown_contains_pass_verdict_and_table(self):
        from lab.lxmf_multi_user_synth import render_markdown, PairResult

        # All-ok report → PASS
        report = _stub_report([
            PairResult(
                user_short="synth-u0", peer_name="moc",
                samples=10, ok=10, timeout=0, no_route=0, send_error=0,
                rtts_ms=[100] * 10,
            ),
        ])
        md = render_markdown(report)
        assert "**PASS**" in md
        assert "synth-u0" in md
        assert "| moc " in md or "| moc|" in md or "moc " in md

    def test_markdown_fail_when_below_threshold(self):
        from lab.lxmf_multi_user_synth import render_markdown, PairResult

        report = _stub_report([
            PairResult(
                user_short="synth-u0", peer_name="moc",
                samples=10, ok=5, timeout=5, no_route=0, send_error=0,
                rtts_ms=[100, 200, 300, 400, 500],
            ),
        ])
        md = render_markdown(report)
        assert "**FAIL**" in md
        # Breakdown column shows the timeout count
        assert "timeout=5" in md

    def test_json_round_trips(self):
        from lab.lxmf_multi_user_synth import render_json

        report = _stub_report()
        payload = json.loads(render_json(report))
        assert payload["pattern"] == "sustained"
        assert payload["n_users"] == 10
        assert "pair_results" in payload
        assert isinstance(payload["pair_results"], list)
        assert payload["pair_results"][0]["user"] == "synth-u0"
        assert payload["total_samples"] == report.total_samples

    def test_json_handles_missing_rtts(self):
        from lab.lxmf_multi_user_synth import render_json, PairResult

        # A peer with zero ACKs → mean_ms / p95_ms must serialize as null
        report = _stub_report([
            PairResult(
                user_short="synth-u0", peer_name="moc3",
                samples=5, ok=0, timeout=0, no_route=5, send_error=0,
            ),
        ])
        payload = json.loads(render_json(report))
        row = payload["pair_results"][0]
        assert row["mean_ms"] is None
        assert row["p95_ms"] is None
        assert row["no_route"] == 5


# ---------------------------------------------------- validation in run_synth


class TestRunSynthValidation:
    def test_rejects_invalid_pattern(self):
        from lab.lxmf_multi_user_synth import run_synth

        with pytest.raises(ValueError, match="unknown pattern"):
            run_synth(peers=[], pattern="flood")

    def test_rejects_zero_users(self):
        from lab.lxmf_multi_user_synth import run_synth

        with pytest.raises(ValueError, match="n_users must be"):
            run_synth(peers=[], n_users=0)


# -------------------------------------------------------- exclude matching


class TestExcludeMatch:
    """The operator types short aliases on the CLI, but lab_peers
    carries fleet-prefixed full names. Surfaced from the first smoke
    fire 2026-05-12 — bare-equality match silently passed the peer
    through and the verdict reported 80% / FAIL when the real answer
    was 100% / PASS."""

    def test_exact_match(self):
        from lab.lxmf_multi_user_synth import _peer_matches_exclude

        assert _peer_matches_exclude("alpha", {"alpha"})

    def test_case_insensitive(self):
        from lab.lxmf_multi_user_synth import _peer_matches_exclude

        assert _peer_matches_exclude("ALPHA", {"alpha"})

    def test_meshforge_prefix_stripped(self):
        from lab.lxmf_multi_user_synth import _peer_matches_exclude

        # Peer name carries fleet prefix; user types just the suffix.
        assert _peer_matches_exclude("meshforge-alpha", {"alpha"})

    def test_meshanchor_prefix_stripped(self):
        from lab.lxmf_multi_user_synth import _peer_matches_exclude

        assert _peer_matches_exclude("meshanchor-server", {"server"})

    def test_no_match_when_substring_only(self):
        """Don't fall through to substring match — too coarse. A user
        typing --exclude alpha must NOT silently drop alpha1, alpha2."""
        from lab.lxmf_multi_user_synth import _peer_matches_exclude

        assert not _peer_matches_exclude("meshforge-alpha1", {"alpha"})
        assert not _peer_matches_exclude("meshforge-alphax", {"alpha"})

    def test_full_name_still_works_when_user_types_full(self):
        from lab.lxmf_multi_user_synth import _peer_matches_exclude

        assert _peer_matches_exclude(
            "meshforge-alpha", {"meshforge-alpha"},
        )
