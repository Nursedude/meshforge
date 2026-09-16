"""Deployment-profile menu gating — the rules, pinned.

Wired 2026-09-16 (plan Phase 3). Three things had drifted or were never
built, and each has a test here because each was invisible:

1. **The vocabulary had two independent halves.** Profiles declared
   ``maps`` and no handler consumed it; one handler consumed
   ``fleet_management`` and no profile declared it. Both halves type-
   checked, both looked authoritative, and the result was a promise the
   screen could not keep (hfm #5 — two consumers of one artifact).

2. **Nothing fed the flags.** The registry's filter and the handlers'
   flag field had existed and been tested for a year against a context
   whose ``feature_flags`` was provably always ``{}``.

3. **Hiding needs a way back, ON SCREEN.** A missing row announces
   itself far less than a wrong one, so every gated menu carries a row
   naming the profile and the count — and it must be visible WITHOUT
   scrolling, which is why its position is pinned below. That is not a
   style rule: measured with ``scripts/tui_smoke.py`` on 2026-09-16, a
   bottom-placed row fell off the end of a 24x80 terminal under the
   ``gateway`` and ``field`` profiles.
"""

import logging
import os
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src',
                                'launcher_tui'))

import main as tui_main                      # noqa: E402
from handler_protocol import TUIContext      # noqa: E402
from handler_registry import HandlerRegistry  # noqa: E402
from handlers import get_all_handlers        # noqa: E402
from handlers.manifest import HANDLER_MANIFEST  # noqa: E402
from utils.deployment_profiles import (      # noqa: E402
    FEATURE_FLAGS, PROFILES, ProfileName, list_profiles,
)

SHOW_ALL = HandlerRegistry.SHOW_ALL_TAG

# The eight menus built by MeshForgeLauncher._build_section_menu.
SECTIONS = ("dashboard", "mesh_networks", "rf_sdr", "maps_viz",
            "configuration", "system", "extensions", "about")

LEGACY = {
    "dashboard": [("network", "Network Status      Ports, interfaces")],
    "configuration": [("rns-config", "RNS Config          Reticulum settings")],
}


def _make(profile=None, show_all=False):
    """A real registry with every real handler, gated by ``profile``."""
    logging.disable(logging.INFO)
    ctx = TUIContext(dialog=SimpleNamespace())
    if profile is not None:
        ctx.profile = profile
        ctx.feature_flags = dict(profile.feature_flags)
    ctx.show_all_features = show_all
    registry = HandlerRegistry(ctx)
    for cls in get_all_handlers():
        registry.register(cls())
    ctx.registry = registry
    holder = SimpleNamespace(_registry=registry, _tui_context=ctx)
    holder._owner_flag = lambda sec, tag: registry.owner_flag(sec, tag)
    return ctx, registry, holder


def _rows(holder, section):
    legacy = list(LEGACY.get(section, []))
    if section == "extensions":
        legacy = [("mfmaps", "MeshForge Maps      Multi-source map extension",
                   holder._owner_flag("maps_viz", "mfmaps"))]
    ordering = tui_main.SECTION_ORDERINGS.get(section)
    return tui_main.MeshForgeLauncher._build_section_menu(
        holder, section, legacy, ordering)


# ------------------------------------------------------- the vocabulary

class TestFlagVocabularyIsShared:
    """Both halves of the gate must speak the same words.

    ``TUIContext.feature_enabled`` answers True for a flag it has never
    heard of — correct for "no profile", and the reason a one-sided flag
    is SILENT rather than loud. So neither side may grow a word alone.
    """

    def test_every_consumed_flag_is_in_the_vocabulary(self):
        consumed = {flag
                    for h in HANDLER_MANIFEST
                    for _tag, _desc, flag in h["menu_items"]
                    if flag is not None}
        unknown = consumed - set(FEATURE_FLAGS)
        assert not unknown, (
            f"handlers gate on {sorted(unknown)}, which no profile can "
            f"declare — feature_enabled() defaults those to True, so the "
            f"gate can never close. Add them to FEATURE_FLAGS and to "
            f"every profile, or drop them from menu_items().")

    def test_every_vocabulary_flag_has_a_consumer(self):
        consumed = {flag
                    for h in HANDLER_MANIFEST
                    for _tag, _desc, flag in h["menu_items"]
                    if flag is not None}
        inert = set(FEATURE_FLAGS) - consumed
        assert not inert, (
            f"profiles declare {sorted(inert)} and NO menu action carries "
            f"it, so setting it False hides nothing. That was true of "
            f"'maps' for a year. Either wire it to the rows it describes "
            f"or delete it from FEATURE_FLAGS — a flag that gates nothing "
            f"is a promise the screen cannot keep.")

    def test_profiles_and_handlers_agree_exactly(self):
        declared = set()
        for profile in PROFILES.values():
            declared |= set(profile.feature_flags)
        assert declared == set(FEATURE_FLAGS)


# --------------------------------------------------- the default is safe

class TestUngatedIsUnchanged:
    """A box with no saved profile must render exactly what it did.

    This is the property that let the change ship to nine boxes at once:
    gating is opt-in per box, by a human writing a profile into
    deployment.json. Auto-detection is deliberately NOT consulted — it
    reads which services are RUNNING, so gating on it would hide the RNS
    menu on a box whose rnsd is down, taking the tool away at the exact
    moment it is needed.
    """

    def test_no_profile_hides_nothing(self):
        _ctx, registry, _holder = _make(None)
        for section in registry.section_names:
            assert registry.get_hidden_items(section) == []

    def test_no_profile_renders_no_gating_row(self):
        _ctx, _registry, holder = _make(None)
        for section in SECTIONS:
            tags = [t for t, _d in _rows(holder, section)]
            assert SHOW_ALL not in tags, (
                f"{section} grew an escape-hatch row with no profile "
                f"gating anything — the row must appear only beside a "
                f"real absence")

    def test_launcher_reads_saved_profile_not_detection(self):
        """The wiring must CALL load_profile(), never load_or_detect_profile().

        Asserted over the parsed call graph rather than the source text —
        the method's own docstring explains the distinction and names
        both, and a substring check cannot tell an explanation from a
        call. (A checker must not be fooled by the thing it is reading.)
        """
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(
            tui_main.MeshForgeLauncher._wire_profile_flags)))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        imported = {alias.name
                    for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                    for alias in n.names}
        assert "load_profile" in called and "load_profile" in imported
        assert "load_or_detect_profile" not in called, (
            "the TUI must not gate on an auto-detected profile: detection "
            "reads which services are running, and a box with rnsd down "
            "would silently lose the RNS menu")


# ------------------------------------------------ hiding stays explicable

class TestHidingAlwaysExplainsItself:

    @pytest.mark.parametrize("profile", list_profiles(),
                             ids=lambda p: p.name.value)
    def test_every_section_that_hides_says_so(self, profile):
        _ctx, registry, holder = _make(profile)
        for section in SECTIONS:
            rows = _rows(holder, section)
            tags = [t for t, _d in rows]
            hidden = registry.get_hidden_items(section)
            legacy_hidden = (section == "extensions"
                             and registry.owner_flag("maps_viz", "mfmaps")
                             and not _ctx.feature_enabled(
                                 registry.owner_flag("maps_viz", "mfmaps")))
            if hidden or legacy_hidden:
                assert SHOW_ALL in tags, (
                    f"{profile.name.value}/{section} hides "
                    f"{[t for t, _, _ in hidden]} with no row saying so")
            else:
                assert SHOW_ALL not in tags

    @pytest.mark.parametrize("profile", list_profiles(),
                             ids=lambda p: p.name.value)
    def test_the_row_names_the_profile_and_the_count(self, profile):
        _ctx, registry, holder = _make(profile)
        for section in SECTIONS:
            for tag, desc in _rows(holder, section):
                if tag != SHOW_ALL:
                    continue
                assert profile.name.value in desc, (
                    f"the row must name the profile doing the hiding: {desc!r}")
                assert any(ch.isdigit() for ch in desc), (
                    f"the row must say HOW MANY are hidden: {desc!r}")

    @pytest.mark.parametrize("profile", list_profiles(),
                             ids=lambda p: p.name.value)
    def test_the_row_is_visible_without_scrolling(self, profile):
        """Pinned at index 0 — the fix for a MEASURED defect.

        scripts/tui_smoke.py, 2026-09-16, real whiptail on a 24x80 PTY:
        with the row appended before "Back", mesh_networks under the
        'gateway' and 'field' profiles pushed it past the end of the
        visible list. dashboard (21 rows) and system (19) overflow a
        24-row terminal routinely. An explanation you have to scroll to
        find is not an explanation, so position is part of the contract.
        """
        _ctx, _registry, holder = _make(profile)
        for section in SECTIONS:
            rows = _rows(holder, section)
            tags = [t for t, _d in rows]
            if SHOW_ALL in tags:
                assert tags[0] == SHOW_ALL, (
                    f"{profile.name.value}/{section}: the escape hatch is "
                    f"at index {tags.index(SHOW_ALL)} of {len(tags)} rows. "
                    f"It must be first — a section list can be taller than "
                    f"the terminal and whiptail scrolls it away.")


# ------------------------------------------------------- the way back works

class TestEscapeHatchRestoresEverything:

    @pytest.mark.parametrize("profile", list_profiles(),
                             ids=lambda p: p.name.value)
    def test_show_all_restores_every_hidden_row(self, profile):
        _c0, _r0, ungated = _make(None)
        _c1, _r1, shown = _make(profile, show_all=True)
        for section in SECTIONS:
            base = {t for t, _d in _rows(ungated, section)}
            after = {t for t, _d in _rows(shown, section)} - {SHOW_ALL}
            assert base <= after, (
                f"{profile.name.value}/{section}: 'Show all' did not bring "
                f"back {sorted(base - after)} — gating must hide a VIEW, "
                f"never a capability")

    @pytest.mark.parametrize("profile", list_profiles(),
                             ids=lambda p: p.name.value)
    def test_the_count_on_screen_is_the_count_that_is_real(self, profile):
        """Both labels must report the number of rows actually removed.

        Derived from the ROW SETS — ungated minus gated — not from the
        label, so the test cannot be satisfied by two labels agreeing
        with each other while both are wrong.

        The first version of this test asked only about sections that
        hide something when filtered, and therefore skipped the exact
        case that produced the bug it was written for: Extensions under
        'field', where maps is ON, hides nothing when filtered and still
        announced "hides 1" in show-all mode. The cross-section row was
        counted as gateable whenever it CARRIED a flag rather than when
        the profile would BLOCK it. A count the operator cannot
        reproduce teaches them the numbers are decoration.
        """
        _cu, _ru, ungated = _make(None)
        _cg, _rg, gated = _make(profile)
        _cs, _rs, shown = _make(profile, show_all=True)
        for section in SECTIONS:
            base = {t for t, _d in _rows(ungated, section)}
            gated_rows = dict(_rows(gated, section))
            really_hidden = len(base - set(gated_rows))

            if really_hidden == 0:
                assert SHOW_ALL not in gated_rows, (
                    f"{profile.name.value}/{section}: an escape hatch "
                    f"beside nothing hidden")
                assert SHOW_ALL not in dict(_rows(shown, section)), (
                    f"{profile.name.value}/{section}: show-all offers to "
                    f"filter rows this profile does not filter")
                continue

            gated_n = int(re.search(r"(\d+) hidden",
                                    gated_rows[SHOW_ALL]).group(1))
            assert gated_n == really_hidden, (
                f"{profile.name.value}/{section}: the row says "
                f"{gated_n} hidden, {really_hidden} rows are actually gone")

            shown_label = dict(_rows(shown, section)).get(SHOW_ALL)
            assert shown_label is not None, (
                f"{profile.name.value}/{section}: the toggle vanished in "
                f"show-all mode, so there is no way back to the filter")
            shown_n = int(re.search(r"hides (\d+)", shown_label).group(1))
            assert shown_n == really_hidden, (
                f"{profile.name.value}/{section}: show-all says the "
                f"profile hides {shown_n}, it hides {really_hidden}")

    def test_dispatching_the_tag_flips_the_toggle(self):
        ctx, registry, _holder = _make(PROFILES[ProfileName.MONITOR])
        assert ctx.show_all_features is False
        assert registry.dispatch("mesh_networks", SHOW_ALL) is True
        assert ctx.show_all_features is True
        assert registry.dispatch("mesh_networks", SHOW_ALL) is True
        assert ctx.show_all_features is False

    def test_the_tag_is_dispatchable_from_every_section(self):
        """Including the two sub-menus built inside handlers.

        ``meshtasticd_config`` dispatches CONDITIONALLY (``choice in
        registry_tags``) rather than calling dispatch() first like the
        eight section loops — a reserved tag belongs to no handler's tag
        set, so that loop needed an explicit branch. Menu loops are not
        one shape; the ones with few members are where a blanket
        assumption breaks.
        """
        ctx, registry, _holder = _make(PROFILES[ProfileName.MONITOR])
        for section in registry.section_names:
            ctx.show_all_features = False
            assert registry.dispatch(section, SHOW_ALL) is True, (
                f"the escape hatch is not dispatchable from {section!r}")
            assert ctx.show_all_features is True

    def test_meshtasticd_loop_handles_the_reserved_tag(self):
        import inspect
        from handlers.meshtasticd_config import MeshtasticdConfigHandler
        src = inspect.getsource(MeshtasticdConfigHandler)
        assert "SHOW_ALL_TAG" in src, (
            "meshtasticd's menu loop dispatches only tags in "
            "registry_tags, so it needs an explicit branch for the "
            "registry-owned escape hatch or the row falls through to the "
            "'not wired' tail")


# ------------------------------------------- cross-section rows obey flags

class TestCrossSectionRowsObeyTheirOwner:
    """A duplicate row must not outlive the original.

    'MeshForge Maps' is rendered in Extensions but owned in Maps & Viz,
    where it carries the ``maps`` flag. Hiding it on one screen and
    leaving the same action reachable from another is worse than not
    gating at all: the operator learns the menu lies about what is there.
    """

    def test_extensions_mfmaps_hides_with_maps(self):
        _ctx, _registry, holder = _make(PROFILES[ProfileName.MONITOR])
        tags = [t for t, _d in _rows(holder, "extensions")]
        assert "mfmaps" not in tags
        assert SHOW_ALL in tags, "and it must say so"

    def test_extensions_mfmaps_returns_with_maps(self):
        _ctx, _registry, holder = _make(PROFILES[ProfileName.FIELD])
        tags = [t for t, _d in _rows(holder, "extensions")]
        assert "mfmaps" in tags

    def test_owner_flag_is_read_not_retyped(self):
        import inspect
        src = inspect.getsource(tui_main.MeshForgeLauncher._extensions_menu)
        assert "_owner_flag" in src, (
            "the cross-section row must inherit its owner's flag rather "
            "than repeat the flag name — a second copy is the drift this "
            "whole phase exists to remove")


# --------------------------------------------- a row never vanishes quietly

class TestMainMenuRowsNeverVanishSilently:

    def test_unowned_row_falls_back_and_logs(self, caplog):
        """A handler that failed to import must not delete a top-level row."""
        ctx = TUIContext(dialog=SimpleNamespace())
        registry = HandlerRegistry(ctx)   # deliberately EMPTY
        ctx.registry = registry
        fake = SimpleNamespace(
            _registry=registry, _tui_context=ctx,
            _MAIN_FALLBACK_LABELS=(
                tui_main.MeshForgeLauncher._MAIN_FALLBACK_LABELS))
        logging.disable(logging.NOTSET)
        with caplog.at_level(logging.ERROR):
            row = tui_main.MeshForgeLauncher._handler_row(fake, "t")
        assert row == [("t", tui_main.MeshForgeLauncher
                        ._MAIN_FALLBACK_LABELS["t"])]
        assert any("no registry owner" in r.message for r in caplog.records), (
            "a fallback that leaves no witness is a swallow (hfm #9)")

    def test_gated_row_is_absent_without_the_error(self, caplog):
        """Gated is a DECISION; unowned is a DEFECT. They must not look alike."""
        _ctx, _registry, _holder = _make(PROFILES[ProfileName.MONITOR])
        ctx, registry, _h = _make(PROFILES[ProfileName.MONITOR])
        fake = SimpleNamespace(
            _registry=registry, _tui_context=ctx,
            _MAIN_FALLBACK_LABELS=(
                tui_main.MeshForgeLauncher._MAIN_FALLBACK_LABELS))
        logging.disable(logging.NOTSET)
        with caplog.at_level(logging.ERROR):
            row = tui_main.MeshForgeLauncher._handler_row(fake, "t")
        assert row == []
        assert not [r for r in caplog.records if "no registry owner" in r.message]
