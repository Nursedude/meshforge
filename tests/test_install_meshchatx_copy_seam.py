"""install_meshchatx.sh's client-key COPY block asks the ONE rpc_key seam
(`scripts/rns_rpc_key_pin.py --copy-from`) — WITNESS-TUI r13.3, Fable r15 #1.

r13.2 moved the installer's GATE to the seam, so a quoted server key now
reads "pinned" and the unit runs — but the COPY block kept its own regex and
said "no pinned rpc_key" for that same key: MeshChatX started with no key
("digest sent was rejected"). The old block was wrong on 338/506 Fable shapes,
including writing a DUPLICATE rpc_key into a client that already carried the
key quoted (an unloadable client). These run the REAL block through the
shared harness. MeshForge-only: MeshAnchor's twin block keeps its python until
the MA seam port (the shared harness test stays byte-identical).
"""
import sys

from tests.test_install_meshchatx_client_key import CLIENT, KEY, OTHER, REPO, _block, _run

sys.path.insert(0, str(REPO / "src"))
from utils.rns_config_reader import rnsd_rpc_key  # noqa: E402

PLAIN = f"[reticulum]\n  share_instance = Yes\n  rpc_key = {KEY}\n"
QUOTED = f'[reticulum]\n  share_instance = Yes\n  rpc_key = "{KEY}"\n'


def _rnsd_reads(text):
    return rnsd_rpc_key(text.encode())


def test_the_block_calls_the_seam_not_a_regex():
    block = _block()
    assert 'rns_rpc_key_pin.py" --copy-from' in block
    assert "re.compile" not in block


def test_a_quoted_server_key_rnsd_reads_is_copied(tmp_path):
    assert _rnsd_reads(QUOTED) == ("valid", KEY)          # the gate says pinned
    r, client = _run(tmp_path, QUOTED, CLIENT)
    assert _rnsd_reads(client) == ("valid", KEY), r.stderr
    assert "SYSTEMCTL restart meshchatx" in r.stdout


def test_an_inline_commented_server_key_is_copied(tmp_path):
    server = f"[reticulum]\n  share_instance = Yes\n  rpc_key = {KEY}  # pinned\n"
    assert _rnsd_reads(server) == ("valid", KEY)
    r, client = _run(tmp_path, server, CLIENT)
    assert _rnsd_reads(client) == ("valid", KEY), r.stderr


def test_a_client_carrying_the_same_key_quoted_gets_no_duplicate(tmp_path):
    client_in = CLIENT + f'  rpc_key = "{KEY}"\n'
    r, client = _run(tmp_path, PLAIN, client_in)
    assert client == client_in, "client rewritten"
    assert "already pinned" in r.stdout and "SYSTEMCTL" not in r.stdout


def test_a_client_with_a_different_quoted_key_is_never_overwritten(tmp_path):
    client_in = CLIENT + f'  rpc_key = "{OTHER}"\n'
    r, client = _run(tmp_path, PLAIN, client_in)
    assert client == client_in
    assert "DIFFERENT" in r.stderr and "SYSTEMCTL" not in r.stdout


def test_a_server_key_rnsd_does_not_use_is_not_copied(tmp_path):
    server = f"[reticulum]\n  share_instance = Yes\n  rpc_key = {KEY},\n"   # a list
    r, client = _run(tmp_path, server, CLIENT)
    assert client == CLIENT and "SYSTEMCTL" not in r.stdout
    assert "rc=3" in r.stderr


def test_the_copied_client_still_loads_and_the_key_is_never_printed(tmp_path):
    r, client = _run(tmp_path, QUOTED, CLIENT)
    assert _rnsd_reads(client) == ("valid", KEY)
    assert KEY not in r.stdout and KEY not in r.stderr
