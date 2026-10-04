"""Set TX Power must not present the SETTING as radiated power (2026-10-03).

Measured that day: one fleet box's PA HAT radiated flat from setting ~12 to 30
(within ~2.5 dB; SDR step test), and another box's mesh_bot was stranded by a
tx_power --set (the in-process reconfig refused its API connection). The
menu said "Higher power = more range" and computed mW from the setting, and
its confirm defaulted to YES while Set Region's defaults to NO.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))


def _drive(menu_choice="17"):
    from handlers.radio_menu import RadioMenuHandler
    seen = {"menu": None, "yesno": None, "ran": []}

    def menu(title, text, choices, **kw):
        seen["menu"] = text
        return menu_choice

    def yesno(title, text, **kw):
        seen["yesno"] = (text, kw)
        return False                      # never run the CLI in a test

    h = RadioMenuHandler()
    h.ctx = SimpleNamespace(dialog=SimpleNamespace(menu=menu, yesno=yesno,
                                                   msgbox=lambda *a, **k: None),
                            get_meshtastic_cli=lambda: "meshtastic")
    h._radio_run = lambda *a, **k: seen["ran"].append(a)
    h._radio_set_tx_power()
    return seen


def test_menu_does_not_promise_range_from_power():
    text = _drive()["menu"]
    assert "Higher power = more range" not in text
    assert "TX Power Truth" in text


def test_confirm_says_setting_is_not_radiated_and_warns_api_clients():
    text, kw = _drive()["yesno"]
    assert "approximately" not in text          # no mW claim from the setting
    assert "API client" in text
    assert kw.get("default_no") is True


def test_declining_runs_nothing():
    assert _drive()["ran"] == []
