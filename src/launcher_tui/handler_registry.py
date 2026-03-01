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

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from handler_protocol import CommandHandler, LifecycleHandler, TUIContext

logger = logging.getLogger(__name__)


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

        handler.set_context(self._ctx)
        self._handlers[hid] = handler
        self._sections[handler.menu_section].append(handler)

        # Build tag index for this handler
        for tag, _desc, _flag in handler.menu_items():
            if tag in self._tag_index[handler.menu_section]:
                logger.warning(
                    "Duplicate tag %r in section %r — overwriting with %s",
                    tag, handler.menu_section, hid,
                )
            self._tag_index[handler.menu_section][tag] = handler

        logger.debug(
            "Registered handler %s (section=%s, items=%d)",
            hid, handler.menu_section, len(handler.menu_items()),
        )

    def get_handler(self, handler_id: str) -> Optional[CommandHandler]:
        """Look up a handler by its unique ID.

        Returns:
            The handler, or None if not found.
        """
        return self._handlers.get(handler_id)

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
        consistent error handling.

        Args:
            section: Menu section key.
            tag: The action tag selected by the user.

        Returns:
            True if a handler was found and invoked, False otherwise.
        """
        handler = self._tag_index.get(section, {}).get(tag)
        if handler is None:
            return False

        self._ctx.safe_call(handler.handler_id, handler.execute, tag)
        return True

    def startup_all(self) -> None:
        """Call ``on_startup()`` on all handlers that implement LifecycleHandler."""
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
        """Call ``on_shutdown()`` on all handlers that implement LifecycleHandler."""
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
