"""Tests for the MF025 file-size ratchet in scripts/lint.py.

CLAUDE.md's "ALWAYS split files exceeding 1,500 lines" rule had no
enforcement until 2026-07-13, by which point five src/ files had drifted
past the cap. MF025 pins it: non-baseline src/ python files fail above
1,500 lines; the five frozen offenders fail only if they GROW past their
frozen size. The baseline may only shrink — the gate carries the rule
across model handoffs (model-agnostic-harness principle).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

# scripts/lint.py is not packaged as a module; import by file path.
import importlib.util
_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)

REPO_ROOT = Path(__file__).parent.parent


def _write_py(tmp_path: Path, rel: str, n_lines: int) -> Path:
    """Create tmp_path/rel with exactly n_lines lines."""
    full = tmp_path / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("# filler\n" * n_lines)
    return full


class TestRatchetMechanics:
    def test_small_file_passes(self, tmp_path):
        _write_py(tmp_path, "src/utils/small.py", 100)
        issues = lint.check_file_size_ratchet(
            [str(tmp_path / "src/utils/small.py")], repo_root=str(tmp_path)
        )
        assert issues == []

    def test_file_at_limit_passes(self, tmp_path):
        _write_py(tmp_path, "src/utils/edge.py", lint.MF025_LINE_LIMIT)
        issues = lint.check_file_size_ratchet(
            [str(tmp_path / "src/utils/edge.py")], repo_root=str(tmp_path)
        )
        assert issues == []

    def test_file_over_limit_fails(self, tmp_path):
        _write_py(tmp_path, "src/utils/big.py", lint.MF025_LINE_LIMIT + 1)
        issues = lint.check_file_size_ratchet(
            [str(tmp_path / "src/utils/big.py")], repo_root=str(tmp_path)
        )
        assert len(issues) == 1
        assert issues[0].code == "MF025"
        assert issues[0].severity == lint.Severity.ERROR
        assert "split the file" in issues[0].message

    def test_non_src_file_ignored(self, tmp_path):
        _write_py(tmp_path, "tests/huge_test.py", 5000)
        issues = lint.check_file_size_ratchet(
            [str(tmp_path / "tests/huge_test.py")], repo_root=str(tmp_path)
        )
        assert issues == []

    def test_non_python_file_ignored(self, tmp_path):
        full = tmp_path / "src" / "big.sh"
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("echo hi\n" * 5000)
        issues = lint.check_file_size_ratchet(
            [str(full)], repo_root=str(tmp_path)
        )
        assert issues == []


class TestFrozenBaseline:
    def test_baseline_file_at_frozen_size_passes(self, tmp_path):
        rel = next(iter(lint.MF025_BASELINE))
        frozen = lint.MF025_BASELINE[rel]
        _write_py(tmp_path, rel, frozen)
        issues = lint.check_file_size_ratchet(
            [str(tmp_path / rel)], repo_root=str(tmp_path)
        )
        assert issues == []

    def test_baseline_file_grown_fails(self, tmp_path):
        rel = next(iter(lint.MF025_BASELINE))
        frozen = lint.MF025_BASELINE[rel]
        _write_py(tmp_path, rel, frozen + 1)
        issues = lint.check_file_size_ratchet(
            [str(tmp_path / rel)], repo_root=str(tmp_path)
        )
        assert len(issues) == 1
        assert "frozen baseline" in issues[0].message

    def test_baseline_file_shrunk_under_cap_passes(self, tmp_path):
        rel = next(iter(lint.MF025_BASELINE))
        _write_py(tmp_path, rel, 200)
        issues = lint.check_file_size_ratchet(
            [str(tmp_path / rel)], repo_root=str(tmp_path)
        )
        assert issues == []


class TestLiveRepoContract:
    """Pin the rule against THIS repo — the gate must hold on real files."""

    def test_live_tree_is_green(self):
        """Every src/ file passes: under the cap, or frozen-baseline exact."""
        files = lint.get_all_python_files(str(REPO_ROOT / "src"))
        issues = lint.check_file_size_ratchet(files, repo_root=str(REPO_ROOT))
        assert issues == [], [f"{i.file}: {i.message}" for i in issues]

    def test_baseline_entries_still_needed(self):
        """A baseline entry whose file dropped under the cap (or vanished)
        must be DELETED — stale grants are future headroom."""
        stale = []
        for rel, frozen in lint.MF025_BASELINE.items():
            full = REPO_ROOT / rel
            if not full.is_file():
                stale.append(f"{rel}: file gone — delete the entry")
                continue
            with open(full, encoding="utf-8", errors="ignore") as fh:
                lines = sum(1 for _ in fh)
            if lines <= lint.MF025_LINE_LIMIT:
                stale.append(
                    f"{rel}: now {lines} lines (<= cap) — delete the entry"
                )
            elif lines < frozen:
                stale.append(
                    f"{rel}: shrank to {lines} — ratchet the baseline down"
                )
        assert stale == [], stale

    def test_baseline_never_grows_check(self):
        """The frozen counts must match the freeze exactly — editing an entry
        upward is the forbidden move this test pins. node_history.py,
        watchdog_probes_drift.py, and watchdog_probes_gateway.py were split out
        (2026-07-14) and their entries deleted, so the set shrank; a deletion
        via a real split is the sanctioned way the baseline moves."""
        assert lint.MF025_BASELINE == {
            'src/utils/map_data_collector.py': 1566,
            'src/gateway/rns_bridge.py': 1544,
        }
