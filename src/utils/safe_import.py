"""
Safe Import Utility — Phase 1.1 Technical Debt Reduction

Consolidates the ~489 try/except ImportError blocks scattered across
the codebase into a single reusable helper.

Usage:
    from utils.safe_import import safe_import

    # Single attribute from a module
    emit_message, HAS_BUS = safe_import('utils.event_bus', 'emit_message')

    # Multiple attributes from one module
    check_service, ServiceState, HAS_CHECK = safe_import(
        'utils.service_check', 'check_service', 'ServiceState'
    )

    # Relative import (within a package)
    Handler, HAS_HANDLER = safe_import(
        '.mqtt_bridge_handler', 'MQTTBridgeHandler', package='gateway'
    )

    # Whole-module import (no attribute names)
    requests_mod, HAS_REQUESTS = safe_import('requests')

Return value:
    Always a tuple of (*values, available: bool).
    On success: (attr1, attr2, ..., True)
    On failure: (None, None, ..., False)
"""

import importlib
import logging
from typing import Any, Tuple

logger = logging.getLogger(__name__)

_MISSING = object()  # sentinel: distinguishes absent attrs from None-valued ones


def safe_import(module: str, *names: str, package: str = None) -> Tuple[Any, ...]:
    """Import a module and return requested attributes with a success flag.

    Args:
        module: Dotted module path (e.g. 'utils.event_bus') or relative
                path (e.g. '.mqtt_bridge_handler') when ``package`` is set.
        *names: Attribute names to extract from the module. If empty, the
                module object itself is returned.
        package: Required for relative imports. Pass ``__package__`` from the
                 calling module.

    Returns:
        Tuple of (*attrs, available_bool). When the import fails — module
        missing, OR any requested name neither an attribute nor an
        importable submodule (from-import semantics) — every attr is
        ``None`` and the flag is ``False``. The flag is never ``True``
        alongside a ``None`` for a missing name.

    Examples:
        >>> emit, ok = safe_import('utils.event_bus', 'emit_message')
        >>> if ok:
        ...     emit('rx', 'hello')

        >>> svc, state, ok = safe_import(
        ...     'utils.service_check', 'check_service', 'ServiceState')
    """
    try:
        mod = importlib.import_module(module, package=package)
    except ImportError:
        logger.debug("safe_import: module %r not available", module)
        if not names:
            return (None, False)
        return tuple([None] * len(names)) + (False,)

    if not names:
        return (mod, True)

    attrs = []
    missing = []
    for name in names:
        val = getattr(mod, name, _MISSING)
        if val is _MISSING:
            # from-import semantics: `from pkg import name` falls back to
            # importing pkg.name as a submodule when the package doesn't
            # expose it as an attribute (e.g. pubsub.pub, logging.handlers).
            # Without this, `safe_import('pubsub', 'pub')` returned
            # (None, True) on a cold interpreter — an honest-failure-modes
            # lie the flag exists to prevent.
            try:
                val = importlib.import_module(f"{module}.{name}",
                                              package=package)
            except ImportError:
                missing.append(name)
                val = None
        attrs.append(val)

    if missing:
        # A requested name the module cannot provide is a failed import,
        # not a healthy one with holes: every attr None, flag False, so
        # callers take their designed fallback instead of crashing on None.
        logger.debug("safe_import: %r imported but has no %s", module,
                     ", ".join(missing))
        return tuple([None] * len(names)) + (False,)

    return tuple(attrs) + (True,)


__all__ = ['safe_import']
