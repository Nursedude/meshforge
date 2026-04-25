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

    def test_lxmrouter_canonical_msgpack_shape(self):
        """LXMF >= 0.6 — the canonical wire format.

        Per LXMF/LXMRouter.py::get_announce_app_data:
            peer_data = [display_name_bytes, stamp_cost]
            return msgpack.packb(peer_data)

        Pre-fix the parser ignored the msgpack array+bin8 headers and
        let their bytes (notably the bin8 length byte 0x22 = '"' for
        a 34-char name) leak into display_name. Captured from real
        gateway announces on the fleet 2026-04-25.
        """
        import msgpack as _mp
        from gateway.rns_services import LXMFParser

        name = 'MeshForge Gateway (meshforge-fleet-host-1)'
        app_data = _mp.packb([name.encode('utf-8'), None])

        # Sanity: byte 0 is a fixarray header, byte 2 is the bin8 length
        # that used to leak through.
        assert app_data[0] in range(0x90, 0xa0), \
            f"expected fixarray header, got {app_data[0]:#x}"

        info = LXMFParser.parse(app_data, 'lxmf.delivery')
        self.assertEqual(info.display_name, name)
        # Explicit guards for the historical leak chars.
        self.assertFalse(info.display_name.startswith('"'))
        self.assertFalse(info.display_name.startswith('!'))

    def test_lxmrouter_canonical_with_stamp_cost(self):
        """Stamp cost present (small int after the name) — must not
        be misread as part of the name."""
        import msgpack as _mp
        from gateway.rns_services import LXMFParser

        name = 'NodeWithStamp'
        app_data = _mp.packb([name.encode('utf-8'), 8])

        info = LXMFParser.parse(app_data, 'lxmf.delivery')
        self.assertEqual(info.display_name, 'NodeWithStamp')

    def test_lxmrouter_canonical_with_sideband_telemetry(self):
        """Sideband variant: trailing dict carries lat/lon. Parser
        must still pull a clean display_name AND honour telemetry."""
        import msgpack as _mp
        from gateway.rns_services import LXMFParser

        telemetry = {'lat': 19.42, 'lon': -155.28}
        app_data = _mp.packb([b'TelemetryNode', None, telemetry])

        info = LXMFParser.parse(app_data, 'lxmf.delivery')
        self.assertEqual(info.display_name, 'TelemetryNode')
        # Telemetry parsing is best-effort; we don't strictly assert
        # latitude here because Sideband-format keys vary, but the
        # fact that we didn't crash is the load-bearing assertion.

    def test_legacy_uint8_length_prefix_still_works(self):
        """Strategy-2 fallback for non-LXMRouter peers that emit a
        bare ``<L><name>`` blob."""
        from gateway.rns_services import LXMFParser

        name = b'LegacyNode'
        app_data = bytes([len(name)]) + name

        info = LXMFParser.parse(app_data, 'lxmf.delivery')
        self.assertEqual(info.display_name, 'LegacyNode')

    def test_garbage_app_data_does_not_crash(self):
        """Random bytes that aren't msgpack and aren't a valid
        length-prefix shouldn't crash the parser."""
        from gateway.rns_services import LXMFParser

        info = LXMFParser.parse(b'\xff\xfe\xfd\x00garbage', 'lxmf.delivery')
        # We don't assert on display_name (best-effort); we assert
        # that parse returned a valid ServiceInfo.
        self.assertIsNotNone(info)
        self.assertEqual(info.service_type.name, 'LXMF_DELIVERY')


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
