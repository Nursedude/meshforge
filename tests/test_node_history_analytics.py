"""Dashboard › Analytics re-pointed at the live node history (2026-09-23).

The DB is built with the REAL `NodeHistoryDB` schema, so a schema change
breaks these tests instead of silently breaking the screens. Rows are
SNAPSHOTS (the collector writes every known node each cycle, last-heard SNR
and battery repeated until a new packet): the tests pin that repeats count
once, offline rows are not readings, and absence is never an empty verdict.
"""
import contextlib
import io
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "..", "src"), os.path.join(HERE, "..", "src", "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)

from utils import node_history_analytics as nha  # noqa: E402
from utils.db_helpers import connect_tuned  # noqa: E402

NOW = 1_790_000_000.0
H = 3600.0


@pytest.fixture
def db(tmp_path):
    from utils.node_history import NodeHistoryDB
    path = tmp_path / "node_history.db"
    NodeHistoryDB(db_path=path)  # the real schema
    return path


def _obs(path, rows):
    """rows: (node_id, t_offset_h, is_online, snr, battery, lat, lon, via_mqtt[, network])"""
    c = connect_tuned(path)
    for r in rows:
        nid, off, online, snr, bat, lat, lon, mq = r[:8]
        net = r[8] if len(r) > 8 else "meshtastic"
        c.execute("INSERT INTO node_observations (node_id, timestamp, latitude, longitude,"
                  " snr, battery, is_online, network, via_mqtt, name) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (nid, NOW + off * H, lat, lon, snr, bat, online, net, mq, nid.upper()))
    c.commit()
    c.close()


# --- states: absent / unreadable / empty are distinct and never a verdict --

@pytest.mark.parametrize("fn", [nha.health_timeline, nha.link_trends, nha.predictive, nha.coverage])
def test_absent_db_is_absent_and_not_created(tmp_path, fn):
    p = tmp_path / "nope" / "node_history.db"
    res = fn(p, now=NOW)
    assert res["state"] == "absent"
    assert not p.exists() and not p.parent.exists()  # a screen never creates a store


@pytest.mark.parametrize("fn", [nha.health_timeline, nha.link_trends, nha.predictive, nha.coverage])
def test_garbage_file_is_unreadable_not_empty(tmp_path, fn):
    p = tmp_path / "node_history.db"
    p.write_bytes(b"not a database at all" * 50)
    res = fn(p, now=NOW)
    assert res["state"] == "unreadable" and res["error"]


@pytest.mark.parametrize("fn", [nha.health_timeline, nha.link_trends, nha.predictive, nha.coverage])
def test_readable_but_nothing_in_window_is_empty(db, fn):
    _obs(db, [("!old", -100, 1, 5.0, 50, 21.0, -157.0, 0)])  # outside the 48 h window
    assert fn(db, now=NOW)["state"] == "empty"


# --- health: known vs online, SNR over ONLINE nodes only -------------------

def test_health_counts_known_and_online_and_averages_online_snr_only(db):
    _obs(db, [
        ("!a", -2.5, 1, 10.0, None, 21.0, -157.0, 0),
        ("!b", -2.4, 0, -20.0, None, 21.0, -157.0, 0),   # known, offline: its SNR is stale
        ("!c", -2.3, 1, 0.0, None, 21.0, -157.0, 0),
    ])
    res = nha.health_timeline(db, now=NOW)
    assert res["state"] == "ok"
    (hour,) = res["hours"]
    assert (hour["known"], hour["online"]) == (3, 2)
    assert hour["avg_snr_online"] == pytest.approx(5.0)
    assert hour["snr_samples"] == 2


def test_health_marks_the_current_hour_partial(db):
    _obs(db, [("!a", -3, 1, 1.0, None, 21.0, -157.0, 0), ("!a", -0.01, 1, 2.0, None, 21.0, -157.0, 0)])
    hours = nha.health_timeline(db, now=NOW)["hours"]
    assert [h["partial"] for h in hours] == [False, True]


# --- trends: repeats collapse; offline rows are not readings ---------------

def test_repeated_snapshot_value_is_one_reading(db):
    rows = [("!rep", -47 + i * 0.1, 1, 4.0, None, 21, -157, 0) for i in range(30)]
    rows += [("!rep", -1 + i * 0.1, 1, 4.0, None, 21, -157, 0) for i in range(10)]
    _obs(db, rows)
    res = nha.link_trends(db, now=NOW)
    assert res["nodes_with_snr"] == 1 and res["nodes_judged"] == 0


def test_distinct_readings_at_both_ends_are_judged(db):
    early = [("!n", -47 + i * 0.5, 1, 8.0 - i, None, 21, -157, 0) for i in range(4)]
    late = [("!n", -5 + i * 0.5, 1, -2.0 - i, None, 21, -157, 0) for i in range(4)]
    offline_noise = [("!n", -3 + i * 0.1, 0, 99.0 + i, None, 21, -157, 0) for i in range(5)]
    _obs(db, early + late + offline_noise)
    res = nha.link_trends(db, now=NOW)
    (r,) = res["declining"]
    assert r["snr_first"] == pytest.approx(6.5) and r["snr_last"] == pytest.approx(-3.5)
    assert r["delta_db"] == pytest.approx(-10.0)
    assert res["improving"] == []


# --- predictive: slope, ETA, and what is NOT a battery reading -------------

def test_falling_battery_is_flagged_with_eta(db):
    _obs(db, [("!bat", -10 + i, 1, None, 80 - 2 * i, 21, -157, 0) for i in range(8)])
    res = nha.predictive(db, now=NOW)
    (a,) = res["alerts"]
    assert a["kind"] == "battery" and a["slope"] == pytest.approx(-2.0)
    assert a["last"] == 66 and a["eta_h_to_floor"] == pytest.approx(23.0)


def test_battery_zero_and_external_power_are_not_readings(db):
    rows = [("!pwr", -10 + i, 1, None, v, 21, -157, 0)
            for i, v in enumerate([101, 0, 101, 0, 101, 0, 101, 0])]
    _obs(db, rows)
    res = nha.predictive(db, now=NOW)
    # observations exist, so this is NOT "empty" — nothing was judgeable
    assert res["state"] == "ok" and res["battery_nodes_judged"] == 0 and res["alerts"] == []


def test_steady_node_is_judged_but_not_flagged(db):
    _obs(db, [("!ok", -10 + i, 1, None, 90 - (i % 2), 21, -157, 0) for i in range(8)])
    res = nha.predictive(db, now=NOW)
    assert res["battery_nodes_judged"] == 1 and res["alerts"] == []


# --- coverage: newest fix per node; outliers counted, not drawn ------------

def test_coverage_uses_central_extent_and_counts_outliers(db):
    rows = [(f"!h{i}", -1, 1, None, None, 19.5 + i * 0.1, -156.0 - i * 0.1, 0) for i in range(20)]
    rows += [("!far", -1, 1, None, None, 48.47, 157.5, 1), ("!zero", -1, 1, None, None, 0.02, -160.0, 0)]
    rows += [("!h0", -30, 1, None, None, 60.0, 10.0, 0)]  # an OLDER fix must not be used
    _obs(db, rows)
    res = nha.coverage(db, now=NOW)
    assert res["positioned"] == 22 and res["via_mqtt"] == 1
    assert res["outside_extent"] >= 2
    assert res["extent_diagonal_km"] < 500  # not the 6,600 km a raw bbox gave on live data
    assert res["nodes_known"] == 22


# --- the screens say it --------------------------------------------------

def _render(method_name, monkeypatch, path):
    from handler_test_utils import make_handler_context
    import handlers.analytics as ah
    monkeypatch.setattr(nha, "default_db_path", lambda: path)
    monkeypatch.setattr(ah, "clear_screen", lambda: None)
    h = ah.AnalyticsHandler()
    h.set_context(make_handler_context())
    h.ctx.wait_for_enter = lambda *a, **k: None
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        getattr(h, method_name)()
    return out.getvalue()


SCREENS = ["_show_health_history", "_show_link_trends", "_show_predictive_alerts",
           "_show_coverage_stats"]


@pytest.mark.parametrize("screen", SCREENS)
def test_screen_says_absent(tmp_path, monkeypatch, screen):
    text = _render(screen, monkeypatch, tmp_path / "missing.db")
    assert "No node history on this box" in text


@pytest.mark.parametrize("screen", SCREENS)
def test_screen_names_its_source_when_it_has_data(db, monkeypatch, screen):
    import time as _t
    now = _t.time()
    c = connect_tuned(db)
    for i in range(8):
        c.execute("INSERT INTO node_observations (node_id, timestamp, latitude, longitude, snr,"
                  " battery, is_online, network, via_mqtt, name) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  ("!x", now - (47 - i) * H, 21.3, -157.8, 5.0 - i, 90 - 3 * i, 1,
                   "meshtastic", 0, "X"))
    c.commit()
    c.close()
    text = _render(screen, monkeypatch, db)
    assert "node_history.db on THIS box" in text


def test_predictive_never_calls_unjudged_nodes_healthy(db, monkeypatch):
    import time as _t
    c = connect_tuned(db)
    c.execute("INSERT INTO node_observations (node_id, timestamp, latitude, longitude, snr,"
              " battery, is_online, network, via_mqtt, name) VALUES (?,?,?,?,?,?,?,?,?,?)",
              ("!one", _t.time() - H, 21.3, -157.8, 5.0, 90, 1, "meshtastic", 0, "ONE"))
    c.commit()
    c.close()
    text = _render("_show_predictive_alerts", monkeypatch, db)
    assert "UNKNOWN" in text and "not healthy" in text
    assert "No node observations" not in text  # there IS one; it is too few to judge


def test_observations_without_snr_are_not_empty(db, monkeypatch):
    # MQTT-only shape (moc, measured): observations, none with SNR.
    import time as _t
    c = connect_tuned(db)
    for i in range(5):
        c.execute("INSERT INTO node_observations (node_id, timestamp, latitude, longitude, snr,"
                  " battery, is_online, network, via_mqtt, name) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (f"!m{i}", _t.time() - H, 21.3, -157.8, None, None, 1, "meshtastic", 1, "M"))
    c.commit()
    c.close()
    res = nha.link_trends(db)
    assert res["state"] == "ok" and res["nodes_with_snr"] == 0
    text = _render("_show_link_trends", monkeypatch, db)
    assert "cannot be measured here" in text and "No node observations" not in text
    # one explanation, not two (live double tap on moc, 2026-09-23)
    assert "Not enough distinct readings" not in text
