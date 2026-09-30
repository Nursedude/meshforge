"""Primary channel writer, driven through its REAL entry point
(`ChannelConfigHandler._set_primary_channel`) — reader-pair finding A5,
2026-09-29: MeshForge's writer had NO consumer-path test (only the parser was
pinned; the live proof lived in the sandbox journeys, which CI does not run),
so reverting its no-change/confirm body kept CI green. MeshAnchor carries the
same tests (tests/test_tui_radio_writes_finding2.py); the twins' code is
identical, so the tests are too.

A renamed primary changes the channel hash — every peer on the mesh stops
hearing the box.
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


def _ch0_line(name):
    # json.dumps(ensure_ascii=True) is the escaping protobuf json_format
    # really emits; generated at run time so no tool can pre-decode it.
    return ('  Index 0: PRIMARY psk=default { "psk": "AQ==", "name": '
            + json.dumps(name, ensure_ascii=True) + ', "channelNum": 0 }')


_CH_NAMED = _ch0_line("Fleet0")
_CH_UNNAMED = _ch0_line("")
_CH_HAWAIIAN = _ch0_line("hōkū")
_CH_QUOTE = _ch0_line('a"b')
_CH_TRAILING = _ch0_line("Fleet0 ")
assert "\\u014d" in _CH_HAWAIIAN, "fixture lost its escapes — it would test nothing"


def _run(raw, inputs, yesno=(), info_ok=True):
    from handlers.channel_config import ChannelConfigHandler
    h = ChannelConfigHandler()
    ctx = make_handler_context()
    ctx.src_dir = _ROOT / "src"
    h.set_context(ctx)
    h.ctx.dialog._inputbox_returns = list(inputs)   # [] = press Enter on the pre-fill
    h.ctx.dialog._yesno_returns = list(yesno)       # [] = the dialog's default (No)
    writes = []
    info = CommandResult(success=info_ok, message="", raw_output=raw)
    with patch.object(_mesh_cmd_module, 'get_node_info', return_value=info), \
         patch.object(_mesh_cmd_module, 'set_channel_name',
                      side_effect=lambda i, n: (writes.append((i, n)), CommandResult.ok("OK"))[1]):
        h._set_primary_channel()
    inits = [kw['init'] for name, args, kw in h.ctx.dialog.calls if name == 'inputbox']
    return h, inits, writes


def test_one_enter_writes_nothing_and_prefills_the_current_name():
    h, inits, writes = _run(_CH_NAMED, inputs=[])
    assert inits == ["Fleet0"], inits
    assert writes == [], writes


def test_never_prefills_a_brand_name():
    for raw, ok in ((_CH_NAMED, True), (_CH_UNNAMED, True), ("", False)):
        h, inits, writes = _run(raw, inputs=[], info_ok=ok)
        assert not set(inits) & {"MeshForge", "MeshAnchor"}, inits
        assert writes == [], writes


def test_a_changed_name_needs_an_explicit_yes():
    h, inits, writes = _run(_CH_NAMED, inputs=["Fleet1"])        # confirm defaults No
    assert writes == []
    confirms = [kw for name, args, kw in h.ctx.dialog.calls if name == 'yesno']
    assert confirms and confirms[0].get('default_no') is True, confirms


def test_deliberate_confirmed_change_is_written():
    h, inits, writes = _run(_CH_NAMED, inputs=["Fleet1"], yesno=[True])
    assert writes == [(0, "Fleet1")], writes


def test_failed_read_says_unknown():
    h, inits, writes = _run("", inputs=[], info_ok=False)
    prompts = [args[1] for name, args, kw in h.ctx.dialog.calls if name == 'inputbox']
    assert prompts and "UNKNOWN" in prompts[0], prompts
    assert writes == []


def test_parse_unescapes_json():
    from handlers.channel_config import ChannelConfigHandler
    assert ChannelConfigHandler._parse_primary_name(_CH_HAWAIIAN) == "hōkū"
    assert ChannelConfigHandler._parse_primary_name(_CH_QUOTE) == 'a"b'
    assert ChannelConfigHandler._parse_primary_name(_CH_UNNAMED) == ""
    assert ChannelConfigHandler._parse_primary_name("  Index 0: PRIMARY psk=default") is None


def test_prefill_is_the_real_name_not_escape_text():
    h, inits, writes = _run(_CH_HAWAIIAN, inputs=[])
    assert inits == ["hōkū"], inits
    assert writes == []


def test_trailing_space_on_the_radio_is_no_change():
    h, inits, writes = _run(_CH_TRAILING, inputs=[], yesno=[True])
    assert writes == []
    assert not [c for c in h.ctx.dialog.calls if c[0] == 'yesno'], "confirm shown for no change"


def test_name_over_11_utf8_bytes_is_refused_not_truncated():
    # nanopb ChannelSettings.name max_size:12 = 11 bytes + NUL.
    for too_long in ("TwelveChars!", "hōkūlani🌺"):   # 12 B/12 cp; 14 B/9 cp
        assert len(too_long.encode()) > 11
        h, inits, writes = _run(_CH_NAMED, inputs=[too_long], yesno=[True])
        assert writes == [], (too_long, writes)
    h, inits, writes = _run(_CH_NAMED, inputs=["hōkū"], yesno=[True])
    assert writes == [(0, "hōkū")], writes


def test_the_slow_read_is_announced():
    h, inits, writes = _run(_CH_NAMED, inputs=[])
    names = [c[0] for c in h.ctx.dialog.calls]
    assert "infobox" in names and names.index("infobox") < names.index("inputbox"), names
