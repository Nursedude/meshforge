"""bridge_leg_down — a DECLARED mesh_bridge leg reads disconnected (2026-09-27).

Born from an operator drill: moc's SHORT_TURBO USB radio was pulled "to see
if the domain would notice — nope". These tests drive the probe with the
EXACT line shapes moc's gateway prints (two-bridge block, measured live).
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from utils.watchdog_probe_core import collect_dispositions, reset_dispositions
from utils.watchdog_probes import probe_bridge_leg_down

UP = "    LONG_FAST: connected   SHORT_TURBO: connected"
ST_DOWN = "    LONG_FAST: connected   SHORT_TURBO: disconnected"
RNS_LINE = "    Meshtastic: connected   RNS: connected"


@pytest.fixture(autouse=True)
def _reset():
    reset_dispositions()
    yield
    reset_dispositions()


def _home(tmp_path, mesh_bridge):
    cfg = tmp_path / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    if mesh_bridge is not None:
        (cfg / "gateway.json").write_text(json.dumps({"mesh_bridge": mesh_bridge}))
    return str(tmp_path)


MB = {"enabled": True,
      "primary": {"preset": "LONG_FAST"},
      "secondary": {"preset": "SHORT_TURBO", "connection_type": "serial"}}

RUNNING = lambda: ("ok", 1234)  # noqa: E731


def _run(tmp_path, lines, mb=MB, pid=RUNNING, **kw):
    return probe_bridge_leg_down(home=_home(tmp_path, mb), pid_status_fn=pid,
                                 lines_fn=lambda pattern: lines, **kw)


def _disp():
    return collect_dispositions()["bridge_leg_down"]


def test_fires_when_declared_leg_down_in_every_block(tmp_path):
    sig = _run(tmp_path, [ST_DOWN, RNS_LINE] * 6)
    assert sig is not None
    assert sig.subject == "mesh_bridge:SHORT_TURBO"
    assert sig.extra["readings"] == 6
    assert "by-id" in sig.detail


def test_clean_when_all_connected(tmp_path):
    assert _run(tmp_path, [UP, RNS_LINE] * 10) is None
    assert _disp()["disp"] == "clean"


def test_a_radio_reboot_does_not_page(tmp_path):
    # one or two disconnected blocks among connected ones: under debounce
    assert _run(tmp_path, [UP] * 4 + [ST_DOWN] * 2 + [UP] * 4) is None
    assert _disp()["disp"] == "indeterminate"


def test_too_few_readings_does_not_page(tmp_path):
    # gateway just (re)started: 3 blocks, all down — not yet a verdict
    assert _run(tmp_path, [ST_DOWN] * 3) is None
    assert _disp()["disp"] == "indeterminate"


def test_rns_dialect_line_is_not_read_as_a_leg(tmp_path):
    # "Meshtastic:"/"RNS:" labels are not declared presets
    assert _run(tmp_path, [RNS_LINE.replace("connected", "disconnected")] * 10
                + [UP] * 10) is None
    assert _disp()["disp"] == "clean"


def test_declared_leg_with_no_readings_is_not_clean(tmp_path):
    # the gateway prints nothing about SHORT_TURBO: silence is not health
    assert _run(tmp_path, ["    LONG_FAST: connected"] * 10) is None
    d = _disp()
    assert d["disp"] == "indeterminate"
    assert "SHORT_TURBO" in d["reason"]


def test_no_mesh_bridge_declared_is_inert(tmp_path):
    assert _run(tmp_path, [], mb={"enabled": False}) is None
    assert _disp()["disp"] == "inert"


def test_no_gateway_json_is_inert(tmp_path):
    assert _run(tmp_path, [], mb=None) is None
    assert _disp()["disp"] == "inert"


def test_unreadable_gateway_json_is_indeterminate(tmp_path):
    home = _home(tmp_path, None)
    (tmp_path / ".config" / "meshforge" / "gateway.json").write_text("{not json")
    assert probe_bridge_leg_down(home=home, pid_status_fn=RUNNING,
                                 lines_fn=lambda p: []) is None
    assert _disp()["disp"] == "indeterminate"


def test_journal_unobservable_is_indeterminate(tmp_path):
    assert _run(tmp_path, None) is None
    assert _disp()["disp"] == "indeterminate"


def test_gateway_absent_is_inert(tmp_path):
    assert _run(tmp_path, [ST_DOWN] * 10, pid=lambda: ("absent", None)) is None
    assert _disp()["disp"] == "inert"


def test_leg_label_falls_back_to_leg_name(tmp_path):
    mb = {"enabled": True, "primary": {"preset": "LONG_FAST"}, "secondary": {}}
    sig = _run(tmp_path, ["    LONG_FAST: connected   secondary: disconnected"] * 6,
               mb=mb)
    assert sig is not None and sig.subject == "mesh_bridge:secondary"
