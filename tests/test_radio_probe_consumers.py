"""MeshForge consumers of the radio verdict (radio-probe-usb-present-is-not-
connected-2026-11-15): the map's /api/status summary, the ssh-spool collector
and fleet_snapshot all reach the ONE status_blocks decision — presence is
never 'connected'; a held device is. Twin-portable rows live in
test_radio_probe_held.py."""
from unittest.mock import patch

import utils._map_status_endpoints as mse
from utils import fleet_snapshot
from utils.map_http_handler import MapRequestHandler


def _summary(held):
    h = MapRequestHandler.__new__(MapRequestHandler)
    with patch.object(mse, "_HAS_MESHTASTIC_CONN", True), \
            patch.object(mse, "tcp_port_listening", return_value=False), \
            patch("glob.glob", side_effect=lambda g: ["/dev/ttyACM0"] if "ACM" in g else []), \
            patch.object(mse, "tty_held", return_value=held) as th, \
            patch("socket.socket") as sock:
        s = h._get_radio_status_summary()
    sock.assert_not_called()
    th.assert_called_once_with(["/dev/ttyACM0"])
    return s


def test_map_summary_held_usb_is_connected_serial():
    s = _summary(True)
    assert (s["connected"], s["mode"], s["usb_held"]) == (True, "serial", True)


def test_map_summary_unheld_usb_is_unverified():
    s = _summary(False)
    assert (s["connected"], s["mode"]) == (None, "usb_unheld")
    assert s["usb_devices"] == ["/dev/ttyACM0"]          # evidence still shown


def test_map_summary_unknown_holder_is_unverified():
    s = _summary(None)
    assert (s["connected"], s["mode"], s["usb_held"]) == (None, "usb_holder_unknown", None)


def _collector_slo(monkeypatch, probe):
    import time
    from utils import fleet_truth_collector as c
    doc = {"schema": "truth_spool/v1", "alias": "box", "fetched_at": time.time(),
           "slo": None, "status": None, "raw_watchdog": {"ok": True, "ts": time.time()},
           "deployment": {"role": "gateway-only"}, "http_surface_expected": False,
           "radio_probe": probe}
    monkeypatch.setattr(c, "_read_spool", lambda alias, **k: doc)
    monkeypatch.setattr(c, "_http_get_json", lambda *a, **k: None)
    return c._fetch_peer("box", is_self=False, port=5000)["slo"]["radio"]


def test_collector_carries_a_held_device_to_connected(monkeypatch):
    r = _collector_slo(monkeypatch, {"tcp_listening": False, "usb_present": True,
                                     "usb_held": True})
    assert r == {"connected": True, "mode": "serial"}


def test_collector_never_collapses_unknown_holder_to_false(monkeypatch):
    # bool(None) would read 'not held' -> still unverified, but a stray
    # truthy value must not read held either; only real True counts.
    for held, mode in ((None, "usb_holder_unknown"), (False, "usb_unheld"),
                       ("true", "usb_holder_unknown")):
        r = _collector_slo(monkeypatch, {"tcp_listening": False, "usb_present": True,
                                         "usb_held": held})
        assert r == {"connected": None, "mode": mode}, held


def test_collector_old_spool_without_holder_is_unverified(monkeypatch):
    r = _collector_slo(monkeypatch, {"tcp_listening": False, "usb_present": True})
    assert r == {"connected": None, "mode": "usb_holder_unknown"}


# ── fleet_snapshot: the SAME list + holder scan + verdict (review r2 F3) ───
def _snapshot(devs, held, meshcore=False):
    with patch.object(fleet_snapshot, "_systemctl_state",
                      lambda unit, *a, **k: "not_running"), \
            patch("socket.socket") as sock, \
            patch.object(fleet_snapshot, "usb_radio_devices", return_value=devs), \
            patch("os.path.exists", side_effect=lambda p: meshcore and p == "/dev/ttyMeshCore"), \
            patch.object(fleet_snapshot, "tty_held", return_value=held) as th:
        sock.return_value.__enter__.return_value.connect_ex.return_value = 1
        r = fleet_snapshot._probe_radio()
    return r, th


def test_snapshot_held_meshcore_is_connected():
    r, th = _snapshot(["/dev/ttyACM0"], True, meshcore=True)
    assert (r["connected"], r["name"]) == (True, "meshcore")
    th.assert_called_once_with(["/dev/ttyACM0"])


def test_snapshot_unheld_meshcore_is_unverified_not_connected():
    for held in (False, None):
        r, _ = _snapshot(["/dev/ttyACM0"], held, meshcore=True)
        assert (r["connected"], r["name"]) == (None, "meshcore"), held


def test_snapshot_unheld_plain_usb_is_unverified_not_failed():
    # r2 F3: a dev board, no meshtasticd, no MeshCore symlink, on a MAP box
    # read connected=False -> FAILED via /fleet/slo while the spool said DARK.
    r, _ = _snapshot(["/dev/ttyACM0"], False)
    assert (r["connected"], r["name"]) == (None, "usb")


def test_snapshot_no_device_and_no_phoneapi_is_disconnected():
    r, th = _snapshot([], None)
    assert (r["connected"], r["name"]) == (False, None)
    th.assert_not_called()


# ── map: the holder scan is skipped when tcp already answers (r2 M4) ───────
def test_map_summary_skips_the_fd_scan_when_tcp_listens():
    h = MapRequestHandler.__new__(MapRequestHandler)
    with patch.object(mse, "_HAS_MESHTASTIC_CONN", True), \
            patch.object(mse, "tcp_port_listening", return_value=True), \
            patch("glob.glob", side_effect=lambda g: ["/dev/ttyACM0"] if "ACM" in g else []), \
            patch.object(mse, "tty_held") as th:
        s = h._get_radio_status_summary()
    th.assert_not_called()
    assert (s["connected"], s["mode"], s["usb_held"]) == (True, "tcp", None)


# ── the CLI status line says 'unverified', never 'disconnected' (r2 M10) ────
def test_cli_status_line_says_unverified_for_unknown(capsys):
    import io
    import json
    from utils import map_data_service as mds
    body = json.dumps({"collector": True,
                       "radio": {"mode": "usb_unheld", "connected": None}}).encode()
    with patch.object(mds, "_is_port_in_use", return_value=True), \
            patch("urllib.request.urlopen", return_value=io.BytesIO(body)):
        assert mds._check_server_status(5000) == 0
    out = capsys.readouterr().out
    assert "Radio: usb_unheld (unverified)" in out and "disconnected" not in out


# ── r3: the snapshot carries the MODE so a map box's DARK reason says which ─
def test_snapshot_reason_names_the_blindness_on_map_boxes():
    from utils import fleet_truth as ft
    for held, mode in ((False, "usb_unheld"), (None, "usb_holder_unknown")):
        r, _ = _snapshot(["/dev/ttyACM0"], held)
        assert r["mode"] == mode, r
        cell = ft._radio_cell({"radio": r})
        assert cell["state"] == ft.DARK and mode in cell["reason"], cell
