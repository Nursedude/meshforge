"""TUI audit finding 9 (2026-09-27): non-atomic writes of shared state.

- rns/logging rewrote rnsd's config with truncate-then-write (torn on power
  loss -> rnsd will not start).
- Set Node Positions reset an UNREADABLE node_cache.json to empty and saved
  it — wiping the gateway's cache to add one position.
- Enable SPI counted a commented "#dtparam=spi=on" as enabled and rewrote
  config.txt (on vfat /boot) in place.
"""

import json
import os
import stat
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context


# ------------------------------------------------ the atomic helper (vfat-safe)

class TestAtomicHelper:
    def test_no_op_chmod_is_skipped(self, tmp_path):
        """On vfat, owner/mode come from mount options and fchmod can raise;
        the helper must not call it when the temp file already matches."""
        from utils.paths import atomic_write_text_preserving
        f = tmp_path / "config.txt"
        f.write_text("old")
        os.chmod(f, 0o600)                      # == a fresh mkstemp file
        with patch("os.fchmod", side_effect=AssertionError("fchmod called")):
            atomic_write_text_preserving(f, "new")
        assert f.read_text() == "new"

    def test_mode_is_still_restored_when_it_differs(self, tmp_path):
        from utils.paths import atomic_write_text_preserving
        f = tmp_path / "config"
        f.write_text("old")
        os.chmod(f, 0o644)
        atomic_write_text_preserving(f, "new")
        assert stat.S_IMODE(f.stat().st_mode) == 0o644


# ------------------------------------------------------------- rnsd config

def test_rns_loglevel_write_is_atomic():
    import inspect
    from handlers import rns_config
    src = inspect.getsource(rns_config)
    block = src[src.index("# Update loglevel in config"):]
    block = block[:block.index("level_name = dict(levels)")]
    assert "atomic_write_text_preserving(config_path, new_content)" in block
    assert "config_path.write_text(" not in block


# ------------------------------------------------------------- node cache

def _rns_menu():
    from handlers.rns_menu import RNSMenuHandler
    h = RNSMenuHandler.__new__(RNSMenuHandler)
    h.ctx = make_handler_context()
    return h


class TestNodeCache:
    def _cache(self, tmp_path):
        d = tmp_path / ".config" / "meshforge"
        d.mkdir(parents=True)
        return d / "node_cache.json"

    def test_unreadable_cache_is_refused_not_wiped(self, tmp_path):
        cache = self._cache(tmp_path)
        cache.write_text("{corrupt")
        before = cache.read_bytes()
        with patch("handlers.rns_menu.get_real_user_home", return_value=tmp_path):
            err = _rns_menu()._save_rns_node_position("abc", "n", 21.3, -157.8)
        assert err and "refusing to overwrite" in err
        assert cache.read_bytes() == before

    def test_other_nodes_survive_an_update(self, tmp_path):
        cache = self._cache(tmp_path)
        cache.write_text(json.dumps({"version": 1, "nodes": [{"id": "keep", "name": "K"}]}))
        with patch("handlers.rns_menu.get_real_user_home", return_value=tmp_path):
            err = _rns_menu()._save_rns_node_position("new", "N", 21.3, -157.8)
        assert err is None
        ids = [n["id"] for n in json.loads(cache.read_text())["nodes"]]
        assert ids == ["keep", "new"]


# ------------------------------------------------------------- SPI

class TestSpiConfig:
    def _run(self, text):
        from handlers.hardware import spi_enabled_config
        return spi_enabled_config(text)

    def test_commented_line_does_not_count_as_enabled(self):
        new, changed = self._run("#dtparam=spi=on\n")
        assert changed
        active = [l for l in new.split("\n") if l and not l.startswith("#")]
        assert "dtparam=spi=on" in active and "dtoverlay=spi0-0cs" in active

    def test_already_enabled_is_left_alone(self):
        text = "dtparam=spi=on\ndtoverlay=spi0-0cs\n"
        new, changed = self._run(text)
        assert not changed and new == text

    def test_active_spi_without_overlay_gets_the_overlay_once(self):
        new, changed = self._run("dtparam=spi=on\n#dtoverlay=spi0-0cs\n")
        assert changed
        assert [l for l in new.split("\n") if l == "dtoverlay=spi0-0cs"] == ["dtoverlay=spi0-0cs"]

    def test_empty_config_gets_both(self):
        new, changed = self._run("")
        assert changed and "dtparam=spi=on" in new and "dtoverlay=spi0-0cs" in new
