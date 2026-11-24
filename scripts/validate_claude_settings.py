#!/usr/bin/env python3
"""Validate a Claude Code settings.json against the permission grammar.

Catches silent-skip bugs like the CVE-2026-21852 class of issue where a
malformed deny rule loads successfully but matches nothing. Claude Code
prints a warning at startup and continues; without this validator, the
gap can persist until someone runs /doctor.

Rules enforced:
  1. File must be valid JSON.
  2. permissions.{allow,deny,ask} must be arrays of strings.
  3. For any Bash(<pattern>) rule, `:*` may appear only at the end of
     <pattern> — anywhere else is invalid (use `*` for mid-pattern).
  4. Parens must be balanced in Bash(...) rules.

Exits 0 on success, 1 on validation failure. Intended for wiring into
PostToolUse hooks on .claude/settings.json edits and into pre-commit.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hook_payload import payload_paths, read_stdin_text  # noqa: E402

BASH_RULE_RE = re.compile(r"^Bash\((.+)\)$")


def validate_rule(rule: str) -> str | None:
    """Return error message if rule is invalid, else None."""
    if not isinstance(rule, str) or not rule:
        return f"empty or non-string rule: {rule!r}"

    m = BASH_RULE_RE.match(rule)
    if not m:
        # Non-Bash rules (Read, Write, Edit, WebFetch(...), etc.) — skip.
        # A full grammar check would cover these, but the failure mode this
        # validator exists to catch is specific to Bash pattern syntax.
        return None

    inner = m.group(1)
    if not inner:
        return f"empty Bash() pattern in {rule!r}"

    # :* is prefix-matching and must be terminal
    if ":*" in inner and not inner.endswith(":*"):
        return (
            f"{rule!r}: `:*` must be at the end of the pattern. "
            "Move it to the end, or use `*` for mid-pattern wildcard."
        )

    # Unbalanced parens inside the Bash pattern
    if inner.count("(") != inner.count(")"):
        return f"{rule!r}: unbalanced parentheses in Bash() pattern"

    return None


def validate_settings(path: Path) -> list[str]:
    """Return list of error messages. Empty list = valid."""
    errors: list[str] = []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"file not found: {path}"]
    except json.JSONDecodeError as e:
        return [f"invalid JSON in {path}: {e}"]
    except UnicodeDecodeError as e:
        return [f"not UTF-8: {path}: {e}"]
    except OSError as e:  # unreadable (mode 000, EIO): Claude Code can't load it either
        return [f"unreadable: {path}: {e}"]
    if not isinstance(data, dict):
        # Claude Code ignores a settings file whose top level is not an object.
        return [f"top level of {path} is {type(data).__name__}, not an object"]

    perms = data.get("permissions", {})
    if not isinstance(perms, dict):
        return [f"permissions must be an object, got {type(perms).__name__}"]

    for bucket in ("allow", "deny", "ask"):
        rules = perms.get(bucket, [])
        if not isinstance(rules, list):
            errors.append(f"permissions.{bucket} must be an array")
            continue
        for i, rule in enumerate(rules):
            err = validate_rule(rule)
            if err:
                errors.append(f"permissions.{bucket}[{i}]: {err}")

    return errors


SETTINGS_SUFFIXES = ("/.claude/settings.json", "/.claude/settings.local.json")


def hook_main(stdin_text: str) -> tuple[int, str]:
    """PostToolUse Write|Edit hook entry: validate only when the edited file
    is a ``.claude/settings.json`` or ``.claude/settings.local.json`` (by its
    LINK path when symlinked — the parser keeps the final component). Exit 2 + stderr on invalid (fed back to
    the model); 0 silent otherwise; 1 when the payload is unreadable.
    The old hook keyed on ``$CLAUDE_TOOL_ARG_file_path`` (not set by Claude
    Code) and so never fired — tests/test_lint_hook_mode.py. The payload is
    read by the ONE shared parser (scripts/hook_payload.py)."""
    base = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    path, lexical, err = payload_paths(stdin_text, base)
    if err:
        return 1, f"settings hook: {err}; settings NOT validated"
    # NAME match on the path as named OR as resolved: a dotfiles-symlinked
    # `.claude` dir resolves to another name (Fable r3 I2), a symlinked file
    # keeps its link name. No backslash rewriting — on Linux it is a filename char.
    if not path or not (lexical.endswith(SETTINGS_SUFFIXES) or path.endswith(SETTINGS_SUFFIXES)):
        return 0, ""
    p = Path(path)
    if not p.is_file():
        return 0, ""
    errors = validate_settings(p)
    if not errors:
        return 0, ""
    lines = [f"INVALID: {p} has {len(errors)} permission grammar error(s):"]
    lines += [f"  - {e}" for e in errors]
    lines.append("These rules would be silently skipped by Claude Code at startup.")
    return 2, "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv[1:] == ["--hook"]:
        text, err = read_stdin_text(sys.stdin)
        if err:
            rc, msg = 1, f"settings hook: {err}; settings NOT validated"
        else:
            try:
                rc, msg = hook_main(text)
            except Exception as e:  # last resort: never a bare traceback
                rc, msg = 1, f"settings hook: crashed ({type(e).__name__}: {e}); settings NOT validated"
        if msg:
            print(msg, file=sys.stderr)
        return rc
    if len(argv) != 2:
        print("usage: validate_claude_settings.py <path>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    errors = validate_settings(path)

    if errors:
        print(f"INVALID: {path} has {len(errors)} permission grammar error(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\nThese rules would be silently skipped by Claude Code at startup. "
            "Fix before relying on them.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {path} — permission grammar valid")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
