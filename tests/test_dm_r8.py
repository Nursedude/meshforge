"""BIC-2 DM slice 1 -- Fable r8 (2026-11-26) folds, red on e5deaf76.

Operator 2026-11-26 (AskUserQuestion): "Small: one seam + pins, r9".
r8 #1 (important, from the r7 fold): sends now store ``to_id`` as
``!%08x`` but the page keyed its new-DM draft by the TYPED id, so a short
id left a phantom empty draft and the sent row landed in another thread.
The seam: the partner is canonicalised in ONE place, ``_thread_key``, and
the page pads the id the same way and re-keys from the send's ``to_id``.
"""
import inspect
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from commands import messaging as messaging_mod  # noqa: E402
from utils import dm_ack_witness, inbox_api, own_radio  # noqa: E402
from utils.dm_delivery import ROW_DELIVERED, RoutingReply, Send, Witness, row_verdict  # noqa: E402

ME = 0x6201CE25
P5 = 0xB03DA894
PID = 0xAB3C8AC6
T0 = 1_795_000_000.0
ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.fixture(autouse=True)
def _clean():
    own_radio._reset()
    inbox_api._REFUTED.clear()
    yield
    own_radio._reset()
    inbox_api._REFUTED.clear()


def _row(to_id, frm="local"):
    return {"network": "meshtastic", "from_id": frm, "to_id": to_id, "channel": 0}


class TestOnePartnerSpelling:

    @pytest.mark.parametrize("spelling", ["!a1b2c3d", "!A1B2C3D", "!0a1b2c3d", "!0A1B2C3D"])
    def test_every_spelling_of_one_node_is_one_thread(self, spelling):
        key, kind, partner, _ = inbox_api._thread_key(_row(spelling))
        assert (key, kind, partner) == ("meshtastic:!0a1b2c3d", "dm", "!0a1b2c3d")

    def test_a_received_row_joins_the_same_thread(self):
        assert inbox_api._thread_key({"network": "meshtastic", "from_id": "!0A1B2C3D",
                                      "to_id": "!6201ce25"})[0] == "meshtastic:!0a1b2c3d"

    @pytest.mark.parametrize("bcast", ["!FFFFFFFF", "!ffffffff"])
    def test_any_broadcast_spelling_is_the_channel(self, bcast):
        assert inbox_api._thread_key(_row(bcast))[1] == "channel"

    def test_a_non_node_partner_is_left_as_stored(self):
        assert inbox_api._thread_key(_row("someone"))[2] == "someone"

    def test_rns_partners_are_untouched(self):
        assert inbox_api._thread_key({"network": "rns", "from_id": "local",
                                      "to_id": "ABCDEF"})[2] == "ABCDEF"

    def test_the_send_body_returns_the_canonical_id(self, tmp_path, monkeypatch):
        db = tmp_path / "messages.db"
        monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
        params, _ = inbox_api.validate_send({"network": "meshtastic", "text": "x",
                                             "destination": "!a1b2c3d"})
        with patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
                   return_value=PID):
            code, body = inbox_api.send(params)
        assert code == 200 and body["to_id"] == "!0a1b2c3d"


class TestThePage:
    """No JS engine on the box: the page's handlers are pinned by source."""

    def _html(self):
        with open(os.path.join(ROOT, "web", "inbox.html"), encoding="utf-8") as f:
            return f.read()

    def test_the_new_dm_id_is_padded_like_the_server(self):
        assert 'padStart(8, "0")' in self._html()

    def test_the_draft_is_re_keyed_from_the_sends_to_id(self):
        html = self._html()
        assert "j.to_id" in html and "rekeyDraft(" in html

    def test_a_stamp_error_reaches_the_operator(self):
        assert "j.stamp_error" in self._html()


# --- pins for r8's surviving mutants ------------------------------------------

def _kept_self_dm(tmp_path, monkeypatch):
    db = tmp_path / "messages.db"
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db)
    monkeypatch.setattr(messaging_mod, "_last_message_prune_ts", 0.0)
    r = messaging_mod.store_outgoing("Wx", "meshtastic", "!6201ce25", channel=0,
                                     delivered=True, packet_id=PID, own_id=ME)
    mid = r.data["message_id"]
    messaging_mod.keep_dm([(mid, PID, {"state": ROW_DELIVERED, "detail": "delivered",
                                       "latency_s": 0.0})])
    return mid


def _read(mid):
    class W:
        def snapshot(self, now=None):
            return {"witness": Witness(T0 - 10 ** 9), "replies": [], "tx": {}, "tx_from": {},
                    "channels": {}, "decrypted": {}, "counts": {}}
    with patch.object(inbox_api.time, "time", return_value=T0), \
         patch("utils.message_listener.get_listener_status", return_value={}), \
         patch("utils.inbox_legs.load_sections", return_value=None), \
         patch.object(dm_ack_witness, "get_witness", return_value=W()):
        return inbox_api.read_inbox()


def test_m76_the_witness_line_says_a_refute_only_after_one(tmp_path, monkeypatch):
    mid = _kept_self_dm(tmp_path, monkeypatch)
    snap = {"witness": Witness(T0 - 60), "replies": [], "tx": {}, "tx_from": {},
            "channels": {}, "decrypted": {}, "counts": {}}
    assert "refuted" not in inbox_api._witness_line(snap, T0, 0)["line"]
    out = _read(mid)
    assert "refuted by the origin and dropped" in out["dm_witness"]["line"]


def test_m73_a_failed_drop_is_never_counted(tmp_path, monkeypatch):
    mid = _kept_self_dm(tmp_path, monkeypatch)
    with patch.object(messaging_mod, "drop_dm_kept", side_effect=RuntimeError("locked")):
        out = _read(mid)
    assert out["dm_witness"]["refuted_kept"] == 0


def test_m60_the_validator_binds_the_seams_pattern():
    src = inspect.getsource(inbox_api)
    assert "_NODE_ID = NODE_ID_RE" in src and "re.compile(r\"^![0-9a-fA-F]{1,8}$\")" not in src


@pytest.mark.parametrize("bcast", ["!FFFFFFFF", "!ffffffff"])
def test_m85_a_broadcast_spelling_never_asks_for_an_ack(bcast):
    params, err = inbox_api.validate_send({"network": "meshtastic", "text": "x",
                                           "destination": bcast})
    assert err is None and params["dest_num"] is None and params["to_id"] is None


@pytest.mark.parametrize("bad", ["a1b2c3d4", "!a1b2c3d4\n", " !a1b2c3d4"])
def test_m90_only_a_bang_id_is_a_destination(bad):
    params, err = inbox_api.validate_send({"network": "meshtastic", "text": "x",
                                           "destination": bad})
    assert params is None and err


def test_m94_an_ack_just_before_the_stamp_is_exactly_zero():
    v = row_verdict(Send(T0, PID, P5, sender=ME), [RoutingReply(T0 - 1.5, P5, PID, "NONE")],
                    Witness(T0 - 3600), now=T0 + 3)
    assert v.latency_s == 0.0
