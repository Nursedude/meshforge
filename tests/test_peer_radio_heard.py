"""MAP-H H14 (2026-11-08): a peer's RADIO hearing time, with its own trust.

Pre-registered acceptance (session notes 11-08 11:33):
  1. moc3 on the manager map reads heard-by-peer minutes, not "204d".
  2. a peer with a frozen clock reads UNOBSERVABLE — never heard / not heard.

The refuted premise this replaces: a federated ``last_seen`` is the peer's
directory WRITE time (node_history stamps local rows ``now``), so it can never
be read as hearing time.
"""

import math

import pytest

from utils.peer_radio_heard import (
    coerce_radio_ts,
    peer_heard_verdict,
    peer_radio_trust,
)

NOW = 1_794_175_000.0
WIN = 2 * 3600.0


class TestCoerceRadioTs:
    @pytest.mark.parametrize("bad", [None, 0, -5, "", "x", float("nan"),
                                     float("inf"), True, [1], {"a": 1}])
    def test_not_a_hearing_time(self, bad):
        assert coerce_radio_ts(bad, NOW) is None

    def test_future_beyond_skew_is_not_a_hearing_time(self):
        assert coerce_radio_ts(NOW + 301, NOW) is None

    def test_small_skew_kept(self):
        assert coerce_radio_ts(NOW + 10, NOW) == NOW + 10

    def test_numeric_string_accepted(self):
        assert coerce_radio_ts(str(NOW - 60), NOW) == NOW - 60


class TestPeerRadioTrust:
    def test_old_code_peer_exports_nothing(self):
        ok, reason = peer_radio_trust(False, None, NOW, WIN)
        assert ok is False and "does not export" in reason

    def test_exports_but_no_radio_rows(self):
        ok, reason = peer_radio_trust(True, None, NOW, WIN)
        assert ok is False and "no radio" in reason

    def test_frozen_clock_peer_is_untrusted(self):
        # moc2 10-26..11-08: every lastHeard frozen ~478 h back
        ok, reason = peer_radio_trust(True, NOW - 478 * 3600, NOW, WIN)
        assert ok is False and "frozen clock or deaf" in reason

    def test_future_newest_is_untrusted(self):
        ok, reason = peer_radio_trust(True, NOW + 3600, NOW, WIN)
        assert ok is False and "ahead" in reason

    def test_live_peer_trusted(self):
        ok, _ = peer_radio_trust(True, NOW - 30, NOW, WIN)
        assert ok is True

    def test_boundary_exactly_window_old_is_untrusted(self):
        # strict `<` like the map's own online judgement
        ok, _ = peer_radio_trust(True, NOW - WIN, NOW, WIN)
        assert ok is False


class TestPeerHeardVerdict:
    def _trust(self, **kw):
        return {p: v for p, v in kw.items()}

    def test_no_claims_returns_none(self):
        assert peer_heard_verdict({}, {}, {}, NOW, WIN) is None

    def test_acceptance_1_heard_by_peer_minutes(self):
        v = peer_heard_verdict(
            {"10.0.0.2": NOW - 420},
            {"10.0.0.2": (True, "")},
            {"10.0.0.2": "moc2"}, NOW, WIN)
        assert v["state"] == "heard"
        assert v["by"] == "moc2"
        assert v["ts"] == NOW - 420
        assert v["age_s"] == 420

    def test_acceptance_2_frozen_clock_peer_is_unobservable(self):
        v = peer_heard_verdict(
            {"moc2": NOW - 60},  # a fresh-LOOKING row from an untrusted peer
            {"moc2": (False, "radio heard nothing in 2 h (frozen clock or deaf)")},
            {}, NOW, WIN)
        assert v["state"] == "unobservable"
        assert v["ts"] is None
        assert "frozen clock" in v["reason"]
        assert v["by"] == "moc2"

    def test_trusted_peer_old_hearing_is_not_heard(self):
        v = peer_heard_verdict({"moc2": NOW - 3 * 3600},
                               {"moc2": (True, "")}, {}, NOW, WIN)
        assert v["state"] == "not_heard"
        assert v["ts"] == NOW - 3 * 3600

    def test_newest_trusted_peer_wins_untrusted_ignored(self):
        v = peer_heard_verdict(
            {"a": NOW - 900, "b": NOW - 60, "frozen": NOW - 5},
            {"a": (True, ""), "b": (True, ""), "frozen": (False, "x")},
            {"a": "kiai"}, NOW, WIN)
        assert v["state"] == "heard" and v["by"] == "b" and v["ts"] == NOW - 60
        assert v["unverified"] == "frozen: x"   # fresher but untrusted — said, not dropped

    def test_peer_missing_from_trust_map_is_untrusted(self):
        v = peer_heard_verdict({"ghost": NOW - 5}, {}, {}, NOW, WIN)
        assert v["state"] == "unobservable"

    def test_invalid_claim_ts_dropped(self):
        v = peer_heard_verdict({"a": float("nan"), "b": NOW + 9999},
                               {"a": (True, ""), "b": (True, "")}, {}, NOW, WIN)
        assert v is None

    def test_age_is_finite_int(self):
        v = peer_heard_verdict({"a": NOW - 1.6}, {"a": (True, "")}, {}, NOW, WIN)
        assert isinstance(v["age_s"], int) and math.isfinite(v["age_s"])

    def test_nonfinite_now_or_window_never_raises_and_never_heard(self):
        v = peer_heard_verdict({"a": NOW - 5}, {"a": (True, "")}, {},
                               float("nan"), WIN)
        assert v is None or v["state"] != "heard"


# ── peer side: node_history directory carries radio_heard ──────────────────

from utils.node_history import NodeHistoryDB  # noqa: E402


def _radio_feat(nid, last_heard, origin="local_radio", stamp=True, **extra):
    props = {"id": nid, "name": nid, "network": "meshtastic",
             "source_origin": origin, "last_heard": last_heard}
    if stamp:   # as the radio drain stamps it (H14 r3)
        props["radio_last_heard"] = last_heard
    props.update(extra)
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-155.0, 19.5]},
            "properties": props}


def _dir_row(hist, nid):
    feats, pl = hist.get_directory_snapshot(include_position_less=True)
    for p in [f["properties"] for f in feats] + pl:
        if p["id"] == nid:
            return p
    raise AssertionError(f"{nid} not in directory")


class TestDirectoryRadioHeard:
    @pytest.fixture
    def hist(self, tmp_path):
        return NodeHistoryDB(db_path=tmp_path / "nh.db", retention_seconds=86400)

    def test_local_radio_row_exports_its_radio_heard(self, hist):
        import time as _t
        heard = _t.time() - 420
        hist.record_observations([_radio_feat("!ebfa1b11", heard)])
        row = _dir_row(hist, "!ebfa1b11")
        assert row["radio_heard"] == pytest.approx(heard, abs=1.0)
        # last_seen is still the write stamp — the two are different quantities
        assert row["last_seen"] > heard + 60

    def test_federated_copy_never_writes_radio_heard(self, hist):
        """A relayed row must not launder a peer's claim into OUR radio's."""
        import time as _t
        hist.record_observations([_radio_feat(
            "!relay", _t.time() - 5, federated=True, federated_from="moc2")])
        assert _dir_row(hist, "!relay")["radio_heard"] is None

    @pytest.mark.parametrize("origin", ["mqtt_local", "node_tracker",
                                        "meshcore_public", "public_fallback"])
    def test_non_radio_origin_never_writes_radio_heard(self, hist, origin):
        import time as _t
        hist.record_observations([_radio_feat("!x", _t.time() - 5, origin=origin)])
        assert _dir_row(hist, "!x")["radio_heard"] is None

    @pytest.mark.parametrize("bad", [None, 0, -1, "junk", float("nan")])
    def test_bad_last_heard_is_null(self, hist, bad):
        hist.record_observations([_radio_feat("!b", bad)])
        assert _dir_row(hist, "!b")["radio_heard"] is None

    def test_future_last_heard_is_null(self, hist):
        import time as _t
        hist.record_observations([_radio_feat("!f", _t.time() + 86400)])
        assert _dir_row(hist, "!f")["radio_heard"] is None

    def test_monotonic_and_null_never_erases(self, hist):
        import time as _t
        now = _t.time()
        hist.record_observations([_radio_feat("!m", now - 100)])
        hist.record_observations([_radio_feat("!m", now - 900)])   # older
        assert _dir_row(hist, "!m")["radio_heard"] == pytest.approx(now - 100, abs=1.0)
        hist.record_observations([_radio_feat("!m", None)])        # no stamp
        assert _dir_row(hist, "!m")["radio_heard"] == pytest.approx(now - 100, abs=1.0)
        hist.record_observations([_radio_feat("!m", now - 10)])    # newer
        assert _dir_row(hist, "!m")["radio_heard"] == pytest.approx(now - 10, abs=1.0)

    def test_pre_h14_db_migrates_and_keeps_rows(self, tmp_path):
        import sqlite3
        db = tmp_path / "old.db"
        h = NodeHistoryDB(db_path=db, retention_seconds=86400)
        h.record_observations([_radio_feat("!old", 1.0)])
        con = sqlite3.connect(db)
        con.execute("ALTER TABLE nodes DROP COLUMN radio_heard")
        con.commit()
        con.close()
        h2 = NodeHistoryDB(db_path=db, retention_seconds=86400)
        assert _dir_row(h2, "!old")["radio_heard"] is None


# ── federation ingest: per-peer radio claims survive the cross-peer merge ──

import time as _time  # noqa: E402
from unittest.mock import patch  # noqa: E402

from utils.map_federation import (  # noqa: E402
    FederationCollector, FederationPeerStatus, _extract_features)


def _payload(rows):
    return {"features": [], "nodes_without_position": rows}


class TestFederationIngest:
    def test_extract_carries_radio_heard_only_when_exported(self):
        new = _extract_features(_payload([
            {"id": "!a", "network": "meshtastic", "last_seen": 5,
             "radio_heard": 123.0}]), "moc2")
        old = _extract_features(_payload([
            {"id": "!a", "network": "meshtastic", "last_seen": 5}]), "moc2")
        assert new[0]["radio_heard"] == 123.0
        assert "radio_heard" not in old[0]

    @pytest.mark.parametrize("junk", [True, "x", float("nan"), -3, [1]])
    def test_extract_junk_radio_heard_is_none_but_exported(self, junk):
        out = _extract_features(_payload([
            {"id": "!a", "network": "meshtastic", "radio_heard": junk}]), "p")
        assert "radio_heard" in out[0] and out[0]["radio_heard"] is None

    def _status(self, host):
        t = _time.time()
        return FederationPeerStatus(hostname=host, ok=True, last_sync=t,
                                    last_attempt=t, last_count=1)

    def _poll(self, responses):
        fc = FederationCollector(list(responses), poll_interval=10, timeout=1.0)
        with patch("utils.map_federation.fetch_peer_directory",
                   side_effect=lambda peer, *a, **kw: responses[peer]):
            fc.poll_once()
        return fc.get_snapshot()

    def test_every_peer_claim_kept_whichever_entry_wins(self):
        now = _time.time()
        a = {"network": "meshtastic", "id": "!n", "last_seen": now,       # wins
             "source_origin": "local_radio", "federated_from": "a",
             "radio_heard": now - 7200 * 3}
        b = {"network": "meshtastic", "id": "!n", "last_seen": now - 50,
             "source_origin": "local_radio", "federated_from": "b",
             "radio_heard": now - 60}
        snap = self._poll({"a": ([a], self._status("a")),
                           "b": ([b], self._status("b"))})
        e = snap.by_node[("meshtastic", "!n")]
        assert e["radio_heard_by"] == {"a": now - 7200 * 3, "b": now - 60}

    def test_peer_status_records_export_and_newest(self):
        now = _time.time()
        rows = [{"network": "meshtastic", "id": f"!{i}", "radio_heard": now - i * 10,
                 "source_origin": "local_radio", "federated_from": "a"}
                for i in range(1, 4)]
        rows.append({"network": "rns", "id": "r", "radio_heard": None,
                     "federated_from": "a"})
        snap = self._poll({"a": (rows, self._status("a")),
                           "old": ([{"network": "rns", "id": "q",
                                     "federated_from": "old"}],
                                   self._status("old"))})
        assert snap.peer_status["a"].radio_exported is True
        assert snap.peer_status["a"].radio_newest_by_network == {
            "meshtastic": pytest.approx(now - 10, abs=1.0)}
        assert snap.peer_status["old"].radio_exported is False
        assert snap.peer_status["old"].radio_newest_by_network == {}
        assert "radio_heard_by" not in snap.by_node[("rns", "q")] or \
            snap.by_node[("rns", "q")]["radio_heard_by"] == {}


# ── consumer: the manager map attaches peer_heard; is_online untouched ─────

from unittest.mock import MagicMock  # noqa: E402

from utils.map_data_collector import MapDataCollector  # noqa: E402
from utils.map_federation import FederationSnapshot  # noqa: E402


@pytest.fixture
def collector(tmp_path):
    home = tmp_path / "home"
    cfg = home / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    with patch("utils.map_data_collector.get_real_user_home", return_value=home):
        return MapDataCollector(cache_dir=tmp_path / "cache",
                                enable_history=False, config_dir=cfg)


def _stub(collector, by_node, statuses):
    fc = MagicMock(spec=FederationCollector)
    fc.peers = list(statuses)
    fc.get_snapshot.return_value = FederationSnapshot(
        by_node=by_node, peer_status=statuses, last_sync=1, last_attempt=1)
    collector._federation = fc


def _st(host, name, newest, exported=True, net="meshtastic", **kw):
    return FederationPeerStatus(
        hostname=host, peer_name=name, ok=kw.pop("ok", True),
        radio_exported=exported,
        radio_newest_by_network={net: newest} if newest is not None else {}, **kw)


class TestManagerMapPeerHeard:
    def test_acceptance_1_local_stub_shows_heard_by_moc2(self, collector):
        """moc3's 204-day local tracker stub keeps is_online False (THIS radio
        did not hear it) but reads heard by moc2 minutes ago."""
        now = _time.time()
        _stub(collector, {("meshtastic", "!ebfa1b11"): {
            "id": "!ebfa1b11", "network": "meshtastic", "lat": None, "lon": None,
            "last_seen": now - 263, "source_origin": "local_radio",
            "federated_from": "10.0.0.2", "seen_by_peers": ["10.0.0.2"],
            "radio_heard_by": {"10.0.0.2": now - 420}}},
            {"10.0.0.2": _st("10.0.0.2", "moc2", now - 30)})
        features = {"!ebfa1b11": {"type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-155, 19.5]},
                    "properties": {"id": "!ebfa1b11", "network": "meshtastic",
                                   "source_origin": "local_radio",
                                   "last_heard": now - 204 * 86400,
                                   "is_online": False}}}
        block = collector._merge_federation(features, [])
        props = features["!ebfa1b11"]["properties"]
        assert props["is_online"] is False
        ph = props["peer_heard"]
        assert ph["state"] == "heard" and ph["by"] == "moc2"
        assert 400 <= ph["age_s"] <= 440
        assert block["total"] == 0          # local still wins the marker
        row = block["peer_status"][0]
        assert row["radio_witness"]["meshtastic"]["ok"] is True

    def test_acceptance_2_frozen_clock_peer_reads_unobservable(self, collector):
        now = _time.time()
        _stub(collector, {("meshtastic", "!n"): {
            "id": "!n", "network": "meshtastic", "lat": 19.5, "lon": -155.0,
            "last_seen": now - 30, "source_origin": "local_radio",
            "federated_from": "moc2", "seen_by_peers": ["moc2"],
            "radio_heard_by": {"moc2": now - 478 * 3600}}},
            {"moc2": _st("moc2", "moc2", now - 478 * 3600)})
        features: dict = {}
        collector._merge_federation(features, [])
        (feat,) = features.values()
        ph = feat["properties"]["peer_heard"]
        assert ph["state"] == "unobservable"
        assert "frozen clock" in ph["reason"]
        assert feat["properties"]["is_online"] is False

    def test_old_code_peer_attaches_nothing(self, collector):
        now = _time.time()
        _stub(collector, {("rns", "r"): {
            "id": "r", "network": "rns", "lat": None, "lon": None,
            "last_seen": now, "source_origin": "rns_path_table",
            "federated_from": "old", "seen_by_peers": ["old"]}},
            {"old": _st("old", None, None, exported=False)})
        pl: list = []
        collector._merge_federation({}, pl)
        assert "peer_heard" not in pl[0]

    def test_position_less_local_winner_gets_peer_heard(self, collector):
        now = _time.time()
        _stub(collector, {("meshtastic", "!p"): {
            "id": "!p", "network": "meshtastic", "lat": None, "lon": None,
            "last_seen": now, "source_origin": "local_radio",
            "federated_from": "a", "seen_by_peers": ["a"],
            "radio_heard_by": {"a": now - 100}}},
            {"a": _st("a", "kiai", now - 5)})
        pl = [{"id": "!p", "network": "meshtastic", "source_origin": "local_radio"}]
        collector._merge_federation({}, pl)
        assert len(pl) == 1 and pl[0]["peer_heard"]["by"] == "kiai"


# ── page: ONE reader says it; popup escapes it ──────────────────────────────

import os  # noqa: E402
import re  # noqa: E402

_HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                          "web", "node_map.html"), encoding="utf-8").read()


def _js_body(name):
    m = re.search(r"function %s\(([^)]*)\)\s*\{" % name, _HTML)
    assert m, name
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(_HTML[i], 0)
        i += 1
    return _HTML[m.end():i]


class TestPageSaysPeerHeard:
    def test_status_reader_appends_peer_heard_for_federated_and_not_online(self):
        body = _js_body("nodeStatusText")
        assert body.count("peerHeardText(props.peer_heard)") == 2
        # an online node never gets a peer clause: this radio is the better witness
        assert "props.is_online ? '' : peerHeardText" in body

    def test_peer_text_names_every_state_and_reads_ts_not_last_seen(self):
        body = _js_body("peerHeardText")
        for state in ("'unobservable'", "'heard'", "'not_heard'"):
            assert state in body
        assert "ph.age_s" in body and "last_seen" not in body
        assert "isFinite(age)" in body

    def test_popup_escapes_the_status_line(self):
        assert "${escHtml(nodeStatusText(props))}" in _HTML



# ── review round 1 (code-review medium, 11-08) — one pin per finding ───────

class TestReviewRound1:
    @pytest.fixture
    def hist(self, tmp_path):
        return NodeHistoryDB(db_path=tmp_path / "nh.db", retention_seconds=86400)

    def test_f1_gps_less_node_reaches_the_directory_with_radio_heard(self, tmp_path):
        """The position-less path (most Meshtastic nodes) must carry last_heard
        end to end: parser dict -> collect's history feature -> radio_heard."""
        import utils.map_data_collector as mdc
        home = tmp_path / "home"
        cfg = home / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        with patch("utils.map_data_collector.get_real_user_home", return_value=home):
            c = MapDataCollector(cache_dir=tmp_path / "cache",
                                 enable_history=True, config_dir=cfg)
        now = _time.time()
        _, nopos, _ = c._drain_interface_nodes(_FakeIface({"!c0ffee01": {
            "num": 0xC0FFEE01, "lastHeard": now - 300,
            "user": {"id": "!c0ffee01", "longName": "nogps"}}}, my_num=1), "t")
        (entry,) = nopos
        assert entry["radio_last_heard"] == now - 300

        def only(self, name, fn, *a, **kw):
            if name == "meshtasticd":           # the radio answered: no GPS
                self._nodes_without_position.append(dict(entry))
            return []

        with patch.object(MapDataCollector, "_timed_collect", only), \
             patch.object(MapDataCollector, "_merge_federation",
                          lambda self, f, pl, *a, **k: {}):
            c.collect(max_age_seconds=0)
        row = _dir_row(c._history, "!c0ffee01")
        assert row["radio_heard"] == pytest.approx(now - 300, abs=1.0)
        assert mdc  # imported for the patch target's module

    def test_f2_mqtt_downlinked_node_is_not_this_radios_hearing(self, hist):
        hist.record_observations([_radio_feat("!mq", _time.time() - 5, via_mqtt=True)])
        assert _dir_row(hist, "!mq")["radio_heard"] is None

    def test_f3_fresh_meshcore_row_does_not_vouch_for_frozen_meshtastic(self, collector):
        now = _time.time()
        st = FederationPeerStatus(
            hostname="moc2", peer_name="moc2", ok=True, radio_exported=True,
            radio_newest_by_network={"meshtastic": now - 478 * 3600,
                                     "meshcore": now - 10})
        _stub(collector, {("meshtastic", "!n"): {
            "id": "!n", "network": "meshtastic", "lat": None, "lon": None,
            "last_seen": now, "source_origin": "local_radio",
            "federated_from": "moc2", "seen_by_peers": ["moc2"],
            "radio_heard_by": {"moc2": now - 2 * 86400}}}, {"moc2": st})
        pl: list = []
        collector._merge_federation({}, pl)
        assert pl[0]["peer_heard"]["state"] == "unobservable"

    def test_f3_ingest_newest_is_per_network(self):
        now = _time.time()
        rows = [{"network": "meshtastic", "id": "!a", "radio_heard": now - 9000,
                 "federated_from": "p"},
                {"network": "meshcore", "id": "mc", "radio_heard": now - 5,
                 "federated_from": "p"}]
        fc = FederationCollector(["p"], poll_interval=10, timeout=1.0)
        t = _time.time()
        st = FederationPeerStatus(hostname="p", ok=True, last_sync=t, last_attempt=t)
        with patch("utils.map_federation.fetch_peer_directory", return_value=(rows, st)), \
             patch("utils.map_federation._fetch_peer_claw", return_value=None):
            fc.poll_once()
        got = fc.get_snapshot().peer_status["p"].radio_newest_by_network
        assert got == {"meshtastic": pytest.approx(now - 9000, abs=1.0),
                       "meshcore": pytest.approx(now - 5, abs=1.0)}

    def test_f4_window_is_the_entry_networks(self, collector):
        now = _time.time()
        collector._settings.set("rns_threshold_minutes", 30)
        _stub(collector, {("rns", "r1"): {
            "id": "r1", "network": "rns", "lat": None, "lon": None,
            "last_seen": now, "source_origin": "rns_path_table",
            "federated_from": "a", "seen_by_peers": ["a"],
            "radio_heard_by": {"a": now - 45 * 60}}},
            {"a": _st("a", "kiai", now - 60, net="rns")})
        pl: list = []
        collector._merge_federation({}, pl)
        assert pl[0]["peer_heard"]["state"] == "not_heard"   # 45 min > rns 30 min

    def test_f5_clock_ahead_peer_is_named_not_no_rows(self):
        out = _extract_features(_payload([
            {"id": "!a", "network": "meshtastic", "radio_heard": _time.time() + 3600}]), "p")
        assert out[0]["radio_heard"] is not None          # kept for naming
        ok, reason = peer_radio_trust(True, out[0]["radio_heard"], _time.time(), WIN)
        assert ok is False and "ahead" in reason
        # ...and still never read as a hearing time
        assert peer_heard_verdict({"p": out[0]["radio_heard"]}, {"p": (True, "")},
                                  {}, _time.time(), WIN) is None

    def test_f6_page_renders_the_servers_age_not_the_browsers(self):
        body = _js_body("peerHeardText")
        assert "ph.age_s" in body and "Date.now" not in body

    @pytest.mark.parametrize("kw,want", [({"ok": False}, "fetch failed"),
                                         ({"in_backoff": True}, "backoff")])
    def test_f7_failed_or_backoff_peer_is_said_as_such(self, kw, want):
        ok, reason = peer_radio_trust(False, None, NOW, WIN, **kw)
        assert ok is False and want in reason and "old code" not in reason

    def test_f8_every_untrusted_claimer_is_named(self):
        v = peer_heard_verdict(
            {"alaula": NOW - 5, "moc2": NOW - 9},
            {"alaula": (False, "old code"), "moc2": (False, "frozen clock")},
            {}, NOW, WIN)
        assert v["state"] == "unobservable"
        assert "alaula" in v["by"] and "moc2" in v["by"]
        assert "old code" in v["reason"] and "frozen clock" in v["reason"]

    def test_f8_fresher_untrusted_claim_is_surfaced_beside_not_heard(self):
        v = peer_heard_verdict(
            {"a": NOW - 5 * 3600, "b": NOW - 600},
            {"a": (True, ""), "b": (False, "frozen clock")}, {}, NOW, WIN)
        assert v["state"] == "not_heard" and v["by"] == "a"
        assert "b" in v["unverified"] and "frozen clock" in v["unverified"]
        assert "ph.unverified" in _js_body("peerHeardText")


# ── review round 2 (Fable by running, 11-08) ───────────────────────────────

class TestReviewRound2:
    def test_f2_federated_heard_never_reads_online(self, collector):
        now = _time.time()
        _stub(collector, {("meshtastic", "!h"): {
            "id": "!h", "network": "meshtastic", "lat": 19.5, "lon": -155.0,
            "last_seen": now, "source_origin": "local_radio",
            "federated_from": "a", "seen_by_peers": ["a"],
            "radio_heard_by": {"a": now - 30}}},
            {"a": _st("a", "kiai", now - 5)})
        features: dict = {}
        collector._merge_federation(features, [])
        (feat,) = features.values()
        assert feat["properties"]["peer_heard"]["state"] == "heard"
        assert feat["properties"]["is_online"] is False

    def test_f2_local_heard_never_flips_is_online(self, collector):
        now = _time.time()
        _stub(collector, {("meshtastic", "!l"): {
            "id": "!l", "network": "meshtastic", "lat": None, "lon": None,
            "last_seen": now, "source_origin": "local_radio",
            "federated_from": "a", "seen_by_peers": ["a"],
            "radio_heard_by": {"a": now - 30}}},
            {"a": _st("a", "kiai", now - 5)})
        features = {"!l": {"type": "Feature", "geometry": {"type": "Point",
                    "coordinates": [-155, 19.5]}, "properties": {
                    "id": "!l", "network": "meshtastic",
                    "source_origin": "local_radio", "is_online": False}}}
        collector._merge_federation(features, [])
        assert features["!l"]["properties"]["peer_heard"]["state"] == "heard"
        assert features["!l"]["properties"]["is_online"] is False

    def test_f3_popup_row_and_sidebar_say_whose_radio(self):
        assert '<div class="info-label">Last heard here</div>' in _HTML
        side = _HTML[_HTML.index("const lastSeen = node.last_seen || '';"):][:900]
        assert "peerHeardText(node.peer_heard)" in side
        assert "${escHtml(name)}" in side and "${escHtml(lastSeen)}" in side

    def test_f4_http_no_position_entry_carries_via_mqtt(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "src", "utils", "_map_collector_meshtastic.py"),
                   encoding="utf-8").read()
        block = src[src.index("no_position_nodes.append({"):][:700]
        assert '"via_mqtt": node.via_mqtt' in block

    def test_verdict_boundary_is_strict(self):
        v = peer_heard_verdict({"a": NOW - WIN}, {"a": (True, "")}, {}, NOW, WIN)
        assert v["state"] == "not_heard"
        v = peer_heard_verdict({"a": NOW - WIN + 1}, {"a": (True, "")}, {}, NOW, WIN)
        assert v["state"] == "heard"

    @pytest.mark.parametrize("order", [("a", "b"), ("b", "a")])
    def test_claims_kept_in_either_completion_order(self, order):
        """r2: as_completed order made the winner-drop mutant 1-in-3; pin both."""
        now = _time.time()
        rows = {
            "a": {"network": "meshtastic", "id": "!n", "last_seen": now,
                  "source_origin": "local_radio", "federated_from": "a",
                  "radio_heard": now - 9000},
            "b": {"network": "meshtastic", "id": "!n", "last_seen": now - 50,
                  "source_origin": "local_radio", "federated_from": "b",
                  "radio_heard": now - 60},
        }
        t = _time.time()
        resp = {p: ([dict(rows[p])], FederationPeerStatus(
            hostname=p, ok=True, last_sync=t, last_attempt=t)) for p in rows}
        fc = FederationCollector(list(order), poll_interval=10, timeout=1.0)

        def in_order(futs):
            by_peer = {futs[f]: f for f in futs}
            return [by_peer[p] for p in order]

        with patch("utils.map_federation.fetch_peer_directory",
                   side_effect=lambda peer, *a, **kw: resp[peer]), \
             patch("utils.map_federation._fetch_peer_claw", return_value=None), \
             patch("utils.map_federation.as_completed", side_effect=in_order):
            fc.poll_once()
        got = fc.get_snapshot().by_node[("meshtastic", "!n")]["radio_heard_by"]
        assert got == {"a": pytest.approx(now - 9000, abs=1.0),
                       "b": pytest.approx(now - 60, abs=1.0)}


# ── review round 3 (Fable by running, 11-08) ───────────────────────────────

class _FakeIface:
    def __init__(self, nodes, my_num=None, local_num=None):
        self.nodes = nodes
        if my_num is not None:
            self.myInfo = type("MI", (), {"my_node_num": my_num})()
        if local_num is not None:
            self.localNode = type("LN", (), {"nodeNum": local_num})()


def _nodedb(num, age, via_mqtt=False, pos=True):
    d = {"num": num, "lastHeard": _time.time() - age, "viaMqtt": via_mqtt,
         "user": {"id": "!%08x" % num, "longName": "n%x" % num}}
    if pos:
        d["position"] = {"latitude": 19.5, "longitude": -155.0}
    return d


class TestReviewRound3:
    def _drain(self, collector, iface):
        feats, nopos, _ = collector._drain_interface_nodes(iface, "test")
        by = {f["properties"]["id"]: f["properties"] for f in feats}
        by.update({e["id"]: e for e in nopos})
        return by

    def test_radio_stamp_is_set_only_by_the_radio_drain(self, collector):
        by = self._drain(collector, _FakeIface({
            "!0000aaa1": _nodedb(0xaaa1, 300),
            "!0000aaa2": _nodedb(0xaaa2, 400, pos=False),
            "!0000aaa3": _nodedb(0xaaa3, 50, via_mqtt=True)}, my_num=0xbeef))
        assert by["!0000aaa1"]["radio_last_heard"] == pytest.approx(_time.time() - 300, abs=2)
        assert by["!0000aaa2"]["radio_last_heard"] == pytest.approx(_time.time() - 400, abs=2)
        assert by["!0000aaa3"].get("radio_last_heard") is None   # MQTT-only

    @pytest.mark.parametrize("kw", [{"my_num": 0xddfb8065},
                                    {"local_num": 0xddfb8065}])
    def test_f6_own_node_is_tagged_from_the_connection_never_stamped(self, collector, kw):
        by = self._drain(collector, _FakeIface({
            "!ddfb8065": _nodedb(0xddfb8065, 1),
            "!0000aaa1": _nodedb(0xaaa1, 300)}, **kw))
        assert by["!ddfb8065"]["is_self"] is True
        assert by["!ddfb8065"].get("radio_last_heard") is None
        assert by["!0000aaa1"].get("is_self") is not True

    def test_f6_unknown_own_id_is_not_inferred(self, collector):
        by = self._drain(collector, _FakeIface({"!ddfb8065": _nodedb(0xddfb8065, 1)}))
        assert "is_self" not in by["!ddfb8065"]

    def test_f10_fresher_mqtt_copy_never_moves_the_radio_time(self, tmp_path):
        """collect(): radio heard it 3 d ago, MQTT 60 s ago -> radio_heard = 3 d."""
        home = tmp_path / "home"
        cfg = home / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        with patch("utils.map_data_collector.get_real_user_home", return_value=home):
            c = MapDataCollector(cache_dir=tmp_path / "cache",
                                 enable_history=True, config_dir=cfg)
        radio, _, _ = c._drain_interface_nodes(
            _FakeIface({"!0000abcd": _nodedb(0xabcd, 3 * 86400)}, my_num=1), "t")
        mqtt = [{"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [-155.0, 19.5]},
                 "properties": {"id": "!0000abcd", "network": "meshtastic",
                                "via_mqtt": True, "last_heard": _time.time() - 60}}]

        def only(self, name, fn, *a, **kw):
            return {"meshtasticd": radio, "mqtt": mqtt}.get(name, [])

        with patch.object(MapDataCollector, "_timed_collect", only), \
             patch.object(MapDataCollector, "_merge_federation",
                          lambda self, f, pl, *a, **k: {}):
            c.collect(max_age_seconds=0)
        row = _dir_row(c._history, "!0000abcd")
        assert row["radio_heard"] == pytest.approx(_time.time() - 3 * 86400, abs=5)

    def test_f5_tracker_or_cache_feature_never_stamps(self, tmp_path):
        h = NodeHistoryDB(db_path=tmp_path / "t.db", retention_seconds=86400)
        h.record_observations([_radio_feat("!trk", _time.time() - 5, stamp=False)])
        assert _dir_row(h, "!trk")["radio_heard"] is None


# ── review round 4 (Fable by running, 11-08: CLEAN) — pin the redundant guards

class TestReviewRound4Guards:
    def test_directory_rejects_a_stamped_own_row(self, tmp_path):
        """The drain withholds the stamp; the directory refuses it anyway."""
        h = NodeHistoryDB(db_path=tmp_path / "g.db", retention_seconds=86400)
        h.record_observations([_radio_feat("!self", _time.time() - 5, is_self=True)])
        assert _dir_row(h, "!self")["radio_heard"] is None

    @pytest.mark.parametrize("extra", [{}, {"via_mqtt": True}, {"is_self": True}])
    def test_stampless_or_flagged_gps_less_entry_writes_nothing(self, tmp_path, extra):
        """A position-less entry with only last_heard (HTTP path) — or one the
        drain flagged — must reach the directory as NULL radio_heard."""
        home = tmp_path / "home"
        cfg = home / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        with patch("utils.map_data_collector.get_real_user_home", return_value=home):
            c = MapDataCollector(cache_dir=tmp_path / "cache",
                                 enable_history=True, config_dir=cfg)
        entry = {"id": "!0000e1e1", "name": "x", "network": "meshtastic",
                 "last_heard": _time.time() - 30, **extra}
        if extra:
            entry["radio_last_heard"] = _time.time() - 30

        def only(self, name, fn, *a, **kw):
            if name == "meshtasticd":
                self._nodes_without_position.append(dict(entry))
            return []

        with patch.object(MapDataCollector, "_timed_collect", only), \
             patch.object(MapDataCollector, "_merge_federation",
                          lambda self, f, pl, *a, **k: {}):
            c.collect(max_age_seconds=0)
        assert _dir_row(c._history, "!0000e1e1")["radio_heard"] is None
