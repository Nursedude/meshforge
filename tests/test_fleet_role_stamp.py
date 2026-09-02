"""Tests for scripts/fleet_role_stamp.py — deriving each box's declared role
into the naming registry so a poller can tell "absent by design" from "broken".

The whole value of this organ is the TRI-STATE and the direction of its
degradations, so that is what these pin:
  * enabled -> True; disabled/absent -> False
  * unknown role, undeclared service, or an out-of-vocabulary value -> None
  * None NEVER becomes False (that would stop polling a genuinely broken map)
  * a box that ANSWERED but declares no MeshForge role is INERT, not a
    degraded run — `inert` and `indeterminate` are different claims
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fleet_role_stamp as frs  # noqa: E402


CATALOG = {"roles": {
    "full-gateway": {"services": {"meshforge-map": "enabled"}},
    "gateway-only": {"services": {"meshforge-map": "disabled"}},
    "field-node": {"services": {"meshforge-map": "absent"}},
    "mystery": {"services": {"meshforge-gateway": "enabled"}},
    "weird": {"services": {"meshforge-map": "sometimes"}},
}}


class TestServesMapForRole:
    @pytest.mark.parametrize("role,expected", [
        ("full-gateway", True),
        ("gateway-only", False),
        ("field-node", False),
    ])
    def test_catalog_vocabulary(self, role, expected):
        got, note = frs._serves_map_for_role(CATALOG, role)
        assert got is expected, note

    def test_unknown_role_is_none_not_false(self):
        got, note = frs._serves_map_for_role(CATALOG, "no-such-role")
        assert got is None
        assert "not in catalog" in note

    def test_role_that_does_not_declare_the_map_is_none(self):
        """A role that simply forgot to mention meshforge-map is UNKNOWN. If
        this returned False the box would stop being polled on the strength of
        an omission — absence of a declaration is not a declaration."""
        got, note = frs._serves_map_for_role(CATALOG, "mystery")
        assert got is None
        assert "does not declare" in note

    def test_out_of_vocabulary_value_is_none_and_named(self):
        """A closed enum needs a closed consumer (hfm #7): an unrecognised
        state must not be silently read as one of the known ones."""
        got, note = frs._serves_map_for_role(CATALOG, "weird")
        assert got is None
        assert "sometimes" in note

    def test_no_role_ever_yields_false_by_accident(self):
        """Sweep: only the two explicit no-map states may produce False."""
        for role in CATALOG["roles"]:
            got, _ = frs._serves_map_for_role(CATALOG, role)
            if got is False:
                state = CATALOG["roles"][role]["services"]["meshforge-map"]
                assert state in ("disabled", "absent"), (role, state)


class TestNoRoleIsInertNotDegraded:
    def test_empty_role_from_a_box_that_answered_is_its_own_signal(self):
        """The MeshAnchor box answers ssh and declares no MeshForge role. That
        is a FACT (inert), not a failed observation — folding it into the
        unreachable bucket would make the run report CONCERN forever, and a
        permanent CONCERN nobody can clear is how a real one stops being read.
        """
        assert frs.NO_ROLE_DECLARED != ""
        # the sentinel must be distinguishable from every transport error
        for transport_err in ("ssh timeout", "ssh failed: boom", "rc=255"):
            assert transport_err != frs.NO_ROLE_DECLARED


class TestStateVocabulary:
    def test_enabled_is_the_only_true(self):
        trues = [k for k, v in frs.STATE_SERVES.items() if v is True]
        assert trues == ["enabled"]

    def test_every_known_state_maps_to_a_bool_never_none(self):
        # None is reserved for "we could not decide"; a state that IS in the
        # vocabulary has been decided.
        assert all(isinstance(v, bool) for v in frs.STATE_SERVES.values())
