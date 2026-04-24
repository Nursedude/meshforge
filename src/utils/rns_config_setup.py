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

logger = logging.getLogger(__name__)

# The placeholder shipped in templates/reticulum.conf. Any caller that
# substitutes by hand should match this literal.
PLACEHOLDER = "__RPC_KEY__"

# rpc_key is 32 bytes → 64 hex chars, lowercase (RNS normalizes).
_VALID_KEY_RE = re.compile(r"^[0-9a-f]{64}$")

# Matches a live (uncommented) rpc_key assignment line in a config.
# Accepts any indentation, any whitespace around '=', any 64-hex value.
_LIVE_KEY_RE = re.compile(
    r"^(?P<indent>\s*)rpc_key\s*=\s*(?P<value>\S+)\s*$"
)

# Matches the [reticulum] section header.
_RETICULUM_HEADER_RE = re.compile(r"^\s*\[reticulum\]\s*$")


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


def render_template(text: str, rpc_key: Optional[str] = None) -> str:
    """Return ``text`` with ``__RPC_KEY__`` substituted (or inserted).

    If ``rpc_key`` is ``None``, a fresh key is generated. If ``text``
    already contains the placeholder, it's substituted in place. If not,
    a ``rpc_key = <key>`` line is inserted at the end of the
    ``[reticulum]`` section — preserving the file's indentation
    convention (2-space).

    Raises ``ValueError`` if a non-None ``rpc_key`` argument is not a
    valid 64-hex string, so we never bake an invalid key into a
    fresh-install config.
    """
    if rpc_key is None:
        rpc_key = generate_rpc_key()
    elif not is_valid_key(rpc_key):
        raise ValueError(
            f"rpc_key must be 64 lowercase hex chars, got: {rpc_key!r}"
        )

    if PLACEHOLDER in text:
        return text.replace(PLACEHOLDER, rpc_key)

    # Placeholder absent — insert a fresh line under [reticulum].
    lines = text.splitlines(keepends=False)
    out: list = []
    inserted = False
    in_reticulum = False
    section_start_idx = -1
    for i, line in enumerate(lines):
        out.append(line)
        if _RETICULUM_HEADER_RE.match(line):
            in_reticulum = True
            section_start_idx = i
            continue
        if in_reticulum and line.strip().startswith('[') \
                and not _RETICULUM_HEADER_RE.match(line):
            # Next section starts — insert before it
            insert_pos = len(out) - 1
            # Walk back past trailing blank lines inside the section
            while (insert_pos - 1 > section_start_idx
                   and out[insert_pos - 1].strip() == ""):
                insert_pos -= 1
            out.insert(insert_pos, f"  rpc_key = {rpc_key}")
            inserted = True
            in_reticulum = False

    if in_reticulum and not inserted:
        # [reticulum] was the last section — append to end
        while out and out[-1].strip() == "":
            out.pop()
        out.append(f"  rpc_key = {rpc_key}")
        inserted = True

    if not inserted:
        # No [reticulum] section at all — append one. Caller probably
        # has a malformed template but we still produce a usable file.
        out.append("")
        out.append("[reticulum]")
        out.append(f"  rpc_key = {rpc_key}")

    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def ensure_rpc_key(
    config_path: Path, force: bool = False,
) -> Tuple[str, bool]:
    """Make sure ``config_path`` carries a valid per-box rpc_key.

    Returns ``(rpc_key, was_generated)``. When ``was_generated`` is
    ``True``, the helper inserted a freshly-minted key and wrote the
    file back to disk. When ``False``, the existing key was already
    valid and nothing was written.

    Behavior:

    - If the file has a live ``rpc_key = <64-hex>`` line, returns that
      key and touches nothing. Preserves existing fleet state.
    - If the file has the ``__RPC_KEY__`` placeholder, substitutes a
      fresh key (the install-time case).
    - If the file has NO rpc_key line at all, inserts one under
      ``[reticulum]`` (the upgrade case for pre-Issue-#41 boxes).
    - If the file has an INVALID rpc_key (not 64-hex), replaces it
      with a fresh one when ``force=True``; otherwise logs a warning
      and leaves the invalid value alone so the operator sees the
      mismatch during the Config Doctor pass.

    Raises OSError on read/write failure. Callers should catch and
    present a readable dialog when running under the TUI.
    """
    text = config_path.read_text()

    # Placeholder takes priority — always a fresh install scenario.
    if PLACEHOLDER in text:
        new_key = generate_rpc_key()
        new_text = text.replace(PLACEHOLDER, new_key)
        config_path.write_text(new_text)
        logger.info(
            "Substituted __RPC_KEY__ placeholder with fresh key in %s",
            config_path,
        )
        return new_key, True

    # Look for an existing live rpc_key line.
    invalid_line_to_strip = None
    for line in text.splitlines():
        m = _LIVE_KEY_RE.match(line)
        if not m:
            continue
        # Skip comments — `#` prefix means the line is inactive even
        # if the rest matches.
        if line.lstrip().startswith('#'):
            continue
        value = m.group('value').lower()
        if is_valid_key(value):
            return value, False
        if not force:
            logger.warning(
                "%s has an rpc_key line but value is not valid 64-hex: %r "
                "(skipping; pass force=True to replace)",
                config_path, value,
            )
            return value, False
        # force=True and invalid — remember the exact line to strip, then
        # fall through to replacement below.
        invalid_line_to_strip = line
        break

    # If force=True found an invalid line, drop it before re-rendering so
    # we don't land two rpc_key entries in the file.
    if invalid_line_to_strip is not None:
        text = "\n".join(
            line for line in text.splitlines()
            if line != invalid_line_to_strip
        ) + ("\n" if text.endswith("\n") else "")

    # Either no rpc_key line at all, or invalid-with-force (now stripped).
    new_key = generate_rpc_key()
    new_text = render_template(text, new_key)
    config_path.write_text(new_text)
    logger.info(
        "Inserted fresh rpc_key in %s (was missing/invalid)",
        config_path,
    )
    return new_key, True
