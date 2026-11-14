"""EVAL-PF step 3 — the eval key installer + the session's ssh shim.

plans/eval_pf_grilling_2026_11_13.md: the lab wrapper only protects the box if
the session can reach it with NOTHING BUT the eval key. Two leaks this pins:
an ssh-agent / ~/.ssh/config identity being offered (the operator's full-shell
key), and a word the session types being parsed as an ssh option (-i/-o/-F).
And (operator 11-13 "one writer"): the installer never edits the box's key
file — it only drives the one grant CLI, kind eval-pf.
"""

import importlib.util
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHIM = REPO / "scripts" / "eval_pf_ssh.sh"
_spec = importlib.util.spec_from_file_location(
    "eval_pf_key_install", REPO / "scripts" / "eval_pf_key_install.py")
inst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inst)


def test_remote_commands_drive_only_the_grant_cli():
    pub = "ssh-ed25519 AAAA eval-pf:center-a"
    for cmd in (inst.grant_add_cmd(pub, "center-a", "abcdefgh"),
                inst.grant_revoke_cmd("SHA256:" + "A" * 43),
                inst.grant_list_cmd()):
        words = shlex.split(cmd)
        assert words[:2] == ["python3", inst.BOX_GRANT_CLI]
    add = shlex.split(inst.grant_add_cmd(pub, "center-a", "abcdefgh"))
    assert add[2:] == ["add", "--kind", "eval-pf", "--pubkey", pub,
                       "--center", "center-a", "--confirm", "abcdefgh"]


def test_pubkey_cannot_inject_into_the_remote_command():
    evil = "ssh-ed25519 AAAA eval-pf:x; rm -rf ~"
    words = shlex.split(inst.grant_add_cmd(evil, "c", "12345678"))
    assert words[words.index("--pubkey") + 1] == evil


def test_center_name_is_grant_safe():
    assert inst.center_name("CenterA") == "centera"
    assert inst.center_name("my box.local") == "my-box.local"


def test_listed_finds_the_kind_column():
    fp = "SHA256:" + "B" * 43
    out = (f"{fp}  eval-pf eval-pf:center-a        last read never from -  [authorized_keys:4]\n"
           f"SHA256:{'C' * 43}  noc     noc:center-a            last read never from -  [x:1]\n")
    assert [k for k, _ in inst.listed(out, fp)] == ["eval-pf"]
    assert inst.listed(out, "SHA256:" + "Z" * 43) == []


SSH_G = """user wh6gxz
hostname lab.example.invalid
port 223
hostkeyalias meshforge-lab
identityfile ~/.claude/ssh/id_ed25519
"""


def test_ssh_config_offers_only_the_eval_key():
    cfg = inst.render_ssh_config(inst.parse_ssh_g(SSH_G), "/h/.ssh/eval_pf_ed25519", "/h/.ssh/known_hosts")
    assert "Host eval-lab" in cfg
    assert "HostName lab.example.invalid" in cfg and "Port 223" in cfg
    assert "HostKeyAlias meshforge-lab" in cfg
    assert "IdentityFile /h/.ssh/eval_pf_ed25519" in cfg
    assert "IdentitiesOnly yes" in cfg
    assert "IdentityAgent none" in cfg
    assert "ForwardAgent no" in cfg
    assert "StrictHostKeyChecking yes" in cfg
    assert "~/.claude/ssh/id_ed25519" not in cfg   # the admin identity is NOT copied


def test_parse_ssh_g_refuses_missing_fields():
    with pytest.raises(ValueError):
        inst.parse_ssh_g("user x\nport 22\n")


def test_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(inst, "get_real_user_home", lambda: tmp_path)
    called = []
    monkeypatch.setattr(inst, "run", lambda *a, **k: called.append(a))
    assert inst.main(["--box", "lab.example.invalid"]) == 0
    assert called == []
    assert "DRY RUN" in capsys.readouterr().out
    assert not (tmp_path / ".ssh").exists()


@pytest.fixture
def shim(tmp_path):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    rec = tmp_path / "ssh_call"
    ssh = stubs / "ssh"
    ssh.write_text(
        "#!/bin/bash\n"
        f'{{ printf "%s\\n" "$@"; echo "AGENT=${{SSH_AUTH_SOCK-unset}}"; }} > "{rec}"\n')
    ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR)
    cfg = tmp_path / "ssh_config"

    def go(*args, with_cfg=True):
        if with_cfg:
            cfg.write_text("Host eval-lab\n")
        elif cfg.exists():
            cfg.unlink()
        env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path),
               "EVAL_PF_SSH_CONFIG": str(cfg), "SSH_AUTH_SOCK": "/tmp/agent.sock"}
        p = subprocess.run(["/bin/bash", str(SHIM), *args], env=env, capture_output=True,
                           text=True, timeout=20, stdin=subprocess.DEVNULL)
        lines = rec.read_text().splitlines() if rec.exists() else []
        return p, lines

    go.cfg = cfg
    return go


def test_shim_puts_every_session_word_after_the_host(shim):
    p, argv = shim("-i", "/home/x/.ssh/id_ed25519", "-o", "ProxyCommand=sh", "bash")
    assert p.returncode == 0
    assert argv[:4] == ["-F", str(shim.cfg), "--", "eval-lab"]
    assert argv[4] == "-i /home/x/.ssh/id_ed25519 -o ProxyCommand=sh bash"
    assert len([a for a in argv if not a.startswith("AGENT=")]) == 5


def test_shim_never_hands_ssh_the_agent(shim):
    _, argv = shim("uptime")
    assert argv[-1] == "AGENT=unset"


def test_shim_refuses_without_config_or_command(shim):
    p, argv = shim("uptime", with_cfg=False)
    assert p.returncode == 2 and argv == [] and "eval_pf_key_install.py" in p.stderr
    p, argv = shim()
    assert p.returncode == 2 and argv == []


# ── the --apply / --revoke flow, end to end against a fake box ──────────
# Fable 11-14: every check in main() could be deleted and the suite stayed
# green (mutants M2–M5, M9, M10, M13–M15 survived). Each test below fails if
# one of those checks is removed. Nothing here reaches a network: `run` is
# replaced, and the box name is *.invalid.

BOX = "lab.example.invalid"


class FakeBox:
    def __init__(self, fp):
        self.fp = fp
        self.wrapper = "ok\n"
        self.add_rc = 0
        self.list_rc = 0
        self.list_rows = [f"{fp}  eval-pf eval-pf:center-a        last read never from -  [authorized_keys:4]"]
        self.list_rows_after_revoke = []
        self.revoked = False
        self.revoke_rc = 0
        self.uptime_rc = 0
        self.id_rc = 126
        self.calls = []

    def __call__(self, argv, *, env=None):
        self.calls.append((list(argv), env))
        out, rc = "", 0
        if argv[:2] == ["ssh", "-G"]:
            out = SSH_G
        elif argv[0] == "ssh":
            remote = argv[-1]
            if remote.startswith("test -x"):
                out = self.wrapper
            elif " add " in f" {remote} ":
                rc = self.add_rc
            elif " revoke " in f" {remote} ":
                rc = self.revoke_rc
                self.revoked = rc == 0
            elif remote.endswith(" list"):
                rc = self.list_rc
                rows = self.list_rows_after_revoke if self.revoked else self.list_rows
                out = "" if (rc or getattr(self, "list_empty_output", False)) else "\n".join(rows) + "\n"
        elif argv[0] == "bash" and argv[1] == inst.SHIM:
            rc = {"uptime": self.uptime_rc, "id": self.id_rc}[argv[2]]
        return subprocess.CompletedProcess(argv, rc, out, "")

    def remotes(self):
        return [a[-1] for a, _ in self.calls if a[0] == "ssh" and a[1] != "-G"]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    key = home / ".ssh" / "eval_pf_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                    "eval-pf:center-a", "-f", str(key)], check=True, timeout=30,
                   stdin=subprocess.DEVNULL)
    monkeypatch.setattr(inst, "get_real_user_home", lambda: home)
    monkeypatch.setattr(inst.socket, "gethostname", lambda: "center-a")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    fake = FakeBox(inst.load_key(str(key)).fingerprint)
    monkeypatch.setattr(inst, "run", fake)
    fake.home = home
    return fake


def test_apply_happy_path_grants_writes_config_and_proves(lab, capsys):
    assert inst.main(["--box", BOX, "--apply"]) == 0
    out = capsys.readouterr().out
    assert "TOUCH:" in out and lab.fp in out
    cfg = Path(inst.config_path(str(lab.home)))
    assert cfg.exists() and stat.S_IMODE(cfg.stat().st_mode) == 0o600
    shim_envs = [env for a, env in lab.calls if a[0] == "bash"]
    assert shim_envs and all("SSH_AUTH_SOCK" not in e for e in shim_envs)   # M10
    # every admin ssh ends the options before the box name (Fable NIT 5)
    for a, _ in lab.calls:
        if a[0] == "ssh":
            assert a[a.index(BOX) - 1] == "--", a


@pytest.mark.parametrize("breakit,why", [
    (lambda b: setattr(b, "wrapper", ""), "wrapper absent"),                       # M15
    (lambda b: setattr(b, "add_rc", 1), "add failed"),                             # M14
    (lambda b: setattr(b, "list_rc", 255), "list failed"),
    (lambda b: setattr(b, "list_rows", [b.list_rows[0] + "  ⚠ FOREIGN — x"]), "foreign"),   # M2
    (lambda b: setattr(b, "list_rows", [b.list_rows[0].replace("eval-pf eval", "noc     eval")]), "kind"),  # M3
    (lambda b: setattr(b, "list_rows", b.list_rows * 2), "duplicate"),             # M5
    (lambda b: setattr(b, "uptime_rc", 255), "uptime failed"),                     # M13
    (lambda b: setattr(b, "id_rc", 0), "id ran"),                                  # M9
    (lambda b: setattr(b, "id_rc", 255), "id transport fail"),
])
def test_apply_refuses_when_any_check_fails(lab, capsys, breakit, why):
    breakit(lab)
    assert inst.main(["--box", BOX, "--apply"]) == 1, why
    assert "TOUCH:" not in capsys.readouterr().out


def test_apply_without_wrapper_never_runs_the_grant(lab):
    lab.wrapper = ""
    inst.main(["--box", BOX, "--apply"])
    assert not any(" add " in f" {r} " for r in lab.remotes())


def test_failed_proof_after_grant_says_to_revoke(lab, capsys):
    lab.uptime_rc = 255
    assert inst.main(["--box", BOX, "--apply"]) == 1
    assert "--revoke --apply" in capsys.readouterr().err


def test_revoke_happy_path(lab, capsys):
    lab.list_rows_after_revoke = ["no NOC read grants on this box"]
    assert inst.main(["--box", BOX, "--revoke", "--apply"]) == 0
    assert "REVOKED" in capsys.readouterr().out


@pytest.mark.parametrize("breakit,why", [
    (lambda b: setattr(b, "revoke_rc", 1), "revoke failed"),
    (lambda b: setattr(b, "list_rc", 255), "read-back blind"),                     # M4 / Fable 2
    (lambda b: setattr(b, "list_rows_after_revoke", list(b.list_rows)), "still listed"),
    (lambda b: setattr(b, "list_empty_output", True), "list printed nothing"),
    (lambda b: setattr(b, "list_rows_after_revoke", ["Welcome to lab"]), "list output unrecognised"),
])
def test_revoke_refuses_unless_read_back_proves_it(lab, capsys, breakit, why):
    breakit(lab)
    assert inst.main(["--box", BOX, "--revoke", "--apply"]) == 1, why
    assert "TOUCH:" not in capsys.readouterr().out


@pytest.mark.parametrize("box", ["-oProxyCommand=touch x", "a b", "", "x;id"])
def test_box_name_that_is_not_a_host_alias_is_refused(lab, box):
    assert inst.main(["--box", box, "--apply"]) == 2
    assert lab.calls == []
