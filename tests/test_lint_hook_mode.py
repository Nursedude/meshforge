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
import os
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


# ---------------------------------------------------------------------------
# Round 2 (2026-11-23 non-author review, NOT CLEAN): the hook and pre-commit
# each held their OWN idea of "lint this file" — the hook missed MF018 and
# handed the linter an absolute path where pre-commit hands a repo-relative
# one ('/tests/' substring exemptions disagreed → MF007 missed). THE SEAM:
# lint.per_file_issues(rel_paths, root) is the one per-file set; `--staged`
# and the hook both call it; every check_* is classified per-file or
# whole-tree (closed enum, honest_failure_modes #7).
# ---------------------------------------------------------------------------

import ast  # noqa: E402

import hook_payload  # noqa: E402

LINT = lint_hook.lint
RAW_TCP = "import meshtastic.tcp_interface\niface = meshtastic.tcp_interface." + "TCPInterface('localhost')\n"
EDITOR_SPAWN = "import subprocess\n\ndef edit(p):\n    subprocess.run(['nano', p], timeout=5)\n"  # MF018 scans src/launcher_tui/ only


def _write_rel(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _precommit_errors(root: Path, rel: str, monkeypatch):
    monkeypatch.chdir(root)
    return [i for i in LINT.per_file_issues([rel], repo_root=str(root))
            if i.severity == LINT.Severity.ERROR]


class TestSeamParity:
    @pytest.mark.parametrize("rel,text,code", [
        ("tests/conftest.py", RAW_TCP, "MF007"),                       # review #2
        ("src/launcher_tui/handlers/foo.py", EDITOR_SPAWN, "MF018"),   # review #1
        ("src/utils/bad.py", BAD_PY, "MF001"),
    ])
    def test_hook_reports_exactly_what_precommit_reports(self, tmp_path, monkeypatch,
                                                         rel, text, code):
        root = _repo(tmp_path)
        f = _write_rel(root, rel, text)
        pre = _precommit_errors(root, rel, monkeypatch)
        assert any(i.code == code for i in pre), f"fixture must trip {code} pre-commit-side"
        monkeypatch.chdir(tmp_path)  # the hook must not depend on the caller's cwd
        rc, msg = lint_hook.hook_main(_payload(f), str(root))
        assert rc == 2 and code in msg
        assert os.getcwd() == str(tmp_path), "hook_main must restore cwd"

    def test_hook_calls_the_seam_with_a_repo_relative_path(self, tmp_path, monkeypatch):
        root = _repo(tmp_path)
        f = _write_rel(root, "src/utils/ok.py", CLEAN_PY)
        seen = []
        monkeypatch.setattr(LINT, "per_file_issues",
                            lambda files, repo_root=None: seen.append((files, repo_root)) or [])
        assert lint_hook.hook_main(_payload(f), str(root)) == (0, "")
        assert seen == [(["src/utils/ok.py"], os.path.realpath(root))]

    def test_staged_mode_uses_the_seam(self, monkeypatch):
        seen = []
        monkeypatch.setattr(LINT, "get_staged_files_all_types", lambda: ["src/x.py", "docs/y.md"])
        monkeypatch.setattr(LINT, "get_staged_files", lambda: ["src/x.py"])
        monkeypatch.setattr(LINT, "per_file_issues",
                            lambda files, repo_root=None: seen.append(list(files)) or [])
        monkeypatch.setattr(sys, "argv", ["lint.py", "--staged"])
        monkeypatch.chdir(REPO_ROOT)
        try:
            LINT.main()
        except SystemExit:  # main() exits only when it found errors
            pass
        assert seen == [["src/x.py", "docs/y.md"]]

    def test_every_check_is_classified_per_file_or_whole_tree(self):
        tree = ast.parse((REPO_ROOT / "scripts" / "lint.py").read_text())
        defined = {n.name for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name.startswith("check_")}
        per_file = {f.__name__ for f in LINT.PER_FILE_CHECKS}
        whole = {f.__name__ for f in LINT.WHOLE_TREE_CHECKS}
        assert not per_file & whole
        assert defined == per_file | whole, (
            f"unclassified: {sorted(defined - per_file - whole)} — add each new check_* "
            "to PER_FILE_CHECKS (runs in --staged AND the post-edit hook) or WHOLE_TREE_CHECKS")

    def test_hook_runs_no_whole_tree_check(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("whole-tree check ran in hook mode")
        for f in LINT.WHOLE_TREE_CHECKS:       # by name AND in the dispatch tuple
            monkeypatch.setattr(LINT, f.__name__, boom)
        monkeypatch.setattr(LINT, "WHOLE_TREE_CHECKS", tuple(boom for _ in LINT.WHOLE_TREE_CHECKS))
        root = _repo(tmp_path)
        f = _write_rel(root, "src/utils/bad.py", BAD_PY)
        assert lint_hook.hook_main(_payload(f), str(root))[0] == 2


class TestNestedWorktreeRoot:
    def test_nested_worktree_file_is_judged_against_its_own_root(self, tmp_path, monkeypatch):
        root = _repo(tmp_path)
        wt = root / ".claude" / "worktrees" / "r1"
        (wt / "src").mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /elsewhere\n")
        f = _write_rel(wt, "src/utils/bad.py", BAD_PY)
        seen = []
        real = LINT.per_file_issues
        monkeypatch.setattr(LINT, "per_file_issues",
                            lambda files, repo_root=None: seen.append((files, repo_root)) or real(files, repo_root=repo_root))
        rc, msg = lint_hook.hook_main(_payload(f), str(root))
        assert seen == [(["src/utils/bad.py"], os.path.realpath(wt))]
        assert rc == 2 and "MF001" in msg


class TestSharedPayloadParser:
    def test_both_hooks_use_the_one_parser(self):
        assert lint_hook.edited_path is hook_payload.edited_path
        assert vcs.payload_paths is hook_payload.payload_paths
        # edited_path is a view of payload_paths — ONE implementation, two shapes
        src = inspect.getsource(hook_payload.edited_path)
        assert "payload_paths(" in src and "json.loads" not in src

    def test_relative_path_resolves_against_payload_cwd(self, tmp_path):
        p = tmp_path / ".claude" / "settings.json"
        p.parent.mkdir()
        p.write_text(json.dumps({"permissions": {"deny": ["Bash(curl:* | sh)"]}}))
        payload = json.dumps({"cwd": str(tmp_path),
                              "tool_input": {"file_path": ".claude/settings.json"}})
        rc, msg = vcs.hook_main(payload)
        assert rc == 2 and "INVALID" in msg

    @pytest.mark.parametrize("raw", ['"just a string"', "7", '{"tool_input": "x"}',
                                     '{"tool_input": {"file_path": {"a": 1}}}'])
    def test_malformed_shapes_are_witnessed_identically(self, raw):
        assert hook_payload.edited_path(raw, "/")[1], raw
        assert vcs.hook_main(raw)[0] == 1
        assert lint_hook.hook_main(raw)[0] == 1


# ---------------------------------------------------------------------------
# Round 3 (2026-11-23 Fable r2 BY RUNNING, NOT CLEAN). I1 = the SAME KIND as
# r1 #2 (hook vs pre-commit path shape): realpath() judged a symlinked FILE at
# its target; git lists — and pre-commit lints — the LINK. Seam: the parser
# resolves directories, never the final component. Plus: tuples are the
# dispatcher (M13–M27 survived a declaration-only tuple), payload/settings
# crash shapes witnessed, pre-commit's -z/ACMR staged list.
# ---------------------------------------------------------------------------

import inspect  # noqa: E402
import io  # noqa: E402
import subprocess  # noqa: E402

FLEET_HOST = "volcano" + "ai"  # an MF014 fleet hostname, split so this file stays clean


def _git_repo(tmp_path: Path) -> Path:
    root = _repo(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=30)
    return root


class TestSymlinkIsJudgedAtTheLinkPath:
    def test_in_repo_link_to_a_tests_file_is_linted_as_its_src_path(self, tmp_path, monkeypatch):
        root = _git_repo(tmp_path)
        _write_rel(root, "tests/test_target.py", RAW_TCP)        # MF007-exempt under tests/
        (root / "src" / "utils").mkdir(parents=True)
        link = root / "src" / "utils" / "link.py"
        link.symlink_to(root / "tests" / "test_target.py")
        pre = _precommit_errors(root, "src/utils/link.py", monkeypatch)
        assert any(i.code == "MF007" for i in pre)
        rc, msg = lint_hook.hook_main(_payload(link), str(root))
        assert rc == 2 and "MF007" in msg and "src/utils/link.py" in msg

    def test_in_repo_link_to_an_outside_file_is_linted(self, tmp_path):
        root = _git_repo(tmp_path)
        outside = tmp_path / "outside.py"
        outside.write_text(BAD_PY)
        (root / "src" / "utils").mkdir(parents=True)
        link = root / "src" / "utils" / "ext.py"
        link.symlink_to(outside)
        rc, msg = lint_hook.hook_main(_payload(link), str(root))
        assert rc == 2 and "MF001" in msg

    def test_parser_keeps_the_final_component(self, tmp_path):
        target = tmp_path / "t.py"
        target.write_text("")
        link = tmp_path / "l.py"
        link.symlink_to(target)
        assert hook_payload.edited_path(_payload(link), "/") == (str(link.resolve().parent / "l.py"), "")

    def test_symlinked_settings_file_is_validated(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"permissions": {"deny": ["Bash(curl:* | sh)"]}}))
        p = tmp_path / ".claude" / "settings.json"
        p.parent.mkdir()
        p.symlink_to(bad)
        rc, msg = vcs.hook_main(_payload(p, "Write"))
        assert rc == 2 and "INVALID" in msg


class TestTuplesAreTheDispatcher:
    def test_whole_tree_checks_take_no_required_args(self):
        for f in LINT.WHOLE_TREE_CHECKS:
            req = [p for p in inspect.signature(f).parameters.values()
                   if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            assert not req, f"{f.__name__} is in WHOLE_TREE_CHECKS but needs {req}"

    def test_per_file_checks_take_files_and_repo_root(self):
        for f in LINT.PER_FILE_CHECKS:
            params = list(inspect.signature(f).parameters)
            assert params[0] == "files" and "repo_root" in params, f.__name__

    @pytest.mark.parametrize("argv,expect_skipped", [
        (["lint.py", "--all"], set()),
        (["lint.py", "--staged"], {"check_version_consistency", "check_systemd_sandbox_paths",
                                    "check_operator_values_full_tree",
                                    "check_pip_invocations_full_tree",
                                    "check_in_domain_escapes_full_tree"}),
    ])
    def test_main_runs_every_declared_whole_tree_check(self, monkeypatch, argv, expect_skipped):
        called = []

        def rec(f):
            def w(*a, **k):
                called.append(f.__name__)
                return []
            w.__name__ = f.__name__
            return w
        wrap = {f: rec(f) for f in LINT.WHOLE_TREE_CHECKS}  # main() matches by identity
        monkeypatch.setattr(LINT, "WHOLE_TREE_CHECKS", tuple(wrap.values()))
        monkeypatch.setattr(LINT, "STAGED_SKIPS", frozenset(wrap[f] for f in LINT.STAGED_SKIPS))
        monkeypatch.setattr(LINT, "_WHOLE_TREE_TWIN",
                            {k: wrap[v] for k, v in LINT._WHOLE_TREE_TWIN.items()})
        monkeypatch.setattr(LINT, "get_all_python_files", lambda d: [])
        monkeypatch.setattr(LINT, "get_staged_files_all_types", lambda: [])
        monkeypatch.setattr(LINT, "get_staged_files", lambda: [])
        monkeypatch.setattr(sys, "argv", argv)
        monkeypatch.chdir(REPO_ROOT)
        try:
            LINT.main()
        except SystemExit:
            pass
        declared = {f.__name__ for f in LINT.WHOLE_TREE_CHECKS}
        assert set(called) == declared - expect_skipped

    def test_staged_and_all_are_mutually_exclusive(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["lint.py", "--staged", "--all"])
        with pytest.raises(SystemExit) as e:
            LINT.main()
        assert e.value.code == 2


class TestHookWitnessesEveryFailureShape:
    def test_a_crashed_check_exits_1_with_a_message(self, tmp_path, monkeypatch):
        root = _repo(tmp_path)
        f = _write_rel(root, "src/utils/ok.py", CLEAN_PY)

        def boom(*a, **k):
            raise RuntimeError("synthetic")
        monkeypatch.setattr(LINT, "per_file_issues", boom)
        rc, msg = lint_hook.hook_main(_payload(f), str(root))
        assert rc == 1 and "crashed" in msg

    @pytest.mark.parametrize("raw", [
        '{"tool_input": {"file_path": 0}}', '{"tool_input": {"file_path": []}}',
        '{"tool_input": {"file_path": false}}', '{"tool_input": {"file_path": {}}}',
        '{"tool_input": {"file_path": "/a\\u0000b.py"}}',
        '{"cwd": "/x\\u0000", "tool_input": {"file_path": "a.py"}}',
        '{"tool_input": {"file_path": "/a/\\ud800.py"}}',
        "[" * 100_000,
    ])
    def test_malformed_payloads_are_witnessed_not_raised(self, raw):
        for hook in (lint_hook.hook_main, vcs.hook_main):
            rc, msg = hook(raw)
            assert rc == 1 and msg, (hook.__module__, raw[:40])

    def test_non_utf8_stdin_is_witnessed(self, monkeypatch, capsys):
        for mod in (lint_hook, vcs):
            monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff\xfe{")))
            rc = mod.main() if mod is lint_hook else mod.main(["x", "--hook"])
            assert rc == 1, mod.__name__
            assert "NOT" in capsys.readouterr().err

    def test_md_and_yml_reach_mf014_through_the_seam(self, tmp_path):
        root = _repo(tmp_path)
        f = _write_rel(root, "config.yml", f"host: {FLEET_HOST}\n")
        rc, msg = lint_hook.hook_main(_payload(f), str(root))
        assert rc == 2 and "MF014" in msg

    def test_notebook_path_is_read(self, tmp_path):
        nb = tmp_path / "n.ipynb"
        nb.write_text("{}")
        raw = json.dumps({"tool_input": {"notebook_path": str(nb)}})
        assert hook_payload.edited_path(raw, "/")[0] == str(nb.resolve())


class TestSettingsHookShapes:
    @pytest.mark.parametrize("body", [b"[1]", b"null", b'"x"', b"\xff\xfe"])
    def test_unloadable_settings_exit_2_not_crash(self, tmp_path, body):
        p = tmp_path / ".claude" / "settings.json"
        p.parent.mkdir()
        p.write_bytes(body)
        rc, msg = vcs.hook_main(_payload(p, "Write"))
        assert rc == 2 and "INVALID" in msg

    def test_local_settings_are_validated(self, tmp_path):
        p = tmp_path / ".claude" / "settings.local.json"
        p.parent.mkdir()
        p.write_text(json.dumps({"permissions": {"deny": ["Bash(curl:* | sh)"]}}))
        assert vcs.hook_main(_payload(p, "Write"))[0] == 2

    def test_lookalike_suffix_is_ignored(self, tmp_path):
        p = tmp_path / "x.claude" / "settings.json"
        p.parent.mkdir()
        p.write_text("{not json")
        assert vcs.hook_main(_payload(p, "Write")) == (0, "")


class TestStagedListSeesRenamesAndQuotedNames:
    def test_renamed_modified_and_quoted_names_are_listed(self, tmp_path, monkeypatch):
        root = _git_repo(tmp_path)
        run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, timeout=30,  # noqa: E731
                                        capture_output=True)
        run("config", "user.email", "t@example.invalid")
        run("config", "user.name", "t")
        _write_rel(root, "src/a.py", CLEAN_PY)
        run("add", "-A")
        run("commit", "-q", "-m", "base")
        run("mv", "src/a.py", "src/b.py")
        _write_rel(root, "src/b.py", BAD_PY)
        _write_rel(root, "src/é tab\t.py", BAD_PY)
        run("add", "-A")
        monkeypatch.chdir(root)
        got = set(LINT.get_staged_files_all_types())
        assert {"src/b.py", "src/é tab\t.py"} <= got
        assert {"src/b.py", "src/é tab\t.py"} <= set(LINT.get_staged_files())


# ---------------------------------------------------------------------------
# Round 4 (2026-11-23 Fable r3 BY RUNNING, NOT CLEAN): two regressions r3's
# own fixes introduced — (I1) `-z` + text=True decoded a non-UTF-8 staged name,
# `except: return []` turned the crash into "nothing staged" → pre-commit
# linted NOTHING, exit 0; (I2) resolving directories made a symlinked `.claude`
# dir miss the settings suffix → silent skip. Plus the r3 survivors pinned.
# ---------------------------------------------------------------------------


def _git(root: Path, *a, **kw):
    return subprocess.run(["git", "-C", str(root), *a], check=True, timeout=30,
                          capture_output=True, **kw)


def _committed_repo(tmp_path: Path) -> Path:
    root = _git_repo(tmp_path)
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    return root


class TestStagedListNeverGoesSilentlyEmpty:
    def test_non_utf8_name_does_not_blind_the_rest(self, tmp_path, monkeypatch):
        root = _committed_repo(tmp_path)
        _write_rel(root, "src/utils/zk.py", BAD_PY)
        bad_name = os.fsencode(str(root / "src" / "utils")) + b"/zk\xe9.py"
        with open(bad_name, "w") as fh:
            fh.write(CLEAN_PY)
        _git(root, "add", "-A")
        monkeypatch.chdir(root)
        got = LINT.get_staged_files_all_types()
        assert "src/utils/zk.py" in got and len(got) == 2
        assert LINT.get_staged_files() and "src/utils/zk.py" in LINT.get_staged_files()

    def test_git_failure_is_loud_not_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # not a git repo → git diff fails
        with pytest.raises(LINT.StagedListError):
            LINT.get_staged_files_all_types()
        with pytest.raises(LINT.StagedListError):
            LINT.get_staged_files()

    def test_main_staged_exits_nonzero_and_says_why_on_stdout(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["lint.py", "--staged"])
        with pytest.raises(SystemExit) as e:
            LINT.main()
        assert e.value.code not in (0, None)
        assert "staged" in capsys.readouterr().out.lower()   # pre-commit drops stderr

    def test_a_real_rename_is_status_R_and_is_listed(self, tmp_path, monkeypatch):
        root = _committed_repo(tmp_path)
        body = "".join(f"x{i} = {i}\n" for i in range(60))
        _write_rel(root, "src/a.py", body)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "base")
        _git(root, "mv", "src/a.py", "src/b.py")
        _write_rel(root, "src/b.py", body + BAD_PY)
        _git(root, "add", "-A")
        status = _git(root, "diff", "--cached", "--name-status", text=True).stdout
        assert status.startswith("R"), f"fixture must be a real rename: {status!r}"
        monkeypatch.chdir(root)
        assert "src/b.py" in LINT.get_staged_files_all_types()

    def test_typechange_is_listed(self, tmp_path, monkeypatch):
        root = _committed_repo(tmp_path)
        _write_rel(root, "src/t.py", CLEAN_PY)
        (root / "src" / "l.py").symlink_to("t.py")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "base")
        (root / "src" / "l.py").unlink()
        _write_rel(root, "src/l.py", BAD_PY)
        _git(root, "add", "-A")
        status = _git(root, "diff", "--cached", "--name-status", text=True).stdout
        assert status.startswith("T"), status
        monkeypatch.chdir(root)
        assert "src/l.py" in LINT.get_staged_files_all_types()


class TestSettingsHookThroughADirSymlink:
    def test_symlinked_dot_claude_dir_is_still_validated(self, tmp_path):
        real = tmp_path / "dotfiles" / "claude"
        real.mkdir(parents=True)
        (real / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Bash(curl:* | sh)"]}}))
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").symlink_to(real)
        rc, msg = vcs.hook_main(_payload(home / ".claude" / "settings.json", "Write"))
        assert rc == 2 and "INVALID" in msg

    def test_unreadable_settings_is_invalid_not_crash(self, tmp_path):
        p = tmp_path / ".claude" / "settings.json"
        p.parent.mkdir()
        p.write_text("{}")
        p.chmod(0)
        try:
            if os.access(p, os.R_OK):
                pytest.skip("running as a user that can read mode-000 files")
            rc, msg = vcs.hook_main(_payload(p, "Write"))
            assert rc == 2 and "INVALID" in msg
        finally:
            p.chmod(0o600)

    def test_project_dir_env_is_the_base_without_payload_cwd(self, tmp_path, monkeypatch):
        p = tmp_path / ".claude" / "settings.json"
        p.parent.mkdir()
        p.write_text(json.dumps({"permissions": {"deny": ["Bash(curl:* | sh)"]}}))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.chdir("/")
        raw = json.dumps({"tool_input": {"file_path": ".claude/settings.json"}})
        assert vcs.hook_main(raw)[0] == 2


class TestParserPhysicalPaths:
    def test_directories_are_resolved(self, tmp_path):
        (tmp_path / "real").mkdir()
        (tmp_path / "real" / "f.py").write_text("")
        (tmp_path / "dl").symlink_to(tmp_path / "real")
        got, err = hook_payload.edited_path(_payload(tmp_path / "dl" / "f.py"), "/")
        assert not err and got == str((tmp_path / "real").resolve() / "f.py")

    def test_dotdot_after_a_dir_symlink_is_physical(self, tmp_path):
        root = tmp_path / "r"
        (root / "tests").mkdir(parents=True)
        (root / "utils").mkdir()
        (root / "utils" / "z.py").write_text("")
        (root / "src").mkdir()
        (root / "src" / "zld").symlink_to(root / "tests")
        got, _ = hook_payload.edited_path(_payload(str(root / "src" / "zld" / ".." / "utils" / "z.py")), "/")
        assert got == str(root.resolve() / "utils" / "z.py")  # what the kernel opens

    @pytest.mark.parametrize("fp", ["", None])
    def test_empty_file_path_falls_through_to_notebook_path(self, tmp_path, fp):
        nb = tmp_path / "n.ipynb"
        nb.write_text("{}")
        raw = json.dumps({"tool_input": {"file_path": fp, "notebook_path": str(nb)}})
        assert hook_payload.edited_path(raw, "/")[0] == str(nb.resolve())

    def test_strict_utf8_even_when_replacement_would_parse(self, monkeypatch, capsys):
        raw = b'{"tool_input": {"file_path": "/tmp/\xff.py"}}'
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
        assert lint_hook.main() == 1
        assert "UTF-8" in capsys.readouterr().err


class TestHookRootAndLastResort:
    def test_repo_root_given_through_a_symlink(self, tmp_path):
        root = _repo(tmp_path)
        f = _write_rel(root, "src/utils/bad.py", BAD_PY)
        via = tmp_path / "via"
        via.symlink_to(root)
        rc, msg = lint_hook.hook_main(_payload(f), str(via))
        assert rc == 2 and "MF001" in msg

    @pytest.mark.parametrize("mod", ["lint_hook", "vcs"])
    def test_last_resort_guard_is_a_witnessed_exit_1(self, monkeypatch, capsys, mod):
        m = lint_hook if mod == "lint_hook" else vcs

        def boom(*a, **k):
            raise RuntimeError("synthetic")
        monkeypatch.setattr(m, "hook_main", boom)
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{}")))
        rc = m.main() if m is lint_hook else m.main(["x", "--hook"])
        assert rc == 1 and "crashed" in capsys.readouterr().err

    def test_broken_lint_module_is_witnessed(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        for name in ("lint_hook.py", "hook_payload.py"):
            (scripts / name).write_text((REPO_ROOT / "scripts" / name).read_text())
        (scripts / "lint.py").write_text("def broken(:\n")
        p = subprocess.run([sys.executable, str(scripts / "lint_hook.py")], input=b"{}",
                           capture_output=True, timeout=30)
        assert p.returncode == 1
        assert b"NOT linted" in p.stderr and b"Traceback" not in p.stderr


class TestAllModeDispatch:
    def test_explicit_files_get_per_file_checks_once_and_twins_full_tree(self, tmp_path, monkeypatch, capsys):
        root = _repo(tmp_path)
        _write_rel(root, "src/utils/big.py", "x = 1\n" * 1600)
        _write_rel(root, "src/launcher_tui/handlers/esc.py", EDITOR_SPAWN)
        monkeypatch.chdir(root)
        monkeypatch.setattr(sys, "argv", ["lint.py", "--format", "json", "--severity", "error",
                                          "src/utils/big.py"])
        try:
            LINT.main()
        except SystemExit:
            pass
        out = capsys.readouterr().out
        data = json.loads(out[: out.rindex("]") + 1])
        codes = [(d["code"], d["file"]) for d in data]
        assert codes.count(("MF025", "src/utils/big.py")) == 1          # L17 + L18
        assert any(c == "MF018" and "esc.py" in f for c, f in codes)   # L26: full-tree twin
