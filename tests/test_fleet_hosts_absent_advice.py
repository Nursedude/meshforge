"""No fleet list (resolver rc 1) — every consumer says the resolver's OWN
reason AND advice that can work, from ONE implementation
(``utils.fleet_hosts.explain_absent``; shell: ``fleet_hosts_absent_say``).

Double-tap 2026-11-06 (live, tree 7af8bb25): ``MESHFORGE_FLEET_HOSTS`` set to
a MISSING path -> ``fleet_status.sh`` exited 2 (right) but advised "Create
one at $HOME/.config/meshforge/fleet_hosts" — FALSE: a set override is
authoritative, so that file would never be read. Six scripts hardcoded their
own text; the resolver already knew the real cause. Non-author review r1
(same day) widened it: HOME unset, a sister repo's per-repo name, two
overrides set, a relative ``cp`` path, the rollup pane's python copy.

Isolation (memory: red-run fixtures must not reach the fleet): HOME is a
scratch dir, the override names a ``*.invalid`` path, every network tool on
PATH is a stub that records a call and fails, and a box carrying a real
/etc/meshforge tier SKIPS the script runs (the /etc tier cannot be
redirected from a script, and would resolve a real fleet).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from utils.fleet_hosts import explain_absent  # noqa: E402

SCRIPTS = ["fleet_status", "fleet_sync", "fleet_pull", "aredn_config_audit",
           "fleet_diag_bundle", "lab_traffic_rollup", "fleet_runtime_health"]
NET_TOOLS = ("ssh", "scp", "rsync", "curl", "wget", "ping", "nc", "ncat",
             "socat", "sshpass", "nmap")
EXAMPLE = ROOT / "contrib" / "fleet" / "fleet_hosts.example"

_etc_tier = [p for p in (Path("/etc/meshforge/fleet_hosts"),
                         Path("/etc/meshforge/fleet_hosts.meshforge"))
             if os.path.lexists(p)]
needs_no_etc_tier = pytest.mark.skipif(
    bool(_etc_tier), reason="this box carries %s - the scripts would resolve "
    "a REAL fleet; never run them here" % _etc_tier)


def _stubs(tmp_path: Path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    for name in NET_TOOLS:
        p = d / name
        p.write_text("#!/bin/sh\necho \"$0 $*\" >> \"%s\"\nexit 99\n"
                     % (tmp_path / "net_called"))
        p.chmod(0o755)
    return d


def _run(script: str, tmp_path: Path, env_extra: dict, home: bool = True):
    env = {"PATH": "%s:%s" % (_stubs(tmp_path), os.environ.get("PATH", "/usr/bin:/bin")),
           "REPO_DIR": str(ROOT)}
    if home:
        h = tmp_path / "home"
        h.mkdir(exist_ok=True)
        env["HOME"] = str(h)
    env.update(env_extra)
    r = subprocess.run(["bash", str(ROOT / "scripts" / f"{script}.sh")],
                       env=env, capture_output=True, text=True, timeout=60,
                       cwd=str(tmp_path))
    called = tmp_path / "net_called"
    assert not called.exists(), "a network tool was invoked: %s" % called.read_text()
    return r


@needs_no_etc_tier
@pytest.mark.parametrize("script", SCRIPTS)
def test_missing_override_names_it_and_never_says_create_home_list(script, tmp_path):
    missing = str(tmp_path / "fleet_hosts.invalid")
    r = _run(script, tmp_path, {"MESHFORGE_FLEET_HOSTS": missing})
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert missing in r.stderr and "does not exist" in r.stderr, r.stderr
    assert "authoritative" in r.stderr, r.stderr
    assert ".config/meshforge/fleet_hosts" not in r.stderr, r.stderr
    assert "Create one" not in r.stderr, r.stderr


@needs_no_etc_tier
@pytest.mark.parametrize("script", SCRIPTS)
def test_no_override_says_what_was_checked_and_where_to_create(script, tmp_path):
    r = _run(script, tmp_path, {})
    assert r.returncode == 2, (r.returncode, r.stderr)
    home_list = str(tmp_path / "home" / ".config" / "meshforge" / "fleet_hosts")
    assert "checked " in r.stderr and home_list in r.stderr, r.stderr
    assert "Create one at %s" % home_list in r.stderr, r.stderr
    # review r1 #10: no "no fleet host list - no fleet list found" stutter
    assert r.stderr.count("no fleet") == 1, r.stderr


@needs_no_etc_tier
def test_home_unset_points_at_etc_never_at_a_home_list(tmp_path):
    """review r1 #1: HOME unset skips the user tiers — "create ~/..." can't work."""
    r = _run("fleet_status", tmp_path, {}, home=False)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "HOME is unset" in r.stderr, r.stderr
    assert "Create /etc/meshforge/fleet_hosts" in r.stderr, r.stderr
    assert ".config/meshforge" not in r.stderr, r.stderr


# ── explain_absent: THE words, pure (env + etc_dir injected) ────────────

def test_both_overrides_set_advice_names_both(tmp_path):
    """review r1 #7: unsetting only the named one leaves the other in charge."""
    env = {"HOME": str(tmp_path), "MESHFORGE_FLEET_HOSTS": "/x/a.invalid",
           "FLEET_HOSTS": "/x/b.invalid"}
    why, advice = explain_absent("/opt/meshforge", env=env, etc_dir=str(tmp_path / "etc"))
    assert "/x/a.invalid" in why
    assert "$MESHFORGE_FLEET_HOSTS and $FLEET_HOSTS" in " ".join(advice)


def test_legacy_alias_alone_is_authoritative_too(tmp_path):
    env = {"HOME": str(tmp_path), "FLEET_HOSTS": "/x/b.invalid"}
    why, advice = explain_absent("/opt/meshforge", env=env, etc_dir=str(tmp_path / "etc"))
    assert "$FLEET_HOSTS=/x/b.invalid" in why
    assert not any(".config/meshforge" in a for a in advice)


def test_sister_repo_is_steered_to_its_per_repo_name(tmp_path):
    """review r1 #4: a meshanchor deploy must not be told to create the
    generic list — that IS MeshForge's fleet."""
    env = {"HOME": str(tmp_path)}
    _why, advice = explain_absent("/opt/meshanchor", env=env, etc_dir=str(tmp_path / "etc"))
    text = "\n".join(advice)
    per_repo = str(tmp_path / ".config" / "meshforge" / "fleet_hosts.meshanchor")
    assert "Create one at %s " % per_repo in advice[0]   # the per-repo name LEADS
    generic = str(tmp_path / ".config" / "meshforge" / "fleet_hosts")
    assert not any(a.startswith("Create one at %s " % generic) for a in advice)
    if EXAMPLE.is_file():
        assert "cp %s %s" % (EXAMPLE, per_repo) in text


def test_meshforge_gets_no_per_repo_detour(tmp_path):
    _why, advice = explain_absent("/opt/meshforge", env={"HOME": str(tmp_path)},
                                  etc_dir=str(tmp_path / "etc"))
    assert not any("fleet_hosts.meshforge" in a for a in advice)


@pytest.mark.skipif(not EXAMPLE.is_file(), reason="no example file in this tree")
def test_cp_line_is_absolute_from_any_cwd(tmp_path):
    """review r1 #8: a repo-relative cp fails from $HOME."""
    _why, advice = explain_absent("/opt/meshforge", env={"HOME": str(tmp_path)},
                                  etc_dir=str(tmp_path / "etc"))
    cp = [a for a in advice if a.strip().startswith("cp ")]
    assert cp and cp[0].split()[1] == str(EXAMPLE) and os.path.isabs(cp[0].split()[1])


def test_advice_is_one_line_per_entry_ascii(tmp_path):
    """--shell writes it to the lib line by line: no embedded newline, and
    ASCII so fsencode never depends on the locale."""
    for env in ({"HOME": str(tmp_path)}, {}, {"MESHFORGE_FLEET_HOSTS": "/x.invalid"}):
        why, advice = explain_absent("/opt/meshanchor", env=env, etc_dir=str(tmp_path))
        for line in [why] + advice:
            assert "\n" not in line and line.isascii(), line


def test_rollup_pane_uses_the_resolver_words(monkeypatch, tmp_path):
    """review r1 #5: the python consumer carried the same false advice."""
    import utils.fleet_hosts as fh
    calls = []
    monkeypatch.setattr(fh, "explain_absent",
                        lambda *a, **k: calls.append(1) or ("WHY-MARK", ["ADVICE-MARK"]))
    import mini_dudeai.rollup as rollup
    # pin: NO list resolves (never this box's real one)
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(tmp_path / "x.invalid"))
    seen = {}
    monkeypatch.setattr(rollup, "_main_render",
                        lambda args, now, no_data: seen.setdefault("t", no_data) and 0)
    rollup.main([])
    assert calls and "WHY-MARK" in seen["t"] and "ADVICE-MARK" in seen["t"]
    assert ".config/meshforge/fleet_hosts" not in seen["t"]


def test_one_message_no_consumer_hardcodes_its_own_advice():
    """review r1 #6/#9: the seam — every shell consumer of the lib, not a
    fixed list. Advice text lives in utils/fleet_hosts.py only."""
    offenders = []
    for p in sorted((ROOT / "scripts").glob("*.sh")):
        t = p.read_text(errors="replace")
        if "fleet_hosts_resolve" not in t:
            continue
        for needle in ("Create one at", "create ~/.config/meshforge/fleet_hosts"):
            if needle in t:
                offenders.append("%s: %r" % (p.name, needle))
    assert not offenders, offenders
    rollup = (ROOT / "src" / "mini_dudeai" / "rollup.py").read_text()
    assert "create ~/.config/meshforge/fleet_hosts" not in rollup


def test_rollup_pane_never_calls_a_list_it_just_read_absent(monkeypatch, tmp_path, capsys):
    """MA port review (2026-11-06, REPRODUCED there): the pane printed
    explain_absent() whenever postures was empty — but a list that EXISTS
    and names only this box (or nobody reachable) also yields no postures.
    It then said "no fleet list found ... Create one at <that very file>"."""
    import utils.fleet_hosts as fh
    import mini_dudeai.rollup as rollup
    lst = tmp_path / "fleet_hosts"
    lst.write_text("localhost\n")
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(lst))
    monkeypatch.setattr(fh, "explain_absent",
                        lambda *a, **k: pytest.fail("explain_absent for a list that exists"))
    monkeypatch.setattr(rollup, "collect_fleet", lambda now: [])
    assert rollup.main([]) == 0
    out = capsys.readouterr().out
    assert str(lst) in out and "names no other box" in out, out
    assert "Create" not in out and "does not exist" not in out, out


def test_rollup_pane_absent_list_still_explains(monkeypatch, tmp_path, capsys):
    import mini_dudeai.rollup as rollup
    missing = tmp_path / "x.invalid"
    monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(missing))
    monkeypatch.setattr(rollup, "collect_fleet", lambda now: [])
    assert rollup.main([]) == 0
    out = capsys.readouterr().out
    assert str(missing) in out and "authoritative" in out, out
