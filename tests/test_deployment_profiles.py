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
)


class TestProfileDefinitions:
    """Verify profile definitions are complete and consistent."""

    def test_all_profiles_have_unique_names(self):
        """Each profile must have a unique ProfileName."""
        names = [p.name for p in PROFILES.values()]
        assert len(names) == len(set(names))

    def test_six_profiles_defined(self):
        """Exactly 6 profiles should be defined."""
        assert len(PROFILES) == 6

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
        """Every profile should require at least the core packages."""
        core = {"rich", "yaml", "requests"}
        for profile in PROFILES.values():
            required = set(profile.required_packages)
            assert core.issubset(required), (
                f"{profile.name} missing core packages: {core - required}"
            )

    def test_feature_flags_are_booleans(self):
        """All feature flag values must be booleans."""
        for profile in PROFILES.values():
            for key, val in profile.feature_flags.items():
                assert isinstance(val, bool), (
                    f"{profile.name}.feature_flags[{key}] = {val!r} is not bool"
                )

    def test_gateway_flag_only_in_gateway_meshchat_and_full(self):
        """Gateway feature should only be enabled in gateway, meshchat, and full profiles."""
        for name, profile in PROFILES.items():
            if name in (ProfileName.GATEWAY, ProfileName.MESHCHAT, ProfileName.FULL):
                assert profile.feature_flags.get("gateway") is True
            else:
                assert profile.feature_flags.get("gateway") is False


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

    def test_list_profiles_returns_all_six(self):
        """list_profiles returns all 6 profiles."""
        profiles = list_profiles()
        assert len(profiles) == 6

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
