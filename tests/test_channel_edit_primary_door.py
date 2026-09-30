"""Edit Channel › PRIMARY — the second door to channel 0 (reader pair A4/B1,
2026-09-29). `_set_primary_channel` was guarded (read current, pre-fill,
confirm), but Edit Channel › PRIMARY › Set Channel Name / Set PSK wrote
channel 0 with no read and no confirm: one typed word renamed the mesh's
primary, two menu picks re-keyed it. Either cuts the node off from every peer.

Driven through the REAL handler methods; the only mock is the CLI boundary.
MeshAnchor carries the same tests (the twins' handler code is identical).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT / "src"), str(_ROOT / "src" / "launcher_tui"), str(_ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from commands import meshtastic as _mesh_cmd_module  # noqa: E402
from commands.base import CommandResult  # noqa: E402
from handler_test_utils import make_handler_context  # noqa: E402

_CH0 = ('  Index 0: PRIMARY psk=default { "psk": "AQ==", "name": '
        + json.dumps("Fleet0", ensure_ascii=True) + ', "channelNum": 0 }')


def _drive(method, idx, menu=(), inputs=(), yesno=()):
    from handlers.channel_config import ChannelConfigHandler
    h = ChannelConfigHandler()
    ctx = make_handler_context()
    ctx.src_dir = _ROOT / "src"
    h.set_context(ctx)
    h.ctx.dialog._menu_returns = list(menu)
    h.ctx.dialog._inputbox_returns = list(inputs)   # exhausted = Enter on the pre-fill
    h.ctx.dialog._yesno_returns = list(yesno)       # exhausted = the dialog's default (No)
    writes = []

    def _rec(kind):
        return lambda i, v: (writes.append((kind, i, v)), CommandResult.ok("OK"))[1]

    info = CommandResult(success=True, message="", raw_output=_CH0)
    with patch.object(_mesh_cmd_module, 'get_node_info', return_value=info), \
         patch.object(_mesh_cmd_module, 'set_channel_name', side_effect=_rec("name")), \
         patch.object(_mesh_cmd_module, 'set_channel_psk', side_effect=_rec("psk")):
        if method == "_call_add":
            h._add_channel()
        else:
            getattr(h, method)(idx)
    return h, writes


def _inits(h):
    return [kw['init'] for name, args, kw in h.ctx.dialog.calls if name == 'inputbox']


# ── Set Channel Name on idx 0 → the guarded primary writer ──────────────

def test_primary_name_door_prefills_current_and_one_enter_writes_nothing():
    h, writes = _drive("_set_channel_name", 0)
    assert _inits(h) == ["Fleet0"], _inits(h)
    assert writes == [], writes


def test_primary_name_door_needs_an_explicit_yes():
    h, writes = _drive("_set_channel_name", 0, inputs=["Fleet1"])
    assert writes == [], writes
    h, writes = _drive("_set_channel_name", 0, inputs=["Fleet1"], yesno=[True])
    assert writes == [("name", 0, "Fleet1")], writes          # control: it CAN write


# ── Set PSK on idx 0 → typed confirmation ───────────────────────────────

def test_primary_psk_two_picks_write_nothing():
    for choice in ("random", "default", "none"):
        h, writes = _drive("_set_channel_psk", 0, menu=[choice])
        assert writes == [], (choice, writes)


def test_primary_psk_yes_is_not_enough_and_near_misses_cancel():
    for typed in ("", "y", "yes", "primary", "PRIM"):
        h, writes = _drive("_set_channel_psk", 0, menu=["random"],
                           inputs=[typed], yesno=[True, True])
        assert writes == [], (typed, writes)


def test_primary_psk_typed_word_writes():
    h, writes = _drive("_set_channel_psk", 0, menu=["random"], inputs=["PRIMARY"])
    assert writes == [("psk", 0, "random")], writes            # control: it CAN write


# ── secondary slots: default-No confirm on a key change ─────────────────

def test_secondary_psk_defaults_to_no():
    h, writes = _drive("_set_channel_psk", 2, menu=["random"])
    assert writes == [], writes
    confirms = [kw for name, args, kw in h.ctx.dialog.calls if name == 'yesno']
    assert confirms and confirms[0].get('default_no') is True, confirms


def test_secondary_psk_yes_writes():
    h, writes = _drive("_set_channel_psk", 2, menu=["random"], yesno=[True])
    assert writes == [("psk", 2, "random")], writes


# ── names: refuse over-long, never truncate ─────────────────────────────

def test_secondary_name_over_11_bytes_is_refused_not_truncated():
    # yesno=[True]: answer the rename confirm, so ONLY the length check can
    # stop the write (reader D2 — without it this passed with no check at all)
    for name in ("a" * 12, "ōōōōōō"):                           # 12 bytes each
        h, writes = _drive("_set_channel_name", 3, inputs=[name], yesno=[True])
        assert writes == [], (name, writes)
        assert "Not written" in (h.ctx.dialog.last_msgbox_text or ""), name


def test_secondary_name_at_limit_is_written_stripped():
    h, writes = _drive("_set_channel_name", 3, inputs=["  " + "a" * 11 + " "], yesno=[True])
    assert writes == [("name", 3, "a" * 11)], writes


def test_secondary_rename_defaults_to_no():
    # slot 7 is the gateway channel the bridge resolves BY NAME (reader B F4b)
    h, writes = _drive("_set_channel_name", 7, inputs=["other"])
    assert writes == [], writes
    confirms = [kw for name, args, kw in h.ctx.dialog.calls if name == 'yesno']
    assert confirms and confirms[0].get('default_no') is True, confirms


# ── PSK tokens the CLI actually understands (reader pair, 2026-09-30) ───
# meshtastic's fromPSK returns "AQ==" / bare base64 / bare hex as a str and
# the protobuf assign raises TypeError — "Use Default PSK" never worked.

_KEY32 = bytes(range(32))
_B64 = __import__("base64").b64encode(_KEY32).decode()


def test_default_choice_sends_the_cli_word_not_aq():
    h, writes = _drive("_set_channel_psk", 2, menu=["default"], yesno=[True])
    assert writes == [("psk", 2, "default")], writes


def test_primary_custom_key_needs_the_typed_word():
    h, writes = _drive("_set_channel_psk", 0, menu=["custom"], inputs=[_B64, ""])
    assert writes == [], writes
    h, writes = _drive("_set_channel_psk", 0, menu=["custom"], inputs=[_B64, " PRIMARY "])
    assert writes == [("psk", 0, "base64:" + _B64)], writes   # control + strip()


def test_custom_key_is_shown_in_the_confirm():
    h, writes = _drive("_set_channel_psk", 2, menu=["custom"], inputs=[_B64])
    texts = [args[1] for name, args, kw in h.ctx.dialog.calls if name == 'yesno']
    assert texts and "256-bit" in texts[0] and _B64[:4] in texts[0], texts


def test_custom_key_normalization():
    from handlers.channel_config import ChannelConfigHandler as H
    n = H._normalize_custom_psk
    assert n(_B64)[0] == "base64:" + _B64
    assert n("base64:" + _B64)[0] == "base64:" + _B64
    assert n(_KEY32.hex())[0] == "base64:" + _B64
    assert n("0x" + _KEY32.hex())[0] == "base64:" + _B64
    assert n(_KEY32[:16].hex())[0].startswith("base64:")
    for bad in ("AQ==", "0x01", "1a2b", "none", "simple10", "base64:AQ==",
                "0xzz", "x" * 44, ""):
        assert n(bad)[0] is None, bad


def test_unusable_custom_key_is_refused_with_a_reason():
    h, writes = _drive("_set_channel_psk", 2, menu=["custom"], inputs=["1a2b"], yesno=[True])
    assert writes == [], writes
    assert "Not written" in (h.ctx.dialog.last_msgbox_text or "")


def test_normalized_tokens_are_accepted_by_the_cli_library():
    # Truth = the meshtastic library's own parser, when it is importable.
    import pytest
    util = pytest.importorskip("meshtastic.util")
    pb = pytest.importorskip("meshtastic.protobuf.channel_pb2")
    from handlers.channel_config import ChannelConfigHandler as H
    for tok in ("default", "random", "none", H._normalize_custom_psk(_B64)[0]):
        ch = pb.ChannelSettings()
        ch.psk = util.fromPSK(tok)                              # raises on a bad token
    assert ch.psk == _KEY32                                     # the LAST token: bytes round-trip
    ch = pb.ChannelSettings()
    with pytest.raises(TypeError):
        ch.psk = util.fromPSK("AQ==")                           # control: the old token


# ── Add Channel: refuse, never truncate (reader pair A1/B F3) ───────────

def test_add_channel_refuses_over_long_name():
    h, writes = _drive("_call_add", 3, menu=["3"], inputs=["ōōōōōō"], yesno=[True])
    assert writes == [], writes
    h, writes = _drive("_call_add", 3, menu=["3"], inputs=["a" * 11], yesno=[True])
    assert ("name", 3, "a" * 11) in writes, writes            # control: it CAN write


# ── reader pair 2 (2026-09-30) ──────────────────────────────────────────

def test_names_the_cli_would_read_as_values_are_refused():
    # meshtastic.util.fromStr: 0x41 -> b'A'; 007 / yes / nan / 1e3 -> non-str
    for name in ("0x41", "007", "yes", "No", "nan", "1e3", "inf", "base64:QQ=="):
        for idx in (0, 3):
            h, writes = _drive("_set_channel_name", idx, inputs=[name], yesno=[True, True])
            assert writes == [], (idx, name, writes)
    for name in ("LongFast", "kūlia", "ch-7"):                   # control
        h, writes = _drive("_set_channel_name", 3, inputs=[name], yesno=[True])
        assert writes == [("name", 3, name)], (name, writes)


def test_well_known_simple_keys_pass_through():
    from handlers.channel_config import ChannelConfigHandler as H
    assert H._normalize_custom_psk("simple3")[0] == "simple3"
    assert H._normalize_custom_psk(" Simple0 ")[0] == "simple0"
    assert H._normalize_custom_psk("simple10")[0] is None


def test_pasted_base64_forms_normalize_to_the_same_key():
    from handlers.channel_config import ChannelConfigHandler as H
    want = "base64:" + _B64
    urlsafe = _B64.replace("+", "-").replace("/", "_")
    for form in (_B64, _B64.rstrip("="), urlsafe.rstrip("="),
                 _B64[:20] + "\n" + _B64[20:], "base64:" + urlsafe):
        assert H._normalize_custom_psk(form)[0] == want, form


def test_a_truncated_256_bit_paste_is_flagged_in_the_confirm():
    # the Generate PSK screen once showed only hex[:32] — which is a VALID
    # 128-bit key; the confirm must make the half-key visible
    h, writes = _drive("_set_channel_psk", 2, menu=["custom"], inputs=[_KEY32.hex()[:32]])
    texts = [args[1] for name, args, kw in h.ctx.dialog.calls if name == 'yesno']
    assert texts and "expected 256" in texts[0], texts
    assert writes == [], writes


def test_generated_key_screen_shows_the_whole_key():
    from handlers.channel_config import ChannelConfigHandler
    h = ChannelConfigHandler()
    h.set_context(make_handler_context())
    h._generate_psk()
    text = h.ctx.dialog.last_msgbox_text or ""
    hexes = [ln for ln in text.splitlines() if ln and all(c in "0123456789abcdef" for c in ln)]
    assert sum(len(x) for x in hexes) == 64, text


def test_gateway_custom_key_goes_through_the_key_confirm():
    from handlers.channel_config import ChannelConfigHandler
    h = ChannelConfigHandler()
    ctx = make_handler_context()
    ctx.src_dir = _ROOT / "src"
    h.set_context(ctx)
    h.ctx.dialog._menu_returns = ["custom"]
    h.ctx.dialog._inputbox_returns = [_B64]
    h.ctx.dialog._yesno_returns = [True]          # "Set up gateway channel?" only
    writes = []
    with patch.object(_mesh_cmd_module, 'set_channel_name',
                      side_effect=lambda i, v: (writes.append(("name", i, v)), CommandResult.ok("OK"))[1]), \
         patch.object(_mesh_cmd_module, 'set_channel_psk',
                      side_effect=lambda i, v: (writes.append(("psk", i, v)), CommandResult.ok("OK"))[1]):
        h._set_gateway_channel()
    assert writes == [], writes
    assert len([c for c in h.ctx.dialog.calls if c[0] == 'yesno']) == 2


def test_command_log_never_carries_a_key():
    from commands.meshtastic import _redact_secrets
    argv = ["meshtastic", "--host", "h", "--ch-index", "0", "--ch-set", "psk",
            "base64:" + _B64, "--info"]
    out = " ".join(_redact_secrets(argv))
    assert _B64 not in out and "<redacted>" in out and "--info" in out
    url = "https://example.invalid/e/#" + "Q" * 12      # a channel URL's shape
    assert url not in " ".join(_redact_secrets(["meshtastic", "--seturl", url]))
    assert argv[7].startswith("base64:")                  # caller's argv untouched


def test_restore_and_region_confirms_default_to_no():
    from handlers.device_backup import BackupHandler
    from handlers.radio_menu import RadioMenuHandler
    h = BackupHandler()
    h.set_context(make_handler_context())
    h.ctx.dialog._menu_returns = ["b1"]
    with patch("handlers.device_backup.list_backups",
               return_value=[{"backup_id": "b1", "device_name": "x", "created_at": "2026-09-30"}]):
        h._restore_device_backup()
    confirms = [(a, kw) for n, a, kw in h.ctx.dialog.calls if n == 'yesno']
    assert confirms and confirms[0][1].get('default_no') is True, confirms
    assert "PRIMARY" in confirms[0][0][1]
    r = RadioMenuHandler()
    r.set_context(make_handler_context())
    r.ctx.dialog._menu_returns = ["US"]
    r._radio_set_region()
    confirms = [kw for n, a, kw in r.ctx.dialog.calls if n == 'yesno']
    assert confirms and confirms[0].get('default_no') is True, confirms
