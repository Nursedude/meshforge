"""Tests for the cross-box dedup JOIN (dedup/identity arc STEP 4c-ii).

``compute_fleet_dup_view`` JOINs per-box ``content_id_view`` state into a
fleet view: a fleet DUPLICATE is the same (content_id, recipient) CONFIRMED
by >1 DISTINCT gateway (the live dup-A). PURE + measure-only; the discipline
under test is HONESTY — absent/torn/stale box views are explicit coverage
gaps, never a forged healthy zero, and <2 contributing gateways is
INDETERMINATE, not "0 dups".
"""
from __future__ import annotations

from utils.fleet_dup_view import compute_fleet_dup_view, classify_box_view


NOW = 1_000_000.0


def _view(confirmed, *, tracked_pairs=None, confirmed_pairs=None,
          untracked=0, unattributed=0):
    """Build a 4b-shaped content_id_view. ``confirmed`` is a list of
    (content_id, recipient, count) tuples."""
    cf = [{"content_id": c, "recipient": r, "confirmed": n}
          for (c, r, n) in confirmed]
    return {
        "tracked_events": sum(n for *_x, n in confirmed),
        "untracked_events": untracked,
        "unattributed_events": unattributed,
        "tracked_pairs": tracked_pairs if tracked_pairs is not None else len(cf),
        "confirmed_pairs": confirmed_pairs if confirmed_pairs is not None else len(cf),
        "local_duplicate_pairs": 0,
        "local_duplicate_deliveries": 0,
        "confirmed": cf,
        "confirmed_truncated": False,
    }


def _box(host, confirmed, *, ts=NOW, unconfirmable_sent=0,
         infra_hashes=None, **kw):
    state = {
        "schema": 1,
        "host": host,
        "ts": ts,
        "content_id_view": _view(confirmed, **kw),
        "unconfirmable_sent": unconfirmable_sent,
    }
    # Only stamp infra_hashes when the test supplies it, so the absent case
    # (old-schema box) is exercised distinctly from an empty published list.
    if infra_hashes is not None:
        state["infra_hashes"] = infra_hashes
    return {"host": host, "state": state}


def _err(host, error):
    return {"host": host, "error": error, "state": None}


# ── classify_box_view ────────────────────────────────────────────────

class TestClassifyBoxView:
    def test_covered_fresh(self):
        c = classify_box_view(_box("moc", [("c1", "r1", 1)]),
                              now=NOW, fresh_window_s=600)
        assert c["status"] == "covered"
        assert c["host"] == "moc"
        assert c["tracked_pairs"] == 1

    def test_error_item_uncovered(self):
        c = classify_box_view(_err("moc3", "ssh: timeout"),
                              now=NOW, fresh_window_s=600)
        assert c["status"] == "error"
        assert "ssh" in c["reason"]

    def test_absent_state_uncovered(self):
        c = classify_box_view({"host": "moc3", "state": None},
                              now=NOW, fresh_window_s=600)
        assert c["status"] == "absent"

    def test_stale_ts_uncovered(self):
        c = classify_box_view(_box("moc3", [("c1", "r1", 1)], ts=NOW - 5000),
                              now=NOW, fresh_window_s=600)
        assert c["status"] == "stale"
        assert c["age_s"] >= 5000

    def test_malformed_view_uncovered(self):
        bad = {"host": "x", "state": {"ts": NOW, "content_id_view": "nope"}}
        c = classify_box_view(bad, now=NOW, fresh_window_s=600)
        assert c["status"] == "malformed"

    def test_non_numeric_ts_uncovered(self):
        bad = {"host": "x", "state": {"ts": None,
                                      "content_id_view": _view([])}}
        c = classify_box_view(bad, now=NOW, fresh_window_s=600)
        assert c["status"] in ("malformed", "stale")


# ── compute_fleet_dup_view ───────────────────────────────────────────

class TestComputeFleetDupView:
    def test_empty_is_indeterminate_not_zero(self):
        v = compute_fleet_dup_view([], now=NOW)
        assert v["status"] == "indeterminate"
        assert v["fleet_duplicate_pairs"] == 0
        assert v["covered_hosts"] == []

    def test_single_contributing_box_is_indeterminate(self):
        v = compute_fleet_dup_view([_box("moc", [("c1", "r1", 1)])], now=NOW)
        assert v["status"] == "indeterminate"
        assert "moc" in v["covered_hosts"]
        assert v["fleet_duplicate_pairs"] == 0

    def test_cross_gateway_dup_detected(self):
        # SAME (content_id, recipient) confirmed by moc AND moc3 = dup-A.
        v = compute_fleet_dup_view([
            _box("moc",  [("c1:abc", "6b1a0120", 1)]),
            _box("moc3", [("c1:abc", "6b1a0120", 1)]),
        ], now=NOW)
        assert v["status"] == "ok"
        assert v["fleet_duplicate_pairs"] == 1
        d = v["fleet_duplicates"][0]
        assert d["content_id"] == "c1:abc"
        assert d["recipient"] == "6b1a0120"
        assert d["distinct_hosts"] == 2
        assert sorted(h["host"] for h in d["hosts"]) == ["moc", "moc3"]
        # one extra copy across two gateways
        assert v["fleet_duplicate_deliveries"] == 1

    def test_fanout_to_different_recipients_is_not_a_dup(self):
        # By-design M->R fan-out: same content_id, DIFFERENT recipients.
        v = compute_fleet_dup_view([
            _box("moc",  [("c1", "rA", 1)]),
            _box("moc3", [("c1", "rB", 1)]),
        ], now=NOW)
        assert v["status"] == "ok"
        assert v["fleet_duplicate_pairs"] == 0

    def test_same_pair_one_box_only_is_not_fleet_dup(self):
        # moc delivered a pair (even twice locally); moc3 delivered a
        # different pair. distinct_hosts==1 for each -> no FLEET dup.
        v = compute_fleet_dup_view([
            _box("moc",  [("c1", "r", 2)]),   # local dup, single host
            _box("moc3", [("c2", "r", 1)]),
        ], now=NOW)
        assert v["status"] == "ok"
        assert v["fleet_duplicate_pairs"] == 0

    def test_three_gateways_one_pair(self):
        v = compute_fleet_dup_view([
            _box("moc",  [("c", "r", 1)]),
            _box("moc3", [("c", "r", 1)]),
            _box("ma",   [("c", "r", 1)]),
        ], now=NOW)
        assert v["fleet_duplicate_pairs"] == 1
        d = v["fleet_duplicates"][0]
        assert d["distinct_hosts"] == 3
        assert v["fleet_duplicate_deliveries"] == 2  # two extra copies

    def test_error_box_surfaced_as_coverage_gap(self):
        v = compute_fleet_dup_view([
            _box("moc", [("c", "r", 1)]),
            _err("moc3", "ssh: Connection timed out"),
        ], now=NOW)
        # only 1 contributing -> indeterminate, gap surfaced
        assert v["status"] == "indeterminate"
        gaps = {g["host"]: g for g in v["uncovered"]}
        assert "moc3" in gaps
        assert gaps["moc3"]["status"] == "error"

    def test_stale_box_is_a_gap_not_a_clear(self):
        v = compute_fleet_dup_view([
            _box("moc",  [("c", "r", 1)]),
            _box("moc3", [("c", "r", 1)], ts=NOW - 99999),  # stale
        ], now=NOW)
        # moc3's confirmation is stale -> not counted -> can't see the dup
        assert v["fleet_duplicate_pairs"] == 0
        assert v["status"] == "indeterminate"
        assert any(g["host"] == "moc3" and g["status"] == "stale"
                   for g in v["uncovered"])

    def test_all_boxes_errored_is_indeterminate_not_healthy(self):
        v = compute_fleet_dup_view([
            _err("moc", "down"), _err("moc3", "down"),
        ], now=NOW)
        assert v["status"] == "indeterminate"
        assert v["fleet_duplicate_pairs"] == 0
        assert len(v["uncovered"]) == 2

    def test_rns_unconfirmed_pairs_summed(self):
        # box has 5 tracked pairs, 2 confirmed -> 3 unconfirmed (miss proxy)
        b1 = _box("moc",  [("c", "r", 1)], tracked_pairs=5, confirmed_pairs=2)
        b2 = _box("moc3", [("c", "r", 1)], tracked_pairs=4, confirmed_pairs=1)
        v = compute_fleet_dup_view([b1, b2], now=NOW)
        assert v["rns_unconfirmed_pairs"] == (5 - 2) + (4 - 1)

    def test_unconfirmable_sent_is_its_own_line(self):
        v = compute_fleet_dup_view([
            _box("moc",  [("c", "r", 1)], unconfirmable_sent=10),
            _box("moc3", [("c", "r", 1)], unconfirmable_sent=7),
        ], now=NOW)
        assert v["unconfirmable_sent"] == 17
        # never folded into dup/miss numbers
        assert v["fleet_duplicate_pairs"] == 1
        assert v["rns_unconfirmed_pairs"] == 0

    def test_blind_spot_lines_summed(self):
        v = compute_fleet_dup_view([
            _box("moc",  [("c", "r", 1)], untracked=4, unattributed=1),
            _box("moc3", [("c", "r", 1)], untracked=2, unattributed=3),
        ], now=NOW)
        assert v["untracked_events"] == 6
        assert v["unattributed_events"] == 4

    # ── infra-vs-human recipient classification (STEP 6) ─────────────
    #
    # A dup whose recipient is itself a gateway/peer hash (e.g. MeshAnchor
    # 58cecbd0, listed in peer_gateway_destinations on BOTH moc and moc3)
    # is an infra-to-infra dup: both gateways legitimately relay there, and
    # it is structurally unsuppressable without a coordination substrate.
    # The HONEST detector tags those separately so the probe pages only on
    # HUMAN-facing dups, not the benign infra steady-state.

    INFRA = "58cecbd0ab5e17750c3d45411c913b75"   # MeshAnchor — a peer gateway
    HUMAN = "6b1a0120941444587d7d1dc1bf6d64d7"   # a human NomadNet inbox

    def test_infra_dup_split_out_not_counted_human(self):
        v = compute_fleet_dup_view([
            _box("moc",  [("c1:x", self.INFRA, 1)], infra_hashes=[self.INFRA]),
            _box("moc3", [("c1:x", self.INFRA, 1)], infra_hashes=[self.INFRA]),
        ], now=NOW)
        assert v["status"] == "ok"
        assert v["fleet_duplicate_pairs"] == 1          # still a real dup
        assert v["fleet_infra_duplicate_pairs"] == 1
        assert v["fleet_human_duplicate_pairs"] == 0    # NOT a human dup
        assert v["infra_hashes_known"] >= 1
        assert v["fleet_duplicates"][0]["recipient_kind"] == "infra"

    def test_human_dup_counted(self):
        v = compute_fleet_dup_view([
            _box("moc",  [("c1:y", self.HUMAN, 1)], infra_hashes=[self.INFRA]),
            _box("moc3", [("c1:y", self.HUMAN, 1)], infra_hashes=[self.INFRA]),
        ], now=NOW)
        assert v["fleet_duplicate_pairs"] == 1
        assert v["fleet_human_duplicate_pairs"] == 1
        assert v["fleet_infra_duplicate_pairs"] == 0
        assert v["fleet_human_duplicate_deliveries"] == 1
        assert v["fleet_duplicates"][0]["recipient_kind"] == "human"

    def test_no_infra_hashes_published_treats_all_as_human(self):
        # Old-schema boxes (no infra_hashes) -> can't prove infra -> page-safe
        # (honest_failure_modes #2: absence of classification != benign infra).
        v = compute_fleet_dup_view([
            _box("moc",  [("c1:z", self.INFRA, 1)]),   # no infra_hashes
            _box("moc3", [("c1:z", self.INFRA, 1)]),
        ], now=NOW)
        assert v["infra_hashes_known"] == 0
        assert v["fleet_human_duplicate_pairs"] == 1   # pages — current behavior
        assert v["fleet_infra_duplicate_pairs"] == 0
        assert v["fleet_duplicates"][0]["recipient_kind"] == "human"

    def test_infra_set_unions_across_boxes(self):
        # Only moc published the infra set; the fleet union still classifies.
        v = compute_fleet_dup_view([
            _box("moc",  [("c1:x", self.INFRA, 1)], infra_hashes=[self.INFRA]),
            _box("moc3", [("c1:x", self.INFRA, 1)]),  # didn't publish its set
        ], now=NOW)
        assert v["infra_hashes_known"] >= 1
        assert v["fleet_infra_duplicate_pairs"] == 1
        assert v["fleet_human_duplicate_pairs"] == 0

    def test_recipient_classification_case_insensitive(self):
        v = compute_fleet_dup_view([
            _box("moc",  [("c1:x", self.INFRA.upper(), 1)],
                 infra_hashes=[self.INFRA.lower()]),
            _box("moc3", [("c1:x", self.INFRA.upper(), 1)],
                 infra_hashes=[self.INFRA.lower()]),
        ], now=NOW)
        assert v["fleet_infra_duplicate_pairs"] == 1
        assert v["fleet_human_duplicate_pairs"] == 0

    def test_mixed_human_and_infra_dups_split(self):
        v = compute_fleet_dup_view([
            _box("moc",  [("cH", self.HUMAN, 1), ("cI", self.INFRA, 1)],
                 infra_hashes=[self.INFRA]),
            _box("moc3", [("cH", self.HUMAN, 1), ("cI", self.INFRA, 1)],
                 infra_hashes=[self.INFRA]),
        ], now=NOW)
        assert v["fleet_duplicate_pairs"] == 2
        assert v["fleet_human_duplicate_pairs"] == 1
        assert v["fleet_infra_duplicate_pairs"] == 1
        assert v["fleet_human_duplicate_deliveries"] == 1

    def test_malformed_infra_hashes_ignored_not_crash(self):
        # A non-list / junk infra_hashes is ignored (absence), never crashes.
        b1 = _box("moc",  [("c1:x", self.INFRA, 1)])
        b1["state"]["infra_hashes"] = "not-a-list"
        b2 = _box("moc3", [("c1:x", self.INFRA, 1)], infra_hashes=[None, 123, ""])
        v = compute_fleet_dup_view([b1, b2], now=NOW)
        assert v["infra_hashes_known"] == 0
        assert v["fleet_human_duplicate_pairs"] == 1   # un-provable -> human

    def test_duplicates_sorted_and_capped(self):
        # 3 dup pairs; cap to 2 -> truncation surfaced, widest dup kept.
        boxes = [
            _box("a", [("c1", "r", 1), ("c2", "r", 1), ("c3", "r", 1)]),
            _box("b", [("c1", "r", 1), ("c2", "r", 1), ("c3", "r", 1)]),
            _box("c", [("c1", "r", 1)]),  # c1 has 3 hosts, others 2
        ]
        v = compute_fleet_dup_view(boxes, now=NOW, pair_cap=2)
        assert v["fleet_duplicate_pairs"] == 3
        assert v["fleet_duplicates_truncated"] is True
        assert len(v["fleet_duplicates"]) == 2
        # widest (c1, 3 hosts) sorts first, never truncated away
        assert v["fleet_duplicates"][0]["content_id"] == "c1"
        assert v["fleet_duplicates"][0]["distinct_hosts"] == 3

    def test_empty_recipient_pair_not_a_dup(self):
        # An empty recipient can't be a real (content_id, recipient) pair.
        v = compute_fleet_dup_view([
            _box("moc",  [("c", "", 1)]),
            _box("moc3", [("c", "", 1)]),
        ], now=NOW)
        assert v["fleet_duplicate_pairs"] == 0

    def test_json_serializable(self):
        import json
        v = compute_fleet_dup_view([
            _box("moc", [("c", "r", 1)]), _box("moc3", [("c", "r", 1)]),
        ], now=NOW)
        json.dumps(v)
