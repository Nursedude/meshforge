"""desktop_purge_audit.keep_set — the policy that decides what a headless
desktop purge must spare. Pinned from the 2026-10-26 fleet audit: a bare
`apt-get purge --auto-remove` of the desktop roots took python3-serial on
lehua (its mesh_bot venv sees system site-packages), and bc / i2c-tools /
python3-smbus / python3-flask / numpy / werkzeug on every box."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "desktop_purge_audit", ROOT / "scripts" / "desktop_purge_audit.py")
dpa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dpa)


def test_imported_gui_lib_is_kept_even_though_policy_allows_removing_it():
    # lehua: the bot imports pygame + picamera2, which PY_REMOVABLE would take
    remove = ["python3-pygame", "python3-picamera2", "python3-pyqt5"]
    keep = dpa.keep_set(remove, imported=["python3-pygame", "python3-picamera2"], roots=[])
    assert keep == ["python3-picamera2", "python3-pygame"]


def test_non_gui_python_lib_is_kept_without_any_import_evidence():
    # a static scan can miss a dynamic import; a few MB beats an outage.
    # None of these is in MUST_KEEP, so only the python3-* rule spares them.
    keep = dpa.keep_set(["python3-lxml", "python3-orjson", "python3-pyudev"], imported=[], roots=[])
    assert keep == ["python3-lxml", "python3-orjson", "python3-pyudev"]


def test_serial_is_kept_even_if_the_python_rule_were_widened():
    # RNode / meshtasticd serial tooling: MUST_KEEP is the second guard
    assert "python3-serial" in dpa.MUST_KEEP
    assert dpa.keep_set(["python3-serial"], imported=[], roots=[]) == ["python3-serial"]


def test_gui_camera_ide_python_libs_are_removable():
    remove = ["python3-pyqt5", "python3-pygame", "python3-mypy", "python3-cups", "python3-picamera2"]
    assert dpa.keep_set(remove, imported=[], roots=[]) == []


def test_must_keep_tools_measured_in_the_removal_set_are_kept():
    remove = ["bc", "i2c-tools", "python3-smbus", "lightdm", "pipewire"]
    assert dpa.keep_set(remove, imported=[], roots=["lightdm"]) == ["bc", "i2c-tools", "python3-smbus"]


def test_unattended_upgrades_cairo_chain_is_kept():
    # unattended-upgrades maps python3-cairo / gi-cairo through python3-gi
    keep = dpa.keep_set(["python3-cairo", "python3-gi-cairo"], imported=[], roots=[])
    assert keep == ["python3-cairo", "python3-gi-cairo"]


def test_a_root_is_never_kept_and_nothing_outside_the_removal_set_is_added():
    keep = dpa.keep_set(["python3-requests"], imported=["python3-numpy"], roots=["python3-requests"])
    assert keep == []


def test_desktop_packages_are_not_kept():
    remove = ["pipewire", "wireplumber", "xdg-desktop-portal-gtk", "cups-daemon", "linux-image-6.12.47+rpt-rpi-v8"]
    assert dpa.keep_set(remove, imported=[], roots=[]) == []
