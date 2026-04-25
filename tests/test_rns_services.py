"""
Tests for RNS service registry and announce parsers.
"""

import sys
sys.path.insert(0, 'src')

import unittest
from datetime import datetime


class TestRNSServiceTypes(unittest.TestCase):
    """Test RNS service type definitions"""

    def test_service_types_defined(self):
        """Verify all expected service types exist"""
        from gateway.rns_services import RNSServiceType

        # Check all expected types
        self.assertTrue(hasattr(RNSServiceType, 'LXMF_DELIVERY'))
        self.assertTrue(hasattr(RNSServiceType, 'LXMF_PROPAGATION'))
        self.assertTrue(hasattr(RNSServiceType, 'NOMAD_PAGE'))
        self.assertTrue(hasattr(RNSServiceType, 'UNKNOWN'))

    def test_aspect_mapping(self):
        """Verify aspect to service type mapping"""
        from gateway.rns_services import ASPECT_TO_SERVICE, RNSServiceType

        self.assertEqual(ASPECT_TO_SERVICE['lxmf.delivery'], RNSServiceType.LXMF_DELIVERY)
        self.assertEqual(ASPECT_TO_SERVICE['nomadnetwork.node'], RNSServiceType.NOMAD_PAGE)


class TestLXMFParser(unittest.TestCase):
    """Test LXMF announce parser"""

    def test_parse_simple_name(self):
        """Parse simple UTF-8 name"""
        from gateway.rns_services import LXMFParser

        app_data = b'TestUser'
        info = LXMFParser.parse(app_data, 'lxmf.delivery')

        self.assertEqual(info.display_name, 'TestUser')
        self.assertEqual(info.service_type.name, 'LXMF_DELIVERY')

    def test_parse_empty_data(self):
        """Handle empty app_data gracefully"""
        from gateway.rns_services import LXMFParser

        info = LXMFParser.parse(b'', 'lxmf.delivery')
        self.assertEqual(info.display_name, '')
        self.assertIsNone(info.latitude)

    def test_parse_unicode_name(self):
        """Parse Unicode characters in name"""
        from gateway.rns_services import LXMFParser

        app_data = 'Tëst Üsér 日本語'.encode('utf-8')
        info = LXMFParser.parse(app_data, 'lxmf.delivery')

        self.assertIn('Tëst', info.display_name)

    def test_msgpack_start_detection(self):
        """Detect msgpack markers correctly"""
        from gateway.rns_services import LXMFParser

        # fixmap marker (0x80-0x8f)
        idx = LXMFParser._find_msgpack_start(b'Name\x82\xa3lat\xcb@')
        self.assertEqual(idx, 4)

        # No msgpack
        idx = LXMFParser._find_msgpack_start(b'JustAName')
        self.assertEqual(idx, -1)

    def test_strips_lxmrouter_length_prefix(self):
        """Real LXMRouter.announce() framing: <uint8 len><name>.

        Pre-fix the parser leaked the length-prefix byte into
        display_name (e.g. ``!MeshForge Gateway (...)`` because byte 0
        was 0x21 = ASCII ``!`` = 33 = the actual name length). Captured
        from moc/fleet-host-1/fleet-host-3 announces 2026-04-25.
        """
        from gateway.rns_services import LXMFParser

        # 33-char name → length byte 0x21 (which renders as `!`)
        name = b'MeshForge Gateway (fleet-host-0)'
        assert len(name) == 33
        app_data = bytes([len(name)]) + name

        info = LXMFParser.parse(app_data, 'lxmf.delivery')
        self.assertEqual(info.display_name, 'MeshForge Gateway (fleet-host-0)')
        # No leading `!`, `"`, etc.
        self.assertFalse(info.display_name.startswith('!'))
        self.assertFalse(info.display_name.startswith('"'))

    def test_strips_lxmrouter_length_prefix_34_chars(self):
        """34-char name → length byte 0x22 (`"`). Matches fleet-host-1/fleet-host-3."""
        from gateway.rns_services import LXMFParser

        name = b'MeshForge Gateway (meshforge-fleet-host-1)'
        assert len(name) == 34
        app_data = bytes([len(name)]) + name

        info = LXMFParser.parse(app_data, 'lxmf.delivery')
        self.assertEqual(info.display_name, 'MeshForge Gateway (meshforge-fleet-host-1)')

    def test_lxmrouter_framing_with_msgpack_telemetry(self):
        """``<len><name>`` followed by msgpack telemetry — Sideband shape."""
        from gateway.rns_services import LXMFParser

        name = b'TestNode'
        # Minimal msgpack fixmap with one float key (lat). Even without
        # msgpack installed the parser must still extract the name
        # cleanly and not crash.
        telemetry = b'\x81\xa3lat\xcb@F\x80\x00\x00\x00\x00\x00'
        app_data = bytes([len(name)]) + name + telemetry

        info = LXMFParser.parse(app_data, 'lxmf.delivery')
        self.assertEqual(info.display_name, 'TestNode')

    def test_length_prefix_falls_back_when_decode_fails(self):
        """If byte 0 isn't a real length prefix (e.g. random binary
        before a name), fall back to scanning for msgpack markers."""
        from gateway.rns_services import LXMFParser

        # Strategy-1 prefix would point past EOF or into garbage.
        # Strategy-2 fallback (legacy heuristic) finds no msgpack
        # marker and returns the whole thing.
        app_data = b'PlainNameNoPrefix'
        info = LXMFParser.parse(app_data, 'lxmf.delivery')
        # Either strategy succeeds; the name should land cleanly.
        self.assertIn('Plain', info.display_name)


class TestNomadParser(unittest.TestCase):
    """Test Nomad Network parser"""

    def test_parse_page_info(self):
        """Parse Nomad page information"""
        from gateway.rns_services import NomadParser

        app_data = b'My Nomad Page\nWelcome to my page'
        info = NomadParser.parse(app_data, 'nomadnetwork.node')

        self.assertEqual(info.display_name, 'My Nomad Page')
        self.assertEqual(info.description, 'Welcome to my page')
        self.assertEqual(info.service_type.name, 'NOMAD_PAGE')
        self.assertIn('pages', info.capabilities)


class TestGenericParser(unittest.TestCase):
    """Test generic/fallback parser"""

    def test_parse_unknown_service(self):
        """Parse unknown service type"""
        from gateway.rns_services import GenericParser

        app_data = b'SomeUnknownService'
        info = GenericParser.parse(app_data, 'unknown.aspect')

        self.assertEqual(info.display_name, 'SomeUnknownService')
        self.assertEqual(info.service_type.name, 'UNKNOWN')
        self.assertIn('app_data_length', info.metadata)


class TestServiceRegistry(unittest.TestCase):
    """Test RNS service registry"""

    def test_registry_singleton(self):
        """Service registry is a singleton"""
        from gateway.rns_services import get_service_registry

        reg1 = get_service_registry()
        reg2 = get_service_registry()

        self.assertIs(reg1, reg2)

    def test_builtin_parsers_registered(self):
        """Built-in parsers are registered"""
        from gateway.rns_services import get_service_registry

        registry = get_service_registry()

        self.assertIn('lxmf.delivery', registry._parsers)
        self.assertIn('nomadnetwork.node', registry._parsers)

    def test_parse_announce_lxmf(self):
        """Parse LXMF announce through registry"""
        from gateway.rns_services import get_service_registry

        registry = get_service_registry()
        dest_hash = bytes.fromhex('0123456789abcdef0123456789abcdef')

        event = registry.parse_announce(
            dest_hash=dest_hash,
            identity=None,
            app_data=b'TestNode',
            aspect='lxmf.delivery'
        )

        self.assertEqual(event.service_info.display_name, 'TestNode')
        self.assertEqual(event.service_info.service_type.name, 'LXMF_DELIVERY')

    def test_get_stats(self):
        """Get service discovery statistics"""
        from gateway.rns_services import RNSServiceRegistry

        # Create fresh registry for stats test
        registry = RNSServiceRegistry()

        # Use valid 32-char hex strings (16 bytes each)
        registry.parse_announce(bytes.fromhex('11111111111111111111111111111111'), None, b'Test1', 'lxmf.delivery')
        registry.parse_announce(bytes.fromhex('22222222222222222222222222222222'), None, b'Test2', 'lxmf.delivery')
        registry.parse_announce(bytes.fromhex('33333333333333333333333333333333'), None, b'Test3', 'nomadnetwork.node')

        stats = registry.get_stats()

        self.assertEqual(stats.get('LXMF_DELIVERY', 0), 2)
        self.assertEqual(stats.get('NOMAD_PAGE', 0), 1)


class TestNetworkTopology(unittest.TestCase):
    """Test network topology graph"""

    def test_add_nodes(self):
        """Add nodes to topology"""
        from gateway.network_topology import NetworkTopology

        topo = NetworkTopology()
        topo.add_node('node1', {'name': 'Node 1'})
        topo.add_node('node2', {'name': 'Node 2'})

        stats = topo.get_topology_stats()
        self.assertEqual(stats['node_count'], 2)

    def test_add_edge(self):
        """Add edge between nodes"""
        from gateway.network_topology import NetworkTopology

        topo = NetworkTopology()
        edge = topo.add_edge('local', 'node1', hops=3)

        self.assertEqual(edge.source_id, 'local')
        self.assertEqual(edge.dest_id, 'node1')
        self.assertEqual(edge.hops, 3)

    def test_find_path(self):
        """Find path between nodes"""
        from gateway.network_topology import NetworkTopology

        topo = NetworkTopology()
        topo.add_edge('A', 'B', hops=1)
        topo.add_edge('B', 'C', hops=2)
        topo.add_edge('A', 'C', hops=5)  # Direct but longer

        # Should find A -> B -> C as shortest weighted path
        path = topo.find_path('A', 'C')

        self.assertIsNotNone(path)
        self.assertEqual(path[0], 'A')
        self.assertEqual(path[-1], 'C')

    def test_edge_weight_calculation(self):
        """Edge weight includes hops and freshness"""
        from gateway.network_topology import NetworkEdge

        edge = NetworkEdge(source_id='A', dest_id='B', hops=2)
        weight = edge.get_weight()

        # Weight should be >= hops + 1
        self.assertGreaterEqual(weight, 3.0)

    def test_topology_to_dict(self):
        """Export topology as dictionary"""
        from gateway.network_topology import NetworkTopology

        topo = NetworkTopology()
        topo.add_edge('local', 'dest1', hops=1)

        data = topo.to_dict()

        self.assertIn('nodes', data)
        self.assertIn('edges', data)
        self.assertIn('stats', data)
        self.assertEqual(len(data['edges']), 1)


class TestUnifiedNodeWithServices(unittest.TestCase):
    """Test UnifiedNode with RNS service info"""

    def test_node_from_rns_with_service_info(self):
        """Create node from RNS with service info"""
        from gateway.node_tracker import UnifiedNode
        from gateway.rns_services import ServiceInfo, RNSServiceType

        service_info = ServiceInfo(
            service_type=RNSServiceType.LXMF_DELIVERY,
            aspect='lxmf.delivery',
            display_name='TestNode',
            latitude=37.7749,
            longitude=-122.4194,
            battery=85,
        )

        node = UnifiedNode.from_rns(
            rns_hash=bytes.fromhex('0123456789abcdef0123456789abcdef'),
            name='',
            app_data=None,
            service_info=service_info,
            aspect='lxmf.delivery',
        )

        self.assertEqual(node.name, 'TestNode')
        self.assertEqual(node.service_type, 'LXMF_DELIVERY')
        self.assertEqual(node.service_aspect, 'lxmf.delivery')
        self.assertAlmostEqual(node.position.latitude, 37.7749, places=4)
        self.assertEqual(node.telemetry.battery_level, 85)

    def test_node_to_dict_includes_service(self):
        """Node serialization includes service info"""
        from gateway.node_tracker import UnifiedNode

        node = UnifiedNode(
            id='rns_test123',
            network='rns',
            name='Test',
            service_type='LXMF_DELIVERY',
            service_aspect='lxmf.delivery',
        )

        data = node.to_dict()

        self.assertEqual(data['service_type'], 'LXMF_DELIVERY')
        self.assertEqual(data['service_aspect'], 'lxmf.delivery')


if __name__ == '__main__':
    unittest.main()
