"""Drift + fidelity guards for the generated handler manifest.

Step 1 of the manifest-lazy handler registry arc
(`.claude/plans/manifest_lazy_handler_registry_design.md`): additive, no
behavior change (`main.py` still registers eagerly). The generated
`src/launcher_tui/handlers/manifest.py` (`HANDLER_MANIFEST`) is a per-commit
snapshot of every TUI handler's menu metadata + import path, so the registry
can build menus / validate tags WITHOUT importing the module and import it
lazily at first dispatch (`HandlerRegistry.register_lazy` / `_LazyHandler`).

This module pins that artifact honest (honest_failure_modes):
  * #1/#5 — the manifest cannot silently drift from the live registry (one
    walker, byte-compared) and cannot pass vacuously (broken discovery FAILS).
  * #1/#9 — a handler on disk but absent from the manifest = dead UI, caught;
    a lazy load failure surfaces a witness, never a silent no-op.

The generator + manifest are ONE walker / two artifacts with the capability
index (`scripts/gen_capability_index.py`), so this mirrors
`tests/test_capability_index.py`.
"""

import ast
import glob
import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

# conftest.py already puts src/ and src/launcher_tui/ on sys.path.
from handler_protocol import TUIContext, LifecycleHandler
from handler_registry import HandlerRegistry, _LazyHandler

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLERS_DIR = REPO_ROOT / "src" / "launcher_tui" / "handlers"

# Import the generator by path (it lives in scripts/, not on the package path).
# Importing it also runs its sys.path setup for handler imports. A distinct
# module name avoids clashing with test_capability_index's copy in sys.modules.
_GEN_PATH = REPO_ROOT / "scripts" / "gen_capability_index.py"
_spec = importlib.util.spec_from_file_location("gen_cap_index_for_manifest", _GEN_PATH)
_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen)

build_manifest_content = _gen.build_manifest_content
collect_handler_descriptors = _gen.collect_handler_descriptors
MANIFEST_PATH = _gen.MANIFEST_PATH

_MANIFEST_KEYS = {
    "handler_id", "module", "class_name", "menu_section",
    "menu_items", "lifecycle", "error",
}


def _committed_manifest() -> str:
    return MANIFEST_PATH.read_text(encoding="utf-8")


def _live_manifest():
    """The checked-in HANDLER_MANIFEST as importable Python data."""
    import handlers.manifest as _m
    return _m.HANDLER_MANIFEST


# ─────────────────────────────────────────────────────────────────────────
# A. Drift + non-vacuity — the manifest tracks the live registry, byte-for-byte
# ─────────────────────────────────────────────────────────────────────────

class TestManifestDrift:
    def test_committed_manifest_matches_live_registry(self):
        """The core pin: committed manifest == freshly-built. If this fails, a
        handler or its menu metadata changed without regenerating the manifest —
        run `python3 scripts/gen_capability_index.py` and commit."""
        assert MANIFEST_PATH.exists(), (
            f"{MANIFEST_PATH} is missing — run "
            f"`python3 scripts/gen_capability_index.py`")
        committed = _committed_manifest()
        fresh = build_manifest_content()
        assert committed == fresh, (
            "handler manifest is STALE: it does not match the live handler "
            "registry. A handler or menu action was added/renamed/removed "
            "without regenerating. Run `python3 scripts/gen_capability_index.py` "
            "and commit src/launcher_tui/handlers/manifest.py")

    def test_manifest_is_not_vacuous(self):
        """honest_failure_modes #1/#2 — a generator that discovered nothing
        (broken path, empty registry) must FAIL, not pass an empty file."""
        entries = _live_manifest()
        assert len(entries) >= 50, (
            f"only {len(entries)} manifest entries — discovery likely broke")
        for d in entries:
            assert set(d) == _MANIFEST_KEYS, (
                f"manifest entry {d.get('handler_id')!r} has non-uniform keys "
                f"{set(d)} — the schema drifted")

    def test_manifest_carries_generated_warning(self):
        """The committed artifact must announce it's generated, so a hand-edit
        without regenerating is visibly wrong (and caught by the pin above)."""
        committed = _committed_manifest()
        assert "AUTO-GENERATED handler manifest" in committed
        assert "do NOT edit by hand" in committed
        assert "HANDLER_MANIFEST = [" in committed

    def test_red_drift_is_detected(self):
        """RED proof — the equality check is meaningful: a single mutated line
        must NOT equal the freshly-built manifest."""
        fresh = build_manifest_content()
        mutated = fresh.replace("HANDLER_MANIFEST = [", "HANDLER_MANIFEST_X = [", 1)
        assert mutated != fresh
        assert _committed_manifest() == fresh


# ─────────────────────────────────────────────────────────────────────────
# B. Fidelity — every entry imports, instantiates, and its snapshot is exact
#    (today's eager-startup cost moved into CI, where it belongs)
# ─────────────────────────────────────────────────────────────────────────

class TestManifestFidelity:
    def test_every_entry_imports_instantiates_and_matches_snapshot(self):
        entries = _live_manifest()
        neutral = TUIContext(dialog=None)
        for d in entries:
            hid = d["handler_id"]
            assert d["error"] is None, (
                f"manifest entry {hid!r} carries a generation error "
                f"({d['error']}) — it cannot be registered; fix the handler")
            module = importlib.import_module(d["module"])
            cls = getattr(module, d["class_name"], None)
            assert cls is not None, (
                f"manifest entry {hid!r}: class {d['class_name']!r} not found "
                f"in module {d['module']!r}")
            inst = cls()
            inst.set_context(neutral)
            assert getattr(inst, "handler_id", None) == hid
            assert getattr(inst, "menu_section", None) == d["menu_section"]
            live = [tuple(it) for it in inst.menu_items()]
            snap = [tuple(it) for it in d["menu_items"]]
            assert live == snap, (
                f"menu_items snapshot mismatch for {hid!r} — the manifest is "
                f"stale.\n live={live}\n snap={snap}")
            assert isinstance(inst, LifecycleHandler) == d["lifecycle"], (
                f"lifecycle flag wrong for {hid!r} — a lifecycle handler must "
                f"stay eager")

    def test_menu_items_are_raw_not_markdown_cleaned(self):
        """The equality check compares live menu_items() to the snapshot, so the
        snapshot must be RAW — flags stay None (not ""), labels keep their column
        padding. A markdown-cleaned snapshot would fail every drift check."""
        entries = _live_manifest()
        # At least one real handler has a padded label (whiptail two-column) and
        # at least one has a None flag — proof we stored raw tuples, not the
        # index's em-dash/`or ""` form.
        assert any(
            any("  " in str(desc) for _, desc, _ in d["menu_items"])
            for d in entries
        ), "no padded labels found — snapshot may have been markdown-cleaned"
        assert any(
            any(flag is None for _, _, flag in d["menu_items"])
            for d in entries
        ), "no None flags found — snapshot may have coerced flags to ''"


# ─────────────────────────────────────────────────────────────────────────
# C. Reachability — a handler on disk but absent from the manifest is dead UI
#    (mirrors TestHandlerReachability, pivoted to manifest-vs-package-walk)
# ─────────────────────────────────────────────────────────────────────────

def _class_has_str_attr(node: ast.ClassDef, name: str) -> bool:
    for stmt in node.body:
        target = None
        value = None
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    target, value = t, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id == name:
                target, value = stmt.target, stmt.value
        if target is not None and isinstance(value, ast.Constant):
            if isinstance(value.value, str) and value.value:
                return True
    return False


def _discover_handler_classes() -> dict:
    """{class_name: module_basename} for every concrete handler defined on disk
    (a class with non-empty handler_id AND menu_section class literals). Mirrors
    tests/test_honesty_invariants.discover_handler_classes; kept local so this
    guard is hermetic. Skips __init__.py and _-prefixed helper modules."""
    discovered: dict = {}
    for path in sorted(glob.glob(str(HANDLERS_DIR / "*.py"))):
        base = os.path.basename(path)
        if base == "__init__.py" or base.startswith("_"):
            continue
        tree = ast.parse(Path(path).read_text(), path)
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and (
                _class_has_str_attr(node, "handler_id")
                and _class_has_str_attr(node, "menu_section")
            ):
                discovered[node.name] = base
    return discovered


class TestManifestReachability:
    def test_every_handler_on_disk_is_in_manifest(self):
        discovered = _discover_handler_classes()
        assert len(discovered) >= 50, (
            f"only {len(discovered)} handler classes discovered under "
            f"{HANDLERS_DIR} — discovery likely broke; refusing to pass "
            f"vacuously")
        manifest_classes = {d["class_name"] for d in _live_manifest()}
        missing = {n: m for n, m in discovered.items()
                   if n not in manifest_classes}
        assert not missing, (
            f"handler class(es) exist on disk but are NOT in the manifest → "
            f"silently unreachable UI once the registry goes manifest-lazy: "
            f"{missing}. Add the handler to handlers/__init__.py and regenerate "
            f"the manifest.")

    def test_manifest_module_basename_matches_disk(self):
        """Every manifest module path resolves to a real handlers/*.py — a
        typo'd or renamed module in the manifest would fail lazy import."""
        discovered_modules = {
            m[:-3] for m in _discover_handler_classes().values()
        }
        for d in _live_manifest():
            mod = d["module"]
            assert mod.startswith("handlers."), (
                f"unexpected module path {mod!r} for {d['handler_id']!r}")
            base = mod.split(".", 1)[1]
            assert base in discovered_modules, (
                f"manifest module {mod!r} for {d['handler_id']!r} has no "
                f"matching handlers/{base}.py on disk")


# ─────────────────────────────────────────────────────────────────────────
# D. register_lazy — indexes from the descriptor WITHOUT importing the module
# ─────────────────────────────────────────────────────────────────────────

def _recording_dialog():
    class _D:
        def __init__(self):
            self.msgboxes = []

        def msgbox(self, title, body):
            self.msgboxes.append((title, body))

        def textbox(self, title, body):
            self.msgboxes.append((title, body))

    return _D()


def _make_ctx():
    """A TUIContext with a recording dialog + hermetic error-log stubs (avoids
    writing the real TUI error log during dispatch-failure tests)."""
    dialog = _recording_dialog()
    ctx = TUIContext(dialog=dialog)
    ctx.log_error = lambda *a, **k: None
    ctx.get_error_log_path = lambda: Path("/tmp/meshforge-test-tui-error.log")
    return ctx, dialog


def _descriptor(**over):
    d = {
        "handler_id": "fake",
        "module": "totally.nonexistent.module.path",  # deliberately un-importable
        "class_name": "Ghost",
        "menu_section": "faketest",
        "menu_items": [("t1", "Item one", None), ("t2", "Item two", None)],
        "lifecycle": False,
        "error": None,
    }
    d.update(over)
    return d


class TestRegisterLazy:
    def test_indexes_menu_without_importing_module(self):
        # The module path is un-importable on purpose: register_lazy + menu build
        # succeeding proves NO import happens until dispatch.
        ctx, _ = _make_ctx()
        reg = HandlerRegistry(ctx)
        reg.register_lazy(_descriptor())
        assert reg.handler_count == 1
        items = reg.get_menu_items("faketest")
        assert ("t1", "Item one") in items
        assert ("t2", "Item two") in items
        assert isinstance(reg._tag_index["faketest"]["t1"], _LazyHandler)

    def test_refuses_lifecycle_descriptor(self):
        ctx, _ = _make_ctx()
        reg = HandlerRegistry(ctx)
        with pytest.raises(ValueError, match="lifecycle"):
            reg.register_lazy(_descriptor(lifecycle=True))
        assert reg.handler_count == 0

    def test_refuses_errored_descriptor(self):
        ctx, _ = _make_ctx()
        reg = HandlerRegistry(ctx)
        with pytest.raises(ValueError, match="generation-time error"):
            reg.register_lazy(_descriptor(error="ImportError: boom"))
        assert reg.handler_count == 0

    def test_refuses_duplicate_handler_id(self):
        ctx, _ = _make_ctx()
        reg = HandlerRegistry(ctx)
        reg.register_lazy(_descriptor())
        with pytest.raises(ValueError, match="already registered"):
            reg.register_lazy(_descriptor(
                menu_section="other", menu_items=[("z", "Z", None)]))

    def test_refuses_duplicate_tag_across_handlers(self):
        ctx, _ = _make_ctx()
        reg = HandlerRegistry(ctx)
        reg.register_lazy(_descriptor())
        with pytest.raises(ValueError, match="Duplicate tag"):
            reg.register_lazy(_descriptor(
                handler_id="fake2", menu_items=[("t1", "dup", None)]))

    def test_eager_and_lazy_share_the_same_tag_guard(self):
        """A lazy handler and an eager one cannot claim the same section+tag —
        the guard is shared, so cross-path collisions are caught too."""
        ctx, _ = _make_ctx()
        reg = HandlerRegistry(ctx)
        reg.register_lazy(_descriptor())

        class _Eager:
            handler_id = "eager"
            menu_section = "faketest"

            def set_context(self, c):
                self.ctx = c

            def menu_items(self):
                return [("t1", "collide", None)]

            def execute(self, action):
                pass

        with pytest.raises(ValueError, match="Duplicate tag"):
            reg.register(_Eager())


# ─────────────────────────────────────────────────────────────────────────
# E. dispatch — materializes a lazy handler, verifies the snapshot, surfaces
#    an honest witness on failure (never a silent no-op)
# ─────────────────────────────────────────────────────────────────────────

def _install_fake_module(monkeypatch, name, live_items):
    """Install a fake handler module into sys.modules (auto-removed after the
    test) whose FakeHandler records the action it was told to execute."""
    mod = types.ModuleType(name)

    class FakeHandler:
        handler_id = "fakeh"
        menu_section = "faketest"
        executed = None

        def set_context(self, ctx):
            self.ctx = ctx

        def menu_items(self):
            return list(live_items)

        def execute(self, action):
            type(self).executed = action

    mod.FakeHandler = FakeHandler
    monkeypatch.setitem(sys.modules, name, mod)
    return FakeHandler


def _lazy_desc(name, snapshot_items):
    return {
        "handler_id": "fakeh",
        "module": name,
        "class_name": "FakeHandler",
        "menu_section": "faketest",
        "menu_items": snapshot_items,
        "lifecycle": False,
        "error": None,
    }


class TestLazyDispatchMaterialization:
    def test_dispatch_materializes_instantiates_and_executes(self, monkeypatch):
        live = [("do", "Do  thing", None)]
        Fake = _install_fake_module(monkeypatch, "fake_lazy_ok", live)
        Fake.executed = None
        ctx, dialog = _make_ctx()
        reg = HandlerRegistry(ctx)
        reg.register_lazy(_lazy_desc("fake_lazy_ok", live))

        assert isinstance(reg._tag_index["faketest"]["do"], _LazyHandler)
        handled = reg.dispatch("faketest", "do")

        assert handled is True
        assert Fake.executed == "do"
        # The real instance replaced the sentinel for id + tag → later dispatch
        # is direct (no re-import).
        real = reg._tag_index["faketest"]["do"]
        assert not isinstance(real, _LazyHandler)
        assert reg._handlers["fakeh"] is real
        assert dialog.msgboxes == []  # a clean load shows no dialog

    def test_second_dispatch_hits_real_instance(self, monkeypatch):
        live = [("do", "Do  thing", None)]
        Fake = _install_fake_module(monkeypatch, "fake_lazy_twice", live)
        ctx, _ = _make_ctx()
        reg = HandlerRegistry(ctx)
        reg.register_lazy(_lazy_desc("fake_lazy_twice", live))
        reg.dispatch("faketest", "do")
        first = reg._handlers["fakeh"]
        Fake.executed = None
        reg.dispatch("faketest", "do")
        assert Fake.executed == "do"
        assert reg._handlers["fakeh"] is first  # same instance, not re-created

    def test_dispatch_refuses_on_manifest_drift(self, monkeypatch):
        # Live menu_items differ from the snapshot → stale manifest that dodged
        # the drift gate → refuse to dispatch, surface a loud dialog.
        live = [("do", "REAL  label", None)]
        Fake = _install_fake_module(monkeypatch, "fake_lazy_drift", live)
        Fake.executed = None
        ctx, dialog = _make_ctx()
        reg = HandlerRegistry(ctx)
        reg.register_lazy(_lazy_desc("fake_lazy_drift", [("do", "STALE  label", None)]))

        handled = reg.dispatch("faketest", "do")

        assert handled is True  # action was owned; failure surfaced, not silent
        assert Fake.executed is None  # never executed on drifted metadata
        assert any("Manifest Drift" in title for title, _ in dialog.msgboxes), (
            "manifest drift must surface a loud, dedicated dialog")

    def test_dispatch_import_failure_surfaces_witness(self):
        # Un-importable module → honest dialog + log, never a silent no-op.
        ctx, dialog = _make_ctx()
        reg = HandlerRegistry(ctx)
        reg.register_lazy({
            "handler_id": "ghosth",
            "module": "no.such.module.xyz123",
            "class_name": "Ghost",
            "menu_section": "faketest",
            "menu_items": [("g", "Ghost", None)],
            "lifecycle": False,
            "error": None,
        })
        handled = reg.dispatch("faketest", "g")
        assert handled is True  # owned; the witness is the dialog below
        assert len(dialog.msgboxes) >= 1, (
            "a lazy load failure must show an honest dialog, never no-op")

    def test_dispatch_unknown_tag_returns_false(self):
        ctx, _ = _make_ctx()
        reg = HandlerRegistry(ctx)
        reg.register_lazy(_descriptor())
        assert reg.dispatch("faketest", "no_such_tag") is False
        assert reg.dispatch("no_such_section", "t1") is False
