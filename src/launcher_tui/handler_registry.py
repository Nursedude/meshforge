"""
Handler Registry — Central dispatch for TUI command handlers.

Manages handler registration, menu-item aggregation, feature-flag
filtering, and action dispatch. Replaces the inline ``dispatch = {}``
dictionaries scattered across MeshForgeLauncher submenu methods.

Phase 0 of the migration: infrastructure only, no existing code changed.

See also:
    handler_protocol.py — TUIContext, CommandHandler, BaseHandler
    handlers/            — Converted handler implementations
"""

import importlib
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from handler_protocol import CommandHandler, LifecycleHandler, TUIContext

logger = logging.getLogger(__name__)


class _LazyHandler:
    """Tag-index sentinel for a handler whose module is NOT yet imported.

    Holds a generated manifest descriptor (see ``handlers/manifest.py``) and
    exposes just enough of the CommandHandler surface — ``handler_id``,
    ``menu_section``, ``menu_items()`` — for the registry to build menus and
    validate tags WITHOUT importing the handler's module. The real class is
    imported + instantiated at first dispatch of one of its tags
    (``HandlerRegistry._materialize``), then swapped in.

    ``menu_items()`` returns the RAW manifest snapshot; at materialization the
    live ``menu_items()`` is compared against it (a mismatch = a stale manifest
    that dodged the drift gate).
    """

    __slots__ = ("descriptor", "handler_id", "menu_section", "_menu_items")

    def __init__(self, descriptor: dict):
        self.descriptor = descriptor
        self.handler_id: str = descriptor["handler_id"]
        self.menu_section: str = descriptor["menu_section"]
        self._menu_items = [tuple(it) for it in descriptor["menu_items"]]

    def menu_items(self) -> List[Tuple]:
        return list(self._menu_items)

    def execute(self, action: str) -> None:  # pragma: no cover - guard only
        raise RuntimeError(
            f"_LazyHandler for {self.handler_id!r} was asked to execute "
            f"{action!r} before its module was imported — dispatch() must "
            f"materialize a lazy handler before calling execute()."
        )


class HandlerRegistry:
    """Central registry for TUI command handlers.

    Handlers register themselves (or are registered by the launcher).
    Submenu orchestrators call ``get_menu_items(section)`` to build menus
    and ``dispatch(section, tag)`` to execute actions.

    During the migration, submenus try registry dispatch first and fall
    back to legacy mixin methods when no handler is found.
    """

    def __init__(self, ctx: TUIContext):
        self._ctx = ctx
        self._handlers: Dict[str, CommandHandler] = {}
        self._sections: Dict[str, List[CommandHandler]] = defaultdict(list)
        # Tag-to-handler index for O(1) dispatch
        self._tag_index: Dict[str, Dict[str, CommandHandler]] = defaultdict(dict)

    def register(self, handler: CommandHandler) -> None:
        """Register a handler, injecting the shared context.

        Args:
            handler: A CommandHandler instance. Must have a unique handler_id.

        Raises:
            ValueError: If handler_id is already registered.
        """
        hid = handler.handler_id
        if hid in self._handlers:
            raise ValueError(
                f"Handler {hid!r} already registered "
                f"(existing: {type(self._handlers[hid]).__name__}, "
                f"new: {type(handler).__name__})"
            )

        # Context first (registration order pre-dated the guard; keep the
        # old invariant that menu_items() only ever runs on a
        # context-injected handler), then ONE snapshot of menu_items() —
        # validating one list and indexing another would let a dynamic
        # implementation dodge the guard.
        handler.set_context(self._ctx)
        items = list(handler.menu_items())

        # Validate tags BEFORE mutating registry state, so a duplicate
        # leaves the registry unchanged (no half-registered handler).
        self._validate_tags(hid, handler.menu_section, items)

        self._handlers[hid] = handler
        self._sections[handler.menu_section].append(handler)

        for tag, _desc, _flag in items:
            self._tag_index[handler.menu_section][tag] = handler

        logger.debug(
            "Registered handler %s (section=%s, items=%d)",
            hid, handler.menu_section, len(items),
        )

    def _validate_tags(self, hid: str, section: str,
                       items: List[Tuple[str, str, Optional[str]]]) -> None:
        """Reject duplicate tags BEFORE any registry mutation.

        Shared verbatim by ``register()`` (eager) and ``register_lazy()``
        (manifest) so both paths enforce the SAME guard from one place
        (honest_failure_modes #5 — two consumers, one rule). ``seen``
        accumulates THIS handler's own tags, so a copy-pasted row duplicating a
        tag within a single handler is caught too — never a silent
        last-write-wins. ``existing`` may be a real handler or a
        ``_LazyHandler``; both carry ``handler_id`` for the message.
        """
        seen: set = set()
        for tag, _desc, _flag in items:
            existing = self._tag_index[section].get(tag)
            if existing is not None:
                raise ValueError(
                    f"Duplicate tag {tag!r} in section {section!r}: already "
                    f"owned by {existing.handler_id!r}, refusing {hid!r} — a "
                    f"duplicate would silently shadow one handler's action"
                )
            if tag in seen:
                raise ValueError(
                    f"Duplicate tag {tag!r} WITHIN {hid!r}'s own menu_items() "
                    f"— a copy-pasted menu row would render twice and silently "
                    f"shadow itself"
                )
            seen.add(tag)

    def register_lazy(self, descriptor: dict) -> None:
        """Register a handler from its generated manifest descriptor WITHOUT
        importing its module — the module is imported + instantiated at first
        dispatch of one of its tags (see ``dispatch`` / ``_materialize``).

        Mirrors ``register()``'s duplicate-tag guard exactly, reading the tags
        from the descriptor's ``menu_items`` snapshot. This is the additive
        step-1 API of the manifest-lazy registry arc: nothing wires it yet
        (``main.py`` still registers eagerly), so it changes no live behavior.

        Refuses two descriptor shapes LOUDLY rather than silently mis-register
        (honest_failure_modes #1 — a degraded input must not read as valid):

        * ``lifecycle: True`` — a handler with on_startup/on_shutdown hooks; its
          hooks cannot run if the module is imported lazily at dispatch, so it
          MUST be registered eagerly via ``register()``.
        * ``error`` set — the generator could not construct the handler / read
          its ``menu_items()``, so its menu metadata is unknown and it cannot be
          registered until it constructs cleanly.

        Raises:
            ValueError: on a lifecycle/errored descriptor, a duplicate
                handler_id, or a duplicate tag.
        """
        hid = descriptor["handler_id"]
        if descriptor.get("lifecycle"):
            raise ValueError(
                f"Handler {hid!r} is a lifecycle handler (on_startup/"
                f"on_shutdown) and MUST be registered eagerly — its startup "
                f"hook cannot run if the module is imported lazily at first "
                f"dispatch. Register it via register() from an eager import."
            )
        if descriptor.get("error"):
            raise ValueError(
                f"Refusing to lazy-register {hid!r}: its manifest entry records "
                f"a generation-time error ({descriptor['error']}) — its menu "
                f"metadata is unknown. Regenerate the manifest once the handler "
                f"constructs cleanly."
            )
        if hid in self._handlers:
            raise ValueError(
                f"Handler {hid!r} already registered "
                f"(existing: {type(self._handlers[hid]).__name__}, "
                f"new: lazy from {descriptor.get('module')!r})"
            )

        lazy = _LazyHandler(descriptor)
        items = lazy.menu_items()
        self._validate_tags(hid, lazy.menu_section, items)

        self._handlers[hid] = lazy
        self._sections[lazy.menu_section].append(lazy)
        for tag, _desc, _flag in items:
            self._tag_index[lazy.menu_section][tag] = lazy

        logger.debug(
            "Registered LAZY handler %s (section=%s, items=%d, module=%s)",
            hid, lazy.menu_section, len(items), descriptor.get("module"),
        )

    def get_handler(self, handler_id: str) -> Optional[CommandHandler]:
        """Look up a handler by its unique ID, materializing a lazy sentinel.

        Cross-handler delegations (``get_handler("broker")._broker_menu()``)
        need the target's real code, so a ``_LazyHandler`` is imported +
        instantiated here exactly as ``dispatch`` does (step-2 review
        BLOCKER-2). On materialize failure this returns None — the witness is
        surfaced inside ``_materialize`` and callers' existing ``if handler:``
        null-guards degrade honestly instead of crashing on a sentinel.

        Returns:
            The handler, or None if not found / failed to load.
        """
        handler = self._handlers.get(handler_id)
        if isinstance(handler, _LazyHandler):
            return self._materialize(handler, handler.menu_section)
        return handler

    def get_menu_items(self, section: str) -> List[Tuple[str, str]]:
        """Get filtered menu items for a section, respecting feature flags.

        Args:
            section: Menu section key (e.g., ``"dashboard"``, ``"rf_sdr"``).

        Returns:
            List of (tag, description) tuples, filtered by feature flags.
        """
        items: List[Tuple[str, str]] = []
        for handler in self._sections.get(section, []):
            for tag, desc, flag in handler.menu_items():
                if flag is None or self._ctx.feature_enabled(flag):
                    items.append((tag, desc))
        return items

    def dispatch(self, section: str, tag: str) -> bool:
        """Find and execute the handler for a given section + tag.

        Wraps the handler's ``execute()`` in ``safe_call()`` for
        consistent error handling. If the matched handler is a lazily-
        registered sentinel, its module is imported + instantiated first
        (``_materialize``); an import/drift failure there surfaces its own
        honest witness and this still returns True (the action WAS owned by a
        handler — never fall through to a "no such action" path or a legacy
        mixin, which would show a second, misleading message).

        Args:
            section: Menu section key.
            tag: The action tag selected by the user.

        Returns:
            True if a handler was found (invoked, or surfaced a load failure),
            False if no handler owns the tag.
        """
        handler = self._tag_index.get(section, {}).get(tag)
        if handler is None:
            return False

        if isinstance(handler, _LazyHandler):
            handler = self._materialize(handler, section)
            if handler is None:
                # Import/drift failure — the witness (dialog + log) is already
                # surfaced inside _materialize; do NOT dispatch on it.
                return True

        self._ctx.safe_call(handler.handler_id, handler.execute, tag)
        return True

    def _materialize(self, lazy: "_LazyHandler",
                     section: str) -> Optional[CommandHandler]:
        """Import + instantiate a lazily-registered handler at first dispatch.

        On success: verifies the live ``menu_items()`` matches the manifest
        snapshot, swaps the real instance into ``_handlers``/``_sections``/
        ``_tag_index`` (so later dispatches are direct), and returns it.

        On failure it NEVER silently no-ops (honest_failure_modes #1/#9 — this
        is a loader):

        * import / construction error → the SAME honest, dependency-aware
          dialog the eager ``_load_x()`` pattern shows, via ``safe_call`` (an
          ImportError names the missing module + a ``pip3 install`` hint), plus
          a log witness. Returns None.
        * manifest drift (live ``menu_items()`` != the snapshot) → a dedicated
          loud "Handler Manifest Drift" dialog + ERROR log, and the action is
          REFUSED rather than run on wrong metadata. This means a stale manifest
          dodged the drift gate. Surfaced in-app (MF018 — no crash of the
          primary TUI), not raised out of the dispatch loop. Returns None.
        """
        module_path = lazy.descriptor["module"]
        class_name = lazy.descriptor["class_name"]

        def _load() -> CommandHandler:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            inst = cls()
            inst.set_context(self._ctx)
            return inst

        inst = self._ctx.safe_call(f"load handler {lazy.handler_id!r}", _load)
        if inst is None:
            # safe_call already showed an honest dialog and logged the cause.
            logger.error(
                "Lazy handler %s failed to load (module=%s, class=%s)",
                lazy.handler_id, module_path, class_name,
            )
            return None

        live_items = [tuple(it) for it in inst.menu_items()]
        snapshot = [tuple(it) for it in lazy.descriptor["menu_items"]]
        if live_items != snapshot:
            logger.error(
                "Manifest drift for handler %s: live menu_items() != snapshot "
                "(manifest is stale). live=%r manifest=%r",
                lazy.handler_id, live_items, snapshot,
            )
            self._ctx.log_error(
                f"manifest drift for {lazy.handler_id}",
                RuntimeError("live menu_items() != checked-in manifest snapshot"),
            )
            self._ctx.dialog.msgbox(
                "Handler Manifest Drift",
                f"The menu metadata for {lazy.handler_id!r} in the checked-in "
                f"handler manifest does NOT match the handler's live "
                f"menu_items().\n\n"
                f"The manifest is STALE (it dodged the drift gate). This action "
                f"is refused rather than run on wrong metadata.\n\n"
                f"Fix: regenerate the manifest —\n"
                f"  python3 scripts/gen_capability_index.py\n"
                f"then commit src/launcher_tui/handlers/manifest.py.",
            )
            return None

        # Swap the real instance in for its id + every one of its tags.
        self._handlers[lazy.handler_id] = inst
        self._sections[section] = [
            inst if h is lazy else h
            for h in self._sections.get(section, [])
        ]
        for tag, _desc, _flag in live_items:
            self._tag_index[section][tag] = inst
        logger.debug(
            "Materialized lazy handler %s from %s", lazy.handler_id, module_path
        )
        return inst

    def startup_all(self) -> None:
        """Call ``on_startup()`` on all handlers that implement LifecycleHandler.

        A ``_LazyHandler`` sentinel is never a LifecycleHandler, so it is
        correctly skipped here — and ``register_lazy`` REFUSES a lifecycle
        descriptor, so a handler with startup hooks is never registered lazily
        (its hooks would otherwise silently never run).
        """
        for handler in self._handlers.values():
            if isinstance(handler, LifecycleHandler):
                try:
                    handler.on_startup()
                except Exception as e:
                    logger.warning(
                        "Startup hook failed for %s: %s",
                        handler.handler_id, e,
                    )

    def shutdown_all(self) -> None:
        """Call ``on_shutdown()`` on all handlers that implement LifecycleHandler.

        DELIBERATELY unconditional (Q5 re-decide of audit W11): in daemon
        mode ``startup_all()`` is skipped, but menu actions can still
        create resources (e.g. the in-process map server on a unit-less
        box), and this sweep at exit is what reclaims them. The contract
        this relies on — every ``on_shutdown()`` must be SAFE TO CALL
        WITHOUT its ``on_startup()`` having run — is pinned by
        tests/test_handler_registry.py::TestShutdownWithoutStartupIsSafe,
        so a hook that assumes started-state fails a test, not a shutdown.
        """
        for handler in self._handlers.values():
            if isinstance(handler, LifecycleHandler):
                try:
                    handler.on_shutdown()
                except Exception as e:
                    logger.warning(
                        "Shutdown hook failed for %s: %s",
                        handler.handler_id, e,
                    )

    @property
    def handler_count(self) -> int:
        """Number of registered handlers."""
        return len(self._handlers)

    @property
    def section_names(self) -> List[str]:
        """List of sections that have at least one handler."""
        return list(self._sections.keys())

    def __repr__(self) -> str:
        return (
            f"HandlerRegistry(handlers={len(self._handlers)}, "
            f"sections={list(self._sections.keys())})"
        )
