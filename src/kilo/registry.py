"""Kilo node registry — WHICH nodes should exist, so silence is a signal.

The registry is the expectation side of the instrument: each lab node
declares its identity anchors (radio ids — NEVER IP addresses), its role,
the metrics it should emit, and how often. The ingest spine records what
was actually heard; ``kilo status`` joins the two and reports per node.

Honest-failure contract (the #80 class, applied at write time):
  * an unreadable/invalid registry loads as ``(None, errors)`` — never as
    an empty registry that would read "no nodes expected, all quiet is
    fine" (error mapped to a valid-looking value);
  * ``{"nodes": []}`` IS valid (the author meant empty) — absence of
    nodes and absence of a readable file are different facts;
  * IP-shaped identity anchors are refused loudly: DHCP reassigns them,
    and a registry keyed on yesterday's IP reports a healthy node dark
    (the fleet's 2026-06-24 moc5 lesson).

Registry file: ``~/.config/meshforge/kilo_nodes.json`` (operator values
live outside the repo, MF014); template: ``configs/kilo_nodes.example.json``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.paths import get_real_user_home

REGISTRY_BASENAME = "kilo_nodes.json"

# Closed role vocabulary — a typo'd role is an authoring error, not a new
# category (closed enums need closed consumers; grow deliberately).
ROLES = ("esp32-sensor", "nrf-meshtastic", "rnode", "claw", "gateway", "other")

# Identity-anchor kinds the ingest layer can currently OBSERVE. Anchors of
# other kinds (rns, claw, mac, ble) are legal in the registry — they mark
# planned adapters — but a node with no observable anchor is reported
# UNKNOWN by status, never OK and never DARK.
OBSERVABLE_ANCHORS = ("meshtastic",)

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?$")


def registry_path() -> Path:
    return get_real_user_home() / ".config" / "meshforge" / REGISTRY_BASENAME


@dataclass
class KiloNode:
    kilo_id: str
    role: str
    ids: Dict[str, str]
    expected_metrics: List[str] = field(default_factory=list)
    cadence_s: float = 900.0
    location: str = ""
    notes: str = ""

    def observable(self) -> bool:
        """True when at least one anchor kind has an ingest adapter today."""
        return any(k in self.ids for k in OBSERVABLE_ANCHORS)


def _validate_node(raw: dict, i: int, errors: List[str]) -> Optional[KiloNode]:
    kid = raw.get("kilo_id")
    if not kid or not isinstance(kid, str):
        errors.append(f"nodes[{i}]: missing kilo_id")
        return None
    where = f"nodes[{i}] ({kid})"
    role = raw.get("role")
    if role not in ROLES:
        errors.append(f"{where}: role {role!r} not in {list(ROLES)}")
        return None
    ids = raw.get("ids")
    if not isinstance(ids, dict) or not ids:
        errors.append(f"{where}: needs a non-empty 'ids' object "
                      f"(identity anchors, e.g. {{\"meshtastic\": \"!a1b2c3d4\"}})")
        return None
    for kind, val in ids.items():
        if not isinstance(val, str) or not val.strip():
            errors.append(f"{where}: ids[{kind!r}] must be a non-empty string")
            return None
        if _IPV4_RE.match(val.strip()):
            errors.append(
                f"{where}: ids[{kind!r}] = {val!r} looks like an IP address — "
                f"DHCP reassigns those and the node would read dark while "
                f"healthy; anchor on a radio identity instead")
            return None
    metrics = raw.get("expected_metrics", [])
    if not isinstance(metrics, list) \
            or not all(isinstance(m, str) and m for m in metrics):
        errors.append(f"{where}: expected_metrics must be a list of names")
        return None
    cadence = raw.get("cadence_s", 900.0)
    if not isinstance(cadence, (int, float)) or cadence <= 0:
        errors.append(f"{where}: cadence_s must be a positive number")
        return None
    return KiloNode(
        kilo_id=kid, role=role, ids={k: v.strip() for k, v in ids.items()},
        expected_metrics=list(metrics), cadence_s=float(cadence),
        location=str(raw.get("location", "")), notes=str(raw.get("notes", "")),
    )


def load_registry(path: Optional[str] = None
                  ) -> Tuple[Optional[List[KiloNode]], List[str]]:
    """(nodes, []) on success — including a legitimately empty registry;
    (None, errors) on unreadable/invalid. Never error→empty."""
    p = Path(path) if path else registry_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"registry not found: {p} — copy "
                      f"configs/kilo_nodes.example.json there and edit"]
    except (OSError, ValueError) as e:
        return None, [f"registry unreadable: {p}: {e}"]
    if not isinstance(data, dict) or "nodes" not in data:
        return None, [f"{p}: needs a top-level 'nodes' list"]
    if not isinstance(data["nodes"], list):
        # {"nodes": null} is the truncated-author trap — an ERROR, never
        # zero-nodes-valid (the {"rules": null} lesson, #80).
        return None, [f"{p}: 'nodes' must be a list "
                      f"(got {type(data['nodes']).__name__})"]
    errors: List[str] = []
    nodes: List[KiloNode] = []
    seen: set = set()
    for i, raw in enumerate(data["nodes"]):
        if not isinstance(raw, dict):
            errors.append(f"nodes[{i}]: not an object")
            continue
        node = _validate_node(raw, i, errors)
        if node is None:
            continue
        if node.kilo_id in seen:
            errors.append(f"nodes[{i}]: duplicate kilo_id {node.kilo_id!r}")
            continue
        seen.add(node.kilo_id)
        nodes.append(node)
    if errors:
        return None, errors
    return nodes, []


def anchor_map(nodes: List[KiloNode], kind: str = "meshtastic"
               ) -> Dict[str, str]:
    """{lowercased anchor value: kilo_id} for one anchor kind — the ingest
    join. Meshtastic ids compare case-insensitively ('!A1B2' == '!a1b2')."""
    out: Dict[str, str] = {}
    for n in nodes:
        val = n.ids.get(kind)
        if val:
            out[val.lower()] = n.kilo_id
    return out
