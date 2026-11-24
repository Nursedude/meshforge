"""BIC-2 step 2 — "heard by" for one inbox send, from the fleet's spools.

``inbox_legs.heard_for_send`` reads the center's target list + each box's
fresh spool (``box_truth_center.read_spool``) at the edge and hands the
``rx_heard`` sections to the pure ``inbox_heard.judge_send``; ``heard_line``
renders design D4's words. Pinned (design 2026-11-07, landscape §D step 2):
  * a listed box whose spool is absent/stale reads ``unobservable`` — never
    left out, never "not heard";
  * no target list (a standalone box) → None here; the inbox read says NO_FLEET_LINE;
  * the word "delivered" never appears.
Ambient state is pinned: every test passes its own targets file + spool dir.
"""
import json

import pytest

from utils import inbox_legs

SENT = 1_800_000_000.0
NOW = SENT + 3600
PID = 287851589
FROM = 0x0DAEE001


def _section(entries=()):
    return {"entries": [list(e) for e in entries], "covered_since": SENT - 3600,
            "covered_until": NOW - 60, "last_line_ts": NOW - 70, "read": "incremental"}


@pytest.fixture
def fleet(tmp_path):
    spool = tmp_path / "spool"
    spool.mkdir()
    targets = tmp_path / "targets"

    def make(boxes, listed=None):
        for alias, sec in boxes.items():
            doc = {"schema": "truth_spool/v1", "alias": alias, "fetched_at": NOW - 30}
            if sec is not None:
                doc["rx_heard"] = sec
            (spool / f"{alias}.json").write_text(json.dumps(doc))
        targets.write_text("\n".join(listed if listed is not None else boxes) + "\n")
        return inbox_legs.heard_for_send(PID, SENT, now=NOW, targets_file=targets,
                                         spool_dir=spool)
    return make


def test_the_20_19_send_shape_reads_heard_by_the_boxes_that_decoded_it(fleet):
    out = fleet({"kiai": _section([(FROM, PID, SENT + 1)]),
                 "moc": _section([(FROM, PID, SENT + 1)]),
                 "meshforge-lab": _section()})
    assert out["summary"]["heard"] == ["kiai", "moc"]
    assert out["summary"]["not_heard"] == ["meshforge-lab"]
    assert out["line"] == "heard by kiai, moc · 2 of 3 listening"


def test_a_listed_box_with_no_fresh_spool_is_unobservable_never_dropped(fleet):
    out = fleet({"kiai": _section([(FROM, PID, SENT + 1)])}, listed=["kiai", "moc4"])
    assert out["summary"]["unobservable"] == ["moc4"]
    assert out["line"] == "heard by kiai · 1 of 1 listening · 1 unobservable"


def test_a_spool_without_an_rx_heard_section_is_unobservable(fleet):
    out = fleet({"kiai": None})
    assert out["summary"]["unobservable"] == ["kiai"]
    assert out["line"] == "heard by none · 0 of 0 listening · 1 unobservable"


def test_no_target_list_means_no_fleet_line(tmp_path):
    out = inbox_legs.heard_for_send(PID, SENT, now=NOW, targets_file=tmp_path / "absent",
                                    spool_dir=tmp_path)
    assert out is None


def test_a_send_with_no_packet_id_says_it_cannot_be_joined(fleet):
    out = inbox_legs.heard_line(inbox_legs.summarize_results([]), packet_id=None)
    assert out == "heard by: cannot tell (this send has no radio packet id)"


@pytest.mark.parametrize("summary", [
    {"heard": ["a"], "not_heard": [], "unobservable": [], "cannot_hear": []},
    {"heard": [], "not_heard": ["a"], "unobservable": ["b"], "cannot_hear": ["c"]},
    {"heard": [], "not_heard": [], "unobservable": [], "cannot_hear": []},
])
def test_the_line_never_claims_delivery(summary):
    assert "deliver" not in inbox_legs.heard_line(summary, packet_id=PID).lower()


def test_other_preset_boxes_are_named_as_unable_to_hear(fleet):
    summary = {"heard": ["kiai"], "not_heard": [], "unobservable": [], "cannot_hear": ["moc3"]}
    assert inbox_legs.heard_line(summary, packet_id=PID) == \
        ("heard by kiai · 1 of 1 listening · 1 on another preset/slot "
         "(declared; a bridged copy carries a new id)")
