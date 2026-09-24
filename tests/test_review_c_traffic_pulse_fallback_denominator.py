"""Review C (third-party, 2026-09-23): ``traffic_pulse._parse_delivery_db`` is
the DB fallback for ``/api/gateway/delivery`` when the map daemon is down. Its
docstring pins it to the writer's key scheme ("if the scheme there changes,
this fallback (and its test) updates with it"). 286a812f changed the scheme
(``drop_proto.<reason>.<proto>``) and the confirmation rule (failures count
only on confirmable protocols); the fallback still sums every failure, so the
same DB reads two different confirmation_rates depending on which path served
it — moc: 0.980 served vs 0.975 fallback once the ``secondary`` drops are tagged.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from monitoring import traffic_pulse as tp  # noqa: E402
from utils.db_helpers import connect_tuned  # noqa: E402


_ROWS = [
    ("state.confirmed", 8), ("state.dropped", 6),
    ("state_proto.confirmed.rns", 8),
    ("state_proto.dropped.rns", 2), ("state_proto.dropped.secondary", 4),
    ("drop.retries_exhausted", 6),
    ("drop_proto.retries_exhausted.rns", 2),
    ("drop_proto.retries_exhausted.secondary", 4),
    ("meta.last_event_ts", 1781000000000),
    ("meta.preflight_ok", 1), ("meta.consecutive_write_errors", 0),
]


def _db(tmp_path, rows):
    p = tmp_path / "delivery_counters.db"
    conn = connect_tuned(str(p))
    conn.execute("CREATE TABLE counters (key TEXT PRIMARY KEY, value INTEGER)")
    conn.execute("CREATE TABLE events (ts REAL, id TEXT, state TEXT, "
                 "protocol TEXT, drop_reason TEXT, note TEXT)")
    conn.executemany("INSERT INTO counters VALUES (?,?)", rows)
    conn.commit()
    conn.close()
    return p


def test_fallback_rate_equals_the_served_rule_with_drop_proto_keys(tmp_path, monkeypatch):
    from gateway.delivery_counters import compute_confirmation_view
    p = _db(tmp_path, _ROWS)
    monkeypatch.setattr(tp, "_db_path",
                        lambda n: p if n == "delivery_counters" else None)
    d = tp._parse_delivery_db()
    served = compute_confirmation_view(
        {"confirmed": 8, "dropped": 6},
        {"confirmed": {"rns": 8}, "dropped": {"rns": 2, "secondary": 4}},
        {"retries_exhausted": 6},
        drop_reasons_by_protocol={"rns": {"retries_exhausted": 2},
                                  "secondary": {"retries_exhausted": 4}},
    )
    assert served["confirmation_rate"] == 0.8          # 8 / (8 + 2 rns)
    assert d["confirmation_rate"] == served["confirmation_rate"]


def test_fallback_drop_proto_keys_are_not_misfiled_as_reasons(tmp_path, monkeypatch):
    p = _db(tmp_path, _ROWS)
    monkeypatch.setattr(tp, "_db_path",
                        lambda n: p if n == "delivery_counters" else None)
    d = tp._parse_delivery_db()
    assert set(d["drop_reasons"]) == {"retries_exhausted"}
