"""MeshForge's spool WIRING of the NOC read grant — the parts that drive
``scripts/fleet_truth_spool.py`` and the live crontab (split out of
``test_box_truth_center.py`` in session 3, 2026-10-14, so that file can be
byte-identical with MeshAnchor). MeshAnchor's spool writer is session 3
step 3; until it exists these stay MeshForge-only.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_box_truth_center import EXPECT, Fleet, bc, home  # noqa: E402,F401


def test_spool_writer_reads_the_same_targets_file(home):
    """One path, two consumers (hfm #5): the spool imports it from here."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fts", Path(__file__).resolve().parent.parent / "scripts" / "fleet_truth_spool.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.targets_path is bc.targets_path and mod.read_targets is bc.read_targets


def test_unpushed_target_reads_refused_grant_next_tick(home):
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "scripts" / "fleet_truth_spool.py"
    spec = importlib.util.spec_from_file_location("fleet_truth_spool_t", path)
    spool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spool)

    bc.set_target(bc.targets_path(home), "moc3", pinned=True)
    assert bc.unpush("moc3", "moc3", home=home, expect=EXPECT,
                     exec_=Fleet().exec_).ok
    targets = bc.read_targets(bc.targets_path(home))
    assert [t.alias for t in targets] == ["moc3"], "unpushed box dropped from the writer"
    _s, transport, _m = spool.fetch_box(targets[0], home=home)
    assert transport["outcome"] == "refused_grant"
    assert "box_truth_grant.py push moc3 --confirm moc3" in transport["reason"]


def test_spool_cron_line_is_the_live_one():
    """The line the tooling installs must be the one the manager runs today
    (wired by hand 2026-07-19) — so installing it on a fresh center changes
    nothing on this one."""
    try:
        tab = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("no crontab here")
    if "fleet_truth_spool.py" not in tab:
        pytest.skip("not a center")
    assert bc.SPOOL_CRON_LINE in tab
