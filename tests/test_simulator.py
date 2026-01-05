"""
Tests for MeshForge Hardware Simulator

TDD approach: Tests for RF and mesh network simulation.
"""

import sys
import os
import math

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.simulator import (
    MeshSimulator,
    RFSimulator,
    SimulatedNode,
    SimulatedMessage,
    SimulationMode,
    RFSimulationResult,
    get_mesh_simulator,
    get_rf_simulator,
    is_simulation_enabled,
)


class TestSimulationMode:
    """Test SimulationMode enum"""

    def test_mode_values(self):
        """All simulation modes should have correct values"""
        assert SimulationMode.DISABLED.value == "disabled"
        assert SimulationMode.RF_ONLY.value == "rf_only"
        assert SimulationMode.MESH_NETWORK.value == "mesh_network"
        assert SimulationMode.FULL.value == "full"


class TestSimulatedNode:
    """Test SimulatedNode dataclass"""

    def test_node_creation(self):
        """SimulatedNode should store all fields correctly"""
        node = SimulatedNode(
            node_id="!abc12345",
            short_name="TEST",
            long_name="Test Node",
            latitude=19.7297,
            longitude=-155.0900,
            altitude=50.0,
            battery_level=85,
            snr=5.5,
            rssi=-90,
            hops_away=2,
        )

        assert node.node_id == "!abc12345"
        assert node.short_name == "TEST"
        assert node.latitude == 19.7297
        assert node.longitude == -155.0900
        assert node.altitude == 50.0
        assert node.battery_level == 85
        assert node.snr == 5.5
        assert node.hops_away == 2

    def test_node_to_dict(self):
        """SimulatedNode should convert to meshtastic-compatible dict"""
        node = SimulatedNode(
            node_id="!abc12345",
            short_name="TEST",
            long_name="Test Node",
            latitude=19.7297,
            longitude=-155.0900,
        )

        d = node.to_dict()

        assert "user" in d
        assert d["user"]["id"] == "!abc12345"
        assert d["user"]["shortName"] == "TEST"
        assert "position" in d
        assert d["position"]["latitude"] == 19.7297
        assert "deviceMetrics" in d

    def test_node_defaults(self):
        """SimulatedNode should have sensible defaults"""
        node = SimulatedNode(
            node_id="!test",
            short_name="T",
            long_name="Test",
            latitude=0.0,
            longitude=0.0,
        )

        assert node.altitude == 0.0
        assert node.battery_level == 100
        assert node.is_online is True
        assert node.hops_away == 0
        assert node.hardware_model == "SIMULATOR"


class TestSimulatedMessage:
    """Test SimulatedMessage dataclass"""

    def test_message_creation(self):
        """SimulatedMessage should store message data"""
        msg = SimulatedMessage(
            from_node="!sender01",
            to_node="!receiver1",
            message="Hello mesh!",
        )

        assert msg.from_node == "!sender01"
        assert msg.to_node == "!receiver1"
        assert msg.message == "Hello mesh!"
        assert msg.message_type == "TEXT"
        assert msg.channel == 0

    def test_message_defaults(self):
        """SimulatedMessage should have sensible defaults"""
        msg = SimulatedMessage(
            from_node="!test",
            to_node="^all",
            message="Test",
        )

        assert msg.hop_limit == 3
        assert msg.hop_start == 3
        assert msg.timestamp is not None


class TestMeshSimulator:
    """Test MeshSimulator class"""

    def test_simulator_creation(self):
        """Simulator should initialize in disabled state"""
        sim = MeshSimulator()

        assert sim.mode == SimulationMode.DISABLED
        assert sim.is_enabled is False
        assert len(sim.get_nodes()) == 0

    def test_enable_mesh_mode(self):
        """Enabling mesh mode should create nodes"""
        sim = MeshSimulator()
        sim.enable(SimulationMode.MESH_NETWORK)

        assert sim.is_enabled is True
        assert sim.mode == SimulationMode.MESH_NETWORK
        assert len(sim.get_nodes()) > 0

    def test_disable_simulation(self):
        """Disabling should clear all state"""
        sim = MeshSimulator()
        sim.enable(SimulationMode.MESH_NETWORK)
        sim.disable()

        assert sim.is_enabled is False
        assert len(sim.get_nodes()) == 0
        assert len(sim.get_messages()) == 0

    def test_hawaii_preset(self):
        """Hawaii preset should create Hawaiian island nodes"""
        sim = MeshSimulator()
        sim.set_preset(use_hawaii=True)
        sim.enable(SimulationMode.MESH_NETWORK)

        nodes = sim.get_nodes()

        # Check for Hawaii locations
        node_names = [n.short_name for n in nodes]
        assert "HILO" in node_names
        assert "KONA" in node_names
        assert "MAUI" in node_names

    def test_generic_preset(self):
        """Generic preset should create test nodes"""
        sim = MeshSimulator()
        sim.set_preset(use_hawaii=False)
        sim.enable(SimulationMode.MESH_NETWORK)

        nodes = sim.get_nodes()

        # Check for generic test nodes
        node_names = [n.short_name for n in nodes]
        assert "TST1" in node_names

    def test_send_message(self):
        """Should be able to send simulated messages"""
        sim = MeshSimulator()
        sim.enable(SimulationMode.MESH_NETWORK)

        msg = sim.send_message("!sim00001", "Test message", "^all")

        assert msg.message == "Test message"
        assert msg.from_node == "!sim00001"
        assert len(sim.get_messages()) == 1

    def test_message_callback(self):
        """Message callbacks should be triggered"""
        sim = MeshSimulator()
        sim.enable(SimulationMode.MESH_NETWORK)

        received = []
        sim.on_message(lambda m: received.append(m))

        sim.send_message("!test", "Callback test")

        assert len(received) == 1
        assert received[0].message == "Callback test"

    def test_get_node_by_id(self):
        """Should retrieve specific node by ID"""
        sim = MeshSimulator()
        sim.enable(SimulationMode.MESH_NETWORK)

        node = sim.get_node("!sim00001")

        assert node is not None
        assert node.node_id == "!sim00001"

    def test_get_nonexistent_node(self):
        """Should return None for non-existent node"""
        sim = MeshSimulator()
        sim.enable(SimulationMode.MESH_NETWORK)

        node = sim.get_node("!nonexistent")

        assert node is None

    def test_nodes_as_dict(self):
        """Should return nodes in dict format"""
        sim = MeshSimulator()
        sim.enable(SimulationMode.MESH_NETWORK)

        nodes_dict = sim.get_nodes_as_dict()

        assert isinstance(nodes_dict, list)
        assert len(nodes_dict) > 0
        assert "user" in nodes_dict[0]

    def test_add_custom_node(self):
        """Should be able to add custom nodes"""
        sim = MeshSimulator()
        sim.enable(SimulationMode.MESH_NETWORK)

        initial_count = len(sim.get_nodes())

        custom = SimulatedNode(
            node_id="!custom01",
            short_name="CUST",
            long_name="Custom Node",
            latitude=20.0,
            longitude=-156.0,
        )
        sim.add_node(custom)

        assert len(sim.get_nodes()) == initial_count + 1
        assert sim.get_node("!custom01") is not None

    def test_remove_node(self):
        """Should be able to remove nodes"""
        sim = MeshSimulator()
        sim.enable(SimulationMode.MESH_NETWORK)

        initial_count = len(sim.get_nodes())
        sim.remove_node("!sim00001")

        assert len(sim.get_nodes()) == initial_count - 1
        assert sim.get_node("!sim00001") is None


class TestRFSimulator:
    """Test RFSimulator class"""

    def test_rf_simulator_creation(self):
        """RF Simulator should initialize with defaults"""
        rf = RFSimulator()

        assert rf.frequency_mhz == 915.0
        assert rf.tx_power_dbm == 20.0
        assert rf.antenna_gain_dbi == 2.0

    def test_custom_frequency(self):
        """Should accept custom frequency"""
        rf = RFSimulator(frequency_mhz=868.0)

        assert rf.frequency_mhz == 868.0

    def test_fspl_calculation(self):
        """FSPL calculation should be accurate"""
        rf = RFSimulator(frequency_mhz=915.0)

        # At 1 km, 915 MHz: FSPL ≈ 91.7 dB
        fspl = rf.calculate_fspl(1.0)

        assert 90.0 < fspl < 93.0

    def test_fspl_zero_distance(self):
        """FSPL should be 0 at zero distance"""
        rf = RFSimulator()
        fspl = rf.calculate_fspl(0.0)

        assert fspl == 0.0

    def test_fspl_increases_with_distance(self):
        """FSPL should increase with distance"""
        rf = RFSimulator()

        fspl_1km = rf.calculate_fspl(1.0)
        fspl_10km = rf.calculate_fspl(10.0)

        assert fspl_10km > fspl_1km
        # Should be approximately 20 dB more (inverse square law)
        assert 18.0 < (fspl_10km - fspl_1km) < 22.0

    def test_fresnel_radius(self):
        """Fresnel radius calculation should be reasonable"""
        rf = RFSimulator(frequency_mhz=915.0)

        # At 10 km path, midpoint
        fresnel = rf.calculate_fresnel_radius(10.0, position=0.5)

        # Should be tens of meters for 10km at 915MHz
        assert 10.0 < fresnel < 100.0

    def test_fresnel_zero_distance(self):
        """Fresnel radius should be 0 at zero distance"""
        rf = RFSimulator()
        fresnel = rf.calculate_fresnel_radius(0.0)

        assert fresnel == 0.0

    def test_earth_bulge(self):
        """Earth bulge calculation should be reasonable"""
        rf = RFSimulator()

        # At 50 km path, earth bulge should be significant
        bulge = rf.calculate_earth_bulge(50.0, position=0.5)

        # Should be tens of meters
        assert bulge > 10.0

    def test_earth_bulge_short_distance(self):
        """Earth bulge should be small for short distances"""
        rf = RFSimulator()
        bulge = rf.calculate_earth_bulge(1.0)

        # Should be less than 1 meter for 1 km
        assert bulge < 1.0

    def test_simulate_path_clear(self):
        """Path simulation should work for clear LOS"""
        rf = RFSimulator()

        result = rf.simulate_path(
            distance_km=5.0,
            terrain="clear_los",
            weather="clear"
        )

        assert isinstance(result, RFSimulationResult)
        assert result.distance_km == 5.0
        assert result.fspl_db > 0
        assert result.fresnel_radius_m > 0
        assert result.terrain_loss_db == 0.0
        assert result.link_quality in ["Excellent", "Good", "Marginal", "Poor", "No Link"]

    def test_simulate_path_obstructed(self):
        """Obstructed path should have worse link quality"""
        rf = RFSimulator()

        clear = rf.simulate_path(5.0, terrain="clear_los")
        urban = rf.simulate_path(5.0, terrain="urban")

        # Urban should have more path loss
        assert urban.terrain_loss_db > clear.terrain_loss_db
        assert urban.total_path_loss_db > clear.total_path_loss_db

    def test_simulate_path_weather(self):
        """Weather should affect path loss"""
        rf = RFSimulator()

        clear = rf.simulate_path(5.0, weather="clear")
        rain = rf.simulate_path(5.0, weather="heavy_rain")

        assert rain.atmospheric_loss_db > clear.atmospheric_loss_db

    def test_terrain_presets(self):
        """All terrain presets should be defined"""
        assert "clear_los" in RFSimulator.TERRAIN_PRESETS
        assert "light_foliage" in RFSimulator.TERRAIN_PRESETS
        assert "heavy_foliage" in RFSimulator.TERRAIN_PRESETS
        assert "suburban" in RFSimulator.TERRAIN_PRESETS
        assert "urban" in RFSimulator.TERRAIN_PRESETS
        assert "mountainous" in RFSimulator.TERRAIN_PRESETS

    def test_frequency_presets(self):
        """Frequency presets should be defined"""
        assert "US915" in RFSimulator.FREQUENCIES
        assert "EU868" in RFSimulator.FREQUENCIES
        assert RFSimulator.FREQUENCIES["US915"] == 915.0
        assert RFSimulator.FREQUENCIES["EU868"] == 868.0

    def test_coverage_simulation(self):
        """Coverage simulation should return points"""
        rf = RFSimulator()

        points = rf.simulate_coverage(
            center_lat=19.7297,
            center_lon=-155.0900,
            radius_km=5.0,
            resolution=5
        )

        assert len(points) > 0
        assert "lat" in points[0]
        assert "lon" in points[0]
        assert "signal_quality" in points[0]


class TestGlobalSimulators:
    """Test global simulator instances"""

    def test_get_mesh_simulator(self):
        """Should return singleton mesh simulator"""
        sim1 = get_mesh_simulator()
        sim2 = get_mesh_simulator()

        assert sim1 is sim2

    def test_get_rf_simulator(self):
        """Should return singleton RF simulator"""
        rf1 = get_rf_simulator()
        rf2 = get_rf_simulator()

        assert rf1 is rf2

    def test_is_simulation_enabled_default(self):
        """Simulation should be disabled by default"""
        # Reset the global simulator
        import utils.simulator as sim_module
        sim_module._mesh_simulator = None

        # Fresh check
        sim = get_mesh_simulator()
        sim.disable()

        assert is_simulation_enabled() is False


class TestLinkQualityRanges:
    """Test link quality determination"""

    def test_excellent_link(self):
        """Short clear path should be excellent"""
        rf = RFSimulator()
        result = rf.simulate_path(0.5, terrain="clear_los")

        # Very short path should usually be excellent
        assert result.link_quality in ["Excellent", "Good"]

    def test_no_link_long_distance(self):
        """Very long obstructed path should have no link"""
        rf = RFSimulator()
        rf.tx_power_dbm = 10  # Low power
        result = rf.simulate_path(100.0, terrain="urban", weather="heavy_rain")

        # Should be very poor or no link
        assert result.link_quality in ["Poor", "No Link", "Marginal"]


def run_tests():
    """Run all tests without pytest"""
    import traceback

    test_classes = [
        TestSimulationMode,
        TestSimulatedNode,
        TestSimulatedMessage,
        TestMeshSimulator,
        TestRFSimulator,
        TestGlobalSimulators,
        TestLinkQualityRanges,
    ]

    total = 0
    passed = 0
    failed = 0

    for test_class in test_classes:
        print(f"\n{test_class.__name__}")
        print("-" * 40)

        instance = test_class()
        for name in dir(instance):
            if name.startswith("test_"):
                total += 1
                try:
                    getattr(instance, name)()
                    print(f"  PASS: {name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAIL: {name}")
                    print(f"        {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERROR: {name}")
                    traceback.print_exc()
                    failed += 1

    print("\n" + "=" * 40)
    print(f"Total: {total} | Passed: {passed} | Failed: {failed}")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
