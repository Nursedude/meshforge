"""scripts/role_declared_services.py — the declared-state query.

The invariant under test is a single one, and it is the reason the module
exists: **`unknown` must never become `absent`.**

honest_status's watchdog leg excludes a declared-absent watchdog from its
denominator. If an unresolvable declaration silently rendered as `absent`, a
box that had LOST its watchdog would be excluded too — and the fleet would look
CLEANER for having lost it, because `wdtotal` shrinks while `clean/wdtotal`
stays green. That is honest_failure_modes #1 (a degraded value mapped into the
healthy domain), and it is the exact defect this module was written to split.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "role_declared_services", REPO / "scripts" / "role_declared_services.py")
rds = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rds)


class _Host:
    def __init__(self, role):
        self.role = role


class _Registry:
    def __init__(self, hosts):
        self.hosts = hosts


CATALOG = {"roles": {
    "watcher": {"services": {"meshforge-watchdog": "enabled"}},
    "field-node": {"services": {"meshforge-watchdog": "absent"}},
    "quiet": {"services": {"meshforge-watchdog": "disabled"}},
    "silent": {"services": {"meshforge-map": "enabled"}},        # never mentions it
    "garbled": {"services": {"meshforge-watchdog": "sometimes"}},  # outside vocabulary
}}

REG = _Registry({
    "w": _Host("watcher"), "f": _Host("field-node"), "q": _Host("quiet"),
    "s": _Host("silent"), "g": _Host("garbled"), "n": _Host(None),
    "x": _Host("not-in-catalog"),
})


def _state(box):
    return rds.declared_state(CATALOG, REG, box, "meshforge-watchdog")


@pytest.mark.parametrize("box,expect", [
    ("w", "enabled"),
    ("f", "absent"),
    ("q", "disabled"),
])
def test_declared_states_pass_through(box, expect):
    assert _state(box) == expect


@pytest.mark.parametrize("box,why", [
    ("s", "role does not mention the service — silence is not a declaration"),
    ("g", "value outside the catalog vocabulary — reject what the author "
          "cannot have meant (hfm #3)"),
    ("n", "host carries no role stamp"),
    ("x", "role is not in the catalog at all"),
    ("missing-box", "host is not in the registry"),
])
def test_every_unresolvable_case_is_unknown_never_absent(box, why):
    got = _state(box)
    assert got == "unknown", f"{why}: got {got!r}"
    # The point of the whole module, stated as an assertion: an unresolvable
    # declaration must not license the caller to stop watching.
    assert got != "absent"


def test_missing_catalog_or_registry_degrades_to_unknown():
    """A broken load must not answer `absent` for the whole fleet — that would
    turn one failed import into a fleet-wide exclusion."""
    assert rds.declared_state(None, REG, "w", "meshforge-watchdog") == "unknown"
    assert rds.declared_state(CATALOG, None, "w", "meshforge-watchdog") == "unknown"


def test_known_states_are_the_catalog_vocabulary():
    """If fleet_roles.yaml grows a state, this test fails until the module is
    taught it — a closed enum needs a closed consumer (hfm #7)."""
    assert set(rds.KNOWN_STATES) == {"enabled", "disabled", "absent"}
