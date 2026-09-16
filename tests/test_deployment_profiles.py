"""Tests for deployment profile system.

Validates profile definitions, auto-detection, persistence, and validation.
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add src to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from utils.deployment_profiles import (
    ProfileName,
    ProfileDefinition,
    ProfileHealth,
    PROFILES,
    detect_profile,
    validate_profile,
    save_profile,
    load_profile,
    load_or_detect_profile,
    get_profile_by_name,
    list_profiles,
    FEATURE_FLAGS,
)


class TestProfileDefinitions:
    """Verify profile definitions are complete and consistent."""

    def test_all_profiles_have_unique_names(self):
        """Each profile must have a unique ProfileName."""
        names = [p.name for p in PROFILES.values()]
        assert len(names) == len(set(names))

    def test_every_enum_member_has_a_profile(self):
        """PROFILES covers ProfileName exactly — no orphan, no ghost.

        Replaces a hardcoded count (was 5, now 6 with ``field``): the
        count was the thing being asserted, and a count tells you nothing
        about WHICH profile went missing. ``field`` was added 2026-09-16
        for the ecomm kit.
        """
        assert set(PROFILES) == set(ProfileName), (
            f"missing: {set(ProfileName) - set(PROFILES)}, "
            f"extra: {set(PROFILES) - set(ProfileName)}")

    def test_all_profile_names_in_enum(self):
        """All ProfileName enum values should have a corresponding profile."""
        for name in ProfileName:
            assert name in PROFILES, f"Missing profile for {name}"

    def test_radio_maps_requires_meshtasticd(self):
        """Radio+Maps profile requires meshtasticd service."""
        profile = PROFILES[ProfileName.RADIO_MAPS]
        assert "meshtasticd" in profile.required_services

    def test_monitor_requires_no_services(self):
        """Monitor profile should require no services (runs standalone)."""
        profile = PROFILES[ProfileName.MONITOR]
        assert len(profile.required_services) == 0

    def test_gateway_requires_meshtasticd_and_rnsd(self):
        """Gateway profile requires both meshtasticd and rnsd."""
        profile = PROFILES[ProfileName.GATEWAY]
        assert "meshtasticd" in profile.required_services
        assert "rnsd" in profile.required_services

    def test_full_requires_all_three_services(self):
        """Full profile requires meshtasticd, rnsd, and mosquitto."""
        profile = PROFILES[ProfileName.FULL]
        assert "meshtasticd" in profile.required_services
        assert "rnsd" in profile.required_services
        assert "mosquitto" in profile.required_services

    def test_all_profiles_have_display_name(self):
        """Every profile must have a non-empty display name."""
        for profile in PROFILES.values():
            assert profile.display_name, f"{profile.name} missing display_name"

    def test_all_profiles_have_description(self):
        """Every profile must have a non-empty description."""
        for profile in PROFILES.values():
            assert profile.description, f"{profile.name} missing description"

    def test_all_profiles_require_core_packages(self):
        """Every profile requires the packages the TUI cannot start without.

        ``requests`` left this set on 2026-09-16, measured rather than
        assumed: the whole of ``src/`` imports it exactly twice — in
        ``installer/version.py`` (not the TUI) and lazily inside one
        mfmaps health check that already degrades in-app. Everything else
        goes through ``safe_import``. It was declared core for every
        profile anyway, which would have forced an offline field kit to
        carry an HTTP library to pass its own dependency check.
        """
        core = {"rich", "yaml"}
        for profile in PROFILES.values():
            required = set(profile.required_packages)
            assert core.issubset(required), (
                f"{profile.name} missing core packages: {core - required}"
            )

    def test_field_profile_is_offline_capable(self):
        """The ecomm kit's profile must not require an online-only dep."""
        field = PROFILES[ProfileName.FIELD]
        assert "requests" not in field.required_packages, (
            "the field profile is for a kit with no uplink — an HTTP "
            "library is an optional convenience there, not a requirement")
        assert "RNS" in field.required_packages
        assert "LXMF" in field.required_packages
        assert field.feature_flags["mqtt"] is False, (
            "a field kit has no broker to reach")

    def test_feature_flags_are_booleans(self):
        """All feature flag values must be booleans."""
        for profile in PROFILES.values():
            for key, val in profile.feature_flags.items():
                assert isinstance(val, bool), (
                    f"{profile.name}.feature_flags[{key}] = {val!r} is not bool"
                )

    #: The full declared flag matrix. An expectation TABLE rather than a
    #: per-flag rule, because a new profile must state its intent for
    #: every flag instead of inheriting someone else's default — these
    #: values now decide what an operator can SEE, so a silent default is
    #: a silently missing menu row.
    EXPECTED_FLAGS = {
        ProfileName.RADIO_MAPS: dict(
            meshtastic=True, meshcore=False, rns=False, gateway=False,
            mqtt=False, maps=True, tactical=False, fleet_management=False),
        ProfileName.MONITOR: dict(
            meshtastic=False, meshcore=False, rns=False, gateway=False,
            mqtt=True, maps=False, tactical=False, fleet_management=False),
        ProfileName.MESHCORE: dict(
            meshtastic=True, meshcore=True, rns=False, gateway=False,
            mqtt=False, maps=False, tactical=False, fleet_management=False),
        ProfileName.GATEWAY: dict(
            meshtastic=True, meshcore=False, rns=True, gateway=True,
            mqtt=True, maps=True, tactical=True, fleet_management=True),
        # The ecomm kit: both radios carry traffic and the box is its own
        # NOC, so the bridge is the point of it. No broker, no fleet.
        ProfileName.FIELD: dict(
            meshtastic=True, meshcore=False, rns=True, gateway=True,
            mqtt=False, maps=True, tactical=True, fleet_management=False),
        ProfileName.FULL: dict(
            meshtastic=True, meshcore=True, rns=True, gateway=True,
            mqtt=True, maps=True, tactical=True, fleet_management=True),
    }

    def test_declared_flag_matrix(self):
        """Every profile's flags are pinned, all eight, by name."""
        assert set(self.EXPECTED_FLAGS) == set(PROFILES), (
            "a profile was added or removed without stating its flags — "
            "update EXPECTED_FLAGS deliberately, in the same commit")
        for name, expected in self.EXPECTED_FLAGS.items():
            assert PROFILES[name].feature_flags == expected, (
                f"{name.value} flags changed.\n"
                f"  expected: {expected}\n"
                f"  actual:   {PROFILES[name].feature_flags}")

    def test_every_profile_declares_every_flag(self):
        """No profile may leave a flag to feature_enabled()'s True default.

        ``TUIContext.feature_enabled`` returns True for an unknown flag —
        correct for "no profile at all", wrong for "this profile forgot
        one", where it silently means "on". A flag that is missing rather
        than False is how ``fleet_management`` stayed permanently visible.
        """
        for name, profile in PROFILES.items():
            missing = set(FEATURE_FLAGS) - set(profile.feature_flags)
            extra = set(profile.feature_flags) - set(FEATURE_FLAGS)
            assert not missing, f"{name.value} does not declare {sorted(missing)}"
            assert not extra, (
                f"{name.value} declares {sorted(extra)}, which is not in "
                f"FEATURE_FLAGS — add it there or it gates nothing")


class TestProfileDetection:
    """Test auto-detection of profiles from system state."""

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.deployment_profiles._check_package')
    def test_detects_full_when_all_services(self, mock_pkg, mock_svc):
        """Full profile detected when all 3 services running."""
        mock_svc.side_effect = lambda name: name in ('meshtasticd', 'rnsd', 'mosquitto')
        mock_pkg.return_value = True
        profile = detect_profile()
        assert profile.name == ProfileName.FULL

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.deployment_profiles._check_package')
    def test_detects_gateway_when_meshtasticd_and_rnsd(self, mock_pkg, mock_svc):
        """Gateway profile detected when meshtasticd + rnsd running."""
        mock_svc.side_effect = lambda name: name in ('meshtasticd', 'rnsd')
        mock_pkg.return_value = True
        profile = detect_profile()
        assert profile.name == ProfileName.GATEWAY

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.deployment_profiles._check_package')
    def test_detects_monitor_when_no_services_mqtt_available(self, mock_pkg, mock_svc):
        """Monitor profile when no services but paho available."""
        mock_svc.return_value = False
        mock_pkg.side_effect = lambda name: name == 'paho'
        profile = detect_profile()
        assert profile.name == ProfileName.MONITOR

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.deployment_profiles._check_package')
    def test_defaults_to_radio_maps(self, mock_pkg, mock_svc):
        """Defaults to radio_maps when nothing special detected."""
        mock_svc.return_value = False
        mock_pkg.return_value = False
        profile = detect_profile()
        assert profile.name == ProfileName.RADIO_MAPS

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.deployment_profiles._check_package')
    def test_detects_meshcore_when_package_available(self, mock_pkg, mock_svc):
        """MeshCore profile when meshcore package is installed."""
        mock_svc.return_value = False
        mock_pkg.side_effect = lambda name: name == 'meshcore'
        profile = detect_profile()
        assert profile.name == ProfileName.MESHCORE


class TestProfilePersistence:
    """Test saving and loading profiles."""

    def test_save_and_load_roundtrip(self, tmp_path):
        """Profile should survive save/load cycle."""
        profile_path = tmp_path / "deployment.json"
        profile = PROFILES[ProfileName.GATEWAY]

        with patch('utils.deployment_profiles._PROFILE_PATH', profile_path):
            assert save_profile(profile) is True
            loaded = load_profile()
            assert loaded is not None
            assert loaded.name == ProfileName.GATEWAY

    def test_save_profile_preserves_role_and_overrides(self, tmp_path):
        """deployment.json is shared with provision_role.py (`role`,
        `service_overrides`). save_profile must MERGE, not clobber — the old
        implementation wrote {"profile": ...} over the whole file, wiping the
        fleet role. Mirrors provision_role.write_role()."""
        profile_path = tmp_path / "deployment.json"
        profile_path.write_text(json.dumps({
            "role": "full-gateway",
            "service_overrides": {
                "meshforge-map": {"state": "disabled", "reason": "RF-sparse"},
            },
        }))
        with patch('utils.deployment_profiles._PROFILE_PATH', profile_path):
            assert save_profile(PROFILES[ProfileName.GATEWAY]) is True
        data = json.loads(profile_path.read_text())
        assert data["profile"] == ProfileName.GATEWAY.value     # profile written
        assert data["role"] == "full-gateway"                   # role PRESERVED
        assert data["service_overrides"]["meshforge-map"]["reason"] == "RF-sparse"

    def test_load_returns_none_when_no_file(self, tmp_path):
        """load_profile returns None when no saved profile."""
        profile_path = tmp_path / "nonexistent.json"
        with patch('utils.deployment_profiles._PROFILE_PATH', profile_path):
            assert load_profile() is None

    def test_load_returns_none_on_corrupt_json(self, tmp_path):
        """load_profile returns None when file is corrupt."""
        profile_path = tmp_path / "deployment.json"
        profile_path.write_text("{invalid json")
        with patch('utils.deployment_profiles._PROFILE_PATH', profile_path):
            assert load_profile() is None

    def test_load_or_detect_uses_saved(self, tmp_path):
        """load_or_detect_profile prefers saved profile."""
        profile_path = tmp_path / "deployment.json"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps({"profile": "monitor"}))

        with patch('utils.deployment_profiles._PROFILE_PATH', profile_path):
            profile = load_or_detect_profile()
            assert profile.name == ProfileName.MONITOR

    @patch('utils.deployment_profiles.detect_profile')
    def test_load_or_detect_falls_back_to_detect(self, mock_detect, tmp_path):
        """load_or_detect_profile falls back to detection when no saved profile."""
        profile_path = tmp_path / "nonexistent.json"
        mock_detect.return_value = PROFILES[ProfileName.RADIO_MAPS]

        with patch('utils.deployment_profiles._PROFILE_PATH', profile_path):
            profile = load_or_detect_profile()
            assert profile.name == ProfileName.RADIO_MAPS
            mock_detect.assert_called_once()


class TestProfileValidation:
    """Test profile validation against system state."""

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.deployment_profiles._check_package')
    def test_ready_when_all_present(self, mock_pkg, mock_svc):
        """Profile is ready when all required services and packages are present."""
        mock_svc.return_value = True
        mock_pkg.return_value = True
        profile = PROFILES[ProfileName.GATEWAY]
        health = validate_profile(profile)
        assert health.ready is True
        assert len(health.missing_services) == 0
        assert len(health.missing_packages) == 0

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.deployment_profiles._check_package')
    def test_not_ready_when_service_missing(self, mock_pkg, mock_svc):
        """Profile is not ready when a required service is missing."""
        mock_svc.side_effect = lambda name: name != 'rnsd'
        mock_pkg.return_value = True
        profile = PROFILES[ProfileName.GATEWAY]
        health = validate_profile(profile)
        assert health.ready is False
        assert 'rnsd' in health.missing_services

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.deployment_profiles._check_package')
    def test_not_ready_when_package_missing(self, mock_pkg, mock_svc):
        """Profile is not ready when a required package is missing."""
        mock_svc.return_value = True
        mock_pkg.side_effect = lambda name: name != 'RNS'
        profile = PROFILES[ProfileName.GATEWAY]
        health = validate_profile(profile)
        assert health.ready is False
        assert 'RNS' in health.missing_packages

    @patch('utils.deployment_profiles._check_service_available')
    @patch('utils.deployment_profiles._check_package')
    def test_summary_when_not_ready(self, mock_pkg, mock_svc):
        """Health summary describes what's missing."""
        mock_svc.return_value = False
        mock_pkg.return_value = False
        profile = PROFILES[ProfileName.RADIO_MAPS]
        health = validate_profile(profile)
        assert "Missing" in health.summary


class TestProfileLookup:
    """Test profile lookup utilities."""

    def test_get_profile_by_valid_name(self):
        """get_profile_by_name returns profile for valid names."""
        profile = get_profile_by_name("gateway")
        assert profile is not None
        assert profile.name == ProfileName.GATEWAY

    def test_get_profile_by_invalid_name(self):
        """get_profile_by_name returns None for invalid names."""
        assert get_profile_by_name("nonexistent") is None
        assert get_profile_by_name("") is None

    def test_list_profiles_covers_every_profile_name(self):
        """list_profiles is hand-ordered — it must not drop a profile.

        It is a CLOSED consumer of an open enum (hfm #7): a profile added
        to ``ProfileName`` but not listed here would exist, validate, and
        be completely invisible in the Settings menu.
        """
        listed = {p.name for p in list_profiles()}
        assert listed == set(ProfileName), (
            f"list_profiles() omits {sorted(n.value for n in set(ProfileName) - listed)}")

    def test_list_profiles_order(self):
        """list_profiles returns profiles in display order."""
        profiles = list_profiles()
        assert profiles[0].name == ProfileName.RADIO_MAPS
        assert profiles[-1].name == ProfileName.FULL

    def test_profile_to_dict(self):
        """ProfileDefinition.to_dict produces serializable dict."""
        profile = PROFILES[ProfileName.GATEWAY]
        d = profile.to_dict()
        assert d['name'] == 'gateway'
        assert 'display_name' in d
        # Should be JSON-serializable
        json.dumps(d)


class TestSaveProfileClobberGuard:
    """QA 2026-07-05: same clobber class as write_role — an unreadable
    EXISTING deployment.json must never be silently replaced."""

    def test_corrupt_existing_refused_and_intact(self, monkeypatch, tmp_path):
        import utils.deployment_profiles as dp
        p = tmp_path / "deployment.json"
        p.write_text('{"role": "collector", "service_overr')  # torn
        monkeypatch.setattr(dp, "_PROFILE_PATH", p)
        prof = dp.PROFILES[dp.ProfileName.MONITOR]
        assert dp.save_profile(prof) is False       # refused, loudly logged
        assert "collector" in p.read_text()          # file untouched

    def test_healthy_existing_merges(self, monkeypatch, tmp_path):
        import json as _json
        import utils.deployment_profiles as dp
        p = tmp_path / "deployment.json"
        p.write_text('{"role": "collector"}')
        monkeypatch.setattr(dp, "_PROFILE_PATH", p)
        prof = dp.PROFILES[dp.ProfileName.MONITOR]
        assert dp.save_profile(prof) is True
        data = _json.loads(p.read_text())
        assert data["role"] == "collector"           # preserved
        assert data["profile"]                        # merged in


class TestSaveProfileChownBack:
    """QA re-review 2026-07-05: save_profile writing as root into the
    operator's home must chown-back (parity with write_role), or the file
    goes root-owned and locks out every later user-mode reader — and then
    the new clobber-guard fires (unreadable -> refuse)."""

    def test_calls_chown_to_operator(self, monkeypatch, tmp_path):
        import utils.deployment_profiles as dp
        p = tmp_path / "deployment.json"
        monkeypatch.setattr(dp, "_PROFILE_PATH", p)
        called = []
        import utils.paths as up
        monkeypatch.setattr(up, "chown_to_operator",
                            lambda *paths: called.append(paths))
        prof = dp.PROFILES[dp.ProfileName.MONITOR]
        assert dp.save_profile(prof) is True
        assert called and p in called[0]  # the file was handed back
