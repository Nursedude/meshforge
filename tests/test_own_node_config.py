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
# Fable r1 (7a8b185a) I2 + operator 11-27 "corroborate first": a configured id
# rules a self-DM OUT only once a connection read or one of this box's own DM
# headers has matched it. Until then it is ASSERTED: it may mark a DM to that
# very id as self (never delivered), nothing more.

@pytest.fixture
def cfg(monkeypatch, tmp_path):
    own_radio._reset()
    monkeypatch.setattr(own_radio, "_corr_path", lambda: tmp_path / "own_corr.json")
    state = {"v": {"state": "unset", "num": None, "why": ""}}
    monkeypatch.setattr(own_radio, "_read_config", lambda: state["v"])

    def set_(**kw):
        state["v"] = kw
    yield set_
    own_radio._reset()


class TestResolveOwn:

    def test_nothing_known_is_unknown(self, cfg):
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["self_only"] is None

    def test_an_uncorroborated_config_id_is_asserted_never_authority(self, cfg):
        cfg(state="ok", num=ME, why="")
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["self_only"] == ME and r["trust"] == "asserted"
        assert "not yet corroborated" in r["note"]

    def test_connection_alone_is_today(self, cfg):
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] == ME and r["source"] == "map collector"

    def test_agreeing_connection_corroborates_and_persists(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0, record=True)
        assert r["num"] == ME and r["trust"] == "corroborated"
        own_radio._reset()                      # a new process: no connection id
        r = own_radio.resolve_own(now=2000.0)
        assert r["num"] == ME and r["trust"] == "corroborated" and "map collector" in r["source"]

    def test_a_read_only_view_never_writes_corroboration(self, cfg, tmp_path):
        cfg(state="ok", num=ME, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        own_radio.resolve_own(now=1000.0)       # record defaults False
        assert not (tmp_path / "own_corr.json").exists()

    def test_disagreeing_sources_stamp_nothing_and_say_both(self, cfg):
        cfg(state="ok", num=OTHER, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["self_only"] is None and r["config"] == "mismatch"
        assert "!0a1b2c3d" in r["note"] and "!6201ce25" in r["note"]

    @pytest.mark.parametrize("age", [own_radio.OWN_FRESH_S + 1, 86400, -600])
    def test_r1_i1_an_old_or_future_disagreeing_connection_still_vetoes(self, cfg, age):
        """Fable r1 I1: freshness gated the veto too, so a contradiction the box
        had SEEN was absorbed after an hour and the wrong config id stamped."""
        cfg(state="ok", num=OTHER, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0 + age)
        assert r["num"] is None and r["self_only"] is None and r["config"] == "mismatch"

    def test_a_header_corroborates(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_header(ME, now=1000.0)
        r = own_radio.resolve_own(now=1001.0)
        assert r["num"] == ME and r["trust"] == "corroborated" and "header" in r["source"]

    def test_a_header_contradicts_and_the_contradiction_persists(self, cfg):
        cfg(state="ok", num=OTHER, why="")
        own_radio.observe_own_header(ME, now=1000.0)
        own_radio._reset()
        r = own_radio.resolve_own(now=2000.0)
        assert r["num"] is None and r["self_only"] is None and r["trust"] == "contradicted"
        assert "!6201ce25" in r["note"]

    def test_latest_evidence_wins(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_header(OTHER, now=1000.0)
        own_radio.observe_own_header(ME, now=1100.0)
        assert own_radio.resolve_own(now=1200.0)["trust"] == "corroborated"

    def test_a_changed_config_value_starts_uncorroborated(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_header(ME, now=1000.0)
        cfg(state="ok", num=OTHER, why="")
        r = own_radio.resolve_own(now=1100.0)
        assert r["trust"] == "asserted" and r["self_only"] == OTHER

    @pytest.mark.parametrize("body", ["{", "[]", '{"config_num": "x"}', '{"config_num": 1644285477, "state": "weird"}'])
    def test_a_garbage_record_is_never_corroboration(self, cfg, tmp_path, body):
        cfg(state="ok", num=ME, why="")
        (tmp_path / "own_corr.json").write_text(body)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["trust"] == "asserted"

    def test_a_header_without_config_writes_nothing(self, cfg, tmp_path):
        own_radio.observe_own_header(ME, now=1000.0)
        assert not (tmp_path / "own_corr.json").exists()

    @pytest.mark.parametrize("hdr", [None, 0, 0xFFFFFFFF, True, "!6201ce25", 2 ** 40])
    def test_a_garbage_header_is_no_evidence(self, cfg, tmp_path, hdr):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_header(hdr, now=1000.0)
        assert not (tmp_path / "own_corr.json").exists()

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
        """r1 #8: an answer outside the reader's closed set is refused, said."""
        monkeypatch.setattr(own_radio, "_read_config", lambda: garbage)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["self_only"] is None and r["note"]

    def test_a_raising_reader_never_costs_the_send(self, monkeypatch):
        import utils.global_config as gc
        monkeypatch.undo()          # the real _read_config, a broken reader under it
        monkeypatch.setattr(gc, "read_own_node_id", lambda: 1 / 0)
        r = own_radio._read_config()
        assert r["state"] == "unreadable" and r["num"] is None


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
        assert "not yet corroborated" in w["line"]

    def test_the_witness_line_names_a_corroborated_config(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_header(ME, now=999.0)
        w = self._line()
        assert "own radio id: !6201ce25 (from config" in w["line"] and w["own_id"] == "!6201ce25"

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

    def test_a_corroborated_config_id_is_stamped(self, cfg, tmp_path, monkeypatch):
        """The END of the slice: no connection id, a corroborated config -> the
        row carries it, so dm_origin can rule a self-DM out."""
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_header(ME, now=1000.0)
        assert _send(monkeypatch, tmp_path, "!0a1b2c3d") == [(ME,)]

    def test_r1_i2_an_asserted_config_id_never_rules_a_self_dm_out(self, cfg, tmp_path, monkeypatch):
        """Fable r1 I2: a WRONG config id (OTHER) with nothing contesting it was
        stamped on a DM to the real own radio (ME) -> dm_origin 'remote' -> the
        own radio's ack read 'delivered'. Asserted ids stamp only a DM to
        themselves."""
        from utils import dm_delivery as dd
        cfg(state="ok", num=OTHER, why="")
        assert _send(monkeypatch, tmp_path, "!6201ce25") == []
        assert dd.dm_origin("!6201ce25", None, None).state == dd.ORIGIN_UNKNOWN

    def test_an_asserted_config_id_marks_a_dm_to_itself_self(self, cfg, tmp_path, monkeypatch):
        from utils import dm_delivery as dd
        cfg(state="ok", num=ME, why="")
        stamp = _send(monkeypatch, tmp_path, "!6201ce25")
        assert stamp == [(ME,)]
        assert dd.dm_origin("!6201ce25", ME, None).state == dd.ORIGIN_SELF

    def test_a_mismatch_stamps_nothing(self, cfg, tmp_path, monkeypatch):
        import time as _t
        cfg(state="ok", num=OTHER, why="")
        own_radio.note_own_node(ME, source="map collector", now=_t.time())
        assert _send(monkeypatch, tmp_path, "!0b0b0b0b") == []

    def test_the_inbox_read_feeds_own_headers_to_corroboration(self):
        """The judge loop is where this box SEES its own DMs' headers; the
        corroboration writer must be called there (wiring pin)."""
        import inspect
        from utils import inbox_api
        assert "observe_own_headers(" in inspect.getsource(inbox_api)


# ── the TUI surface: the editor's file prep + the summary screen ────────────

class TestTuiSurface:

    def test_absent_file_gets_the_template_and_reads_unset(self, tmp_path, monkeypatch):
        import utils.paths as up
        monkeypatch.setattr(up, "chown_to_operator", lambda *p: None)
        p = global_config.ensure_own_node_id_section(tmp_path / "cfg" / "global.ini")
        assert "[meshtastic]" in p.read_text()
        assert global_config.read_own_node_id(p)["state"] == "unset"

    def test_existing_sections_are_kept_and_the_section_appended_once(self, tmp_path, monkeypatch):
        import utils.paths as up
        monkeypatch.setattr(up, "chown_to_operator", lambda *p: None)
        p = _ini(tmp_path, "[mqtt]\nbroker = b.example\n")
        global_config.ensure_own_node_id_section(p)
        global_config.ensure_own_node_id_section(p)
        text = p.read_text()
        assert text.count("[meshtastic]") == 1 and "broker = b.example" in text
        assert global_config.load_global_overrides(p)["mqtt_broker"] == "b.example"

    def test_an_existing_meshtastic_section_is_never_rewritten(self, tmp_path, monkeypatch):
        import utils.paths as up
        monkeypatch.setattr(up, "chown_to_operator", lambda *p: None)
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

    def test_summary_says_an_asserted_id_is_not_yet_corroborated(self):
        s = self._summary({"num": None, "self_only": ME, "source": "", "config": "ok",
                           "trust": "asserted", "note": "configured !6201ce25 not yet corroborated"})
        assert "UNKNOWN" in s and "NOT YET CORROBORATED" in s and "!6201ce25" in s
        assert "live view" in s


class TestBatchedHeaders:

    def test_one_contradiction_in_a_read_outranks_agreement(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([ME, OTHER, ME, None], now=1000.0)
        assert own_radio.resolve_own(now=1001.0)["trust"] == "contradicted"

    def test_agreement_corroborates_and_garbage_is_ignored(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.observe_own_headers([None, True, 0, ME], now=1000.0)
        assert own_radio.resolve_own(now=1001.0)["trust"] == "corroborated"

    def test_the_config_is_read_once(self, cfg, monkeypatch):
        calls = {"n": 0}
        real = own_radio._read_config

        def counted():
            calls["n"] += 1
            return real()
        cfg(state="ok", num=ME, why="")
        monkeypatch.setattr(own_radio, "_read_config", counted)
        own_radio.observe_own_headers([ME] * 50, now=1000.0)
        assert calls["n"] == 1
