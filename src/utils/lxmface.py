"""
LXMFace — deterministic avatar/identicon generator for LXMF/Reticulum addresses.

Same address always renders the same face, with no server, no image files, and
no external dependencies. Because the avatar is a pure function of the LXMF/RNS
*destination hash*, a node's face here is byte-identical to the face shown in
Ratspeak and other RNS-ecosystem clients that use LXMFace — giving MeshForge
operators a recognizable, ecosystem-interoperable visual identity per node.

This is a faithful Python port of ratspeak/LXMFace (`js/lxmface.js`), which is
itself "adapted from https://github.com/download13/blockies (MIT license)".
Parity is locked by the upstream shared test vectors copied into
`tests/fixtures/lxmface_vectors.json`; see `tests/test_lxmface.py`.

Attribution:
    LXMFace  — Copyright (c) Ratspeak contributors — MIT License
               https://github.com/ratspeak/LXMFace
    blockies — Copyright (c) 2015 download13 — MIT License
               https://github.com/download13/blockies
    See /opt/meshforge/ATTRIBUTION.md.

Fidelity note: the JS reference performs its PRNG in 32-bit *signed* integers
(`<<`, `^`, signed `>>`) with one unsigned step (`>>> 0`), and `rand()` returns
values in [0, 2) (it divides the unsigned 32-bit state by 2**31, not 2**32).
This module reproduces those exact semantics so output matches upstream
character-for-character. Do not "simplify" the integer math without re-running
the parity test — the vectors are the contract.

Usage:
    from utils.lxmface import (
        seed_string_for_node, avatar_svg, avatar_color, avatar_glyph,
    )

    seed = seed_string_for_node(node_id="!a2e95ba4", rns_hash_hex=None)
    svg = avatar_svg(seed)            # self-contained <svg> string
    color = avatar_color(seed)        # 'hsl(...)' primary color for tints
    glyph = avatar_glyph(seed)        # 2-char unicode token for text UIs
"""

import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

_MASK = 0xFFFFFFFF
_GRID_SIZE = 8

# Conservative palette of Geometric-Shapes-block symbols for text UIs (whiptail
# strips ANSI colour, so a glyph token carries the per-node distinction instead).
_GLYPH_PALETTE = "◐◑◒◓◔◕●○◆◇◈■□▣▤▥"


def _i32(x: int) -> int:
    """Coerce an integer to a signed 32-bit value (JS ToInt32 semantics)."""
    x &= _MASK
    return x - 0x100000000 if x & 0x80000000 else x


def _seed_rand(seed: str) -> Tuple[Callable[[], float], List[int]]:
    """Seed an xorshift128 PRNG from a string, JS-faithfully.

    Returns ``(rand, state)``. ``state`` is the post-seed 4-element register
    (full-precision, as JS stores it); apply :func:`_i32` to view it the way the
    upstream ``seeded_state`` test field records it. ``rand()`` returns a float
    in [0, 2) — note the upstream quirk: the unsigned 32-bit state is divided by
    2**31, not 2**32.
    """
    s = [0, 0, 0, 0]
    for i, ch in enumerate(seed):
        j = i % 4
        cur = s[j]
        # JS `(s[j] << 5)`: ToInt32(s[j]) then shift, result is int32.
        shifted = _i32((_i32(cur) << 5) & _MASK)
        # `- s[j] + charCode`: full-precision (the stored Number is NOT rewrapped
        # until its next bitwise use). Python ints stay exact well within 2**53.
        s[j] = shifted - cur + ord(ch)

    def rand() -> float:
        s0 = _i32(s[0])
        t = _i32(((s0 & _MASK) ^ ((s0 << 11) & _MASK)))
        s[0] = s[1]
        s[1] = s[2]
        s[2] = s[3]
        s3 = _i32(s[3])
        # signed arithmetic shifts (`>> 19`, `>> 8`) then `>>> 0` (unsigned).
        s[3] = (s3 ^ (s3 >> 19) ^ t ^ (t >> 8)) & _MASK
        return s[3] / 2147483648.0

    return rand, s


def _js_num(x: float) -> str:
    """Format a float the way JS ``Number.prototype.toString`` would.

    Integer-valued numbers print with no decimal point; everything else uses the
    shortest round-tripping decimal — which CPython's ``repr`` produces with the
    same digits as V8 for the non-exponential range these colour components live
    in (saturation ~[40,160), lightness ~[0,200)).
    """
    if x == math.floor(x) and abs(x) < 1e21:
        return str(int(x))
    return repr(x)


def _create_color(rand: Callable[[], float]) -> str:
    """One HSL colour string, consuming 6 PRNG draws (h, s, l1..l4)."""
    h = int(math.floor(rand() * 360))
    s = (rand() * 60) + 40
    l = (rand() + rand() + rand() + rand()) * 25
    return "hsl(" + str(h) + "," + _js_num(s) + "%," + _js_num(l) + "%)"


def _create_image_data(rand: Callable[[], float], grid_size: int = _GRID_SIZE) -> List[List[int]]:
    """8x8 (by default) grid, left half randomised then mirrored across columns."""
    w = grid_size
    h = grid_size
    half_w = math.ceil(w / 2)
    data: List[List[int]] = []
    for _y in range(h):
        row = [int(math.floor(rand() * 2.3)) for _x in range(half_w)]
        full_row = list(row)
        for x in range(math.floor(w / 2) - 1, -1, -1):
            full_row.append(row[x])
        data.append(full_row)
    return data


@dataclass
class AvatarData:
    """Structured avatar components (the inputs the SVG is assembled from)."""

    color: str
    bgcolor: str
    spotcolor: str
    grid: List[List[int]]
    seeded_state: List[int]


def avatar_data(seed: str) -> AvatarData:
    """Compute the deterministic colour palette + grid for a seed string."""
    rand, state = _seed_rand(seed or "")
    seeded_state = [_i32(v) for v in state]
    color = _create_color(rand)
    bgcolor = _create_color(rand)
    spotcolor = _create_color(rand)
    grid = _create_image_data(rand)
    return AvatarData(
        color=color,
        bgcolor=bgcolor,
        spotcolor=spotcolor,
        grid=grid,
        seeded_state=seeded_state,
    )


def avatar_svg(seed: str, size: int = 64) -> str:
    """Self-contained SVG identicon string, byte-identical to upstream LXMFace."""
    d = avatar_data(seed)
    grid_size = _GRID_SIZE
    rects: List[str] = []
    for y in range(grid_size):
        for x in range(grid_size):
            val = d.grid[y][x]
            fill = d.bgcolor if val == 0 else (d.color if val == 1 else d.spotcolor)
            rects.append(
                '<rect x="' + str(x) + '" y="' + str(y)
                + '" width="1" height="1" fill="' + fill + '"/>'
            )
    r = grid_size // 2
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="' + str(size)
        + '" height="' + str(size) + '" viewBox="0 0 ' + str(grid_size) + " "
        + str(grid_size) + '" shape-rendering="crispEdges">'
        + '<clipPath id="lxmface-clip"><circle cx="' + str(r) + '" cy="' + str(r)
        + '" r="' + str(r) + '"/></clipPath>'
        + '<g clip-path="url(#lxmface-clip)">' + "".join(rects) + "</g></svg>"
    )


def avatar_color(seed: str) -> str:
    """Primary HSL colour for a seed — for marker tints / compact surfaces."""
    return avatar_data(seed).color


def avatar_glyph(seed: str) -> str:
    """Deterministic 2-char unicode token for text UIs (TUI node lists).

    Whiptail/dialog strip ANSI colour from menu items, so the glyph carries the
    per-node distinction instead of a colour. Stable for a given seed.
    """
    rand, _state = _seed_rand(seed or "")
    n = len(_GLYPH_PALETTE)
    i1 = int(math.floor(rand() * n)) % n
    i2 = int(math.floor(rand() * n)) % n
    return _GLYPH_PALETTE[i1] + _GLYPH_PALETTE[i2]


def seed_string_for_node(node_id: str, rns_hash_hex: Optional[str] = None) -> str:
    """Canonical avatar seed string for a node — the cross-app contract.

    - RNS/LXMF node: pass ``rns_hash_hex`` (the 16-byte destination hash as
      lowercase hex). This matches the address string Ratspeak seeds from, so the
      face is identical across apps.
    - Meshtastic-only node: pass ``node_id`` (e.g. ``"!a2e95ba4"``); the leading
      ``!`` is stripped and it is lowercased. Deterministic within MeshForge only
      — Ratspeak has no such address namespace.

    The JS side (``web/js/lxmface.js`` ``seedForId``) applies the equivalent
    transform to ``props.id`` so the web map agrees with this module.
    """
    if rns_hash_hex:
        h = rns_hash_hex.strip().lower()
        if h.startswith("0x"):
            h = h[2:]
        return h
    nid = (node_id or "").strip()
    if nid.startswith("!"):
        nid = nid[1:]
    return nid.lower()
