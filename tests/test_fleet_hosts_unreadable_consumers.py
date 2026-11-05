"""r7e #2 — every consumer of the shared fleet-list resolver REFUSES on a
present-but-unreadable list; none turns it into "no fleet" or a lower tier.

Operator 2026-11-05: "quietly failing is not an answer". The resolver now
raises ``FleetHostsUnreadable`` (python) / returns rc 2 (shell lib). This
file drives each python consumer with a resolver that raises and pins what
the consumer does with it: a refusal / UNKNOWN naming the path, and no
write that would read as the fleet's answer. The shell consumers all
already refuse on a non-zero rc; the lib itself prints the reason
(``tests/test_fleet_hosts_resolver.py::TestShellUnreadableParity``).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import utils.fleet_hosts as fh  # noqa: E402
from utils.fleet_hosts import FleetHostsUnreadable  # noqa: E402

BAD = Path("/nowhere/fleet_hosts.meshforge")


def _boom(*_a, **_k):
    raise FleetHostsUnreadable(BAD, "permission denied")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_r7e2_{name}", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclasses resolve their module by name
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return mod


@pytest.fixture
def raising(monkeypatch):
    """The SHARED resolver raises, for consumers that import it lazily."""
    monkeypatch.setattr(fh, "resolve_fleet_hosts_file", _boom)
    monkeypatch.setattr(fh, "resolve_fleet_hosts", _boom)
    for k in fh.ENV_OVERRIDE_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_dep_advisory_reads_unknown_naming_the_path(tmp_path, monkeypatch):
    dac = _load("dep_advisory_check")
    monkeypatch.setattr(dac, "resolve_fleet_hosts_file", _boom)
    for k in fh.ENV_OVERRIDE_KEYS:
        monkeypatch.delenv(k, raising=False)
    hosts, err = dac.read_fleet_hosts()
    assert hosts == [] and str(BAD) in err and "unreadable" in err

    monkeypatch.setattr(dac, "get_real_user_home", lambda: str(tmp_path))
    monkeypatch.setattr(dac, "_run", lambda cmd, timeout, stdin_text=None: (0, "", ""))
    assert dac.main(["--quiet"]) == 2
    status = (tmp_path / ".meshforge-dep-advisories").read_text()
    assert "UNKNOWN" in status and str(BAD) in status


def test_fleet_role_stamp_refuses(tmp_path, monkeypatch, capsys):
    frs = _load("fleet_role_stamp")
    monkeypatch.setattr(frs, "resolve_fleet_hosts_file", _boom)
    monkeypatch.setattr(frs, "resolve_fleet_hosts", _boom)
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"hosts": {}}))
    before = reg.read_bytes()
    assert frs.main(["--registry", str(reg)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and str(BAD) in out
    assert reg.read_bytes() == before


def test_provision_role_fleet_check_refuses(raising, capsys):
    pr = _load("provision_role")
    assert pr.main(["--fleet-check"]) == 2
    err = capsys.readouterr().err
    assert str(BAD) in err


def test_resource_baseline_refuses_before_capturing(raising, monkeypatch, capsys):
    rb = _load("resource_baseline")
    monkeypatch.setattr(rb, "resolve_fleet_hosts", _boom)
    monkeypatch.setattr(rb, "_run_capture",
                        lambda *_a, **_k: pytest.fail("captured a self-only fleet"))
    assert rb.cmd_capture("t") == 2
    assert str(BAD) in capsys.readouterr().err


def test_rns_alignment_refuses(raising, capsys):
    ra = _load("rns_alignment")
    assert ra._audit_fleet() == 2
    assert str(BAD) in capsys.readouterr().err


def test_fleet_naming_audit_refuses(monkeypatch, capsys):
    fna = _load("fleet_naming_audit")
    monkeypatch.setattr(fna, "fleet_hosts_file", _boom)
    assert fna.main([]) == 2
    assert str(BAD) in capsys.readouterr().err


def test_fleet_dup_collector_writes_nothing(tmp_path, monkeypatch, capsys):
    import utils.fleet_dup_collector as fdc
    monkeypatch.setattr(fdc, "fleet_hosts_file", _boom)
    out = tmp_path / "view.json"
    out.write_text('{"prior": true}')
    assert fdc.main(["--out", str(out), "--quiet"]) == 2
    assert out.read_text() == '{"prior": true}', "prior rollup must be held"
    assert str(BAD) in capsys.readouterr().err


def test_rollup_pane_is_unknown_not_a_fleet_of_one(raising, capsys):
    from mini_dudeai import rollup
    assert rollup.main([]) == 2
    out = capsys.readouterr().out
    assert "UNKNOWN" in out and str(BAD) in out
    assert rollup.main(["--deep"]) == 2


def test_mini_daemon_refuses_to_guess_a_preset(raising):
    from mini_dudeai import daemon
    with pytest.raises(SystemExit) as ei:
        daemon.main(["--preset", "auto"])
    assert str(BAD) in str(ei.value.code)


def test_mini_preset_rule_raises_for_the_watchdog_to_see(raising):
    """The watchdog probe catches this as `unresolvable` — it must RAISE,
    never resolve to a preset."""
    from mini_dudeai.daemon import _resolve_preset_name
    with pytest.raises(FleetHostsUnreadable):
        _resolve_preset_name("auto", {"HOME": "/x"}, announce=False)


def test_nomadnet_watch_import_default_is_the_unreadable_path(raising):
    from monitoring import nomadnet_silence_watch as nsw
    assert nsw._default_fleet_hosts() == BAD


def test_watchdog_orphaned_exclusion_probe_is_indeterminate(raising, tmp_path):
    from utils.watchdog_probe_core import collect_dispositions, reset_dispositions
    from utils.watchdog_probes_mini import probe_mini_rule_orphaned_exclusion
    (tmp_path / "mini_dudeai_rules.json").write_text(json.dumps({"rules": [
        {"id": "r1", "match": {"kind": "x", "subject_exclude_globs": ["*a*"]}}]}))
    reset_dispositions()
    assert probe_mini_rule_orphaned_exclusion(mini_home=str(tmp_path)) is None
    d = collect_dispositions()["mini_rule_orphaned_exclusion"]
    assert d["disp"] == "indeterminate" and str(BAD) in d["reason"]


def test_fleet_truth_build_never_raises_and_says_why(raising, monkeypatch):
    """Already safe before r7e #2 (its build is wrapped); pinned so a refactor
    cannot turn the raise into a silent fleet of one."""
    import utils.fleet_truth_collector as ftc
    monkeypatch.setattr("mini_dudeai.rollup.resolve_fleet_hosts", _boom)
    with pytest.raises(FleetHostsUnreadable):
        ftc.collect_snapshots()
