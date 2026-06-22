"""dude-AI Mesh Oracle — Phase 0 (read-only intent engine).

The oracle answers a text query about NOC state over the mesh, so "I" remain
reachable when the cloud session is away. Phase 0 is the deterministic core:
pure intent functions over a read-only ``NocSnapshot``, ≤237-byte calibrated
answers, NO mesh I/O and NO LLM. Arc: ``.claude/plans/dudeai_mesh_oracle_arc_2026_06_21.md``.

THE INVARIANT: the oracle is READ-ONLY (autonomy rung 1 — report, never act).
It only ever reads state and returns a string; it does not transmit, mutate,
or shell out. ``tests/test_oracle.py::test_oracle_module_is_read_only`` pins it.

Public API:
- ``answer(query, snap)``  — pure: query + snapshot → reply string (≤237 bytes).
- ``is_query(text)``       — pure: does this inbound text look like an oracle query?
- ``read_snapshot(...)``   — the only I/O: read-only state load → ``NocSnapshot``.
- ``NocSnapshot`` / ``NodeRec`` — the snapshot the intents operate on.
- ``MAX_REPLY_BYTES``      — reply cap (== gateway Meshtastic payload limit, test-pinned).
"""
from __future__ import annotations

from .intents import MAX_REPLY_BYTES, answer, is_query
from .responder import MeshOracleResponder
from .snapshot import (
    MINI_STALE_S,
    WATCHDOG_STALE_S,
    WATCHDOG_STATE_PATH,
    NocSnapshot,
    NodeRec,
    read_snapshot,
)

__all__ = [
    "answer",
    "is_query",
    "MeshOracleResponder",
    "read_snapshot",
    "NocSnapshot",
    "NodeRec",
    "MAX_REPLY_BYTES",
    "WATCHDOG_STATE_PATH",
    "WATCHDOG_STALE_S",
    "MINI_STALE_S",
]
