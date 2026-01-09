"""
Tests for amateur radio callsign management.

Tests the CallsignManager, StationIDTimer, and utility functions
for callsign validation, lookup, and grid square conversion.
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import json
import sys
import os

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from amateur.callsign import (
    CallsignInfo,
    CallsignManager,
    StationIDTimer,
)


class TestCallsignInfo:
    """Tests for CallsignInfo dataclass"""

    def test_basic_creation(self):
        """Test basic CallsignInfo creation"""
        info = CallsignInfo(callsign='W1AW', name='ARRL HQ')
        assert info.callsign == 'W1AW'
        assert info.name == 'ARRL HQ'
        assert info.country == 'US'

    def test_is_valid(self):
        """Test validity check"""
        valid_info = CallsignInfo(callsign='W1AW', name='ARRL')
        assert valid_info.is_valid() is True

        invalid_info = CallsignInfo(callsign='W1AW', name='')
        assert invalid_info.is_valid() is False

    def test_is_expired(self):
        """Test expiration check"""
        # Not expired
        future_date = (datetime.now() + timedelta(days=365)).strftime('%Y-%m-%d')
        valid_info = CallsignInfo(callsign='W1AW', name='Test', expiration_date=future_date)
        assert valid_info.is_expired() is False

        # Expired
        past_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        expired_info = CallsignInfo(callsign='W1AW', name='Test', expiration_date=past_date)
        assert expired_info.is_expired() is True

        # No expiration date
        no_exp_info = CallsignInfo(callsign='W1AW', name='Test')
        assert no_exp_info.is_expired() is False

    def test_to_dict_from_dict(self):
        """Test serialization round-trip"""
        original = CallsignInfo(
            callsign='W1AW',
            name='ARRL HQ',
            city='Newington',
            state='CT',
            license_class='Amateur Extra',
        )
        data = original.to_dict()
        restored = CallsignInfo.from_dict(data)

        assert restored.callsign == original.callsign
        assert restored.name == original.name
        assert restored.city == original.city
        assert restored.license_class == original.license_class


class TestCallsignManager:
    """Tests for CallsignManager"""

    @pytest.fixture
    def temp_config_dir(self):
        """Create temporary config directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def manager(self, temp_config_dir):
        """Create CallsignManager with temp directory"""
        return CallsignManager(config_dir=temp_config_dir)

    def test_validate_us_callsign(self, manager):
        """Test US callsign validation"""
        # Valid US callsigns (US formats: 1x2, 1x3, 2x1, 2x2, 2x3)
        assert manager.validate_callsign('W1AW') is True       # 1x2 format
        assert manager.validate_callsign('K6LPT') is True      # 1x3 format
        assert manager.validate_callsign('N0CAL') is True      # 1x3 format
        assert manager.validate_callsign('AA1AAA') is True     # 2x3 format
        assert manager.validate_callsign('WH6GXZ') is True     # 2x3 format

        # Invalid callsigns
        assert manager.validate_callsign('') is False
        assert manager.validate_callsign('INVALID') is False
        assert manager.validate_callsign('123ABC') is False
        assert manager.validate_callsign('N0CALL') is False    # 1x4 not valid US format

    def test_validate_international_callsigns(self, manager):
        """Test international callsign patterns"""
        # Canada
        assert manager.validate_callsign('VE3ABC', country='Canada') is True

        # UK
        assert manager.validate_callsign('G3ABC', country='UK') is True

        # Germany
        assert manager.validate_callsign('DL1ABC', country='Germany') is True

    def test_get_call_district(self, manager):
        """Test US call district lookup"""
        # District 6 = California
        district = manager.get_call_district('K6LPT')
        assert 'California' in district

        # District 1 = New England
        district = manager.get_call_district('W1AW')
        assert 'Connecticut' in district

    def test_set_my_callsign(self, manager):
        """Test setting operator callsign"""
        assert manager.set_my_callsign('W1AW') is True
        assert manager.my_callsign == 'W1AW'
        assert manager.my_info is not None

        # Invalid callsign should fail
        assert manager.set_my_callsign('INVALID') is False

    def test_cache_persistence(self, temp_config_dir):
        """Test cache save and load"""
        # Create manager and add data
        manager1 = CallsignManager(config_dir=temp_config_dir)
        manager1.set_my_callsign('W1AW')
        manager1._cache['K6LPT'] = CallsignInfo(callsign='K6LPT', name='Test Op')
        manager1._save_cache()

        # Create new manager - should load from cache
        manager2 = CallsignManager(config_dir=temp_config_dir)
        assert manager2.my_callsign == 'W1AW'
        assert 'K6LPT' in manager2._cache

    def test_get_id_string(self, manager):
        """Test station ID string generation"""
        manager.set_my_callsign('W1AW')

        # Simple ID
        assert manager.get_id_string() == 'W1AW'

        # With tactical callsign
        assert manager.get_id_string(tactical='NET CONTROL') == 'NET CONTROL, W1AW'

    def test_coords_to_grid(self, manager):
        """Test coordinate to grid square conversion"""
        # ARRL HQ in Newington, CT
        grid = manager.coords_to_grid(41.714775, -72.727260)
        assert grid.startswith('FN31')

        # Los Angeles, CA
        grid = manager.coords_to_grid(34.052235, -118.243683)
        assert grid.startswith('DM04')

        # Invalid coordinates
        grid = manager.coords_to_grid(999, 999)
        assert grid == ''

    def test_grid_to_coords(self, manager):
        """Test grid square to coordinate conversion"""
        # 4-character grid
        lat, lon = manager.grid_to_coords('FN31')
        assert 40 < lat < 42
        assert -74 < lon < -72

        # 6-character grid
        lat, lon = manager.grid_to_coords('FN31pr')
        assert 40 < lat < 42
        assert -74 < lon < -72

        # Invalid grid
        lat, lon = manager.grid_to_coords('XX')
        assert lat == 0.0
        assert lon == 0.0

    def test_grid_roundtrip(self, manager):
        """Test coordinate -> grid -> coordinate roundtrip"""
        original_lat, original_lon = 41.714775, -72.727260

        # Convert to grid and back
        grid = manager.coords_to_grid(original_lat, original_lon)
        recovered_lat, recovered_lon = manager.grid_to_coords(grid)

        # Should be close (within grid square precision)
        assert abs(recovered_lat - original_lat) < 0.1
        assert abs(recovered_lon - original_lon) < 0.2

    def test_check_license_expiration_no_callsign(self, manager):
        """Test expiration check with no callsign set"""
        result = manager.check_license_expiration()
        assert result['status'] == 'unknown'
        assert result['callsign'] is None

    def test_clear_cache(self, manager):
        """Test cache clearing"""
        manager._cache['W1AW'] = CallsignInfo(callsign='W1AW', name='Test')
        assert len(manager._cache) > 0

        manager.clear_cache()
        assert len(manager._cache) == 0

    def test_get_cache_stats(self, manager):
        """Test cache statistics"""
        manager._cache['W1AW'] = CallsignInfo(callsign='W1AW', name='Test1')
        manager._cache['K6LPT'] = CallsignInfo(callsign='K6LPT', name='Test2')

        stats = manager.get_cache_stats()
        assert stats['total_cached'] == 2
        assert 'W1AW' in stats['callsigns']
        assert 'K6LPT' in stats['callsigns']


class TestStationIDTimer:
    """Tests for StationIDTimer"""

    @pytest.fixture
    def temp_config_dir(self):
        """Create temporary config directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock get_real_user_home to return temp dir
            with patch('amateur.callsign.get_real_user_home', return_value=Path(tmpdir)):
                yield Path(tmpdir)

    def test_basic_creation(self, temp_config_dir):
        """Test basic timer creation"""
        with patch('amateur.callsign.get_real_user_home', return_value=temp_config_dir):
            timer = StationIDTimer(callsign='W1AW')
            assert timer.callsign == 'W1AW'
            assert timer.interval_minutes == 10
            assert timer.is_running is False

    def test_id_due_on_start(self, temp_config_dir):
        """Test that ID is due immediately on fresh start"""
        with patch('amateur.callsign.get_real_user_home', return_value=temp_config_dir):
            timer = StationIDTimer(callsign='W1AW')
            assert timer.is_id_due() is True
            assert timer.seconds_until_id_due() == 0

    def test_record_id(self, temp_config_dir):
        """Test recording station ID"""
        with patch('amateur.callsign.get_real_user_home', return_value=temp_config_dir):
            timer = StationIDTimer(callsign='W1AW')

            # Record ID
            timer.record_id()

            # Should not be due immediately
            assert timer.is_id_due() is False
            assert timer.seconds_until_id_due() > 0
            assert timer.last_id_time is not None

    def test_time_until_id_due_format(self, temp_config_dir):
        """Test human-readable time format"""
        with patch('amateur.callsign.get_real_user_home', return_value=temp_config_dir):
            timer = StationIDTimer(callsign='W1AW')

            # Before any ID - should show "ID NOW"
            assert timer.time_until_id_due() == "ID NOW"

            # After ID - should show time remaining
            timer.record_id()
            time_str = timer.time_until_id_due()
            assert ':' in time_str  # Format is "M:SS"

    def test_get_id_string(self, temp_config_dir):
        """Test ID string generation"""
        with patch('amateur.callsign.get_real_user_home', return_value=temp_config_dir):
            timer = StationIDTimer(callsign='W1AW')

            # Simple ID
            assert timer.get_id_string() == 'W1AW'

            # With tactical
            assert timer.get_id_string(tactical='NET CTRL') == 'NET CTRL, W1AW'

    def test_state_persistence(self, temp_config_dir):
        """Test timer state persistence across restarts"""
        with patch('amateur.callsign.get_real_user_home', return_value=temp_config_dir):
            # Create timer and record ID
            timer1 = StationIDTimer(callsign='W1AW')
            timer1.record_id()
            last_id = timer1.last_id_time

            # Create new timer - should load state
            timer2 = StationIDTimer(callsign='W1AW')
            assert timer2.last_id_time is not None
            # Times should be close (within a second)
            delta = abs((timer2.last_id_time - last_id).total_seconds())
            assert delta < 1

    def test_different_callsign_no_load(self, temp_config_dir):
        """Test that different callsign doesn't load previous state"""
        with patch('amateur.callsign.get_real_user_home', return_value=temp_config_dir):
            # Create timer and record ID
            timer1 = StationIDTimer(callsign='W1AW')
            timer1.record_id()

            # Different callsign should not load W1AW's state
            timer2 = StationIDTimer(callsign='K6LPT')
            assert timer2.last_id_time is None

    def test_start_stop(self, temp_config_dir):
        """Test timer start/stop"""
        with patch('amateur.callsign.get_real_user_home', return_value=temp_config_dir):
            timer = StationIDTimer(callsign='W1AW')

            assert timer.is_running is False

            timer.start()
            assert timer.is_running is True

            timer.stop()
            assert timer.is_running is False


class TestFCCLookupMock:
    """Tests for FCC lookup with mocked responses"""

    @pytest.fixture
    def temp_config_dir(self):
        """Create temporary config directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def manager(self, temp_config_dir):
        """Create CallsignManager with temp directory"""
        return CallsignManager(config_dir=temp_config_dir)

    def test_fcc_lookup_success(self, manager):
        """Test FCC lookup with mocked success response"""
        mock_response = {
            'Licenses': {
                'License': {
                    'serviceCode': 'HA',
                    'licName': 'AMERICAN RADIO RELAY LEAGUE INC',
                    'addressLine1': '225 MAIN ST',
                    'addressCity': 'NEWINGTON',
                    'addressState': 'CT',
                    'addressZIP': '06111',
                    'operatorClass': 'E',
                    'grantDate': '2020-01-01T00:00:00',
                    'expiredDate': '2030-01-01T00:00:00',
                    'frn': '0000000001',
                }
            }
        }

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_context = MagicMock()
            mock_context.__enter__ = MagicMock(return_value=mock_context)
            mock_context.__exit__ = MagicMock(return_value=False)
            mock_context.read.return_value = json.dumps(mock_response).encode('utf-8')
            mock_urlopen.return_value = mock_context

            info = manager._lookup_fcc_uls('W1AW')

            assert info is not None
            assert info.callsign == 'W1AW'
            assert info.name == 'AMERICAN RADIO RELAY LEAGUE INC'
            assert info.city == 'NEWINGTON'
            assert info.state == 'CT'
            assert info.license_class == 'Amateur Extra'

    def test_fcc_lookup_not_found(self, manager):
        """Test FCC lookup with no results"""
        mock_response = {'Licenses': {'License': []}}

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_context = MagicMock()
            mock_context.__enter__ = MagicMock(return_value=mock_context)
            mock_context.__exit__ = MagicMock(return_value=False)
            mock_context.read.return_value = json.dumps(mock_response).encode('utf-8')
            mock_urlopen.return_value = mock_context

            info = manager._lookup_fcc_uls('NOTFOUND')
            assert info is None

    def test_fcc_lookup_network_error(self, manager):
        """Test FCC lookup handles network errors gracefully"""
        import urllib.error

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError('Network error')

            info = manager._lookup_fcc_uls('W1AW')
            assert info is None  # Should return None, not raise

    def test_lookup_multi_with_cache(self, manager):
        """Test multi-source lookup uses cache"""
        # Pre-populate cache
        cached_info = CallsignInfo(callsign='W1AW', name='Cached Data')
        manager._cache['W1AW'] = cached_info

        # lookup_callsign_multi should use cache
        result = manager.lookup_callsign_multi('W1AW', use_cache=True)
        assert result.name == 'Cached Data'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
