"""BIC-2 TUI pane — per-route delivery legs, read from the gateway's file.

The pane renders ``snapshot.legs`` from the delivery_snapshot.json the
gateway already publishes (no DB, no HTTP, no radio — the TUI stays a
surface). Every honesty rule of the counters must SURVIVE the render:
a window with no events reads "—", never 0; ``legs: None`` is UNKNOWN; a
writer too old to publish legs is UNKNOWN, not "no traffic"; windows that
reach back before counting began say PARTIAL; and because broadcasts can
never confirm, sent − confirmed is labelled NOT a loss count.

Seam: ``utils.delivery_view.route_legs_leg`` / ``render_route_legs``.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import delivery_view as dv  # noqa: E402

NOW = 1_800_000_000.0


def _gw(status):
    return lambda unit: (status, 1234 if status == "ok" else None)


def _publish(home, ts_age=10.0, **snap):
    p = os.path.join(str(home), dv._DELIVERY_SNAPSHOT_SUBPATH)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        json.dump({"schema": 1, "ts": NOW - ts_age, "snapshot": snap}, fh)


def _legs_snap(legs, since_age=7200.0, partial=("24h",), errors=0,
               last_err=None):
    return dict(legs=legs, legs_since_ts=NOW - since_age,
                legs_partial_windows=list(partial),
                legs_write_errors=errors, legs_last_write_error_ts=last_err,
                health={"preflight_ok": True})


def _render(home, gw="ok"):
    return dv.render_route_legs(home=str(home), now=NOW,
                                gateway_resolver=_gw(gw),
                                unit_enabled_fn=lambda unit: True)


LEGS = {
    "meshtastic>rns": {
        "15m": {"queued": 2, "sent": 2, "confirmed": 1, "from_ts": NOW - 900},
        "1h": {"queued": 5, "sent": 5, "confirmed": 4, "failed": 1,
               "from_ts": NOW - 3600},
        "24h": {"queued": 40, "sent": 39, "confirmed": 36, "failed": 2,
                "from_ts": NOW - 86400},
    },
    "rns>meshtastic": {
        "24h": {"sent": 12, "from_ts": NOW - 86400},
    },
}


def test_each_leg_shows_its_counts_per_window(tmp_path):
    _publish(tmp_path, **_legs_snap(LEGS))
    leg = dv.route_legs_leg(str(tmp_path), NOW, "ok")

    assert leg.status == dv.OK
    text = "\n".join(leg.lines)
    assert "meshtastic>rns" in text and "rns>meshtastic" in text
    row = next(ln for ln in leg.lines
               if ln.lstrip().startswith("meshtastic>rns") and " 1h " in ln)
    # inside an OBSERVED window a missing outcome is a true 0
    assert row.split()[2:] == ["5", "5", "4", "1", "0"]


def test_a_window_with_no_events_reads_dash_never_zero(tmp_path):
    _publish(tmp_path, **_legs_snap(LEGS))
    leg = dv.route_legs_leg(str(tmp_path), NOW, "ok")

    row = next(ln for ln in leg.lines
               if ln.lstrip().startswith("rns>meshtastic") and " 15m " in ln)
    assert "nothing observed" in row
    assert " 0 " not in row


def test_unobservable_legs_are_unknown(tmp_path):
    _publish(tmp_path, legs=None, legs_since_ts=None,
             legs_partial_windows=None, legs_write_errors=None,
             health={"preflight_ok": True})
    leg = dv.route_legs_leg(str(tmp_path), NOW, "ok")

    assert leg.status == dv.UNKNOWN
    assert "could not read" in leg.why


def test_a_writer_that_publishes_no_legs_is_unknown_not_quiet(tmp_path):
    _publish(tmp_path, state_totals={"sent": 3}, health={"preflight_ok": True})
    leg = dv.route_legs_leg(str(tmp_path), NOW, "ok")

    assert leg.status == dv.UNKNOWN
    assert "older" in leg.why


def test_a_stale_record_is_unknown(tmp_path):
    _publish(tmp_path, ts_age=3600 * 6, **_legs_snap(LEGS))
    leg = dv.route_legs_leg(str(tmp_path), NOW, "ok")

    assert leg.status == dv.UNKNOWN and "STALE" in leg.why


def test_no_gateway_on_this_box_is_inert(tmp_path):
    leg = dv.route_legs_leg(str(tmp_path), NOW, "absent")

    assert leg.status == dv.INERT


def test_partial_windows_are_named(tmp_path):
    _publish(tmp_path, **_legs_snap(LEGS, since_age=1800,
                                    partial=("1h", "24h")))
    text = "\n".join(dv.route_legs_leg(str(tmp_path), NOW, "ok").lines)

    assert "PARTIAL" in text and "1h" in text and "24h" in text


def test_leg_write_errors_are_surfaced_with_their_age(tmp_path):
    _publish(tmp_path, **_legs_snap(LEGS, errors=3, last_err=NOW - 120))
    text = "\n".join(dv.route_legs_leg(str(tmp_path), NOW, "ok").lines)

    assert "3 leg write error" in text and "2m old" in text


def test_sent_minus_confirmed_is_labelled_not_a_loss_count(tmp_path):
    _publish(tmp_path, **_legs_snap(LEGS))
    text = "\n".join(dv.route_legs_leg(str(tmp_path), NOW, "ok").lines)

    assert "NOT a loss count" in text


def test_the_unknown_origin_leg_is_explained(tmp_path):
    _publish(tmp_path, **_legs_snap(
        {"?>rns": {"15m": {"sent": 1, "from_ts": NOW - 900}}}))
    text = "\n".join(dv.route_legs_leg(str(tmp_path), NOW, "ok").lines)

    assert "?>rns" in text and "did not name its origin" in text


def test_counting_but_nothing_counted_yet_says_so(tmp_path):
    _publish(tmp_path, **_legs_snap({}, since_age=300, partial=("1h", "24h")))
    leg = dv.route_legs_leg(str(tmp_path), NOW, "ok")

    assert leg.status == dv.OK
    assert any("no leg has counted an event" in ln for ln in leg.lines)


def test_not_counting_yet_says_so(tmp_path):
    _publish(tmp_path, legs={}, legs_since_ts=None,
             legs_partial_windows=["15m", "1h", "24h"], legs_write_errors=0,
             health={"preflight_ok": True})
    leg = dv.route_legs_leg(str(tmp_path), NOW, "ok")

    assert any("has not counted its first event" in ln for ln in leg.lines)


def test_render_route_legs_has_a_header_and_never_changes_anything(tmp_path):
    _publish(tmp_path, **_legs_snap(LEGS))
    text = _render(tmp_path)

    assert text.startswith("DELIVERY LEGS")
    assert "never changes anything" in text


def test_the_pane_never_claims_messages_are_arriving(tmp_path):
    # The shared headline says "Messages are arriving" for any fresh, passing
    # section. This pane only COUNTS — it judges no rate — so it must never
    # borrow that verdict (found writing it, 10-23).
    _publish(tmp_path, **_legs_snap(LEGS))
    text = _render(tmp_path)

    assert "arriving" not in text
    assert "COUNTS ONLY" in text


def test_an_unknown_pane_keeps_the_unknown_headline(tmp_path):
    _publish(tmp_path, legs=None, health={"preflight_ok": True})

    assert "UNKNOWN" in _render(tmp_path).splitlines()[1]


# ── review of the pane (non-author, 10-23) ────────────────────────────

def test_one_misshaped_leg_does_not_blind_the_others(tmp_path):
    # #1: a bad value for ONE route is that route's problem, not the pane's.
    snap = _legs_snap({**LEGS, "rns>rns": ["not", "a", "dict"]})
    snap["legs_partial_windows"] = 24                  # #6: and a bad partial
    _publish(tmp_path, **snap)
    leg = dv.route_legs_leg(str(tmp_path), NOW, "ok")
    text = "\n".join(leg.lines)

    assert leg.status == dv.OK
    assert "meshtastic>rns" in text
    assert "rns>rns" in text and "misshaped" in text


def test_the_reader_shares_the_writers_window_and_outcome_names():
    # #2 (hfm #5/#7): the pane reads what delivery_legs writes; a new window
    # or outcome there must fail HERE until the pane shows it.
    from gateway import delivery_legs as dl

    assert dv._ROUTE_WINDOWS == tuple(w for w, _s in dl.WINDOWS)
    assert dv._ROUTE_OUTCOMES == dl.OUTCOMES


def test_an_inert_pane_says_only_what_it_measured(tmp_path):
    # #3: the shared inert headline claimed "no soak timer" — never checked.
    text = dv.render_route_legs(home=str(tmp_path), now=NOW,
                                gateway_resolver=lambda u: ("absent", None),
                                unit_enabled_fn=lambda u: True)

    assert "soak" not in text.splitlines()[1]
    assert "inert" in text.splitlines()[1].lower()


def test_counts_without_a_start_time_are_partial_not_empty(tmp_path):
    # #4: leg_meta lost while buckets survive — never "empty by construction"
    # above a table of counts.
    _publish(tmp_path, legs=LEGS, legs_since_ts=None,
             legs_partial_windows=["15m", "1h", "24h"], legs_write_errors=0,
             health={"preflight_ok": True})
    text = "\n".join(dv.route_legs_leg(str(tmp_path), NOW, "ok").lines)

    assert "empty by construction" not in text
    assert "start is UNKNOWN" in text and "PARTIAL" in text


def test_each_windows_reach_comes_from_the_published_bucket_size(tmp_path):
    # #5, corrected by the LIVE render (10-23): from_ts is the oldest bucket
    # that HAD events, not the window's start ("15m spans 5 min" was false).
    # A window's true reach is its length + one bucket, from what the writer
    # publishes — never a sentence that rots when BUCKET_S moves.
    _publish(tmp_path, legs_bucket_s=300, **_legs_snap(LEGS))
    text = " ".join(" ".join(
        dv.route_legs_leg(str(tmp_path), NOW, "ok").lines).split())

    assert "15m reaches up to 20 min" in text
    assert "1h up to 65 min" in text
    assert "spans" not in text


def test_an_unpublished_bucket_size_says_the_edges_are_approximate(tmp_path):
    _publish(tmp_path, **_legs_snap(LEGS))      # an older writer: no field
    text = " ".join(" ".join(
        dv.route_legs_leg(str(tmp_path), NOW, "ok").lines).split())

    assert "bucket size not published" in text


def test_a_bool_write_error_count_is_not_rendered_as_a_number(tmp_path):
    _publish(tmp_path, **_legs_snap(LEGS, errors=True))
    text = "\n".join(dv.route_legs_leg(str(tmp_path), NOW, "ok").lines)

    assert "True leg write" not in text


def test_every_line_fits_a_78_column_box(tmp_path):
    # #9: the honesty caveats are exactly what a clipped line would lose.
    _publish(tmp_path, **_legs_snap({**LEGS, "?>rns": LEGS["rns>meshtastic"]},
                                    errors=3, last_err=NOW - 120,
                                    partial=("1h", "24h")))
    text = _render(tmp_path)

    too_wide = [ln for ln in text.splitlines()
                if len(ln) > 78 and not ln.lstrip().startswith("source:")]
    assert not too_wide, too_wide


def test_a_fresh_record_needs_no_gateway_unit_lookup(tmp_path):
    # #7: the systemctl lookup only matters when the record is absent.
    _publish(tmp_path, **_legs_snap(LEGS))

    calls = []
    dv.render_route_legs(home=str(tmp_path), now=NOW,
                         gateway_resolver=lambda u: calls.append(u) or ("ok", 1),
                         unit_enabled_fn=lambda u: calls.append(u) or True)

    assert calls == []   # (_gateway_state swallows a raising resolver)


# ── re-review of the pane fixes (non-author, 10-23) ───────────────────

_BOX_INTERIOR = 74     # dialog --textbox 78 wide, minus border + scrollbar


def test_every_line_fits_the_textbox_interior(tmp_path):
    # #1/#2: the shared render indents leg lines by 2 AFTER the wrap.
    _publish(tmp_path, legs_bucket_s=300,
             **_legs_snap({**LEGS, "?>rns": LEGS["rns>meshtastic"]},
                          errors=3, last_err=NOW - 120, partial=("1h", "24h")))
    text = _render(tmp_path)

    assert not [ln for ln in text.splitlines() if len(ln) > _BOX_INTERIOR]


def test_table_rows_are_never_wrapped_mid_columns(tmp_path):
    # #3: a long route name is truncated, its row stays one line.
    long_name = "meshtastic>rns-with-a-very-long-suffix"
    _publish(tmp_path, **_legs_snap({long_name: LEGS["meshtastic>rns"]}))
    rows = [ln for ln in _render(tmp_path).splitlines()
            if ln.lstrip().startswith("meshtastic>rns") and " 1h " in ln]

    assert len(rows) == 1 and rows[0].split()[-5:] == ["5", "5", "4", "1", "0"]


def test_a_misshaped_partial_list_is_rederived_from_the_start_time(tmp_path):
    # #4: with since known, partiality is computable — never "all partial".
    snap = _legs_snap(LEGS, since_age=7200)
    snap["legs_partial_windows"] = 24
    _publish(tmp_path, **snap)
    text = " ".join(" ".join(
        dv.route_legs_leg(str(tmp_path), NOW, "ok").lines).split())

    assert "PARTIAL: 24h reach" in text


def test_a_negative_error_count_is_not_an_alarm(tmp_path):
    _publish(tmp_path, **_legs_snap(LEGS, errors=-1))
    text = "\n".join(dv.route_legs_leg(str(tmp_path), NOW, "ok").lines)

    assert "write error" not in text


def test_the_window_seconds_are_pinned_to_the_writer_too():
    # #6: labels AND seconds, one artifact, one constant (hfm #5).
    from gateway import delivery_legs as dl

    assert dv._ROUTE_WINDOW_SECS == tuple(dl.WINDOWS)


def test_a_sub_minute_bucket_size_is_stated_in_seconds(tmp_path):
    # #8: "whole 0-min buckets" contradicted the reach it explained.
    _publish(tmp_path, legs_bucket_s=30, **_legs_snap(LEGS))
    text = " ".join(" ".join(
        dv.route_legs_leg(str(tmp_path), NOW, "ok").lines).split())

    assert "30-s buckets" in text and "0-min" not in text


def test_an_inert_pane_says_its_why_once(tmp_path):
    # #9: the headline and the section repeated the same sentence.
    text = dv.render_route_legs(home=str(tmp_path), now=NOW,
                                gateway_resolver=lambda u: ("absent", None),
                                unit_enabled_fn=lambda u: True)

    assert text.count("not a gateway") == 1
