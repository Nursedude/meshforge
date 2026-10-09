"""RNS interface config flags — pure, no RNS import (safe at TUI startup).

interface_enabled() mirrors RNS Reticulum.py: an interface is brought up when
``interface_enabled`` OR ``enabled`` is true (ConfigObj booleans, any case).
Lives here, not in commands.rns, because the TUI startup-lean guard forbids
loading RNS at handler import (2026-09-25).
"""

import re

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


# Key matched CASE-SENSITIVELY (ConfigObj keys are; `Enabled` is not a key
# RNS reads), optionally quoted (`"enabled" = yes` is legal ConfigObj); the
# VALUE is matched case-insensitively, quoted or bare (review 2026-10-08).
_ENABLE_KEY = re.compile(
    r"^(?P<lead>\s*)(?P<q>[\"']?)(?P<key>(?:interface_)?enabled)(?P=q)(?P<eq>\s*=\s*)"
    r"(?P<val>\"[^\"]*\"|'[^']*'|[^\s#]+)(?P<rest>.*)$")


def set_section_enabled(content: str, name: str, enabled: bool):
    """Set ONE ``[[name]]`` section's enable state. Returns
    ``(new_content, found, changed)``. Pure.

    The section's own keys run to the next header of ANY depth (keys after a
    ``[[[sub]]]`` belong to the sub). Disable turns off EVERY true enable key in it,
    because RNS brings an interface up when ``interface_enabled`` OR
    ``enabled`` is true; enable sets the keys that are present to yes, or adds
    ``enabled = yes`` under the header when neither exists. Inline comments
    are kept. Born 2026-10-08 (non-author review): Repair's regex crossed into
    the NEXT section and disabled a working interface, and Enable/Disable
    Interface rewrote only ``enabled`` lines — no-op success on a canonical
    RNode."""
    lines = content.split("\n")
    head = None
    for i, ln in enumerate(lines):
        if ln.strip() == f"[[{name}]]":
            head = i
            break
    if head is None:
        return content, False, False
    # The section's OWN keys end at the first header of ANY depth: ConfigObj
    # gives every key after a [[[sub]]] to that sub, never back to the parent
    # (review 2026-10-08, VERIFIED against RNS's vendored ConfigObj — the first
    # cut counted sub keys as the parent's, so Enable re-enabled a sub-band the
    # operator had turned off, and read a sub's True as "parent already up").
    end = len(lines)
    for j in range(head + 1, len(lines)):
        if lines[j].lstrip().startswith("["):
            end = j
            break
    hits = []
    for j in range(head + 1, end):
        m = _ENABLE_KEY.match(lines[j])
        if m:
            hits.append((j, m, m.group("val").strip("\"'").strip().lower() in _TRUE_WORDS))
    if enabled and any(t for _, _, t in hits):
        return content, True, False                     # already up (RNS ORs)
    changed = False
    for j, m, is_true in hits:
        if enabled or is_true:
            word = "yes" if enabled else "no"
            q = m.group("q")
            lines[j] = f"{m.group('lead')}{q}{m.group('key')}{q}{m.group('eq')}{word}{m.group('rest')}"
            changed = True
    if enabled and not hits:
        indent = len(lines[head]) - len(lines[head].lstrip()) + 2
        lines.insert(head + 1, " " * indent + "enabled = yes")
        changed = True
    return "\n".join(lines), True, changed
