"""SECURITY.md must say what the code does (ledger sec-doc-security-md-truth-2026-11-11).

The 03-02 version claimed 0.5.x supported and a localhost bind for 8 months
after both stopped being true. These pins fail when the version moves or a
LAN-exposed listener the installer opens is missing from **Known gaps**.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SECURITY_MD = REPO / "SECURITY.md"


def _version() -> str:
    text = (REPO / "src" / "__version__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "src/__version__.py has no __version__ line"
    return m.group(1)


def _known_gaps() -> str:
    text = SECURITY_MD.read_text(encoding="utf-8")
    m = re.search(r"### Known gaps.*?(?=\n### )", text, re.DOTALL)
    assert m, "SECURITY.md lost its Known gaps section"
    return m.group(0)


def test_supported_row_matches_current_minor():
    major, minor = _version().split(".")[:2]
    text = SECURITY_MD.read_text(encoding="utf-8")
    assert f"| {major}.{minor}.x (current `main`) | :white_check_mark: |" in text
    assert f"| < {major}.{minor}   | :x:" in text


def test_known_gaps_dated_to_current_version():
    assert f"(as of {_version()}," in _known_gaps()


def test_known_gaps_names_every_installed_lan_listener():
    gaps = _known_gaps()
    # Measured 2026-11-19 on a fleet unit (ss -ltnp): the map process holds
    # 0.0.0.0:5000 (HTTP) AND 0.0.0.0:5001 (WebSocket, bind follows the map).
    for needle in ("--port 5000", "5001", "bind_address", "MODE=\"0666\"",
                   "TCP Server"):
        assert needle in gaps, f"Known gaps no longer names {needle!r}"
