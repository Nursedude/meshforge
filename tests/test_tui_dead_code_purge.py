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
    # W7 (2026-08-14) deleted ~31 legacy entries shadowed by registry tags.
    # The three survivors were cross-section rows; on 2026-09-16 those
    # moved into ONE declaration (CROSS_SECTION_ROWS, rendered by the
    # registry alias), so NO legacy block may carry an entry any more.
    SURVIVORS = {('dashboard', 'network'), ('configuration', 'rns-config'),
                 ('extensions', 'mfmaps')}

    def test_no_legacy_block_carries_an_entry(self):
        src = _main_src()
        tags = set()
        for block in re.findall(r'legacy = \[(.*?)\]', src, re.DOTALL):
            tags.update(re.findall(r'\(\s*["\']([^"\']+)["\']', block))
        assert tags == set(), (
            f"legacy menu entries returned: {sorted(tags)}. A same-section "
            "entry is DEAD the moment a handler owns its tag (audit W7); a "
            "cross-section row belongs in CROSS_SECTION_ROWS, never in a "
            "per-loop list (review 2026-09-16 R4)."
        )

    def test_the_declaration_is_exactly_the_survivors(self):
        import main as tui_main
        declared = {(s, t) for s, t, _os, _ot
                    in tui_main.MeshForgeLauncher.CROSS_SECTION_ROWS}
        assert declared == self.SURVIVORS, declared

    def test_survivors_are_actually_cross_section(self):
        # Each declared row must NOT be owned by its screen's own section
        # (a handler taking the tag over kills the alias — and alias()
        # refuses it loudly), and its owner MUST exist.
        import main as tui_main
        from handlers import get_all_handlers
        sections = {}
        for cls in get_all_handlers():
            h = cls()
            for item in h.menu_items():
                sections.setdefault(h.menu_section, set()).add(item[0])
        for screen, tag, osec, otag in tui_main.MeshForgeLauncher.CROSS_SECTION_ROWS:
            assert screen != osec, (screen, tag)
            assert tag not in sections.get(screen, set()), (screen, tag)
            assert otag in sections.get(osec, set()), (osec, otag)


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
        """The flags live on the context, and only there.

        W4 (2026-08-14) found the launcher carrying its own
        ``_profile`` / ``_feature_flags`` / gate helper that no
        construction site ever fed, so every gate was dead, and ruled:
        decide wire-or-delete, never leave dead gates.

        It was WIRED on 2026-09-16, through the seam W4 named —
        ``TUIContext.feature_flags``, MARKED ``[off]`` (never filtered
        out) by ``HandlerRegistry.get_menu_items``. So this assertion still
        stands and now means something narrower: the launcher must not
        grow a SECOND, private copy of the gate beside the context's.
        Two predicates answering "is this feature on" is how the menu and
        the handler come to disagree.

        The old form stripped a comment out of the source before
        checking. That comment is gone, so the strip is gone with it — a
        tolerance nothing needs is a quiet exemption waiting to be used.
        See tests/test_profile_gating.py for what the wiring must do.
        """
        src = _main_src()
        assert '_feature_enabled' not in src, (
            "launcher-side feature gating returned. The flags belong on "
            "TUIContext, which the registry's row marking already reads "
            "(audit W4: decide wire-or-delete, never leave dead gates)"
        )

    def test_no_gating_row_escape_hatch_on_launcher(self):
        """The hide-plus-escape-hatch rendering was reversed the same day
        it shipped (2026-09-16, ``2b28aa98``): a profile MARKS a row
        ``[off]``, it never removes one, so there is nothing to un-hide
        and ``_gating_row`` has no reason to exist. Two test fakes kept a
        stub for it after the method was deleted — a lambda that is never
        called never raises — which is the deletion-residue class the
        review of that range named. Pin the absence so the residue cannot
        return as a method either.
        """
        src = _main_src()
        assert '_gating_row' not in src, (
            "the profile escape hatch returned to the launcher. A profile "
            "marks rows [off]; nothing is hidden, so nothing needs a hatch"
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
