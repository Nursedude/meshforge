"""r13.6 — "the config rnsd loads" has ONE answer, and the installer's gate
and its client-key COPY share it (Fable r17 #1, the r15 #1 KIND again).

r13.5's gate asked the resolver (which sees ``~/.config/reticulum``) while
the COPY block still hard-coded ``/etc/reticulum/config`` then
``~/.reticulum/config``: on an XDG box the gate passed and COPY said "client
shares rnsd's config" about a keyless file — MeshChatX started with no key.
Now ``ReticulumPaths.rnsd_config_dir_for_home`` is rnsd's order with an
EXPLICIT home and no env override (r17 NIT 3), the checker exposes it as
``--rnsd-config --home`` / ``--check --home``, and the installer resolves
RNSD_CFG ONCE and hands that one value to both. These run the REAL gate +
COPY blocks together on every layout, against the real checker with only
``/etc/reticulum`` remapped (a python3 shim on PATH).

Also pinned: every RNSConfigError built inside an ``except`` is
``from None`` (r17 NIT 5 — five such sites survived mutation), and
rns_alignment says UNKNOWN, not "rnsd cannot load", for a file THIS python
cannot judge (r17 NIT 1).
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from utils import rns_config_reader as reader  # noqa: E402
from utils.paths import ReticulumPaths  # noqa: E402

KEY = "ab" * 32
PINNED = f"[reticulum]\n  share_instance = Yes\n  rpc_key = {KEY}\n"
NOKEY = "[reticulum]\n  share_instance = Yes\n  instance_name = default\n"
TQ = f"[reticulum]\n  share_instance = Yes\n  rpc_key = '''{KEY}'''\n"
PIN = REPO / "scripts" / "rns_rpc_key_pin.py"
INSTALLER = REPO / "scripts" / "install_meshchatx.sh"

# ── layouts: which of the three configs exist (rnsd's order) ────────────
# name: (etc, xdg, trad) contents (None = absent); want = which dir rnsd loads
LAYOUTS = {
    "etc-only":       (PINNED, None, None, "etc"),
    "etc-beats-xdg":  (PINNED, NOKEY, None, "etc"),
    "etc-beats-all":  ("", PINNED, PINNED, "etc"),   # an EMPTY /etc config still wins
    "xdg-only":       (None, PINNED, None, "xdg"),
    "xdg-beats-trad": (None, PINNED, NOKEY, "xdg"),
    "trad-only":      (None, None, PINNED, "trad"),
    "none":           (None, None, None, "trad"),    # rnsd's default dir, no file yet
}


def _layout(tmp_path, name):
    etc_c, xdg_c, trad_c, want = LAYOUTS[name]
    etc = tmp_path / "etc-reticulum"
    home = tmp_path / "home"
    dirs = {"etc": etc, "xdg": home / ".config" / "reticulum", "trad": home / ".reticulum"}
    for d, content in zip(("etc", "xdg", "trad"), (etc_c, xdg_c, trad_c)):
        dirs[d].mkdir(parents=True, exist_ok=True)   # a dir alone never counts
        if content is not None:
            (dirs[d] / "config").write_text(content)
    return etc, home, dirs[want]


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_resolver_is_rnsds_order_by_existence(tmp_path, monkeypatch, name):
    etc, home, want = _layout(tmp_path, name)
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", etc)
    monkeypatch.setenv("MESHFORGE_RNS_CONFIGDIR", str(tmp_path / "decoy"))  # rnsd ignores it
    assert ReticulumPaths.rnsd_config_dir_for_home(home) == want


def test_get_config_dir_delegates_to_the_one_resolver(tmp_path, monkeypatch):
    etc, home, want = _layout(tmp_path, "xdg-only")
    monkeypatch.setattr(ReticulumPaths, "ETC_BASE", etc)
    monkeypatch.delenv("MESHFORGE_RNS_CONFIGDIR", raising=False)
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: home)
    assert ReticulumPaths.get_config_dir() == want
    monkeypatch.setenv("MESHFORGE_RNS_CONFIGDIR", str(tmp_path / "sandbox"))
    assert ReticulumPaths.get_config_dir() == tmp_path / "sandbox"   # MeshForge's own knob, kept


# ── the real checker, /etc remapped by a python3 shim on PATH ───────────

def _shim(tmp_path, etc, no_rns=False):
    """A ``python3`` on PATH that runs the given script with
    ``ReticulumPaths.ETC_BASE`` pointed at ``etc`` — nothing else changed
    (``no_rns``: RNS made unimportable too, as on a box without it)."""
    driver = tmp_path / "etc_driver.py"
    block = ("for m in ('RNS', 'RNS.vendor', 'RNS.vendor.configobj'):\n"
             "    sys.modules[m] = None\n") if no_rns else ""
    driver.write_text(
        "import runpy, sys\n" + block +
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from pathlib import Path\n"
        "from utils.paths import ReticulumPaths\n"
        f"ReticulumPaths.ETC_BASE = Path({str(etc)!r})\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    py = bindir / "python3"
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{driver}" "$@"\n')
    py.chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
            "MESHFORGE_RNS_CONFIGDIR": str(tmp_path / "decoy")}


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_cli_rnsd_config_prints_rnsds_file(tmp_path, name):
    etc, home, want = _layout(tmp_path, name)
    r = subprocess.run(["python3", str(PIN), "--rnsd-config", "--home", str(home)],
                       env=_shim(tmp_path, etc), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(want / "config")


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_cli_check_home_judges_that_same_file(tmp_path, name):
    etc, home, want = _layout(tmp_path, name)
    r = subprocess.run(["python3", str(PIN), "--check", "--home", str(home)],
                       env=_shim(tmp_path, etc), capture_output=True, text=True, timeout=60)
    content = (want / "config").read_text() if (want / "config").exists() else None
    expect = {PINNED: 0, NOKEY: 4, "": 4, None: 1}[content]
    assert r.returncode == expect, r.stdout + r.stderr
    assert str(want / "config") in r.stdout + r.stderr          # the file judged is named
    assert KEY not in r.stdout + r.stderr


def test_no_config_names_the_case_not_a_generic_remedy(tmp_path):
    """r17 NIT 2: ENOENT is "rnsd has not run as this user yet", not
    "make it readable"."""
    r = subprocess.run([sys.executable, str(PIN), "--check", str(tmp_path / "config")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert "no config at" in r.stderr and "has not run as this user" in r.stderr


@pytest.mark.parametrize("bad", ["relative/home", "/home/../root"])
def test_home_must_be_absolute_without_dotdot(bad):
    r = subprocess.run([sys.executable, str(PIN), "--rnsd-config", "--home", bad],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and r.stdout == ""


# ── installer gate + COPY, TOGETHER (the r17 #1 reproducer) ─────────────

def _section(start, end):
    text = INSTALLER.read_text()
    assert text.count(start) == 1, start
    i = text.index(start)
    return text[i:text.index(end, i)]


def _install(tmp_path, name, no_rns=False, rnsd_text=None):
    """Run the REAL rpc_key_check + gate + (stubbed activation) + COPY."""
    etc, home, want = _layout(tmp_path, name)
    if rnsd_text is not None:
        (want / "config").write_text(rnsd_text)
    fn = _section("rpc_key_check() {", "\n}\n") + "\n}\n"
    gate = _section('echo "=== rpc_key precondition (Issue #41) ==="',
                    'echo "=== Activating meshchatx.service')
    copy = _section('echo "=== Client rpc_key (MeshChatX\'s own RNS configdir) ==="',
                    'echo "=== Stock-RNS waiver')
    script = ("set -euo pipefail\n"
              'run_as_user_with_path() { "$@"; }\n'
              'run_user_systemctl() { echo "SYSTEMCTL $*"; }\n'
              f'REAL_HOME="{home}"; REAL_USER=someuser; REPO_ROOT="{REPO}"\n'
              "seq() { echo 1; }\n"
              + fn + gate + 'echo "GATE_PASSED ${RNSD_CFG}"\n' + copy + "echo COPY_DONE\n")
    r = subprocess.run(["bash", "-c", script], env=_shim(tmp_path, etc, no_rns),
                       capture_output=True, text=True, timeout=120)
    return r, home, want


def _client(home):
    p = home / ".reticulum" / "config"
    return p.read_text() if p.exists() else None


def test_xdg_box_gate_passes_and_copy_pins_the_client(tmp_path):
    """THE r17 #1 layout: rnsd's pinned config in ~/.config/reticulum, a
    keyless ~/.reticulum/config (MeshChatX's own). r13.5 said "client shares
    rnsd's config — nothing to copy"."""
    r, home, want = _install(tmp_path, "xdg-beats-trad")
    assert f"GATE_PASSED {want / 'config'}" in r.stdout, r.stdout + r.stderr
    assert "nothing to copy" not in r.stdout
    assert reader.rnsd_rpc_key(_client(home).encode()) == ("valid", KEY), r.stderr
    assert "SYSTEMCTL restart meshchatx" in r.stdout and "COPY_DONE" in r.stdout
    assert KEY not in r.stdout + r.stderr


def test_etc_box_copies_into_the_client(tmp_path):
    (tmp_path / "home" / ".reticulum").mkdir(parents=True)
    (tmp_path / "home" / ".reticulum" / "config").write_text(NOKEY)
    r, home, want = _install(tmp_path, "etc-only")
    assert f"GATE_PASSED {want / 'config'}" in r.stdout, r.stderr
    assert reader.rnsd_rpc_key(_client(home).encode()) == ("valid", KEY), r.stderr


def test_trad_box_client_shares_rnsds_config_truthfully(tmp_path):
    """rnsd loads ~/.reticulum/config itself — the one case "shares" is true."""
    r, home, want = _install(tmp_path, "trad-only")
    assert f"client shares rnsd's config ({want / 'config'})" in r.stdout, r.stdout + r.stderr
    assert "SYSTEMCTL" not in r.stdout


@pytest.mark.parametrize("name", ["xdg-only", "etc-beats-xdg"])
def test_a_keyless_rnsd_config_never_activates(tmp_path, name):
    """Gate and COPY can only disagree if they read different files; with the
    gate judging rnsd's own file, a keyless one stops the install (rc 3)."""
    r, _home, want = _install(tmp_path, name, rnsd_text=NOKEY)
    assert r.returncode == 3 and "GATE_PASSED" not in r.stdout
    assert "not pinned in the config rnsd loads" in r.stderr
    assert str(want / "config") in r.stderr                    # r17 NIT 7: the file is named


def test_no_config_anywhere_is_unknown_with_the_named_reason(tmp_path):
    r, _home, want = _install(tmp_path, "none")
    assert r.returncode == 3 and "GATE_PASSED" not in r.stdout
    assert "UNKNOWN" in r.stderr and "has not run as this user" in r.stderr


def test_unjudged_rnsd_config_is_unknown_never_shares(tmp_path):
    """No RNS for the checker's python + a triple-quoted key rnsd reads:
    UNKNOWN at the gate (rc 3, not activated), never "shares"/"copied"."""
    r, _home, _want = _install(tmp_path, "xdg-beats-trad", no_rns=True, rnsd_text=TQ)
    assert r.returncode == 3 and "GATE_PASSED" not in r.stdout
    assert "UNKNOWN" in r.stderr and "cannot judge" in r.stderr
    assert "rnsd cannot load" not in r.stderr and "shares" not in r.stdout
    assert KEY not in r.stdout + r.stderr


def test_copy_with_no_resolved_rnsd_config_is_unknown_not_shares(tmp_path):
    """r13.5's block said "client shares rnsd's config" when it had no
    server path at all; the gate always sets RNSD_CFG, so this is a wiring
    fault — said, never dressed as a fact."""
    home = tmp_path / "home"
    (home / ".reticulum").mkdir(parents=True)
    (home / ".reticulum" / "config").write_text(NOKEY)
    copy = _section('echo "=== Client rpc_key (MeshChatX\'s own RNS configdir) ==="',
                    'echo "=== Stock-RNS waiver')
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n"
                        'run_as_user_with_path() { "$@"; }\n'
                        'run_user_systemctl() { echo "SYSTEMCTL $*"; }\n'
                        f'REAL_HOME="{home}"; REPO_ROOT="{REPO}"\nseq() {{ echo 1; }}\n'
                        + copy + "echo COPY_DONE\n"],
                       env={**os.environ, "RNSD_CFG": ""},
                       capture_output=True, text=True, timeout=60)
    assert "COPY_DONE" in r.stdout, r.stderr
    assert "UNKNOWN" in r.stderr and "shares" not in r.stdout and "SYSTEMCTL" not in r.stdout


# ── r17 NIT 5: every RNSConfigError raised in an except is `from None` ──

_SANITIZED = {"RNSConfigError", "RNSConfigUnjudged", "RPCKeyWriteRefused", "_foreign"}


def _raises_in_handlers(path):
    tree = ast.parse(path.read_text())
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        for node in ast.walk(handler):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                fn = node.exc.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name in _SANITIZED:
                    yield node


@pytest.mark.parametrize("rel", ["src/utils/rns_config_reader.py", "src/utils/rns_config_setup.py"])
def test_every_config_error_raised_in_a_handler_drops_its_cause(rel):
    """A foreign exception (ConfigObj ParseError, UnicodeDecodeError) can
    quote the ``rpc_key <hex>`` line; as ``__cause__`` it rides into every
    traceback (r16 #2). Pinned structurally so a new site is covered too."""
    sites = list(_raises_in_handlers(REPO / rel))
    assert sites, f"no sanitized raise found in {rel} — the pin went blind"
    bad = [n.lineno for n in sites
           if not (isinstance(n.cause, ast.Constant) and n.cause.value is None)]
    assert not bad, f"{rel}: raise without `from None` at lines {bad}"


# ── r17 NIT 1: rns_alignment says UNKNOWN for an unjudged file ──────────

def test_alignment_words_an_unjudged_etc_config_unknown(tmp_path, monkeypatch):
    """The tool the not-pinned remedy sends the operator to said "rnsd
    cannot load … fix the file by hand" about a file rnsd reads."""
    from utils import rns_alignment as ra
    cfg = tmp_path / "config"
    cfg.write_text(TQ)
    monkeypatch.setattr(reader, "HAS_CONFIGOBJ", False)
    facts = ra._scan_config_file(cfg)
    assert facts.config_error.startswith(ra.UNJUDGED_PREFIX), facts.config_error
    state = ra.RNSAlignmentState(
        hostname="t", rnsd_active=True, rnsd_user="root",
        rnsd_exec_start="/usr/local/bin/rnsd --config /etc/reticulum --service",
        rnsd_configdir=ra.CANONICAL_CONFIGDIR, etc_config=facts)
    reasons = " | ".join(ra.analyze_drift(state))
    assert "UNKNOWN" in reasons and "rnsd may read it fine" in reasons, reasons
    assert "rnsd cannot load" not in reasons and "fix the file by hand" not in reasons
    assert KEY not in reasons


def test_shared_rpc_key_logs_unknown_for_an_unjudged_file(tmp_path, monkeypatch, caplog):
    cfg = tmp_path / "config"
    cfg.write_text(TQ)
    monkeypatch.setattr(reader, "HAS_CONFIGOBJ", False)
    monkeypatch.setattr(ReticulumPaths, "get_config_file", classmethod(lambda cls: cfg))
    assert ReticulumPaths.get_shared_rpc_key() is None          # never derived over it
    assert any("UNKNOWN" in r.getMessage() and "cannot judge" in r.getMessage()
               for r in caplog.records)
    assert KEY not in caplog.text


def test_file_rpc_key_logs_unknown_for_an_unjudged_file(tmp_path, monkeypatch, caplog):
    cfg = tmp_path / "config"
    cfg.write_text(TQ)
    monkeypatch.setattr(reader, "HAS_CONFIGOBJ", False)
    assert reader.file_rpc_key(cfg) is None
    assert any("UNKNOWN" in r.getMessage() and "cannot judge" in r.getMessage()
               for r in caplog.records)
    assert KEY not in caplog.text


def test_a_resolver_failure_is_unknown_and_nothing_is_checked(tmp_path):
    """If ``--rnsd-config`` itself fails there is no file to judge: the gate
    says UNKNOWN (rc 3, not activated) and never checks a guessed path."""
    root = tmp_path / "stubrepo"
    (root / "scripts").mkdir(parents=True)
    log = tmp_path / "calls.log"
    (root / "scripts" / "rns_rpc_key_pin.py").write_text(
        "import sys\n"
        f"open({str(log)!r}, 'a').write(repr(sys.argv[1:]) + '\\n')\n"
        "print('RESOLVER-BROKE', file=sys.stderr)\nsys.exit(1)\n")
    fn = _section("rpc_key_check() {", "\n}\n") + "\n}\n"
    gate = _section('echo "=== rpc_key precondition (Issue #41) ==="',
                    'echo "=== Activating meshchatx.service')
    r = subprocess.run(["bash", "-c", f'set -euo pipefail\nREAL_HOME=/home/u; REAL_USER=u; '
                        f'REPO_ROOT="{root}"\n' + fn + gate + 'echo "GATE_PASSED"\n'],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 3 and "GATE_PASSED" not in r.stdout
    assert "UNKNOWN" in r.stderr and "RESOLVER-BROKE" in r.stderr
    assert [ln for ln in log.read_text().splitlines()] == ["['--rnsd-config', '--home', '/home/u']"]
