"""Deterministic memory writer for the mini-dudeai second-brain loop.

This module is the WRITE/APPLY half of the second-brain loop. mini-dudeai's
read-back path is proven; this closes the gap by giving the deterministic
judgment layer a way to materialize a *properly formatted* canonical memory
file plus its MEMORY.md index pointer.

There is **no LLM in this code**. Everything here is pure, given an explicit
``memory_dir``. The live store is never hardcoded; callers pass the directory.

Canonical store shape (matched to the real store at
``~/.claude/projects/-opt-meshforge/memory/``):

    ---
    name: <kebab-case-slug>
    description: <one line>
    metadata:
      node_type: memory
      type: <user|feedback|project|reference>
      originSessionId: <session-id-or-null>
      origin: <mini|claude|operator>
      host: <hostname>
      confidence: <str-or-null>
      verified: <true|false>
      superseded_by: <name>   # only present after supersede()
    ---

    <markdown body>

The MEMORY.md index carries one pointer line per fact:

    - [Title](file.md) — hook text

Trust boundary
--------------
This is the trust boundary of the whole second-brain loop. Wrong-but-confident
writes are the failure mode being guarded against. Validation is conservative
and returns ``invalid`` (never raises) on bad input, writing NOTHING. A
mini-origin candidate is structurally barred from claiming ``verified=True`` —
mini grades its own confidence and is not a write authority (the red-team
flagged mini-origin auto-trust as a day-one provenance-rot backdoor).
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_MEM_TYPES = ("user", "feedback", "project", "reference")
ALLOWED_ORIGINS = ("mini", "claude", "operator")

#: mem_types that must carry the Why / How-to-apply convention in the body.
TYPES_REQUIRING_WHY_HOWTO = ("feedback", "project")

#: kebab-case slug: lowercase alphanumerics, single hyphen separators.
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Index filename used inside ``memory_dir``.
INDEX_FILENAME = "MEMORY.md"

#: Markers we scan the body for on feedback/project candidates. Matching is
#: case-insensitive and tolerant of the surrounding markdown emphasis the
#: store convention uses (e.g. ``**Why:**`` / ``**How to apply:**``).
_WHY_MARKER_RE = re.compile(r"why", re.IGNORECASE)
_HOWTO_MARKER_RE = re.compile(r"how\s+to\s+apply", re.IGNORECASE)

#: Banner prefix used by supersede(); also used to detect prior supersession.
SUPERSEDE_BANNER_PREFIX = "⚠️ SUPERSEDED"  # warning sign + SUPERSEDED
SUPERSEDE_HOOK_PREFIX = "[SUPERSEDED] "


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Provenance:
    """Where an authored fact came from and how much we trust it.

    ``origin`` is the write authority axis:
      * ``mini``     — mini-dudeai itself (deterministic agent). NOT a write
                       authority; structurally barred from ``verified=True``.
      * ``claude``   — a Claude session that reviewed the fact.
      * ``operator`` — the human operator.

    ``verified`` reflects whether a write authority has confirmed the fact.
    ``host`` + ``session_id`` let the deferred leaf-distribution phase dedup.
    """

    origin: str
    host: str
    session_id: Optional[str] = None
    confidence: Optional[str] = None
    verified: bool = False


@dataclass
class MemoryCandidate:
    """Structured input produced by the deterministic judgment layer."""

    name: str
    description: str
    mem_type: str
    body: str
    index_title: str
    index_hook: str
    provenance: Provenance
    links: List[str] = field(default_factory=list)


@dataclass
class ApplyResult:
    """Outcome of an apply / supersede operation.

    ``status`` is one of:
      * ``written``        — file written (and index updated if needed).
      * ``skipped_exists`` — file already present, no overwrite requested.
      * ``dry_run``        — nothing touched; ``content`` holds would-be text.
      * ``invalid``        — validation failed; nothing written.
    """

    status: str
    path: Optional[Path] = None
    index_updated: bool = False
    reason: str = ""
    content: Optional[str] = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_candidate(candidate: MemoryCandidate) -> Optional[str]:
    """Return a human-readable reason string if invalid, else ``None``.

    Conservative by design: any failure short-circuits with a clear reason so
    the apply layer can return ``invalid`` and write nothing.
    """
    name = candidate.name
    if not isinstance(name, str) or not _KEBAB_RE.match(name):
        return (
            f"name {name!r} is not kebab-case "
            "(expected ^[a-z0-9]+(-[a-z0-9]+)*$)"
        )

    if candidate.mem_type not in ALLOWED_MEM_TYPES:
        return f"mem_type {candidate.mem_type!r} not in {ALLOWED_MEM_TYPES}"

    desc = candidate.description
    if not isinstance(desc, str) or not desc.strip():
        return "description is empty"
    if "\n" in desc or "\r" in desc:
        return "description must be a single line (contains a newline)"

    if not isinstance(candidate.body, str) or not candidate.body.strip():
        return "body is empty"

    if candidate.mem_type in TYPES_REQUIRING_WHY_HOWTO:
        missing = []
        if not _WHY_MARKER_RE.search(candidate.body):
            missing.append("Why")
        if not _HOWTO_MARKER_RE.search(candidate.body):
            missing.append("How to apply")
        if missing:
            return (
                f"mem_type {candidate.mem_type!r} requires "
                f"{' and '.join(missing)} marker(s) in the body "
                "(CLAUDE.md convention)"
            )

    prov = candidate.provenance
    if prov is None:
        return "provenance is required"
    if prov.origin not in ALLOWED_ORIGINS:
        return f"provenance.origin {prov.origin!r} not in {ALLOWED_ORIGINS}"

    # Provenance gate: mini grades its own confidence and is not a write
    # authority. A mini-origin fact that claims verified=True is a provenance-
    # rot backdoor; bar it structurally.
    if prov.origin == "mini" and prov.verified:
        return (
            "provenance gate: mini-origin candidate cannot set verified=True "
            "(mini is not a write authority)"
        )

    if not isinstance(prov.host, str) or not prov.host.strip():
        return "provenance.host is empty"

    if not candidate.index_title or not str(candidate.index_title).strip():
        return "index_title is empty"
    if not candidate.index_hook or not str(candidate.index_hook).strip():
        return "index_hook is empty"

    return None


# ---------------------------------------------------------------------------
# Rendering (pure)
# ---------------------------------------------------------------------------


def _yaml_scalar(value: object) -> str:
    """Render a Python value as a minimal-but-safe YAML scalar.

    Strings are wrapped in double quotes (with internal quotes/backslashes
    escaped) so colons, leading symbols, and other YAML-special characters in
    descriptions never break the frontmatter. ``None`` becomes ``null`` and
    bools become lowercase ``true``/``false``.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_frontmatter(candidate: MemoryCandidate,
                        superseded_by: Optional[str] = None) -> str:
    """Render the YAML frontmatter block (including the delimiting ``---``)."""
    prov = candidate.provenance
    lines = ["---"]
    lines.append(f"name: {_yaml_scalar(candidate.name)}")
    lines.append(f"description: {_yaml_scalar(candidate.description)}")
    lines.append("metadata:")
    lines.append("  node_type: memory")
    lines.append(f"  type: {_yaml_scalar(candidate.mem_type)}")
    lines.append(f"  originSessionId: {_yaml_scalar(prov.session_id)}")
    lines.append(f"  origin: {_yaml_scalar(prov.origin)}")
    lines.append(f"  host: {_yaml_scalar(prov.host)}")
    lines.append(f"  confidence: {_yaml_scalar(prov.confidence)}")
    lines.append(f"  verified: {_yaml_scalar(prov.verified)}")
    if superseded_by:
        lines.append(f"  superseded_by: {_yaml_scalar(superseded_by)}")
    lines.append("---")
    return "\n".join(lines)


def _render_links_line(links: List[str], body: str) -> Optional[str]:
    """Return a ``Related:`` line for links not already cited in the body.

    Prefer that callers embed ``[[name]]`` links in the body directly. We only
    append the convenience line for links that are *not* already present, and
    only when at least one such link exists.
    """
    if not links:
        return None
    missing = [ln for ln in links if f"[[{ln}]]" not in body]
    if not missing:
        return None
    rendered = " ".join(f"[[{ln}]]" for ln in missing)
    return f"Related: {rendered}"


def render_memory_file(candidate: MemoryCandidate) -> str:
    """Return the full memory file text (frontmatter + body).

    Pure: used by both ``apply_memory_candidate`` and its dry-run path, and
    trivially unit-testable. Output always ends with a single trailing
    newline.
    """
    frontmatter = _render_frontmatter(candidate)
    body = candidate.body.rstrip("\n")
    parts = [frontmatter, "", body]

    links_line = _render_links_line(candidate.links, candidate.body)
    if links_line:
        parts.append("")
        parts.append(links_line)

    return "\n".join(parts) + "\n"


def render_index_line(candidate: MemoryCandidate) -> str:
    """Return the single MEMORY.md pointer line for this candidate.

    Shape: ``- [Title](file.md) — hook``  (em-dash separator).
    """
    filename = f"{candidate.name}.md"
    title = str(candidate.index_title).strip()
    hook = str(candidate.index_hook).strip()
    return f"- [{title}]({filename}) — {hook}"


# ---------------------------------------------------------------------------
# Atomic write helper (mirrors the state.py / candidate.py idiom)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (tmp file + ``os.replace``).

    The temp file is created in the destination directory so ``os.replace`` is
    a same-filesystem rename. On any failure the temp file is cleaned up.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Index maintenance
# ---------------------------------------------------------------------------


def _index_path(memory_dir: Path) -> Path:
    return Path(memory_dir) / INDEX_FILENAME


def _read_index(memory_dir: Path) -> str:
    index = _index_path(memory_dir)
    if not index.exists():
        return ""
    try:
        return index.read_text(encoding="utf-8")
    except OSError:
        return ""


def _index_has_entry(index_text: str, filename: str) -> bool:
    """True if the index already carries a pointer line for ``filename``.

    Match is on the markdown link target ``(filename)`` so we never duplicate
    a line for a memory that is already indexed (idempotency on the file).
    """
    needle = f"]({filename})"
    return needle in index_text


def _append_index_line(memory_dir: Path, line: str) -> None:
    """Append ``line`` to MEMORY.md, ensuring exactly one trailing newline."""
    existing = _read_index(memory_dir)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    new_text = existing + line.rstrip("\n") + "\n"
    _atomic_write(_index_path(memory_dir), new_text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_memory_candidate(
    candidate: MemoryCandidate,
    memory_dir: Path,
    *,
    dry_run: bool = False,
    allow_overwrite: bool = False,
) -> ApplyResult:
    """Materialize ``candidate`` into ``memory_dir`` (file + index line).

    Pure given an explicit ``memory_dir``; the live store is never assumed.

    Behavior:
      * Validate first. On failure → ``status='invalid'``, writes NOTHING.
      * Idempotency: if ``<name>.md`` exists and not ``allow_overwrite`` →
        ``status='skipped_exists'``, writes nothing (never blind-overwrite).
      * ``dry_run=True`` → compute everything, return ``status='dry_run'``
        with the would-be file content in ``result.content``; touch nothing.
      * Otherwise write the file atomically, then append the MEMORY.md line
        only if an entry for the file isn't already present.
    """
    memory_dir = Path(memory_dir)

    reason = validate_candidate(candidate)
    if reason is not None:
        return ApplyResult(status="invalid", reason=reason)

    target = memory_dir / f"{candidate.name}.md"
    content = render_memory_file(candidate)
    index_line = render_index_line(candidate)

    if dry_run:
        index_text = _read_index(memory_dir)
        would_update_index = not _index_has_entry(index_text, target.name)
        return ApplyResult(
            status="dry_run",
            path=target,
            index_updated=would_update_index,
            reason="dry_run: nothing written",
            content=content,
        )

    if target.exists() and not allow_overwrite:
        return ApplyResult(
            status="skipped_exists",
            path=target,
            index_updated=False,
            reason=f"{target.name} already exists (allow_overwrite=False)",
            content=content,
        )

    _atomic_write(target, content)

    index_updated = False
    index_text = _read_index(memory_dir)
    if not _index_has_entry(index_text, target.name):
        _append_index_line(memory_dir, index_line)
        index_updated = True

    return ApplyResult(
        status="written",
        path=target,
        index_updated=index_updated,
        reason="written",
        content=content,
    )


def _frontmatter_split(text: str) -> Tuple[Optional[List[str]], str]:
    """Split a memory file into (frontmatter_lines, body).

    Returns ``(None, text)`` if the file has no leading ``---`` frontmatter
    block. ``frontmatter_lines`` excludes the delimiting ``---`` lines.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            fm = lines[1:idx]
            body = "\n".join(lines[idx + 1:])
            return fm, body
    # Unterminated frontmatter — treat the whole thing as body.
    return None, text


def supersede_memory(
    name: str,
    memory_dir: Path,
    *,
    superseded_by: Optional[str] = None,
    note: str,
    date: Optional[str] = None,
) -> ApplyResult:
    """Mark an existing memory retracted/superseded. Never deletes the file.

    An add-only loop accumulates wrong facts; this is the retraction path the
    red-team required. We:
      * Prepend a clear ``⚠️ SUPERSEDED <date> — <note>`` banner into the body.
      * Add ``superseded_by: <name>`` into the frontmatter metadata when
        ``superseded_by`` is provided.
      * Keep the MEMORY.md line, prefixing its hook with ``[SUPERSEDED] ``.
      * Write atomically.

    ``date`` is caller-supplied (no clock dependency here); omitted if None.
    """
    memory_dir = Path(memory_dir)

    if not isinstance(name, str) or not _KEBAB_RE.match(name):
        return ApplyResult(
            status="invalid",
            reason=f"name {name!r} is not kebab-case",
        )
    if not note or not str(note).strip():
        return ApplyResult(status="invalid", reason="note is empty")

    target = memory_dir / f"{name}.md"
    if not target.exists():
        return ApplyResult(
            status="invalid",
            path=target,
            reason=f"{target.name} does not exist; nothing to supersede",
        )

    try:
        original = target.read_text(encoding="utf-8")
    except OSError as exc:
        return ApplyResult(
            status="invalid",
            path=target,
            reason=f"could not read {target.name}: {exc}",
        )

    fm_lines, body = _frontmatter_split(original)

    # Build the banner.
    date_part = f" {date}" if date else ""
    banner = f"{SUPERSEDE_BANNER_PREFIX}{date_part} — {note.strip()}"

    # Always prepend the newest banner (history of supersessions is allowed to
    # stack in the body; the frontmatter superseded_by is the single source of
    # the current pointer).
    new_body = banner + "\n\n" + body.lstrip("\n")

    if fm_lines is None:
        # No frontmatter to update; just rewrite the body. (Defensive — real
        # store files always carry frontmatter.)
        new_text = new_body.rstrip("\n") + "\n"
    else:
        new_fm = list(fm_lines)
        if superseded_by:
            sb_line = f"  superseded_by: {_yaml_scalar(superseded_by)}"
            # Replace an existing superseded_by under metadata if present.
            replaced = False
            for i, line in enumerate(new_fm):
                if line.strip().startswith("superseded_by:"):
                    new_fm[i] = sb_line
                    replaced = True
                    break
            if not replaced:
                # Insert after the last indented (metadata-child) line so the
                # key lands inside the metadata mapping; fall back to the end.
                insert_at = len(new_fm)
                for i in range(len(new_fm) - 1, -1, -1):
                    if new_fm[i].startswith("  "):
                        insert_at = i + 1
                        break
                new_fm.insert(insert_at, sb_line)
        new_text = (
            "---\n"
            + "\n".join(new_fm)
            + "\n---\n\n"
            + new_body.rstrip("\n")
            + "\n"
        )

    _atomic_write(target, new_text)

    # Prefix the index hook with [SUPERSEDED] (idempotently).
    index_updated = _supersede_index_line(memory_dir, target.name)

    return ApplyResult(
        status="written",
        path=target,
        index_updated=index_updated,
        reason="superseded",
        content=new_text,
    )


def _supersede_index_line(memory_dir: Path, filename: str) -> bool:
    """Prefix the MEMORY.md hook for ``filename`` with ``[SUPERSEDED] ``.

    Idempotent: if the hook already carries the prefix (or there is no line
    for the file), the index is left unchanged. Returns True if it changed.
    """
    index_text = _read_index(memory_dir)
    if not index_text or not _index_has_entry(index_text, filename):
        return False

    lines = index_text.split("\n")
    needle = f"]({filename})"
    em_dash = " — "
    changed = False
    for i, line in enumerate(lines):
        if needle not in line:
            continue
        if em_dash not in line:
            continue
        head, hook = line.split(em_dash, 1)
        if hook.startswith(SUPERSEDE_HOOK_PREFIX):
            # Already marked — idempotent.
            continue
        lines[i] = head + em_dash + SUPERSEDE_HOOK_PREFIX + hook
        changed = True

    if not changed:
        return False

    new_text = "\n".join(lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    _atomic_write(_index_path(memory_dir), new_text)
    return True


__all__ = [
    "ALLOWED_MEM_TYPES",
    "ALLOWED_ORIGINS",
    "Provenance",
    "MemoryCandidate",
    "ApplyResult",
    "validate_candidate",
    "render_memory_file",
    "render_index_line",
    "apply_memory_candidate",
    "supersede_memory",
]
