"""Pins for the 2026-08-14 Q1 dead-code purge (audit W4/W6/W7/W14).

Deleted code has a way of growing back through copy-paste. Each pin here
names what was cut and why; a failure means someone is re-adding a shape
the audit proved dead — add a handler / wire the feature instead.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

REPO = os.path.join(os.path.dirname(__file__), '..')
MAIN = os.path.join(REPO, 'src', 'launcher_tui', 'main.py')


def _main_src():
    with open(MAIN, encoding='utf-8') as fh:
        return fh.read()


class TestLegacyMenuEntriesStayDead:
    # The ONLY legitimate legacy entries are cross-section dispatches whose
    # handler lives in a DIFFERENT registry section. Everything else was
    # shadowed by a same-section registry tag and filtered out on every
    # render (W7 — ~31 entries deleted after per-tag verification).
    SURVIVORS = {'network', 'rns-config', 'mfmaps'}

    def test_only_cross_section_survivors_remain(self):
        src = _main_src()
        tags = set()
        for block in re.findall(r'legacy = \[(.*?)\]', src, re.DOTALL):
            tags.update(re.findall(r'\(\s*["\']([^"\']+)["\']', block))
        assert tags == self.SURVIVORS, (
            f"legacy menu entries changed: {sorted(tags)} vs expected "
            f"{sorted(self.SURVIVORS)}. A same-section entry is DEAD the "
            "moment a handler owns its tag — register a handler action "
            "instead of adding a legacy entry (audit W7)."
        )

    def test_survivors_are_actually_cross_section(self):
        # The survivors must NOT be owned by their menu's own section —
        # if a handler takes the tag over, the legacy entry dies too.
        from handlers import get_all_handlers
        sections = {}
        for cls in get_all_handlers():
            h = cls()
            for item in h.menu_items():
                sections.setdefault(h.menu_section, set()).add(item[0])
        assert 'network' not in sections.get('dashboard', set())
        assert 'rns-config' not in sections.get('configuration', set())
        assert 'mfmaps' not in sections.get('extensions', set())


class TestStatusBarEnhancedHalfStaysDead:
    def test_deleted_methods_absent(self):
        # W14: eight methods called only by tests, plus a second
        # StartupChecker nobody displayed. If a fleet-status view is ever
        # wanted, build it on the live event-fed half.
        from status_bar import StatusBar
        for name in ('get_enhanced_status_line', 'get_environment',
                     'get_alerts', 'has_conflicts', 'refresh_environment',
                     'set_subsystem_states', 'set_node_count',
                     'get_service_status'):
            assert not hasattr(StatusBar, name), (
                f"StatusBar.{name} returned — it was deleted 2026-08-14 "
                "with no production caller (audit W14)"
            )

    def test_no_second_startup_checker(self):
        from status_bar import StatusBar
        import inspect
        src = inspect.getsource(StatusBar.__init__)
        assert 'StartupChecker(' not in src, (
            "StatusBar grew its own StartupChecker again — the launcher's "
            "is the only one (audit W14)"
        )


class TestProfileGatingStaysDecided:
    def test_no_feature_enabled_on_launcher(self):
        # W4: flags were provably always {} (no construction site passed a
        # profile), so every gate was dead. Wire TUIContext.feature_flags
        # if profile-based menu filtering is ever actually wanted.
        src = _main_src()
        assert '_feature_enabled' not in src.replace(
            '# (_profile/_feature_flags/_feature_enabled', ''), (
            "launcher-side feature gating returned without a wiring "
            "(audit W4: decide wire-or-delete, never leave dead gates)"
        )

    def test_dead_cleanup_block_stays_gone(self):
        # W6: the finally-block getattr'd attributes that moved onto
        # handlers years ago; registry.shutdown_all() is the real cleanup.
        src = _main_src()
        for attr in ('_mqtt_subscriber', '_mqtt_ws_bridge',
                     '_telemetry_poller', '_map_server_process'):
            assert src.count(attr) <= 1, (  # allowed once: in the comment
                f"main.py references {attr} again — that attribute never "
                "exists on the launcher (audit W6)"
            )


class TestRelicsStayDeleted:
    def test_textual_experiment_gone(self):
        assert not os.path.exists(os.path.join(REPO, 'test_tui_minimal.py')), (
            "the unused Textual experiment returned to the repo root"
        )

    def test_standalone_single_haversine(self):
        with open(os.path.join(REPO, 'src', 'standalone.py'),
                  encoding='utf-8') as fh:
            src = fh.read()
        nested = re.findall(r'def _?haversine\w*\(', src)
        assert nested == ['def _haversine_m('], (
            f"standalone.py haversine definitions: {nested} — it carried "
            "two inline copies with DIFFERENT Earth radii before the purge; "
            "_haversine_m (meters, utils.rf-delegating) is the only one"
        )
