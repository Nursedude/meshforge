"""install_meshchatx.sh declares MeshChatX's pipx venv as stock-RNS by design
(operator 2026-10-21) in the ONE waiver file the rns_stray_env_drift probe
and fork_roll_install both read. Runs the REAL installer block in a sandbox.
Byte-identical in MeshForge and MeshAnchor.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
START = 'echo "=== Stock-RNS waiver'
END = 'echo "=== Verifying ==="'
LABEL = "user-pipx:reticulum-meshchatx"


def _run(tmp_path, existing=None):
    text = (REPO / "scripts" / "install_meshchatx.sh").read_text()
    i = text.index(START)
    block = text[i:text.index(END, i)]
    f = tmp_path / "etc" / "rns_stray_waivers.json"
    if existing is not None:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(existing)
    script = ('set -uo pipefail\nas_root() { "$@"; }\n'
              f'RNS_STRAY_WAIVERS="{f}"\n{block}\necho BLOCK_DONE\n')
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    return r, f


def test_a_fresh_box_gets_the_declaration(tmp_path):
    r, f = _run(tmp_path)
    assert "BLOCK_DONE" in r.stdout, r.stderr
    assert LABEL in json.loads(f.read_text())["waived"]


def test_other_waivers_are_kept_and_a_rerun_is_idempotent(tmp_path):
    r, f = _run(tmp_path, json.dumps({"waived": {"foreign-venv:x": "kept"}}))
    w = json.loads(f.read_text())["waived"]
    assert w["foreign-venv:x"] == "kept" and LABEL in w
    r2, _ = _run(tmp_path, f.read_text())
    assert "already declared" in r2.stdout


def test_a_malformed_file_is_never_rewritten_and_said(tmp_path):
    r, f = _run(tmp_path, "{not json")
    assert f.read_text() == "{not json"
    assert "malformed" in r.stdout + r.stderr and "WARN" in r.stderr


def test_the_probe_reads_what_the_installer_wrote(tmp_path):
    sys.path.insert(0, str(REPO / "src"))
    mod = pytest.importorskip("utils.watchdog_probes_rns_env")  # MeshForge's probe
    _, f = _run(tmp_path)
    assert LABEL in mod._load_stray_waivers(str(f))
