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


# ── resolve_own: config beside the connection, ONE answer ───────────────────

@pytest.fixture
def cfg(monkeypatch):
    own_radio._reset()
    state = {"v": {"state": "unset", "num": None, "why": ""}}
    monkeypatch.setattr(own_radio, "_read_config", lambda: state["v"])

    def set_(**kw):
        state["v"] = kw
    yield set_
    own_radio._reset()


class TestResolveOwn:

    def test_nothing_known_is_unknown(self, cfg):
        assert own_radio.resolve_own(now=1000.0)["num"] is None

    def test_config_alone_is_used_and_named(self, cfg):
        cfg(state="ok", num=ME, why="")
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] == ME and r["source"] == "config" and r["note"] == ""

    def test_connection_alone_is_today(self, cfg):
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] == ME and r["source"] == "map collector"

    def test_agreeing_sources_say_so(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] == ME and "config" in r["source"] and "map collector" in r["source"]

    def test_disagreeing_sources_stamp_nothing_and_say_both(self, cfg):
        cfg(state="ok", num=OTHER, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] is None and r["config"] == "mismatch"
        assert "!0a1b2c3d" in r["note"] and "!6201ce25" in r["note"]

    def test_a_stale_connection_id_does_not_contest_config_at_send(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.note_own_node(OTHER, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0 + own_radio.OWN_FRESH_S + 5)
        assert r["num"] == ME and r["source"] == "config"

    def test_the_witness_view_still_sees_an_old_disagreeing_connection(self, cfg):
        cfg(state="ok", num=ME, why="")
        own_radio.note_own_node(OTHER, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0 + own_radio.OWN_FRESH_S + 5, fresh=False)
        assert r["num"] is None and r["config"] == "mismatch"

    @pytest.mark.parametrize("state", ["invalid", "unreadable"])
    def test_a_refused_config_never_hides_the_connection_and_is_said(self, cfg, state):
        cfg(state=state, num=None, why="own_node_id '6201ce25' is not !xxxxxxxx")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        r = own_radio.resolve_own(now=1000.0)
        assert r["num"] == ME and "refused" in r["note"]

    @pytest.mark.parametrize("garbage", [None, [], "x", {"state": "ok", "num": True},
                                         {"state": "ok", "num": 0},
                                         {"state": "ok", "num": 0xFFFFFFFF}])
    def test_a_garbage_config_answer_is_never_a_node(self, cfg, monkeypatch, garbage):
        monkeypatch.setattr(own_radio, "_read_config", lambda: garbage)
        assert own_radio.resolve_own(now=1000.0)["num"] is None

    def test_a_raising_reader_never_costs_the_send(self, monkeypatch):
        import utils.global_config as gc
        monkeypatch.undo()          # the real _read_config, a broken reader under it
        monkeypatch.setattr(gc, "read_own_node_id", lambda: 1 / 0)
        r = own_radio._read_config()
        assert r["state"] == "unreadable" and r["num"] is None


# ── the consumers: the send stamp and the witness line ─────────────────────

class TestConsumers:

    def _snap(self):
        from utils.dm_delivery import Witness
        return {"witness": Witness(None, "no witness in this test"), "replies": [], "tx": {},
                "tx_from": {}, "channels": {}, "decrypted": {}, "counts": {}}

    def test_the_witness_line_names_config_as_the_source(self, cfg):
        from utils import inbox_api
        cfg(state="ok", num=ME, why="")
        w = inbox_api._witness_line(self._snap(), 1000.0, 0, own_radio.resolve_own(1000.0, fresh=False))
        assert "own radio id: !6201ce25 (from config)" in w["line"]
        assert w["own_id"] == "!6201ce25" and w["own_id_source"] == "config"

    def test_the_witness_line_says_a_mismatch(self, cfg):
        from utils import inbox_api
        cfg(state="ok", num=OTHER, why="")
        own_radio.note_own_node(ME, source="map collector", now=1000.0)
        w = inbox_api._witness_line(self._snap(), 1000.0, 0, own_radio.resolve_own(1000.0, fresh=False))
        assert "own radio id: unknown" in w["line"] and "⚠" in w["line"]
        assert "!0a1b2c3d" in w["own_id_note"]

    def test_the_witness_line_says_a_refusal(self, cfg):
        from utils import inbox_api
        cfg(state="invalid", num=None, why="own_node_id '6201ce25' is not !xxxxxxxx (8 hex digits)")
        w = inbox_api._witness_line(self._snap(), 1000.0, 0, own_radio.resolve_own(1000.0, fresh=False))
        assert "refused" in w["line"] and "6201ce25" in w["line"]

    def test_the_send_stamps_the_configured_id(self, cfg, tmp_path, monkeypatch):
        """The END of the slice: with no connection id, a DM's row now carries
        the configured id, so dm_origin can rule a self-DM out."""
        from commands import messaging as messaging_mod
        from utils import inbox_api
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: tmp_path / "messages.db")
        import gateway.meshtastic_protobuf_client as pc
        monkeypatch.setattr(pc, "send_text_direct_with_id", lambda **kw: 0x1234ABCD)
        cfg(state="ok", num=ME, why="")
        params, err = inbox_api.validate_send(
            {"network": "meshtastic", "text": "hi", "destination": "!0a1b2c3d"})
        assert err is None
        status, body = inbox_api.send(params)
        assert status == 200 and body.get("stamp_error") is None, body
        conn = messaging_mod._init_db()
        try:
            row = conn.execute("SELECT own_id FROM dm_origin").fetchall()
        finally:
            conn.close()
        assert [tuple(r) for r in row] == [(ME,)]

    def test_a_mismatch_stamps_nothing(self, cfg, tmp_path, monkeypatch):
        from commands import messaging as messaging_mod
        from utils import inbox_api
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: tmp_path / "messages.db")
        import gateway.meshtastic_protobuf_client as pc
        monkeypatch.setattr(pc, "send_text_direct_with_id", lambda **kw: 0x1234ABCD)
        cfg(state="ok", num=OTHER, why="")
        import time as _t
        own_radio.note_own_node(ME, source="map collector", now=_t.time())
        params, _ = inbox_api.validate_send(
            {"network": "meshtastic", "text": "hi", "destination": "!0b0b0b0b"})
        inbox_api.send(params)
        conn = messaging_mod._init_db()
        try:
            assert conn.execute("SELECT own_id FROM dm_origin").fetchall() == []
        finally:
            conn.close()


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
