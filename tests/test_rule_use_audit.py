"""rule_use_audit — the reader reads ASSISTANT words only, and says so when it can't.

2026-11-13: the first cut pre-filtered on '"type":"assistant"' within the first
200 bytes of each line; real lines carry it ~2.3 KB in, so it reported 53/53
rows uncited — a confident zero from a reader that never read. These pin it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import rule_use_audit as rua  # noqa: E402

PAD = "x" * 3000


def _line(role, text, sidechain=False):
    return json.dumps({"pad": PAD, "isSidechain": sidechain, "message": {
        "role": role, "content": [{"type": "text", "text": text},
                                  {"type": "tool_use", "input": {"command": f"{text} cmd"}}]}})


def test_reads_assistant_text_and_tool_input_even_deep_in_the_line():
    out = rua.assistant_text([_line("assistant", "saw #69")])
    assert "saw #69" in out and "saw #69 cmd" in out


def test_ignores_injected_context_and_sidechains():
    out = rua.assistant_text([_line("user", "#69 in CLAUDE.md"),
                              _line("assistant", "#73 by a subagent", sidechain=True),
                              "{not json"])
    assert out == ""


def test_signatures_include_issue_refs_and_tell_literals():
    rows = rua.pi_signatures(
        "| Symptom | Class |\n|---|---|\n"
        "| `[Errno 24]` fds | fd leak (#73) <!-- tell: \"Too many open files\" --> |\n")
    (_sym, sigs), = rows
    assert {"#73", "[Errno 24]", "Too many open files"} <= sigs
