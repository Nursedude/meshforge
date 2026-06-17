"""Tests for scripts/meshtastic_install_audit.py — the install-layout audit.

Exercises the pure classify() verdict logic + the enumerate_installs() glob
against synthetic site-packages in tmp_path, so it never depends on real fleet
state. The recurring update/install/env class (feedback_version_env_rigor):
the SAME meshtastic at divergent versions across venv / system-dist / pipx /
user-site drives the TUI's phantom "update available".
"""

import importlib.util
from pathlib import Path

# Import the audit script as a module — it lives in scripts/, off the package path.
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "meshtastic_install_audit.py"
_spec = importlib.util.spec_from_file_location("meshtastic_install_audit", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

InstallRecord = _mod.InstallRecord
classify = _mod.classify
enumerate_installs = _mod.enumerate_installs
render_table = _mod.render_table


def _rec(label, version, path=None, owner="svc"):
    return InstallRecord(label=label, path=path or f"/{label}", version=version, owner=owner)


class TestClassify:
    def test_no_floor_is_indeterminate_not_ok(self):
        report = classify([_rec("venv", "2.7.9")], None)
        assert report.verdict == "NO_FLOOR"

    def test_no_install(self):
        report = classify([], "2.7.9")
        assert report.verdict == "NO_INSTALL"

    def test_single_install_at_floor_is_ok(self):
        report = classify([_rec("venv", "2.7.9")], "2.7.9")
        assert report.verdict == "OK"
        assert report.consumer == "venv"

    def test_all_agree_at_floor_is_ok(self):
        report = classify(
            [_rec("venv", "2.7.9"), _rec("system-dist", "2.7.9"), _rec("user-pipx", "2.7.9")],
            "2.7.9")
        assert report.verdict == "OK"

    def test_benign_pipx_ahead_is_ok(self):
        # pipx CLI legitimately ahead of the venv lib, both >= floor — intended.
        report = classify([_rec("venv", "2.7.9"), _rec("user-pipx", "2.8.0")], "2.7.9")
        assert report.verdict == "OK"

    def test_fragmented_stray_below_floor(self):
        # The 2026-06-17 case: consumer (venv) fine, system-dist + pipx stale.
        report = classify(
            [_rec("venv", "2.7.9"), _rec("user-site", "2.7.9"),
             _rec("system-dist", "2.7.8"), _rec("root-pipx", "2.7.8")],
            "2.7.9")
        assert report.verdict == "FRAGMENTED"
        assert report.consumer == "venv"
        assert report.consumer_version == "2.7.9"
        below = {r.label for r in report.records if r.below_floor}
        assert below == {"system-dist", "root-pipx"}

    def test_stale_consumer_when_consumer_below_floor(self):
        report = classify([_rec("venv", "2.7.8"), _rec("system-dist", "2.7.8")], "2.7.9")
        # All agree (no fragmentation) but the consumer itself is stale.
        assert report.verdict == "STALE_CONSUMER"

    def test_stale_consumer_outranks_fragmented(self):
        # Consumer below floor AND divergent → STALE_CONSUMER is the stronger verdict.
        report = classify([_rec("venv", "2.7.8"), _rec("system-dist", "2.7.9")], "2.7.9")
        assert report.verdict == "STALE_CONSUMER"

    def test_consumer_is_user_site_when_no_venv(self):
        report = classify([_rec("user-site", "2.7.9"), _rec("system-dist", "2.7.8")], "2.7.9")
        assert report.consumer == "user-site"
        assert report.verdict == "FRAGMENTED"

    def test_below_floor_flags_are_set(self):
        report = classify([_rec("venv", "2.7.10"), _rec("system-dist", "2.7.8")], "2.7.9")
        flags = {r.label: r.below_floor for r in report.records}
        assert flags == {"venv": False, "system-dist": True}


class TestEnumerateInstalls:
    @staticmethod
    def _distinfo(site_dir, name, version):
        d = site_dir / f"{name}-{version}.dist-info"
        d.mkdir(parents=True)
        (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")

    def test_finds_venv_and_user_site_at_divergent_versions(self, tmp_path):
        mroot = tmp_path / "opt" / "meshforge"
        self._distinfo(mroot / "venv" / "lib" / "python3.13" / "site-packages",
                       "meshtastic", "2.7.9")
        home = tmp_path / "home" / "svc"
        self._distinfo(home / ".local" / "lib" / "python3.13" / "site-packages",
                       "meshtastic", "2.7.8")
        records = enumerate_installs(
            "meshtastic", meshforge_root=str(mroot), user="svc", user_home=str(home))
        by_label = {r.label: r.version for r in records}
        assert by_label.get("venv") == "2.7.9"
        assert by_label.get("user-site") == "2.7.8"
        # And the audit calls it fragmented (a below-floor stray exists).
        report = classify(records, "2.7.9")
        assert report.verdict == "FRAGMENTED"

    def test_no_install_when_trees_absent(self, tmp_path):
        records = enumerate_installs(
            "meshtastic", meshforge_root=str(tmp_path / "nope"),
            user="svc", user_home=str(tmp_path / "alsono"))
        # tmp roots are empty; only real system-wide installs (if any on the
        # host) could appear — assert no venv/user record from our empty roots.
        labels = {r.label for r in records}
        assert "venv" not in labels
        assert "user-site" not in labels


class TestRender:
    def test_fragmented_render_includes_reconcile(self):
        report = classify(
            [_rec("venv", "2.7.9", path="/opt/meshforge/venv/lib/python3.13/site-packages"),
             _rec("system-dist", "2.7.8", path="/usr/local/lib/python3.13/dist-packages", owner="root")],
            "2.7.9")
        out = render_table(report, show_fix=True)
        assert "FRAGMENTED" in out
        assert "Reconcile" in out
        assert "system-dist" in out
        assert "<FLOOR" in out

    def test_ok_render_has_no_reconcile(self):
        report = classify([_rec("venv", "2.7.9")], "2.7.9")
        out = render_table(report, show_fix=True)
        assert "VERDICT: OK" in out
        assert "Reconcile" not in out
