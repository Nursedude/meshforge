"""scripts/fork_roll_install.py — step 1 of an rns/lxmf fork roll, from the pin.

2026-10-16: the mf.11 and mf.12 rolls installed from a SCRATCHPAD script,
hand-edited per roll (SHA literal, an lxmf-only "fix marker" grep for the
verify). It lived only in one session's tmp dir — the next roll would have
re-derived it from memory. Landed with the pin as its only input: the
version comes from the `# MF-FORK-PIN` line (rns_version_check's own
parser), the URL@SHA from the requirement line, and the verify is pip's own
record of what it installed (dist-info version + direct_url.json commit_id),
which needs no per-roll marker.

Pinned here: the survey never ADDS an env; each env installs as its OWNER
with its OWN python version; a mismatched version or commit fails; an env
the script cannot classify is UNKNOWN, never skipped silently.
"""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "fork_roll_install", ROOT / "scripts" / "fork_roll_install.py")
fri = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fri)

SHA = "4a4880af2ffa580afa9c8a9594ae26802c80ccc3"
PIN = ("# MF-FORK-PIN rns 1.3.8+mf.5\n"
       "# MF-FORK-PIN lxmf 1.0.1+mf.12\n"
       "rns @ git+https://github.com/Nursedude/reticulum.git@3f91b93bdb1b7e994a84561f784c67ef4ad14fd3\n"
       f"lxmf @ git+https://github.com/Nursedude/lxmf.git@{SHA}\n")


@pytest.fixture
def req(tmp_path):
    p = tmp_path / "rns.txt"
    p.write_text(PIN)
    return p


# ---- the pin ----------------------------------------------------------------

def test_pin_comes_from_the_requirements_file(req):
    pin = fri.read_pin("lxmf", req)
    assert pin.version == "1.0.1+mf.12"
    assert pin.sha == SHA
    assert pin.spec == f"lxmf @ git+https://github.com/Nursedude/lxmf.git@{SHA}"


def test_unknown_dist_or_short_sha_refuses(req, tmp_path):
    with pytest.raises(fri.PinError):
        fri.read_pin("nomadnet", req)
    bad = tmp_path / "bad.txt"
    bad.write_text("# MF-FORK-PIN lxmf 1.0.1+mf.12\nlxmf @ git+https://x/lxmf.git@4a4880af\n")
    with pytest.raises(fri.PinError):
        fri.read_pin("lxmf", bad)


# ---- classifying an env -------------------------------------------------------

@pytest.mark.parametrize("site,kind,py", [
    ("/home/u/.local/lib/python3.13/site-packages", "user-site", "/usr/bin/python3.13"),
    ("/root/.local/lib/python3.13/site-packages", "user-site", "/usr/bin/python3.13"),
    ("/usr/local/lib/python3.11/dist-packages", "system", "/usr/bin/python3.11"),
    ("/opt/meshforge/venv/lib/python3.13/site-packages", "venv", "/opt/meshforge/venv/bin/python"),
    ("/home/u/.local/share/pipx/venvs/nomadnet/lib/python3.13/site-packages", "pipx",
     "/home/u/.local/share/pipx/venvs/nomadnet/bin/python"),
])
def test_each_env_installs_with_its_own_interpreter(site, kind, py):
    env = fri.classify(site)
    assert env.kind == kind
    assert env.python == py


def test_an_unrecognised_site_is_unknown_not_skipped():
    assert fri.classify("/srv/odd/lib/site-packages").kind == "unknown"


def test_install_argv_never_pulls_deps_and_names_the_exact_spec(req):
    pin = fri.read_pin("lxmf", req)
    for site in ("/home/u/.local/lib/python3.13/site-packages",
                 "/usr/local/lib/python3.13/dist-packages",
                 "/opt/meshforge/venv/lib/python3.13/site-packages",
                 "/home/u/.local/share/pipx/venvs/nomadnet/lib/python3.13/site-packages"):
        argv = fri.install_argv(fri.classify(site), pin, owner="u")
        assert "--no-deps" in argv and pin.spec in argv
        assert argv[:4] == ["sudo", "-n", "-u", "u"] or argv[:2] == ["sudo", "-n"]


def test_user_site_installs_as_its_owner_with_user_flag(req):
    pin = fri.read_pin("lxmf", req)
    argv = fri.install_argv(fri.classify("/home/u/.local/lib/python3.13/site-packages"),
                            pin, owner="u")
    assert argv[:6] == ["sudo", "-n", "-u", "u", "-H", "/usr/bin/python3.13"]
    assert "--user" in argv and "--break-system-packages" in argv


def test_pipx_installs_through_pipx_runpip_as_its_owner(req):
    pin = fri.read_pin("lxmf", req)
    argv = fri.install_argv(fri.classify(
        "/home/u/.local/share/pipx/venvs/nomadnet/lib/python3.13/site-packages"), pin, owner="u")
    assert argv[:7] == ["sudo", "-n", "-u", "u", "-H", "pipx", "runpip"]
    assert argv[7] == "nomadnet"


# ---- verifying what pip recorded -------------------------------------------------

def _dist_info(site, ver, sha):
    d = site / f"lxmf-{ver}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(f"Name: lxmf\nVersion: {ver}\n")
    if sha is not None:
        (d / "direct_url.json").write_text(json.dumps(
            {"url": "https://github.com/Nursedude/lxmf.git",
             "vcs_info": {"commit_id": sha, "vcs": "git"}}))
    return d


def test_verify_passes_only_on_version_and_commit(req, tmp_path):
    pin = fri.read_pin("lxmf", req)
    _dist_info(tmp_path / "a", "1.0.1+mf.12", SHA)
    _dist_info(tmp_path / "b", "1.0.1+mf.11", SHA)
    _dist_info(tmp_path / "c", "1.0.1+mf.12", "b8357d8cfd818f92e64dc53c08b18a10b642ba34")
    _dist_info(tmp_path / "d", "1.0.1+mf.12", None)
    assert fri.verify(str(tmp_path / "a"), pin) == (True, "1.0.1+mf.12 @ 4a4880af")
    assert fri.verify(str(tmp_path / "b"), pin)[0] is False
    assert fri.verify(str(tmp_path / "c"), pin)[0] is False
    ok, why = fri.verify(str(tmp_path / "d"), pin)
    assert ok is False and "direct_url" in why


def test_two_dist_infos_in_one_site_fail(req, tmp_path):
    """A half-finished pip leaves two; which one imports is undefined."""
    pin = fri.read_pin("lxmf", req)
    _dist_info(tmp_path / "a", "1.0.1+mf.12", SHA)
    _dist_info(tmp_path / "a", "1.0.1+mf.11", SHA)
    ok, why = fri.verify(str(tmp_path / "a"), pin)
    assert ok is False and "2 dist-info" in why


# ---- the run ----------------------------------------------------------------------

def _run(sites, req, apply, install_rc=0, verify=None, owner="u"):
    lines = []
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)

        class R:
            returncode = install_rc
            stdout = "Successfully installed lxmf-1.0.1+mf.12\n"
            stderr = ""
        return R()

    with patch.object(fri, "survey", return_value=sites), \
         patch.object(fri, "_owner", return_value=owner), \
         patch.object(fri, "verify", side_effect=verify or (lambda s, p: (True, "ok"))), \
         patch.object(fri.subprocess, "run", side_effect=fake_run):
        rc = fri.run("lxmf", req, apply=apply, out=lines.append)
    return rc, calls, "\n".join(lines)


SITES = ["/home/u/.local/lib/python3.13/site-packages",
         "/opt/meshforge/venv/lib/python3.13/site-packages"]


def test_dry_run_installs_nothing(req):
    rc, calls, text = _run(SITES, req, apply=False)
    assert calls == [] and rc == 0
    assert "1.0.1+mf.12" in text and SITES[0] in text


def test_apply_installs_every_surveyed_env_then_verifies(req):
    rc, calls, _ = _run(SITES, req, apply=True)
    assert len(calls) == 2 and rc == 0


def test_no_env_carries_the_dist_is_not_an_install(req):
    rc, calls, text = _run([], req, apply=True)
    assert calls == [] and rc == 0 and "no env carries lxmf" in text


def test_failed_install_or_failed_verify_is_rc1(req):
    rc, _, _ = _run(SITES, req, apply=True, install_rc=1)
    assert rc == 1
    rc, _, text = _run(SITES, req, apply=True,
                       verify=lambda s, p: (False, "1.0.1+mf.11 @ b8357d8c"))
    assert rc == 1 and "MISMATCH" in text


def test_unknown_env_is_rc2_and_not_installed(req):
    rc, calls, text = _run(["/srv/odd/lib/site-packages"], req, apply=True)
    assert calls == [] and rc == 2 and "UNKNOWN" in text


@pytest.mark.parametrize("script", ["fork_roll_install.py", "fork_roll_restart.py"])
def test_a_root_run_redirects_bytecode_out_of_the_repo(script, monkeypatch):
    """2026-10-16: the first fleet-wide `sudo -n` dry run of fork_roll_restart
    wrote a root-owned .pyc into src/utils/__pycache__ on 9 boxes."""
    import sys as _sys
    monkeypatch.setattr(_sys, "pycache_prefix", None)
    monkeypatch.delenv("MF_ROOT_PYCACHE", raising=False)
    monkeypatch.setattr("os.geteuid", lambda: 0)
    spec = importlib.util.spec_from_file_location("x_" + script[:-3], ROOT / "scripts" / script)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    assert _sys.pycache_prefix == "/var/cache/meshforge/pycache-root"


# ---- sixth read (Fable 5.1, 2026-10-16) --------------------------------------

def test_root_pipx_venv_is_a_known_env():
    """persistent_issues names root's pipx as a real RNS env class (kiai)."""
    env = fri.classify("/root/.local/share/pipx/venvs/meshtastic/lib/python3.13/site-packages")
    assert env.kind == "pipx" and env.pipx_name == "meshtastic"
    assert env.python == "/root/.local/share/pipx/venvs/meshtastic/bin/python"


def test_survey_searches_every_root_that_may_be_its_own_filesystem():
    seen = {}

    def fake(argv, **kw):
        seen["argv"] = argv

        class R:
            returncode, stdout, stderr = 0, "/home/u/.local/lib/python3.13/site-packages/lxmf-1.dist-info\n", ""
        return R()

    with patch.object(fri.subprocess, "run", side_effect=fake):
        assert fri.survey("lxmf") == ["/home/u/.local/lib/python3.13/site-packages"]
    argv = seen["argv"]
    roots = argv[1:argv.index("-xdev")]
    assert set(roots) >= {"/", "/home", "/opt", "/root", "/usr/local"}


def test_a_find_that_errors_is_not_a_complete_survey():
    class R:
        returncode, stdout, stderr = 1, "", "find: '/x': Input/output error\n"
    with patch.object(fri.subprocess, "run", return_value=R()):
        with pytest.raises(fri.SurveyError):
            fri.survey("lxmf")
