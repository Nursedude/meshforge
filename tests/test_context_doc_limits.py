"""MF012 — per-turn context docs stay capped (context trim pass 2026-10-29).

Everything under ``.claude/rules/`` is loaded into EVERY model turn, so its
size is paid forever. The trim moved the stories to
``.claude/foundations/rules_evidence.md``; these tests keep the rules files
from regrowing and keep the numbered items code and tests cite.
"""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mf_lint", ROOT / "scripts" / "lint.py")
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


def test_every_rules_file_is_capped():
    """A NEW rules file is loaded every turn the moment it lands — it must
    carry a limit (closed enum, closed consumer: hfm #7)."""
    for p in sorted((ROOT / ".claude" / "rules").rglob("*.md")):
        rel = p.relative_to(ROOT).as_posix()
        assert rel in lint.CONTEXT_DOC_LIMITS, f"{rel} has no MF012 limit"


def test_an_uncapped_rules_file_fails_lint(tmp_path):
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "new_rule.md").write_text("# new\n")
    issues = lint.check_context_doc_sizes(str(tmp_path))
    assert any(i.code == "MF012" and "new_rule.md" in i.file for i in issues)


def test_a_rules_file_in_a_subdirectory_is_seen(tmp_path):
    sub = tmp_path / ".claude" / "rules" / "sub"
    sub.mkdir(parents=True)
    (sub / "x.md").write_text("# x\n")
    issues = lint.check_context_doc_sizes(str(tmp_path))
    assert any(i.file == ".claude/rules/sub/x.md" for i in issues)


def test_staged_mode_with_no_python_staged_still_runs_repo_checks(
        monkeypatch):
    """The pre-commit hook lints with --staged. A docs-only commit stages no
    .py, and main() used to return 0 ("No files to lint.") before ANY
    repo-level check — MF012 and the staged MF014/MF022 scans included — so
    a regrown rules file committed clean (non-author read 2026-10-29; the
    first fix was proven wrong by a live plant)."""
    planted = lint.LintIssue(".claude/rules/x.md", 0, lint.Severity.ERROR,
                             "MF012", "planted")
    monkeypatch.setattr(lint, "get_staged_files", lambda: [])
    monkeypatch.setattr(lint, "get_staged_files_all_types", lambda: [])
    monkeypatch.setattr(lint, "check_context_doc_sizes", lambda: [planted])
    monkeypatch.setattr(lint.sys, "argv", ["lint.py", "--staged"])
    assert lint.main() == 1


def test_a_missing_capped_file_is_not_a_silent_pass(tmp_path):
    """A listed doc that vanished (renamed) would escape its cap unseen."""
    issues = lint.check_context_doc_sizes(str(tmp_path))
    named = {i.file for i in issues if i.code == "MF012"}
    assert set(lint.CONTEXT_DOC_LIMITS) <= named


def test_the_live_tree_passes():
    assert lint.check_context_doc_sizes(str(ROOT)) == []


def test_honest_failure_modes_keeps_items_1_to_11():
    """`honest_failure_modes #N` is cited across src/ and tests/."""
    text = (ROOT / ".claude/rules/honest_failure_modes.md").read_text()
    nums = [int(n) for n in re.findall(r"^(\d+)\. \*\*", text, re.M)]
    assert nums == list(range(1, 12))


def test_calibrated_claims_keeps_rules_1_to_7():
    text = (ROOT / ".claude/rules/calibrated_claims.md").read_text()
    block = text.split("## The rules", 1)[1].split("\n## ", 1)[0]
    nums = [int(n) for n in re.findall(r"^(\d+)\. \*\*", block, re.M)]
    assert nums == list(range(1, 8))


def _at_includes(path, seen):
    """Every file reachable from ``path`` through ``@<path>`` lines."""
    if path in seen or not path.is_file():
        return seen
    seen.add(path)
    for m in re.finditer(r"^@(\S+)", path.read_text(), re.M):
        _at_includes(ROOT / m.group(1), seen)
    return seen


def test_the_evidence_file_is_not_loaded_per_turn():
    """It must not be reachable from CLAUDE.md's @-include graph, nor sit
    where Claude Code auto-loads (``.claude/rules/``)."""
    ev = (ROOT / ".claude/foundations/rules_evidence.md").resolve()
    assert ev.is_file()
    loaded = {p.resolve() for p in _at_includes(ROOT / "CLAUDE.md", set())}
    loaded |= {p.resolve() for p in (ROOT / ".claude/rules").rglob("*.md")}
    assert ev not in loaded
    assert ROOT / ".claude/rules/security.md" in {p for p in _at_includes(
        ROOT / "CLAUDE.md", set())}   # the walker does see real includes
