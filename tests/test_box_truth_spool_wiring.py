"""MeshForge's spool WIRING of the NOC read grant — the parts that drive
``scripts/fleet_truth_spool.py`` and the live crontab (split out of
``test_box_truth_center.py`` in session 3, 2026-10-14, so that file can be
byte-identical with MeshAnchor). MeshAnchor's spool writer is session 3
step 3; until it exists these stay MeshForge-only.
"""
import json
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


# ── step 2 (2026-10-14): the BOX judges its role; the spool reads verdicts ──

ROOT = Path(__file__).resolve().parent.parent


def _load_spool():
    import importlib.util
    path = ROOT / "scripts" / "fleet_truth_spool.py"
    spec = importlib.util.spec_from_file_location("fleet_truth_spool_w", path)
    spool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spool)
    return spool


def _real_src(tmp_path, deployment):
    """The export's collector against THIS checkout's real provisioner and
    catalog (not the seam's /opt path, which CI does not have)."""
    from utils import box_truth_app as app
    from utils import box_truth_collect as bcol
    h = tmp_path / "home"
    d = h / ".config" / app.CONFIG_DIRNAME
    d.mkdir(parents=True)
    (d / "deployment.json").write_text(json.dumps(deployment))
    return bcol.Sources(home=h, env={}, watchdog_path=tmp_path / "wd.json",
                        proc_tcp=(str(tmp_path / "no-proc"),),
                        dev_globs=(str(tmp_path / "nodev*"),),
                        runner=lambda *a, **k: (_ for _ in ()).throw(OSError("no")),
                        http_get=lambda url: None,
                        provisioner=ROOT / "scripts" / "provision_role.py",
                        catalog=ROOT / "docs" / "fleet_roles.yaml")


@pytest.mark.parametrize("role, want", [
    ("gateway-only", {"http_surface": False, "watchdog": True}),   # moc3
    ("field-node", {"http_surface": False, "watchdog": False}),   # lehua
    ("lab", {"http_surface": False, "watchdog": True}),           # C21 bench box
])
def test_real_catalog_verdicts_match_what_the_center_used_to_decide(tmp_path, role, want):
    """The two live cases the center judged until today, now judged by the
    box from the same catalog: identical verdicts, so the /fleet cells do
    not move when the judgment moves."""
    from utils import box_truth_collect as bcol
    s, errors = bcol.collect_sections(_real_src(tmp_path, {"role": role}))
    assert "role" not in errors, errors
    assert s["role"]["expected"] == want
    assert s["role"]["name"] == role and s["role"]["app"] == "meshforge"


def test_real_catalog_map_running_role_expects_the_surface_and_watchdog(tmp_path):
    from utils import box_truth_collect as bcol
    s, _e = bcol.collect_sections(_real_src(tmp_path, {"role": "primary"}))
    assert s["role"]["expected"]["http_surface"] in (True, None)
    assert s["role"]["expected"]["watchdog"] is True


def test_real_catalog_unknown_role_is_none_with_the_reason(tmp_path):
    from utils import box_truth_collect as bcol
    s, errors = bcol.collect_sections(_real_src(tmp_path, {"role": "no-such-role"}))
    assert s["role"]["expected"] == {"http_surface": None, "watchdog": None}
    assert "no-such-role" in s["role"]["why"]


@pytest.mark.parametrize("section, want", [
    ({"expected": {"http_surface": False, "watchdog": True}}, (False, True)),
    ({"expected": {"http_surface": None, "watchdog": None}}, (None, None)),
    ({"expected": {"http_surface": "no", "watchdog": 0}}, (None, None)),  # never coerced
    ({"expected": None}, (None, None)),
    ({}, (None, None)),
    (None, (None, None)),            # an export older than step 2
    ("nope", (None, None)),
])
def test_spool_reads_the_box_verdict_and_nothing_else(section, want):
    spool = _load_spool()
    assert spool.expected_from_role(section) == want


def test_spool_writer_no_longer_judges_roles():
    """The design decision as a structural pin: the center carries NO role
    catalog and NO provisioner import for judging a target. A center of
    either app can read a box of either app."""
    spool = _load_spool()
    for gone in ("http_surface_expected", "watchdog_expected", "_unit_expected"):
        assert not hasattr(spool, gone), gone
    text = (ROOT / "scripts" / "fleet_truth_spool.py").read_text()
    assert "provision_role" not in text
    assert "fleet_roles" not in text


def test_spool_doc_carries_the_box_verdict_end_to_end(tmp_path):
    from unittest.mock import patch
    spool = _load_spool()
    home = tmp_path / "home"
    (home / ".config" / "meshforge").mkdir(parents=True)
    (home / ".config" / "meshforge" / "truth_spool_targets").write_text("moc3 noc\n")
    (home / ".ssh").mkdir(mode=0o700)
    spool.br.noc_key_path(home).write_text("scratch")
    doc = {"schema": "box_truth/v1",
           "sections": {"role": {"expected": {"http_surface": False, "watchdog": True}},
                        "deployment": {"role": "gateway-only"}}}

    def read(alias, expect, *, key_path=None, **_kw):
        return spool.br.ReadResult(alias=alias, outcome="ok", pinned=True, doc=doc)
    with patch.object(spool, "get_real_user_home", return_value=home), \
         patch.object(spool, "truth_spool_dir", return_value=tmp_path / "spool"), \
         patch.object(spool, "_expect_of", return_value=("SHA256:" + "A" * 43, None)), \
         patch.object(spool.br, "read_box", side_effect=read):
        assert spool.main() == 0
    out = json.loads((tmp_path / "spool" / "moc3.json").read_text())
    assert out["http_surface_expected"] is False
    assert out["watchdog_expected"] is True
    assert out["role"]["expected"]["watchdog"] is True
