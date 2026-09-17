"""Deployment-profile menu gating — a profile MARKS rows, never removes them.

Wired 2026-09-16 (plan Phase 3), and its rendering half was reversed the
same day after the operator reframed who the TUI is for:

    "this is more for users in someway who are new to this domain"
    "the scaffolding of this domain has to work if it overcomplicate itself"

The first implementation HID rows a profile excluded and offered a
"Show all" escape hatch. That is wrong for a newcomer — you cannot go
looking for a capability you have never been shown, so hiding teaches
nothing and a shorter menu just looks like a smaller product. It was also
the more complicated of the two answers by a wide margin: it needed a
session-wide override, four predicates over one question, a reserved tag
threaded through eleven menu loops of three different shapes, and a fix
for the hatch scrolling off a 24x80 terminal.

Marking needs none of that. Row counts never change, so nothing can
scroll away; there is nothing to un-hide, so there is no override; and a
gated row explains itself when selected instead of being absent.

(Menu LENGTH is still a real problem — dashboard is 21 rows. That is a
SHAPE problem and belongs to the section-cap work, which fixes it for
every user rather than only for boxes that declare a profile.)

What survived the reversal, because it was load-bearing either way: one
flag vocabulary consumed by both sides, the startup wiring from a SAVED
profile only, and the main-menu label de-duplication.
"""

import logging
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src',
                                'launcher_tui'))

import main as tui_main                       # noqa: E402
from handler_protocol import TUIContext       # noqa: E402
from handler_registry import HandlerRegistry   # noqa: E402
from handlers import get_all_handlers         # noqa: E402
from handlers.manifest import HANDLER_MANIFEST  # noqa: E402
from utils.deployment_profiles import (       # noqa: E402
    FEATURE_FLAGS, PROFILES, ProfileName, list_profiles,
)

OFF = HandlerRegistry.OFF_MARK

SECTIONS = ("dashboard", "mesh_networks", "rf_sdr", "maps_viz",
            "configuration", "system", "extensions", "about")

class RecordingDialog:
    """Captures msgbox calls so a refusal can be read back."""

    def __init__(self):
        self.msgboxes = []

    def msgbox(self, title, text, **kw):
        self.msgboxes.append((title, text))

    def menu(self, *a, **k):
        return None

    def yesno(self, *a, **k):
        return False


def _make(profile=None):
    logging.disable(logging.INFO)
    ctx = TUIContext(dialog=RecordingDialog())
    if profile is not None:
        ctx.profile = profile
        ctx.feature_flags = dict(profile.feature_flags)
    registry = HandlerRegistry(ctx)
    for cls in get_all_handlers():
        registry.register(cls())
    # The cross-section rows come from the launcher's ONE declaration —
    # this file used to carry its own copy of them, and that copy had
    # already drifted from main.py (review 2026-09-16, R4).
    tui_main.MeshForgeLauncher._declare_cross_section_rows(registry)
    ctx.registry = registry
    holder = SimpleNamespace(_registry=registry, _tui_context=ctx)
    return ctx, registry, holder


def _rows(holder, section):
    return tui_main.MeshForgeLauncher._build_section_menu(
        holder, section, [], tui_main.SECTION_ORDERINGS.get(section))


# ------------------------------------------------------- the vocabulary

class TestFlagVocabularyIsShared:
    """Both halves of the gate must speak the same words.

    ``feature_enabled`` answers True for a flag it has never heard of —
    correct for "no profile", and the reason a one-sided flag is SILENT
    rather than loud. So neither side may grow a word alone.
    """

    def test_every_consumed_flag_is_in_the_vocabulary(self):
        consumed = {f for h in HANDLER_MANIFEST
                    for _t, _d, f in h["menu_items"] if f is not None}
        unknown = consumed - set(FEATURE_FLAGS)
        assert not unknown, (
            f"handlers gate on {sorted(unknown)}, which no profile can "
            f"declare — feature_enabled() defaults those to True, so the "
            f"gate can never close.")

    def test_every_vocabulary_flag_has_a_consumer(self):
        consumed = {f for h in HANDLER_MANIFEST
                    for _t, _d, f in h["menu_items"] if f is not None}
        inert = set(FEATURE_FLAGS) - consumed
        assert not inert, (
            f"profiles declare {sorted(inert)} and NO menu action carries "
            f"it, so setting it False marks nothing. That was true of "
            f"'maps' for a year.")

    def test_profiles_and_handlers_agree_exactly(self):
        declared = set()
        for profile in PROFILES.values():
            declared |= set(profile.feature_flags)
        assert declared == set(FEATURE_FLAGS)


# ------------------------------------------------ nothing ever disappears

class TestAProfileNeverRemovesARow:
    """THE invariant of this design, and the reason it is simple.

    If no row can vanish, there is nothing to un-hide: no escape hatch,
    no session override, no reserved tag, and no row that can scroll off
    the bottom of a short terminal. Every one of those existed in the
    first implementation and every one of them is gone.
    """

    @pytest.mark.parametrize("profile", list_profiles(),
                             ids=lambda p: p.name.value)
    def test_the_tag_set_is_identical_with_and_without_a_profile(self, profile):
        _cu, _ru, ungated = _make(None)
        _cg, _rg, gated = _make(profile)
        for section in SECTIONS:
            before = [t for t, _d in _rows(ungated, section)]
            after = [t for t, _d in _rows(gated, section)]
            assert before == after, (
                f"{profile.name.value}/{section}: a profile changed WHICH "
                f"rows exist. It may only change what they SAY.\\n"
                f"  gone:  {sorted(set(before) - set(after))}\\n"
                f"  extra: {sorted(set(after) - set(before))}")

    @pytest.mark.parametrize("profile", list_profiles(),
                             ids=lambda p: p.name.value)
    def test_the_main_menu_keeps_all_thirteen_rows(self, profile):
        _cu, _ru, _hu = _make(None)
        _cg, registry, _hg = _make(profile)
        rows = dict(registry.get_menu_items("main"))
        for tag in tui_main.MeshForgeLauncher._MAIN_FALLBACK_LABELS:
            assert tag in rows, (
                f"{profile.name.value}: top-level row {tag!r} vanished. The "
                f"top level is where a newcomer learns what this software "
                f"can do at all.")


# ------------------------------------------------------ marked, and legible

class TestGatedRowsAreMarked:

    @staticmethod
    def _resolve_flag(section, tag):
        """The flag a row rendered in ``section`` is governed by.

        Section-scoped, because TAGS ARE NOT GLOBALLY UNIQUE and asking
        globally gets the wrong answer: 'wizard' is Setup Wizard
        (configuration, unflagged) AND Gateway Wizard (mesh_networks,
        flag 'gateway'). Two distinct actions sharing a short tag is fine
        — dispatch is ``dispatch(section, tag)`` — but a global tag->flag
        map collapses them, which is what the first version of this test
        did before reporting it as a defect in the code.

        Falls through to a unique owner in ANOTHER section for the
        cross-section case (Extensions renders 'mfmaps', which Maps & Viz
        owns and flags). Returns (flag, True) when governed, (None, False)
        when the row is not registry-owned at all ('back', and the legacy
        rows that carry no flag).
        """
        for h in HANDLER_MANIFEST:
            if h["menu_section"] == section:
                for t, _d, f in h["menu_items"]:
                    if t == tag:
                        return f, True
        owners = [f for h in HANDLER_MANIFEST
                  for t, _d, f in h["menu_items"] if t == tag]
        if len(owners) == 1:
            return owners[0], True
        return None, False

    @pytest.mark.parametrize("profile", list_profiles(),
                             ids=lambda p: p.name.value)
    def test_every_gated_row_carries_the_mark(self, profile):
        ctx, _registry, holder = _make(profile)
        for section in SECTIONS:
            for tag, desc in _rows(holder, section):
                flag, governed = self._resolve_flag(section, tag)
                if not governed:
                    assert not desc.startswith(OFF), (
                        f"{profile.name.value}/{section}/{tag} is marked "
                        f"off but no handler flags it: {desc!r}")
                    continue
                should_mark = flag is not None and not ctx.feature_enabled(flag)
                assert desc.startswith(OFF) == should_mark, (
                    f"{profile.name.value}/{section}/{tag}: flag={flag!r} "
                    f"allowed={not should_mark} but label is {desc!r}")

    def test_a_tag_is_not_ambiguous_within_one_section(self):
        """Global uniqueness is NOT the invariant; per-section IS.

        Two sections may reuse a short tag for different actions. Two
        handlers in the SAME section claiming one tag is a real collision
        — dispatch would pick whichever registered first.
        """
        seen = {}
        for h in HANDLER_MANIFEST:
            for tag, _d, _f in h["menu_items"]:
                key = (h["menu_section"], tag)
                assert key not in seen, (
                    f"{h['menu_section']}/{tag} is claimed by both "
                    f"{seen[key]!r} and {h['handler_id']!r}")
                seen[key] = h["handler_id"]

    def test_the_mark_keeps_the_row_saying_what_the_tool_is(self):
        """The point of marking over hiding — so keep the original label."""
        _ctx, registry, _h = _make(PROFILES[ProfileName.MONITOR])
        rows = dict(registry.get_menu_items("mesh_networks"))
        assert rows["rns"].startswith(OFF)
        assert "Reticulum" in rows["rns"], (
            "the mark replaced the description instead of prefixing it — "
            "a reader now learns the row is off but not what it does")

    def test_the_mark_is_a_prefix_not_a_suffix(self):
        """whiptail truncates to the box width; a suffix is what vanishes.

        Measured on this project's own 24x80 floor: the previous design's
        escape-hatch row fell off the end of a tall section entirely. A
        marker at the END of a long label is the same failure in miniature
        — invisible on exactly the terminal that needs it most.
        """
        assert not OFF.strip().endswith("]") or OFF[0] == "[", OFF
        _ctx, registry, _h = _make(PROFILES[ProfileName.MONITOR])
        for _tag, desc in registry.get_menu_items("mesh_networks"):
            if OFF.strip() in desc:
                assert desc.startswith(OFF), (
                    f"the mark must lead the label, not trail it: {desc!r}")


# ------------------------------------------ shown, but not run — and it says why

class TestGatedRowsRefuseWithAnExplanation:

    def test_dispatching_a_gated_tag_does_not_reach_the_handler(self):
        ctx, registry, _h = _make(PROFILES[ProfileName.MONITOR])
        reached = []
        handler = registry._tag_index["mesh_networks"]["rns"]
        handler.execute = lambda *a, **k: reached.append(a)
        assert registry.dispatch("mesh_networks", "rns") is True, (
            "a gated tag IS owned — returning False would reach the "
            "'not wired' tripwire and report a wiring bug that does not "
            "exist")
        assert not reached, "a row the profile excludes was actually run"

    def test_the_refusal_explains_itself(self):
        ctx, registry, _h = _make(PROFILES[ProfileName.MONITOR])
        registry.dispatch("mesh_networks", "rns")
        assert ctx.dialog.msgboxes, "refused silently — the worst option"
        title, body = ctx.dialog.msgboxes[-1]
        assert "monitor" in body, "must name the profile responsible"
        assert "rns" in body, "must name the flag responsible"
        assert "Deployment Profile" in body, "must say how to change it"

    def test_the_refusal_never_sends_the_operator_out_of_the_app(self):
        """MF018 — in-domain remediation.

        The twin repo's version of this dialog tells the operator to quit
        and run ``python3 src/launcher.py --profile gateway``. An app that
        answers a question by sending you to a shell has failed at the
        thing it is for.
        """
        ctx, registry, _h = _make(PROFILES[ProfileName.MONITOR])
        registry.dispatch("mesh_networks", "rns")
        _title, body = ctx.dialog.msgboxes[-1]
        for leak in ("python3 ", "src/launcher.py", "--profile ", "sudo "):
            assert leak not in body, (
                f"the refusal tells the operator to leave the TUI: {leak!r}")

    def test_an_allowed_row_still_runs(self):
        ctx, registry, _h = _make(PROFILES[ProfileName.FIELD])
        reached = []
        handler = registry._tag_index["mesh_networks"]["rns"]
        handler.execute = lambda *a, **k: reached.append(a)
        registry.dispatch("mesh_networks", "rns")
        assert reached, "'field' enables rns — the row must actually run"
        assert not ctx.dialog.msgboxes, "and must not explain itself"


# --------------------------------------------- the default is still a no-op

class TestUngatedIsUnchanged:

    def test_no_profile_marks_nothing(self):
        _ctx, registry, holder = _make(None)
        for section in registry.section_names:
            assert registry.get_gated_items(section) == []
        for section in SECTIONS:
            for _tag, desc in _rows(holder, section):
                assert not desc.startswith(OFF)

    def test_launcher_reads_saved_profile_not_detection(self):
        """Gating on auto-detection would hide tools when they are needed.

        Asserted over the parsed call graph, not the source text — the
        method's docstring explains the distinction and names both, and a
        substring check cannot tell an explanation from a call.
        """
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(
            tui_main.MeshForgeLauncher._wire_profile_flags)))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "load_profile" in called
        assert "load_or_detect_profile" not in called, (
            "detection reads which services are RUNNING, so a box with "
            "rnsd down would mark the RNS menu off at the exact moment it "
            "is needed")


# ------------------------------------------- cross-section rows read alike

class TestCrossSectionRowsObeyTheirOwner:
    """The same action must not read as available on one screen and off
    on another — that teaches the operator the menu is unreliable."""

    def test_extensions_mfmaps_is_marked_with_maps(self):
        _ctx, _registry, holder = _make(PROFILES[ProfileName.MONITOR])
        rows = dict(_rows(holder, "extensions"))
        assert rows["mfmaps"].startswith(OFF)

    def test_extensions_mfmaps_is_clean_when_maps_is_on(self):
        _ctx, _registry, holder = _make(PROFILES[ProfileName.FIELD])
        rows = dict(_rows(holder, "extensions"))
        assert not rows["mfmaps"].startswith(OFF)

    def test_it_matches_its_owner_exactly(self):
        _ctx, registry, holder = _make(PROFILES[ProfileName.MONITOR])
        owner = dict(registry.get_menu_items("maps_viz"))["mfmaps"]
        here = dict(_rows(holder, "extensions"))["mfmaps"]
        assert here.startswith(OFF) == owner.startswith(OFF)

    def test_the_declaration_carries_no_label(self):
        """CROSS_SECTION_ROWS is (screen, tag, owner_section, owner_tag)
        and nothing else. A label in it would be the second copy the
        table exists to remove — the owner's row IS the label."""
        for entry in tui_main.MeshForgeLauncher.CROSS_SECTION_ROWS:
            assert len(entry) == 4, entry
            assert all(" " not in part for part in entry), (
                f"looks like a label crept into the declaration: {entry!r}")

    def test_the_rendered_label_is_the_owners(self):
        _ctx, registry, holder = _make()
        for screen, tag, osec, otag in tui_main.MeshForgeLauncher.CROSS_SECTION_ROWS:
            here = dict(_rows(holder, screen))[tag]
            owner = dict(registry.get_menu_items(osec))[otag]
            assert here == owner, (screen, tag, here, owner)

    def test_the_launcher_keeps_no_owner_flag_helper(self):
        assert not hasattr(tui_main.MeshForgeLauncher, "_owner_flag"), (
            "the per-loop flag threading returned; the registry alias "
            "renders and dispatches cross-section rows now")


# ------------------------------------- a top-level row never vanishes quietly

class TestMainMenuRowsNeverVanishSilently:

    def test_unowned_row_falls_back_and_logs(self, caplog):
        ctx = TUIContext(dialog=RecordingDialog())
        registry = HandlerRegistry(ctx)          # deliberately EMPTY
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


# -------------------------------------------- the reversed design stays gone

class TestTheEscapeHatchStaysRemoved:
    """A reversal only holds if re-adding the machinery fails a test.

    Otherwise the next session reads the old plan, sees an escape hatch
    described, and rebuilds it beside the thing that replaced it — which
    is how the twin repo ended up applying its own fix to one menu and
    not the other.
    """

    def test_no_session_wide_override(self):
        assert not hasattr(TUIContext(dialog=None), "show_all_features"), (
            "show_all_features returned. Nothing is hidden any more, so "
            "there is nothing to un-hide — an override could only "
            "un-mark rows, which is what the profile is for.")

    def test_no_reserved_tag_or_hatch_helpers(self):
        for gone in ("SHOW_ALL_TAG", "gating_row", "get_gateable_items",
                     "flag_allowed", "get_hidden_items"):
            assert not hasattr(HandlerRegistry, gone), (
                f"HandlerRegistry.{gone} returned — it belongs to the "
                f"hide-and-un-hide design that was reversed 2026-09-16")

    def test_one_predicate_answers_is_this_feature_on(self):
        """Four predicates over one question was the complexity smell."""
        import inspect
        src = inspect.getsource(HandlerRegistry)
        assert src.count("def flag_allowed") == 0
        assert src.count("feature_enabled(") >= 1


# ------------------- every cross-section row inherits its OWNER's flag

class TestEveryCrossSectionRowInheritsItsOwnersFlag:
    """Three menus carry a row whose handler lives in another section
    (``CROSS_SECTION_ROWS``). ``dispatch()`` refuses on the OWNER's flag,
    so the row must be marked from the owner's flag too, or the screen
    says "available" and the keypress says "not in this profile". Until
    2026-09-16 only mfmaps threaded it by hand; now the registry alias
    renders all three from the owner. These drive the REAL menu loops
    with the owner's flag forced off, so the wiring is what is under
    test, not a copy of it. The owner's ``menu_items`` is patched on the
    CLASS (``patch.object``) because an instance assignment would raise
    on a ``__slots__`` ``_LazyHandler`` (review R7).
    """

    @staticmethod
    def _drive(menu_method, holder, owner_section, owner_tag):
        from unittest.mock import patch as _patch
        ctx = holder._tui_context
        ctx.feature_flags = {"maps": False}
        ctx.profile = SimpleNamespace(
            name=SimpleNamespace(value="testprofile"),
            feature_flags=ctx.feature_flags)
        registry = holder._registry
        owner = registry._tag_index[owner_section][owner_tag]
        orig = type(owner).menu_items
        forced = lambda self: [  # noqa: E731 — the owner gains a flag
            (t, d, "maps") if t == owner_tag else (t, d, f)
            for t, d, f in orig(self)]
        seen = []

        def fake_menu(title, subtitle, choices):
            seen.append(list(choices))
            return "back"

        holder.dialog = SimpleNamespace(menu=fake_menu)
        holder._build_section_menu = (
            lambda *a: tui_main.MeshForgeLauncher._build_section_menu(
                holder, *a))
        with _patch.object(type(owner), "menu_items", forced):
            menu_method(holder)
        assert seen, "the menu loop rendered nothing"
        return dict(seen[0])

    def test_dashboard_network_is_marked_from_its_owner(self):
        _ctx, _registry, holder = _make()
        rows = self._drive(tui_main.MeshForgeLauncher._dashboard_menu,
                           holder, "system", "network")
        assert rows["network"].startswith(OFF), rows["network"]

    def test_configuration_rns_config_is_marked_from_its_owner(self):
        _ctx, _registry, holder = _make()
        rows = self._drive(tui_main.MeshForgeLauncher._configuration_menu,
                           holder, "rns", "edit")
        assert rows["rns-config"].startswith(OFF), rows["rns-config"]

    def test_extensions_mfmaps_is_marked_from_its_owner(self):
        _ctx, _registry, holder = _make()
        rows = self._drive(tui_main.MeshForgeLauncher._extensions_menu,
                           holder, "maps_viz", "mfmaps")
        assert rows["mfmaps"].startswith(OFF), rows["mfmaps"]


# ------------------------ the refusal is titled with the row you selected

class TestCrossSectionRefusalNamesTheRowOnScreen:
    """Review R1 (2026-09-16): the refusal dialog was titled with the
    OWNER's label while the screen showed a hand-copied one — 'Network
    Tools — not in this profile' after selecting 'Network Status'. With
    one declaration the rendered label and the refusal's title are the
    same string."""

    def test_extensions_mfmaps_refusal_matches_its_row(self):
        ctx, registry, holder = _make(PROFILES[ProfileName.MONITOR])
        rendered = dict(_rows(holder, "extensions"))["mfmaps"]
        assert rendered.startswith(OFF)
        assert registry.dispatch("extensions", "mfmaps") is True
        title, _body = ctx.dialog.msgboxes[-1]
        shown = rendered[len(OFF):].strip().split("  ")[0]
        assert title.startswith(shown), (title, rendered)

    def test_an_alias_to_nothing_fails_at_declaration(self):
        _ctx, registry, _h = _make()
        with pytest.raises(ValueError):
            registry.alias("dashboard", "ghost", "system", "no-such-tag")

    def test_an_alias_over_an_owned_tag_fails_at_declaration(self):
        _ctx, registry, _h = _make()
        owned = next(iter(registry._tag_index["dashboard"]))
        with pytest.raises(ValueError):
            registry.alias("dashboard", owned, "system", "network")


# ------------------------------- the count agrees with the marks on screen

class TestTheGatedCountCountsRows:
    """Review F4: the Settings dialog and the startup log counted handler
    ACTIONS, so the marked cross-section copy of mfmaps in Extensions was
    not in the number the operator read against the marks on screen."""

    def test_extensions_marked_copy_is_counted(self):
        _ctx, registry, holder = _make(PROFILES[ProfileName.MONITOR])
        marked_rows = [t for t, d in _rows(holder, "extensions")
                       if d.startswith(OFF)]
        counted = [t for t, _d, _f in registry.get_gated_items("extensions")]
        assert marked_rows == counted == ["mfmaps"], (marked_rows, counted)

    def test_count_equals_marks_on_every_screen(self):
        _ctx, registry, holder = _make(PROFILES[ProfileName.MONITOR])
        for section in registry.section_names:
            marked = sorted(t for t, d in _rows(holder, section)
                            if d.startswith(OFF))
            counted = sorted(t for t, _d, _f in registry.get_gated_items(section))
            assert marked == counted, (section, marked, counted)
