#!/usr/bin/env python3
"""PostToolUse Write|Edit|MultiEdit hook: lint the ONE file just edited.

Reads the hook payload (JSON on stdin; ``tool_input.file_path``), runs only
the per-file checks from ``lint.py``, and:

- exit 2 + findings on stderr when the file has ERROR-severity issues —
  Claude Code feeds PostToolUse exit-2 stderr back to the model, so the slip
  is fixed in the edit that made it, not at commit time;
- exit 0, silent, when clean / warnings-only / not a repo file;
- exit 1 + a message when the payload cannot be read (a witness — the edit
  went unlinted and someone should be able to see that).

Whole-tree checks (MF012/14/17/18/21/22/23/24/30 full scans) do NOT run here:
they cost ~6.7 s per edit and belong to pre-commit / ``lint.py --all`` / CI.

History (2026-11-23): the previous hook passed ``$CLAUDE_TOOL_ARG_file_path``
— not a variable Claude Code sets — so it linted nothing per edit, ran only
the whole-tree scans, and masked its exit with ``|| true``. Pinned by
tests/test_lint_hook_mode.py.
"""

import json
import os
import sys
from typing import Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)

import lint  # noqa: E402  (scripts/ is not a package)


def _edited_path(stdin_text: str) -> Tuple[str, str]:
    """Return (path, error). Empty path with no error = nothing to lint."""
    try:
        payload = json.loads(stdin_text)
    except ValueError as e:
        return "", f"unparseable hook payload ({e})"
    if not isinstance(payload, dict):
        return "", f"hook payload is {type(payload).__name__}, not an object"
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return "", "hook payload tool_input is not an object"
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return (path if isinstance(path, str) else ""), ""


def hook_main(stdin_text: str, repo_root: str = REPO_ROOT) -> Tuple[int, str]:
    path, err = _edited_path(stdin_text)
    if err:
        return 1, f"lint hook: {err}; the edited file was NOT linted"
    if not path:
        return 0, ""

    root = os.path.realpath(repo_root)
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    if not os.path.isfile(full) or os.path.commonpath([full, root]) != root:
        return 0, ""

    files = [full]
    try:
        issues = lint.MeshForgeLinter().lint_files(files)
        issues += lint.check_operator_values_in_files(files, repo_root=root)
        issues += lint.check_pip_invocations_in_files(files, repo_root=root)
        issues += lint.check_file_size_ratchet(files, repo_root=root)
        issues += lint.check_config_atomicity(files, repo_root=root)
        issues += lint.check_probe_fail_dark(files, repo_root=root)
        issues += lint.check_state_saver_swallow(files, repo_root=root)
    except Exception as e:  # a crashed check must be visible, never a silent pass
        return 1, f"lint hook: a check crashed ({type(e).__name__}: {e}); file NOT fully linted"

    errors = [i for i in issues if i.severity == lint.Severity.ERROR]
    if not errors:
        return 0, ""
    rel = os.path.relpath(full, root)
    lines = [f"lint (post-edit hook): {len(errors)} error(s) in {rel} — fix before moving on:"]
    lines += [str(i) for i in errors]
    lines.append("(full gate: python3 scripts/lint.py --all — pre-commit runs it)")
    return 2, "\n".join(lines)


def main() -> int:
    rc, msg = hook_main(sys.stdin.read())
    if msg:
        print(msg, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
