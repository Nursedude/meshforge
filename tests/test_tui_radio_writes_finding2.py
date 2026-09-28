"""TUI audit finding 2, remainder (2026-09-27): one stray Enter must not
change the radio.

- HAT swap ("Activate Config") and "Remove Config" confirms defaulted YES —
  one Enter removed the radio's hardware config (and restarted meshtasticd).
- MeshForge-authored MeshAdv-Mini presets set `CS: 8`: GPIO 8 is SPI0 CE0,
  already driven by the kernel's spidev; claiming it crashloops meshtasticd
  on trixie (lehua 2026-08-29). Fleet boxes run such boards WITHOUT CS.
- radio_menu carried a second, weaker owner-name writer; it now delegates to
  the one writer fixed after the 2026-09-20 rename incidents.
"""

import glob
import inspect
import os
import re
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

import yaml

REPO = os.path.join(os.path.dirname(__file__), '..')


class TestDestructiveConfirmsDefaultNo:
    def test_every_yesno_in_meshtasticd_radio_defaults_no(self):
        # Each confirm in this handler removes or swaps the radio's hardware
        # config or rewrites its identity — a default of YES is one Enter.
        from handlers import meshtasticd_radio
        src = inspect.getsource(meshtasticd_radio)
        calls = [m.start() for m in re.finditer(r"\.yesno\(", src)]
        assert calls, "scanner found no yesno calls — it cannot see its subject"
        for start in calls:
            depth, i = 0, src.index("(", start)
            for j in range(i, len(src)):
                depth += {"(": 1, ")": -1}.get(src[j], 0)
                if depth == 0:
                    call = src[i:j + 1]
                    break
            assert "default_no=True" in call, f"confirm defaults YES:\n{call[:160]}"


class TestNoKernelChipSelectInOurPresets:
    PRESETS = sorted(glob.glob(os.path.join(REPO, "templates", "meshforge-presets", "*.yaml")))

    def test_scanner_sees_presets(self):
        assert len(self.PRESETS) >= 4

    def test_no_meshforge_preset_claims_gpio8(self):
        for p in self.PRESETS:
            cs = (yaml.safe_load(open(p)).get("Lora") or {}).get("CS")
            assert cs != 8, f"{os.path.basename(p)} claims CS: 8 (SPI0 CE0)"

    def test_builder_meshadv_mini_has_no_cs(self):
        from handlers import meshtasticd_lora
        src = inspect.getsource(meshtasticd_lora)
        block = src[src.index('"meshadv-mini": {'):src.index('"meshadv-pi-hat": {')]
        assert '"CS"' not in block


class TestSingleOwnerWriter:
    def _handler(self, registry):
        from handlers.radio_menu import RadioMenuHandler
        from handler_test_utils import make_handler_context
        h = RadioMenuHandler.__new__(RadioMenuHandler)
        h.ctx = make_handler_context()
        h.ctx.registry = registry
        return h

    def test_delegates_to_the_one_writer(self):
        target = MagicMock()
        registry = MagicMock()
        registry.get_handler.return_value = target
        h = self._handler(registry)
        with patch("subprocess.run") as run:
            h._radio_set_name()
        registry.get_handler.assert_called_once_with("meshtasticd_radio")
        target._set_owner_name.assert_called_once()
        run.assert_not_called()          # no second writer ran

    def test_unloadable_writer_changes_nothing(self):
        registry = MagicMock()
        registry.get_handler.return_value = None
        h = self._handler(registry)
        with patch("subprocess.run") as run:
            h._radio_set_name()
        run.assert_not_called()
        titles = [c[1][0] for c in h.ctx.dialog.calls if c[0] == "msgbox"]
        assert "Node Name" in titles

    def test_no_raw_set_owner_left_in_radio_menu(self):
        from handlers import radio_menu
        src = inspect.getsource(radio_menu)
        # the argv LITERAL a CLI call would carry, not the word in prose
        assert "'--set-owner'" not in src and '"--set-owner"' not in src
