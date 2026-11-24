"""Post-edit hook mode for scripts/lint.py and scripts/validate_claude_settings.py.

Why this exists (2026-11-23, /doctor): the PostToolUse Write|Edit hooks read
``$CLAUDE_TOOL_ARG_file_path`` — a variable Claude Code does not set (hook
input arrives as JSON on stdin; every other hook here reads ``tool_input``).
So the lint hook ran ``lint.py ""``: it linted NOTHING about the edited file,
paid ~6.7 s of whole-tree checks per edit, and exited 0 via ``|| true`` so even
a finding never reached the model. The settings validator's ``case`` never
matched. A gate that cannot fail.

Contract pinned here:
- the edited path comes from stdin ``tool_input.file_path``;
- only per-file checks run (no whole-tree scans — those belong to pre-commit/CI);
- an ERROR finding exits 2 with the finding on stderr (Claude Code feeds
  PostToolUse exit-2 stderr back to the model); warnings/clean exit 0 silent;
- files outside the repo are not linted (scratch scripts are not product code);
- a payload the hook cannot parse exits 1 with a message (a witness, not a
  silent 0).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("lint_hook", REPO_ROOT / "scripts" / "lint_hook.py")
lint_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint_hook)
lint = lint_hook  # hook_main lives here; the checks it calls are lint_hook.lint.*

import validate_claude_settings as vcs  # noqa: E402

# Split so this test file itself does not trip MF001 under `lint.py --staged`.
_HOME_CALL = "Path." + "home()"
BAD_PY = f"from pathlib import Path\nHOME = {_HOME_CALL}\n"
WARN_ONLY_PY = "import subprocess\n\ndef f():\n    subprocess.run(['ls'])\n"
CLEAN_PY = "def f(x):\n    return x + 1\n"


def _payload(path, tool="Edit") -> str:
    return json.dumps({"hook_event_name": "PostToolUse", "tool_name": tool,
                       "tool_input": {"file_path": str(path)}})


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    return root


class TestLintHookMode:
    def test_error_in_repo_file_exits_2_with_finding(self, tmp_path):
        root = _repo(tmp_path)
        f = root / "src" / "bad.py"
        f.write_text(BAD_PY)
        rc, msg = lint.hook_main(_payload(f), str(root))
        assert rc == 2
        assert "MF001" in msg and "bad.py" in msg

    def test_warning_only_is_silent_exit_0(self, tmp_path):
        root = _repo(tmp_path)
        f = root / "src" / "warn.py"
        f.write_text(WARN_ONLY_PY)
        assert lint.hook_main(_payload(f), str(root)) == (0, "")

    def test_clean_file_is_silent_exit_0(self, tmp_path):
        root = _repo(tmp_path)
        f = root / "src" / "ok.py"
        f.write_text(CLEAN_PY)
        assert lint.hook_main(_payload(f), str(root)) == (0, "")

    def test_non_python_repo_file_is_silent_exit_0(self, tmp_path):
        root = _repo(tmp_path)
        f = root / "NOTES.md"
        f.write_text(f"{_HOME_CALL} is mentioned in prose only.\n")
        assert lint.hook_main(_payload(f), str(root)) == (0, "")

    def test_file_outside_repo_is_not_linted(self, tmp_path):
        root = _repo(tmp_path)
        f = tmp_path / "scratch.py"
        f.write_text(BAD_PY)
        assert lint.hook_main(_payload(f), str(root)) == (0, "")

    def test_relative_path_is_resolved_against_repo_root(self, tmp_path):
        root = _repo(tmp_path)
        (root / "src" / "bad.py").write_text(BAD_PY)
        rc, msg = lint.hook_main(_payload("src/bad.py"), str(root))
        assert rc == 2 and "MF001" in msg

    @pytest.mark.parametrize("tool_input", [{}, {"file_path": ""},
                                            {"file_path": "/nonexistent/x.py"}])
    def test_no_lintable_file_exits_0(self, tmp_path, tool_input):
        root = _repo(tmp_path)
        payload = json.dumps({"tool_name": "Edit", "tool_input": tool_input})
        assert lint.hook_main(payload, str(root)) == (0, "")

    @pytest.mark.parametrize("raw", ["", "{not json", "[1, 2]"])
    def test_unparseable_payload_is_a_witnessed_exit_1(self, tmp_path, raw):
        rc, msg = lint.hook_main(raw, str(_repo(tmp_path)))
        assert rc == 1 and msg

    def test_whole_tree_checks_are_not_run(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("whole-tree check ran in hook mode")
        for name in ("check_operator_values_full_tree", "check_pip_invocations_full_tree",
                     "check_systemd_sandbox_paths", "check_version_consistency",
                     "check_roadmap_rules", "check_context_doc_sizes",
                     "check_mini_dudeai_observation_only",
                     "check_bounded_collect_chokepoint", "get_all_python_files",
                     "get_staged_files", "get_staged_files_all_types"):
            monkeypatch.setattr(lint_hook.lint, name, boom)
        root = _repo(tmp_path)
        f = root / "src" / "bad.py"
        f.write_text(BAD_PY)
        rc, _ = lint.hook_main(_payload(f), str(root))
        assert rc == 2


class TestSettingsValidatorHookMode:
    def test_invalid_settings_exits_2(self, tmp_path):
        p = tmp_path / ".claude" / "settings.json"
        p.parent.mkdir()
        p.write_text(json.dumps({"permissions": {"deny": ["Bash(curl:* | sh)"]}}))
        assert vcs.validate_settings(p), "fixture must be invalid per the validator"
        rc, msg = vcs.hook_main(_payload(p, "Write"))
        assert rc == 2 and "INVALID" in msg

    def test_valid_settings_exits_0_silent(self, tmp_path):
        p = tmp_path / ".claude" / "settings.json"
        p.parent.mkdir()
        p.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
        assert vcs.hook_main(_payload(p, "Write")) == (0, "")

    def test_other_file_is_ignored(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text("{not json")
        assert vcs.hook_main(_payload(p)) == (0, "")

    def test_unparseable_payload_is_a_witnessed_exit_1(self):
        rc, msg = vcs.hook_main("{not json")
        assert rc == 1 and msg


class TestHookWiring:
    """The live consumer: .claude/settings.json must call the stdin modes."""

    def _post_edit_commands(self):
        s = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
        return [h["command"] for grp in s["hooks"]["PostToolUse"]
                if "Edit" in grp.get("matcher", "") for h in grp["hooks"]]

    def test_no_hook_reads_the_nonexistent_tool_arg_env(self):
        text = (REPO_ROOT / ".claude" / "settings.json").read_text()
        assert "CLAUDE_TOOL_ARG_" not in text

    def test_lint_hook_uses_hook_mode_and_does_not_mask_exit(self):
        cmds = [c for c in self._post_edit_commands() if "lint" in c and "validate" not in c]
        assert len(cmds) == 1
        assert "scripts/lint_hook.py" in cmds[0] and "|| true" not in cmds[0]

    def test_settings_validator_hook_uses_hook_mode(self):
        cmds = [c for c in self._post_edit_commands() if "validate_claude_settings" in c]
        assert len(cmds) == 1 and "--hook" in cmds[0]
