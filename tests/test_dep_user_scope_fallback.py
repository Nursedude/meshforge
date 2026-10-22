"""dep_* user-scope on rnsd-less boxes (decided 2026-10-22, provenance 10-21 tail).

`dep_version_drift`, `dep_install_fragmented` and `rns_stray_env_drift` derived
the user-scope (user-site / user-pipx) owner ONLY from rnsd's `User=`. On a box
whose role declares `rnsd: absent` (meshforge-lab, field-node) that is None, so
the import-priority user scope was never enumerated and all three read
`indeterminate` forever — a real blind spot (bench scripts import from it), not
an inert case.

Cure: ONE helper, `dep_scope_user()`, falls back to the operator who owns the
systemd --user manager — the same root-safe source `deployment_declaration_path`
already falls back to on rnsd-absent boxes (09-08) — and only when there is NO
rnsd unit at all (a readable unit with no User= is implicit root: stays dark). The reason string says which scope was read, every tick.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions, dep_scope_user, reset_dispositions,
)
from utils.watchdog_probes_drift import (  # noqa: E402
    probe_dep_install_fragmented, probe_dep_version_drift,
)
from utils.watchdog_probes_rns_env import probe_rns_env_coherence  # noqa: E402

_RNSD = "utils.rns_tree_perms._read_rnsd_user"
_OP = "utils.fleet_test_runner._find_operator_user"
_UNIT = "utils.rns_tree_perms.rnsd_unit_readable"


def _entry(cls):
    return collect_dispositions().get(cls) or {}


class TestDepScopeUser:
    def test_rnsd_user_wins(self):
        with patch(_RNSD, return_value="svc"), patch(_OP, return_value=(1000, "op")):
            assert dep_scope_user() == ("svc", "rnsd")

    def test_no_rnsd_user_falls_back_to_operator(self):
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), patch(_OP, return_value=(1000, "op")):
            assert dep_scope_user() == ("op", "operator")

    def test_no_rnsd_user_and_no_operator_is_unresolved(self):
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), patch(_OP, return_value=None):
            assert dep_scope_user() == (None, "none")

    def test_explicit_root_rnsd_is_not_replaced(self):
        # User=root is a positive observation, not an absence — no fallback.
        with patch(_RNSD, return_value="root"), patch(_OP, return_value=(1000, "op")):
            assert dep_scope_user() == ("root", "rnsd")

    def test_implicit_root_rnsd_stays_root_not_operator(self):
        # Non-author pass 2026-10-22: a READABLE unit with no User= is rnsd
        # running as root — an observation, never "no rnsd" (stays dark).
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=True), \
                patch(_OP, return_value=(1000, "op")):
            assert dep_scope_user() == ("root", "rnsd")

    def test_raising_rnsd_reader_is_unresolved_not_operator(self):
        with patch(_RNSD, side_effect=OSError("boom")), \
                patch(_OP, return_value=(1000, "op")):
            assert dep_scope_user() == (None, "none")


class TestDepVersionDriftUserScope:
    def setup_method(self):
        reset_dispositions()

    def test_rnsd_less_box_enumerates_operator_scope_and_reads_clean(self, monkeypatch):
        import utils.watchdog_probes_drift as d
        seen = []
        monkeypatch.setattr(d, "_enumerate_pkg_installs",
                            lambda pkg, user, **kw: seen.append(user) or {"system-dist": "9.9.9"})
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), patch(_OP, return_value=(1000, "op")):
            assert probe_dep_version_drift(floors={"meshtastic": "2.0.0"}) is None
        assert seen == ["op"]
        e = _entry("dep_version_drift")
        assert e.get("disp") == "clean"
        assert "operator" in (e.get("reason") or "")

    def test_no_operator_either_stays_indeterminate(self, monkeypatch):
        import utils.watchdog_probes_drift as d
        monkeypatch.setattr(d, "_enumerate_pkg_installs",
                            lambda pkg, user, **kw: {"system-dist": "9.9.9"})
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), patch(_OP, return_value=None):
            assert probe_dep_version_drift(floors={"meshtastic": "2.0.0"}) is None
        e = _entry("dep_version_drift")
        assert e.get("disp") == "indeterminate"
        assert "unobservable" in (e.get("reason") or "")

    def test_implicit_root_rnsd_box_stays_indeterminate(self, monkeypatch):
        import utils.watchdog_probes_drift as d
        monkeypatch.setattr(d, "_enumerate_pkg_installs",
                            lambda pkg, user, **kw: {"system-dist": "9.9.9"})
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=True), \
                patch(_OP, return_value=(1000, "op")):
            assert probe_dep_version_drift(floors={"meshtastic": "2.0.0"}) is None
        e = _entry("dep_version_drift")
        assert e.get("disp") == "indeterminate"
        assert "read as operator" not in (e.get("reason") or "")

    def test_injected_installed_never_claims_operator_scope(self):
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), \
                patch(_OP, return_value=(1000, "op")):
            assert probe_dep_version_drift(
                floors={"meshtastic": "2.0.0"},
                installed={"meshtastic": "9.9.9"}) is None
        e = _entry("dep_version_drift")
        assert e.get("disp") == "clean"
        assert "read as operator" not in (e.get("reason") or "")

    def test_rnsd_user_box_unchanged_plain_clean(self, monkeypatch):
        import utils.watchdog_probes_drift as d
        seen = []
        monkeypatch.setattr(d, "_enumerate_pkg_installs",
                            lambda pkg, user, **kw: seen.append(user) or {"venv": "9.9.9"})
        with patch(_RNSD, return_value="svc"), patch(_OP, return_value=(1000, "op")):
            assert probe_dep_version_drift(floors={"meshtastic": "2.0.0"}) is None
        assert seen == ["svc"]
        assert _entry("dep_version_drift").get("disp") == "clean"


class TestDepInstallFragmentedUserScope:
    def setup_method(self):
        reset_dispositions()

    def test_rnsd_less_box_enumerates_operator_scope(self, monkeypatch, tmp_path):
        import utils.watchdog_probes_drift as d
        seen = []
        monkeypatch.setattr(d, "_enumerate_pkg_installs",
                            lambda pkg, user, **kw: seen.append(user) or {"system-dist": "9.9.9"})
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), patch(_OP, return_value=(1000, "op")):
            assert probe_dep_install_fragmented(
                floor="2.0.0", state_path=str(tmp_path / "s.json")) is None
        assert seen == ["op"]
        e = _entry("dep_install_fragmented")
        assert e.get("disp") == "clean"
        assert "operator" in (e.get("reason") or "")

    def test_operator_scope_fragmentation_fires(self, monkeypatch, tmp_path):
        # The point of looking: a bench-fragmented user-site copy is a FINDING.
        import utils.watchdog_probes_drift as d
        monkeypatch.setattr(d, "_enumerate_pkg_installs",
                            lambda pkg, user, **kw: {"system-dist": "9.9.9",
                                                     "user-site": "1.0.0"})
        sp = str(tmp_path / "s.json")
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), patch(_OP, return_value=(1000, "op")):
            probe_dep_install_fragmented(floor="2.0.0", state_path=sp)
            sig = probe_dep_install_fragmented(floor="2.0.0", state_path=sp)
        assert sig is not None and sig.cls == "dep_install_fragmented"

    def test_no_operator_either_stays_indeterminate(self, monkeypatch, tmp_path):
        import utils.watchdog_probes_drift as d
        monkeypatch.setattr(d, "_enumerate_pkg_installs",
                            lambda pkg, user, **kw: {"system-dist": "9.9.9"})
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), patch(_OP, return_value=None):
            probe_dep_install_fragmented(floor="2.0.0", state_path=str(tmp_path / "s.json"))
        assert _entry("dep_install_fragmented").get("disp") == "indeterminate"


class TestRnsStrayEnvUserScope:
    def setup_method(self):
        reset_dispositions()

    def test_rnsd_less_box_enumerates_operator_scope(self, monkeypatch, tmp_path):
        import utils.watchdog_probes_rns_env as r
        seen = []
        monkeypatch.setattr(r, "_enumerate_lib_installs",
                            lambda pkg, user, **kw: seen.append(user) or {"system-dist": "1.3.8"})
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), patch(_OP, return_value=(1000, "op")):
            assert probe_rns_env_coherence(
                state_path=str(tmp_path / "s.json"),
                waivers_path=str(tmp_path / "w.json")) is None
        assert seen and set(seen) == {"op"}
        e = _entry("rns_stray_env_drift")
        assert e.get("disp") == "clean"
        assert "operator" in (e.get("reason") or "")

    def test_no_operator_either_stays_indeterminate(self, monkeypatch, tmp_path):
        import utils.watchdog_probes_rns_env as r
        monkeypatch.setattr(r, "_enumerate_lib_installs",
                            lambda pkg, user, **kw: {"system-dist": "1.3.8"})
        with patch(_RNSD, return_value=None), patch(_UNIT, return_value=False), patch(_OP, return_value=None):
            probe_rns_env_coherence(state_path=str(tmp_path / "s.json"),
                                      waivers_path=str(tmp_path / "w.json"))
        assert _entry("rns_stray_env_drift").get("disp") == "indeterminate"
