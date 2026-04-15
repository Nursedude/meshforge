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
import re
import sys
from pathlib import Path

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
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return [f"file not found: {path}"]
    except json.JSONDecodeError as e:
        return [f"invalid JSON in {path}: {e}"]

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


def main(argv: list[str]) -> int:
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
