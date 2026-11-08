"""Terminal events get their OWN prune pool (2026-11-07, moc3 blind 10-30→).

The events table pruned every non-refusal row as ONE pool, so on a gateway
whose traffic is mostly Meshtastic queued/sent (moc3: 198 of 200 recent rows)
the RNS confirmed/dropped rows the #74 stall probe judges were deleted after
~26 h — `recent_terminal` held 2 events and a 7-day window could never fill.
"""
import sqlite3

from gateway import delivery_counters as dc


def _c(tmp_path, cap=40):
    return dc.DeliveryCounters(db_path=tmp_path / "dc.db", run_preflight=False,
                               ring_cap=cap)


def _count(tmp_path, where, args=()):
    with sqlite3.connect(str(tmp_path / "dc.db")) as raw:
        return raw.execute(f"SELECT COUNT(*) FROM events WHERE {where}", args).fetchone()[0]


def test_inflight_flood_never_evicts_a_terminal(tmp_path):
    c = _c(tmp_path)
    for i in range(5):
        c.record(dc.DeliveryState.CONFIRMED, f"ok{i}", protocol="rns", source="meshtastic")
    c.record(dc.DeliveryState.DROPPED, "f0", protocol="rns", source="meshtastic",
             drop_reason=dc.DropReason.RNS_DELIVERY_FAILED)
    for i in range(40 * 5):     # moc3's shape: Meshtastic queued/sent swamps the ring
        c.record(dc.DeliveryState.QUEUED, f"q{i}", protocol="meshtastic", source="rns")
        c.record(dc.DeliveryState.SENT, f"q{i}", protocol="meshtastic", source="rns")
    snap = c.snapshot()
    states = [e["state"] for e in snap["recent_terminal"]]
    assert states.count("confirmed") == 5 and states.count("dropped") == 1
    # the in-flight pool still bounds itself at its own cap
    assert _count(tmp_path, "state NOT IN ('confirmed', 'dropped')") == 40


def test_terminal_pool_bounds_itself(tmp_path):
    c = _c(tmp_path)
    for i in range(100):
        c.record(dc.DeliveryState.CONFIRMED, f"ok{i}", protocol="rns", source="meshtastic")
    assert _count(tmp_path, "state IN ('confirmed', 'dropped') "
                            "AND drop_reason IS NOT 'ingress_refused'") == 40
    # newest kept
    ids = [e["id"] for e in c.snapshot()["recent_terminal"]]
    assert "ok99" in ids and "ok0" not in ids


def test_terminal_flood_never_evicts_inflight(tmp_path):
    c = _c(tmp_path)
    for i in range(10):
        c.record(dc.DeliveryState.QUEUED, f"q{i}", protocol="meshtastic", source="rns")
    for i in range(200):
        c.record(dc.DeliveryState.CONFIRMED, f"ok{i}", protocol="rns", source="meshtastic")
    assert _count(tmp_path, "state = 'queued'") == 10


def test_refusals_still_their_own_pool(tmp_path):
    c = _c(tmp_path)
    for i in range(dc.REFUSAL_RING_CAP * 3):
        c.record(dc.DeliveryState.DROPPED, f"r{i}", protocol="meshtastic", source="rns",
                 drop_reason=dc.DropReason.INGRESS_REFUSED)
    c.record(dc.DeliveryState.CONFIRMED, "ok", protocol="rns", source="meshtastic")
    assert _count(tmp_path, "drop_reason = 'ingress_refused'") == dc.REFUSAL_RING_CAP
    assert [e["id"] for e in c.snapshot()["recent_terminal"]] == ["ok"]


# ── review round 1 (2026-11-07) ─────────────────────────────────────────

def test_benign_drop_flood_never_evicts_a_judged_terminal(tmp_path):
    """r1 #1: Meshtastic dedup/shed drops are terminal but NOT judged — they
    must not take the RNS rows' pool or their served slots."""
    c = _c(tmp_path)
    for i in range(5):
        c.record(dc.DeliveryState.CONFIRMED, f"ok{i}", protocol="rns", source="meshtastic")
    for i in range(400):
        c.record(dc.DeliveryState.DROPPED, f"d{i}", protocol="meshtastic", source="rns",
                 drop_reason=dc.DropReason.DEDUP)
    ring = c.snapshot()["recent_terminal"]
    assert [e["id"] for e in ring] == [f"ok{i}" for i in range(5)]


def test_a_refusal_in_any_state_lives_only_in_the_refusal_pool(tmp_path):
    """Fable r1 F2: QUEUED + INGRESS_REFUSED landed in BOTH pools and the
    in-flight prune deleted it."""
    c = _c(tmp_path, cap=5)
    c.record(dc.DeliveryState.QUEUED, "ref", protocol="meshtastic", source="rns",
             drop_reason=dc.DropReason.INGRESS_REFUSED)
    for i in range(6):
        c.record(dc.DeliveryState.QUEUED, f"q{i}", protocol="meshtastic", source="rns")
    assert _count(tmp_path, "id = 'ref'") == 1
    assert _count(tmp_path, "state = 'queued' AND drop_reason IS NULL") == 5


def test_python_and_sql_judged_predicate_agree_on_every_input(tmp_path):
    """The prune decides in Python, the served ring and the pool count in SQL:
    one predicate, so they must agree on the FULL state × reason space —
    and the three pools must partition it (each row in exactly one)."""
    from utils._delivery_confirmation_window import (
        JUDGED_TERMINAL_ARGS, JUDGED_TERMINAL_SQL, is_judged_terminal)
    from gateway.delivery_counters import _OTHER_POOL_ARGS, _OTHER_POOL_SQL
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE events (state TEXT, drop_reason TEXT)")
    combos = [(st.value, dr) for st in dc.DeliveryState
              for dr in [None, ""] + [r.value for r in dc.DropReason]]
    combos += [("weird", None), ("weird", "rns_delivery_failed")]
    conn.executemany("INSERT INTO events VALUES (?, ?)", combos)
    for st, dr in combos:
        w = "state IS ? AND drop_reason IS ?"
        judged = conn.execute(f"SELECT COUNT(*) FROM events WHERE {w} AND {JUDGED_TERMINAL_SQL}",
                              (st, dr) + JUDGED_TERMINAL_ARGS).fetchone()[0]
        other = conn.execute(f"SELECT COUNT(*) FROM events WHERE {w} AND {_OTHER_POOL_SQL}",
                             (st, dr) + _OTHER_POOL_ARGS).fetchone()[0]
        refused = conn.execute(f"SELECT COUNT(*) FROM events WHERE {w} AND "
                               "COALESCE(drop_reason, '') = 'ingress_refused'",
                               (st, dr)).fetchone()[0]
        assert judged == int(is_judged_terminal(st, dr)), (st, dr)
        assert judged + other + refused == 1, (st, dr, judged, other, refused)


def test_shared_constants_are_pinned_to_the_gateway():
    from utils import _delivery_confirmation_window as w
    assert w.INGRESS_REFUSED == dc.DropReason.INGRESS_REFUSED.value
    assert w.SERVED_TERMINAL_LIMIT == dc.SNAPSHOT_RECENT_LIMIT
    assert w._DELIVERY_FAILURE_REASONS == dc.DELIVERY_FAILURE_REASONS
