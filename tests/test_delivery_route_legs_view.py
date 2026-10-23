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
