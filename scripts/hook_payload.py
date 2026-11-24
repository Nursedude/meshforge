#!/usr/bin/env python3
"""ONE parser for the Claude Code hook payload (JSON on stdin).

Shared by every PostToolUse file hook here (scripts/lint_hook.py,
scripts/validate_claude_settings.py --hook) so they tri-state the same input
the same way (honest_failure_modes #5: two consumers of one artifact share
ONE reader). Pinned by tests/test_lint_hook_mode.py.

Path shape (r3, 2026-11-23): directories are resolved, the FINAL component
never is. git lists — and pre-commit lints — a symlinked file at its LINK
path; resolving it (r2 used realpath) judged the target's path instead and
the hook disagreed with the commit in both directions.
"""

import json
import os
from typing import Tuple


def read_stdin_text(stream) -> Tuple[str, str]:
    """Read the payload as strict UTF-8. Returns (text, error)."""
    data = stream.buffer.read() if hasattr(stream, "buffer") else stream.read()
    if isinstance(data, str):
        return data, ""
    try:
        return data.decode("utf-8"), ""
    except UnicodeDecodeError as e:
        return "", f"hook payload is not UTF-8 ({e})"


def payload_paths(stdin_text: str, default_base: str) -> Tuple[str, str, str]:
    """Return (physical path, lexical path, error).

    physical — directories resolved (symlinks, then ``..`` the way the kernel
               walks them), the FINAL component kept: what a lint must judge.
    lexical  — the path as named, only normalised: what a NAME match (the
               settings hook's ``/.claude/settings.json``) must test, since a
               symlinked ``.claude`` dir resolves to some other name (r3 I2).
    ("", "", "")    — a well-formed payload that names no file.
    ("", "", "msg") — a payload the hook cannot use: the caller must SAY so.
    A relative path resolves against the payload's ``cwd``, else
    ``default_base``. ``file_path`` wins; an empty/null one falls through to
    ``notebook_path``.
    """
    try:
        payload = json.loads(stdin_text)
    except (ValueError, RecursionError) as e:
        return "", "", f"unparseable hook payload ({type(e).__name__}: {str(e)[:120]})"
    if not isinstance(payload, dict):
        return "", "", f"hook payload is {type(payload).__name__}, not an object"
    tool_input = payload.get("tool_input")
    if tool_input is None:
        tool_input = {}
    if not isinstance(tool_input, dict):
        return "", "", "hook payload tool_input is not an object"
    raw = ""
    for key in ("file_path", "notebook_path"):
        value = tool_input.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            return "", "", f"hook payload {key} is {type(value).__name__}, not a string"
        raw = value
        break
    if not raw:
        return "", "", ""
    cwd = payload.get("cwd")
    base = cwd if isinstance(cwd, str) and cwd else default_base
    try:
        if "\x00" in raw or "\x00" in base:
            raise ValueError("embedded NUL")
        joined = raw if os.path.isabs(raw) else os.path.join(base, raw)
        joined = joined.rstrip("/") or "/"
        lexical = os.path.abspath(joined)
        head, tail = os.path.split(joined)
        physical = os.path.join(os.path.realpath(head or "."), tail)
        physical.encode("utf-8")  # lone surrogates cannot name a real file
    except (ValueError, UnicodeError, OSError) as e:
        return "", "", f"unusable path in hook payload ({type(e).__name__}: {e})"
    return physical, lexical, ""


def edited_path(stdin_text: str, default_base: str) -> Tuple[str, str]:
    """(physical path, error) — see payload_paths."""
    physical, _lexical, err = payload_paths(stdin_text, default_base)
    return physical, err
