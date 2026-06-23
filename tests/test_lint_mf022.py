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
