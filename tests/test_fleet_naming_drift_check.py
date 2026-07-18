"""Tests for scripts/fleet_naming_drift_check.py — the DNS-leg cron judge.

Pins the pure ``judge()`` reduction: resolution/zone drift and unwaived
config debt FAIL; waived findings and healthy hosts pass; degraded inputs
(broken registry, zero hosts) never read as healthy (honest_failure_modes
#1/#3); the waiver loader's absent-file leg is [] not an error.
"""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "fleet_naming_drift_check", REPO_ROOT / "scripts" / "fleet_naming_drift_check.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

judge = _mod.judge
load_waivers = _mod.load_waivers


def _host(alias, findings=None):
    return {"alias": alias, "method": "dns", "target": f"{alias}.x",
            "address": "10.0.0.1", "findings": findings or []}


def _report(**over):
    base = {"registry_ok": True, "registry_errors": [],
            "hosts": [_host("a"), _host("b")], "config_findings": [],
            "finding_count": 0}
    base.update(over)
    return base


class TestJudgeHealthy:
    def test_all_green_is_ok(self):
        code, msg = judge(_report(), [])
        assert code == 0 and msg.startswith("OK")

    def test_waived_config_finding_is_ok_and_counted(self):
        rep = _report(config_findings=["ip_with_name_available:x 1.2.3.4 -> kiai"])
        code, msg = judge(rep, ["1.2.3.4 -> kiai"])
        assert code == 0
        assert "1 waived" in msg


class TestJudgeDrift:
    def test_host_finding_fails(self):
        rep = _report(hosts=[_host("a", ["unresolved: no answer"])])
        code, msg = judge(rep, [])
        assert code == 1 and "a: unresolved" in msg

    def test_fallback_stale_fails(self):
        rep = _report(hosts=[_host("a", ["fallback_stale(1.1.1.1->2.2.2.2)"])])
        assert judge(rep, [])[0] == 1

    def test_unwaived_config_finding_fails(self):
        rep = _report(config_findings=["ip_with_name_available:new 5.6.7.8 -> moc"])
        code, msg = judge(rep, ["1.2.3.4 -> kiai"])
        assert code == 1 and "5.6.7.8" in msg

    def test_broken_registry_never_reads_healthy(self):
        rep = _report(registry_ok=False, registry_errors=["boom"])
        code, msg = judge(rep, [])
        assert code == 1 and "registry broken" in msg

    def test_zero_hosts_never_reads_healthy(self):
        code, msg = judge(_report(hosts=[]), [])
        assert code == 1 and "ZERO hosts" in msg


class TestLoadWaivers:
    def test_absent_file_is_empty_not_error(self, tmp_path):
        assert load_waivers(tmp_path / "nope.txt") == []

    def test_comments_and_blanks_ignored(self, tmp_path):
        p = tmp_path / "w.txt"
        p.write_text("# why: kiai rtun proxy\n1.2.3.4 -> kiai\n\n  # x\n")
        assert load_waivers(p) == ["1.2.3.4 -> kiai"]
