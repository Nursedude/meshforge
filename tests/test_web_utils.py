"""
Tests for web utility functions.

Run: python3 -m pytest tests/test_web_utils.py -v
"""

import pytest
from unittest.mock import patch, mock_open, MagicMock

from src.web.utils import (
    TCP_STATES,
    VALID_ACTIONS,
    ALLOWED_SERVICES,
    hex_to_ip,
    parse_proc_net_line,
    parse_proc_net,
    validate_config_name,
)


class TestTCPStates:
    """Tests for TCP state constants."""

    def test_established(self):
        """Test ESTABLISHED state mapping."""
        assert TCP_STATES.get('01') == 'ESTABLISHED'

    def test_listen(self):
        """Test LISTEN state mapping."""
        assert TCP_STATES.get('0A') == 'LISTEN'

    def test_time_wait(self):
        """Test TIME_WAIT state mapping."""
        assert TCP_STATES.get('06') == 'TIME_WAIT'

    def test_all_states_exist(self):
        """Test all expected states are mapped."""
        expected_states = [
            'ESTABLISHED', 'SYN_SENT', 'SYN_RECV', 'FIN_WAIT1',
            'FIN_WAIT2', 'TIME_WAIT', 'CLOSE', 'CLOSE_WAIT',
            'LAST_ACK', 'LISTEN', 'CLOSING'
        ]
        for state in expected_states:
            assert state in TCP_STATES.values()


class TestServiceConstants:
    """Tests for service-related constants."""

    def test_valid_actions(self):
        """Test valid service actions."""
        assert 'start' in VALID_ACTIONS
        assert 'stop' in VALID_ACTIONS
        assert 'restart' in VALID_ACTIONS
        assert 'status' in VALID_ACTIONS
        assert len(VALID_ACTIONS) == 4

    def test_allowed_services(self):
        """Test allowed services list."""
        assert 'meshtasticd' in ALLOWED_SERVICES
        assert 'rnsd' in ALLOWED_SERVICES


class TestHexToIp:
    """Tests for hex_to_ip function."""

    def test_localhost(self):
        """Test converting localhost address."""
        # 127.0.0.1 in little-endian hex
        assert hex_to_ip('0100007F') == '127.0.0.1'

    def test_any_address(self):
        """Test converting 0.0.0.0."""
        assert hex_to_ip('00000000') == '0.0.0.0'

    def test_192_168_1_1(self):
        """Test converting 192.168.1.1."""
        # 192.168.1.1 in little-endian is 0x0101A8C0
        assert hex_to_ip('0101A8C0') == '192.168.1.1'

    def test_invalid_hex(self):
        """Test invalid hex returns default."""
        assert hex_to_ip('invalid') == '0.0.0.0'

    def test_empty_string(self):
        """Test empty string returns default."""
        assert hex_to_ip('') == '0.0.0.0'


class TestParseProcNetLine:
    """Tests for parse_proc_net_line function."""

    def test_valid_tcp_line(self):
        """Test parsing a valid TCP connection line."""
        line = "   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1"

        result = parse_proc_net_line(line)

        assert result is not None
        assert result['local_ip'] == '127.0.0.1'
        assert result['local_port'] == 8080
        assert result['state'] == 'LISTEN'

    def test_header_line_ignored(self):
        """Test header line is ignored."""
        header = "  sl  local_address rem_address   st tx_queue rx_queue"

        result = parse_proc_net_line(header)

        assert result is None

    def test_sl_line_ignored(self):
        """Test sl line is ignored."""
        line = "sl  local_address rem_address"

        result = parse_proc_net_line(line)

        assert result is None

    def test_empty_line(self):
        """Test empty line returns None."""
        assert parse_proc_net_line('') is None
        assert parse_proc_net_line('   ') is None

    def test_short_line_ignored(self):
        """Test line with insufficient parts is ignored."""
        line = "0: 0100007F:1F90"  # Too few parts

        result = parse_proc_net_line(line)

        assert result is None


class TestParseProcNet:
    """Tests for parse_proc_net function."""

    def test_parse_tcp_empty_file(self):
        """Test parsing empty /proc/net/tcp."""
        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', mock_open(read_data="sl local_address\n")):
                # Force fallback by patching the import flag
                with patch('src.web.utils._HAS_NETWORK_DIAG', False):
                    result = parse_proc_net('tcp')
                    assert isinstance(result, list)

    def test_returns_list(self):
        """Test function returns a list."""
        result = parse_proc_net('tcp')
        assert isinstance(result, list)

    def test_nonexistent_file(self):
        """Test handling of nonexistent file."""
        with patch('src.web.utils._HAS_NETWORK_DIAG', False):
            with patch('os.path.exists', return_value=False):
                result = parse_proc_net('tcp')
                assert result == []


class TestValidateConfigName:
    """Tests for validate_config_name function."""

    def test_valid_yaml(self):
        """Test valid .yaml filename."""
        assert validate_config_name('config.yaml') is True
        assert validate_config_name('my-config.yaml') is True
        assert validate_config_name('my_config.yaml') is True

    def test_valid_yml(self):
        """Test valid .yml filename."""
        assert validate_config_name('config.yml') is True
        assert validate_config_name('test-123.yml') is True

    def test_alphanumeric_names(self):
        """Test alphanumeric config names."""
        assert validate_config_name('Config123.yaml') is True
        assert validate_config_name('TEST.yml') is True

    def test_invalid_extension(self):
        """Test invalid extensions are rejected."""
        assert validate_config_name('config.json') is False
        assert validate_config_name('config.txt') is False
        assert validate_config_name('config') is False

    def test_path_traversal_blocked(self):
        """Test path traversal is blocked."""
        assert validate_config_name('../config.yaml') is False
        assert validate_config_name('/etc/passwd') is False
        assert validate_config_name('../../secret.yaml') is False
        assert validate_config_name('config/../other.yaml') is False

    def test_special_chars_blocked(self):
        """Test special characters are blocked."""
        assert validate_config_name('config$.yaml') is False
        assert validate_config_name('config;.yaml') is False
        assert validate_config_name('config .yaml') is False

    def test_empty_name(self):
        """Test empty name is rejected."""
        assert validate_config_name('') is False
        assert validate_config_name(None) is False

    def test_hidden_files_blocked(self):
        """Test hidden files are blocked."""
        assert validate_config_name('.config.yaml') is False
        assert validate_config_name('.hidden.yml') is False


class TestIntegration:
    """Integration tests for web utilities."""

    def test_tcp_state_used_in_parsing(self):
        """Test TCP states are correctly applied during parsing."""
        line = "   0: 0100007F:0050 0100007F:1388 01 00000000:00000000 00:00000000 00000000     0        0 12345 1"

        with patch('src.web.utils._HAS_NETWORK_DIAG', False):
            result = parse_proc_net_line(line)

        assert result['state'] == 'ESTABLISHED'  # 01 = ESTABLISHED
