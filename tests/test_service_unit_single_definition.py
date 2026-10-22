"""<app>.service has ONE definition: scripts/<app>.service == the unit
install_noc.sh writes — the NOC orchestrator.

2026-10-21: scripts/meshanchor.service was the interactive TUI LAUNCHER while
install_noc.sh wrote the ORCHESTRATOR under the same name; update.sh / the TUI
refresh copied the launcher over the running orchestrator (2026-10-19) and the
next restart crash-looped it 2,172 times. MeshForge carried the same pair
latent (moc was already overwritten). Byte-identical in both repos.
"""
import re
from pathlib import Path

REPO = Path(__file__).parent.parent
APP = "meshforge" if (REPO / "scripts" / "meshforge.service").exists() else "meshanchor"
TAG = APP.upper() + "_SERVICE"


def _code(text):
    return [l.rstrip() for l in text.splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def _heredoc():
    text = (REPO / "scripts" / "install_noc.sh").read_text()
    m = re.search(r"<< '" + TAG + r"'\n(.*?)\n" + TAG + r"\n", text, re.S)
    assert m, f"install_noc.sh no longer writes {APP}.service from a heredoc"
    return m.group(1)


def test_the_shipped_unit_is_the_one_install_noc_writes():
    assert _code((REPO / "scripts" / f"{APP}.service").read_text()) == _code(_heredoc())


def test_the_unit_runs_the_orchestrator_not_the_tui_launcher():
    ex = [l for l in _code(_heredoc()) if l.startswith("ExecStart=")]
    assert ex and "-m core.orchestrator" in ex[0] and "launcher.py" not in ex[0]
