"""Installer side of B7 (phase 2, 2026-09-30): the shell installer and the
post-install check must agree with the TUI and with meshtasticd itself.

- install_noc.sh's USB/SPI menus classify through scripts/overlay_kind.py
  (the TUI's classify_overlay) — it used to classify by FILENAME and offered
  `Serial:` files meshtasticd ignores. A helper that cannot run must be LOUD.
- The placeholder config.yaml (installer heredocs + templates/config.yaml)
  carries `Module: auto`: with Module UNSET meshtasticd silently runs a
  SIMULATED radio (measured with --output-yaml). `auto` finds a CH341 /
  HAT+ EEPROM or exits loudly; a config.d overlay's Module wins.
- verify_post_install.sh asks meshtasticd (--output-yaml) which module it
  will use; its old grep read a COMMENTED `# Module: auto` as "Module: Module:".
Driven through the REAL shell functions/blocks, extracted from the scripts.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
from core.meshtasticd_templates import classify_overlay  # noqa: E402

INSTALL = _ROOT / "scripts" / "install_noc.sh"
VERIFY = _ROOT / "scripts" / "verify_post_install.sh"
TEMPLATES = _ROOT / "templates" / "available.d"
DIR_VAR = "MF_SCRIPT_DIR"             # the RUNNING checkout's scripts/ (both twins)


def _extract(script: Path, start: str, end_re: str) -> str:
    lines = script.read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith(start))
    j = next(n for n in range(i + 1, len(lines)) if re.match(end_re, lines[n]))
    return "\n".join(lines[i:j + 1]) + "\n"


def _classify_sh(tmp_path, files, install_dir):
    fn = _extract(INSTALL, "classify_template() {", r"^}$")
    drv = tmp_path / "drv.sh"
    drv.write_text("YELLOW=''; NC=''\n" + fn + f'{DIR_VAR}="$1"; shift\n'
                   'for f in "$@"; do classify_template "$f"; done\n')
    r = subprocess.run(["bash", str(drv), str(install_dir), *map(str, files)],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.split(), r.stderr


_MAP = {"ch341": "usb", "spi": "spi", "aux": "display", "ignored": "ignored"}


def test_installer_classifier_is_the_tui_classifier(tmp_path):
    files = sorted(TEMPLATES.glob("*.yaml"))
    assert files
    serial = tmp_path / "rak4631-usb.yaml"
    serial.write_text("Serial:\n  Device: auto\n")
    got, err = _classify_sh(tmp_path, files + [serial], _ROOT / "scripts")
    want = [_MAP[classify_overlay(f.read_text())] for f in files] + ["ignored"]
    assert got == want, list(zip([f.name for f in files + [serial]], got, want))
    assert err == "", err


def test_installer_classifier_stderr_noise_never_blanks_a_radio(tmp_path):
    # only stdout is the answer: PYTHONVERBOSE import chatter on stderr used
    # to merge into it and turn a real HAT into `ignored`, silently (reader H4)
    fn = _extract(INSTALL, "classify_template() {", r"^}$")
    drv = tmp_path / "drv.sh"
    drv.write_text("YELLOW=''; NC=''\n" + fn + f'{DIR_VAR}="$1"; shift\nclassify_template "$1"\n')
    r = subprocess.run(["bash", str(drv), str(_ROOT / "scripts"), str(TEMPLATES / "meshadv-mini.yaml")],
                       capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONVERBOSE="1"))
    assert r.stdout.split() == ["spi"], (r.stdout, r.stderr[-300:])


def test_meshtasticd_radio_menu_offers_hats_and_ch341_sticks(tmp_path):
    # a detected CH341 lands in the meshtasticd-radio (HAT) branch; its filter
    # kept only `spi` and hid every CH341 overlay (reader pair, 2026-09-30).
    # Drive the REAL filter line from the script.
    line = next(l.strip() for l in INSTALL.read_text().splitlines()
                if 'classify_template "$hat_file"' in l)
    fn = _extract(INSTALL, "classify_template() {", r"^}$")
    drv = tmp_path / "drv.sh"
    drv.write_text("YELLOW=''; NC=''\n" + fn + f'{DIR_VAR}="$1"; shift\n'
                   'for hat_file in "$@"; do\n  ' + line + '\n  basename "$hat_file"\ndone\n')
    names = ["lora-usb-meshtoad-e22.yaml", "meshtoad-spi.yaml", "meshadv-mini.yaml",
             "display-waveshare-2.8.yaml"]
    serial = tmp_path / "heltec-usb.yaml"
    serial.write_text("Serial:\n  Device: auto\n")
    r = subprocess.run(["bash", str(drv), str(_ROOT / "scripts"),
                        *[str(TEMPLATES / n) for n in names], str(serial)],
                       capture_output=True, text=True, timeout=60)
    assert r.stdout.split() == ["lora-usb-meshtoad-e22.yaml", "meshtoad-spi.yaml",
                                "meshadv-mini.yaml"], (r.stdout, r.stderr)


def test_usb_node_branch_installs_no_meshtasticd():
    text = INSTALL.read_text()
    a = text.index("        usb)\n")
    b = text.index("        none)\n", a)
    branch = text[a:b]
    assert 'DAEMON_TYPE="usb-direct"' in branch
    for forbidden in ("apt-get install", "meshtasticd.service", "classify_template",
                      "deploy_meshforge_templates"):
        assert forbidden not in branch, forbidden


def test_installer_classifier_failure_is_loud_and_never_offered(tmp_path):
    got, err = _classify_sh(tmp_path, [TEMPLATES / "meshadv-mini.yaml"], tmp_path / "nowhere")
    assert got == ["ignored"]
    assert "cannot classify meshadv-mini.yaml" in err, err


_PLACEHOLDER_OLD = "# Module: auto  # Disabled"


def test_placeholder_configs_set_module_auto():
    text = INSTALL.read_text()
    assert _PLACEHOLDER_OLD not in text
    assert len(re.findall(r"^  Module: auto$", text, re.M)) == 3, "the three fallback config.yaml heredocs"
    cfg = (_ROOT / "templates" / "config.yaml").read_text()
    assert re.search(r"^  Module: auto$", cfg, re.M), cfg[:400]
    assert "DO NOT set Module: auto" not in cfg


def test_dead_usb_native_upgrade_script_stays_deleted():
    # it wrote `Lora: SerialPath:` (the firmware reads SerialPath only under
    # GPS) plus a Module-less config.yaml: a silent simulated radio
    assert not (_ROOT / "scripts" / "upgrade_to_native.sh").exists()


def test_installer_names_no_deleted_template():
    for script in (INSTALL, VERIFY):
        text = script.read_text()
        for name in ("heltec-usb", "meshstick-usb", "meshtoad-usb", "rak4631-usb",
                     "station-g2-usb", "tbeam-usb", "usb-serial-generic"):
            assert name not in text, (script.name, name)


def _module_block():
    # from the marker through the `fi` that closes the exception guard
    lines = VERIFY.read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if "# Which radio module will meshtasticd ACTUALLY use?" in l)
    j = next(n for n in range(i + 1, len(lines)) if lines[n] == "    fi" and lines[n - 1] == "    esac")
    return "\n".join(lines[i:j + 1]) + "\n"


def _host_has_ch341_or_hatplus():
    for d in Path("/sys/bus/usb/devices").glob("*"):
        try:
            if (d / "idVendor").read_text().strip() == "1a86" and (d / "idProduct").read_text().strip() == "5512":
                return True
        except OSError:
            continue
    return os.access("/proc/device-tree/hat/product", os.R_OK)


def test_verify_asks_meshtasticd_which_module(tmp_path):
    """The block's parse + verdicts. The test host fence REFUSES a real
    `meshtasticd`, and CI may forbid user namespaces, so stubs on PATH stand
    in for meshtasticd / unshare / systemctl. The meshtasticd stub prints
    what the real binary printed for each case (measured 2026-09-30 with
    --output-yaml, incl. a yaml-cpp `*** Exception` for a malformed overlay)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "meshtasticd").write_text('#!/bin/sh\ncat "$STUB_OUT"\n[ "${STUB_RC:-0}" = 0 ]\n')
    (bindir / "unshare").write_text('#!/bin/sh\nwhile [ "${1#-}" != "$1" ]; do shift; done\nexec "$@"\n')
    (bindir / "systemctl").write_text('#!/bin/sh\n[ "${STUB_WANTED:-0}" = 1 ]\n')
    for f in bindir.iterdir():
        f.chmod(0o755)
    stubs = ("check_pass(){ echo \"PASS $2\"; }; check_fail(){ echo \"FAIL $2\"; }\n"
             "check_warn(){ echo \"WARN $2\"; }; check_info(){ echo \"INFO $2\"; }\n")
    cfg = tmp_path / "etc"
    (cfg / "config.d").mkdir(parents=True)
    drv = tmp_path / "drv.sh"
    drv.write_text(stubs + f'CONFIG_DIR={cfg}\nCONFIG_YAML={cfg}/config.yaml\n'
                   f'VERIFY_SCRIPT_DIR={_ROOT / "scripts"}\n' + _module_block())

    def verdict(stdout, wanted=True, overlay=None, rc=0, path=None):
        for f in (cfg / "config.d").glob("*"):
            f.unlink()
        if overlay:
            (cfg / "config.d" / "radio.yaml").write_text(overlay)
        out = tmp_path / "out.txt"
        out.write_text(stdout)
        env = dict(os.environ, STUB_OUT=str(out), STUB_RC=str(rc), STUB_WANTED="1" if wanted else "0",
                   PATH=path or f"{bindir}{os.pathsep}{os.environ['PATH']}")
        r = subprocess.run(["bash", str(drv)], capture_output=True, text=True, timeout=60, env=env)
        return r.stdout.strip()

    y = "Portduino is starting\nLora:\n  Module: {}\n  spidev: spidev0.0\nGeneral:\n  MaxNodes: 100\n"
    assert verdict(y.format("sim")).startswith("FAIL SIMULATED")
    assert verdict(y.format("sim"), wanted=False).startswith("INFO unset")      # not run here by design
    assert verdict(y.format("sx1262")).startswith("PASS sx1262")
    # a radio overlay whose Module meshtasticd did not take (e.g. `sx1276`) -> still auto
    assert verdict(y.format("auto"), overlay="Lora:\n  Module: sx1276\n  CS: 7\n").startswith("FAIL radio.yaml")
    # auto with no overlay: judged against the hardware autoconf would look for
    got = verdict(y.format("auto"))
    assert got.startswith("PASS auto" if _host_has_ch341_or_hatplus() else "WARN auto"), got
    # a config meshtasticd could not load -> UNKNOWN, never a pass
    assert verdict("Portduino is starting\n*** Exception yaml-cpp: error at line 2\nLora:\n  Module: auto\n"
                   ).startswith("WARN UNKNOWN — meshtasticd could not load")
    # control: a meshtasticd that answers nothing -> UNKNOWN, never a pass
    assert verdict("", rc=1).startswith("WARN UNKNOWN — could not ask")


def _firmware_module_names():
    from utils.paths import get_real_user_home
    h = get_real_user_home() / "mtd-build" / "firmware" / "src" / "platform" / "portduino" / "PortduinoGlue.h"
    if h.exists():
        block = h.read_text(errors="replace").split("loraModules", 1)[1].split("};", 1)[0]
        return set(re.findall(r'"([A-Za-z0-9]+)"', block))
    # measured from v2.7.26 PortduinoGlue.h loraModules
    return {"sim", "auto", "RF95", "sx1262", "sx1268", "sx1280", "lr1110", "lr1120", "lr1121", "LLCC68"}


def test_every_shipped_module_name_is_one_meshtasticd_knows():
    import yaml
    names = _firmware_module_names()
    assert {"RF95", "sx1262", "auto"} <= names, names
    bad = {}
    for f in sorted(TEMPLATES.glob("*.yaml")):
        lora = (yaml.safe_load(f.read_text()) or {}).get("Lora")
        m = lora.get("Module") if isinstance(lora, dict) else None
        if m is not None and m not in names:
            bad[f.name] = m
    assert bad == {}, bad            # `sx1276` silently configured nothing


def test_basic_config_writer_sets_module():
    text = (_ROOT / "src" / "config" / "config_file_manager.py").read_text()
    body = text[text.index("def _create_basic_config"):]
    body = body[:body.index("\n    def ", 1)]
    assert re.search(r"^Lora:\n  Module: auto$", body, re.M), "a Module-less config.yaml runs a SIMULATED radio"
