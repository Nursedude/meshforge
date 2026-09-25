"""RNS interface config flags — pure, no RNS import (safe at TUI startup).

interface_enabled() mirrors RNS Reticulum.py: an interface is brought up when
``interface_enabled`` OR ``enabled`` is true (ConfigObj booleans, any case).
Lives here, not in commands.rns, because the TUI startup-lean guard forbids
loading RNS at handler import (2026-09-25).
"""

_TRUE_WORDS = ("yes", "true", "on", "1")


def interface_enabled(settings: dict) -> bool:
    """Is this interface brought up by RNS? UP when ``interface_enabled`` OR
    ``enabled`` is true, otherwise NOT brought up.

    MeshForge read only ``enabled``: an RNode configured the canonical way
    (``interface_enabled = True``) showed DISABLED in Interface Status while
    rnsd ran it; one counter even treated a missing key as enabled
    (live-truth pass 2026-09-25)."""
    return any(str(settings.get(k, "")).strip().lower() in _TRUE_WORDS
               for k in ("interface_enabled", "enabled"))
