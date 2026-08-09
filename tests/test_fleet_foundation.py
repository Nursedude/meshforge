"""Tests for utils/fleet_foundation.py — the permission-foundation SSOT.

The audit/plan engine is pure (owner-lookup injected), so it's unit-tested with
no real filesystem. Covers: spec construction, data-root
drift detection (owned/root-owned/absent), idempotent plan, and the MeshForge
data-root declaration.
"""

from pathlib import Path

import pytest

from utils.fleet_foundation import (
    FoundationSpec,
    audit_data_roots,
    canonical_foundation,
    meshforge_data_roots,
    plan_data_root_fixes,
)

HOME = Path("/home/op")
ROOTS = (HOME / ".config" / "meshforge", HOME / ".cache" / "meshforge")


def _spec(user="op", roots=ROOTS):
    return FoundationSpec(operator_user=user, data_roots=roots)


# ---------- spec construction ----------


class TestCanonicalFoundation:
    def test_spec_defaults(self):
        spec = canonical_foundation(operator_user="op", home=HOME)
        assert spec.operator_user == "op"
        assert spec.rns_configdir == Path("/etc/reticulum")

    def test_spec_carries_no_topology(self):
        """2026-08-09 — the fleet/standalone axis was REMOVED, not persisted.

        It never had a consumer: validated, held on the spec, printed by the
        CLI, tested only for round-tripping, and its own docstring conceded the
        two states were identical by design. The plan had been to store it in
        deployment.json; that would have made a second writer with no reader.
        This pins that it stays gone — if a real difference appears, it should
        arrive WITH the code that consumes it.
        """
        spec = canonical_foundation(operator_user="op", home=HOME)
        assert not hasattr(spec, "topology")

    def test_data_roots_are_the_meshforge_dirs(self):
        spec = canonical_foundation(operator_user="op", home=HOME)
        assert spec.data_roots == meshforge_data_roots(HOME)


class TestMeshforgeDataRoots:
    def test_includes_xdg_dirs_and_nomadnet(self):
        roots = meshforge_data_roots(HOME)
        assert HOME / ".config" / "meshforge" in roots
        assert HOME / ".local" / "share" / "meshforge" in roots
        assert HOME / ".cache" / "meshforge" in roots
        assert HOME / ".nomadnetwork" in roots

    def test_does_not_include_etc_reticulum(self):
        # The RNS tree is the shared/parity core (rns_alignment), not a data-root.
        assert Path("/etc/reticulum") not in meshforge_data_roots(HOME)


# ---------- pure audit engine ----------


class TestAuditDataRoots:
    def test_all_operator_owned_no_drift(self):
        owner_of = lambda p: "op:op"
        assert audit_data_roots(_spec(), owner_of) == []

    def test_root_owned_flagged(self):
        owner_of = lambda p: "root:root"
        reasons = audit_data_roots(_spec(), owner_of)
        assert len(reasons) == len(ROOTS)
        assert all("must be op" in r for r in reasons)

    def test_absent_root_not_flagged(self):
        # None = absent/unreadable; born-correct at first write, never guess.
        owner_of = lambda p: None
        assert audit_data_roots(_spec(), owner_of) == []

    def test_partial_drift_only_flags_the_bad_one(self):
        bad = ROOTS[0]
        owner_of = lambda p: "root:root" if p == bad else "op:op"
        reasons = audit_data_roots(_spec(), owner_of)
        assert len(reasons) == 1
        assert str(bad) in reasons[0]

    def test_group_mismatch_alone_is_not_drift(self):
        # Ownership is by USER (who can write the files); group differences alone
        # don't block a chown-free service from writing its own files.
        owner_of = lambda p: "op:staff"
        assert audit_data_roots(_spec(), owner_of) == []


# ---------- pure plan engine ----------


class TestPlanDataRootFixes:
    def test_clean_yields_empty_plan(self):
        owner_of = lambda p: "op:op"
        assert plan_data_root_fixes(_spec(), owner_of) == []

    def test_drift_yields_recursive_chown_per_root(self):
        owner_of = lambda p: "root:root"
        plan = plan_data_root_fixes(_spec(), owner_of)
        assert len(plan) == len(ROOTS)
        for action in plan:
            assert action.cmd[:3] == ["sudo", "chown", "-R"]
            assert action.cmd[3] == "op:op"

    def test_plan_targets_match_drifted_roots(self):
        owner_of = lambda p: "root:root"
        plan = plan_data_root_fixes(_spec(), owner_of)
        targets = {action.cmd[4] for action in plan}
        assert targets == {str(r) for r in ROOTS}

    def test_idempotent_after_fix(self):
        # Simulate: first run drifted, second run (post-chown) clean -> empty.
        owner_of_clean = lambda p: "op:op"
        assert plan_data_root_fixes(_spec(), owner_of_clean) == []


# ---------- CLI arg parsing (regression: --user must work in either position) --


def _load_cli():
    import importlib.util
    p = Path(__file__).resolve().parent.parent / "scripts" / "fleet_foundation.py"
    spec = importlib.util.spec_from_file_location("ff_cli", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCLIArgOrder:
    def test_user_after_subcommand(self):
        # The canonical form, and the one that ERRORED before the fix (options
        # were top-level only). Must work now.
        ns = _load_cli().build_parser().parse_args(["apply", "--user", "op"])
        assert ns.cmd == "apply" and ns.user == "op"

    def test_user_before_subcommand_rejected(self):
        # Options go on the subcommand; the before-form errors loudly rather than
        # silently no-op'ing (which was the masking failure mode dogfooding moc3).
        with pytest.raises(SystemExit):
            _load_cli().build_parser().parse_args(["--user", "op", "apply"])

    def test_apply_dry_run(self):
        ns = _load_cli().build_parser().parse_args(["apply", "--user", "op", "--dry-run"])
        assert ns.dry_run is True

    def test_topology_flag_is_gone(self):
        """The removed axis is removed from the operator surface too."""
        parser = _load_cli().build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["audit", "--topology", "standalone"])
