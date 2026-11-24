#!/usr/bin/env python3
"""PreToolUse Write|Edit|MultiEdit gate: the deferred ledger may only shrink.

Operator 2026-11-23 ("the product is???"): every session ended by queueing
more — 117 live rows in ~/deferred_work.json, 34 % of 30 d commits touching
src/. A queue is persistence WITHOUT a decision. This hook refuses any edit to
a file named ``deferred_work.json`` that would raise the count of LIVE rows
(status not in LIVE_EXCLUDED). To defer something new, close or drop one in
the same edit — a dated do-or-kill decision. No stored baseline: the
invariant itself ratchets down.

exit 2 + reason on stderr → Claude Code blocks the tool call and shows the
model why; exit 0 → allowed. exit 1 → the hook could not judge: NON-blocking,
and PreToolUse exit-1 stderr is NOT shown to the model (transcript only) — so
it is used only where blocking would be worse: a payload that names no
readable path, or a crash (this hook runs on EVERY Write/Edit). An
unjudgeable CURRENT ledger allows an edit whose result parses (that is the
repair) and refuses one whose result does not. A seatbelt, not a jail: a Bash
write bypasses it.
Pinned by tests/test_deferred_cap.py.
"""

import json
import os
import sys
from typing import Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from hook_payload import payload_paths, read_stdin_text  # noqa: E402

LEDGER_BASENAME = "deferred_work.json"
#: Terminal statuses = NOT live. Test-pinned equal to the ledger owner's
#: deferred_work_watch.TERMINAL_STATUSES and warmstart.DEFERRED_TERMINAL_
#: STATUSES (not imported: the watcher reads files at import and this hook
#: runs on every edit). roadmap.py's intake line imports live_count from here.
LIVE_EXCLUDED = frozenset({"done", "dropped"})


class LedgerShapeError(ValueError):
    """The ledger is not {"tasks": [ {..}, ... ]} — the count can't be judged."""


def live_count(data) -> int:
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise LedgerShapeError("ledger has no tasks list")
    if not all(isinstance(t, dict) for t in data["tasks"]):
        raise LedgerShapeError("a task is not an object")
    # A non-string status (list/dict/number) is unjudgeable as terminal →
    # counted LIVE; never an unhashable-type crash that lets the edit through.
    return sum(1 for t in data["tasks"]
               if not (isinstance(t.get("status"), str) and t["status"] in LIVE_EXCLUDED))


def _candidate(tool: str, tool_input: dict, current: str):
    """The file text after the tool runs, or None when the tool will fail
    on its own (an old_string that is absent)."""
    if tool == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None
    edits = tool_input.get("edits") if tool == "MultiEdit" else [tool_input]
    if not isinstance(edits, list):
        return None
    text = current
    if (tool == "Edit" and current == "" and isinstance(tool_input.get("old_string"), str)
            and tool_input["old_string"] == "" and isinstance(tool_input.get("new_string"), str)):
        return tool_input["new_string"]  # Edit with empty old_string CREATES the file
    for e in edits:
        if not isinstance(e, dict):
            return None
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or not old or old not in text:
            return None
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def hook_main(stdin_text: str) -> Tuple[int, str]:
    physical, lexical, err = payload_paths(stdin_text, os.getcwd())
    if err:
        return 1, f"deferred cap: {err}; edit NOT judged"
    if os.path.basename(lexical or "") != LEDGER_BASENAME and os.path.basename(physical or "") != LEDGER_BASENAME:
        return 0, ""
    payload = json.loads(stdin_text)
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    try:
        with open(physical, encoding="utf-8") as f:
            current = f.read()
        before = live_count(json.loads(current))
    except FileNotFoundError:
        current, before = "", 0
    except (OSError, ValueError, RecursionError) as e:  # unreadable / unparseable / shape
        try:
            with open(physical, encoding="utf-8", errors="replace") as f:
                current = f.read()
        except OSError:
            return 1, f"deferred cap: current ledger unreadable ({type(e).__name__}: {e}); edit NOT judged"
        before = None  # unjudgeable now: allow a repair that parses, refuse one that doesn't
    text = _candidate(tool, tool_input, current)
    if text is None:
        return 0, ""
    try:
        after = live_count(json.loads(text))
    except (ValueError, RecursionError) as e:
        return 2, (f"deferred cap: this edit leaves {LEDGER_BASENAME} unable to parse "
                   f"({type(e).__name__}: {e}) — deferred_work_watch would go blind. Refused.")
    if before is None:
        return 0, ""  # the edit repairs an unjudgeable ledger into a parseable one
    if after > before:
        return 2, (f"deferred cap: live deferred rows {before} → {after}. The ledger may only shrink "
                   f"(operator 2026-11-23). Close or drop one row in the same edit (status "
                   f"'done'/'dropped' + a dated note), or finish the work now instead of queueing it.")
    return 0, ""


def main() -> int:
    text, err = read_stdin_text(sys.stdin)
    if err:
        rc, msg = 1, f"deferred cap: {err}; edit NOT judged"
    else:
        try:
            rc, msg = hook_main(text)
        except Exception as e:  # never a bare traceback, never a silent pass
            rc, msg = 1, f"deferred cap: crashed ({type(e).__name__}: {e}); edit NOT judged"
    if msg:
        print(msg, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
