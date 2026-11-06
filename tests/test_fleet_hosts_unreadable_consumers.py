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
import os
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


def test_mini_daemon_refuses_only_when_the_list_decides_the_preset(raising, monkeypatch):
    """r7f F1: with NATS configured the list picks standalone vs fleet, so an
    unreadable list must refuse the start."""
    from mini_dudeai import daemon
    monkeypatch.setenv("MINI_DUDEAI_NATS_SERVER", "nats://x:4222")
    with pytest.raises(SystemExit) as ei:
        daemon.main(["--preset", "auto"])
    assert str(BAD) in str(ei.value.code)


def test_mini_preset_rule_raises_when_nats_makes_the_list_decisive(raising):
    """The watchdog probe catches this as `unresolvable`."""
    from mini_dudeai.daemon import _resolve_preset_name
    with pytest.raises(FleetHostsUnreadable):
        _resolve_preset_name("auto", {"HOME": "/x", "MINI_DUDEAI_NATS_SERVER": "n"},
                             announce=False)


def test_mini_preset_rule_without_nats_starts_the_fleet_preset_and_says_why(
        raising, capsys):
    """r7f F1: without NATS both branches of the rule land on meshforge_fleet,
    so the list cannot change the answer — refusing would blind the night
    watcher over a permission slip. It starts, and the journal line names the
    unreadable list."""
    from mini_dudeai.daemon import _resolve_preset_name
    assert _resolve_preset_name("auto", {"HOME": "/x"}) == "meshforge_fleet"
    out = capsys.readouterr().out
    assert str(BAD) in out and "UNREADABLE" in out


def test_nomadnet_watch_import_default_is_the_unreadable_path(raising):
    from monitoring import nomadnet_silence_watch as nsw
    assert nsw._default_fleet_hosts() == BAD


def test_nomadnet_watch_set_but_missing_override_is_not_replaced_by_home(
        monkeypatch, tmp_path):
    """r7f F3: the authoritative override pointing nowhere must stay the
    default (so the run refuses) — never the operator's generic list."""
    from monitoring import nomadnet_silence_watch as nsw
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(tmp_path / "missing"))
    monkeypatch.delenv("FLEET_HOSTS", raising=False)
    assert nsw._default_fleet_hosts() == tmp_path / "missing"


def test_nomadnet_watch_directory_list_gets_the_refusal(monkeypatch, tmp_path, capsys):
    """r7f F7: a directory at the list path is unreadable, not 'create one'."""
    from monitoring import nomadnet_silence_watch as nsw
    d = tmp_path / "fleet_hosts"
    d.mkdir()
    monkeypatch.setattr(sys, "argv", ["nsw", "--fleet-hosts", str(d)])
    assert nsw.main() == 2
    err = capsys.readouterr().err
    assert "unreadable" in err and "create" not in err


def test_watchdog_orphaned_exclusion_probe_is_indeterminate(raising, tmp_path):
    from utils.watchdog_probe_core import collect_dispositions, reset_dispositions
    from utils.watchdog_probes_mini import probe_mini_rule_orphaned_exclusion
    (tmp_path / "mini_dudeai_rules.json").write_text(json.dumps({"rules": [
        {"id": "r1", "match": {"kind": "x", "subject_exclude_globs": ["*a*"]}}]}))
    reset_dispositions()
    assert probe_mini_rule_orphaned_exclusion(mini_home=str(tmp_path)) is None
    d = collect_dispositions()["mini_rule_orphaned_exclusion"]
    assert d["disp"] == "indeterminate" and str(BAD) in d["reason"]


def test_watchdog_probe_other_resolver_errors_are_indeterminate_too(monkeypatch, tmp_path):
    """r7f F10: ANY resolver failure leaves the peer universe unknowable —
    never an empty one that calls owned exclusions orphaned."""
    from utils.watchdog_probe_core import collect_dispositions, reset_dispositions
    from utils.watchdog_probes_mini import probe_mini_rule_orphaned_exclusion

    def bad(*_a, **_k):
        raise ValueError("embedded null byte")
    monkeypatch.setattr(fh, "resolve_fleet_hosts", bad)
    (tmp_path / "mini_dudeai_rules.json").write_text(json.dumps({"rules": [
        {"id": "r1", "match": {"kind": "x", "subject_exclude_globs": ["*a*"]}}]}))
    reset_dispositions()
    assert probe_mini_rule_orphaned_exclusion(mini_home=str(tmp_path)) is None
    assert collect_dispositions()["mini_rule_orphaned_exclusion"]["disp"] == "indeterminate"


def test_fleet_dup_collector_bad_bytes_and_read_errors_refuse(tmp_path, monkeypatch, capsys):
    """r7f F5: its own strict read turned a stray byte into a traceback."""
    import utils.fleet_dup_collector as fdc
    hf = tmp_path / "fleet_hosts"
    hf.write_bytes(b"moc  # caf\xe9\nmoc1\n")
    monkeypatch.setattr(fdc, "fleet_hosts_file", lambda: hf)
    seen = {}

    def views(hosts, **_k):
        seen["hosts"] = list(hosts)
        return [], []
    monkeypatch.setattr(fdc, "collect_box_views", views)
    fdc.build_fleet_dup_rollup(local_host="self")
    assert seen["hosts"] == ["self", "moc", "moc1"]

    def boom(self, *a, **k):
        raise PermissionError(13, "denied")
    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(FleetHostsUnreadable):
        fdc.build_fleet_dup_rollup(local_host="self")


def test_fleet_truth_build_never_raises_and_says_why(raising, monkeypatch):
    """Already safe before r7e #2 (its build is wrapped); pinned so a refactor
    cannot turn the raise into a silent fleet of one."""
    import utils.fleet_truth_collector as ftc
    monkeypatch.setattr("mini_dudeai.rollup.resolve_fleet_hosts", _boom)
    with pytest.raises(FleetHostsUnreadable):
        ftc.collect_snapshots()


def test_mini_daemon_pinned_preset_never_imports_the_fleet_resolver(monkeypatch):
    """r7g F3: only `auto` consults the list; a pinned preset must start
    where `utils` is not importable, as it did before r7e #2."""
    from mini_dudeai import daemon
    monkeypatch.setitem(sys.modules, "utils.fleet_hosts", None)  # import -> ImportError
    seen = {}

    def fake_import(name):
        seen["name"] = name
        raise SystemExit(0)
    monkeypatch.setattr(daemon, "_import_preset", fake_import)
    with pytest.raises(SystemExit) as ei:
        daemon.main(["--preset", "meshforge_fleet"])
    assert ei.value.code == 0 and seen["name"] == "meshforge_fleet"



# ── r11: every reader refuses a CORRUPT list by name (r10 + Fable) ───────


@pytest.fixture
def corrupt(tmp_path, monkeypatch):
    """A corrupt list whose readable entry is a RESERVED name (.invalid,
    RFC 2606) — never a real box. 2026-11-05: a red run of these tests
    against the OLD code read 'moc1' as a one-box fleet and the nomadnet
    watcher polled the real moc1 over ssh (read-only). A fixture must not
    be able to reach the fleet under ANY version of the code."""
    f = tmp_path / "fleet_hosts"
    f.write_bytes(b"box.invalid\n\0\0\0\0\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(f))
    monkeypatch.delenv("FLEET_HOSTS", raising=False)
    # and no ssh can leave this process, whatever the code under test does
    fake = tmp_path / "nossh"
    fake.mkdir()
    (fake / "ssh").write_text("#!/bin/sh\necho 'ssh stubbed by test' >&2\nexit 255\n")
    (fake / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (fake, os.environ.get("PATH", "")))
    return f


def _noreach_env(corrupt):
    return {"PATH": os.environ["PATH"], "HOME": str(corrupt.parent),
            "MESHFORGE_FLEET_HOSTS": str(corrupt)}


def test_r11_provision_role_fleet_check_is_not_ok_over_no_fleet(corrupt, capsys):
    pr = _load("provision_role")
    assert pr.main(["--fleet-check"]) == 2
    err = capsys.readouterr().err
    assert str(corrupt) in err and "not a host name" in err


def test_r11_tui_membership_is_unreadable_never_standalone(corrupt):
    from launcher_tui.handlers import _fleet_membership_core as fm
    st = fm.membership_state(str(corrupt))
    assert st["mode"] == "unreadable" and str(corrupt) in st["error"]
    assert fm.membership_state(str(corrupt) + ".absent")["mode"] == "standalone"


def test_r11_tui_edit_cannot_overwrite_a_list_it_could_not_read(corrupt):
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    before = corrupt.read_bytes()
    seen = []

    class _D:
        def msgbox(self, title, text): seen.append(text)
        def inputbox(self, *a, **k): raise AssertionError("Edit opened over an unreadable list")

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_edit(fm, fm.membership_state(str(corrupt)))
    assert corrupt.read_bytes() == before and "Refused" in seen[0]


@pytest.mark.timeout(30)
def test_r11_nomadnet_says_unreadable_not_create(corrupt, monkeypatch, capsys):
    from monitoring import nomadnet_silence_watch as nsw
    monkeypatch.setattr(sys, "argv", ["nsw", "--fleet-hosts", str(corrupt)])
    assert nsw.main() == 2
    err = capsys.readouterr().err
    assert "not a host name" in err and "create" not in err


def test_r11_nomadnet_a_comment_byte_is_harmless(tmp_path):
    """Fable r10 #1 (VERIFIED): a 0xFF inside a comment was an uncaught
    UnicodeDecodeError — the strict read."""
    from monitoring import nomadnet_silence_watch as nsw
    f = tmp_path / "fleet_hosts"
    f.write_bytes(b"a.invalid  # caf\xff\nb.invalid\n")
    assert nsw._boxes_from_fleet_hosts(f, include_self=False) == ["a.invalid", "b.invalid"]


def test_r11_fleet_naming_audit_refuses_rc2_not_a_traceback(corrupt, monkeypatch, capsys):
    fna = _load("fleet_naming_audit")
    monkeypatch.setattr(fna, "fleet_hosts_file", lambda: corrupt)
    assert fna.main([]) == 2
    assert "not a host name" in capsys.readouterr().err


def test_r11_fleet_posture_guard_names_the_refusal(corrupt):
    fp = _load("fleet_posture")
    bridges, note = fp._bridge_boxes()
    assert bridges is None and "FleetHostsUnreadable" in note


def test_r11_fleet_platform_refuses_a_one_box_fleet(corrupt, monkeypatch, capsys):
    fpl = _load("fleet_platform")
    monkeypatch.setattr(fpl, "load_catalog", lambda *_a: (object(), []))
    monkeypatch.setattr(fpl, "load_declarations", lambda *_a: ({}, "ok"))
    monkeypatch.setattr(fpl, "observe", lambda *_a: pytest.fail("observed a one-box fleet"))
    args = type("A", (), {"catalog": None, "declared": None, "local": False})()
    assert fpl.cmd_show(args) == 2
    assert "not a host name" in capsys.readouterr().out


def test_r11_dep_advisory_names_the_file(corrupt):
    dac = _load("dep_advisory_check")
    hosts, err = dac.read_fleet_hosts(str(corrupt))
    assert hosts == [] and str(corrupt) in err and "not a host name" in err


def test_r11_fleet_os_upgrade_survey_refuses_not_an_empty_survey(corrupt):
    import subprocess
    p = subprocess.run(["bash", str(ROOT / "scripts" / "fleet_os_upgrade.sh"), "survey"],
                       capture_output=True, text=True, timeout=60, env=_noreach_env(corrupt))
    assert p.returncode != 0 and "BOX" not in p.stdout, (p.returncode, p.stdout, p.stderr)
    assert "not a host name" in p.stderr


def test_r11_soak_check_refuses_rather_than_checking_nobody(corrupt):
    import subprocess
    p = subprocess.run(["bash", str(ROOT / "scripts" / "soak_check_fleet_2026_05_14.sh"), "check"],
                       capture_output=True, text=True, timeout=60, env=_noreach_env(corrupt))
    assert p.returncode == 2 and "refusing" in p.stderr, (p.returncode, p.stderr)



# ── r13 (Fable r12): the three private SHELL readers + digest + TUI ──────


def _sh_env(tmp_path, **extra):
    nossh = tmp_path / "nossh"
    nossh.mkdir(exist_ok=True)
    (nossh / "ssh").write_text("#!/bin/sh\nexit 255\n")
    (nossh / "ssh").chmod(0o755)
    (nossh / "scp").write_text("#!/bin/sh\nexit 255\n")
    (nossh / "scp").chmod(0o755)
    stub = tmp_path / "verdict.sh"
    stub.write_text('#!/bin/sh\necho "VERDICT $1 $2 $3"\n')
    stub.chmod(0o755)
    env = {"PATH": "%s:%s" % (nossh, os.environ.get("PATH", "")), "HOME": str(tmp_path),
           "BOX_CAPTURE_VERDICT": str(stub)}
    env.update(extra)
    return env, stub


def _run_script(name, env, *args):
    import subprocess
    return subprocess.run(["bash", str(ROOT / "scripts" / name), *args],
                          capture_output=True, text=True, timeout=120, env=env)


@pytest.mark.parametrize("name,var", [
    ("fleet_registry_sync.sh", "FLEET_REGISTRY_SYNC_HOSTS"),
    ("fleet_posture_sync.sh", "FLEET_POSTURE_SYNC_HOSTS"),
])
def test_r13_sync_organs_never_ok_over_an_unreadable_list(tmp_path, name, var):
    """Fable r12 #1 (VERIFIED) / #3: `done 3< "$HOSTS_FILE"` on an
    unreadable list ran ZERO times and the cron verdict read OK."""
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    bad = tmp_path / "fl"
    bad.write_text("a.invalid\n")
    bad.chmod(0)
    env, _ = _sh_env(tmp_path, **{var: str(bad)})
    try:
        p = _run_script(name, env)
    finally:
        bad.chmod(0o600)
    both = p.stdout + p.stderr
    assert "unreadable" in both and " OK " not in both, both


def test_r13_box_capture_refuses_an_unreadable_list(tmp_path):
    """Fable r12 #2 (VERIFIED): unreadable -> silently self-only."""
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    bad = tmp_path / "fl"
    bad.write_text("a.invalid\n")
    bad.chmod(0)
    env, _ = _sh_env(tmp_path, MESHFORGE_FLEET_HOSTS=str(bad),
                     BOX_CAPTURE_ROOT=str(tmp_path / "dest"), BOX_CAPTURE_SELF="self.invalid")
    try:
        p = _run_script("box_config_capture.sh", env)
    finally:
        bad.chmod(0o600)
    assert "VERDICT box_config_capture FAIL" in p.stdout and "unreadable" in p.stdout, p.stdout + p.stderr
    assert not (tmp_path / "dest").exists() or not any((tmp_path / "dest").rglob("*.tgz"))


def test_r13_digest_warns_on_an_empty_list(tmp_path):
    """Fable r12 #9: a list with no hosts gave a quiet one-box digest."""
    empty = tmp_path / "fl"
    empty.write_text("# nobody yet\n")
    env, _ = _sh_env(tmp_path, MESHFORGE_FLEET_HOSTS=str(empty), MESHFORGE_NTFY_TOPIC="t")
    sb = tmp_path / "nossh"
    (sb / "curl").write_text('#!/bin/sh\nprev=""; for a in "$@"; do [ "$prev" = "-d" ] && printf "%s" "$a" > "$CURL_BODY_OUT"; prev="$a"; done\nexit 0\n')
    (sb / "curl").chmod(0o755)
    for c in ("git", "apt-cache", "dpkg-query", "apt-mark", "pgrep"):
        (sb / c).write_text("#!/bin/sh\nexit 0\n")
        (sb / c).chmod(0o755)
    env["CURL_BODY_OUT"] = str(tmp_path / "body")
    p = _run_script("weekly_updates_digest.sh", env)
    body = (tmp_path / "body").read_text()
    assert p.returncode == 2 and "lists no hosts" in body, (p.returncode, body)


def test_r13_tui_names_the_list_the_fleet_tools_use(tmp_path, monkeypatch):
    """Fable r12 #6: with the list in another tier the TUI said STANDALONE
    — "fleet tools cover this box only" — while every tool used a fleet."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    other = tmp_path / "per-repo-list"
    other.write_text("a.invalid\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(other))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(tmp_path / "wizard"))
    st = fm.membership_state()
    assert st["mode"] == "standalone" and st["resolved_file"] == str(other)


# ── r15 (Fable r14) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("content,override", [
    (b"", True),                       # zero-byte (power-loss truncation)
    (b"# nobody yet\n", True),         # comments only
    (None, True),                      # override pointing NOWHERE
])
def test_r15_box_capture_never_ok_over_one_box(tmp_path, content, override):
    """Fable r14 F1 (VERIFIED): all three read 'OK captured 1 box(es)'."""
    fl = tmp_path / "fl"
    if content is not None:
        fl.write_bytes(content)
    env, _ = _sh_env(tmp_path, MESHFORGE_FLEET_HOSTS=str(fl),
                     BOX_CAPTURE_ROOT=str(tmp_path / "dest"), BOX_CAPTURE_SELF="self.invalid")
    p = _run_script("box_config_capture.sh", env)
    assert "VERDICT box_config_capture FAIL" in p.stdout and "NOT captured" in p.stdout, p.stdout + p.stderr
    assert not (tmp_path / "dest").exists()


def test_r15_digest_names_an_existing_empty_list_as_existing(tmp_path):
    """Fable r14 O3: it read 'NO fleet hosts file found (<file> lists no hosts)'."""
    empty = tmp_path / "fl"
    empty.write_text("# nobody yet\n")
    env, _ = _sh_env(tmp_path, MESHFORGE_FLEET_HOSTS=str(empty), MESHFORGE_NTFY_TOPIC="t")
    sb = tmp_path / "nossh"
    (sb / "curl").write_text('#!/bin/sh\nprev=""; for a in "$@"; do [ "$prev" = "-d" ] && printf "%s" "$a" > "$CURL_BODY_OUT"; prev="$a"; done\nexit 0\n')
    (sb / "curl").chmod(0o755)
    for c in ("git", "apt-cache", "dpkg-query", "apt-mark", "pgrep"):
        (sb / c).write_text("#!/bin/sh\nexit 0\n")
        (sb / c).chmod(0o755)
    env["CURL_BODY_OUT"] = str(tmp_path / "body")
    _run_script("weekly_updates_digest.sh", env)
    body = (tmp_path / "body").read_text()
    assert "lists no hosts" in body and "NO fleet hosts file found" not in body, body


def test_r15_tui_fleet_mode_says_when_the_tools_read_another_file(tmp_path, monkeypatch):
    """Fable r14 F2 (VERIFIED): FLEET mode showed the wizard's list while
    every tool read another one; Edit must warn too."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    wiz = tmp_path / "wizard"
    wiz.write_text("wiz1.invalid\n")
    other = tmp_path / "tools-list"
    other.write_text("a.invalid\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(other))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(wiz))
    st = fm.membership_state()
    assert st["mode"] == "fleet" and str(other) in fm.tools_note(st)
    heads, prompts = [], []

    class _D:
        def menu(self, title, text, choices):
            heads.append(text)
            return "back"
        def inputbox(self, title, text, init=""):
            prompts.append(text)
            return None
        def msgbox(self, *a): pass

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_menu()
    assert "Mode: FLEET" in heads[0] and str(other) in heads[0] and "NOT this file" in heads[0]
    h._membership_edit(fm, st)
    assert str(other) in prompts[0]


def test_r15_tui_a_corrupt_disabled_list_is_said_not_silently_empty(tmp_path, monkeypatch):
    """Fable r14 O4."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    wiz = tmp_path / "wizard"
    (tmp_path / ("wizard" + fm.DISABLED_SUFFIX)).write_bytes(b"\0\0\0\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(tmp_path / "none"))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(wiz))
    prompts = []

    class _D:
        def inputbox(self, title, text, init=""):
            prompts.append(text)
            return None
        def msgbox(self, *a): pass

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_edit(fm, fm.membership_state())
    assert "could not be read" in prompts[0]



# ── r17 (Fable r16) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("content", [b"", b"# nobody\n"])
def test_r17_health_never_clears_red_over_an_empty_list(tmp_path, content):
    """Fable r16 F2 (VERIFIED): an empty list -> 0 red -> `rm -f RED` -> rc 0;
    a zero-byte truncation cleared a standing fleet RED twice a day."""
    fl = tmp_path / "fl"
    fl.write_bytes(content)
    red = tmp_path / ".meshforge-fleet-RED"
    red.write_text("standing RED\n")
    env, _ = _sh_env(tmp_path, MESHFORGE_FLEET_HOSTS=str(fl))
    p = _run_script("fleet_runtime_health.sh", env)
    assert p.returncode == 2 and "lists no hosts" in p.stderr, (p.returncode, p.stderr)
    assert red.read_text() == "standing RED\n"


def test_r17_sync_refuses_an_empty_list_before_doing_anything(tmp_path):
    """Fable r16 F3: the 6h --memory-only cron reported success syncing
    nobody (fleet_pull already refused this case)."""
    fl = tmp_path / "fl"
    fl.write_text("# nobody\n")
    env, _ = _sh_env(tmp_path, MESHFORGE_FLEET_HOSTS=str(fl))
    p = _run_script("fleet_sync.sh", env, "--memory-only")
    assert p.returncode == 2 and "lists no hosts" in p.stderr, (p.returncode, p.stdout, p.stderr)


def test_r17_tui_says_the_tools_see_no_list_when_an_override_points_nowhere(tmp_path, monkeypatch):
    """Fable r16 F1 (VERIFIED): wizard has hosts + override -> nowhere read
    as plain FLEET while every tool saw no list."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    wiz = tmp_path / "wizard"
    wiz.write_text("moc1.invalid\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(tmp_path / "nope"))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(wiz))
    st = fm.membership_state()
    assert st["mode"] == "fleet" and "see NO list" in fm.tools_note(st)


def test_r17_tui_standalone_head_and_disabled_hint_come_back(tmp_path, monkeypatch):
    """Fable r16 O1: with the tools AGREEING (no list anywhere) the true
    'this box only' head and the disabled-list hint were dropped."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    wiz = tmp_path / "wizard"
    (tmp_path / ("wizard" + fm.DISABLED_SUFFIX)).write_text("a.invalid\n")
    monkeypatch.delenv("MESHFORGE_FLEET_HOSTS", raising=False)
    monkeypatch.delenv("FLEET_HOSTS", raising=False)
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(wiz))
    import utils.fleet_hosts as fhm
    monkeypatch.setattr(fhm, "resolve_fleet_hosts_file", lambda *a, **k: None)
    heads = []

    class _D:
        def menu(self, title, text, choices):
            heads.append(text)
            return "back"

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_menu()
    assert "STANDALONE" in heads[0] and "Edit restores it" in heads[0], heads[0]


def test_r17_tui_unreadable_head_says_it_once(tmp_path, monkeypatch):
    """Fable r16 O2: the same refusal twice in two vocabularies."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    wiz = tmp_path / "wizard"
    wiz.mkdir()
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(wiz))
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(wiz))
    st = fm.membership_state()
    assert st["mode"] == "unreadable" and fm.tools_note(st) == ""



# ── r19 (Fable r18): resolve-then-READ consumers refuse an empty list too ──


@pytest.fixture
def empty_list(tmp_path, monkeypatch):
    f = tmp_path / "fleet_hosts"
    f.write_text("# all boxes retired\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(f))
    monkeypatch.delenv("FLEET_HOSTS", raising=False)
    nossh = tmp_path / "nossh"
    nossh.mkdir()
    (nossh / "ssh").write_text("#!/bin/sh\nexit 255\n")
    (nossh / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (nossh, os.environ.get("PATH", "")))
    return f


def test_r19_fleet_check_is_not_ok_over_an_empty_list(empty_list, capsys):
    pr = _load("provision_role")
    assert pr.main(["--fleet-check"]) == 2
    assert "lists no hosts" in capsys.readouterr().err


def test_r19_rns_alignment_is_not_aligned_over_an_empty_list(empty_list, capsys):
    ra = _load("rns_alignment")
    assert ra._audit_fleet() == 2
    assert "lists no hosts" in capsys.readouterr().err


def test_r19_naming_audit_is_not_zero_findings_over_an_empty_list(empty_list, monkeypatch, capsys):
    fna = _load("fleet_naming_audit")
    monkeypatch.setattr(fna, "fleet_hosts_file", lambda: empty_list)
    assert fna.main([]) == 2
    assert "lists no hosts" in capsys.readouterr().err


def test_r19_dup_collector_holds_the_prior_rollup_over_an_empty_list(empty_list, tmp_path, capsys):
    import utils.fleet_dup_collector as fdc
    out = tmp_path / "view.json"
    out.write_text('{"prior": true}')
    assert fdc.main(["--out", str(out), "--quiet"]) == 2
    assert out.read_text() == '{"prior": true}'


@pytest.mark.timeout(30)   # on OLD code this is a forever watch loop
def test_r19_nomadnet_refuses_an_empty_list(empty_list, monkeypatch, capsys):
    from monitoring import nomadnet_silence_watch as nsw
    monkeypatch.setattr(sys, "argv", ["nsw", "--fleet-hosts", str(empty_list)])
    assert nsw.main() == 2
    assert "lists no hosts" in capsys.readouterr().err


def test_r19_tui_offers_the_cure_for_an_empty_list(tmp_path, monkeypatch):
    """Fable r18 F4 (VERIFIED): every tool refused the empty file while the
    TUI named a 'Go standalone' it did not offer."""
    from launcher_tui.handlers import _fleet_membership_core as fm
    from launcher_tui.handlers.fleet_provision import FleetProvisionHandler
    wiz = tmp_path / "wizard"
    wiz.write_text("")
    monkeypatch.delenv("MESHFORGE_FLEET_HOSTS", raising=False)
    monkeypatch.delenv("FLEET_HOSTS", raising=False)
    monkeypatch.setattr(fm, "default_hosts_path", lambda: str(wiz))
    seen = []

    class _D:
        def menu(self, title, text, choices):
            seen.append([c[0] for c in choices])
            return "back"

    h = FleetProvisionHandler.__new__(FleetProvisionHandler)
    h.ctx = type("C", (), {"dialog": _D()})()
    h._membership_menu()
    assert "standalone" in seen[0], seen


def test_r19_going_standalone_never_destroys_a_saved_list(tmp_path):
    from launcher_tui.handlers import _fleet_membership_core as fm
    wiz = tmp_path / "wizard"
    wiz.write_text("")
    saved = tmp_path / ("wizard" + fm.DISABLED_SUFFIX)
    saved.write_text("a.invalid\nb.invalid\n")
    assert fm.declare_standalone(str(wiz)) == str(saved)
    assert not wiz.exists() and saved.read_text() == "a.invalid\nb.invalid\n"
