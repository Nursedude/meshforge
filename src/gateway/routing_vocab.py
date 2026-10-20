"""``default_route`` vocabulary — ONE source for the config validator and
both routing deciders (MeshAnchor #151; honest_failure_modes #5).

Its own module because gateway/config.py sits at the 1,500-line cap (MF025).
config.py re-exports these names, so `from gateway.config import ...` works.
"""
from typing import Optional


def _error(field_name, message, severity):
    from .config import ConfigValidationError
    return ConfigValidationError(field_name, message, severity=severity)


#: ``default_route`` vocabulary — ONE source for the validator and both
#: routing deciders (MeshAnchor #151; honest_failure_modes #5).
#:   open        a message no rule matches is bridged
#:   a direction that SOURCE network is open; others need a rule
#:   "allowlist" only messages an enabled rule matches are bridged
DEFAULT_ROUTE_OPEN = ("bidirectional", "all_to_all")
DEFAULT_ROUTE_SOURCE = {
    "mesh_to_rns": "meshtastic", "mesh_to_meshcore": "meshtastic",
    "rns_to_mesh": "rns", "rns_to_meshcore": "rns",
    "meshcore_to_mesh": "meshcore", "meshcore_to_rns": "meshcore",
}
DEFAULT_ROUTE_ALLOWLIST = "allowlist"


def validate_default_route(value, field_name: str) -> Optional["ConfigValidationError"]:
    """Warn on a value outside the vocabulary. It is READ as an allow-list
    (rules only), so a typo turns an open gateway closed — say so here,
    where the operator edits, not only in the journal."""
    if value in DEFAULT_ROUTE_OPEN or value in DEFAULT_ROUTE_SOURCE \
            or value == DEFAULT_ROUTE_ALLOWLIST:
        return None
    return _error(
        field_name,
        f"{value!r} is not a recognised default_route; it is read as "
        f"'{DEFAULT_ROUTE_ALLOWLIST}' (only messages an enabled rule matches "
        f"are bridged). Use one of: {', '.join(DEFAULT_ROUTE_OPEN)}, "
        f"{DEFAULT_ROUTE_ALLOWLIST}, or a direction "
        f"({', '.join(DEFAULT_ROUTE_SOURCE)})",
        "warning")
