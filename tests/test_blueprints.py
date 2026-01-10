"""
Tests for Flask Blueprint modules and web utilities

Split into:
- Utility tests: Pure Python, no Flask required
- Blueprint tests: Flask required, skip if not available
"""

import pytest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# Check if Flask is available
try:
    import flask
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False


# =============================================================================
# UTILITY TESTS - No Flask required
# =============================================================================

class TestWebUtilsImport:
    """Test that web.utils can be imported without Flask"""

    def test_import_web_utils(self):
        """Test importing web.utils module"""
        from web.utils import (
            TCP_STATES,
            VALID_ACTIONS,
            ALLOWED_SERVICES,
            hex_to_ip,
            parse_proc_net_line,
            validate_config_name,
        )
        assert TCP_STATES is not None
        assert VALID_ACTIONS is not None
        assert ALLOWED_SERVICES is not None


class TestTCPStates:
    """Test TCP state mapping"""

    def test_tcp_state_established(self):
        """Test ESTABLISHED state"""
        from web.utils import TCP_STATES
        assert TCP_STATES.get('01') == 'ESTABLISHED'

    def test_tcp_state_listen(self):
        """Test LISTEN state"""
        from web.utils import TCP_STATES
        assert TCP_STATES.get('0A') == 'LISTEN'

    def test_tcp_state_time_wait(self):
        """Test TIME_WAIT state"""
        from web.utils import TCP_STATES
        assert TCP_STATES.get('06') == 'TIME_WAIT'

    def test_tcp_state_all_states(self):
        """Test all standard TCP states exist"""
        from web.utils import TCP_STATES
        expected_states = [
            'ESTABLISHED', 'SYN_SENT', 'SYN_RECV', 'FIN_WAIT1', 'FIN_WAIT2',
            'TIME_WAIT', 'CLOSE', 'CLOSE_WAIT', 'LAST_ACK', 'LISTEN', 'CLOSING'
        ]
        for state in expected_states:
            assert state in TCP_STATES.values()


class TestHexToIP:
    """Test hex to IP conversion"""

    def test_localhost(self):
        """Test localhost conversion"""
        from web.utils import hex_to_ip
        # 0100007F = 127.0.0.1 (little endian)
        assert hex_to_ip('0100007F') == '127.0.0.1'

    def test_any_address(self):
        """Test 0.0.0.0 conversion"""
        from web.utils import hex_to_ip
        assert hex_to_ip('00000000') == '0.0.0.0'

    def test_broadcast(self):
        """Test broadcast address conversion"""
        from web.utils import hex_to_ip
        # FFFFFFFF = 255.255.255.255
        assert hex_to_ip('FFFFFFFF') == '255.255.255.255'

    def test_invalid_hex(self):
        """Test invalid hex returns 0.0.0.0"""
        from web.utils import hex_to_ip
        assert hex_to_ip('invalid') == '0.0.0.0'
        assert hex_to_ip('') == '0.0.0.0'


class TestParseProcNetLine:
    """Test /proc/net line parsing"""

    def test_header_line(self):
        """Test header line returns None"""
        from web.utils import parse_proc_net_line
        result = parse_proc_net_line("  sl  local_address rem_address")
        assert result is None

    def test_empty_line(self):
        """Test empty line returns None"""
        from web.utils import parse_proc_net_line
        assert parse_proc_net_line("") is None
        assert parse_proc_net_line("   ") is None

    def test_valid_listening_socket(self):
        """Test parsing a valid LISTEN socket"""
        from web.utils import parse_proc_net_line
        line = "   0: 00000000:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0"
        result = parse_proc_net_line(line)
        assert result is not None
        assert result['local_port'] == 8080  # 0x1F90 = 8080
        assert result['state'] == 'LISTEN'  # 0A = LISTEN
        assert result['local_ip'] == '0.0.0.0'

    def test_valid_established_socket(self):
        """Test parsing an ESTABLISHED socket"""
        from web.utils import parse_proc_net_line
        line = "   1: 0100007F:1F91 0100007F:1F90 01 00000000:00000000 00:00000000 00000000     0        0 12346 1 0000000000000000 100 0 0 10 0"
        result = parse_proc_net_line(line)
        assert result is not None
        assert result['local_port'] == 8081  # 0x1F91
        assert result['remote_port'] == 8080  # 0x1F90
        assert result['state'] == 'ESTABLISHED'
        assert result['local_ip'] == '127.0.0.1'

    def test_short_line(self):
        """Test short/malformed line returns None"""
        from web.utils import parse_proc_net_line
        assert parse_proc_net_line("0: 00000000:1F90") is None


class TestValidateConfigName:
    """Test config name validation"""

    def test_valid_yaml(self):
        """Test valid .yaml names"""
        from web.utils import validate_config_name
        assert validate_config_name('test.yaml') is True
        assert validate_config_name('radio_config.yaml') is True
        assert validate_config_name('Config123.yaml') is True

    def test_valid_yml(self):
        """Test valid .yml names"""
        from web.utils import validate_config_name
        assert validate_config_name('test.yml') is True
        assert validate_config_name('my-config.yml') is True

    def test_path_traversal(self):
        """Test path traversal attacks rejected"""
        from web.utils import validate_config_name
        assert validate_config_name('../etc/passwd') is False
        assert validate_config_name('/etc/passwd') is False
        assert validate_config_name('..\\windows\\system32') is False

    def test_invalid_extension(self):
        """Test invalid extensions rejected"""
        from web.utils import validate_config_name
        assert validate_config_name('test.txt') is False
        assert validate_config_name('test.json') is False
        assert validate_config_name('test.yaml.bak') is False

    def test_empty_name(self):
        """Test empty name rejected"""
        from web.utils import validate_config_name
        assert validate_config_name('') is False
        assert validate_config_name(None) is False

    def test_no_extension(self):
        """Test name without extension rejected"""
        from web.utils import validate_config_name
        assert validate_config_name('test') is False


class TestServiceConstants:
    """Test service-related constants"""

    def test_valid_actions(self):
        """Test valid service actions"""
        from web.utils import VALID_ACTIONS
        assert 'start' in VALID_ACTIONS
        assert 'stop' in VALID_ACTIONS
        assert 'restart' in VALID_ACTIONS
        assert 'status' in VALID_ACTIONS

    def test_allowed_services(self):
        """Test allowed services list"""
        from web.utils import ALLOWED_SERVICES
        assert 'meshtasticd' in ALLOWED_SERVICES
        assert 'rnsd' in ALLOWED_SERVICES


# =============================================================================
# BLUEPRINT TESTS - Flask required
# =============================================================================

@pytest.mark.skipif(not FLASK_AVAILABLE, reason="Flask not installed")
class TestBlueprintImports:
    """Test that blueprints can be imported"""

    def test_import_blueprints_module(self):
        """Test importing the blueprints package"""
        from web.blueprints import (
            system_bp,
            config_bp,
            nodes_bp,
            network_bp,
            service_bp
        )
        assert system_bp is not None
        assert config_bp is not None
        assert nodes_bp is not None
        assert network_bp is not None
        assert service_bp is not None

    def test_register_blueprints_function(self):
        """Test the register_blueprints function exists"""
        from web.blueprints import register_blueprints
        assert callable(register_blueprints)


@pytest.mark.skipif(not FLASK_AVAILABLE, reason="Flask not installed")
class TestBlueprintNames:
    """Test blueprint names"""

    def test_system_blueprint_name(self):
        """Test system blueprint has correct name"""
        from web.blueprints.system import system_bp
        assert system_bp.name == 'system'

    def test_config_blueprint_name(self):
        """Test config blueprint has correct name"""
        from web.blueprints.config import config_bp
        assert config_bp.name == 'config'

    def test_nodes_blueprint_name(self):
        """Test nodes blueprint has correct name"""
        from web.blueprints.nodes import nodes_bp
        assert nodes_bp.name == 'nodes'

    def test_network_blueprint_name(self):
        """Test network blueprint has correct name"""
        from web.blueprints.network import network_bp
        assert network_bp.name == 'network'

    def test_service_blueprint_name(self):
        """Test service blueprint has correct name"""
        from web.blueprints.service import service_bp
        assert service_bp.name == 'service'


@pytest.mark.skipif(not FLASK_AVAILABLE, reason="Flask not installed")
class TestBlueprintRegistration:
    """Test blueprint registration with Flask app"""

    def test_register_with_flask_app(self):
        """Test registering blueprints with a Flask app"""
        from flask import Flask
        from web.blueprints import register_blueprints

        app = Flask(__name__)
        register_blueprints(app)

        # Check that blueprints are registered
        assert 'system' in app.blueprints
        assert 'config' in app.blueprints
        assert 'nodes' in app.blueprints
        assert 'network' in app.blueprints
        assert 'service' in app.blueprints

    def test_blueprint_url_prefixes(self):
        """Test blueprints have correct URL prefixes"""
        from flask import Flask
        from web.blueprints import register_blueprints

        app = Flask(__name__)
        register_blueprints(app)

        # Get all registered rules
        rules = [rule.rule for rule in app.url_rules]

        # Check API routes exist with /api prefix
        assert any('/api/status' in r for r in rules)
        assert any('/api/nodes' in r for r in rules)
        assert any('/api/service' in r for r in rules)
