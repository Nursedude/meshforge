"""Tests for MF014 operator-local deny-patterns in scripts/lint.py.

Born 2026-07-23: a real operator LAN IP landed in a repo docstring and the
tracked MF014_PATTERNS were structurally blind to it — the operator's own
subnets can never be listed in the public lint.py (that would BE the leak),
so they load from an untracked per-box file. These tests pin the loader's
failure modes: absence is normal, bad regex is loud-but-skipped, and a
loaded pattern actually flags a violating line.
"""
import importlib.util
import os
from pathlib import Path

# scripts/lint.py is not packaged as a module; import by file path.
_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint_mf014_local", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


class TestLoadLocalOperatorPatterns:
    def test_absent_file_returns_empty_no_error(self, tmp_path):
        assert lint.load_local_operator_patterns(str(tmp_path / "nope.txt")) == []

    def test_loads_tab_and_multispace_separated_entries(self, tmp_path):
        path = _write(tmp_path, "pats.txt",
                      "# comment\n"
                      "\n"
                      "10\\.99\\.[0-9]+\\.[0-9]+\toperator subnet\n"
                      "secrethost   operator hostname\n")
        pats = lint.load_local_operator_patterns(path)
        assert len(pats) == 2
        assert pats[0][0].search("x = '10.99.1.2'")
        assert pats[0][1] == "operator subnet"
        assert pats[1][0].search("ssh secrethost")

    def test_bad_regex_is_skipped_loudly_good_ones_survive(self, tmp_path, capsys):
        path = _write(tmp_path, "pats.txt",
                      "([bad\tbroken regex\n"
                      "goodpat\tfine\n")
        pats = lint.load_local_operator_patterns(path)
        assert len(pats) == 1
        assert pats[0][1] == "fine"
        assert "bad regex" in capsys.readouterr().err

    def test_env_override_wins(self, tmp_path, monkeypatch):
        path = _write(tmp_path, "pats.txt", "envpat\tfrom env\n")
        monkeypatch.setenv(lint.MF014_LOCAL_PATTERNS_ENV, path)
        pats = lint.load_local_operator_patterns()
        assert len(pats) == 1 and pats[0][1] == "from env"


class TestLocalPatternsEnforced:
    def test_local_pattern_flags_violating_line(self, tmp_path, monkeypatch):
        pats_path = _write(tmp_path, "pats.txt",
                           "10\\.99\\.[0-9]+\\.[0-9]+\toperator subnet leak\n")
        monkeypatch.setattr(
            lint, "_MF014_LOCAL_PATTERNS",
            lint.load_local_operator_patterns(pats_path))
        src = _write(tmp_path, "leaky.py", 'HOST = "10.99.12.34"\n')
        issues = lint._check_operator_values_in_file(src, "src/leaky.py")
        assert len(issues) == 1
        assert issues[0].code == "MF014"
        assert "operator subnet leak" in issues[0].message

    def test_tracked_patterns_still_fire_without_local_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lint, "_MF014_LOCAL_PATTERNS", [])
        src = _write(tmp_path, "leaky.py", "path = '/home/wh6gxz/x'\n")
        issues = lint._check_operator_values_in_file(src, "src/leaky.py")
        assert len(issues) == 1 and issues[0].code == "MF014"

    def test_clean_file_with_local_patterns_loaded_stays_clean(self, tmp_path, monkeypatch):
        pats_path = _write(tmp_path, "pats.txt", "10\\.99\\.\tleak\n")
        monkeypatch.setattr(
            lint, "_MF014_LOCAL_PATTERNS",
            lint.load_local_operator_patterns(pats_path))
        src = _write(tmp_path, "clean.py", 'HOST = "192.0.2.7"  # TEST-NET\n')
        assert lint._check_operator_values_in_file(src, "src/clean.py") == []
