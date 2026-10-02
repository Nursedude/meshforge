"""scripts/hs_substrate_skew.py — running substrate vs installed substrate.

The 2026-10-02 defect: honest_status's running-code skew leg dropped every
process that loads no REPO code, so rnsd/lxmd/nomadnet running a pre-roll
RNS fork for a day read as "every ACTIVE unit current" (8 of 9 rnsd behind,
measured). These tests run the helper against REAL processes with a planted
distribution, because the attribution is the part that lies when it is wrong:
the first live runs over-reported unattended-upgrades (an env that CAN import
rns), missed nomadnet (MainPID is a tmux server), and called meshforge-maps
behind on rns (a repo that never imports it). Each of those is pinned here.
"""
import importlib.util
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "hs_substrate_skew",
    Path(__file__).resolve().parent.parent / "scripts" / "hs_substrate_skew.py")
hs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hs)

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")


# ---- pure pieces ---------------------------------------------------------

@pytest.mark.parametrize("argv,want", [
    (["/usr/bin/python3", "/home/u/.local/bin/rnsd", "--service"],
     ("script", "/home/u/.local/bin/rnsd", "scriptdir")),
    (["/v/bin/python", "-m", "mini_dudeai", "--interval", "30"],
     ("module", "mini_dudeai", "cwd")),
    (["python3", "-u", "-mLXMF.Utilities.lxmd"], ("module", "LXMF.Utilities.lxmd", "cwd")),
    (["python3", "-X", "dev", "x.py"], ("script", "x.py", "scriptdir")),
    (["python3", "-c", "pass"], (None, None, "cwd")),
])
def test_parse_entry(argv, want):
    assert hs.parse_entry(argv) == want


def test_classify_slack_and_direction():
    assert hs.classify(1000, 1003) == "behind"
    assert hs.classify(1000, 1001) == "current"   # inside slack: unknowable order
    assert hs.classify(1000, 900) == "current"


def test_judge_entry_is_judged_even_without_repo():
    data = {"dists": {"rns": ["1.3.8+mf.4", 2000.0, "/s/RNS/__init__.py"]},
            "closure": ["rns"], "repo_bound": None}
    assert hs.judge("rnsd.service", 1000.0, data, ["rns"], 2000.0) == \
        ["SB rnsd.service rns=1.3.8+mf.4 0 entry"]


def test_judge_importable_is_not_imported():
    """unattended-upgrades on the system python CAN import rns. Not judged."""
    data = {"dists": {"rns": ["1.3.8+mf.4", 2000.0, "/s/RNS/__init__.py"]},
            "closure": ["python-apt"], "repo_bound": None}
    # Not judged (never SB/SC) — but disclosed as SX, so it is not silence.
    assert hs.judge("unattended-upgrades.service", 1000.0, data, ["rns"], 2000.0) == \
        ["SX unattended-upgrades.service rns"]


def test_judge_repo_that_never_imports_is_not_judged(tmp_path, monkeypatch):
    """meshforge-maps is repo-bound but has no `import RNS` anywhere."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("import json\n# mentions RNS in prose\n")
    hs._IMPORTS.clear()
    data = {"dists": {"rns": ["1", 2000.0, "/s/RNS/__init__.py"]},
            "closure": None, "repo_bound": str(tmp_path)}
    assert hs.judge("meshforge-maps.service", 1000.0, data, ["rns"], 2000.0) == \
        ["SX meshforge-maps.service rns"]
    (tmp_path / "src" / "bridge.py").write_text("def f():\n    import RNS\n")
    hs._IMPORTS.clear()
    assert hs.judge("meshforge-maps.service", 1000.0, data, ["rns"], 2000.0) == \
        ["SB meshforge-maps.service rns=1 0 repo"]


# ---- real processes, planted distribution -------------------------------

@pytest.fixture
def planted(tmp_path):
    """A site dir holding a fake `rns` dist with a console script."""
    site = tmp_path / "site"
    pkg = site / "RNS"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    di = site / "rns-9.9.9+mf.9.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: rns\nVersion: 9.9.9+mf.9\n")
    (di / "top_level.txt").write_text("RNS\n")
    (di / "entry_points.txt").write_text("[console_scripts]\nfakernsd = RNS:main\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    script = bin_ / "fakernsd"
    script.write_text("import time\nwhile True:\n    time.sleep(1)\n")
    return site, script, di, pkg


def _spawn(argv, site):
    env = dict(os.environ, PYTHONPATH=str(site))
    p = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    time.sleep(0.6)  # let the child exec its interpreter
    return p


def _run(unit, pid):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hs.main(["rns"], stdin=["user %s %d\n" % (unit, pid)])
    return buf.getvalue().split("\n")


def _future(di, pkg):
    t = time.time() + 30
    for p in (di, pkg / "__init__.py"):
        os.utime(p, (t, t))


def test_real_process_current_then_behind_after_install(planted):
    site, script, di, pkg = planted
    p = _spawn([sys.executable, str(script)], site)
    try:
        assert "SC rnsd.service rns=9.9.9+mf.9 entry" in _run("rnsd.service", p.pid)
        _future(di, pkg)   # an in-place roll lands AFTER the process started
        assert "SB rnsd.service rns=9.9.9+mf.9 0 entry" in _run("rnsd.service", p.pid)
    finally:
        p.kill()
        p.wait()


def test_wrapper_mainpid_descends_to_python_child(planted):
    """nomadnet.service: MainPID is a tmux server; python is its child."""
    site, script, di, pkg = planted
    p = _spawn(["/bin/sh", "-c", '"%s" "%s"; true' % (sys.executable, script)], site)
    try:
        _future(di, pkg)
        out = _run("nomadnet.service", p.pid)
        assert "SB nomadnet.service rns=9.9.9+mf.9 0 entry" in out
    finally:
        subprocess.run(["pkill", "-f", str(script)], timeout=10)
        p.kill()
        p.wait()


def test_first_copy_on_sys_path_wins(planted, tmp_path):
    """Two copies on one box: the process loads the FIRST on sys.path."""
    site, script, di, pkg = planted
    shadow = tmp_path / "shadow"
    (shadow / "RNS").mkdir(parents=True)
    (shadow / "RNS" / "__init__.py").write_text("")
    sd = shadow / "rns-1.0.0.dist-info"
    sd.mkdir()
    (sd / "METADATA").write_text("Metadata-Version: 2.1\nName: rns\nVersion: 1.0.0\n")
    (sd / "top_level.txt").write_text("RNS\n")
    env = dict(os.environ, PYTHONPATH="%s:%s" % (shadow, site))
    p = subprocess.Popen([sys.executable, str(script)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.6)
    try:
        _future(di, pkg)   # only the SHADOWED copy changed: the process is current
        out = _run("rnsd.service", p.pid)
        assert any(l.startswith("SC rnsd.service rns=1.0.0") for l in out), out
    finally:
        p.kill()
        p.wait()


def test_non_python_mainpid_with_no_python_children_is_silent():
    p = subprocess.Popen(["sleep", "30"])
    try:
        assert [l for l in _run("x.service", p.pid) if l] == []
    finally:
        p.kill()
        p.wait()


def test_no_watched_dists_is_unknown_not_clean():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hs.main([], stdin=[])
    assert buf.getvalue().startswith("SU ")


def test_honest_status_wires_the_ssot_and_pairs_per_record():
    """The leg reads the watched set from MF-FORK-PIN, and pairs MainPID with
    Id per RECORD (systemctl prints MainPID first: line pairing mislabelled
    rnsd as polkit on the first live run)."""
    src = (Path(__file__).resolve().parent.parent / "scripts" / "honest_status.sh").read_text()
    assert "MF-FORK-PIN" in src and "hs_substrate_skew.py" in src
    assert src.count("-v RS=") >= 2
    assert '"running substrate"' in src


# ---- reviewer A blockers (2026-10-02), each measured false green ---------

def _venv(tmp_path):
    """A real venv WITHOUT system site, holding its own fake rns."""
    import venv
    root = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, system_site_packages=False, symlinks=True).create(root)
    py = root / "bin" / "python"
    site = Path(subprocess.run(
        [str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, timeout=30).stdout.strip())
    (site / "RNS").mkdir(parents=True)
    (site / "RNS" / "__init__.py").write_text("")
    di = site / "rns-7.7.7.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: rns\nVersion: 7.7.7\n")
    (di / "top_level.txt").write_text("RNS\n")
    (di / "entry_points.txt").write_text("[console_scripts]\nfakernsd = RNS:main\n")
    return py, site, di


def test_venv_process_is_probed_in_its_own_venv(tmp_path):
    """/proc/PID/exe resolves a venv's python symlink to the base interpreter,
    which never reads pyvenv.cfg — so the probe judged a DIFFERENT copy (the
    pipx nomadnet shape). The process's own argv[0] keeps the venv."""
    py, site, di = _venv(tmp_path)
    script = tmp_path / "fakernsd"
    script.write_text("import time\nwhile True:\n    time.sleep(1)\n")
    p = subprocess.Popen([str(py), str(script)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env={"PATH": os.environ.get("PATH", "")})
    time.sleep(0.6)
    try:
        _future(di, site / "RNS")
        out = _run("nomadnet.service", p.pid)
        assert "SB nomadnet.service rns=7.7.7 0 entry" in out, out
    finally:
        p.kill()
        p.wait()


def test_repo_basis_checks_every_top_level_name(tmp_path):
    """rns ships top_level.txt = CRNS + RNS. Grepping the repo only for the
    FIRST name made every repo unit running stale RNS read 'not judged'."""
    (tmp_path / "gw.py").write_text("import RNS\n")
    hs._IMPORTS.clear()
    data = {"dists": {"rns": ["1", 2000.0, "/s/CRNS/__init__.py", ["CRNS", "RNS"]]},
            "closure": None, "repo_bound": str(tmp_path)}
    assert hs.judge("meshforge-map.service", 1000.0, data, ["rns"], 2000.0) == \
        ["SB meshforge-map.service rns=1 0 repo"]


def test_forged_probe_output_is_refused():
    """A non-root target controls its own probe's stdout: a version carrying a
    newline must not inject an SC line."""
    data = {"dists": {"rns": ["1\nSC rnsd.service rns=x entry", 2000.0, "/s/RNS/__init__.py", ["RNS"]]},
            "closure": ["rns"], "repo_bound": None}
    out = hs.judge("rnsd.service", 1000.0, data, ["rns"], 2000.0)
    assert out == ["SU rnsd.service bad-probe-output"], out


def test_probe_child_never_reads_the_unit_list(planted):
    """PYTHONINSPECT in a target's env would drop the probe into a REPL that
    eats the remaining '<scope> <unit> <pid>' lines from the shared stdin."""
    site, script, di, pkg = planted
    env = dict(os.environ, PYTHONPATH=str(site), PYTHONINSPECT="1")
    a = subprocess.Popen([sys.executable, str(script)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    b = subprocess.Popen([sys.executable, str(script)], env=dict(os.environ, PYTHONPATH=str(site)),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.6)
    r = None
    try:
        r = subprocess.run(
            [sys.executable, str(Path(hs.__file__)), "rns"],
            input="user a.service %d\nuser b.service %d\n" % (a.pid, b.pid),
            capture_output=True, text=True, timeout=60)
        assert "b.service" in r.stdout, r.stdout + r.stderr
    finally:
        for p in (a, b):
            p.kill()
            p.wait()
