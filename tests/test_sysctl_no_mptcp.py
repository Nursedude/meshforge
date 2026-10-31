"""update.sh installs the no-MPTCP sysctl drop-in, so it never lives only as a
hand-placed file on a box (operator rule 2026-10-13: don't build things that
get forgotten).

2026-10-31, CVE-2026-98432 (IPv4 TCP use-after-free; the published root
exploit needs an MPTCP listener). Measured that day on all 11 boxes: 0 MPTCP
sockets in any state, and nothing in MeshForge or MeshAnchor uses MPTCP, while
`net.mptcp.enabled` was 1 (the kernel default) everywhere. Turning it off
removes that exploit path; the kernel fix itself still has to come from a
Raspberry Pi kernel >= 6.18.56 / 6.12.113.

Runs the REAL update.sh block with its paths pointed at a sandbox.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
UPDATE = REPO / "scripts" / "update.sh"
DROPIN = REPO / "templates" / "sysctl.d" / "90-meshforge-no-mptcp.conf"
START = "# Deploy the no-MPTCP sysctl drop-in."


def _block() -> str:
    text = UPDATE.read_text()
    i = text.index(START)
    j = text.index("\nfi\n", i) + len("\nfi\n")
    return text[i:j]


def _run(tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path]:
    d = tmp_path / "sysctl.d"
    block = (_block().replace("/etc/sysctl.d", str(d))
             .replace("sysctl -q -p", "echo APPLIED"))
    script = f'GREEN=""; NC=""; INSTALL_DIR="{REPO}"\n{block}\n'
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    return p, d


def test_the_dropin_disables_mptcp_and_nothing_else():
    keys = [l.split("=")[0].strip() for l in DROPIN.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]
    assert keys == ["net.mptcp.enabled"]
    assert "net.mptcp.enabled = 0" in DROPIN.read_text()


def test_installs_applies_once_then_is_idempotent(tmp_path):
    first, d = _run(tmp_path)
    assert first.returncode == 0, first.stderr
    assert (d / DROPIN.name).read_text() == DROPIN.read_text()
    assert first.stdout.count("APPLIED") == 1
    second, _ = _run(tmp_path)
    assert second.returncode == 0, second.stderr
    assert "APPLIED" not in second.stdout and "already current" in second.stdout


def test_never_restarts_a_service():
    assert "systemctl" not in _block()
