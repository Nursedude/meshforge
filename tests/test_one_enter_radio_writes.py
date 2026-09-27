"""One Enter must never change the radio (sandbox journeys, 2026-09-27).

Three TUI dialogs pre-filled a HARDCODED value — slot "0", channel "MeshForge",
MQTT root "msh" — and wrote it on one Enter. Journeyed against a SimRadio in
the TUI sandbox: slot 8 -> 0, Fleet0 -> MeshForge, msh/US/HI -> msh. Those
journeys (scripts/tui_journeys.py *_one_enter / *_deliberate) are the live
proof; these pin the pieces CI can run without a radio.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_SRC = Path(__file__).resolve().parents[1] / "src"
for p in (str(_SRC), str(_SRC / "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.meshtastic_cli import CLIResult, MeshtasticCLI  # noqa: E402
from handlers.channel_config import ChannelConfigHandler  # noqa: E402


def _cli_returning(result):
    cli = MeshtasticCLI.__new__(MeshtasticCLI)
    cli.run = MagicMock(return_value=result)
    return cli


def test_get_pref_reads_the_value():
    cli = _cli_returning(CLIResult(True, "Connected to radio\nlora.channel_num: 8\n"))
    assert cli.get_pref("lora.channel_num") == "8"


def test_get_pref_is_none_not_a_default_when_the_read_fails():
    assert _cli_returning(CLIResult(False, "", "timeout")).get_pref("mqtt.root") is None
    # a successful call that never printed the key is a read that did not happen
    assert _cli_returning(CLIResult(True, "Connected to radio\n")).get_pref("mqtt.root") is None


def test_get_pref_does_not_match_a_longer_key():
    cli = _cli_returning(CLIResult(True, "lora.channel_num_extra: 3\n"))
    assert cli.get_pref("lora.channel_num") is None


def test_primary_name_parse_three_states():
    named = 'Index 0: PRIMARY psk=default { "psk": "AQ==", "name": "Fleet0" }'
    unnamed = 'Index 0: PRIMARY psk=default { "psk": "AQ==" }'
    assert ChannelConfigHandler._parse_primary_name(named) == "Fleet0"
    assert ChannelConfigHandler._parse_primary_name(unnamed) == ""      # firmware default
    assert ChannelConfigHandler._parse_primary_name("Owner: x (y)") is None  # no read


def test_detection_never_probes_a_serial_port(monkeypatch):
    """On an RNode box /dev/ttyACM0 IS the RNode; detection used to send
    it `meshtastic --port /dev/ttyACM0 --export-config` whenever TCP failed."""
    from utils import lora_presets
    calls = []

    def fake_run(argv, *a, **k):
        calls.append(list(argv))
        return MagicMock(returncode=1, stdout="", stderr="refused")

    monkeypatch.setattr(lora_presets, "subprocess", MagicMock(
        run=fake_run, TimeoutExpired=TimeoutError), raising=False)
    with patch("subprocess.run", side_effect=fake_run), \
            patch("glob.glob", return_value=["/dev/ttyACM0", "/dev/ttyUSB0"]):
        lora_presets.detect_meshtastic_settings()
    cli_calls = [c for c in calls if any("meshtastic" in str(x) for x in c[:1])]
    assert cli_calls, "detection asked meshtasticd nothing — the test would pass vacuously"
    for c in cli_calls:
        assert "--host" in c and not any(x.startswith("/dev/") for x in c), c
        host = c[c.index("--host") + 1]
        assert host.startswith("localhost:"), f"--host must carry the port: {c}"
