"""BENCH-G red tests — widen bench_guard from Axiometa Headless to EVERY flasher.

Spec: .claude/plans/bench_g_threat_model_2026_11_17.md (threat model + operator
decisions 2026-11-17). Job: stop a COOPERATING session from ACCIDENTALLY
writing to, resetting or holding a serial port that is not a declared bench
board (lab box) or — for ``rnodeconf`` — that box's own RNode as declared in
rnsd's config. Seatbelt, not jail: plain shapes only; deliberate construction,
the ``meshtastic`` CLI and humans are out of scope.

RED by design: every refusal case is ``xfail(strict=True)`` until BENCH-G is
built — the guard that catches one turns it XPASS, which FAILS the suite, so
the marker must come off in the same commit (a red test can't silently stay
red, and can't silently start passing). The ALLOW cases are plain tests: they
pass today and pin that the widened guard adds no false refusal.

Environment the build must honour: ``BENCH_GUARD_RNS_CONFIG`` — a path to the
rnsd config the guard reads for the box's declared RNode port (default: the
real user's ``~/.reticulum/config`` then ``/etc/reticulum/config``).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "bench_guard.py")
sys.path.insert(0, os.path.dirname(_SCRIPT))
import bench_guard  # noqa: E402

BY_ID = "/dev/serial/by-id/"
BENCH = "D0:CF:13:0A:68:68"                       # the lab box's Genesis Mini
RNODE = "80:F1:B2:A7:C3:94"                       # the manager box's Heltec V4 RNode
OTHER = "44:1B:F6:6A:D3:B0"                       # someone else's radio
BENCH_PORT = f"{BY_ID}usb-Espressif_USB_JTAG_serial_debug_unit_{BENCH}-if00"
RNODE_PORT = f"{BY_ID}usb-Espressif_USB_JTAG_serial_debug_unit_{RNODE}-if00"
OTHER_PORT = f"{BY_ID}usb-Espressif_USB_JTAG_serial_debug_unit_{OTHER}-if00"
CP2102_PORT = f"{BY_ID}usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"

RED = pytest.mark.xfail(strict=True, reason="BENCH-G not built (threat model 2026-11-17)")


def _rns_config(port: str, enabled: str = "yes") -> str:
    return ("[reticulum]\n  share_instance = Yes\n\n[interfaces]\n"
            "  [[Regional RNode]]\n    type = RNodeInterface\n"
            f"    enabled = {enabled}\n    port = {port}\n"
            "    frequency = 903625000\n    bandwidth = 250000\n")


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A box: role, bench list and rnsd config set per test; witness to tmp."""
    dep, boards = tmp_path / "deployment.json", tmp_path / "bench_boards"
    rns, witness = tmp_path / "rns_config", tmp_path / "witness.log"
    monkeypatch.setenv("BENCH_GUARD_DEPLOYMENT", str(dep))
    monkeypatch.setenv("BENCH_GUARD_BOARDS", str(boards))
    monkeypatch.setenv("BENCH_GUARD_RNS_CONFIG", str(rns))
    monkeypatch.setenv("BENCH_GUARD_WITNESS", str(witness))

    class Box:
        def role(self, r):
            dep.write_text(json.dumps({"role": r}))
            return self

        def bench(self, *serials):
            boards.write_text("".join(s + "\n" for s in serials))
            return self

        def rnode(self, port, enabled="yes"):
            rns.write_text(_rns_config(port, enabled))
            return self
    return Box()


def bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def refused(payload) -> bool:
    return bench_guard.decide(payload) is not None


def manager(box):
    """The manager box's shape: role primary, the RNode its only port, declared in rnsd."""
    return box.role("primary").bench().rnode(RNODE_PORT)


def lab(box):
    return box.role("lab").bench(BENCH).rnode("/dev/null", enabled="no")


# ── A1 a flasher with NO port: auto-detect picks the only/first port ──────────

A1 = [
    "pio run -t upload",
    "pio run -e heltec-v4 --target upload",
    "platformio run --target upload",
    "esptool write_flash 0x0 firmware.bin",
    "esptool.py write_flash 0 firmware.bin",
    "esptool write-flash 0x0 firmware.bin",                  # esptool v5 spelling
    "python3 -m esptool write_flash 0 firmware.bin",
    "esptool erase_flash",
    "/home/u/.platformio/packages/tool-esptoolpy/esptool.py write_flash 0 x.bin",
    "idf.py flash",
    "arduino-cli upload --fqbn esp32:esp32:esp32s3 sketch",
    "mpremote cp main.py :",
    "rnodeconf --autoinstall",
    "sudo esptool write_flash 0 firmware.bin",
    "env PLATFORMIO_UPLOAD_PORT= pio run -t upload",
]


@RED
@pytest.mark.parametrize("cmd", A1)
def test_a1_no_port_is_refused_on_the_manager(box, cmd):
    manager(box)
    assert refused(bash(cmd))


@RED
@pytest.mark.parametrize("cmd", A1)
def test_a1_no_port_is_refused_even_on_the_lab(box, cmd):
    lab(box)
    assert refused(bash(cmd))


# ── A2 a port by KERNEL name: ttyACM*/ttyUSB* renumber, the name proves nothing ─

A2 = [
    "esptool --port /dev/ttyACM0 write_flash 0 firmware.bin",
    "esptool -p /dev/ttyUSB0 write_flash 0 firmware.bin",
    "pio run -t upload --upload-port /dev/ttyACM0",
    "arduino-cli upload -p /dev/ttyACM0 --fqbn esp32:esp32:esp32s3 sketch",
    "rnodeconf /dev/ttyACM0 --info",
    "idf.py -p /dev/ttyACM0 flash",
]


@RED
@pytest.mark.parametrize("cmd", A2)
def test_a2_kernel_name_port_is_refused_on_the_lab(box, cmd):
    lab(box)
    assert refused(bash(cmd))


# ── A3 by-id of a board NOT on the bench list (lab box) ───────────────────────

@RED
@pytest.mark.parametrize("cmd", [
    f"esptool --port {OTHER_PORT} write_flash 0 firmware.bin",
    f"pio run -t upload --upload-port {RNODE_PORT}",
    f"arduino-cli upload -p {OTHER_PORT} --fqbn esp32:esp32:esp32s3 sketch",
])
def test_a3_unlisted_by_id_is_refused_on_the_lab(box, cmd):
    lab(box)
    assert refused(bash(cmd))


@RED
def test_a3_bench_port_is_still_refused_off_the_lab(box):
    manager(box).bench(BENCH)          # a bench list on a non-lab box grants nothing
    assert refused(bash(f"esptool --port {BENCH_PORT} write_flash 0 firmware.bin"))


# ── A4 right command, wrong box: ssh — the remote role is unknowable here ─────

@RED
@pytest.mark.parametrize("cmd", [
    "ssh fleet-box-2 esptool write_flash 0 firmware.bin",
    "ssh fleet-box-2 pio run -t upload",
    f"ssh lab-box esptool --port {BENCH_PORT} write_flash 0 x.bin",
    "ssh fleet-box-k rnodeconf --autoinstall",
])
def test_a4_ssh_flasher_is_refused(box, cmd):
    lab(box)
    assert refused(bash(cmd))


# ── A5 a "read-only" device query still opens + resets the port ───────────────

@RED
@pytest.mark.parametrize("cmd", [
    "esptool chip_id",
    "esptool read_mac",
    f"esptool --port {RNODE_PORT} read_mac",
    f"esptool --port {RNODE_PORT} flash_id",
    "pio device monitor",
    f"pio device monitor --port {RNODE_PORT}",
    f"arduino-cli monitor -p {RNODE_PORT}",
    "idf.py monitor",
])
def test_a5_device_query_is_refused_on_the_manager(box, cmd):
    manager(box)
    assert refused(bash(cmd))


# ── A6 a NON-UNIQUE by-id serial on a bench list names no board ───────────────

@RED
def test_a6_generic_serial_on_the_bench_list_is_refused(box):
    box.role("lab").bench("0001").rnode("/dev/null", enabled="no")   # CP2102 0001: on two fleet boxes
    assert refused(bash(f"esptool --port {CP2102_PORT} write_flash 0 firmware.bin"))


# ── A7 rnodeconf on a fleet box: only the RNode rnsd's config declares ────────

@RED
@pytest.mark.parametrize("cmd", [
    f"rnodeconf {OTHER_PORT} --update",
    f"rnodeconf {OTHER_PORT} --info",
    f"rnodeconf {BENCH_PORT} --autoinstall",
])
def test_a7_rnodeconf_on_an_undeclared_port_is_refused(box, cmd):
    manager(box)
    assert refused(bash(cmd))


@RED
def test_a7_a_disabled_interface_declares_nothing(box):
    box.role("primary").bench().rnode(RNODE_PORT, enabled="no")
    assert refused(bash(f"rnodeconf {RNODE_PORT} --update"))


@RED
def test_a7_no_rnsd_config_declares_nothing(box):
    box.role("primary").bench()        # BENCH_GUARD_RNS_CONFIG points at a missing file
    assert refused(bash(f"rnodeconf {RNODE_PORT} --info"))


# ── A8 MCP flash tools named without "axiometa" ───────────────────────────────

@RED
@pytest.mark.parametrize("tool,args", [
    ("mcp__esp-idf__flash_project", {"project_path": "/home/u/fw"}),
    ("mcp__esp-idf__flash_project", {"project_path": "/home/u/fw", "port": "/dev/ttyACM0"}),
    ("mcp__platformio__upload", {"environment": "heltec-v4"}),
    ("mcp__esp32__flash", {"image": "fw.bin"}),
    ("mcp__esp-idf__monitor", {}),
])
def test_a8_mcp_flash_tool_is_refused_on_the_manager(box, tool, args):
    manager(box)
    assert refused({"tool_name": tool, "tool_input": args})


# ── ALLOW: what the widened guard must NOT refuse (green today; pins no FP) ───

ALLOW_EVERYWHERE = [
    "pio run",                                       # compile only (operator: keep pio)
    "pio run -e heltec-v4",
    "pio device list",                               # lists ports, opens none
    "arduino-cli compile --fqbn esp32:esp32:esp32s3 sketch",
    "arduino-cli board list",
    "esptool version",
    "esptool --help",
    "rnodeconf --help",
    "grep -rn esptool docs/",
    "grep -n write_flash notes.md",
    "git log -S upload_port --oneline",
    "cat platformio.ini",
    "ls ~/.platformio/packages",
    "command -v esptool pio arduino-cli rnodeconf",
    "meshtastic --info",                             # operator: meshtastic CLI is OUT of scope
    "meshtastic --set lora.region US",
]


@pytest.mark.parametrize("cmd", ALLOW_EVERYWHERE)
def test_allow_on_the_manager(box, cmd):
    manager(box)
    assert not refused(bash(cmd))


@pytest.mark.parametrize("cmd", ALLOW_EVERYWHERE)
def test_allow_on_the_lab(box, cmd):
    lab(box)
    assert not refused(bash(cmd))


@pytest.mark.parametrize("cmd", [
    f"esptool --port {BENCH_PORT} write_flash 0 firmware.bin",
    f"esptool --port {BENCH_PORT} chip_id",
    f"pio run -t upload --upload-port {BENCH_PORT}",
    f"pio device monitor --port {BENCH_PORT}",
    f"arduino-cli upload -p {BENCH_PORT} --fqbn esp32:esp32:esp32s3 sketch",
    f"rnodeconf {BENCH_PORT} --info",
])
def test_allow_bench_port_on_the_lab(box, cmd):
    lab(box)
    assert not refused(bash(cmd))


@pytest.mark.parametrize("cmd", [
    f"rnodeconf {RNODE_PORT} --info",
    f"rnodeconf {RNODE_PORT} --update",
])
def test_allow_rnodeconf_on_the_declared_rnode(box, cmd):
    manager(box)                       # 11-04 standing go for RNode changes
    assert not refused(bash(cmd))


def test_allow_rnodeconf_on_a_generic_serial_rnode(box):
    """A collector whose RNode IS the CP2102 `0001` (measured on the fleet 11-17): the A7 match is the full by-id name from
    rnsd's config, never the serial alone — the A6 rule must not break this."""
    box.role("collector").bench().rnode(CP2102_PORT)
    assert not refused(bash(f"rnodeconf {CP2102_PORT} --info"))


@pytest.mark.parametrize("tool", ["mcp__esp-idf__build_project", "mcp__platformio__list_devices"])
def test_allow_non_device_mcp_tools(box, tool):
    manager(box)
    assert not refused({"tool_name": tool, "tool_input": {}})
