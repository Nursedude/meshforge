"""
Tests for RadioMode awareness in ServiceOrchestrator.

Covers:
- MESHCORE mode marks meshtasticd as optional
- MESHTASTIC mode keeps meshtasticd required
- DUAL mode keeps both required
- get_config_info includes radio_mode and bridge_mode
- get_status returns NOT_NEEDED for meshtasticd in MESHCORE mode

Run: python3 -m pytest tests/test_orchestrator_radio_mode.py -v
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from core.radio_mode import RadioMode


@pytest.fixture
def _patch_orchestrator_deps():
    """Patch orchestrator dependencies for clean instantiation."""
    with patch('core.orchestrator.Path.exists', return_value=False), \
         patch('core.orchestrator._detect_radio_hardware', return_value={
             'has_spi': False, 'has_usb': True,
             'spi_devices': [], 'usb_devices': ['/dev/ttyUSB0'],
             'usb_device': '/dev/ttyUSB0', 'hardware_type': 'usb',
         }):
        yield


def _make_orchestrator(radio_mode: RadioMode):
    """Create an orchestrator with a specific RadioMode."""
    with patch('core.orchestrator.get_radio_mode', return_value=radio_mode):
        from core.orchestrator import ServiceOrchestrator
        return ServiceOrchestrator(config_path=Path('/nonexistent'))


class TestRadioModeConfiguration:
    """Test _configure_for_radio_mode adjusts service requirements."""

    def test_meshcore_mode_meshtasticd_optional(self, _patch_orchestrator_deps):
        """MESHCORE mode should mark meshtasticd as not required."""
        orch = _make_orchestrator(RadioMode.MESHCORE)
        assert orch.SERVICES['meshtasticd'].required is False

    def test_meshcore_mode_keeps_startup_order(self, _patch_orchestrator_deps):
        """MESHCORE mode should NOT remove meshtasticd from startup order."""
        orch = _make_orchestrator(RadioMode.MESHCORE)
        assert 'meshtasticd' in orch.STARTUP_ORDER

    def test_meshtastic_mode_default(self, _patch_orchestrator_deps):
        """MESHTASTIC mode should keep meshtasticd required (default)."""
        orch = _make_orchestrator(RadioMode.MESHTASTIC)
        assert orch.SERVICES['meshtasticd'].required is True

    def test_dual_mode_both_required(self, _patch_orchestrator_deps):
        """DUAL mode should keep both meshtasticd and rnsd required."""
        orch = _make_orchestrator(RadioMode.DUAL)
        assert orch.SERVICES['meshtasticd'].required is True
        assert orch.SERVICES['rnsd'].required is True

    def test_rnsd_always_required(self, _patch_orchestrator_deps):
        """rnsd should remain required regardless of RadioMode."""
        for mode in RadioMode:
            orch = _make_orchestrator(mode)
            assert orch.SERVICES['rnsd'].required is True


class TestRadioModeProperty:
    """Test the radio_mode property."""

    def test_radio_mode_returns_cached_value(self, _patch_orchestrator_deps):
        """radio_mode property should return the mode set at init."""
        for mode in RadioMode:
            orch = _make_orchestrator(mode)
            assert orch.radio_mode == mode


class TestGetConfigInfoRadioMode:
    """Test get_config_info includes RadioMode fields."""

    def test_config_info_has_radio_mode(self, _patch_orchestrator_deps):
        """get_config_info should include radio_mode key."""
        orch = _make_orchestrator(RadioMode.MESHCORE)
        info = orch.get_config_info()
        assert info['radio_mode'] == 'meshcore'

    def test_config_info_has_bridge_mode(self, _patch_orchestrator_deps):
        """get_config_info should include bridge_mode key."""
        orch = _make_orchestrator(RadioMode.DUAL)
        info = orch.get_config_info()
        assert info['bridge_mode'] == 'tri_bridge'

    def test_config_info_meshtastic_bridge_mode(self, _patch_orchestrator_deps):
        """MESHTASTIC mode should map to mqtt_bridge."""
        orch = _make_orchestrator(RadioMode.MESHTASTIC)
        info = orch.get_config_info()
        assert info['bridge_mode'] == 'mqtt_bridge'


class TestGetStatusRadioMode:
    """Test get_status behavior with RadioMode."""

    def test_meshcore_meshtasticd_not_installed_shows_not_needed(
        self, _patch_orchestrator_deps
    ):
        """In MESHCORE mode, uninstalled meshtasticd should show NOT_NEEDED."""
        orch = _make_orchestrator(RadioMode.MESHCORE)
        with patch.object(orch, 'is_installed', return_value=False):
            from core.orchestrator import ServiceState
            status = orch.get_status('meshtasticd')
            assert status.state == ServiceState.NOT_NEEDED
            assert 'MESHCORE' in status.message

    def test_meshtastic_meshtasticd_not_installed_shows_not_installed(
        self, _patch_orchestrator_deps
    ):
        """In MESHTASTIC mode, uninstalled meshtasticd should show NOT_INSTALLED."""
        orch = _make_orchestrator(RadioMode.MESHTASTIC)
        with patch.object(orch, 'is_installed', return_value=False):
            from core.orchestrator import ServiceState
            status = orch.get_status('meshtasticd')
            assert status.state == ServiceState.NOT_INSTALLED
