"""LXMF identity registry: a NAME and a PURPOSE for every hash we print.

Born 2026-10-07 (operator: "name their purpose"). The RNS→RF allowlist
(``rns_ingress_policy``) says WHO MAY bridge; this says WHO IT IS. They are
deliberately separate, so a surface can tell three things apart:

* named + listed      — a declared member;
* named, NOT listed   — a known stranger (e.g. a sibling app's identity);
* unnamed             — UNKNOWN, printed as ``unknown:<hash8>``.

Declared per box in ``~/.config/meshforge/lxmf_identities.json``::

    {"schema": "lxmf_identities/v1",
     "identities": {"<32 hex>": {"name": "...", "purpose": "..."}}}

``lab_peers`` (``name=hash``) is read as a FALLBACK, so the lab echoes are
named on every box that has one; a registry entry overrides it.

Honest failure (hfm #1/#3): an unreadable or wrong-shaped file is its own
state, ``unreadable`` — every label then SAYS the registry is unreadable
rather than reading "unknown", which would claim we looked and found no
name. A malformed entry is skipped AND named in ``problems``.
Nothing here ever raises into a caller; the registry only labels, it never
decides (the allowlist decides).
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

REGISTRY_FILENAME = "lxmf_identities.json"
LAB_PEERS_FILENAME = "lab_peers"
SCHEMA = "lxmf_identities/v1"

STATE_OK = "ok"
STATE_ABSENT = "absent"
STATE_UNREADABLE = "unreadable"

_HEX = set("0123456789abcdef")


def _norm(h: object) -> Optional[str]:
    if not isinstance(h, str):
        return None
    s = h.strip().lower()
    return s if len(s) == 32 and set(s) <= _HEX else None


def default_config_dir() -> Path:
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".config" / "meshforge"


@dataclass(frozen=True)
class Entry:
    name: str
    purpose: str
    source: str  # "registry" | "lab_peers"


@dataclass
class IdentityRegistry:
    state: str
    entries: Dict[str, Entry] = field(default_factory=dict)
    error: str = ""
    problems: List[str] = field(default_factory=list)

    def lookup(self, h: object) -> Optional[Entry]:
        s = _norm(h)
        return self.entries.get(s) if s else None

    def label(self, h: object) -> str:
        """Short form for counters/cells: the name, or ``unknown:<hash8>``."""
        e = self.lookup(h)
        if e:
            return e.name
        short = str(h or "")[:8].lower()
        if self.state == STATE_UNREADABLE:
            return f"unnamed:{short} (registry unreadable)"
        return f"unknown:{short}"

    def describe(self, h: object) -> str:
        """Long form for journal lines: ``name — purpose [hash8]``."""
        short = str(h or "")[:8].lower()
        e = self.lookup(h)
        if e:
            return f"{e.name} — {e.purpose or 'purpose not declared'} [{short}]"
        if self.state == STATE_UNREADABLE:
            return f"unnamed [{short}] (registry unreadable: {self.error})"
        return f"UNKNOWN identity [{short}]"

    def unnamed(self, hashes: Iterable[str]) -> List[str]:
        return [h for h in hashes if self.lookup(h) is None]


def _read_lab_peers(path: Path) -> Dict[str, Entry]:
    out: Dict[str, Entry] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, h = line.partition("=")
        s = _norm(h)
        if s and name.strip():
            out[s] = Entry(name=f"lab-echo ({name.strip()})",
                           purpose="lab echo responder — LXMF PING/ACK "
                                   "round-trip probe",
                           source="lab_peers")
    return out


def load_registry(config_dir: Optional[Path] = None) -> IdentityRegistry:
    """Read the registry (+ lab_peers fallback). Never raises."""
    try:
        d = Path(config_dir) if config_dir is not None else default_config_dir()
    except Exception as e:  # noqa: BLE001 — no home = cannot look
        return IdentityRegistry(state=STATE_UNREADABLE, error=f"no config dir: {e}")
    entries = _read_lab_peers(d / LAB_PEERS_FILENAME)
    path = d / REGISTRY_FILENAME
    if not path.exists():
        return IdentityRegistry(state=STATE_ABSENT, entries=entries)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        ids = doc.get("identities") if isinstance(doc, dict) else None
        if not isinstance(ids, dict):
            raise ValueError("'identities' must be an object of hash → entry")
    except (OSError, ValueError) as e:
        return IdentityRegistry(state=STATE_UNREADABLE, entries=entries,
                                error=f"{path.name}: {e}")
    problems: List[str] = []
    for k, v in ids.items():
        s = _norm(k)
        if not s:
            problems.append(f"{k!r}: not a 32-hex LXMF hash")
            continue
        if not isinstance(v, dict):
            problems.append(f"{s[:8]}: entry must be an object")
            continue
        name = v.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{s[:8]}: missing 'name'")
            continue
        purpose = v.get("purpose")
        entries[s] = Entry(name=name.strip(),
                           purpose=purpose.strip() if isinstance(purpose, str) else "",
                           source="registry")
    return IdentityRegistry(state=STATE_OK, entries=entries, problems=problems)


class RegistryCache:
    """Re-reads when either source file changes, at most every
    ``min_interval_s`` — an operator's edit lands without a restart."""

    def __init__(self, config_dir: Optional[Path] = None, *,
                 min_interval_s: float = 30.0, now_fn=time.monotonic):
        self._dir = config_dir
        self._min = min_interval_s
        self._now = now_fn
        self._lock = threading.Lock()
        self._reg: Optional[IdentityRegistry] = None
        self._sig: tuple = ()
        self._checked = float("-inf")

    def _signature(self) -> tuple:
        try:
            d = Path(self._dir) if self._dir is not None else default_config_dir()
        except Exception:  # noqa: BLE001
            return ()
        sig = []
        for name in (REGISTRY_FILENAME, LAB_PEERS_FILENAME):
            try:
                st = os.stat(d / name)
                sig.append((name, st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((name, None, None))
        return tuple(sig)

    def get(self) -> IdentityRegistry:
        with self._lock:
            now = self._now()
            if self._reg is not None and now - self._checked < self._min:
                return self._reg
            self._checked = now
            sig = self._signature()
            if self._reg is None or sig != self._sig:
                self._reg = load_registry(self._dir)
                self._sig = sig
            return self._reg
