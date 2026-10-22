"""RNS probes on a box with NO RNS stack by design must read ``inert``.

2026-10-21, meshforge-lab: the bench box's role SSOT declares ``rnsd: absent``
(docs/fleet_roles.yaml ``lab``) and nothing serves RNS there — yet
``rf_leg_silent``, ``rns_interface_down_peer_reachable`` and
``rns_rpc_unresponsive`` sat ``indeterminate`` ("rnstatus binary not found")
from its first tick, so ``detector_blind_any`` stayed active permanently.

``absent`` needs the DECLARATION (effective role state ``rnsd: absent``) AND
the OBSERVATION (no ``@rns/`` listener bound). v1 of this fix inferred absence
from the rnstatus binary + the system unit instead; the non-author review
showed the root watchdog cannot see a ``~/.local/bin`` rnstatus and a user-unit
rnsd has no system unit, so a live RNS box would have read ``inert``.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import role_declaration as drift  # noqa: E402
from utils import watchdog_probes_rf_leg as rf  # noqa: E402
from utils import watchdog_probes_rns as wpr  # noqa: E402
from utils.rns_status_parser import RNSStatus  # noqa: E402
from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions,
    reset_dispositions,
)

BINARY_MISSING = "rnstatus binary not found. Install RNS: pip install rns"
SS_EMPTY = "Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
SS_RNS = SS_EMPTY + ("u_str LISTEN 0 0 @rns/default/rpc 123 * 0 "
                     'users:(("rnsd",pid=42,fd=7))\n')
TCP_NONE = "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 4096 0.0.0.0:9443 0.0.0.0:*\n"
TCP_RNS = TCP_NONE + "LISTEN 0 5 127.0.0.1:37428 0.0.0.0:*\n"


@pytest.fixture(autouse=True)
def _clean_dispositions():
    reset_dispositions()
    yield
    reset_dispositions()


def _disp(cls):
    return collect_dispositions().get(cls, {})


# ── the declaration: effective role state ────────────────────────────


class TestRoleDeclaredUnitState:

    def test_loads_the_checkout_it_runs_from(self):
        """Non-vacuity for every ``is None`` below: the converge SSOT must
        actually LOAD from this checkout (not a hardcoded /opt/meshforge), or
        None would be the load error, not the rule."""
        mod = drift._load_provision_role(drift._MESHFORGE_ROOT)
        assert Path(drift._MESHFORGE_ROOT) == Path(__file__).resolve().parents[1]
        assert callable(mod.effective_unit_state)

    def _state(self, decl):
        with patch.object(drift, "_read_deployment_declaration_status",
                          return_value=decl):
            return drift.role_declared_unit_state("rnsd")

    def test_lab_role_declares_rnsd_absent(self):
        # the REAL catalog: the motivating declaration, not a fixture's
        assert self._state(("declared", "lab", {})) == "absent"

    def test_honored_override_wins(self):
        ov = {"rnsd": {"state": "enabled", "reason": "bench run needs rnsd"}}
        assert self._state(("declared", "lab", ov)) == "enabled"

    def test_unhonored_override_is_unresolvable(self):
        # no reason → not honored → None, never the role's 'absent'
        assert self._state(("declared", "lab", {"rnsd": {"state": "absent"}})) is None

    @pytest.mark.parametrize("decl", [
        ("undeclared", None, {}), ("unreadable", None, {}),
        ("declared", "no-such-role-xyz", {})])
    def test_unresolvable_is_none(self, decl):
        assert self._state(decl) is None


# ── the gate: declaration AND observation ────────────────────────────


class TestRnsStackPresence:

    def _presence(self, declared, ss_output, tcp=TCP_NONE):
        with patch.object(drift, "role_declared_unit_state",
                          return_value=declared):
            return wpr.rns_stack_presence(ss_output=ss_output,
                                          ss_tcp_output=tcp)

    def test_absent_when_declared_absent_and_no_listener(self):
        assert self._presence("absent", SS_EMPTY) == "absent"

    def test_tcp_shared_instance_is_not_absent(self):
        # shared_instance_type = tcp binds no @rns/ socket (review 2)
        assert self._presence("absent", SS_EMPTY, tcp=TCP_RNS) == "unknown"

    def test_tcp_table_unobservable_is_unknown(self):
        with patch.object(drift, "role_declared_unit_state",
                          return_value="absent"), \
             patch.object(wpr, "_rns_tcp_shared_listening", return_value=None):
            assert wpr.rns_stack_presence(ss_output=SS_EMPTY) == "unknown"

    def test_listener_bound_overrides_declaration(self):
        # something IS serving RNS here (rnsd, user unit, host-fallback app)
        assert self._presence("absent", SS_RNS) == "unknown"

    def test_listener_table_unobservable_is_unknown(self):
        with patch.object(drift, "role_declared_unit_state",
                          return_value="absent"), \
             patch.object(wpr, "_rns_listener_tokens", return_value=None):
            assert wpr.rns_stack_presence() == "unknown"

    @pytest.mark.parametrize("declared", ["enabled", "disabled", None])
    def test_not_declared_absent_is_unknown(self, declared):
        # declared-enabled + nothing running must stay LOUD
        assert self._presence(declared, SS_EMPTY) == "unknown"

    def test_error_is_unknown(self):
        with patch.object(drift, "role_declared_unit_state",
                          side_effect=RuntimeError("boom")):
            assert wpr.rns_stack_presence(ss_output=SS_EMPTY) == "unknown"


# ── the three consumers ──────────────────────────────────────────────


def _binary_missing():
    return RNSStatus(parse_error=BINARY_MISSING)


CASES = [("absent", "inert"), ("unknown", "indeterminate")]


@pytest.mark.parametrize("presence, expected", CASES)
def test_rpc_responsive(presence, expected):
    assert wpr.probe_rns_rpc_responsive(
        rnstatus_status=_binary_missing(), rns_presence=presence) is None
    assert _disp("rns_rpc_unresponsive")["disp"] == expected


@pytest.mark.parametrize("presence, expected", CASES)
def test_interface_down_peer_reachable(presence, expected):
    assert wpr.probe_rns_interface_down_peer_reachable(
        rnstatus_status=_binary_missing(), rns_presence=presence) is None
    assert _disp("rns_interface_down_peer_reachable")["disp"] == expected


@pytest.mark.parametrize("presence, expected", CASES)
def test_rf_leg_silent(presence, expected, tmp_path):
    assert rf.probe_rf_leg_silent(
        status=_binary_missing(), rns_presence=presence,
        state_path=str(tmp_path / "rf.json")) is None
    assert _disp("rf_leg_silent")["disp"] == expected


def test_probes_compute_presence_when_not_given(tmp_path):
    """Standalone callers (no runner) still get the gate."""
    with patch.object(wpr, "rns_stack_presence", return_value="absent") as p:
        wpr.probe_rns_rpc_responsive(rnstatus_status=_binary_missing())
        wpr.probe_rns_interface_down_peer_reachable(
            rnstatus_status=_binary_missing())
        rf.probe_rf_leg_silent(status=_binary_missing(),
                               state_path=str(tmp_path / "rf.json"))
    assert p.call_count == 3
    for cls in ("rns_rpc_unresponsive", "rns_interface_down_peer_reachable",
                "rf_leg_silent"):
        assert _disp(cls)["disp"] == "inert", cls


@pytest.mark.parametrize("probe", ["rf", "iface"])
def test_timeout_never_reads_absent(probe, tmp_path):
    """A TIMEOUT means something answered slowly — the #72 wedge shape."""
    timed = RNSStatus(timed_out=True, parse_error="rnstatus timed out")
    if probe == "rf":
        rf.probe_rf_leg_silent(status=timed, rns_presence="absent",
                               state_path=str(tmp_path / "rf.json"))
        cls = "rf_leg_silent"
    else:
        wpr.probe_rns_interface_down_peer_reachable(
            rnstatus_status=timed, rns_presence="absent")
        cls = "rns_interface_down_peer_reachable"
    assert _disp(cls)["disp"] == "indeterminate"


# ── the shared rule (one copy for converge + watchdog) ───────────────


class TestEffectiveUnitState:

    def _mod(self):
        return drift._load_provision_role(drift._MESHFORGE_ROOT)

    def test_rule(self):
        eus = self._mod().effective_unit_state
        svc = {"rnsd": "absent"}
        assert eus(svc, {}, "rnsd") == "absent"
        assert eus(svc, {"rnsd": {"state": "enabled", "reason": "r"}},
                   "rnsd") == "enabled"
        assert eus(svc, {"rnsd": {"state": "enabled"}}, "rnsd") is None
        assert eus(svc, {"rnsd": {"state": "bogus", "reason": "r"}},
                   "rnsd") is None
