"""r13.5 — "this python cannot judge" is UNKNOWN, never "rnsd cannot load".

The input space was enumerated first (shape x python(RNS / no-RNS) x mode,
66 rows per python, by RUNNING the seam): 12 rc disagreements, all one root —
the strict fallback (no RNS importable) raised the same ``RNSConfigError``
for a triple-quoted value rnsd's ConfigObj READS as for a file rnsd cannot
load. ``--copy-from`` then said "server has no usable key" (rc 3) and every
caller worded it as "rnsd cannot load" (Fable r16 #1 / r15 #7). Now: the
fallback raises ``RNSConfigUnjudged``, the CLI answers rc 5 = UNKNOWN in all
three modes, and the three shell callers word 1/2/5 as UNKNOWN, 3/4 as not
pinned — executed here against a stub checker for every rc (r15 #4: the
callers' decisions were only grepped, 10/25 mutants survived).

Also: no RNSConfigError carries its foreign cause (r16 #2 — the ParseError
quotes the ``rpc_key <hex>`` line); the COPY block captures the seam's
stderr (r16 #4 ``2>&1``); ``--copy-from`` never forces over a client key.
"""
import os
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import rns_rpc_key_pin as pin_cli  # noqa: E402
from utils import rns_config_reader as reader  # noqa: E402
from utils.rns_config_reader import RNSConfigError, RNSConfigUnjudged, rnsd_rpc_key  # noqa: E402
from utils.rns_config_setup import RPCKeyWriteRefused, pin_rpc_key_bytes  # noqa: E402

KEY = "ab" * 32
TQ = f"[reticulum]\n  share_instance = Yes\n  rpc_key = '''{KEY}'''\n".encode()
PLAIN = f"[reticulum]\n  share_instance = Yes\n  rpc_key = {KEY}\n".encode()
ABSENT = b"[reticulum]\n  share_instance = Yes\n"
DUP = b"[reticulum]\n  rpc_key = a\n  rpc_key = b\n"


@pytest.fixture
def no_rns(monkeypatch):
    monkeypatch.setattr(reader, "HAS_CONFIGOBJ", False)


# ── the seam ────────────────────────────────────────────────────────────

def test_fallback_says_unjudged_not_unloadable(no_rns):
    with pytest.raises(RNSConfigUnjudged) as ei:
        rnsd_rpc_key(TQ)
    assert isinstance(ei.value, RNSConfigError)          # old catchers still catch
    assert "install RNS" in str(ei.value)


def test_a_real_unloadable_file_is_not_unjudged(no_rns):
    with pytest.raises(RNSConfigError) as ei:
        rnsd_rpc_key(DUP)
    assert not isinstance(ei.value, RNSConfigUnjudged)


@pytest.mark.skipif(not reader.HAS_CONFIGOBJ, reason="needs RNS's ConfigObj")
def test_rnsd_reads_the_triple_quoted_key():
    assert rnsd_rpc_key(TQ) == ("valid", KEY)


def test_the_pinner_passes_unjudged_through_not_as_a_refusal(no_rns):
    with pytest.raises(RNSConfigUnjudged) as ei:
        pin_rpc_key_bytes(TQ, key="cd" * 32)
    assert not isinstance(ei.value, RPCKeyWriteRefused)


_CAUSE_SHAPES = [
    ("invalid-line", f"[reticulum]\n  rpc_key {KEY}\n".encode()),   # ParseError quotes the line
    ("dup", f"[reticulum]\n  rpc_key = {KEY}\n  rpc_key = {KEY}\n".encode()),
    ("undecodable", b"[reticulum]\n  rpc_key = " + KEY.encode() + b"\xff\n"),
]


@pytest.mark.parametrize("rns", [True, False], ids=["RNS", "noRNS"])
@pytest.mark.parametrize("sid,raw", _CAUSE_SHAPES, ids=[s[0] for s in _CAUSE_SHAPES])
def test_no_refusal_carries_its_foreign_cause(monkeypatch, rns, sid, raw):
    if rns and not reader.HAS_CONFIGOBJ:
        pytest.skip("needs RNS's ConfigObj")
    monkeypatch.setattr(reader, "HAS_CONFIGOBJ", rns)
    for call in (rnsd_rpc_key, lambda r: pin_rpc_key_bytes(r, key="cd" * 32)):
        with pytest.raises(RNSConfigError) as ei:
            call(raw)
        e = ei.value
        while e is not None:                               # the whole chain
            assert e.__cause__ is None, (sid, type(e.__cause__))
            e = e.__context__ if not e.__suppress_context__ else None
        printed = "".join(traceback.format_exception(ei.value))
        assert KEY not in printed, sid


# ── the CLI, on a python without RNS ────────────────────────────────────

def _cli_no_rns(tmp_path, *args):
    """Run the REAL CLI in a python where RNS cannot be imported."""
    driver = tmp_path / "no_rns_driver.py"
    driver.write_text(
        "import runpy, sys\n"
        "for m in ('RNS', 'RNS.vendor', 'RNS.vendor.configobj'):\n"
        "    sys.modules[m] = None\n"
        f"sys.argv = [{str(REPO / 'scripts' / 'rns_rpc_key_pin.py')!r}] + sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n")
    return subprocess.run([sys.executable, str(driver), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("mode", ["check", "copy-server", "copy-client", "pin"])
def test_cli_unjudged_is_rc5_unknown_and_writes_nothing(tmp_path, mode):
    tq, other = tmp_path / "tq", tmp_path / "other"
    tq.write_bytes(TQ)
    other.write_bytes(PLAIN if mode == "copy-client" else ABSENT)
    args = {"check": ("--check", tq), "pin": (tq,),
            "copy-server": ("--copy-from", tq, other),
            "copy-client": ("--copy-from", other, tq)}[mode]
    r = _cli_no_rns(tmp_path, *args)
    out = r.stdout + r.stderr
    assert r.returncode == pin_cli.RC_UNJUDGED == 5, out
    assert "UNKNOWN" in out and "cannot judge" in out and "rnsd cannot load" not in out
    assert KEY not in out
    assert tq.read_bytes() == TQ and not list(tmp_path.glob("*.bak-*"))
    assert other.read_bytes() == (PLAIN if mode == "copy-client" else ABSENT)


def test_cli_unloadable_stays_rnsd_cannot_load(tmp_path):
    cfg = tmp_path / "c"
    cfg.write_bytes(DUP)
    r = _cli_no_rns(tmp_path, "--check", cfg)
    assert r.returncode == 2 and "rnsd cannot load" in r.stderr


def test_check_without_a_path_or_home_is_usage_not_a_guess():
    """r13.6: the no-path ``--check`` inferred the home from SUDO_USER/LOGNAME
    and honoured MESHFORGE_RNS_CONFIGDIR, both unlike rnsd (r17 NIT 3) —
    gone; callers pass ``--home``. rnsd's order: test_rnsd_config_seam."""
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "rns_rpc_key_pin.py"), "--check"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "--home DIR" in r.stderr


def test_copy_never_forces_over_an_invalid_client_key(tmp_path):
    server, client = tmp_path / "s", tmp_path / "c"
    server.write_bytes(PLAIN)
    client_raw = b"[reticulum]\n  share_instance = Yes\n  rpc_key = nothex\n"
    client.write_bytes(client_raw)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "rns_rpc_key_pin.py"),
                        "--copy-from", server, client],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, r.stderr
    assert client.read_bytes() == client_raw and not list(tmp_path.glob("c.bak-*"))


# ── the shell callers' DECISIONS, executed (r15 #4) ─────────────────────

ALL_RCS = [0, 1, 2, 3, 4, 5]
STUB_CFG = "/stub/rnsd/config"          # what the stub's --rnsd-config prints


def _stub_repo(tmp_path, rc, msg="STUB-REASON", stream="stderr"):
    """A repo whose rns_rpc_key_pin.py exits ``rc`` and logs its argv, so a
    caller's branch on every rc can be observed (``--rnsd-config`` answers
    ``STUB_CFG``, rc 0)."""
    root = tmp_path / "stubrepo"
    (root / "scripts").mkdir(parents=True)
    log = tmp_path / "calls.log"
    (root / "scripts" / "rns_rpc_key_pin.py").write_text(
        "import os, sys\n"
        f"open({str(log)!r}, 'a').write(repr(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:2] == ['--rnsd-config']:\n"
        f"    print({STUB_CFG!r}); sys.exit(0)\n"
        f"print({msg!r}, file=sys.{stream})\n"
        f"sys.exit({rc})\n")
    return root, log


def _section(path, start, end):
    text = (REPO / path).read_text()
    assert text.count(start) == 1, (path, start)
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def _bash(script, env=None):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=60, env={**os.environ, **(env or {})})


def _calls(log):
    return [eval(line) for line in log.read_text().splitlines()]  # noqa: S307 — our own repr


def _verdict(r, rc):
    if rc == 0:
        return "pass"
    if rc in pin_cli.NOT_PINNED_RCS:
        return "not-pinned"
    assert rc in pin_cli.UNKNOWN_RCS
    return "unknown"


@pytest.mark.parametrize("rc", ALL_RCS)
def test_wrapper_gate_decides_on_every_rc(tmp_path, rc):
    repo, log = _stub_repo(tmp_path, rc)
    gate = _section("templates/python/meshchatx_wrapper.sh",
                    "# 1. rpc_key precondition", "# 2. Resolve the meshchatx binary")
    script = ("set -eu\nEXIT_AUTH_MISMATCH=87\n"
              "err() { printf '%s\\n' \"$*\" >&2; }\n" + gate + "echo GATE_PASSED\n")
    r = _bash(script, {"MESHFORGE_REPO": str(repo)})
    want = _verdict(r, rc)
    if want == "pass":
        assert r.returncode == 0 and "GATE_PASSED" in r.stdout, r.stderr
    else:
        assert r.returncode == 87 and "GATE_PASSED" not in r.stdout
        assert ("MISMATCH" in r.stderr) is (want == "not-pinned"), r.stderr
        assert ("UNKNOWN" in r.stderr) is (want == "unknown"), r.stderr
    assert "STUB-REASON" in r.stderr                       # the seam's reason reaches the journal
    # the home is EXPLICIT — the one rnsd expands — never inferred (r17 NIT 3)
    assert _calls(log) == [["--check", "--home", os.environ["HOME"]]]


def test_wrapper_without_a_checker_is_unknown(tmp_path):
    gate = _section("templates/python/meshchatx_wrapper.sh",
                    "# 1. rpc_key precondition", "# 2. Resolve the meshchatx binary")
    r = _bash("set -eu\nEXIT_AUTH_MISMATCH=87\nerr() { printf '%s\\n' \"$*\" >&2; }\n" + gate,
              {"MESHFORGE_REPO": str(tmp_path / "nowhere")})
    assert r.returncode == 87 and "UNKNOWN" in r.stderr and "MISMATCH" not in r.stderr


_INSTALLER = "scripts/install_meshchatx.sh"


def _installer_check_fn():
    return _section(_INSTALLER, "rpc_key_check() {", "\n}\n") + "\n}\n"


@pytest.mark.parametrize("rc", ALL_RCS)
def test_installer_gate_decides_on_every_rc(tmp_path, rc):
    repo, log = _stub_repo(tmp_path, rc)
    gate = _section(_INSTALLER, 'echo "=== rpc_key precondition (Issue #41) ==="',
                    'echo "=== Activating meshchatx.service')
    script = (f'set -uo pipefail\nREPO_ROOT="{repo}"; REAL_USER=someuser; REAL_HOME=/home/someuser\n'
              + _installer_check_fn() + gate + 'echo "GATE_PASSED ${RNSD_CFG}"\n')
    r = _bash(script)
    want = _verdict(r, rc)
    if want == "pass":
        assert r.returncode == 0 and "GATE_PASSED" in r.stdout, r.stderr
    else:
        assert r.returncode == 3 and "GATE_PASSED" not in r.stdout
        assert ("UNKNOWN" in r.stderr) is (want == "unknown"), r.stderr
        assert ("not pinned" in r.stderr) is (want == "not-pinned"), r.stderr
    if want == "pass":
        assert f"GATE_PASSED {STUB_CFG}" in r.stdout       # RNSD_CFG rides on to COPY
    else:
        assert "STUB-REASON" in r.stderr                   # the reason names the file (r17 NIT 7)
    # ONE resolution, then the check of THAT path (r17 #1)
    assert _calls(log) == [["--rnsd-config", "--home", "/home/someuser"], ["--check", STUB_CFG]]


@pytest.mark.parametrize("rc", ALL_RCS)
def test_installer_check_mode_counts_and_words_every_rc(tmp_path, rc):
    repo, _log = _stub_repo(tmp_path, rc)
    case = _section(_INSTALLER, "    rpc_key_check\n    case", "    esac\n") + "    esac\n"
    script = (f'set -uo pipefail\nREPO_ROOT="{repo}"; REAL_USER=someuser; REAL_HOME=/home/someuser\n'
              + _installer_check_fn() + "drift=0\n" + case + 'echo "DRIFT=${drift}"\n')
    r = _bash(script)
    want = _verdict(r, rc)
    assert f"DRIFT={0 if want == 'pass' else 1}" in r.stdout, r.stdout + r.stderr
    assert ("UNKNOWN" in r.stdout) is (want == "unknown")
    assert ("not pinned" in r.stdout) is (want == "not-pinned")
    assert ("STUB-REASON" in r.stdout) is (want != "pass")


@pytest.mark.parametrize("rc", ALL_RCS)
def test_configure_gateway_decides_on_every_rc(tmp_path, rc):
    repo, log = _stub_repo(tmp_path, rc, stream="stdout")
    block = _section("scripts/configure_gateway.sh",
                     "# rpc_key must be pinned in rnsd's config", "\nesac\n") + "\nesac\n"
    script = (f'set -euo pipefail\nREPO_ROOT="{repo}"; TARGET_USER=someuser; '
              f'TARGET_HOME=/home/someuser; HASH_PY="{sys.executable}"\n'
              "warn() { printf 'WARN %s\\n' \"$*\"; }\n" + block + "echo BLOCK_DONE\n")
    r = _bash(script)
    assert "BLOCK_DONE" in r.stdout, r.stderr               # a warning, never an abort
    want = _verdict(r, rc)
    assert ("WARN" in r.stdout) is (want != "pass")
    assert ("UNKNOWN" in r.stdout) is (want == "unknown")
    assert ("NOT pinned" in r.stdout) is (want == "not-pinned")
    assert "STUB-REASON" in r.stdout
    assert _calls(log) == [["--check", "--home", "/home/someuser"]]   # explicit home (NIT 3)
    if want == "unknown":                                   # its Check line re-runs THE same call (NIT 6)
        assert f"Check: sudo {sys.executable} {repo}/scripts/rns_rpc_key_pin.py --check --home /home/someuser" in r.stdout


# ── the COPY block's arms (r16 #4: the seam's stderr IS the WARN) ───────

@pytest.mark.parametrize("rc", [1, 2, 3, 4, 5])
def test_copy_block_says_the_seams_reason_on_every_failure(tmp_path, rc):
    repo, _log = _stub_repo(tmp_path, rc, msg="SEAM-SAYS-WHY", stream="stderr")
    from tests.test_install_meshchatx_client_key import _block
    home = tmp_path / "home"
    (home / ".reticulum").mkdir(parents=True)
    (home / ".reticulum" / "config").write_text("[reticulum]\n")
    server = tmp_path / "etc-config"
    server.write_text("[reticulum]\n")
    script = ("set -uo pipefail\n"
              'run_as_user_with_path() { "$@"; }\n'
              'run_user_systemctl() { echo "SYSTEMCTL $*"; }\n'
              f'REAL_HOME="{home}"; REAL_USER=someuser; RNSD_CFG="{server}"; REPO_ROOT="{repo}"\n'
              "seq() { echo 1; }\n" + _block())
    r = _bash(script)
    warn = [ln for ln in r.stderr.splitlines() if "WARN" in ln or "UNKNOWN" in ln]
    assert warn and "SEAM-SAYS-WHY" in warn[0], r.stderr   # captured via 2>&1, same line
    assert ("UNKNOWN" in warn[0]) is (rc == 5)
    assert "SYSTEMCTL" not in r.stdout
