"""Tests for the shared requirements-floor SSOT (utils.requirements_floor).

This module is the ONE parser + ONE below-floor test that both the watchdog
``probe_dep_version_drift`` and the TUI ``version_checker`` import, so the
fleet floor constant can't drift between them (honest_failure_modes item 5;
the 2026-06-17 phantom-update class, feedback_version_env_rigor).
"""

from utils.requirements_floor import (
    default_core_requirements,
    read_floor,
    read_requirement_floors,
    version_below,
)


class TestReadRequirementFloors:
    def test_parses_ge_eq_compatible_pins(self, tmp_path):
        req = tmp_path / "r.txt"
        req.write_text(
            "# a comment\n"
            "meshtastic>=2.7.9\n"
            "pinned==1.2.3\n"
            "compat~=4.5\n"
            "bare-no-floor\n"            # no operator → no floor, ignored
            "withmarker>=3.0 ; python_version >= '3.9'\n"
        )
        floors = read_requirement_floors(
            ("meshtastic", "pinned", "compat", "bare-no-floor", "withmarker"), str(req)
        )
        assert floors == {
            "meshtastic": "2.7.9",
            "pinned": "1.2.3",
            "compat": "4.5",
            "withmarker": "3.0",
        }

    def test_only_requested_pkgs_returned(self, tmp_path):
        req = tmp_path / "r.txt"
        req.write_text("meshtastic>=2.7.9\nrich>=13.0.0\n")
        assert read_requirement_floors(("meshtastic",), str(req)) == {"meshtastic": "2.7.9"}

    def test_case_insensitive_name_match(self, tmp_path):
        req = tmp_path / "r.txt"
        req.write_text("Meshtastic>=2.7.9\n")
        assert read_requirement_floors(("meshtastic",), str(req)) == {"meshtastic": "2.7.9"}

    def test_unreadable_file_returns_empty(self, tmp_path):
        # Missing file → {} (indeterminate; the caller decides, never "compliant").
        assert read_requirement_floors(("meshtastic",), str(tmp_path / "nope.txt")) == {}


class TestReadFloor:
    def test_single_pkg_floor(self, tmp_path):
        req = tmp_path / "r.txt"
        req.write_text("meshtastic>=2.7.9\n")
        assert read_floor("meshtastic", str(req)) == "2.7.9"

    def test_absent_pkg_returns_none(self, tmp_path):
        req = tmp_path / "r.txt"
        req.write_text("rich>=13.0.0\n")
        assert read_floor("meshtastic", str(req)) is None

    def test_real_core_txt_carries_meshtastic_floor(self):
        # Guards the live SSOT path the probe + checker both rely on: the
        # repo's requirements/core.txt must carry a parseable meshtastic floor.
        floor = read_floor("meshtastic")
        assert floor is not None and floor[0].isdigit()

    def test_default_core_requirements_points_at_core_txt(self):
        p = default_core_requirements()
        assert p.name == "core.txt"
        assert p.parent.name == "requirements"


class TestVersionBelow:
    def test_below_floor_true(self):
        assert version_below("2.7.8", "2.7.9") is True

    def test_at_floor_false(self):
        assert version_below("2.7.9", "2.7.9") is False

    def test_above_floor_false(self):
        # 2.7.10 > 2.7.9 — would be a false "below" under a naive string compare.
        assert version_below("2.7.10", "2.7.9") is False

    def test_missing_side_false(self):
        assert version_below(None, "2.7.9") is False
        assert version_below("2.7.9", None) is False
        assert version_below("", "2.7.9") is False

    def test_unparseable_false(self):
        assert version_below("not-a-version", "2.7.9") is False
