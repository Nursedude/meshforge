"""Lazy package exports (PEP 562) — one factory for the copied __getattr__/__dir__.

The gateway / monitoring / commands packages each defer their heavy submodule
imports to first attribute access (perf arc 2026-07-13: eager package ``__init__``
imports pulled the RNS/LXMF/meshtastic/folium stacks into every consumer of any
submodule). They all used the SAME ``__getattr__`` / ``__dir__`` scaffold over a
per-package ``name -> submodule`` map; this collapses those copies into one
factory (honest_failure_modes #5 — one implementation, not several drifting
copies).

Usage in a package ``__init__``::

    from utils.lazy_exports import make_lazy_exports
    _LAZY_EXPORTS = {"RNSMeshtasticBridge": ".rns_bridge", "rns_bridge": ".rns_bridge", ...}
    __getattr__, __dir__, __all__ = make_lazy_exports(__name__, globals(), _LAZY_EXPORTS)

Stdlib only, and imported at package-init time — it MUST stay import-light (no
heavy transitive imports) or it would reintroduce the very startup cost the lazy
scaffolds exist to avoid.
"""
from importlib import import_module


def make_lazy_exports(pkg_name, pkg_globals, lazy_map):
    """Build ``(__getattr__, __dir__, __all__)`` for a package that resolves its
    exports lazily via PEP 562.

    ``lazy_map`` maps a public symbol to the RELATIVE submodule that defines it
    (e.g. ``{"RNSMeshtasticBridge": ".rns_bridge"}``). A symbol whose mapped
    value is exactly ``"." + symbol`` resolves to the submodule object itself, so
    ``pkg.rns_bridge`` keeps working as a package attribute (the old eager
    imports bound these). The resolved value is cached into ``pkg_globals`` so
    ``__getattr__`` fires only on the first miss — byte-for-byte the semantics of
    the hand-written scaffolds this replaces.

    Returns the three module-level objects to assign at the call site::

        __getattr__, __dir__, __all__ = make_lazy_exports(__name__, globals(), _LAZY_EXPORTS)
    """
    def __getattr__(name):
        submodule = lazy_map.get(name)
        if submodule is None:
            raise AttributeError(
                f"module {pkg_name!r} has no attribute {name!r}")
        module = import_module(submodule, pkg_name)
        value = module if submodule == "." + name else getattr(module, name)
        pkg_globals[name] = value  # cache: __getattr__ only fires on the first miss
        return value

    def __dir__():
        return sorted(set(pkg_globals) | set(lazy_map))

    return __getattr__, __dir__, list(lazy_map)
