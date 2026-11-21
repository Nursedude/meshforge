"""MAP-H H4a r6 — ONE owner of the local-vs-peer decision per (network, id).

The state table + invariants live in `.claude/plans/maps_audit_2026_11_06.md`
§ "H4a r6". Root (read): ``_merge_federation`` indexed local FEATURES first and
added a position-less record only if the key was not already there, so a node
positioned by a lower-trust local source (``mqtt_local``) AND heard GPS-less by
our radio was judged on the feature alone — our hearing never entered the
decision (r5 F1). Each test below names its table row and invariant:

- I1 our position-less hearing is never discarded and is the ONLY source of
  ``heard_here``.
- I2 a node our radio hears is never counted federated-only, and is OURS in at
  most one of {features, served nodes_without_position} (row 7's documented
  shape excepted: stub ours + a federated marker at a peer fix).
- I3 a trust win never removes geometry without supplying geometry.
- I4 at most one marker per (network, id).
- F2 one winner rule; a non-numeric ``last_seen`` loses, never raises.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from tests._federation_net_guard import _no_real_peer_http  # noqa: F401 — autouse: no real peer HTTP

from utils.map_data_collector import MapDataCollector
from utils.map_federation import (
    FederationCollector,
    FederationPeerStatus,
    FederationSnapshot,
    better_fix,
)

NODE_NUM = 0xA2EA2F10
NODE_ID = "!a2ea2f10"
FED_KEY = f"federated:meshtastic:{NODE_ID}"
PEER = "moc5.mf.internal"


@pytest.fixture
def collector(tmp_path):
    fake_home = tmp_path / "home"
    cfg_dir = fake_home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
        return MapDataCollector(cache_dir=tmp_path / "cache",
                                enable_history=False, config_dir=cfg_dir)


def _peer(collector, row):
    """A peer snapshot holding ONE winner row for (row.network, row.id)."""
    now = time.time()
    fc = MagicMock(spec=FederationCollector)
    fc.peers = [PEER]
    fc.get_snapshot.return_value = FederationSnapshot(
        by_node={(row["network"], row["id"]): {
            "name": "FROM PEER", "federated_from": PEER, "seen_by_peers": [PEER],
            "last_seen": now - 5, **row}},
        peer_status={PEER: FederationPeerStatus(
            hostname=PEER, ok=True, last_sync=now, last_attempt=now, last_count=1)},
        last_sync=now, last_attempt=now)
    collector._federation = fc


def _peer_meshtastic(collector, *, fix, origin="local_radio"):
    row = {"id": NODE_ID, "network": "meshtastic", "source_origin": origin,
           "lat": 19.43 if fix else None, "lon": -155.21 if fix else None}
    _peer(collector, row)


def _iface(heard_ts):
    iface = MagicMock()
    iface.myInfo = None
    iface.localNode = None
    iface.nodes = {NODE_ID: {
        "num": NODE_NUM, "lastHeard": int(heard_ts),
        "user": {"id": NODE_ID, "longName": "LOCAL HEARD", "shortName": "LH"}}}
    return iface


def _drain(collector, heard_ts):
    """THIS radio's real GPS-less record for NODE_ID (the real producer)."""
    _, no_position, _ = collector._drain_interface_nodes(_iface(heard_ts), "meshtasticd")
    return list(no_position)


def _mqtt_feature(lat=19.0, lon=-155.0):
    """A node positioned by a lower-trust LOCAL source (mqtt_local, 70)."""
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat, 0]},
            "properties": {"id": NODE_ID, "network": "meshtastic",
                           "source_origin": "mqtt_local", "is_online": True}}


def _ours_in(position_less, nid=NODE_ID):
    return [e for e in position_less if e.get("id") == nid and not e.get("federated")]


def _markers(features, nid=NODE_ID):
    return [k for k, f in features.items() if (f.get("properties") or {}).get("id") == nid]


# --------------------------------------------------------------------- row 6
class TestRow6TrustWinWithoutGeometryKeepsOurs:
    def test_higher_trust_peer_stub_never_erases_our_position(self, collector):
        """I3: before r6 our mqtt_local marker was popped and replaced by a
        position-less federated stub — the node vanished from the map."""
        features = {NODE_ID: _mqtt_feature()}
        position_less: list = []
        _peer_meshtastic(collector, fix=False)
        block = collector._merge_federation(features, position_less)

        assert _markers(features) == [NODE_ID], features
        assert features[NODE_ID]["geometry"]["coordinates"][:2] == [-155.0, 19.0]
        assert [e for e in position_less if e.get("id") == NODE_ID] == [], position_less
        assert block["total"] == 0, block


# --------------------------------------------------------------------- row 9
class TestRow9OurHearingSurvivesAnyTrust:
    RNS_ID = "a1b2c3d4e5f60718"

    def _rns_stub(self):
        return [{"id": self.RNS_ID, "name": "rns node", "network": "rns",
                 "is_online": True, "source_origin": "rns_path_table"}]

    def _peer_rns(self, collector, fix):
        _peer(collector, {"id": self.RNS_ID, "network": "rns",
                          "source_origin": "local_radio",
                          "lat": 19.5 if fix else None, "lon": -155.5 if fix else None})

    @pytest.mark.parametrize("fix", [True, False])
    def test_higher_trust_peer_never_drops_our_record(self, collector, fix):
        """I1: before r6 a peer local_radio (100) row dropped our
        rns_path_table (90) record BEFORE the history loop read it."""
        features: dict = {}
        position_less = self._rns_stub()
        self._peer_rns(collector, fix)
        block = collector._merge_federation(features, position_less)

        assert len(_ours_in(position_less, self.RNS_ID)) == 1, position_less
        assert not [e for e in position_less
                    if e.get("id") == self.RNS_ID and e.get("federated")], position_less
        assert block["total"] == 0, block          # I2: never federated-only
        if fix:
            assert list(features) == [f"federated:rns:{self.RNS_ID}"]
            assert block["position_adopted"] == 1
        else:
            assert features == {}


# -------------------------------------------------------------------- row 10
class TestRow10OneRecordWhenWeHoldBoth:
    def test_hearing_folds_into_our_marker_federation_off(self, collector):
        """I2: a node positioned by mqtt_local AND heard GPS-less by our radio
        was served in BOTH features and nodes_without_position."""
        heard = time.time() - 60
        features = {NODE_ID: _mqtt_feature()}
        position_less = _drain(collector, heard)
        collector._merge_federation(features, position_less)

        assert _ours_in(position_less) == [], position_less
        assert _markers(features) == [NODE_ID]
        assert features[NODE_ID]["properties"]["radio_last_heard"] == float(int(heard))

    def test_hearing_folds_with_a_lower_trust_peer(self, collector):
        heard = time.time() - 60
        features = {NODE_ID: _mqtt_feature()}
        position_less = _drain(collector, heard)
        _peer_meshtastic(collector, fix=True, origin="meshcore_public")
        block = collector._merge_federation(features, position_less)

        assert _ours_in(position_less) == []
        assert _markers(features) == [NODE_ID]           # I4
        assert block["total"] == 0                       # I2


# -------------------------------------------------------------------- row 11
class TestRow11HigherTrustPeerFixKeepsOurHearing:
    def test_peer_fix_drawn_with_our_hearing_counted_once(self, collector):
        """r5 F1: the REPLACE path drew the peer's marker WITHOUT heard_here
        ("not heard here" for a node heard 60 s ago) and the stub stayed,
        so the node was counted twice."""
        heard = time.time() - 60
        features = {NODE_ID: _mqtt_feature()}
        position_less = _drain(collector, heard)
        _peer_meshtastic(collector, fix=True, origin="local_radio")
        block = collector._merge_federation(features, position_less)

        assert _markers(features) == [FED_KEY], features   # I4, peer fix wins
        props = features[FED_KEY]["properties"]
        assert props["heard_here"] is True, props          # I1
        assert props["heard_here_at"] == float(int(heard))
        assert _ours_in(position_less) == [], position_less
        assert block["total"] == 0 and block["by_network"] == {}, block   # I2
        assert block["position_adopted"] == 1

    def test_peer_stub_without_fix_keeps_our_marker(self, collector):
        """I3 with the fold: no geometry offered, ours stays (row 10 shape)."""
        heard = time.time() - 60
        features = {NODE_ID: _mqtt_feature()}
        position_less = _drain(collector, heard)
        _peer_meshtastic(collector, fix=False, origin="local_radio")
        collector._merge_federation(features, position_less)

        assert _markers(features) == [NODE_ID]
        assert features[NODE_ID]["geometry"]["coordinates"][:2] == [-155.0, 19.0]
        assert _ours_in(position_less) == []
        assert [e for e in position_less if e.get("id") == NODE_ID] == []


# --------------------------------------------------- rows 10/11, full collect
def _history_collector(tmp_path, heard):
    fake_home = tmp_path / "home"
    cfg_dir = fake_home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
        c = MapDataCollector(cache_dir=tmp_path / "cache",
                             enable_history=True, config_dir=cfg_dir)
    for name in ("_collect_unified_tracker", "_collect_direct_radio",
                 "_collect_node_tracker", "_collect_aredn",
                 "_collect_aredn_worldmap", "_collect_rns_direct",
                 "_collect_meshcore_public"):
        setattr(c, name, lambda: [])
    c._collect_public_fallbacks = lambda current_feature_count=0: []
    c._collect_mqtt = lambda: [_mqtt_feature()]

    def radio():
        _, no_pos, _ = c._drain_interface_nodes(_iface(heard), "meshtasticd")
        c._nodes_without_position.extend(no_pos)
        return []
    c._collect_meshtasticd = radio
    c._history.record_observations = MagicMock()
    return c


def _persisted_hearing(c):
    return [f for call in c._history.record_observations.call_args_list
            for f in call.args[0]
            if (f.get("properties") or {}).get("id") == NODE_ID
            and (f.get("properties") or {}).get("source_origin") == "local_radio"]


class TestHistoryKeepsTheFoldedHearing:
    @pytest.mark.parametrize("peer_fix", [None, True])
    def test_served_once_and_history_still_gets_our_hearing(self, tmp_path, peer_fix):
        """I1 + I2 end to end: the fold takes the stub out of the SERVED list
        only; history must still persist our GPS-less local_radio hearing."""
        heard = time.time() - 60
        c = _history_collector(tmp_path, heard)
        if peer_fix:
            _peer_meshtastic(c, fix=True, origin="local_radio")

        geo = c._collect_locked()

        served = [e for e in geo["properties"]["nodes_without_position"]
                  if e.get("id") == NODE_ID]
        assert served == [], served
        drawn = [f for f in geo["features"] if f["properties"].get("id") == NODE_ID]
        assert len(drawn) == 1, drawn
        rows = _persisted_hearing(c)
        assert len(rows) == 1, rows
        assert not (rows[0].get("geometry") or {}).get("coordinates"), rows[0]
        assert rows[0]["properties"]["radio_last_heard"] == float(int(heard))

    def test_fold_list_is_per_cycle_never_replayed(self, tmp_path):
        """A folded hearing is a claim about THIS cycle's drain: a second
        cycle whose radio no longer reports the node must persist none."""
        heard = time.time() - 60
        c = _history_collector(tmp_path, heard)
        c._collect_locked()
        c._history.record_observations.reset_mock()
        c._collect_meshtasticd = lambda: []
        c._collect_locked()
        assert _persisted_hearing(c) == []


# ------------------------------------------------------------------------ F2
class TestF2OneWinnerRuleNeverRaises:
    @pytest.mark.parametrize("bad", ["yesterday", object(), [1], float("nan")])
    def test_better_fix_non_numeric_last_seen_loses(self, bad):
        good = {"source_origin": "local_radio", "last_seen": 100.0}
        odd = {"source_origin": "local_radio", "last_seen": bad}
        assert better_fix(good, odd) is True
        assert better_fix(odd, good) is False

    def test_poll_winner_rule_survives_a_non_numeric_last_seen(self):
        """The poll's own row winner made the same ``or 0`` compare: a peer
        sending a string ``last_seen`` raised TypeError inside poll_once."""
        now = time.time()

        def rows(peer, last_seen):
            return ([{"network": "meshtastic", "id": NODE_ID, "name": peer,
                      "lat": 19.4, "lon": -155.2, "last_seen": last_seen,
                      "source_origin": "local_radio", "federated_from": peer}],
                    FederationPeerStatus(hostname=peer, ok=True, last_sync=now,
                                         last_count=1, last_attempt=now))
        responses = {"peer-a": rows("peer-a", now - 10),
                     "peer-b": rows("peer-b", "not-a-time")}
        fc = FederationCollector(list(responses), poll_interval=10, timeout=1.0)
        with patch("utils.map_federation.fetch_peer_directory",
                   side_effect=lambda peer, *a, **kw: responses[peer]):
            fc.poll_once()
        entry = fc.get_snapshot().by_node[("meshtastic", NODE_ID)]
        assert entry["name"] == "peer-a", entry


# ------------------------------------------------- HEARING_ORIGINS on the fold
class TestFoldOnlyCountsOurReceptionAsHearing:
    def test_public_feed_stub_folded_is_not_our_hearing(self, collector):
        """A public-feed position-less stub beside our feature is deduped by
        the fold, but it is NOT this box's hearing: a higher-trust peer fix
        must take the ordinary row-5 path (federated-only, no heard_here,
        not adopted) — never row 11's hearing branch."""
        features = {NODE_ID: _mqtt_feature()}
        position_less = [{"id": NODE_ID, "network": "meshtastic", "name": "pub",
                          "source_origin": "meshcore_public"}]
        _peer_meshtastic(collector, fix=True, origin="local_radio")
        block = collector._merge_federation(features, position_less)

        assert _markers(features) == [FED_KEY]
        props = features[FED_KEY]["properties"]
        assert "heard_here" not in props, props
        assert block["position_adopted"] == 0
        assert block["total"] == 1
        assert [e for e in position_less if e.get("id") == NODE_ID] == []


# ------------------------------------------------- r6 review (Fable) findings
class TestReviewF1HugeIntegerLastSeen:
    def test_json_bigint_last_seen_never_raises(self):
        """json.loads gives a Python int for a 401-digit number; float() of it
        raises OverflowError — the r6 rule must treat it as UNKNOWN."""
        good = {"source_origin": "local_radio", "last_seen": 100.0}
        huge = {"source_origin": "local_radio", "last_seen": 10 ** 400}
        assert better_fix(good, huge) is True
        assert better_fix(huge, good) is False


class TestReviewF2FixTrustIsTheFixesOwn:
    def test_lower_trust_best_fix_never_replaces_our_fix(self, collector):
        """Trust was judged on the ROW's origin while the geometry came from
        best_fix's: a public_fallback fix replaced our mqtt_local marker."""
        heard = time.time() - 60
        features = {NODE_ID: _mqtt_feature()}
        position_less = _drain(collector, heard)
        _peer(collector, {"id": NODE_ID, "network": "meshtastic",
                          "source_origin": "local_radio", "lat": None, "lon": None,
                          "best_fix": {"lat": 21.3, "lon": -157.8, "altitude": None,
                                       "source_origin": "public_fallback",
                                       "last_seen": time.time() - 79200,
                                       "federated_from": "peer-x"}})
        block = collector._merge_federation(features, position_less)
        assert _markers(features) == [NODE_ID], features
        assert features[NODE_ID]["geometry"]["coordinates"][:2] == [-155.0, 19.0]
        assert block["position_adopted"] == 0


class TestReviewP1CacheNeverReplaysAPeersMarkerAsOurs:
    def test_second_cycle_still_says_heard_here(self, tmp_path):
        """No positioned live source → Source 7 replays the disk cache, which
        held cycle 1's FEDERATED marker; it came back as a LOCAL feature and
        the seam lost our hearing from cycle 2 on."""
        heard = time.time() - 60
        c = _history_collector(tmp_path, heard)
        c._collect_mqtt = lambda: []
        _peer_meshtastic(c, fix=True, origin="local_radio")
        for cycle in (1, 2, 3):
            geo = c._collect_locked()
            drawn = [f["properties"] for f in geo["features"]
                     if f["properties"].get("id") == NODE_ID]
            assert len(drawn) == 1, (cycle, drawn)
            assert drawn[0].get("heard_here") is True, (cycle, drawn[0])
            assert geo["properties"]["federation"]["position_adopted"] == 1, cycle
            assert geo["properties"]["federation"]["total"] == 0, cycle


class TestReviewP2OperatorPositionKeepsOurHearing:
    def test_operator_positioned_node_hearing_reaches_history(self, tmp_path):
        """_apply_operator_positions removed our record BEFORE the fold, so
        history never received this radio's hearing of the node (I1)."""
        heard = time.time() - 60
        c = _history_collector(tmp_path, heard)
        c._collect_mqtt = lambda: []
        c._settings["meshcore_positions"] = {NODE_ID: {"lat": 19.7, "lon": -155.1}}
        geo = c._collect_locked()
        drawn = [f["properties"] for f in geo["features"]
                 if f["properties"].get("id") == NODE_ID]
        assert len(drawn) == 1 and drawn[0]["source_origin"] == "operator_positions"
        assert drawn[0].get("radio_last_heard") == float(int(heard)), drawn[0]
        assert [e for e in geo["properties"]["nodes_without_position"]
                if e.get("id") == NODE_ID] == []
        assert len(_persisted_hearing(c)) == 1


# --------------------------------------- r6 review F3 (operator: option 1)
import json  # noqa: E402

from tests.test_peer_radio_heard import (  # noqa: E402 — the page-JS harness
    _js_engine, _js_fn, _run_page_js)


class TestReviewF3OurMarkerSaysOurHearing:
    """Row 10's fold removed the sidebar stub — the only served sign that our
    radio hears the node. Operator 11-20 option 1: our marker carries
    heard_here* (same fields + same words as a federated marker); is_online
    (and so every page count) is left alone."""

    def test_fold_stamps_heard_here_on_our_marker(self, collector):
        heard = time.time() - 60
        features = {NODE_ID: _mqtt_feature()}
        features[NODE_ID]["properties"]["is_online"] = False
        position_less = _drain(collector, heard)
        collector._merge_federation(features, position_less)    # federation OFF
        props = features[NODE_ID]["properties"]
        assert props["heard_here"] is True, props
        assert props["heard_here_at"] == float(int(heard))
        assert props["is_online"] is False                       # counts unchanged

    def test_public_stub_fold_claims_no_hearing(self, collector):
        features = {NODE_ID: _mqtt_feature()}
        collector._merge_federation(features, [
            {"id": NODE_ID, "network": "meshtastic", "source_origin": "meshcore_public"}])
        assert "heard_here" not in features[NODE_ID]["properties"]

    @pytest.mark.parametrize("engine", [_js_engine() or "none"])
    def test_page_says_it_on_our_marker(self, tmp_path, engine):
        if engine == "none":
            pytest.skip("no node and no headless chromium on this box — JS not executed")
        cases = [
            {"network": "meshtastic", "is_online": False, "heard_here": True,
             "heard_here_age_s": 60},
            {"network": "meshtastic", "is_online": True, "heard_here": False,
             "heard_here_age_s": 3 * 3600},
            {"network": "meshtastic", "is_online": True, "heard_here": None},
            {"network": "meshtastic", "is_online": False},            # untouched
        ]
        funcs = ("function meshtasticWindowText(){return '2 h';}\n"
                 "function %s\nfunction %s\nfunction %s\n" % (
                     _js_fn("agoText"), _js_fn("peerHeardText"),
                     _js_fn("nodeStatusText")))
        out = _run_page_js(engine, funcs, "JSON.stringify(%s.map(nodeStatusText))"
                           % json.dumps(cases), tmp_path)
        assert out[0] == "Heard here 1 min ago", out
        assert out[1] == "Not heard here in 2 h (last 3 h ago)", out
        assert out[2] == "Online · heard ≤ 2 h · radio hearing unknown", out
        assert out[3] == "Not heard here in 2 h", out


# ----------------------------------------------- r6 round-2 review (Fable)
class TestR2FoldNeverMutatesASourcesDict:
    def test_stamp_does_not_outlive_the_cycle(self, collector):
        """R2 F1: the fold stamped heard_here*/radio_last_heard INTO the
        feature dict, and a source that serves the same cached list
        (meshcore_public, 600 s) replayed the stamp after our record was gone."""
        heard = time.time() - 30
        cached = _mqtt_feature()                     # the source's own dict
        features = {NODE_ID: cached}
        collector._merge_federation(features, _drain(collector, heard))
        assert features[NODE_ID]["properties"]["heard_here"] is True
        assert "heard_here" not in cached["properties"], cached
        assert "radio_last_heard" not in cached["properties"], cached
        # next cycle: same cached dict, our radio no longer reports the node
        features2 = {NODE_ID: cached}
        collector._merge_federation(features2, [])
        assert "heard_here" not in features2[NODE_ID]["properties"]

    def test_newer_hearing_replaces_an_older_stamp(self, collector):
        heard = time.time() - 30
        feat = _mqtt_feature()
        feat["properties"]["radio_last_heard"] = heard - 3600
        features = {NODE_ID: feat}
        collector._merge_federation(features, _drain(collector, heard))
        assert features[NODE_ID]["properties"]["radio_last_heard"] == float(int(heard))


class TestR2OperatorPromotedWithoutNetwork:
    def test_promoted_record_without_network_still_folds(self, collector):
        """R2 F3: the promoted feature says network 'unknown' while the record
        carries none — the fold never matched and the record stayed served."""
        collector._settings.set("meshcore_positions", {"abc": {"lat": 19.7, "lon": -155.1}})
        collector._nodes_without_position = [
            {"id": "abc", "name": "POS", "source_origin": "local_radio"}]
        features: dict = {}
        collector._apply_operator_positions(features)
        collector._merge_federation(features, collector._nodes_without_position)
        assert collector._nodes_without_position == []
        assert len(features) == 1


class TestR2PageNeverSaysNotHeardForUnknown:
    @pytest.mark.parametrize("engine", [_js_engine() or "none"])
    def test_unknown_hearing_offline_marker(self, tmp_path, engine):
        """R2 F2: 'Not heard here in 2 h · radio hearing unknown' contradicts
        itself (r4 F1: null is said, never 'not heard here')."""
        if engine == "none":
            pytest.skip("no node and no headless chromium on this box — JS not executed")
        cases = [{"network": "meshtastic", "is_online": False, "heard_here": None},
                 {"network": "meshcore", "is_online": False, "heard_here": None}]
        funcs = ("function meshtasticWindowText(){return '2 h';}\n"
                 "function %s\nfunction %s\nfunction %s\n" % (
                     _js_fn("agoText"), _js_fn("peerHeardText"),
                     _js_fn("nodeStatusText")))
        out = _run_page_js(engine, funcs, "JSON.stringify(%s.map(nodeStatusText))"
                           % json.dumps(cases), tmp_path)
        for s in out:
            assert "not heard here" not in s.lower(), out
        assert out[0] == "Not heard in 2 h · radio hearing unknown", out
        assert out[1] == "Offline · radio hearing unknown", out


class TestR2MergeNeverMutatesASourcesDictEither:
    def test_peer_verdict_is_not_written_into_the_source_dict(self, collector):
        """Same class as R2 F1, older path: _attach wrote peer_heard into our
        feature's dict, which a caching source replays next cycle."""
        cached = _mqtt_feature()
        features = {NODE_ID: cached}
        now = time.time()
        _peer_meshtastic(collector, fix=False, origin="meshcore_public")
        snap = collector._federation.get_snapshot.return_value
        snap.by_node[("meshtastic", NODE_ID)]["radio_heard_by"] = {PEER: now - 30}
        snap.peer_status[PEER].radio_newest_by_network = {"meshtastic": now - 30}
        collector._merge_federation(features, [])
        assert "peer_heard" in features[NODE_ID]["properties"], features
        assert "peer_heard" not in cached["properties"], cached
