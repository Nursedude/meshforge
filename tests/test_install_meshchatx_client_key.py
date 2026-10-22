"""install_meshchatx.sh copies rnsd's pinned rpc_key into MeshChatX's OWN
RNS config (2026-10-21, meshanchor-server: MeshChatX ran RNS from ~/.reticulum
and every RPC to rnsd failed "digest sent was rejected", one every 20 s).

Runs the REAL installer block in a sandbox (helpers stubbed, paths pointed at
tmp). Byte-identical in MeshForge and MeshAnchor.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
KEY = "ab" * 32
OTHER = "cd" * 32
START = 'echo "=== Client rpc_key (MeshChatX\'s own RNS configdir) ==="'
END = 'echo "=== Verifying ==="'


def _block() -> str:
    text = (REPO / "scripts" / "install_meshchatx.sh").read_text()
    i = text.index(START)
    return text[i:text.index(END, i)]


def _run(tmp_path, server_text, client_text):
    server = tmp_path / "etc-config"
    server.write_text(server_text)
    home = tmp_path / "home"
    (home / ".reticulum").mkdir(parents=True)
    if client_text is not None:
        (home / ".reticulum" / "config").write_text(client_text)
    script = (
        "set -uo pipefail\n"
        'run_as_user_with_path() { "$@"; }\n'
        'run_user_systemctl() { echo "SYSTEMCTL $*"; }\n'
        f'REAL_HOME="{home}"; RNS_SERVER_CFG="{server}"\n'
        "seq() { echo 1; }\n"  # no 30 s wait in a test
        f"{_block()}\necho BLOCK_DONE\n")
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    client = home / ".reticulum" / "config"
    return r, (client.read_text() if client.exists() else None)


SERVER = f"[reticulum]\n  share_instance = Yes\n  rpc_key = {KEY}\n"
CLIENT = "[reticulum]\n  share_instance = Yes\n  instance_name = default\n"


def test_a_client_without_the_key_gets_rnsds_and_meshchatx_restarts(tmp_path):
    r, client = _run(tmp_path, SERVER, CLIENT)
    assert "BLOCK_DONE" in r.stdout, r.stderr
    assert f"rpc_key = {KEY}" in client
    assert "SYSTEMCTL restart meshchatx" in r.stdout


def test_the_key_is_never_printed(tmp_path):
    r, _ = _run(tmp_path, SERVER, CLIENT)
    assert KEY not in r.stdout and KEY not in r.stderr


def test_a_client_already_carrying_the_key_is_left_and_not_restarted(tmp_path):
    r, client = _run(tmp_path, SERVER, CLIENT + f"  rpc_key = {KEY}\n")
    assert "already pinned" in r.stdout and "SYSTEMCTL" not in r.stdout
    assert client.count("rpc_key") == 1


def test_a_client_with_a_different_key_is_never_overwritten_and_said(tmp_path):
    r, client = _run(tmp_path, SERVER, CLIENT + f"  rpc_key = {OTHER}\n")
    assert OTHER in client and KEY not in client
    assert "DIFFERENT" in r.stderr and "SYSTEMCTL" not in r.stdout


def test_a_server_without_a_pinned_key_is_said_not_guessed(tmp_path):
    r, client = _run(tmp_path, "[reticulum]\n  share_instance = Yes\n", CLIENT)
    assert "rpc_key" not in client
    assert "no pinned rpc_key" in r.stderr


def test_a_client_config_that_never_appears_is_said(tmp_path):
    r, client = _run(tmp_path, SERVER, None)
    assert client is None and "did not appear" in r.stderr
