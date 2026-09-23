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
        # Cross-section rows: (section, tag) -> (owner_section, owner_tag).
        # See ``alias()`` — the ONE declaration of a row shown on a screen
        # other than its handler's own.
        self._aliases: Dict[str, Dict[str, Tuple[str, str]]] = defaultdict(dict)

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

    #: Prefix a row carries when the active profile does not include it.
    #: A PREFIX rather than a suffix on purpose: whiptail truncates a label
    #: to the box width, so a marker at the end is exactly what disappears
    #: on the 24x80 terminal where it matters most.
    OFF_MARK = "[off] "

    def alias(self, section: str, tag: str,
              owner_section: str, owner_tag: str) -> None:
        """Declare a CROSS-SECTION row: ``tag`` on ``section``'s screen IS
        the action ``owner_section``/``owner_tag``, shown one menu away.

        ONE declaration per row, and nothing else about it is stated
        anywhere: its label, its ``[off]`` mark and the title of its
        refusal all derive from the owner through ``owner_row``, so the
        same action cannot read differently on two screens. Until
        2026-09-16 each menu loop carried a hand-copied label, threaded
        the owner's flag by hand (or forgot to), and the refusal dialog
        was titled with the owner's label while the row showed another —
        three copies of one fact, drifting (review R1/R4/F4).

        Fails loud at declaration: an alias to an owner nobody registered,
        or to a tag the section's own handlers already own, is a wiring
        bug the operator must never meet as a blank or duplicate row.
        """
        if owner_tag not in self._tag_index.get(owner_section, {}):
            raise ValueError(
                f"alias {section}/{tag}: no handler owns "
                f"{owner_section}/{owner_tag}")
        if tag in self._tag_index.get(section, {}):
            raise ValueError(
                f"alias {section}/{tag}: a handler in {section!r} already "
                f"owns that tag — the alias is dead, delete it (audit W7)")
        self._aliases[section][tag] = (owner_section, owner_tag)

    def aliases(self, section: str) -> Dict[str, Tuple[str, str]]:
        """The cross-section rows declared on a section's screen."""
        return dict(self._aliases.get(section, {}))

    def get_menu_items(self, section: str) -> List[Tuple[str, str]]:
        """Every menu row for a section — gated ones MARKED, never removed.

        The rule, stated once: **a profile changes what a row SAYS, never
        whether it is there.** Until 2026-09-16 this filtered gated rows
        out of the list entirely. That was wrong for the people the TUI is
        actually for — someone new to the domain cannot go looking for a
        capability they have never been shown, so hiding teaches them
        nothing and a shorter menu just looks like a smaller product.
        Marking teaches them what the tool does AND why this box does not
        do it, which is the whole job of a deployment profile.

        It is also far less machinery. Nothing disappears, so there is no
        escape hatch to render, no session-wide override to track, no
        reserved tag threaded through eleven menu loops, and no row that
        can scroll off the bottom of a short terminal.

        (Menu LENGTH is a real problem — dashboard is 21 rows — but it is
        a SHAPE problem, and capping sections fixes it for every user
        rather than only for boxes that happen to declare a profile.)

        Cross-section rows (``alias``) come last, rendered from their
        owner's label and flag, so the three menus that carry one no
        longer keep a copy of it.

        Returns:
            List of (tag, description). A row whose flag is off keeps its
            own label, prefixed with ``OFF_MARK`` so the reader still sees
            what the tool is.
        """
        items: List[Tuple[str, str]] = []
        for handler in self._sections.get(section, []):
            for tag, desc, flag in handler.menu_items():
                items.append((tag, self.mark_label(desc, flag)))
        for tag, (osec, otag) in self._aliases.get(section, {}).items():
            row = self.owner_row(osec, otag)
            if row is None:
                # Validated at declaration, so the owner LOST the row
                # since. owner_row logged which half drifted; render the
                # tag rather than let the row vanish (audit W7).
                items.append((tag, tag))
                continue
            items.append((tag, self.mark_label(row[0], row[1])))
        return items

    def mark_label(self, desc: str, flag: Optional[str]) -> str:
        """The label a row shows under the active profile.

        One implementation, because four different menu builders render
        rows — they must all mark identically or the same action reads
        differently depending on which screen you found it on.
        """
        if flag is None or self._ctx.feature_enabled(flag):
            return desc
        return self.OFF_MARK + desc

    def get_gated_items(self, section: str) -> List[Tuple[str, str, str]]:
        """The ROWS the active profile marks off on this screen, and the
        flag that did it — including a cross-section row whose owner is
        off, so the count agrees with the marks the operator can see
        (review F4: the old count was per handler action, and the marked
        copy in Extensions was not in it).

        Not "hidden" — nothing is hidden any more. Used for the honest
        counts in the startup log and the Settings dialog.

        Returns:
            List of (tag, description, flag). Empty when no profile is set.
        """
        gated: List[Tuple[str, str, str]] = []
        for handler in self._sections.get(section, []):
            for tag, desc, flag in handler.menu_items():
                if flag is not None and not self._ctx.feature_enabled(flag):
                    gated.append((tag, desc, flag))
        for tag, (osec, otag) in self._aliases.get(section, {}).items():
            row = self.owner_row(osec, otag)
            if row is None or row[1] is None:
                continue
            if not self._ctx.feature_enabled(row[1]):
                gated.append((tag, row[0], row[1]))
        return gated

    def owner_row(self, section: str,
                  tag: str) -> Optional[Tuple[str, Optional[str]]]:
        """The (description, flag) of the handler that OWNS section/tag,
        following a cross-section alias to its owner first.

        ONE lookup for every reader — ``get_menu_items``,
        ``get_gated_items``, ``dispatch`` and ``explain_gated`` — where
        each used to walk the section on its own (review F5). It can fail
        two ways and logs them as two different sentences because they
        are two different defects (review R5): no handler registered for
        the tag is a wiring gap; a registered handler whose LIVE
        ``menu_items()`` no longer lists the tag is a handler that drifted
        from its own registration snapshot.
        """
        section, tag = self._aliases.get(section, {}).get(tag, (section, tag))
        handler = self._tag_index.get(section, {}).get(tag)
        if handler is None:
            logger.warning(
                "owner_row(%r, %r): no handler in that section owns the tag",
                section, tag)
            return None
        for t, desc, flag in handler.menu_items():
            if t == tag:
                return desc, flag
        logger.warning(
            "owner_row(%r, %r): %s is registered for the tag but its live "
            "menu_items() no longer lists it — the handler drifted from "
            "its registration snapshot",
            section, tag, getattr(handler, "handler_id", "?"))
        return None

    def owner_flag(self, section: str, tag: str) -> Optional[str]:
        """The feature flag a tag carries in the section that OWNS it."""
        row = self.owner_row(section, tag)
        return None if row is None else row[1]

    def explain_gated(self, section: str, tag: str, flag: str,
                      label: Optional[str] = None) -> None:
        """Say why this row is off, and how to change it WITHOUT leaving.

        Derived from the flag and the profile rather than read out of a
        per-feature table of hint strings. A table is a second declaration
        of something the handler already states, free to drift from it and
        certain to be missing an entry the day a flag is added — and the
        twin repo's version of exactly that table has never once been
        shown to anyone.

        The remedy it offers is IN-APP (MF018): the TUI must never answer
        a question by telling the operator to quit and run a CLI.

        The dialog goes through ``safe_call`` like every other thing the
        registry shows, so a refusal that fails to render leaves a LOG
        witness (``log_error`` runs before any second dialog). What that
        does NOT buy is exception-freedom: ``safe_call``'s own error
        dialog uses the same backend, so if the dialog itself is what is
        broken the exception still propagates — as it does from every
        ``safe_call`` in the TUI (review R2).
        """
        profile = self._ctx.profile_label() or "?"
        if label is None:
            row = self.owner_row(section, tag)
            label = row[0] if row is not None else tag
        label = label.strip().split("  ")[0] or tag
        self._ctx.safe_call(
            f"explain gated {section}/{tag}", self._ctx.dialog.msgbox,
            f"{label} — not in this profile",
            f"This box is set to the '{profile}' deployment profile, which "
            f"does not include '{flag}'.\n\n"
            f"Nothing is broken and nothing has been removed — the profile "
            f"describes what this box is FOR, so tools outside it are shown "
            f"but not run.\n\n"
            f"To use it, change the profile in:\n"
            f"  Configuration > MeshForge Settings > Deployment Profile\n\n"
            f"Choosing a wider profile (for example 'full') enables "
            f"everything.")

    def dispatch(self, section: str, tag: str) -> bool:
        """Find and execute the handler for a given section + tag.

        A cross-section alias delegates to its owner, so the eleven menu
        loops need no per-tag fallback of their own — and the refusal a
        gated owner shows is titled with the label this screen rendered,
        because both come from the same ``owner_row`` (review R1).

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
            True if a handler was found — invoked, refused with an
            explanation because the active profile does not include it, or
            surfaced a load failure. False only when no handler owns the
            tag.
        """
        target = self._aliases.get(section, {}).get(tag)
        if target is not None:
            return self.dispatch(*target)

        handler = self._tag_index.get(section, {}).get(tag)
        if handler is None:
            return False

        # A row the profile marks off is SHOWN but not RUN. Intercepted
        # here, before materialisation, so all eleven menu loops inherit
        # it — they have at least three different dispatch shapes, and a
        # rule implemented per-loop is a rule that is missing from one of
        # them. Returns True because the tag IS owned; falling through
        # would reach the "not wired" tripwire and tell the operator a
        # wiring bug that does not exist.
        # owner_row logs the witness when the handler's live rows no
        # longer carry the tag (review R3) — the action still runs,
        # unflagged, and the log says why.
        row = self.owner_row(section, tag)
        flag = row[1] if row is not None else None
        if flag is not None and not self._ctx.feature_enabled(flag):
            logger.info("Refused %s/%s: '%s' is not in profile %r",
                        section, tag, flag, self._ctx.profile_label())
            self.explain_gated(section, tag, flag, label=row[0])
            return True

        if isinstance(handler, _LazyHandler):
            handler = self._materialize(handler, section)
            if handler is None:
                # Import/drift failure — the witness (dialog + log) is already
                # surfaced inside _materialize; do NOT dispatch on it.
                return True

        # The usage witness (2026-09-22). One INFO line per action RUN, so
        # the fleet's TUI logs can answer "was this feature used in the last
        # 30 days?" — the only honest input to "do we still need it". Until
        # now only REFUSALS were logged at INFO; a used action and a dead one
        # left the same record: none.
        logger.info("dispatch %s/%s", section, tag)
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
        """List of sections that have at least one handler.

        Every "for each section" loop must come through here rather than
        typing the list out: a hand-written section list is a closed
        consumer of an open registry (hfm #7), and the two sub-section
        menus built inside handlers — ``meshtasticd`` and ``rns`` — are
        precisely the ones such a list forgets.
        """
        names = list(self._sections.keys())
        for sec in self._aliases:
            if sec not in names:
                names.append(sec)
        return names

    def __repr__(self) -> str:
        return (
            f"HandlerRegistry(handlers={len(self._handlers)}, "
            f"sections={list(self._sections.keys())})"
        )
