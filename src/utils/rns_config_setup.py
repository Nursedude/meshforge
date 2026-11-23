"""RNS /etc/reticulum/config setup helpers — rpc_key invariant.

Issue #41 pinned rpc_key into MeshForge's CLIENT configdirs
(/tmp/meshforge_rns_client/, gateway/node_tracker.py,
_map_collector_rns.py) so clients and rnsd share the same auth key
regardless of identity paths. That work relied on rnsd's own
/etc/reticulum/config already carrying a valid 64-hex rpc_key — which
is only true on boxes where someone manually added one.

This module closes the loop: every code path that writes (or touches)
/etc/reticulum/config must guarantee that file carries a unique
per-box rpc_key. Otherwise users who fresh-install MeshForge end up
with either no rpc_key at all (clients fail with AuthenticationError,
Issue #37) or — worse — silently copy someone else's published
example and break the "each box has its own auth" invariant.

Public surface:

    generate_rpc_key()              — fresh 64-hex lowercase
    render_template(text, key=None) — substitute __RPC_KEY__ in the
                                      MeshForge template, or insert
                                      a fresh line under [reticulum]
                                      if the placeholder is absent
    ensure_rpc_key(path)            — idempotent guard for an existing
                                      /etc/reticulum/config: if the
                                      rpc_key line is missing or not
                                      valid 64-hex, insert one. Never
                                      overwrites an existing valid key.
    pin_rpc_key_bytes(raw)          — the same, on bytes (no I/O): the
                                      ONE pinner every writer uses.

The write boundary (WITNESS-TUI r13, 2026-11-22): every pin checks its NEW
bytes with rnsd's own parser (``rns_config_reader.rnsd_view``) before it is
written — they must load, carry ``reticulum.rpc_key`` == the key reported,
and change nothing else rnsd reads — or it refuses (``RPCKeyWriteRefused``)
and writes nothing. The line edits below only decide WHERE to write; they
are not trusted to have got the grammar right.

All writers (install_noc.sh, handlers/rns_config.py, fleet_restore.sh)
should call one of these before putting a config on disk. The rpc_key
is NEVER read from / written to any MeshForge-managed source — it's
generated at install time, lives only in /etc/reticulum/config on the
owning box, and is read by the rnsd daemon on that box.
"""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path
from typing import Optional, Tuple

from utils.rns_config_reader import (
    _UTF8_BOM, RNSConfigError, config_text, keyword_name, rnsd_view,
    reticulum_section, section_header,
)

logger = logging.getLogger(__name__)

# The placeholder shipped in templates/reticulum.conf. Any caller that
# substitutes by hand should match this literal.
PLACEHOLDER = "__RPC_KEY__"

# rpc_key is 32 bytes → 64 hex chars, lowercase (RNS normalizes).
_VALID_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


def generate_rpc_key() -> str:
    """Return a fresh 64-hex lowercase RPC key (32 bytes of secure random).

    Uses ``secrets.token_hex`` for CSPRNG-backed randomness — the same
    source OpenSSL's ``rand -hex 32`` uses underneath. The returned
    string is always 64 lowercase hex characters.
    """
    return secrets.token_hex(32)


def is_valid_key(value: str) -> bool:
    """True iff ``value`` is exactly 64 lowercase-hex characters."""
    return bool(_VALID_KEY_RE.match(value))


def _lines(text: str) -> list:
    """rnsd's lines of ``text``: split on ``\\n`` only, each line keeping any
    ``\\r`` (the regexes above tolerate a trailing one). The final empty
    element of a newline-terminated text is dropped; callers re-add it."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


class RPCKeyWriteRefused(RNSConfigError):
    """A pinner refused to write: the new bytes would not give rnsd the key
    it reports, or would change something else rnsd reads. Nothing was
    written; the message says why in words."""


def _header_is_reticulum(line: str) -> bool:
    h = section_header(line)
    return h is not None and h == (1, "reticulum")


def _insert_key_line(text: str, rpc_key: str) -> str:
    """``text`` with ``  rpc_key = <key>`` added at the end of the
    ``[reticulum]`` section (or a new section appended). Locates by
    ConfigObj's own section grammar; ``_require_pinned`` decides whether it
    landed."""
    lines = _lines(text)
    cr = "\r" if lines and lines[0].endswith("\r") else ""
    out: list = []
    inserted = False
    in_reticulum = False
    section_start_idx = -1
    for i, line in enumerate(lines):
        out.append(line)
        if not inserted and _header_is_reticulum(line):
            in_reticulum = True
            section_start_idx = len(out) - 1
            continue
        if in_reticulum and section_header(line) is not None:
            # Next section starts — insert before it
            insert_pos = len(out) - 1
            # Walk back past trailing blank lines inside the section
            while (insert_pos - 1 > section_start_idx
                   and out[insert_pos - 1].strip() == ""):
                insert_pos -= 1
            out.insert(insert_pos, f"  rpc_key = {rpc_key}{cr}")
            inserted = True
            in_reticulum = False

    if in_reticulum and not inserted:
        # [reticulum] was the last section — append to end
        while out and out[-1].strip() == "":
            out.pop()
        out.append(f"  rpc_key = {rpc_key}{cr}")
        inserted = True

    if not inserted:
        # No [reticulum] section at all — append one.
        if out:
            out.append(cr)
        out.append(f"[reticulum]{cr}")
        out.append(f"  rpc_key = {rpc_key}{cr}")

    return "\n".join(out) + ("\n" if text.endswith("\n") or not text else "")


def _require_pinned(before: Optional[dict], new_content, rpc_key: str) -> None:
    """THE write boundary (r13): ``new_content`` must load as rnsd loads it,
    carry ``reticulum.rpc_key == rpc_key``, and — when ``before`` (the old
    file's rnsd view) is given — differ from it in nothing but that key.
    Raises ``RPCKeyWriteRefused`` otherwise; callers write only after this
    returns."""
    try:
        after = rnsd_view(new_content)
    except RNSConfigError as e:
        raise RPCKeyWriteRefused(
            f"the edited config would not load in rnsd ({e}) — nothing "
            f"written") from e
    got = after.get("reticulum")
    if not isinstance(got, dict) or got.get("rpc_key") != rpc_key:
        raise RPCKeyWriteRefused(
            "the edit would not leave rnsd reading the new rpc_key under "
            "[reticulum] — nothing written")
    if before is None:
        return
    expected = {k: (dict(v) if isinstance(v, dict) else v)
                for k, v in before.items()}
    expected.setdefault("reticulum", {})["rpc_key"] = rpc_key
    if after != expected:
        raise RPCKeyWriteRefused(
            "the edit would change more than rpc_key in what rnsd reads — "
            "nothing written")


def render_template(text: str, rpc_key: Optional[str] = None) -> str:
    """Return ``text`` with ``__RPC_KEY__`` substituted (or inserted).

    If ``rpc_key`` is ``None``, a fresh key is generated. If ``text``
    already contains the placeholder, it's substituted in place. If not,
    a ``rpc_key = <key>`` line is inserted at the end of the
    ``[reticulum]`` section.

    Raises ``ValueError`` if a non-None ``rpc_key`` argument is not a
    valid 64-hex string, and ``RPCKeyWriteRefused`` when the rendered text
    would not give rnsd that key — the caller writes nothing.
    """
    if rpc_key is None:
        rpc_key = generate_rpc_key()
    elif not is_valid_key(rpc_key):
        raise ValueError(
            f"rpc_key must be 64 lowercase hex chars, got: {rpc_key!r}"
        )
    if PLACEHOLDER in text:
        rendered = text.replace(PLACEHOLDER, rpc_key)
    else:
        rendered = _insert_key_line(text, rpc_key)
    _require_pinned(None, rendered, rpc_key)
    return rendered


def _strip_reticulum_key_lines(text: str) -> str:
    """Drop every live ``rpc_key = …`` line inside ``[reticulum]``."""
    keep = []
    section = None
    for line in _lines(text):
        h = section_header(line)
        if h is not None:
            section = h
        elif section == (1, "reticulum") and keyword_name(line) == "rpc_key":
            continue
        keep.append(line)
    return "\n".join(keep) + ("\n" if text.endswith("\n") else "")


def pin_rpc_key_bytes(
    raw: bytes, force: bool = False,
) -> Tuple[Optional[bytes], str, bool]:
    """The one rpc_key pinner, on bytes: ``(new_bytes | None, key, generated)``.

    ``new_bytes`` is None when nothing should be written (a valid key is
    already pinned — or an invalid one and ``force`` is False). The key is
    found the way rnsd finds it (``rnsd_view``: inline comments, quoted
    names, ``[ reticulum ]`` …), and every write passes ``_require_pinned``.

    Raises ``RNSConfigError`` (writing nothing) when the file is not UTF-8,
    when rnsd could not load it as it stands, or when the edit would not
    land exactly the key (``RPCKeyWriteRefused``)."""
    bom = _UTF8_BOM if raw.startswith(_UTF8_BOM) else b""
    try:
        text = config_text(raw, strict=True)
    except RNSConfigError as e:
        raise RPCKeyWriteRefused(
            f"the config is not UTF-8 text ({e}) — nothing written; re-save "
            f"it as UTF-8") from e
    try:
        before = rnsd_view(raw)
        current = reticulum_section(before).get("rpc_key")
    except RNSConfigError as e:
        raise RPCKeyWriteRefused(
            f"rnsd cannot load this config as it stands ({e}) — nothing "
            f"written; a key added to it would never be read") from e
    if isinstance(current, list):
        current = ", ".join(current)

    new_key = generate_rpc_key()
    if current == PLACEHOLDER:
        new_text = text.replace(PLACEHOLDER, new_key)
    elif current is not None and is_valid_key(current.lower()):
        return None, current.lower(), False
    elif current is not None and not force:
        logger.warning(
            "rpc_key is set but not valid 64-hex: %r "
            "(skipping; pass force=True to replace)", current)
        return None, current, False
    else:
        if current is not None:          # force over an invalid key
            text = _strip_reticulum_key_lines(text)
        new_text = _insert_key_line(text, new_key)
    new_raw = bom + new_text.encode("utf-8")
    _require_pinned(before, new_raw, new_key)
    return new_raw, new_key, True


def ensure_rpc_key(
    config_path: Path, force: bool = False,
) -> Tuple[str, bool]:
    """Make sure ``config_path`` carries a valid per-box rpc_key.

    Returns ``(rpc_key, was_generated)``. ``was_generated`` is True only
    after the file was written AND its new bytes were checked to give rnsd
    exactly that key (``pin_rpc_key_bytes``). False = nothing written: a
    valid key was already pinned (returned), or an invalid one is left for
    the operator to see unless ``force=True``.

    Raises OSError on read/write failure, and ``RNSConfigError`` (a
    ValueError; ``RPCKeyWriteRefused`` carries the why) — writing NOTHING —
    when the file is not UTF-8, rnsd could not load it, or the edit would
    not land the key. Callers must catch both and SAY the key was not pinned.
    """
    new_raw, key, generated = pin_rpc_key_bytes(
        config_path.read_bytes(), force=force)
    if new_raw is not None:
        config_path.write_bytes(new_raw)
        logger.info("Pinned fresh rpc_key in %s", config_path)
    return key, generated
