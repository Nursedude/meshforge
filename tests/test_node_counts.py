"""utils/node_counts.py — Dashboard > Node Count's sources (2026-09-25 live-truth pass).

The screen read "RNS destinations: 0" (rnstatus -a lists interfaces) and
"Meshtastic: HTTP API unavailable" (an ESP32-only API) on every meshtasticd
box. Pinned here: the shared rnpath parser, the radio's OWN count as the
authority, the map's count labelled as the map's view, and UNKNOWN — never 0 —
whenever a source could not be asked.
"""
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from utils import node_counts as nc  # noqa: E402

RNPATH = """\
<a1b2> is 1 hop  away via <x> on RNodeInterface[Regional RNode RF] expires 2026-09-26 06:00:00
<c3d4> is 2 hops away via <y> on TCPInterface[hub] expires 2026-09-26 06:00:00
<e5f6> is 0 hops away via <z> on LocalInterface[37428] expires 2026-09-26 06:00:00
"""


def test_rnpath_parser_separates_network_from_ipc():
    assert nc.parse_rnpath_table(RNPATH) == (2, 1)
    assert nc.parse_rnpath_table("") == (0, 0)


def _proc(out, rc=0):
    return subprocess.CompletedProcess([], rc, stdout=out, stderr="")


def test_rns_count_is_unknown_not_zero_when_rnpath_cannot_answer():
    with patch.object(nc.shutil, "which", lambda *_: None):
        assert nc.rns_path_table_counts()["network"] is None
    with patch.object(nc.shutil, "which", lambda *_: "/usr/bin/rnpath"), \
         patch.object(nc.subprocess, "run", side_effect=subprocess.TimeoutExpired("rnpath", 10)):
        r = nc.rns_path_table_counts()
        assert r["network"] is None and "timed out" in r["why"]
    with patch.object(nc.shutil, "which", lambda *_: "/usr/bin/rnpath"), \
         patch.object(nc.subprocess, "run", return_value=_proc(RNPATH)):
        assert nc.rns_path_table_counts()["network"] == 2


def test_the_radio_reports_its_own_count_with_age():
    ts = time.time() - 600
    line = (f"{ts:.6f} host meshtasticd[1]: INFO | [DeviceTelemetry] Sending local stats: "
            f"uptime=1, channel_utilization=4.0, air_util_tx=0.1, num_online_nodes=90, "
            f"num_total_nodes=334, noise_floor=-91\n")
    with patch.object(nc.subprocess, "run", return_value=_proc("x\n" + line)):
        r = nc.radio_self_report()
    assert (r["online"], r["total"]) == (90, 334) and 540 < r["age_s"] < 700


def test_no_telemetry_is_unknown_not_zero():
    with patch.object(nc.subprocess, "run", return_value=_proc("")):
        assert nc.radio_self_report()["online"] is None
    with patch.object(nc.subprocess, "run", side_effect=OSError("denied")):
        assert nc.radio_self_report()["online"] is None


class _Resp:
    def __init__(self, doc): self.b = json.dumps(doc).encode()
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_the_maps_count_is_its_view_and_absent_is_unknown():
    ok = {"source_diagnostics": {"meshtasticd": {"yielded": 191, "reason_if_zero": "ok", "notes": "via tcp"}}}
    with patch.object(nc.urllib.request, "urlopen", lambda *a, **k: _Resp(ok)):
        r = nc.meshtastic_radio_nodes()
    assert r["count"] == 191 and "map's view" in r["source"]
    with patch.object(nc.urllib.request, "urlopen", lambda *a, **k: _Resp({})):
        assert nc.meshtastic_radio_nodes()["count"] is None
    bad = {"source_diagnostics": {"meshtasticd": {"yielded": 0, "reason_if_zero": "tcp_refused"}}}
    with patch.object(nc.urllib.request, "urlopen", lambda *a, **k: _Resp(bad)):
        r = nc.meshtastic_radio_nodes()
    assert r["count"] is None and "tcp_refused" in r["why"]
    with patch.object(nc.urllib.request, "urlopen", side_effect=OSError("refused")):
        assert nc.meshtastic_radio_nodes()["count"] is None
