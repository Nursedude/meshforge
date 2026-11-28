"""BIC-2 DM slice 2 (2026-11-27): a config-supplied own node id.

Operator 11-26 queued it ("yes queue the config node id slice"); 11-27 chose
its home: ``[meshtastic] own_node_id`` in ``~/.config/meshforge/global.ini``.
It closes the accepted r5 cost: a DM whose own header was never uplinked, on a
box with no connection-read id, stayed ``left_unconfirmed`` for a real
delivery. Contract (design record §5):

- unset = unknown = today, never a guess;
- strict ``!xxxxxxxx`` at the authoring boundary, refusals SAID (hfm #3);
- it feeds the send-time stamp beside the connection id; disagreeing = not
  stamped, said (a stale config after a radio swap is never absorbed);
- the witness line names the source.
"""
from __future__ import annotations

import pytest

from utils import global_config, own_radio

ME = 0x6201CE25
OTHER = 0x0A1B2C3D


def _ini(tmp_path, body: str):
    p = tmp_path / "global.ini"
    p.write_text(body, encoding="utf-8")
    return p


# ── the reader: strict, tri-state, never raises ─────────────────────────────

class TestReadOwnNodeId:

    def test_no_file_is_unset(self, tmp_path):
        assert global_config.read_own_node_id(tmp_path / "nope.ini")["state"] == "unset"

    def test_no_section_is_unset(self, tmp_path):
        p = _ini(tmp_path, "[mqtt]\nbroker = localhost\n")
        assert global_config.read_own_node_id(p) == {"state": "unset", "num": None, "why": ""}

    def test_empty_value_is_unset(self, tmp_path):
        p = _ini(tmp_path, "[meshtastic]\nown_node_id =\n")
        assert global_config.read_own_node_id(p)["state"] == "unset"

    @pytest.mark.parametrize("spelling", ["!6201ce25", "!6201CE25", "  !6201ce25  "])
    def test_a_full_id_reads(self, tmp_path, spelling):
        r = global_config.read_own_node_id(_ini(tmp_path, f"[meshtastic]\nown_node_id = {spelling}\n"))
        assert r["state"] == "ok" and r["num"] == ME and r["raw"] == "!6201ce25"

    @pytest.mark.parametrize("bad", ["6201ce25", "!6201ce2", "!6201ce251", "!6201cezz",
                                     "!00000000", "!ffffffff", "1644285477", "!6201 ce25"])
    def test_what_the_author_cannot_have_meant_is_refused_loudly(self, tmp_path, bad):
        r = global_config.read_own_node_id(_ini(tmp_path, f"[meshtastic]\nown_node_id = {bad}\n"))
        assert r["state"] == "invalid" and r["num"] is None and r["why"]

    @pytest.mark.parametrize("key", ["node_id", "own_id", "my_node_num", "own-node"])
    def test_a_lookalike_key_is_refused_not_read_as_unset(self, tmp_path, key):
        r = global_config.read_own_node_id(_ini(tmp_path, f"[meshtastic]\n{key} = !6201ce25\n"))
        assert r["state"] == "invalid" and key in r["why"]

    def test_an_unrelated_key_beside_nothing_is_unset(self, tmp_path):
        r = global_config.read_own_node_id(_ini(tmp_path, "[meshtastic]\nhost = localhost\n"))
        assert r["state"] == "unset"

    def test_malformed_ini_is_unreadable_not_unset(self, tmp_path):
        r = global_config.read_own_node_id(_ini(tmp_path, "[meshtastic\nown_node_id = !6201ce25\n"))
        assert r["state"] == "unreadable" and r["num"] is None

    def test_binary_garbage_never_raises(self, tmp_path):
        p = tmp_path / "global.ini"
        p.write_bytes(b"\xff\xfe\x00[meshtastic]\x00")
        assert global_config.read_own_node_id(p)["state"] == "unreadable"

    def test_the_mqtt_reader_is_untouched_by_the_new_section(self, tmp_path):
        p = _ini(tmp_path, "[mqtt]\nbroker = b.example\n[meshtastic]\nown_node_id = !6201ce25\n")
        assert global_config.load_global_overrides(p)["mqtt_broker"] == "b.example"


class TestReaderR1Nits:
    """Fable r1 #4 / #5: the hfm #3 claim held only for 7 exact keys in an
    exact-case section, and [DEFAULT] leaked into [meshtastic]."""

    @pytest.mark.parametrize("hdr", ["[Meshtastic]", "[MESHTASTIC]", "[ meshtastic ]"])
    def test_section_case_and_padding_read(self, tmp_path, hdr):
        r = global_config.read_own_node_id(_ini(tmp_path, f"{hdr}\nown_node_id = !6201ce25\n"))
        assert r["state"] == "ok" and r["num"] == ME

    def test_the_key_under_the_wrong_section_is_refused(self, tmp_path):
        r = global_config.read_own_node_id(_ini(tmp_path, "[mqtt]\nown_node_id = !6201ce25\n"))
        assert r["state"] == "invalid" and "[mqtt]" in r["why"]

    @pytest.mark.parametrize("key", ["nodeid", "my_id", "own_node_ids", "node_num", "own node id"])
    def test_wider_lookalikes_are_refused(self, tmp_path, key):
        r = global_config.read_own_node_id(_ini(tmp_path, f"[meshtastic]\n{key} = !6201ce25\n"))
        assert r["state"] == "invalid", r

    def test_an_empty_value_beside_a_lookalike_is_refused(self, tmp_path):
        r = global_config.read_own_node_id(_ini(tmp_path, "[meshtastic]\nown_node_id =\nnode_id = !6201ce25\n"))
        assert r["state"] == "invalid"

    def test_default_never_leaks_into_the_section(self, tmp_path):
        r = global_config.read_own_node_id(_ini(tmp_path, "[DEFAULT]\nown_node_id = !6201ce25\n[meshtastic]\n"))
        assert r["state"] != "ok"


class TestReaderR2Nits:
    """r2 N4: substring lookalikes over every section of a SHARED file refused
    a valid id beside unrelated sister-app keys; a UTF-8 BOM was unreadable."""

    @pytest.mark.parametrize("extra", ["[power]\nshutdown_idle = 5\n", "[gateway]\ngateway_node_id = !11112222\n",
                                       "[meshcore]\nnode_id = abc\n", "[identity]\nmy_identity = x\n"])
    def test_other_sections_keys_never_refuse_a_valid_id(self, tmp_path, extra):
        r = global_config.read_own_node_id(_ini(tmp_path, extra + "[meshtastic]\nown_node_id = !6201ce25\n"))
        assert r["state"] == "ok" and r["num"] == ME, r

    def test_a_utf8_bom_reads(self, tmp_path):
        p = tmp_path / "global.ini"
        p.write_bytes(b"\xef\xbb\xbf[meshtastic]\nown_node_id = !6201ce25\n")
        assert global_config.read_own_node_id(p)["state"] == "ok"


class TestEnsureSectionR1Nits:

    @pytest.fixture(autouse=True)
    def _no_chown(self, monkeypatch):
        import utils.paths as up
        monkeypatch.setattr(up, "chown_to_operator", lambda *p: None)

    @pytest.mark.parametrize("hdr", ["[meshtastic] # radio", "[meshtastic];c", "[Meshtastic]"])
    def test_r1_3_a_header_configparser_accepts_is_never_duplicated(self, tmp_path, hdr):
        body = f"[mqtt]\nbroker = b.example\n{hdr}\nown_node_id = !6201ce25\n"
        p = _ini(tmp_path, body)
        global_config.ensure_own_node_id_section(p)
        assert p.read_text() == body
        assert global_config.load_global_overrides(p)["mqtt_broker"] == "b.example"

    def test_r1_6_an_undecodable_file_is_refused_as_oserror_and_untouched(self, tmp_path):
        p = tmp_path / "global.ini"
        p.write_bytes(b"\xff\xfe\x00[mqtt]\n")
        with pytest.raises(OSError):
            global_config.ensure_own_node_id_section(p)
        assert p.read_bytes() == b"\xff\xfe\x00[mqtt]\n"

    def test_a_malformed_file_is_refused_and_untouched(self, tmp_path):
        p = _ini(tmp_path, "[mqtt\nbroker = x\n")
        with pytest.raises(OSError):
            global_config.ensure_own_node_id_section(p)
        assert p.read_text() == "[mqtt\nbroker = x\n"

    def test_r1_7_a_symlink_is_written_through_not_replaced(self, tmp_path):
        real = _ini(tmp_path, "[mqtt]\nbroker = b.example\n")
        link = tmp_path / "link.ini"
        link.symlink_to(real)
        global_config.ensure_own_node_id_section(link)
        assert link.is_symlink() and "[meshtastic]" in real.read_text()


# ── resolve_own: config beside the connection, ONE answer ───────────────────
# History: r1 "corroborate first" -> r2 in-memory shelf life -> r3 NOT CLEAN
# (evidence age = first sight, not the header's age). Operator 11-27 "measure
# the id instead": config NEVER rules a self-DM out. Only a radio connection's
# read does; the configured id marks a DM addressed to it as self (never
# delivered); disagreeing evidence vetoes and is said. Test names below keep
# the round that drove them.

@pytest.fixture
def cfg(monkeypatch):
    own_radio._reset()
    state = {"v": {"state": "unset", "num": None, "why": ""}}
    monkeypatch.setattr(own_radio, "_read_config", lambda: state["v"])

    def set_(**kw):
        state["v"] = kw
    yield set_
    own_radio._reset()


W = own_radio.OWN_FRESH_S


class TestResolveOwn:

    def test_nothing_known_is_unknown(self, cfg):
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["self_only"] is None

    def test_an_uncorroborated_config_id_is_asserted_never_authority(self, cfg):
        cfg(state="ok", num=ME, why="")
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["self_only"] == ME and r["trust"] == "asserted"
        assert r["note"] == "" and "self" in r["info"]

    def test_connection_alone_is_today(self, cfg):
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] == ME and r["source"] == "map collector"

    def test_a_fresh_agreeing_connection_corroborates(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0 + W - 1)
        assert r["num"] == ME and "map collector" in r["source"]

    def test_r2_s3_an_old_agreeing_connection_does_not_corroborate(self, cfg):
        """r2 I-A S3: a 5 h-old agreeing read stamped the config id after a swap."""
        cfg(state="ok", num=ME, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0 + 5 * 3600)
        assert r["num"] is None and r["self_only"] == ME and r["trust"] == "asserted"

    def test_r3_a_matching_header_never_makes_config_authority(self, cfg):
        """r3 I-1 + F-1, operator 11-27 "measure the id instead": header
        evidence ages by first sight, not by the header's own age, so a match
        can be stale by hours. Config alone NEVER rules a self-DM out."""
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, ME)], now=1000.0)
        r = own_radio.resolve_own(now=1001.0)
        assert r["num"] is None and r["self_only"] == ME and r["trust"] == "asserted"

    def test_r2_s2_header_corroboration_expires(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, ME)], now=1000.0)
        r = own_radio.resolve_own(now=1000.0 + W + 1)
        assert r["num"] is None and r["trust"] == "asserted"

    def test_r2_s2_a_restart_forgets_corroboration(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, ME)], now=1000.0)
        own_radio._reset()
        assert own_radio.resolve_own(now=1001.0)["trust"] == "asserted"

    def test_a_future_header_never_corroborates(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, ME)], now=5000.0)
        assert own_radio.resolve_own(now=1000.0)["num"] is None

    def test_a_contradicting_header_vetoes_and_names_both(self, cfg):
        cfg(state="ok", num=OTHER, why="")
        own_radio.observe_own_headers([(1, ME)], now=1000.0)
        r = own_radio.resolve_own(now=1001.0)
        assert r["num"] is None and r["self_only"] is None and r["trust"] == "contradicted"
        assert "!6201ce25" in r["note"] and "!0a1b2c3d" in r["note"]

    def test_r2_s4_a_contradiction_is_sticky_against_re_reads(self, cfg):
        """r2 I-B S4: read 3 re-observed p0 (match) after p1 (contra) left the
        ring and flipped back to corroborated."""
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(0, ME)], now=1000.0)
        own_radio.observe_own_headers([(0, ME), (1, OTHER)], now=1010.0)
        own_radio.observe_own_headers([(0, ME)], now=1020.0)
        r = own_radio.resolve_own(now=1030.0)
        assert r["num"] is None and r["self_only"] is None and r["trust"] == "contradicted"

    def test_a_match_after_a_contradiction_inside_the_window_still_refuses(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, OTHER)], now=1000.0)
        own_radio.observe_own_headers([(2, ME)], now=1100.0)
        assert own_radio.resolve_own(now=1200.0)["trust"] == "contradicted"

    def test_a_contradiction_expires_to_asserted_not_corroborated(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, OTHER)], now=1000.0)
        r = own_radio.resolve_own(now=1000.0 + W + 1)
        assert r["trust"] == "asserted" and r["num"] is None

    def test_a_future_contradiction_refuses(self, cfg):
        """hfm #6: a clock step must never turn doubt into authority."""
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(2, ME)], now=1000.0)
        own_radio.observe_own_headers([(1, OTHER)], now=9000.0)
        assert own_radio.resolve_own(now=1001.0)["num"] is None

    def test_r3_a_config_flap_never_re_admits_authority(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, ME)], now=1000.0)
        cfg(state="ok", num=OTHER, why="")
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, ME)], now=1000.0 + 5 * 3600)
        assert own_radio.resolve_own(now=1000.0 + 5 * 3600)["num"] is None

    def test_each_header_counts_once_from_first_sight(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, ME)], now=1000.0)
        own_radio.observe_own_headers([(1, ME)], now=1000.0 + W - 10)   # same pid re-read
        assert own_radio.resolve_own(now=1000.0 + W + 1)["trust"] == "asserted"

    def test_the_seen_set_is_bounded(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(i, ME) for i in range(own_radio.SEEN_PIDS_MAX * 3)], now=1000.0)
        assert len(own_radio._EVID["pids"]) <= own_radio.SEEN_PIDS_MAX

    @pytest.mark.parametrize("item", [None, (1, None), (1, 0), (1, 0xFFFFFFFF), (1, True),
                                      (1, "!6201ce25"), (1, 2 ** 40), (None, ME), "x", (1,)])
    def test_garbage_header_items_are_no_evidence(self, cfg, item):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([item], now=1000.0)
        assert own_radio.resolve_own(now=1001.0)["trust"] == "asserted"

    def test_headers_without_config_are_ignored(self, cfg):
        own_radio.observe_own_headers([(1, ME)], now=1000.0)
        assert own_radio.resolve_own(now=1001.0)["num"] is None

    def test_disagreeing_connection_stamps_nothing_and_says_both(self, cfg):
        cfg(state="ok", num=OTHER, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["self_only"] is None and r["config"] == "mismatch"
        assert "!0a1b2c3d" in r["note"] and "!6201ce25" in r["note"]

    @pytest.mark.parametrize("age", [W + 1, 86400, -600])
    def test_r1_i1_an_old_or_future_disagreeing_connection_still_vetoes(self, cfg, age):
        cfg(state="ok", num=OTHER, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0 + age)
        assert r["num"] is None and r["self_only"] is None and r["config"] == "mismatch"

    def test_a_disagreeing_connection_vetoes_even_beside_a_matching_header(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([(1, ME)], now=1000.0)
        own_radio.note_own_node(OTHER, source="map collector", now=10.0)
        assert own_radio.resolve_own(now=1001.0)["num"] is None

    @pytest.mark.parametrize("state", ["invalid", "unreadable"])
    def test_a_refused_config_never_hides_the_connection_and_is_said(self, cfg, state):
        cfg(state=state, num=None, why="own_node_id '6201ce25' is not !xxxxxxxx")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] == ME and "refused" in r["note"]

    @pytest.mark.parametrize("garbage", [None, [], "x", {"state": "ok", "num": True},
                                         {"state": "ok", "num": 0},
                                         {"state": "ok", "num": 0xFFFFFFFF},
                                         {"state": "OK", "num": ME}, {"num": ME},
                                         {"state": "weird", "num": ME}])
    def test_a_garbage_config_answer_is_never_a_node_and_is_said(self, cfg, monkeypatch, garbage):
        monkeypatch.setattr(own_radio, "_read_config", lambda: garbage)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["self_only"] is None and r["note"]

    def test_a_raising_reader_never_costs_the_send(self, monkeypatch):
        import utils.global_config as gc
        monkeypatch.undo()
        monkeypatch.setattr(gc, "read_own_node_id", lambda: 1 / 0)
        r = own_radio._read_config()
        assert r["state"] == "unreadable" and r["num"] is None

    def test_the_config_is_read_once_per_header_batch(self, cfg, monkeypatch):
        calls = {"n": 0}
        cfg(state="ok", num=ME, why="")
        real = own_radio._read_config

        def counted():
            calls["n"] += 1
            return real()
        monkeypatch.setattr(own_radio, "_read_config", counted)
        own_radio.observe_own_headers([(i, ME) for i in range(50)], now=1000.0)
        assert calls["n"] == 1

    def test_nothing_is_persisted(self, cfg):
        """r2 I-A/I-B/N1/N2: the persisted record outlived swaps, could be
        hand-forged, failed open on a write error. Evidence is in-memory now."""
        import inspect
        src = inspect.getsource(own_radio)
        assert "_corr_path" not in src and "atomic_write" not in src


# ── the consumers: the send stamp and the witness line ─────────────────────

def _send(monkeypatch, tmp_path, dest):
    from commands import messaging as messaging_mod
    from utils import inbox_api
    import gateway.meshtastic_protobuf_client as pc
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: tmp_path / "messages.db")
    monkeypatch.setattr(pc, "send_text_direct_with_id", lambda **kw: 0x1234ABCD)
    params, err = inbox_api.validate_send({"network": "meshtastic", "text": "hi", "destination": dest})
    assert err is None
    status, body = inbox_api.send(params)
    assert status == 200, body
    conn = messaging_mod._init_db()
    try:
        return [tuple(r) for r in conn.execute("SELECT own_id FROM dm_origin").fetchall()]
    finally:
        conn.close()


class TestConsumers:

    def _snap(self):
        from utils.dm_delivery import Witness
        return {"witness": Witness(None, "no witness in this test"), "replies": [], "tx": {},
                "tx_from": {}, "channels": {}, "decrypted": {}, "counts": {}}

    def _line(self, now=1000.0):
        from utils import inbox_api
        return inbox_api._witness_line(self._snap(), now, 0, own_radio.resolve_own(now, fresh=False))

    def test_the_witness_line_says_an_asserted_id(self, cfg):
        cfg(state="ok", num=ME, why="")
        w = self._line()
        assert "own radio id: unknown" in w["line"] and "!6201ce25" in w["line"]
        assert "marks a DM to it as self" in w["line"]

    def test_r3_f1_an_asserted_id_is_said_without_a_warning(self, cfg):
        """r3 F-1: a correctly configured idle box carried a permanent ⚠."""
        cfg(state="ok", num=ME, why="")
        w = self._line()
        assert "⚠" not in w["line"] and "!6201ce25" in w["line"] and w["own_id"] is None

    def test_the_witness_line_says_a_mismatch(self, cfg):
        cfg(state="ok", num=OTHER, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        w = self._line()
        assert "own radio id: unknown" in w["line"] and "⚠" in w["line"]
        assert "!0a1b2c3d" in w["own_id_note"]

    def test_the_witness_line_says_a_refusal(self, cfg):
        cfg(state="invalid", num=None, why="own_node_id '6201ce25' is not !xxxxxxxx (8 hex digits)")
        w = self._line()
        assert "refused" in w["line"] and "6201ce25" in w["line"]

    def test_r3_i1_a_matching_header_never_stamps_config_on_a_dm_elsewhere(self, cfg, tmp_path, monkeypatch):
        """r3 I-1 end of the stamp: a header that matched (possibly hours stale)
        never lets config rule a self-DM out on a DM to another node."""
        import time as _t
        cfg(state="ok", num=OTHER, why="")
        own_radio.observe_own_headers([(1, OTHER)], now=_t.time())
        assert _send(monkeypatch, tmp_path, "!6201ce25") == []

    def test_r1_i2_an_asserted_config_id_never_rules_a_self_dm_out(self, cfg, tmp_path, monkeypatch):
        from utils import dm_delivery as dd
        cfg(state="ok", num=OTHER, why="")
        assert _send(monkeypatch, tmp_path, "!6201ce25") == []
        assert dd.dm_origin("!6201ce25", None, None).state == dd.ORIGIN_UNKNOWN

    def test_r2_s2_a_swap_after_corroboration_never_rules_a_self_dm_out(self, cfg, tmp_path, monkeypatch):
        """r2 I-A S2 end of the stamp: config X corroborated, then the radio is
        Y; after the window (or a restart) a DM to Y is never stamped X."""
        import time as _t
        cfg(state="ok", num=OTHER, why="")
        own_radio.observe_own_headers([(1, OTHER)], now=_t.time() - W - 5)
        assert _send(monkeypatch, tmp_path, "!6201ce25") == []

    def test_an_asserted_config_id_marks_a_dm_to_itself_self(self, cfg, tmp_path, monkeypatch):
        from utils import dm_delivery as dd
        cfg(state="ok", num=ME, why="")
        assert _send(monkeypatch, tmp_path, "!6201ce25") == [(ME,)]
        assert dd.dm_origin("!6201ce25", ME, None).state == dd.ORIGIN_SELF

    def test_a_mismatch_stamps_nothing(self, cfg, tmp_path, monkeypatch):
        import time as _t
        cfg(state="ok", num=OTHER, why="")
        own_radio.note_own_node(ME, source="map collector", now=_t.time())
        assert _send(monkeypatch, tmp_path, "!0b0b0b0b") == []

    def test_the_inbox_read_feeds_own_headers_by_packet_id(self):
        import inspect
        from utils import inbox_api
        src = inspect.getsource(inbox_api)
        assert "observe_own_headers(" in src and '(m["packet_id"], tx_from.get(m["packet_id"]))' in src


# ── the TUI surface: the editor's file prep + the summary screen ────────────

class TestTuiSurface:

    @pytest.fixture(autouse=True)
    def _no_chown(self, monkeypatch):
        import utils.paths as up
        monkeypatch.setattr(up, "chown_to_operator", lambda *p: None)

    def test_absent_file_gets_the_template_and_reads_unset(self, tmp_path):
        p = global_config.ensure_own_node_id_section(tmp_path / "cfg" / "global.ini")
        assert "[meshtastic]" in p.read_text()
        assert global_config.read_own_node_id(p)["state"] == "unset"

    def test_existing_sections_are_kept_and_the_section_appended_once(self, tmp_path):
        p = _ini(tmp_path, "[mqtt]\nbroker = b.example\n")
        global_config.ensure_own_node_id_section(p)
        global_config.ensure_own_node_id_section(p)
        text = p.read_text()
        assert text.count("[meshtastic]") == 1 and "broker = b.example" in text
        assert global_config.load_global_overrides(p)["mqtt_broker"] == "b.example"

    def test_an_existing_meshtastic_section_is_never_rewritten(self, tmp_path):
        body = "[meshtastic]\nown_node_id = !6201ce25\n"
        p = _ini(tmp_path, body)
        global_config.ensure_own_node_id_section(p)
        assert p.read_text() == body

    def _summary(self, own):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "launcher_tui"))
        from handlers.messaging import own_id_summary
        return own_id_summary(own)

    def test_summary_names_the_source(self):
        s = self._summary({"num": ME, "source": "config", "config": "ok", "note": ""})
        assert "!6201ce25" in s and "from config" in s and "WARNING" not in s

    def test_summary_says_unknown_and_how_to_set_it(self):
        s = self._summary({"num": None, "source": "", "config": "unset", "note": ""})
        assert "UNKNOWN" in s and "own_node_id" in s

    def test_summary_says_a_mismatch(self):
        s = self._summary({"num": None, "source": "", "config": "mismatch",
                           "note": "config says !0a1b2c3d but map collector read !6201ce25"})
        assert "UNKNOWN" in s and "WARNING" in s and "!0a1b2c3d" in s

    @pytest.mark.parametrize("garbage", [None, [], {"num": True}, {"num": "!6201ce25"}])
    def test_summary_never_raises_or_invents_an_id(self, garbage):
        assert "UNKNOWN" in self._summary(garbage)

    def test_r3_n4_the_tui_never_claims_a_recorded_corroboration(self):
        s = self._summary({"num": None, "self_only": ME, "source": "", "config": "ok",
                           "trust": "asserted", "note": "", "info": "x"})
        assert "recorded" not in s and "corroborat" not in s.lower()


class TestR3BomSymmetry:

    def test_r3_n3_the_editor_opens_what_the_reader_accepts(self, tmp_path, monkeypatch):
        import utils.paths as up
        monkeypatch.setattr(up, "chown_to_operator", lambda *p: None)
        p = tmp_path / "global.ini"
        p.write_bytes(b"\xef\xbb\xbf[mqtt]\nbroker = b\n")
        global_config.ensure_own_node_id_section(p)
        assert "[meshtastic]" in p.read_text(encoding="utf-8-sig")
