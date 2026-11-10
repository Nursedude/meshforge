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

from tests._federation_net_guard import _no_real_peer_http  # noqa: F401 — autouse

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

    def test_small_future_skew_is_null_too(self, hist):
        """coerce_radio_ts keeps <= 300 s of skew; the directory still refuses
        to STORE a hearing time after now (a mutant dropping that guard
        survived the +1 d test — found porting to MeshAnchor, 2026-11-09)."""
        import time as _t
        hist.record_observations([_radio_feat("!s", _t.time() + 120)])
        assert _dir_row(hist, "!s")["radio_heard"] is None

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
             patch("utils.map_federation._fetch_peer_status_extras", return_value=(None, None)):
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
             patch("utils.map_federation._fetch_peer_status_extras", return_value=(None, None)), \
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


class TestOnePeerStatusSerializer:
    """The /api/status federation block and the geojson federation block
    are two consumers of one FederationPeerStatus. They drifted: /api/status
    lacked radio_newest_by_network / radio_witness (H14), and geojson lacked
    resolution_method. ONE serializer + ONE envelope now feed both (hfm #5)."""

    def _peer(self, now):
        return _st("10.0.0.2", "moc2", now - 30, resolution_method="dns",
                   claw={"installed": True})

    def test_status_block_carries_radio_witness(self, collector):
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        _stub(collector, {}, {"10.0.0.2": self._peer(now)})
        block = build_federation_status(collector, now=now)
        (row,) = block["peer_status"]
        assert row["radio_newest_by_network"]["meshtastic"] == pytest.approx(now - 30)
        assert row["radio_witness"]["meshtastic"] == {"ok": True, "reason": ""}
        assert row["claw"] == {"installed": True}     # /api/status keeps claw

    def test_status_block_says_why_not_a_witness(self, collector):
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        _stub(collector, {}, {"moc2": _st("moc2", "moc2", now - 478 * 3600)})
        (row,) = build_federation_status(collector, now=now)["peer_status"]
        assert row["radio_witness"]["meshtastic"]["ok"] is False
        assert "frozen clock" in row["radio_witness"]["meshtastic"]["reason"]

    def test_both_builders_emit_the_same_row_and_envelope(self, collector):
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        _stub(collector, {}, {"10.0.0.2": self._peer(now)})
        status = build_federation_status(collector, now=now)
        geo = collector._merge_federation({}, [])
        status_row = dict(status["peer_status"][0])
        assert status_row.pop("claw") == {"installed": True}
        assert status_row == geo["peer_status"][0]
        assert "claw" not in geo["peer_status"][0]   # hot payload stays lean
        assert geo["peer_status"][0]["resolution_method"] == "dns"
        for k in ("enabled", "peers", "last_sync", "last_attempt",
                  "non_federating"):
            assert status[k] == geo[k], k

    def test_threshold_raise_keeps_every_row(self, collector):
        """review 11-08 F1: the witness is optional — a window lookup that
        raises must not erase peer_status (mini reads [] as a quiet fleet)."""
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        _stub(collector, {}, {"a": _st("a", "a", now - 30),
                              "b": _st("b", "b", None, ok=False)})
        with patch.object(collector, "get_source_threshold_seconds",
                          side_effect=TypeError("bad window")):
            block = build_federation_status(collector, now=now)
        assert "error" not in block
        rows = {r["hostname"]: r for r in block["peer_status"]}
        assert set(rows) == {"a", "b"}
        w = rows["a"]["radio_witness"]["meshtastic"]
        assert w == {"ok": False, "reason": "witness window unreadable on this box"}
        assert rows["b"]["ok"] is False                 # health row survives

    def test_unknown_network_counted_not_judged(self, collector):
        """review 11-08 F3: a peer's foreign network names are counted, never
        judged with a default window or allowed to grow the row."""
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        st = FederationPeerStatus(
            hostname="p", peer_name="p", ok=True, radio_exported=True,
            radio_newest_by_network={"meshtastic": now - 30, "rns": now - 30,
                                     **{f"x{i}": now for i in range(50)}})
        _stub(collector, {}, {"p": st})
        (row,) = build_federation_status(collector, now=now)["peer_status"]
        assert set(row["radio_witness"]) == {"meshtastic", "rns"}
        assert set(row["radio_newest_by_network"]) == {"meshtastic", "rns"}
        assert row["radio_unknown_networks"] == 50

    def test_status_block_disabled_and_error_shapes_kept(self, collector):
        from utils._map_status_endpoints import build_federation_status
        collector._federation = None
        assert build_federation_status(collector)["enabled"] is False
        assert build_federation_status(None)["peer_status"] == []
        fc = MagicMock(spec=FederationCollector)
        fc.get_snapshot.side_effect = RuntimeError("boom")
        collector._federation = fc
        err = build_federation_status(collector)
        assert err["enabled"] is True and "boom" in err["error"]
        assert "non_federating" in err


class TestThresholdSettingValidation:
    """review 11-08 F2: `"120" * 60` is a 180-digit window that passes every
    frozen peer as a witness. Unusable settings fall back to the default."""

    @pytest.mark.parametrize("bad", ["120", True, None, float("nan"),
                                     float("inf"), 0, -5, [120]])
    def test_bad_value_uses_source_default(self, collector, bad):
        default = collector.get_source_threshold_seconds("meshtastic")
        collector._settings.set("meshtastic_threshold_minutes", bad)
        assert collector.get_source_threshold_seconds("meshtastic") == default

    def test_good_value_still_honoured(self, collector):
        collector._settings.set("meshtastic_threshold_minutes", 30)
        assert collector.get_source_threshold_seconds("meshtastic") == 1800
        collector._settings.set("meshtastic_threshold_minutes", 2.5)
        assert collector.get_source_threshold_seconds("meshtastic") == 150

    def test_vocabulary_is_the_defaults(self, collector):
        assert set(collector.threshold_sources()) == {
            "meshtastic", "mqtt", "rns", "aredn", "public_fallback"}


class TestReviewRound2Seam:
    """review 11-08 r2 (Fable by running): one witness vocabulary for the
    node verdict and the peer row; per-peer trust; per-source windows."""

    @pytest.fixture(autouse=True)
    def _fresh_warn_set(self):
        import utils._map_collector_config as cfg
        cfg._WARNED_BAD_THRESHOLD.clear()
        yield
        cfg._WARNED_BAD_THRESHOLD.clear()

    def test_meshcore_refused_by_verdict_and_row_alike(self, collector):
        """r2 #1: the verdict judged meshcore 'heard' with a default window
        while the row called the network unknown."""
        now = _time.time()
        _stub(collector, {("meshcore", "meshcore:ab"): {
            "id": "meshcore:ab", "network": "meshcore", "lat": 19.5,
            "lon": -155.0, "last_seen": now - 30, "source_origin": "local_radio",
            "federated_from": "ma1", "seen_by_peers": ["ma1"],
            "radio_heard_by": {"ma1": now - 30}}},
            {"ma1": _st("ma1", "ma1", now - 30, net="meshcore")})
        features: dict = {}
        block = collector._merge_federation(features, [])
        (feat,) = features.values()
        ph = feat["properties"]["peer_heard"]
        assert ph["state"] == "unobservable"
        assert "no online window" in ph["reason"]
        row = block["peer_status"][0]
        assert row["radio_unknown_networks"] == 1
        assert "meshcore" not in row["radio_witness"]

    def test_radio_less_peer_still_gets_meshtastic_row(self, collector):
        """r2 mutant F: the implicit meshtastic row is the answer to 'is
        this peer a witness at all' for a peer with no rows."""
        from utils._map_status_endpoints import build_federation_status
        _stub(collector, {}, {"p": _st("p", "p", None)})
        (row,) = build_federation_status(collector)["peer_status"]
        assert row["radio_witness"]["meshtastic"] == {
            "ok": False, "reason": "peer has no radio-heard rows on this network"}

    def test_status_judges_each_network_with_its_own_window(self, collector):
        """r2 mutant J: rns (30 min) heard 45 min ago is stale, while the
        same age on meshtastic (120 min) is fresh."""
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        st = FederationPeerStatus(
            hostname="p", peer_name="p", ok=True, radio_exported=True,
            radio_newest_by_network={"meshtastic": now - 2700, "rns": now - 2700})
        _stub(collector, {}, {"p": st})
        (row,) = build_federation_status(collector, now=now)["peer_status"]
        assert row["radio_witness"]["meshtastic"]["ok"] is True
        assert row["radio_witness"]["rns"]["ok"] is False

    def test_two_peers_keep_their_own_trust_in_both_builders(self, collector):
        """r2 mutant K: trust_for must key on the peer, not the first one."""
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        _stub(collector, {}, {"fresh": _st("fresh", "fresh", now - 30),
                              "frozen": _st("frozen", "frozen", now - 478 * 3600)})
        status = {r["hostname"]: r for r in
                  build_federation_status(collector, now=now)["peer_status"]}
        geo = {r["hostname"]: r for r in
               collector._merge_federation({}, [])["peer_status"]}
        for rows in (status, geo):
            assert rows["fresh"]["radio_witness"]["meshtastic"]["ok"] is True
            assert rows["frozen"]["radio_witness"]["meshtastic"]["ok"] is False

    @pytest.mark.parametrize("source,default_min", [
        ("rns", 30), ("aredn", 60), ("mqtt", 15), ("public_fallback", 240)])
    def test_bad_value_falls_back_to_THAT_sources_default(
            self, collector, source, default_min):
        """r2 mutant M: the fallback is the source's own default."""
        collector._settings.set(f"{source}_threshold_minutes", "120")
        assert collector.get_source_threshold_seconds(source) == default_min * 60

    def test_global_online_threshold_validated_too(self, collector):
        """r2 #2: the sibling getter had the same `"120" * 60` defect."""
        collector._settings.set("online_status_threshold_minutes", "120")
        assert collector.get_online_threshold_seconds() == \
            collector.DEFAULT_ONLINE_THRESHOLD_MINUTES * 60

    def test_warns_once_per_distinct_bad_value(self, collector, caplog):
        """r2 #3 / mutant L: the WARNING is the witness; a second distinct
        bad value on the same key warns again, a repeat does not."""
        import logging
        caplog.set_level(logging.WARNING, logger="utils._map_collector_config")
        key = "meshtastic_threshold_minutes"
        for bad in ("120", "120", None):
            collector._settings.set(key, bad)
            collector.get_source_threshold_seconds("meshtastic")
        lines = [r for r in caplog.records if key in r.getMessage()]
        assert len(lines) == 2


class TestReviewRound3Pins:
    """review 11-08 r3 (Fable by running): validator upper bound; the two
    `known` arguments and the poll-state reasons pinned THROUGH the seam."""

    @pytest.fixture(autouse=True)
    def _fresh_warn_set(self):
        import utils._map_collector_config as cfg
        cfg._WARNED_BAD_THRESHOLD.clear()
        yield
        cfg._WARNED_BAD_THRESHOLD.clear()

    @pytest.mark.parametrize("huge", [1e308, 10 ** 400, 1e30,
                                      365 * 24 * 60 + 1])
    def test_absurd_window_refused_not_crashed(self, collector, huge):
        default = collector.get_source_threshold_seconds("meshtastic")
        collector._settings.set("meshtastic_threshold_minutes", huge)
        assert collector.get_source_threshold_seconds("meshtastic") == default
        collector._settings.set("online_status_threshold_minutes", huge)
        assert collector.get_online_threshold_seconds() == \
            collector.DEFAULT_ONLINE_THRESHOLD_MINUTES * 60

    def test_one_year_is_still_honoured(self, collector):
        collector._settings.set("meshtastic_threshold_minutes", 365 * 24 * 60)
        assert collector.get_source_threshold_seconds("meshtastic") == \
            365 * 24 * 3600

    def test_known_non_meshtastic_network_can_be_a_witness_in_both(self, collector):
        """r3 mutant M: an empty `known` passed to the trust call (but not to
        the serializer) refused rns silently. rns heard 10 min ago is OK."""
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        _stub(collector, {}, {"p": _st("p", "p", now - 600, net="rns")})
        status = build_federation_status(collector, now=now)["peer_status"][0]
        geo = collector._merge_federation({}, [])["peer_status"][0]
        for row in (status, geo):
            assert row["radio_witness"]["rns"] == {"ok": True, "reason": ""}

    @pytest.mark.parametrize("kw,reason", [
        ({"ok": False}, "peer fetch failed this cycle"),
        ({"in_backoff": True}, "peer in federation backoff (not polled)"),
    ])
    def test_poll_state_reason_survives_the_seam(self, collector, kw, reason):
        """r3 mutants R/S: a failed or backed-off poll carries no rows, so
        radio_exported is False — the reason must still name the POLL, not
        'old code' (r1 F7)."""
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        _stub(collector, {}, {"p": _st("p", "p", None, exported=False, **kw)})
        status = build_federation_status(collector, now=now)["peer_status"][0]
        geo = collector._merge_federation({}, [])["peer_status"][0]
        for row in (status, geo):
            assert row["radio_witness"]["meshtastic"] == {"ok": False,
                                                          "reason": reason}


# ── MAP-H H14 queue item: a gateway box's map is not a radio witness BY DESIGN

import json  # noqa: E402

from utils.peer_radio_heard import DEFERRED_REASON  # noqa: E402


def _status_resp(payload):
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.status = 200
    cm.read = MagicMock(return_value=json.dumps(payload).encode("utf-8"))
    return cm


def _diag(reason):
    return {"source_diagnostics": {"meshtasticd": {
        "attempted": 0, "yielded": 0, "reason_if_zero": reason}}}


class TestGatewayDeferredWitness:
    """A box whose map defers meshtasticd to its gateway (#17) has no radio
    rows by design. The manager said "no radio-heard rows" — true but it reads
    as a fault. The peer's /api/status (already fetched each poll for the
    claw) says `deferred_to_gateway`; the witness reason now says so."""

    @pytest.mark.parametrize("payload,expected", [
        (_diag("deferred_to_gateway"), True),
        (_diag("ok"), False),
        (_diag("no_positions"), False),          # answered: vocabulary "ok"
        (_diag("collect_timeout"), None),        # leg could not RUN: unknown
        (_diag("lock_contended"), None),
        # review r2: a known reason the leg REACHED is decisive — `unreachable`
        # (meshtasticd down, gateway check already passed) is NOT deferring
        (_diag("unreachable"), False),
        (_diag("not_configured"), False),
        (_diag("stale_cached"), False),
        (_diag("no_nodes"), None),               # outside the vocabulary
        ({"source_diagnostics": {"meshtasticd": {}}}, None),
        ({"source_diagnostics": {}}, None),      # no meshtasticd leg: unknown
        ({}, None),                              # old peer: unknown
        ({"source_diagnostics": "x"}, None),     # garbage: unknown, no raise
    ])
    def test_status_extras_reads_the_deferral(self, payload, expected):
        from tests._federation_net_guard import REAL_STATUS_EXTRAS as _fetch_peer_status_extras
        with patch("utils.map_federation.urllib.request.urlopen",
                   return_value=_status_resp(payload)):
            claw, deferred = _fetch_peer_status_extras("moc", timeout=1.0)
        assert deferred is expected and claw is None

    def test_status_fetch_failure_is_unknown_not_false(self):
        from tests._federation_net_guard import REAL_STATUS_EXTRAS as _fetch_peer_status_extras
        with patch("utils.map_federation.urllib.request.urlopen",
                   side_effect=OSError("refused")):
            assert _fetch_peer_status_extras("moc", timeout=1.0) == (None, None)

    def test_one_status_fetch_feeds_claw_and_deferral(self):
        from utils.map_federation import _fetch_peer_directory_and_claw
        good = FederationPeerStatus(hostname="moc", ok=True)
        payload = {**_diag("deferred_to_gateway"),
                   "claw": {"installed": True, "device": "c1"}}
        from tests._federation_net_guard import REAL_STATUS_EXTRAS
        with patch("utils.map_federation.fetch_peer_directory",
                   return_value=([], good)), \
             patch("utils.map_federation._fetch_peer_status_extras",
                   REAL_STATUS_EXTRAS), \
             patch("utils.map_federation.urllib.request.urlopen",
                   return_value=_status_resp(payload)) as m:
            _, status = _fetch_peer_directory_and_claw("moc", 1.0)
        assert m.call_count == 1
        assert status.radio_deferred is True
        assert status.claw == {"installed": True, "device": "c1"}

    @pytest.mark.parametrize("newest_age,ok,reason", [
        (None, False, DEFERRED_REASON),               # no rows: by design
        # review r1: a benign label never hides a real finding
        (478 * 3600, False,
         "peer radio heard nothing in the window (frozen clock or deaf)"),
        (-3600, False, "peer radio clock is ahead of this box"),
        (30, True, ""),                               # fresh rows still count
    ])
    def test_trust_names_the_deferral(self, newest_age, ok, reason):
        now = _time.time()
        newest = None if newest_age is None else now - newest_age
        assert peer_radio_trust(True, newest, now, 7200,
                                deferred=True) == (ok, reason)

    def test_old_code_gateway_peer_still_reads_old_code(self):
        """review r1: source_diagnostics predates H14, so a pre-H14 gateway
        peer says deferred=True while exporting no radio times at all."""
        now = _time.time()
        assert peer_radio_trust(False, None, now, 7200, deferred=True)[1] == \
            "peer does not export radio hearing times (old code)"

    def _poll_seq(self, extras_seq):
        """Poll one peer N times, the status probe answering extras_seq[i]."""
        from utils.map_federation import FederationCollector
        fc = FederationCollector(["moc"], poll_interval=0, timeout=1.0)
        out = []
        for extras in extras_seq:
            t = _time.time()
            st = FederationPeerStatus(hostname="moc", ok=True, last_sync=t,
                                      last_attempt=t)
            with patch("utils.map_federation.fetch_peer_directory",
                       return_value=([], st)), \
                 patch("utils.map_federation._fetch_peer_status_extras",
                       return_value=extras):
                fc.poll_once()
            got = fc.get_snapshot().peer_status["moc"]
            out.append((got.radio_deferred, got.status_probe_misses))
        return out

    def test_deferral_held_through_a_missed_probe_with_a_witness(self):
        """review r1 F3: a failed/undecisive status probe must not flap the
        by-design label to "no rows"; the miss count says it is held."""
        seq = self._poll_seq([(None, True), (None, None), (None, None),
                              (None, False)])
        assert seq == [(True, 0), (True, 1), (True, 2), (False, 0)]

    def test_hold_is_bounded(self):
        """review r2 #1: never hold a by-design label forever."""
        from utils.map_federation import DEFERRAL_HOLD_MAX_POLLS as n
        seq = self._poll_seq([(None, True)] + [(None, None)] * (n + 2))
        assert seq[:n + 1] == [(True, i) for i in range(n + 1)]
        assert seq[n + 1:] == [(None, 0), (None, 0)]

    def test_nothing_held_nothing_counted(self):
        """review r2 #4: a peer with no meshtasticd leg never climbs."""
        assert self._poll_seq([(None, None)] * 4) == [(None, 0)] * 4

    def test_crash_path_holds_too(self):
        """review r2 #3: a crashed poll must not drop the hold."""
        from utils.map_federation import FederationCollector
        fc = FederationCollector(["moc"], poll_interval=0, timeout=1.0)
        t = _time.time()
        st = FederationPeerStatus(hostname="moc", ok=True, last_sync=t,
                                  last_attempt=t)
        with patch("utils.map_federation.fetch_peer_directory",
                   return_value=([], st)), \
             patch("utils.map_federation._fetch_peer_status_extras",
                   return_value=(None, True)):
            fc.poll_once()
        with patch("utils.map_federation._fetch_peer_directory_and_claw",
                   side_effect=RuntimeError("boom")):
            fc.poll_once()
        got = fc.get_snapshot().peer_status["moc"]
        assert "crash" in got.last_error
        assert (got.radio_deferred, got.status_probe_misses) == (True, 1)

    def test_held_count_reaches_the_row(self):
        """review r2 #2: the hold witness is pinned on the ROW, nonzero."""
        from utils.map_federation import FederationCollector, serialize_peer_status
        fc = FederationCollector(["moc"], poll_interval=0, timeout=1.0)
        for extras in [(None, True), (None, None), (None, None)]:
            t = _time.time()
            st = FederationPeerStatus(hostname="moc", ok=True, last_sync=t,
                                      last_attempt=t, radio_exported=True)
            with patch("utils.map_federation.fetch_peer_directory",
                       return_value=([], st)), \
                 patch("utils.map_federation._fetch_peer_status_extras",
                       return_value=extras):
                fc.poll_once()
        row = serialize_peer_status(fc.get_snapshot().peer_status["moc"],
                                    lambda _s, _n: (False, DEFERRED_REASON), ())
        assert row["status_probe_misses"] == 2
        assert row["meshtasticd_deferred_to_gateway"] is True

    def test_http_exception_is_caught_not_a_crash(self):
        """review r2 #3: IncompleteRead is not an OSError; it escaped and
        voided a GOOD directory fetch as 'crash'."""
        import http.client
        from tests._federation_net_guard import REAL_STATUS_EXTRAS
        with patch("utils.map_federation.urllib.request.urlopen",
                   side_effect=http.client.IncompleteRead(b"x")):
            assert REAL_STATUS_EXTRAS("moc", timeout=1.0) == (None, None)

    def test_unknown_or_false_deferral_keeps_old_reasons(self):
        now = _time.time()
        for d in (None, False):
            assert peer_radio_trust(True, None, now, 7200, deferred=d)[1] == \
                "peer has no radio-heard rows on this network"

    def test_poll_failure_outranks_deferral(self):
        now = _time.time()
        assert peer_radio_trust(True, None, now, 7200, ok=False,
                                deferred=True)[1] == "peer fetch failed this cycle"

    def test_deferral_is_meshtastic_only(self, collector):
        """It is the meshtasticd leg that defers; an rns radio on the same
        box is judged on its own rows."""
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        st = FederationPeerStatus(
            hostname="moc", peer_name="moc", ok=True, radio_exported=True,
            radio_newest_by_network={"rns": now - 30}, radio_deferred=True)
        _stub(collector, {}, {"moc": st})
        (row,) = build_federation_status(collector, now=now)["peer_status"]
        assert row["radio_witness"]["meshtastic"]["reason"] == DEFERRED_REASON
        assert row["radio_witness"]["rns"] == {"ok": True, "reason": ""}
        # A STALE rns radio on the same box keeps its OWN reason — the
        # meshtasticd deferral must not explain another radio's silence.
        st.radio_newest_by_network = {"rns": now - 478 * 3600}
        (row,) = build_federation_status(collector, now=now)["peer_status"]
        assert "frozen clock" in row["radio_witness"]["rns"]["reason"]

    def test_both_builders_say_it(self, collector):
        from utils._map_status_endpoints import build_federation_status
        now = _time.time()
        st = FederationPeerStatus(hostname="moc", peer_name="moc", ok=True,
                                  radio_exported=True, radio_deferred=True)
        _stub(collector, {}, {"moc": st})
        s = build_federation_status(collector, now=now)["peer_status"][0]
        g = collector._merge_federation({}, [])["peer_status"][0]
        for row in (s, g):
            assert row["radio_witness"]["meshtastic"] == {
                "ok": False, "reason": DEFERRED_REASON}
            assert row["meshtasticd_deferred_to_gateway"] is True
            assert row["status_probe_misses"] == 0


class TestFederationPanelSaysWitness:
    """The witness verdict lands IN the app: the map's federation panel says,
    per peer, whether its radio is a witness and why not (gateway deferral
    included). Executed in headless chromium at authoring time; pinned here."""

    def test_panel_renders_the_witness_line_escaped(self):
        body = _js_body("updateFederationPanel")
        assert "peerWitnessText(p)" in body
        assert "${escapeHtml(wit.text)}" in body
        assert 'title="${escapeHtml(wit.title)}"' in body

    def test_witness_text_never_claims_from_absence(self):
        body = _js_body("peerWitnessText")
        assert "radio witness: unknown" in body          # old server / no block
        assert "m.ok === true" in body                   # no truthy coercion
        assert "cell(n).ok === true" in body             # tooltip agrees
        assert "m.reason" in body


def _js_engine():
    """Which engine runs the page's JS here: ``node`` first, then headless
    chromium, else None. peerWitnessText/escapeHtml are PURE (no document,
    window, fetch — measured 11-10), so any engine runs them identically.

    node first because headless chromium HUNG on a trivial inline page in 5 of
    ~10 CI runs (11-09..11-10; stderr empty or D-Bus noise; always ~1 s on
    the dev box) — root cause UNKNOWN, NOT fixed here: this stops the gate
    depending on it. The engine is the test id, so CI's -v log says which ran."""
    import shutil
    if shutil.which("node"):
        return "node"
    if shutil.which("chromium") or shutil.which("chromium-browser"):
        return "chromium"
    return None


class TestFederationPanelExecuted:
    """review r1 F9: the static pins prove PRESENCE; this RUNS the page's own
    peerWitnessText (node or headless chromium — see _js_engine; skipped where
    neither is installed — an absent engine is said, never passed)."""

    @pytest.mark.parametrize("engine", [_js_engine() or "none"])
    def test_witness_text_runs_on_hostile_and_old_payloads(self, tmp_path, engine):
        if engine == "none":
            pytest.skip("no node and no headless chromium on this box — JS not executed")
        cases = [
            {"radio_witness": {"meshtastic": {"ok": True, "reason": ""}}},
            {"radio_witness": {"meshtastic": {"ok": False,
                                              "reason": DEFERRED_REASON}}},
            {"radio_witness": {"meshtastic": None, "rns": None}},
            {"radio_witness": {"meshtastic": {"ok": "yes"}}},
            {"radio_witness": "garbage"},
            {}, None,
            {"radio_witness": {"meshtastic": {"ok": False,
                                              "reason": DEFERRED_REASON}},
             "meshtasticd_deferred_to_gateway": True, "status_probe_misses": 2},
            {"radio_witness": {"meshtastic": {"ok": False, "reason": {"x": 1}}}},
            # r3 M14/M15: no suffix unless deferred === true AND misses > 0
            {"radio_witness": {"meshtastic": {"ok": False, "reason": "r"}},
             "meshtasticd_deferred_to_gateway": False, "status_probe_misses": 2},
            {"radio_witness": {"meshtastic": {"ok": False, "reason": "r"}},
             "meshtasticd_deferred_to_gateway": True, "status_probe_misses": 0},
            {"radio_witness": {"meshtastic": {"ok": False, "reason": "r"}},
             "meshtasticd_deferred_to_gateway": "true", "status_probe_misses": 2},
        ]
        funcs = "function %s\nfunction %s\n" % (
            _js_fn("peerWitnessText"), _js_fn("escapeHtml"))
        run = ("JSON.stringify(%s.map(p=>{const w=peerWitnessText(p);"
               "return [w.text,w.ok,w.title]}))" % json.dumps(cases))
        out = _run_page_js(engine, funcs, run, tmp_path)
        assert out[0][:2] == ["📻 radio witness", True]
        assert out[1][0] == "📻 not a radio witness — " + DEFERRED_REASON
        assert out[1][1] is False
        assert out[2][:2] == ["📻 radio witness: unknown", False]   # null cell
        assert out[3][1] is False and out[3][2] == "meshtastic: not a witness"
        assert out[4][:2] == ["📻 radio witness: unknown", False]
        assert out[5][:2] == out[6][:2] == ["📻 radio witness: unknown", False]
        assert out[7][0] == ("📻 not a radio witness — " + DEFERRED_REASON
                             + " · last answer held 2 poll(s)")
        assert out[8][0] == "📻 not a radio witness — reason unknown"
        for i in (9, 10, 11):
            assert out[i][0] == "📻 not a radio witness — r", out[i]


def _run_page_js(engine, funcs, expr, tmp_path):
    """Evaluate ``expr`` (a JSON.stringify(...) expression) after ``funcs`` in
    ``engine``; return the decoded value. A JS exception is a readable red."""
    import shutil
    import subprocess
    if engine == "node":
        script = tmp_path / "w.js"
        script.write_text(funcs + "try{process.stdout.write(" + expr + ");}"
                          "catch(e){process.stdout.write('JSERR '+e);}\n")
        proc = subprocess.run([shutil.which("node"), str(script)],
                              capture_output=True, text=True, timeout=20)
        assert proc.returncode == 0, proc.stderr[-500:]
        text = proc.stdout
    else:
        browser = shutil.which("chromium") or shutil.which("chromium-browser")
        page = tmp_path / "w.html"
        page.write_text(
            "<html><body><pre id=o></pre><script>" + funcs +
            "try{document.getElementById('o').textContent=" + expr + ";}"
            "catch(e){document.getElementById('o').textContent='JSERR '+e;}"
            "</script></body></html>")
        stdout, stderr = _dump_dom(browser, page.as_uri(), tmp_path)
        m = re.search(r'<pre id="o">(.*?)</pre>', stdout, re.S)
        assert m, stderr[-500:]
        text = m.group(1).replace("&amp;", "&").replace("&lt;", "<").replace(
            "&gt;", ">").replace("&quot;", '"')
    assert not text.startswith("JSERR"), text
    return json.loads(text)


def _dump_dom(browser, uri, profile_dir, attempt_timeout=10.0, attempts=2):
    """(stdout, stderr) of `browser --dump-dom uri`, bounded at
    attempts × attempt_timeout (+ a 2 s drain each) — under CI's 30 s cap.

    The browser runs in its OWN session and a timeout kills the whole
    process GROUP: killing only the direct child leaves renderer
    grandchildren holding the pipe, and communicate() waits on them
    forever. A hung browser is pytest.fail with its reason — a readable
    red, never a suite crash and never a pass."""
    import signal
    import subprocess
    said = []
    for n in range(attempts):
        proc = subprocess.Popen(
            [browser, "--headless", "--disable-gpu", "--no-sandbox",
             "--disable-dev-shm-usage", "--no-first-run",
             "--no-default-browser-check",
             f"--user-data-dir={profile_dir / f'chromium-profile-{n}'}",
             "--dump-dom", uri],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True)
        try:
            return proc.communicate(timeout=attempt_timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                _, err = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                err = "(stderr unreadable: a group member escaped and holds the pipe)"
            # the browser's own words are the only witness of WHY (hfm #9)
            said.append(f"attempt {n + 1}: stderr tail: "
                        f"{(err or '').strip()[-800:] or '(empty)'}")
    pytest.fail(f"headless browser hung {attempts}× past {attempt_timeout:g} s "
                f"({browser}) — the page JS was NOT executed; this is the "
                "browser, not peerWitnessText.\n" + "\n".join(said))


class TestDumpDomIsBounded:
    """CI hung this test past its 30 s per-test cap TWICE (a6712122,
    7b0b9e9d) — pytest-timeout's thread method then kills the WHOLE suite
    with no summary. subprocess.run(timeout=) kills only the direct child;
    communicate() then waits on any grandchild (chromium's renderer/zygote)
    still holding the pipe. The helper must return inside its own bound
    even then, and say the browser hung — never crash the run."""

    def test_a_grandchild_holding_the_pipe_cannot_outlive_the_bound(self, tmp_path):
        import time
        stub = tmp_path / "hung-browser"
        pids = tmp_path / "grandchild.pids"
        stub.write_text(f"#!/bin/sh\nsleep 300 &\necho $! >> {pids}\nexec sleep 300\n")
        stub.chmod(0o755)
        t0 = time.monotonic()
        with pytest.raises(pytest.fail.Exception, match="hung"):
            _dump_dom(str(stub), "file:///x", tmp_path, attempt_timeout=1.0)
        # 2 attempts × 1 s; a child-only kill also waits out each 2 s drain
        assert time.monotonic() - t0 < 4, "the group was not killed"
        alive = []
        for pid in map(int, pids.read_text().split()):
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except ProcessLookupError:
                pass
        for pid in alive:
            os.kill(pid, 9)
        assert not alive, f"grandchild(ren) leaked past the timeout: {alive}"

    def test_a_hang_carries_the_browsers_own_stderr(self, tmp_path):
        """CI hung 3 of 6 runs (a6712122, 7b0b9e9d, 4657ddf8) and every
        failure said only "hung": the kill discarded chromium's stderr, the
        one witness of WHY (hfm #9). The failure must carry its tail."""
        stub = tmp_path / "talking-hung-browser"
        stub.write_text("#!/bin/sh\necho 'STUB-WHY: bus connect failed' >&2\n"
                        "exec sleep 300\n")
        stub.chmod(0o755)
        with pytest.raises(pytest.fail.Exception) as exc:
            _dump_dom(str(stub), "file:///x", tmp_path, attempt_timeout=1.0)
        assert "STUB-WHY: bus connect failed" in str(exc.value)


def _js_fn(name):
    """`name(args) { body }` of a page function, for execution."""
    m = re.search(r"function (%s\([^)]*\)\s*\{)" % name, _HTML)
    assert m, name
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(_HTML[i], 0)
        i += 1
    return _HTML[m.start(1):i]


def test_deferral_constant_is_a_reason_the_collector_emits():
    """Producer and consumer of `deferred_to_gateway` share ONE value (hfm #5,
    review r1 F7). The collector keeps the literal so the honesty scan sees
    it; this pins the consumer's constant to what that scan finds."""
    from tests.test_map_source_honesty import _emitted_reason_literals
    from utils.map_source_reasons import DEFERRED_TO_GATEWAY, reason_state
    assert DEFERRED_TO_GATEWAY in _emitted_reason_literals()
    assert reason_state(DEFERRED_TO_GATEWAY) == "inactive"


class TestReviewR3FollowUps:
    """item-2 r3 (Fable) F1 + survivor pins, and r4 (item 1) sub-minute."""

    @pytest.fixture(autouse=True)
    def _fresh_warn_set(self):
        import utils._map_collector_config as cfg
        cfg._WARNED_BAD_THRESHOLD.clear()
        yield
        cfg._WARNED_BAD_THRESHOLD.clear()

    def test_backoff_skipped_polls_count_and_expire_the_hold(self):
        """r3 F1: a backed-off peer's carried row froze at 'held 3' forever."""
        from utils.map_federation import (DEFERRAL_HOLD_MAX_POLLS,
                                          FederationCollector)
        fc = FederationCollector(["moc"], poll_interval=0, timeout=1.0)
        fc._snapshot.peer_status["moc"] = FederationPeerStatus(
            hostname="moc", ok=False, in_backoff=True,
            next_eligible_poll_ts=_time.time() + 86400,
            radio_deferred=True, status_probe_misses=1)
        seen = []
        with patch("utils.map_federation._fetch_peer_directory_and_claw",
                   side_effect=AssertionError("a backed-off peer was polled")):
            for _ in range(DEFERRAL_HOLD_MAX_POLLS + 1):
                fc.poll_once()
                st = fc.get_snapshot().peer_status["moc"]
                seen.append((st.radio_deferred, st.status_probe_misses))
        assert seen[:DEFERRAL_HOLD_MAX_POLLS - 1] == [
            (True, i) for i in range(2, DEFERRAL_HOLD_MAX_POLLS + 1)]
        assert seen[-1] == (None, 0)

    def test_deferral_scoped_to_meshtastic_at_the_trust_call(self):
        """r3 M4: another network with no rows must never read 'defers'."""
        from utils.map_federation import peer_status_radio_trust
        now = _time.time()
        st = FederationPeerStatus(hostname="moc", ok=True, radio_exported=True,
                                  radio_deferred=True)
        assert peer_status_radio_trust(st, "rns", now, lambda _n: 1800,
                                       ("rns",))[1] == \
            "peer has no radio-heard rows on this network"
        assert peer_status_radio_trust(st, "meshtastic", now, lambda _n: 7200,
                                       ())[1] == DEFERRED_REASON

    @pytest.mark.parametrize("payload", [[1, 2], None, "str", 1])
    def test_non_dict_status_payload_is_unknown(self, payload):
        """r3 M5."""
        from tests._federation_net_guard import REAL_STATUS_EXTRAS
        with patch("utils.map_federation.urllib.request.urlopen",
                   return_value=_status_resp(payload)):
            assert REAL_STATUS_EXTRAS("moc", timeout=1.0) == (None, None)

    @pytest.mark.parametrize("tiny", [0.001, 5e-324, 1 / 61])
    def test_sub_second_window_refused(self, collector, tiny):
        """item-1 r4: int(minutes * 60) truncated these to a 0 s window."""
        default = collector.get_source_threshold_seconds("meshtastic")
        collector._settings.set("meshtastic_threshold_minutes", tiny)
        assert collector.get_source_threshold_seconds("meshtastic") == default

    def test_one_second_window_still_honoured(self, collector):
        collector._settings.set("meshtastic_threshold_minutes", 1 / 60)
        assert collector.get_source_threshold_seconds("meshtastic") == 1


# ── 2026-11-09 (MA port review F4, measured: the live manager cache held 222
# local_radio features WITH radio_last_heard) — a replayed cache feature keeps
# its first source_origin tag, so its old stamp would read as THIS cycle's
# radio hearing. Only the drain stamps; a replay must arrive stamp-less.

class TestCacheReplayNeverStamps:
    def test_replayed_cached_stamp_writes_no_radio_heard(self, tmp_path):
        home = tmp_path / "home"
        cfg = home / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        with patch("utils.map_data_collector.get_real_user_home", return_value=home):
            c = MapDataCollector(cache_dir=tmp_path / "cache",
                                 enable_history=True, config_dir=cfg)
        cached = _radio_feat("!0000cace", _time.time() - 600)
        assert cached["properties"]["source_origin"] == "local_radio"

        with patch.object(MapDataCollector, "_timed_collect",
                          lambda self, name, fn, *a, **kw: []), \
             patch.object(MapDataCollector, "_load_cache",
                          lambda self: [cached]), \
             patch.object(MapDataCollector, "_merge_federation",
                          lambda self, f, pl, *a, **k: {}):
            geo = c.collect(max_age_seconds=0)
        assert any(f["properties"]["id"] == "!0000cace" for f in geo["features"]), \
            "precondition: the cache replay ran"
        assert _dir_row(c._history, "!0000cace")["radio_heard"] is None
