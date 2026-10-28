"""No test may reach NOAA: space_weather's network goes through ONE seam,
and tests/conftest.py replaces it for the whole session.

CI segfaulted twice (exit 139, no pytest summary) with a status-bar worker
thread mid-SSL-handshake to NOAA: 7d6b46c7 (2026-09-30, fixed per FILE in
c86306e4) and f9b5e0eb (2026-10-27) — the second leak came from
test_event_bus.py, which imports the status bar as
``src.launcher_tui.status_bar``, a different module object from the
``status_bar`` the per-file fixture patched. The stub therefore lives below
every alias, in ``utils.space_weather._urlopen``.
"""
import os
import re
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import space_weather as sw  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "utils", "space_weather.py")


def test_the_session_stub_refuses_the_network():
    try:
        sw._urlopen("https://services.swpc.noaa.gov/", timeout=1)
    except urllib.error.URLError as e:
        assert "network disabled in tests" in str(e)
    else:
        raise AssertionError("conftest did not stub space_weather._urlopen")


def test_every_network_call_goes_through_the_seam():
    text = open(SRC, encoding="utf-8").read()
    direct = [m.start() for m in re.finditer(r"urllib\.request\.urlopen\(", text)]
    # exactly one: inside _urlopen itself
    assert len(direct) == 1, f"direct urlopen calls bypass the seam: {len(direct)}"
    assert "def _urlopen(" in text


def test_a_real_conditions_read_is_the_no_network_path():
    data = sw.SpaceWeatherAPI(timeout=1).get_current_conditions()
    assert data.sources_answered == 0
