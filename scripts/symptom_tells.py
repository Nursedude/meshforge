#!/usr/bin/env python3
"""symptom_tells.py — the symptom pulls its row (PostToolUse hook on Bash).

WHY (operator 2026-11-13: "move persistent_issues to on-demand grep — what
matters is that things don't fall in the shadows"): the tell table was loaded
into EVERY turn (25 KB, ~6k tokens). Measured over 30 days / 105 sessions: 13
of 53 rows never cited. Presence in context is not the same as being reached:
a 45-row table loaded every turn still relies on the reader connecting the
error in front of it to the right row. This hook makes that connection
mechanically — when a Bash result CONTAINS a row's declared output literal,
that row is injected, once per session, at the moment it matters.

CONTRACT: every tell row in .claude/foundations/persistent_issues.md declares
``<!-- tell: "literal" "literal" -->`` (space-separated: a ``|`` would split
the table cell) or ``<!-- tell: none -->`` (a test
refuses a row that declares neither). ``none`` rows are reachable by grep only
— that list IS the shadow inventory, printed by ``--inventory``.

MEASURED: every injection appends one JSON line to
``~/.local/state/meshforge/symptom_tells.jsonl``. Fail-safe: any error →
silent exit 0 (a hook must never break a tool call), with the error witnessed.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TELLS_REL = ".claude/foundations/persistent_issues.md"
ARCHIVE_REL = ".claude/foundations/persistent_issues_archive.md"
_TELL_RE = re.compile(r"<!--\s*tell:\s*(.*?)\s*-->")
#: Commands whose output quotes the table itself (reading or testing it) —
#: matching those would inject rows nobody hit.
_SELF_REFERENCE = ("persistent_issues", "symptom_tells")


def parse_tells(text):
    """[(symptom, row_text, [literals] | [] for none | None if undeclared)]."""
    rows, in_table = [], False
    for line in (text or "").splitlines():
        if line.startswith("| Symptom"):
            in_table = True
            continue
        if in_table and not line.startswith("|"):
            if line.strip():
                in_table = False
            continue
        if not in_table or line.startswith("|---"):
            continue
        m = _TELL_RE.search(line)
        lits = None
        if m:
            body = m.group(1).strip()
            lits = [] if body == "none" else re.findall(r'"([^"]+)"', body)
        clean = _TELL_RE.sub("", line).strip()
        sym = clean.strip("|").split("|")[0].strip()
        rows.append((sym, clean, lits))
    return rows


def matches(rows, output):
    """[(symptom, row_text, first matching literal)] — one entry per row."""
    hits = []
    for sym, row, lits in rows:
        for lit in lits or ():
            if lit in output:
                hits.append((sym, row, lit))
                break
    return hits


def _state_dir():
    sys.path.insert(0, str(REPO / "src"))
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".local/state/meshforge"


def _seen_path(session):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session or "nosession")[:80]
    return _state_dir() / "symptom_tells_seen" / f"{safe}.json"


def _witness(rec):
    try:
        p = _state_dir() / "symptom_tells.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except (OSError, ValueError, ImportError):
        pass


def _output_text(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        return "\n".join(str(resp.get(k) or "") for k in ("stdout", "stderr", "output"))
    return ""


def handle(envelope, tells_text):
    """The additionalContext string to inject, or None."""
    if envelope.get("tool_name") != "Bash":
        return None
    cmd = str((envelope.get("tool_input") or {}).get("command") or "")
    if any(s in cmd for s in _SELF_REFERENCE):
        return None
    out = _output_text(envelope.get("tool_response"))
    if not out:
        return None
    hits = matches(parse_tells(tells_text), out)
    if not hits:
        return None
    session = str(envelope.get("session_id") or "")
    seen_p = _seen_path(session)
    try:
        seen = set(json.loads(seen_p.read_text()))
    except (OSError, ValueError):
        seen = set()
    fresh = [h for h in hits if h[0] not in seen]
    if not fresh:
        return None
    try:
        seen_p.parent.mkdir(parents=True, exist_ok=True)
        seen_p.write_text(json.dumps(sorted(seen | {h[0] for h in fresh})))
    except OSError:
        pass
    for sym, _row, lit in fresh:
        _witness({"ts": time.time(), "session": session, "literal": lit,
                  "symptom": sym[:120]})
    body = "\n".join(f"- matched `{lit}` →\n  {row}" for _sym, row, lit in fresh)
    return ("🔎 persistent_issues tell — this tool output matches a KNOWN symptom "
            f"({TELLS_REL}; story: grep -n '<literal>' {ARCHIVE_REL}):\n{body}")


def inventory(tells_text):
    rows = parse_tells(tells_text)
    reach = [r for r in rows if r[2]]
    grep_only = [r for r in rows if r[2] == []]
    undeclared = [r for r in rows if r[2] is None]
    print(f"{len(rows)} tell rows: {len(reach)} symptom-pulled · "
          f"{len(grep_only)} GREP-ONLY (shadow inventory) · {len(undeclared)} UNDECLARED")
    for sym, _r, _l in grep_only:
        print(f"  grep-only: {sym[:100]}")
    for sym, _r, _l in undeclared:
        print(f"  ⚠️ UNDECLARED: {sym[:100]}")
    return 1 if undeclared else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    tells_text = (REPO / TELLS_REL).read_text()
    if argv[:1] == ["--inventory"]:
        return inventory(tells_text)
    try:
        envelope = json.loads(sys.stdin.read() or "{}")
        ctx = handle(envelope, tells_text)
    except Exception as e:  # noqa: BLE001 — a hook must never break a tool call
        _witness({"ts": time.time(), "error": f"{type(e).__name__}: {e}"[:300]})
        return 0
    if ctx:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
