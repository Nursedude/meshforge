"""TUI audit finding 5 (2026-09-27): landing/summary screens mapped UNKNOWN to
healthy. Each test PLANTS the fault (a probe that raises, a daemon that is
stale, a leg that cannot be observed) and asserts the screen does not say
healthy. The happy paths were already journeyed; these are the error branches.
"""

import inspect
import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context


# ---------------------------------------------------------------- NOC Home

def _noc():
    from handlers.noc_home import NocHomeHandler
    h = NocHomeHandler.__new__(NocHomeHandler)
    h.ctx = make_handler_context()
    return h


def _boom(*a, **k):
    raise RuntimeError("probe exploded")


class TestNocHome:
    def test_failed_service_probe_is_unknown_not_off(self):
        with patch("service_remediation.service_enabled_here", return_value=True), \
             patch("utils.service_check.check_service", side_effect=_boom):
            state, label = _noc()._svc_state("meshtasticd")
        assert state == "unknown" and "UNKNOWN" in label

    def test_unobservable_shared_instance_is_unknown_not_up(self):
        from types import SimpleNamespace
        with patch("service_remediation.service_enabled_here", return_value=True), \
             patch("utils.service_check.check_service",
                   return_value=SimpleNamespace(available=True)), \
             patch("utils.service_check.check_rns_shared_instance", side_effect=_boom):
            state, running = _noc()._probe_rns()
        assert state == "unknown" and running is True

    def test_outer_rns_failure_is_unknown_not_off(self):
        with patch("service_remediation.service_enabled_here", side_effect=_boom):
            assert _noc()._probe_rns() == ("unknown", False)

    def test_unreadable_gateway_json_is_unknown_not_disabled(self):
        from gateway.config import GatewayConfig
        cfg = GatewayConfig()
        cfg._load_error = "JSONDecodeError: planted"
        with patch("gateway.config.GatewayConfig.load", return_value=cfg):
            assert _noc()._probe_meshcore() == "unknown"

    def test_meshcore_probe_exception_is_unknown(self):
        with patch("gateway.config.GatewayConfig.load", side_effect=_boom):
            assert _noc()._probe_meshcore() == "unknown"

    def test_unknown_renders_as_question_marks_not_benign(self):
        from handlers.noc_home import _dot
        assert _dot("unknown") == "[ ?? ]"
        assert _dot("unknown") != _dot("off")


# --------------------------------------------------------------- mini pane

def _mini_render(state, errs_findings=None):
    from handlers import mini_dudeai as mod
    h = mod.MiniDudeaiHandler.__new__(mod.MiniDudeaiHandler)
    h.ctx = make_handler_context()
    with patch.object(mod, "_mini_paths", return_value=("s", "h")), \
         patch.object(mod, "_read_json", return_value=state), \
         patch.object(mod, "_read_history", return_value=[]), \
         patch.object(mod, "build_findings", return_value=[]):
        h._render()
    boxes = [c for c in h.ctx.dialog.calls if c[0] == "msgbox"]
    return boxes[-1][1][1]


class TestMiniPane:
    def test_stale_watcher_gets_no_checkmark(self):
        text = _mini_render({"last_tick_ts": time.time() - 3600, "error_count": 0})
        assert "✓" not in text and "NOT a clean bill" in text

    def test_missing_state_gets_no_checkmark(self):
        text = _mini_render({})
        assert "✓" not in text

    def test_source_errors_get_no_checkmark(self):
        text = _mini_render({"last_tick_ts": time.time() - 5, "error_count": 2})
        assert "✓" not in text and "partly blind" in text

    def test_fresh_clean_watcher_keeps_its_checkmark(self):
        text = _mini_render({"last_tick_ts": time.time() - 5, "error_count": 0})
        assert "Nothing actionable right now ✓" in text

    def test_one_freshness_constant(self):
        from handlers import mini_dudeai as mod
        src = inspect.getsource(mod)
        assert "age > STALE_AFTER_S" in src and "age > 300" not in src


# ------------------------------------------------------ RNS diagnostics summary

class TestRnsDiagnosticsSummary:
    def _summary(self, issues, warnings, unobserved):
        from handlers._rns_diagnostics_engine import summary_lines
        return "\n".join(summary_lines(issues, warnings, unobserved))

    def test_blind_leg_never_reads_passed(self):
        text = self._summary([], [], ["interface traffic (rnstatus cannot connect)"])
        assert "All checks passed" not in text and "Connectivity OK" not in text
        assert "NOT CHECKED" in text and "UNKNOWN" in text

    def test_blind_leg_with_warnings_is_not_ok(self):
        text = self._summary([], ["w"], ["x"])
        assert "Connectivity OK" not in text

    def test_all_observed_and_clean_still_passes(self):
        assert "All checks passed" in self._summary([], [], [])

    def test_every_blind_rnstatus_branch_records_unobserved(self):
        from handlers import _rns_diagnostics_engine as mod
        src = inspect.getsource(mod.run_rns_diagnostics)
        block = src[src.index("iface_health = check_rns_interface_health()"):
                    src.index("# Summary")]
        # three "could not" branches + the exception handler
        assert block.count("unobserved.append(") == 4


# --------------------------------------------------------- Dashboard datapath

def test_dashboard_path_probe_checks_the_exit_code():
    # Source-level (the probe sits inside a six-step interactive routine):
    # the rnpath branch must branch on returncode before parsing stdout.
    from handlers import dashboard
    src = inspect.getsource(dashboard)
    block = src[src.index("# Test 6: RNS path table"):]
    block = block[:block.index("except FileNotFoundError")]
    assert "result.returncode != 0" in block
    assert block.index("result.returncode != 0") < block.index("splitlines()")
