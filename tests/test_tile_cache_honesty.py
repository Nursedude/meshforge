"""The tile cache is written and never read (utils/coverage_map.py header,
measured 2026-09-15): no map serves /tiles/. Every screen that could be read
as an offline-maps promise must say so (2026-09-26 live-truth pass).

If a map ever SERVES the cache, this test is the reminder to drop the note —
delete it together with `_TILES_NOT_USED`, never one without the other.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context
from launcher_tui.handlers import _ai_tools_tilecache as tc

SRC = Path(__file__).resolve().parent.parent / "src"


class _Host(tc.TileCacheMixin):
    def __init__(self, dialog):
        self.ctx = make_handler_context(dialog=dialog)


class _EmptyCache:
    def get_stats(self):
        return {"tile_count": 0, "size_mb": 0.0}

    @staticmethod
    def estimate_download_size(bounds):
        return {"total_tiles": 10, "estimated_mb": 0.2}


def _texts(d):
    return "\n".join(" ".join(str(p) for p in c[1] if p is not None) for c in d.calls)


def test_stats_says_no_map_reads_the_cache():
    d = FakeDialog()
    with patch.object(tc, "_HAS_TILE_CACHE", True), patch.object(tc, "TileCache", _EmptyCache):
        _Host(d)._tile_cache_stats()
    assert "NOT\noffline maps" in _texts(d)


def test_download_confirm_says_it_before_anything_is_fetched():
    d = FakeDialog()
    d._menu_returns = ["hawaii"]          # yesno defaults to False: nothing downloads
    with patch.object(tc, "_HAS_TILE_CACHE", True), patch.object(tc, "TileCache", _EmptyCache):
        _Host(d)._tile_cache_download()
    confirm = [c for c in d.calls if c[0] == "yesno"]
    assert confirm and "no MeshForge map reads this cache" in confirm[0][1][1]


def test_premise_still_holds_no_tiles_route():
    handler = (SRC / "utils" / "map_http_handler.py").read_text()
    assert "/tiles/" not in handler, "a map now serves tiles — drop _TILES_NOT_USED"
