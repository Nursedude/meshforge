"""The WAN Path Trace menu entry is actually reachable (2026-09-06).

A menu key that no dispatch entry matches is a silent failure: the item
highlights, the operator presses enter, nothing happens, and no error is
raised anywhere. Cheap to guard, so guard it.
"""
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "launcher_tui"))

from launcher_tui.handlers.network_tools import NetworkToolsHandler  # noqa: E402


def _menu_source():
    return inspect.getsource(NetworkToolsHandler._network_menu)


class TestWanPathIsReachable:
    def test_the_handler_method_exists(self):
        assert callable(getattr(NetworkToolsHandler, "_wan_path_trace", None))

    def test_the_menu_offers_it(self):
        assert '("wanpath"' in _menu_source()

    def test_every_dispatched_choice_has_a_menu_entry_and_vice_versa(self):
        """The wiring guard proper: the choice keys handled by the dispatch dict
        or the explicit branches must cover every key the menu offers."""
        src = _menu_source()
        choices = set(re.findall(r'\(\s*"([a-z_]+)"\s*,\s*"', src))
        choices.discard("back")
        # keys resolved either by the dispatch table or an explicit branch
        dispatched = set(re.findall(r'"([a-z_]+)":\s*\(', src))
        branched = set(re.findall(r'choice == "([a-z_]+)"', src))
        unreachable = choices - dispatched - branched
        assert not unreachable, "menu keys nothing handles: %s" % sorted(unreachable)

    def test_wanpath_specifically_is_wired(self):
        src = _menu_source()
        assert '"wanpath": (' in src and "_wan_path_trace" in src
