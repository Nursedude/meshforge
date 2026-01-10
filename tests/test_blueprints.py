"""
Tests for Flask Blueprint modules

Tests the web API blueprint structure and route registration.
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

# Skip all tests in this module if Flask is not available
pytestmark = pytest.mark.skipif(not FLASK_AVAILABLE, reason="Flask not installed")


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


class TestSystemBlueprint:
    """Tests for the system blueprint"""

    def test_blueprint_name(self):
        """Test blueprint has correct name"""
        from web.blueprints.system import system_bp
        assert system_bp.name == 'system'

    def test_has_status_route(self):
        """Test status route is registered"""
        from web.blueprints.system import system_bp
        rules = [rule.rule for rule in system_bp.url_map.iter_rules()]
        # Note: rules are registered without prefix at this point
        assert '/status' in rules or any('/status' in r for r in rules)


class TestConfigBlueprint:
    """Tests for the config blueprint"""

    def test_blueprint_name(self):
        """Test blueprint has correct name"""
        from web.blueprints.config import config_bp
        assert config_bp.name == 'config'

    def test_validate_config_name_valid(self):
        """Test config name validation with valid names"""
        from web.blueprints.config import validate_config_name
        assert validate_config_name('test.yaml') is True
        assert validate_config_name('my-config.yml') is True
        assert validate_config_name('radio_config.yaml') is True
        assert validate_config_name('Config123.yaml') is True

    def test_validate_config_name_invalid(self):
        """Test config name validation rejects invalid names"""
        from web.blueprints.config import validate_config_name
        assert validate_config_name('../etc/passwd') is False
        assert validate_config_name('/etc/passwd') is False
        assert validate_config_name('test.txt') is False
        assert validate_config_name('') is False
        assert validate_config_name('test') is False
        assert validate_config_name('test.yaml.bak') is False


class TestNodesBlueprint:
    """Tests for the nodes blueprint"""

    def test_blueprint_name(self):
        """Test blueprint has correct name"""
        from web.blueprints.nodes import nodes_bp
        assert nodes_bp.name == 'nodes'


class TestNetworkBlueprint:
    """Tests for the network blueprint"""

    def test_blueprint_name(self):
        """Test blueprint has correct name"""
        from web.blueprints.network import network_bp
        assert network_bp.name == 'network'

    def test_parse_proc_net_empty(self):
        """Test parsing empty /proc/net data"""
        from web.blueprints.network import parse_proc_net_line
        # Test with header line (should return None)
        result = parse_proc_net_line("  sl  local_address rem_address")
        assert result is None

    def test_parse_proc_net_valid(self):
        """Test parsing valid /proc/net line"""
        from web.blueprints.network import parse_proc_net_line
        # Example line from /proc/net/tcp
        line = "   0: 00000000:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0"
        result = parse_proc_net_line(line)
        assert result is not None
        assert result['local_port'] == 8080  # 0x1F90 = 8080
        assert result['state'] == 'LISTEN'  # 0A = LISTEN


class TestServiceBlueprint:
    """Tests for the service blueprint"""

    def test_blueprint_name(self):
        """Test blueprint has correct name"""
        from web.blueprints.service import service_bp
        assert service_bp.name == 'service'

    def test_valid_actions(self):
        """Test valid service actions constant"""
        from web.blueprints.service import VALID_ACTIONS
        assert 'start' in VALID_ACTIONS
        assert 'stop' in VALID_ACTIONS
        assert 'restart' in VALID_ACTIONS

    def test_allowed_services(self):
        """Test allowed services list"""
        from web.blueprints.service import ALLOWED_SERVICES
        assert 'meshtasticd' in ALLOWED_SERVICES
        assert 'rnsd' in ALLOWED_SERVICES


class TestBlueprintRegistration:
    """Test blueprint registration with Flask app"""

    def test_register_with_flask_app(self):
        """Test registering blueprints with a Flask app"""
        try:
            from flask import Flask
        except ImportError:
            pytest.skip("Flask not installed")

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
        try:
            from flask import Flask
        except ImportError:
            pytest.skip("Flask not installed")

        from web.blueprints import register_blueprints

        app = Flask(__name__)
        register_blueprints(app)

        # Get all registered rules
        rules = [rule.rule for rule in app.url_rules]

        # Check API routes exist with /api prefix
        assert any('/api/status' in r for r in rules)
        assert any('/api/nodes' in r for r in rules)
        assert any('/api/service' in r for r in rules)


class TestNetworkParsing:
    """Test network parsing utilities"""

    def test_tcp_state_mapping(self):
        """Test TCP state hex to name mapping"""
        from web.blueprints.network import TCP_STATES
        assert TCP_STATES.get('01') == 'ESTABLISHED'
        assert TCP_STATES.get('0A') == 'LISTEN'
        assert TCP_STATES.get('06') == 'TIME_WAIT'

    def test_hex_to_ip(self):
        """Test hex to IP conversion"""
        from web.blueprints.network import hex_to_ip
        # 0100007F = 127.0.0.1 (little endian)
        assert hex_to_ip('0100007F') == '127.0.0.1'
        # 00000000 = 0.0.0.0
        assert hex_to_ip('00000000') == '0.0.0.0'
