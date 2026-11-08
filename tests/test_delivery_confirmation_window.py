"""The 7-day confirmation window + the 72 h collapse COUNTER (2026-11-07).

moc3's `delivery_confirmation_stall` sat indeterminate from 10-30 on: 2 of 20
terminal events, because the ring it judged held ~26 h. These pin the TIME
window (not "whatever the ring holds"), the clock-hazard directions, and that
the collapse counter is OBSERVATION ONLY (operator decision 2026-11-07).
"""
import json
import time

import pytest

from utils import watchdog_probes_gateway as g
from utils._delivery_confirmation_window import (
    COLLAPSE_COUNTER_WINDOW_S, DELIVERY_STALL_WINDOW_S, confirmation_window)
from utils.watchdog_probe_core import collect_dispositions, reset_dispositions

NOW = 1_800_000_000.0
DAY = 86400.0


def _ev(age_s, state="confirmed", proto="rns", reason=None, ts=...):
    return {"ts": (NOW - age_s) if ts is ... else ts, "id": "x", "state": state,
            "protocol": proto, "drop_reason": reason}


def _payload(ring):
    return {"state_by_protocol": {"confirmed": {"rns": 31564}},
            "recent_terminal": ring}


def test_events_older_than_the_window_are_out_and_counted():
    ring = [_ev(DELIVERY_STALL_WINDOW_S + 60)] * 3 + [_ev(DAY)] * 4
    w = confirmation_window(_payload(ring), now=NOW)
    assert (w["terminal"], w["out_of_window"]) == (4, 3)
    assert len(w["ring"]) == 4      # the TUI span is computed over the window


def test_six_days_of_a_quiet_box_is_judged():
    """moc3's shape: ~4/day RNS terminals → 24 in six days clears the floor."""
    ring = [_ev(i * 6 * 3600) for i in range(24)]
    w = confirmation_window(_payload(ring), now=NOW)
    assert w["terminal"] == 24 >= g.DELIVERY_STALL_MIN_TERMINAL


def test_future_stamped_event_stays_in_window():
    """Clock stepped BACK: dropping the event would erase real evidence."""
    w = confirmation_window(_payload([_ev(-3 * DAY)]), now=NOW)
    assert w["terminal"] == 1 and w["out_of_window"] == 0


@pytest.mark.parametrize("ts", [None, "garbage", float("nan"), True])
def test_undated_event_is_kept_and_counted(ts):
    w = confirmation_window(_payload([_ev(0, ts=ts)]), now=NOW)
    assert (w["terminal"], w["undated"]) == (1, 1)


def test_clock_jumped_forward_reads_too_few_never_green():
    w = confirmation_window(_payload([_ev(0)] * 30), now=NOW + 30 * DAY)
    assert w["terminal"] == 0 and w["out_of_window"] == 30


def test_benign_drops_and_unconfirmable_protocols_stay_out():
    ring = [_ev(0, "dropped", reason="duplicate"), _ev(0, proto="meshtastic"),
            _ev(0, "dropped", reason="rns_delivery_failed")]
    w = confirmation_window(_payload(ring), now=NOW)
    assert (w["confirmed"], w["failed"]) == (0, 1)


def test_collapse_counter_fires_its_candidate_flag_only():
    ring = [_ev(h * 3600, "dropped", reason="rns_delivery_failed") for h in range(6)]
    c = confirmation_window(_payload(ring), now=NOW)["collapse"]
    assert c["candidate"] is True and c["observe_only"] is True
    assert (c["terminal"], c["confirmed"]) == (6, 0)


def test_collapse_counter_needs_the_floor_and_zero_confirmed():
    four = [_ev(0, "dropped", reason="rns_delivery_failed")] * 4
    assert confirmation_window(_payload(four), now=NOW)["collapse"]["candidate"] is False
    mixed = four * 2 + [_ev(0)]
    assert confirmation_window(_payload(mixed), now=NOW)["collapse"]["candidate"] is False
    old = [_ev(COLLAPSE_COUNTER_WINDOW_S + 60, "dropped", reason="rns_delivery_failed")] * 9
    assert confirmation_window(_payload(old), now=NOW)["collapse"]["terminal"] == 0


def _probe(tmp_path, ring, now=None):
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({"schema": 1, "host": "x", "ts": time.time(),
                             "snapshot": _payload(ring)}))
    reset_dispositions()
    sig = g.probe_delivery_confirmation_stall(
        host="127.0.0.1", port=9, timeout_s=0.2, gateway_main_pid=1,
        snapshot_state_path=str(p))
    return sig, collect_dispositions().get("delivery_confirmation_stall")


def _real_ev(age_s, state="confirmed", reason=None):
    return {"ts": time.time() - age_s, "id": "x", "state": state,
            "protocol": "rns", "drop_reason": reason}


def test_probe_judges_a_quiet_box_over_seven_days(tmp_path):
    sig, d = _probe(tmp_path, [_real_ev(i * 6 * 3600) for i in range(24)])
    assert sig is None and d["disp"] == "clean"
    assert "last 7 d" in d["reason"]


def test_probe_counter_is_observation_only(tmp_path):
    """6 failures in 72 h, 0 confirmed: NOT a signal (operator 2026-11-07),
    but the disposition says so out loud."""
    sig, d = _probe(tmp_path, [_real_ev(h * 3600, "dropped", "rns_delivery_failed")
                               for h in range(6)])
    assert sig is None and d["disp"] == "indeterminate"
    assert "COLLAPSE CANDIDATE (observe-only)" in d["reason"]


def test_probe_rate_signal_carries_the_counter(tmp_path):
    ring = [_real_ev(i * 3600, "dropped", "rns_delivery_failed") for i in range(25)]
    sig, _ = _probe(tmp_path, ring)
    assert sig is not None and sig.severity == "wedge"
    assert sig.extra["collapse_counter"]["candidate"] is True
    assert sig.extra["window_s"] == DELIVERY_STALL_WINDOW_S


# ── review round 1 (2026-11-07) ─────────────────────────────────────────

def test_far_future_event_is_kept_but_never_counts_as_recent():
    """r1 #3: a clock-stepped event must not hold the 72 h counter up."""
    ring = [_ev(-5 * DAY, "dropped", reason="rns_delivery_failed")] * 6
    w = confirmation_window(_payload(ring), now=NOW)
    assert w["terminal"] == 6 and w["undated"] == 6
    assert w["collapse"]["terminal"] == 0 and w["collapse"]["candidate"] is False


def test_undated_never_counts_toward_the_collapse_counter():
    """r1 #4: unknown age is not 'within 72 h'."""
    ring = [_ev(0, "dropped", reason="rns_delivery_failed", ts=None)] * 6
    c = confirmation_window(_payload(ring), now=NOW)["collapse"]
    assert c["terminal"] == 0 and c["candidate"] is False


def test_count_bounded_window_says_so_not_seven_days():
    """Fable r1 F3: a full served ring with nothing aged out is bounded by
    COUNT; the label must not claim an age window the data does not carry."""
    from utils._delivery_confirmation_window import SERVED_TERMINAL_LIMIT, format_window
    ring = [_ev(i * 600) for i in range(SERVED_TERMINAL_LIMIT)]
    w = confirmation_window(_payload(ring), now=NOW)
    assert w["truncated"] is True
    txt = format_window(w)
    assert "newest 200 terminals" in txt and "count-bounded" in txt
    assert not txt.startswith("last 7 d")


def test_labels_derive_from_the_constants(monkeypatch):
    """r1 #5: retune the window, the words follow."""
    from utils import _delivery_confirmation_window as m
    monkeypatch.setattr(m, "COLLAPSE_COUNTER_WINDOW_S", 48 * 3600.0)
    txt = m.format_window(m.confirmation_window(_payload([_ev(0)]), now=NOW))
    assert "48 h:" in txt and "72 h" not in txt
    txt = m.format_window(m.confirmation_window(_payload([_ev(0)]), now=NOW,
                                                window_s=3 * DAY))
    assert txt.startswith("last 72 h")


def test_probe_message_names_the_ring_it_judged(tmp_path):
    """r1 #9: 'N in-window of TOTAL' — never the in-window count alone."""
    ring = [_real_ev(30 * DAY)] * 7 + [_real_ev(60)] * 2
    _, d = _probe(tmp_path, ring)
    assert "2 in-window of 9 `recent_terminal` events" in d["reason"]
    assert "7 older outside" in d["reason"]


def test_full_legacy_ring_is_count_bounded_too():
    """Fable r2: a not-yet-rolled gateway's 200-row `recent` ring (26 h of
    mixed traffic) must not be labelled "last 7 d" in the clean branch."""
    ring = [_ev(i * 400, proto="meshtastic", state="sent") for i in range(170)]
    ring += [_ev(i * 400) for i in range(30)]
    w = confirmation_window({"state_by_protocol": {"confirmed": {"rns": 1}},
                             "recent": ring}, now=NOW)
    assert w["ring_source"] == "recent" and w["truncated"] is True
    from utils._delivery_confirmation_window import format_window
    assert format_window(w).startswith("newest 200 `recent` events")


def test_ring_total_counts_entries_not_junk():
    w = confirmation_window(_payload(["junk"] * 200 + [_ev(0)]), now=NOW)
    assert w["ring_total"] == 1 and w["truncated"] is False


def test_full_ring_reaching_past_the_edge_is_age_bounded():
    """Fable MA r1 L2 (shared code): old NON-confirmable entries in a full ring
    still prove the ring reaches past the window edge — not "count-bounded"."""
    from utils._delivery_confirmation_window import format_window
    ring = ([_ev(8 * DAY, "dropped", "meshtastic", "unsendable")] * 150
            + [_ev(i * 600) for i in range(50)])
    w = confirmation_window(_payload(ring), now=NOW)
    assert w["out_of_window"] == 0 and w["truncated"] is False
    assert format_window(w).startswith("last 7 d")


@pytest.mark.parametrize("ts", [0, 0.0, -5])
def test_epoch_zero_ts_is_undated_not_ancient(ts):
    """Fable MA r2 INFO: `now - 0` is the absent-value sentinel class."""
    w = confirmation_window(_payload([_ev(0, ts=ts)]), now=NOW)
    assert (w["undated"], w["out_of_window"]) == (1, 0)
