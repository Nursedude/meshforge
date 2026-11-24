#!/usr/bin/env python3
"""PostToolUse Write|Edit|MultiEdit hook: lint the ONE file just edited.

Runs ``lint.per_file_issues`` — THE per-file set that ``lint.py --staged``
(pre-commit) runs — on the edited file, in pre-commit's shape (cwd = the
file's repo root, a repo-relative path). So the hook reports exactly what the
commit would refuse, at the edit that caused it:

- exit 2 + findings on stderr when the file has ERROR-severity issues —
  Claude Code feeds PostToolUse exit-2 stderr back to the model;
- exit 0, silent, when clean / warnings-only / not a repo file;
- exit 1 + a message when the payload cannot be read or a check crashes
  (a witness — the edit went unlinted and someone should see that).

Scope: the PER-FILE checks only. A whole-tree check's finding that happens to
land on the edited file (MF012 doc caps, MF021 mini-dudeai's fixed set) is
reported by pre-commit, not here — the price of a sub-second hook.

Whole-tree checks (``lint.WHOLE_TREE_CHECKS``) do NOT run here: ~6.7 s per
edit; they belong to pre-commit / ``lint.py --all`` / CI.

History (2026-11-23): r1 replaced a hook that read ``$CLAUDE_TOOL_ARG_file_path``
(never set by Claude Code) and so linted nothing for its whole life. The r1
non-author review (NOT CLEAN) found r1 kept its OWN per-file list (missed
MF018) and passed absolute paths ('/tests/' exemptions flipped → MF007
missed); r2 = the seam. Pinned by tests/test_lint_hook_mode.py.
"""

import os
import sys
from typing import Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)

from hook_payload import edited_path, read_stdin_text  # noqa: E402

try:  # a broken lint.py must be SAID on every edit, not a bare traceback (r3 N2)
    import lint  # noqa: E402  (scripts/ is not a package)
    _LINT_IMPORT_ERROR = ""
except Exception as e:  # SyntaxError, ImportError, anything lint.py raises at import
    lint = None
    _LINT_IMPORT_ERROR = f"{type(e).__name__}: {e}"


def _root_for(full: str, repo_root: str) -> Optional[str]:
    """The git root that owns ``full``: the nearest directory with a ``.git``
    entry between the file and ``repo_root`` (a nested worktree under
    .claude/worktrees/ is its own root), else ``repo_root``. None when the
    file is outside ``repo_root``."""
    root = os.path.realpath(repo_root)
    if os.path.commonpath([full, root]) != root:
        return None
    d = os.path.dirname(full)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        if d == root or d == os.path.dirname(d):
            return root
        d = os.path.dirname(d)


def hook_main(stdin_text: str, repo_root: str = REPO_ROOT) -> Tuple[int, str]:
    if lint is None:
        return 1, f"lint hook: scripts/lint.py failed to import ({_LINT_IMPORT_ERROR}); edits are NOT linted"
    full, err = edited_path(stdin_text, repo_root)
    if err:
        return 1, f"lint hook: {err}; the edited file was NOT linted"
    if not full or not os.path.isfile(full):  # isfile follows a symlinked final component
        return 0, ""
    root = _root_for(full, repo_root)
    if root is None:
        return 0, ""

    rel = os.path.relpath(full, root)
    prev = os.getcwd()
    try:
        os.chdir(root)  # pre-commit's shape: cwd = repo root, relative paths
        issues = lint.per_file_issues([rel], repo_root=root)
    except Exception as e:  # a crashed check must be visible, never a silent pass
        return 1, f"lint hook: a check crashed ({type(e).__name__}: {e}); {rel} NOT fully linted"
    finally:
        os.chdir(prev)

    errors = [i for i in issues if i.severity == lint.Severity.ERROR]
    if not errors:
        return 0, ""
    lines = [f"lint (post-edit hook): {len(errors)} error(s) in {rel} — fix before moving on:"]
    lines += [str(i) for i in errors]
    lines.append("(full gate: python3 scripts/lint.py --all — pre-commit runs it)")
    return 2, "\n".join(lines)


def main() -> int:
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="backslashreplace")  # a non-UTF-8 name never crashes the report
    text, err = read_stdin_text(sys.stdin)
    if err:
        rc, msg = 1, f"lint hook: {err}; the edited file was NOT linted"
    else:
        try:
            rc, msg = hook_main(text)
        except Exception as e:  # last resort: no shape may leave silently or as a bare traceback
            rc, msg = 1, f"lint hook: crashed ({type(e).__name__}: {e}); the edited file was NOT linted"
    if msg:
        print(msg, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
