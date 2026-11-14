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
