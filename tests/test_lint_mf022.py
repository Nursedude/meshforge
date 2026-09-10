"""Tests for the MF022 shell-installer pip/apt-hygiene rule in scripts/lint.py.

Pins the install-hardening guard so the fresh-user failure class (bare pip with
no pip-presence check, the configure_gateway `pip … | tail` exit-code mask, and
apt swallowed to /dev/null) can't creep back into the shell installers. The
rule: shell scripts must route package installs through
scripts/lib/install_common.sh.
"""
import importlib.util
from pathlib import Path

import pytest

_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


def _check(content: str, rel_path: str = "scripts/x.sh", tmp_path=None):
    p = tmp_path / "x.sh"
    p.write_text(content)
    return lint._check_pip_invocations_in_file(str(p), rel_path)


class TestMF022CatchesBadPatterns:
    def test_bare_pip_install_is_flagged_warning(self, tmp_path):
        issues = _check("#!/bin/bash\npip3 install meshtastic\n", tmp_path=tmp_path)
        assert len(issues) == 1
        assert issues[0].code == "MF022"
        assert issues[0].severity == lint.Severity.WARNING
        assert issues[0].line == 2

    def test_pip_piped_to_tail_is_error(self, tmp_path):
        # The single worst silent failure — the pipe hands pip's exit code to
        # tail, which always succeeds.
        issues = _check(
            "#!/bin/bash\nsudo -u bob pip3 install --user lxmf rns 2>&1 | tail -5\n",
            tmp_path=tmp_path,
        )
        assert len(issues) == 1
        assert issues[0].severity == lint.Severity.ERROR
        assert "exit code" in issues[0].message

    def test_apt_install_swallowed_is_flagged(self, tmp_path):
        issues = _check(
            "#!/bin/bash\napt-get install -y -qq python3-pip git &>/dev/null\n",
            tmp_path=tmp_path,
        )
        assert len(issues) == 1
        assert issues[0].severity == lint.Severity.WARNING
        assert "apt" in issues[0].message.lower()


class TestMF022AllowsGoodPatterns:
    def test_mf_pip_install_is_clean(self, tmp_path):
        issues = _check(
            "#!/bin/bash\nmf_pip_install python3 --upgrade pip\n", tmp_path=tmp_path
        )
        assert issues == []

    def test_python_m_pip_install_is_clean(self, tmp_path):
        # The canonical form the helper itself emits.
        issues = _check(
            '#!/bin/bash\n"$py" -m pip install meshtastic\n', tmp_path=tmp_path
        )
        assert issues == []

    def test_apt_install_without_swallow_is_clean(self, tmp_path):
        issues = _check(
            "#!/bin/bash\nif apt-get install -y -q git; then echo ok; fi\n",
            tmp_path=tmp_path,
        )
        assert issues == []

    def test_pip_install_in_quoted_guidance_string_is_clean(self, tmp_path):
        # A fix-hint string is documentation, not a command.
        issues = _check(
            '#!/bin/bash\ncheck_warn "x" "y" \\\n    "Install: pip3 install -r requirements.txt"\n',
            tmp_path=tmp_path,
        )
        assert issues == []

    def test_commented_pip_install_is_clean(self, tmp_path):
        issues = _check("#!/bin/bash\n# pip3 install meshtastic (old way)\n", tmp_path=tmp_path)
        assert issues == []


class TestMF022FileSelection:
    def test_non_shell_extension_skipped(self, tmp_path):
        p = tmp_path / "x.py"
        p.write_text("pip3 install meshtastic\n")
        issues = lint.check_pip_invocations_in_files([str(p)])
        assert issues == []

    def test_allowlisted_lib_is_exempt(self, tmp_path):
        # The lib legitimately constructs `pip install` / `apt-get install`.
        p = tmp_path / "install_common.sh"
        p.write_text("pip3 install meshtastic\n")
        issues = lint.check_pip_invocations_in_files(
            [str(p)]  # rel path won't match the allowlist...
        )
        # ...but exemption is by rel_path; verify the constant carries the lib.
        assert "scripts/lib/install_common.sh" in lint.MF022_ALLOWED_FILES


class TestMF022RealTreeIsClean:
    """The shipped install scripts must already be clean (the arc routed them)."""

    def test_full_tree_has_no_mf022_findings(self):
        repo_root = str(Path(__file__).parent.parent)
        issues = lint.check_pip_invocations_full_tree(repo_root=repo_root)
        assert issues == [], (
            "MF022 findings in the tree:\n"
            + "\n".join(f"{i.file}:{i.line} {i.message}" for i in issues)
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestMF022ExitCodeMaskPastPip:
    """Layer A (2026-09-07): the exit-code mask is not a pip property.

    Defect 3 of the exit-code-gate plan was "printed rc=0 that was head's exit
    code from a pipeline, not python's" — and it RECURRED the same day, in a
    session that had just named the class. CLAUDE.md states the rule generally:
    never `pytest | tail`, because the exit code is tail's.

    The discriminator is CONSUMPTION, not the pipe: `... | head` for display is
    fine and extremely common, so this fires only when `$?` is then read.
    """

    def test_pytest_piped_then_rc_read_is_an_error(self, tmp_path):
        issues = _check("#!/bin/bash\npython3 -m pytest tests/ -q | tail -3\n"
                        'echo "rc=$?"\n', tmp_path=tmp_path)
        assert any(i.code == "MF022" for i in issues), issues
        assert any("not the command's" in i.message for i in issues)

    def test_same_line_form_is_caught(self, tmp_path):
        issues = _check('#!/bin/bash\ngh run list | head -1; echo "exit=$?"\n',
                        tmp_path=tmp_path)
        assert any(i.code == "MF022" for i in issues), issues

    def test_display_only_pipe_is_NOT_flagged(self, tmp_path):
        """The rule must stay quiet on the legitimate shape or it becomes noise
        the reader learns to skip — a guard that cries wolf is disarmed by its
        own users."""
        issues = _check("#!/bin/bash\nsystemctl list-units | head -20\n"
                        "git log --oneline | tail -5\n", tmp_path=tmp_path)
        assert not [i for i in issues if i.code == "MF022"], issues

    def test_quoted_example_is_not_a_command(self, tmp_path):
        issues = _check('#!/bin/bash\necho "never do: pytest | tail -3; echo $?"\n',
                        tmp_path=tmp_path)
        assert not [i for i in issues if i.code == "MF022"], issues


class TestMF022StatementAware:
    """Review pass 4 (2026-09-09), findings 5-8: the predicate is one shared
    function over SHELL STATEMENTS, not per-line regex + parity quote count.
    The PreToolUse hook derives from `mf022_exit_code_mask_findings`, so these
    cases pin the hook's verdicts too (tests/test_exit_code_mask_guard.sh
    asserts the two agree)."""

    def _f(self, text):
        return lint.mf022_exit_code_mask_findings(text)

    # finding 5 — consumption that never names $?
    def test_and_echo_green_is_consumption(self):
        f = self._f("python3 -m pytest tests/ -q | tail -3 && echo GREEN\n")
        assert f and f[0]["how"] == "&&"

    def test_if_then_is_consumption(self):
        f = self._f("if python3 -m pytest tests/ -q | tail -3; then echo ok; fi\n")
        assert f and f[0]["how"] == "conditional"

    def test_or_exit_is_consumption(self):
        f = self._f("python3 -m pytest tests/ -q | tail -3 || exit 1\n")
        assert f and f[0]["how"] == "||"

    def test_or_true_is_an_explicit_discard(self):
        assert self._f("git log | head -3 || true\n") == []

    # finding 6 — a real quote state machine
    def test_apostrophe_inside_double_quotes_is_literal(self):
        assert self._f('echo "Bob\'s run"; git log | head -3; rc=$?\n')

    def test_nested_substitution_quotes_do_not_flip_outer_state(self):
        assert self._f('echo "=== $(git status | head -3) ==="; echo "rc=$?"\n')

    # finding 7 — lookahead skips comments / blanks, joins continuations
    def test_rc_read_behind_a_comment(self):
        assert self._f("python3 -m pytest tests/ | tail -3\n# grab rc\nrc=$?\n")

    def test_rc_read_after_blank_lines(self):
        assert self._f("python3 -m pytest tests/ | tail -3\n\n\nrc=$?\n")

    def test_backslash_continuation_is_one_statement(self):
        assert self._f("python3 -m pytest tests/ \\\n  | tail -3\nrc=$?\n")

    def test_pipe_at_end_of_line_continues_the_pipeline(self):
        assert self._f("python3 -m pytest tests/ |\ntail -3\nrc=$?\n")

    # finding 8 — the $? must belong to the pipeline
    def test_rc_read_before_the_pipe_is_clean(self):
        # The 1c class: $? belongs to the redirected pytest, the pipe is display.
        assert self._f("python3 -m pytest tests/ > log 2>&1; rc=$?; cat log | tail -3\n") == []

    def test_pipefail_exempts(self):
        assert self._f("set -o pipefail; python3 -m pytest tests/ | tail -3; rc=$?\n") == []
        assert self._f("set -euo pipefail\npython3 -m pytest tests/ | tail -3; rc=$?\n") == []

    def test_intervening_statement_breaks_the_read(self):
        assert self._f("python3 -m pytest tests/ | tail -3\nls\nrc=$?\n") == []

    def test_pure_output_between_is_skipped(self):
        assert self._f('curl -s http://x/api | head -c 4000; echo; echo "EXIT:$?"\n')

    def test_quoted_remote_command_still_reads_rc(self):
        assert self._f("ssh moc3 'rnstatus 2>&1 | head -30; echo \"rc=$?\"'\n")

    def test_plain_next_statement_read_still_fires(self):
        # The false-negative the 1c audit feared losing — kept.
        f = self._f("python3 -m pytest tests/ | tail -3; rc=$?\n")
        assert f and f[0]["how"] == "rc-read" and f[0]["lineno"] == 1

    def test_file_scan_reports_the_pipe_line_with_how(self, tmp_path):
        issues = _check("#!/bin/bash\necho start\n"
                        "python3 -m pytest tests/ -q | tail -3 && echo GREEN\n",
                        tmp_path=tmp_path)
        assert [i.line for i in issues if i.code == "MF022"] == [3]
        assert "consumed by `&&`" in issues[0].message
        assert "not the command's" in issues[0].message
