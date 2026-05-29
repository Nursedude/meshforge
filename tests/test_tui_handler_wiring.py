"""Regression guards for TUI handler wiring defects found in the 2026-05-29
reliability audit (`.claude/plans/tui_audit_register.md`).

These pin three crash/logic bugs that reached `main` because nothing asserted
that cross-handler dispatch targets and RF helper imports actually resolve:

  1. site_planner imported a non-existent ``fresnel_zone_radius`` (ImportError).
  2. site_planner / rf_tools link-budget passed swapped args to
     ``free_space_path_loss`` -> ~60 dB error (silent, dangerous for HAMs).
  3. rns_interfaces + _rns_diagnostics_engine called a non-existent
     ``_repair_rns_shared_instance`` (AttributeError on the repair path).
"""

import math

from utils import rf


class TestRFHelperImports:
    """site_planner's Fresnel/link-budget paths must resolve their imports."""

    def test_fresnel_radius_exists_and_old_name_does_not(self):
        assert hasattr(rf, "fresnel_radius")
        assert not hasattr(rf, "fresnel_zone_radius"), (
            "site_planner used this name; if reintroduced, fix the import too"
        )

    def test_free_space_path_loss_exists(self):
        assert hasattr(rf, "free_space_path_loss")

    def test_link_budget_units_correct(self):
        # 1 km @ 915 MHz must be ~91.7 dB. The pre-fix bug (args swapped:
        # distance=freq, freq=dist-km) produced ~31.7 dB.
        fspl = rf.free_space_path_loss(1.0 * 1000.0, 915.0)
        assert 90.0 < fspl < 93.0

    def test_fresnel_matches_physics(self):
        # 5 km @ 915 MHz first Fresnel radius at midpoint ~= 20.2 m.
        r = rf.fresnel_radius(5.0, 915.0 / 1000.0)
        wl = 3e8 / 915e6
        d1 = d2 = 2500.0
        expected = math.sqrt(wl * d1 * d2 / (d1 + d2))
        assert abs(r - expected) < 0.5


class TestRNSRepairDispatchTarget:
    """The cross-handler repair call must hit a method that exists."""

    def test_repair_menu_method_exists(self):
        from handlers.rns_diagnostics import RNSDiagnosticsHandler
        assert hasattr(RNSDiagnosticsHandler, "_rns_repair_menu")
        assert not hasattr(RNSDiagnosticsHandler, "_repair_rns_shared_instance")

    def test_no_handler_source_calls_the_dead_method(self):
        # Belt-and-suspenders: the old name must not reappear as a call target
        # anywhere in the handler package.
        import pathlib
        import handlers
        pkg_dir = pathlib.Path(handlers.__file__).parent
        offenders = [
            str(p)
            for p in pkg_dir.glob("*.py")
            if "_repair_rns_shared_instance" in p.read_text()
        ]
        assert not offenders, f"dead repair method referenced in: {offenders}"
