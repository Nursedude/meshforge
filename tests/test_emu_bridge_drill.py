"""scripts/emu_bridge_drill.py refuses to run where its stores would land in a
REAL home (memory: HOME alone writes the live config when SUDO_USER/LOGNAME
are set — get_real_user_home reads them first)."""
import importlib.util
import pwd
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "emu_bridge_drill",
    Path(__file__).resolve().parent.parent / "scripts" / "emu_bridge_drill.py")
drill = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(drill)

REAL = pwd.getpwuid(__import__("os").getuid()).pw_dir


def test_refuses_with_sudo_user(tmp_path):
    with pytest.raises(drill.Refused, match="SUDO_USER"):
        drill.check_isolation({"HOME": str(tmp_path), "SUDO_USER": "x"})


def test_refuses_with_logname(tmp_path):
    with pytest.raises(drill.Refused, match="LOGNAME"):
        drill.check_isolation({"HOME": str(tmp_path), "LOGNAME": "x"})


def test_refuses_the_real_home():
    with pytest.raises(drill.Refused, match="real home"):
        drill.check_isolation({"HOME": REAL})


def test_refuses_no_home():
    with pytest.raises(drill.Refused):
        drill.check_isolation({})


def test_accepts_a_scratch_home(tmp_path):
    assert drill.check_isolation({"HOME": str(tmp_path)}) == tmp_path


def test_node_config_is_sim_only(tmp_path):
    cfg = drill.write_node_config(tmp_path)
    text = cfg.read_text()
    assert "Module: sim" in text
    assert "Webserver" not in text and "ConfigDirectory" not in text
