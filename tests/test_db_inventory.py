"""Tests for utils.db_inventory — the SSOT for SQLite consumers.

Critical contract: every DB the source actually creates must be in
INVENTORY. Otherwise the lint/audit safeguards have a blind spot —
exactly the trap that caused Phase 1 to miss health_state.db.

The forgot-to-register check works by grepping src/ for `.db` path
literals and asserting each one is reachable from a DBSpec.path_factory().
"""

import re
from pathlib import Path
from typing import Set

import pytest

from utils.db_inventory import INVENTORY, DBSpec, find_spec


REPO_SRC = Path(__file__).resolve().parent.parent / "src"


class TestDBSpec:
    def test_all_specs_have_path_factory(self):
        for spec in INVENTORY:
            path = spec.path_factory()
            assert isinstance(path, Path), f"{spec.name}: path_factory must return Path"

    def test_all_specs_have_unique_names(self):
        names = [s.name for s in INVENTORY]
        assert len(names) == len(set(names)), "duplicate DBSpec.name"

    def test_all_specs_have_unique_paths(self):
        paths = [str(s.path_factory()) for s in INVENTORY]
        assert len(paths) == len(set(paths)), "duplicate DB paths in INVENTORY"

    def test_pragma_defaults_match_helper(self):
        # If anyone weakens these defaults, this fails.
        for spec in INVENTORY:
            assert spec.expected_journal_mode == "wal"
            assert spec.expected_synchronous == 1
            assert spec.expected_journal_size_limit == 67_108_864

    def test_find_spec_returns_correct_db(self):
        spec = find_spec("node_history")
        assert spec is not None
        assert spec.creator_module == "utils.node_history"

    def test_find_spec_returns_none_for_unknown(self):
        assert find_spec("does_not_exist_xyz") is None


class TestEveryRuntimeDBIsInInventory:
    """The forgot-to-register safety net.

    Scans src/ for `.db` path literals (e.g. `"messages.db"` or
    `f"{name}.db"`-style hints) and asserts each is reachable from
    INVENTORY.path_factory().
    """

    DB_LITERAL = re.compile(r'["\']([a-z_][a-z0-9_]*)\.db["\']')

    def _runtime_db_basenames(self) -> Set[str]:
        """Find every "<name>.db" string literal in src/."""
        names: Set[str] = set()
        for py_file in REPO_SRC.rglob("*.py"):
            # Skip the inventory itself and tests
            if py_file.name in ("db_inventory.py",):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for match in self.DB_LITERAL.finditer(content):
                # Filter out obvious false positives (test fixtures, etc.)
                name = match.group(1)
                if name in {"test", "tmp", "x", "foo", "bar", "stub"}:
                    continue
                names.add(name)
        return names

    def _inventory_basenames(self) -> Set[str]:
        return {Path(str(s.path_factory())).stem for s in INVENTORY}

    def test_every_runtime_db_in_inventory(self):
        runtime = self._runtime_db_basenames()
        inventory = self._inventory_basenames()
        # Pop known per-instance DBs that are MessageQueue's two
        # configurations (p2s, s2p) — they share the message_queue
        # DBSpec and don't need their own entries.
        gateway_instance_dbs = {"p2s", "s2p"}
        # Sister-repo node_history files referenced for cache lookup
        cross_repo_aliases: Set[str] = set()
        missing = runtime - inventory - gateway_instance_dbs - cross_repo_aliases
        assert not missing, (
            f"DBs found in src/ but missing from INVENTORY: {missing}. "
            "Add a DBSpec to src/utils/db_inventory.py."
        )
