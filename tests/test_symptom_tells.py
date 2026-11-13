"""symptom_tells — persistent_issues moved to on-demand; the symptom pulls its row.

Operator 2026-11-13: "move persistent_issues to on-demand grep — what matters
is that things don't fall in the shadows." These tests are the anti-shadow
contract: every row declares how it is reached, the always-loaded NEVER list
survives in CLAUDE.md, and the hook is wired.
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import symptom_tells as st  # noqa: E402


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


TELLS = _read(st.TELLS_REL)


# ------------------------------------------------ the anti-shadow contract ---

def test_every_tell_row_declares_how_it_is_reached():
    """A row with neither literals nor `none` is the silent shadow: nothing
    pulls it and nobody listed it as grep-only. Refuse it (hfm #3)."""
    rows = st.parse_tells(TELLS)
    assert len(rows) >= 40, "parser found too few rows — it is not reading the table"
    undeclared = [sym[:70] for sym, _r, lits in rows if lits is None]
    assert undeclared == [], f"rows with no <!-- tell: ... --> marker: {undeclared}"


def test_declared_literals_are_specific_enough_not_to_cry_wolf():
    too_short = [(sym[:50], l) for sym, _r, lits in st.parse_tells(TELLS)
                 for l in (lits or []) if len(l) < 8]
    assert too_short == []


def test_no_marker_puts_a_pipe_inside_a_table_cell():
    """A literal `|` splits a markdown cell (the provenance structure leg's
    class) — literals are space-separated quoted strings."""
    for line in TELLS.splitlines():
        m = st._TELL_RE.search(line)
        if m:
            assert "|" not in m.group(1), line[:80]


def test_claude_md_no_longer_loads_the_table_every_turn_but_points_at_it():
    c = _read("CLAUDE.md")
    assert "@.claude/foundations/persistent_issues.md" not in c
    assert "grep -n -i" in c and "persistent_issues" in c


#: The before-you-act warnings: no symptom exists yet to pull them, so they
#: must stay loaded every turn. Pinned so a trim cannot drop one silently.
NEVER_ITEMS = ("rapid-cycle rnsd", "wire", "Do not roll meshtasticd",
               "EVERY env", "pip over apt")


@pytest.mark.parametrize("item", NEVER_ITEMS)
def test_the_NEVER_list_stays_always_loaded(item):
    assert item in _read("CLAUDE.md"), f"always-loaded NEVER item lost: {item!r}"


def test_the_hook_is_wired_on_bash():
    s = json.loads(_read(".claude/settings.json"))
    posts = s["hooks"]["PostToolUse"]
    bash = [h for p in posts if re.search(r"\bBash\b", p.get("matcher", ""))
            for h in p["hooks"]]
    assert any("symptom_tells.py" in h.get("command", "") for h in bash)


# ------------------------------------------------------------- the hook ----

ROWS = ("| Symptom | Class |\n|---|---|\n"
        "| `[Errno 24]` climbing fds | fd leak (#73) <!-- tell: \"[Errno 24]\" \"Too many open files\" --> |\n"
        "| a probe is blind on ONE box | shape <!-- tell: none --> |\n"
        "| undeclared row | x |\n\n---\n")


def test_parse_reads_literals_none_and_undeclared():
    rows = st.parse_tells(ROWS)
    assert [r[2] for r in rows] == [["[Errno 24]", "Too many open files"], [], None]
    assert "<!--" not in rows[0][1], "the marker never reaches the injected text"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "_state_dir", lambda: tmp_path)
    return tmp_path


def _env(out, cmd="journalctl -u meshforge-map", session="s1", tool="Bash"):
    return {"tool_name": tool, "session_id": session,
            "tool_input": {"command": cmd}, "tool_response": {"stdout": out, "stderr": ""}}


def test_a_matching_output_injects_its_row_and_leaves_a_witness(state):
    ctx = st.handle(_env("OSError: [Errno 24] Too many open files"), ROWS)
    assert ctx and "fd leak (#73)" in ctx and "[Errno 24]" in ctx
    w = [json.loads(l) for l in (state / "symptom_tells.jsonl").read_text().splitlines()]
    assert w[-1]["literal"] == "[Errno 24]"


def test_once_per_session_per_row(state):
    assert st.handle(_env("[Errno 24]"), ROWS)
    assert st.handle(_env("[Errno 24] again"), ROWS) is None
    assert st.handle(_env("[Errno 24]", session="s2"), ROWS), "a new session sees it again"


@pytest.mark.parametrize("env", [
    _env("all quiet"),                                   # no match
    _env("[Errno 24]", tool="Read"),                     # not Bash
    _env("[Errno 24]", cmd="grep -n Errno .claude/foundations/persistent_issues.md"),
    _env("[Errno 24]", cmd="python3 -m pytest tests/test_symptom_tells.py"),
    _env(""),
])
def test_no_injection(state, env):
    assert st.handle(env, ROWS) is None


def test_a_grep_only_row_never_fires(state):
    assert st.handle(_env("a probe is blind on ONE box"), ROWS) is None


def test_main_never_breaks_a_tool_call(monkeypatch, capsys, state):
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: "{not json"})())
    assert st.main([]) == 0
    assert capsys.readouterr().out == ""
    assert "error" in (state / "symptom_tells.jsonl").read_text()


def test_main_emits_the_hook_envelope(monkeypatch, capsys, state):
    monkeypatch.setattr(st, "handle", lambda env, text: "CTX")
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: "{}"})())
    assert st.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"] == {"hookEventName": "PostToolUse", "additionalContext": "CTX"}


def test_inventory_lists_the_shadow_and_fails_on_undeclared(capsys):
    assert st.inventory(ROWS) == 1
    out = capsys.readouterr().out
    assert "GREP-ONLY" in out and "a probe is blind on ONE box" in out and "UNDECLARED" in out
