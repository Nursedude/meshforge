"""Setup wizard › USB: route by what the device IS, write only what works.

meshtasticd 2.7.26 has no `Serial:` key (queued B7, re-measured 2026-09-30),
so the `config.d/usb-serial.yaml` the wizard used to write for USB nodes
configured nothing, and its template list (chosen by filename) offered those
inert files beside the real CH341 `lora-usb-*` configs. Driven through the
REAL `_wizard_step_usb_config`, with /etc/meshtasticd redirected to tmp and
the result read back from disk.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT / "src"), str(_ROOT / "src" / "launcher_tui"), str(_ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from handler_test_utils import make_handler_context  # noqa: E402

SERIAL_ONLY = "Serial:\n  Device: auto\n"
CH341 = ("Lora:\n  Module: sx1262\n  CS: 0\n  IRQ: 6\n  spidev: ch341\n"
         "Webserver:\n  Port: 443\n")            # forbidden key: must be stripped
SPI_HAT = "Lora:\n  Module: sx1262\n  CS: 21\n  IRQ: 16\n"


def _seed(tmp_path):
    avail = tmp_path / "etc" / "meshtasticd" / "available.d"
    avail.mkdir(parents=True)
    (avail / "rak4631-usb.yaml").write_text(SERIAL_ONLY)        # old inert file
    (avail / "lora-usb-meshtoad-e22.yaml").write_text(CH341)
    (avail / "meshadv-mini.yaml").write_text(SPI_HAT)
    return avail


# The REAL scanner shape: a USB node appears in BOTH lists — an lsusb row
# (no tty) and a serial-port row (with the tty). A fixture with only the
# serial row hid the lost-tty bug (reader pair, 2026-09-30).
RAK_PORT = SimpleNamespace(usb_vendor="239a", usb_product="8029",
                           description="RAK4631", device="/dev/ttyUSB3")
RAK_LSUSB = SimpleNamespace(vendor_id="239a", product_id="8029", description="RAK4631")
CH341_STICK = SimpleNamespace(vendor_id="1a86", product_id="5512", description="CH341")
FTDI_PORT = SimpleNamespace(usb_vendor="0403", usb_product="6001",
                            description="FT232R GPS", device="/dev/ttyUSB0")
FTDI_LSUSB = SimpleNamespace(vendor_id="0403", product_id="6001", description="FT232R")
NODE = {"usb_devices": [RAK_LSUSB], "serial_ports": [RAK_PORT]}
STICK = {"usb_devices": [CH341_STICK], "serial_ports": []}
MIXED = {"usb_devices": [FTDI_LSUSB, CH341_STICK], "serial_ports": [FTDI_PORT]}
NOTHING = {"usb_devices": [], "serial_ports": []}


def _run(tmp_path, scan, menu=(), yesno=()):
    """Drive the step with a SCANNER result (exists pre- and post-fix), so a
    red run on the old code is red for its BEHAVIOUR, not a missing name.
    yesno answers Yes where the old flow asked "apply detected template?"."""
    from handlers import first_run
    real_path = first_run.Path

    def redirect(p, *a):
        s = str(p)
        if s.startswith("/etc/meshtasticd"):
            return real_path(tmp_path) / s.lstrip("/")
        return real_path(p, *a)

    h = first_run.FirstRunHandler()
    h.set_context(make_handler_context())
    h.ctx.dialog._menu_returns = list(menu)
    h.ctx.dialog._yesno_returns = list(yesno)
    restarts = []
    with patch.object(first_run, "Path", side_effect=redirect), \
         patch.object(first_run, "apply_config_and_restart",
                      side_effect=lambda u: (restarts.append(u), (True, "ok"))[1]), \
         patch.object(first_run.FirstRunHandler, "_ensure_template_structure", lambda self: None), \
         patch.object(first_run, "DeviceScanner") as ds:
        ds.return_value.scan_all.return_value = scan
        h._wizard_step_usb_config()
    config_d = tmp_path / "etc" / "meshtasticd" / "config.d"
    written = sorted(config_d.glob("*.yaml")) if config_d.exists() else []
    return h, written, restarts


def test_standalone_node_is_confirmed_writes_nothing_and_names_real_knobs(tmp_path):
    _seed(tmp_path)
    h, written, restarts = _run(tmp_path, NODE, menu=["node"], yesno=[True])
    assert written == [] and restarts == [], (written, restarts)
    menus = [a for n, a, kw in h.ctx.dialog.calls if n == 'menu']
    assert "/dev/ttyUSB3" in menus[0][1], menus[0][1]      # the tty survived the join
    text = h.ctx.dialog.last_msgbox_text or ""
    assert "Nothing was written" in text and "/dev/ttyUSB3" in text, text
    # knobs a consumer READS (mesh_bridge.py _connect_interface, rns_transport.py)
    assert "mesh_bridge.secondary" in text and "rns_transport" in text, text
    assert "TCP/MQTT" in text                            # the RNS message bridge's truth


def test_a_ch341_beside_a_serial_device_still_takes_the_ch341_route(tmp_path):
    _seed(tmp_path)
    h, written, _ = _run(tmp_path, MIXED, menu=[None])
    menus = [a for n, a, kw in h.ctx.dialog.calls if n == 'menu']
    assert [t for t, _ in menus[0][2]] == ["lora-usb-meshtoad-e22.yaml"], menus


def test_node_route_skips_the_meshtasticd_steps():
    from handlers import first_run
    h = first_run.FirstRunHandler()
    h.set_context(make_handler_context())
    h.ctx.dialog._yesno_returns = [True]                 # "run the setup wizard?"
    calls = []
    with patch.object(first_run.FirstRunHandler, "_wizard_step_connection_type", lambda self: "usb"), \
         patch.object(first_run.FirstRunHandler, "_wizard_step_usb_config", lambda self: "node"), \
         patch.object(first_run.FirstRunHandler, "_wizard_step_region", lambda self: calls.append("region")), \
         patch.object(first_run.FirstRunHandler, "_wizard_step_services", lambda self: calls.append("services")), \
         patch.object(first_run.FirstRunHandler, "_wizard_complete", lambda self: calls.append("complete")):
        h._run_first_run_wizard()
    assert calls == ["complete"], calls


def test_ch341_menu_offers_only_ch341_overlays(tmp_path):
    _seed(tmp_path)
    h, written, _ = _run(tmp_path, STICK, menu=[None])
    menus = [a for n, a, kw in h.ctx.dialog.calls if n == 'menu']
    offered = [tag for tag, _ in menus[-1][2]]
    assert offered == ["lora-usb-meshtoad-e22.yaml"], offered   # not the Serial file, not the HAT
    assert written == []                                          # cancelled


def test_ch341_choice_installs_the_sanitized_lora_overlay(tmp_path):
    _seed(tmp_path)
    h, written, restarts = _run(tmp_path, STICK, menu=["lora-usb-meshtoad-e22.yaml"])
    assert [w.name for w in written] == ["lora-usb-meshtoad-e22.yaml"], written
    doc = yaml.safe_load(written[0].read_text())
    assert doc["Lora"]["spidev"] == "ch341" and "Webserver" not in doc, doc
    assert restarts == ["meshtasticd"]


def test_unknown_device_asks_the_kind_and_node_answer_writes_nothing(tmp_path):
    _seed(tmp_path)
    h, written, restarts = _run(tmp_path, NOTHING, menu=["node"])
    assert written == [] and restarts == []
    assert "Nothing was written" in (h.ctx.dialog.last_msgbox_text or "")


def test_ch341_with_no_overlay_says_where_they_come_from_and_writes_nothing(tmp_path):
    avail = tmp_path / "etc" / "meshtasticd" / "available.d"
    avail.mkdir(parents=True)
    (avail / "rak4631-usb.yaml").write_text(SERIAL_ONLY)          # only an inert file
    h, written, restarts = _run(tmp_path, STICK, yesno=[True])
    assert written == [] and restarts == []
    text = h.ctx.dialog.last_msgbox_text or ""
    assert "reinstall or upgrade" in text and "Nothing was written" in text, text


def test_detection_maps_ids_to_kinds_never_templates():
    from handlers import first_run
    h = first_run.FirstRunHandler()
    h.set_context(make_handler_context())
    with patch.object(first_run, "DeviceScanner") as ds:
        ds.return_value.scan_all.return_value = NODE
        assert h._detect_usb_radio()[0::2] == ("node", "/dev/ttyUSB3")
        ds.return_value.scan_all.return_value = MIXED
        assert h._detect_usb_radio()[0] == "ch341"
        ds.return_value.scan_all.return_value = STICK
        assert h._detect_usb_radio()[0] == "ch341"
        ds.return_value.scan_all.return_value = NOTHING
        assert h._detect_usb_radio() == (None, None, None)


def test_radio_menu_hides_ignored_overlays(tmp_path):
    avail = _seed(tmp_path)
    (avail / "display-waveshare-2.8.yaml").write_text("Display:\n  Panel: ST7789\n")
    from handlers.meshtasticd_radio import MeshtasticdRadioHandler
    h = MeshtasticdRadioHandler()
    kinds = {f.name: h._classify_hardware_config(f) for f in sorted(avail.glob("*.yaml"))}
    assert kinds == {"lora-usb-meshtoad-e22.yaml": "usb", "meshadv-mini.yaml": "spi",
                     "rak4631-usb.yaml": "ignored",
                     "display-waveshare-2.8.yaml": "aux"}, kinds   # aux: not a radio


def test_recovery_menu_offers_only_radio_configs(tmp_path):
    # launcher's "meshtasticd failed to start" recovery used to offer EVERY
    # available.d file — incl. `Serial:`-only ones older installs left behind
    avail = _seed(tmp_path)
    (avail / "display-waveshare-2.8.yaml").write_text("Display:\n  Panel: ST7789\n")
    import launcher
    offered = []

    class FakeDialog:
        def menu(self, title, text, choices):
            offered.extend(t for t, _ in choices)
            return "skip"

    real_path = launcher.Path

    def redirect(p, *a):
        s = str(p)
        if s.startswith("/etc/meshtasticd"):
            return real_path(tmp_path) / s.lstrip("/")
        return real_path(p, *a)

    with patch.object(launcher, "Path", side_effect=redirect), \
         patch("launcher_tui.backend.DialogBackend", FakeDialog):
        assert launcher._make_config_recovery_callback()() is False
    assert offered == ["lora-usb-meshtoad-e22.yaml", "meshadv-mini.yaml", "skip"], offered


def test_orchestrator_warns_on_an_ignored_active_overlay(tmp_path, caplog):
    import logging
    from core import orchestrator
    inert = tmp_path / "usb-serial.yaml"
    inert.write_text("Serial:\n  Device: /dev/ttyACM0\nWebserver:\n  Port: 443\n")
    radio = tmp_path / "meshadv-mini.yaml"
    radio.write_text(SPI_HAT)
    with caplog.at_level(logging.WARNING):
        orchestrator._warn_ignored_overlays([inert, radio])
    assert "usb-serial.yaml" in caplog.text and "meshadv-mini" not in caplog.text, caplog.text
