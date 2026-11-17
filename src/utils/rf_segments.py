"""The DECLARED RF segment of each fleet box — what /inbox's ``cannot_hear`` reads.

The fleet is two-preset by design (LONG_FAST/ch20 and SHORT_TURBO/ch8). A box
on the other preset cannot demodulate a send, so "not heard" there is not a
fault. Only a bridged copy, which carries a NEW packet id, can reach it.

Why DECLARED, not measured (2026-11-16):
- Linux meshtasticd serves no ``/json/report``: the map's ``/api/status``
  ``radio_config`` reads ``available: false`` on a Pi (measured; ESP32-only,
  upstream FR #9164).
- The journal names channels, not presets.
- Asking the radio is a PhoneAPI client, which force-closed the map twice
  (2026-11-11).
So the segment is an operator declaration, and the page says "declared".

File: ``~/.config/meshforge/rf_segments_declared.json`` (instance values; never
committed, see ``templates/rf_segments_declared.json.template``)::

    {"send_from": "<this box's alias>",
     "segments": {"<alias>": "meshtastic:LONG_FAST/ch20", ...}}

- Absent file: INERT (no box is ever ``cannot_hear``). That is correct for a
  one-preset fleet or a box with no fleet.
- A file that EXISTS but is unreadable or not what the author can have meant:
  LOUD (``error`` set), and NO segments are used. hfm #3: never absorbed into
  "nothing declared".
- A box missing from ``segments`` is undeclared, never guessed.

The preset names come from ONE table, ``meshtastic_modem.PRESET_DISPLAY_NAMES``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, NamedTuple, Optional

from utils.meshtastic_modem import PRESET_DISPLAY_NAMES
from utils.paths import get_real_user_home

CONFIG_BASENAME = "rf_segments_declared.json"
_SEGMENT_RE = re.compile(r"^meshtastic:([A-Z_]+)/ch([0-9]{1,3})$")
_KEYS = frozenset({"send_from", "segments"})


class Declared(NamedTuple):
    segments: Dict[str, str]
    send_from: Optional[str]
    error: Optional[str]

    @property
    def send_segment(self) -> Optional[str]:
        return self.segments.get(self.send_from) if self.send_from else None


INERT = Declared({}, None, None)


def config_path(home: Optional[Path] = None) -> Path:
    return (home or get_real_user_home()) / ".config" / "meshforge" / CONFIG_BASENAME


def parse_segment(value) -> Optional[str]:
    """The segment string when it names a preset the firmware knows, else None."""
    if not isinstance(value, str):
        return None
    m = _SEGMENT_RE.match(value)
    if not m or m.group(1) not in PRESET_DISPLAY_NAMES:
        return None
    return value


def parse(doc) -> Declared:
    """Validate a loaded document. Raises ValueError naming what is wrong."""
    if not isinstance(doc, dict):
        raise ValueError("the declaration must be a JSON object")
    unknown = sorted(k for k in doc if k not in _KEYS and not k.startswith("//"))
    if unknown:
        raise ValueError(f"unknown key(s) {unknown} (known: send_from, segments, //comments)")
    segs = doc.get("segments")
    if not isinstance(segs, dict):
        raise ValueError("segments must be an object of alias -> segment")
    if not segs:
        raise ValueError("no segments declared")
    out: Dict[str, str] = {}
    for alias, value in segs.items():
        if not isinstance(alias, str) or not alias:
            raise ValueError("every alias must be a non-empty string")
        seg = parse_segment(value)
        if seg is None:
            raise ValueError(f"{alias}: {value!r} is not meshtastic:<PRESET>/ch<N> "
                             f"with a preset the firmware knows")
        out[alias] = seg
    send_from = doc.get("send_from")
    if not isinstance(send_from, str) or send_from not in out:
        raise ValueError("send_from must name a box that is in segments "
                         "(this box's own alias — never inferred)")
    return Declared(out, send_from, None)


def load(path: Optional[Path] = None) -> Declared:
    """Read the declaration. Absent = INERT. Anything else that is not a good
    declaration = no segments plus a LOUD ``error``."""
    p = Path(path) if path else config_path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return INERT
    except OSError as e:
        return Declared({}, None, f"declared segments {p} unreadable ({type(e).__name__})")
    try:
        return parse(json.loads(text))
    except ValueError as e:   # JSONDecodeError is a ValueError
        reason = "unreadable JSON" if isinstance(e, json.JSONDecodeError) else str(e)
        return Declared({}, None, f"declared segments {p}: {reason}")
