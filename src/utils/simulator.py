"""
MeshForge Hardware Simulator

Provides simulation modes for testing RF and mesh network functionality
without physical hardware. Useful for development, demos, and testing.

Principle: Don't break code. Safety over features.
Simulation mode should be clearly indicated and not affect real hardware.
"""

import random
import time
import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
from enum import Enum
from datetime import datetime, timedelta


class SimulationMode(Enum):
    """Available simulation modes"""
    DISABLED = "disabled"
    RF_ONLY = "rf_only"           # Simulate RF calculations only
    MESH_NETWORK = "mesh_network"  # Simulate a mesh network with nodes
    FULL = "full"                  # Full hardware simulation


@dataclass
class SimulatedNode:
    """A simulated mesh network node"""
    node_id: str
    short_name: str
    long_name: str
    latitude: float
    longitude: float
    altitude: float = 0.0
    battery_level: int = 100
    snr: float = 0.0
    rssi: int = -100
    last_heard: datetime = field(default_factory=datetime.now)
    hops_away: int = 0
    is_online: bool = True
    hardware_model: str = "SIMULATOR"
    role: str = "CLIENT"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        # Parse node ID to numeric value (handle both hex and non-hex formats)
        num = 0
        if self.node_id.startswith("!"):
            node_hex = self.node_id[1:]  # Remove !
            try:
                num = int(node_hex, 16)
            except ValueError:
                # Non-hex node ID, generate hash
                num = hash(self.node_id) & 0xFFFFFFFF
        return {
            "num": num,
            "user": {
                "id": self.node_id,
                "shortName": self.short_name,
                "longName": self.long_name,
                "hwModel": self.hardware_model,
                "role": self.role,
            },
            "position": {
                "latitude": self.latitude,
                "longitude": self.longitude,
                "altitude": int(self.altitude),
            },
            "deviceMetrics": {
                "batteryLevel": self.battery_level,
            },
            "snr": self.snr,
            "lastHeard": int(self.last_heard.timestamp()),
            "hopsAway": self.hops_away,
        }


@dataclass
class SimulatedMessage:
    """A simulated mesh message"""
    from_node: str
    to_node: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    message_type: str = "TEXT"
    channel: int = 0
    hop_limit: int = 3
    hop_start: int = 3


@dataclass
class RFSimulationResult:
    """Result of RF path simulation"""
    distance_km: float
    fspl_db: float
    fresnel_radius_m: float
    earth_bulge_m: float
    estimated_snr: float
    link_quality: str  # "Excellent", "Good", "Marginal", "Poor", "No Link"
    terrain_loss_db: float = 0.0
    atmospheric_loss_db: float = 0.0
    total_path_loss_db: float = 0.0


class MeshSimulator:
    """
    Mesh network simulator for testing without hardware.

    Usage:
        simulator = MeshSimulator()
        simulator.enable(SimulationMode.MESH_NETWORK)

        # Get simulated nodes
        nodes = simulator.get_nodes()

        # Send simulated message
        simulator.send_message("!sim00001", "Hello from simulator!")

        # Get messages
        messages = simulator.get_messages()
    """

    # Hawaiian island node presets for realistic testing
    HAWAII_NODES = [
        ("!sim00001", "HILO", "Hilo Base Station", 19.7297, -155.0900, 50),
        ("!sim00002", "KONA", "Kona Relay", 19.6400, -155.9969, 25),
        ("!sim00003", "MAUI", "Maui Gateway", 20.7984, -156.3319, 100),
        ("!sim00004", "OAHU", "Oahu Hub", 21.3069, -157.8583, 200),
        ("!sim00005", "MKA1", "Mauna Kea 1", 19.8207, -155.4680, 4200),
        ("!sim00006", "VLY1", "Waipio Valley", 20.1167, -155.5833, 15),
        ("!sim00007", "KAU1", "Kau District", 19.2000, -155.5000, 300),
        ("!sim00008", "PHN1", "Pahoa Node", 19.4978, -154.9463, 180),
    ]

    # Generic test nodes for non-location-specific testing
    GENERIC_NODES = [
        ("!test0001", "TST1", "Test Node 1", 0.0, 0.0, 0),
        ("!test0002", "TST2", "Test Node 2", 0.001, 0.001, 0),
        ("!test0003", "TST3", "Test Node 3", 0.002, 0.0, 0),
        ("!test0004", "RLAY", "Relay Node", 0.001, 0.0005, 50),
        ("!test0005", "GTW1", "Gateway 1", 0.0, 0.002, 0),
    ]

    def __init__(self):
        self._mode = SimulationMode.DISABLED
        self._nodes: Dict[str, SimulatedNode] = {}
        self._messages: List[SimulatedMessage] = []
        self._message_callbacks: List[Callable] = []
        self._node_callbacks: List[Callable] = []
        self._use_hawaii_preset = True
        self._simulation_speed = 1.0  # 1.0 = real-time, 2.0 = 2x speed
        self._last_update = datetime.now()

    @property
    def mode(self) -> SimulationMode:
        """Get current simulation mode"""
        return self._mode

    @property
    def is_enabled(self) -> bool:
        """Check if simulation is enabled"""
        return self._mode != SimulationMode.DISABLED

    def enable(self, mode: SimulationMode = SimulationMode.MESH_NETWORK):
        """Enable simulation mode"""
        self._mode = mode
        if mode in (SimulationMode.MESH_NETWORK, SimulationMode.FULL):
            self._initialize_nodes()
        print(f"[Simulator] Enabled: {mode.value}")

    def disable(self):
        """Disable simulation mode"""
        self._mode = SimulationMode.DISABLED
        self._nodes.clear()
        self._messages.clear()
        print("[Simulator] Disabled")

    def set_preset(self, use_hawaii: bool = True):
        """Set node preset (Hawaii or Generic)"""
        self._use_hawaii_preset = use_hawaii
        if self.is_enabled:
            self._initialize_nodes()

    def _initialize_nodes(self):
        """Initialize simulated nodes"""
        self._nodes.clear()
        presets = self.HAWAII_NODES if self._use_hawaii_preset else self.GENERIC_NODES

        for node_id, short, long, lat, lon, alt in presets:
            self._nodes[node_id] = SimulatedNode(
                node_id=node_id,
                short_name=short,
                long_name=long,
                latitude=lat,
                longitude=lon,
                altitude=alt,
                battery_level=random.randint(50, 100),
                snr=random.uniform(-5.0, 10.0),
                rssi=random.randint(-120, -60),
                hops_away=random.randint(0, 3),
                is_online=random.random() > 0.1,  # 90% online
            )

        # Set first node as "local" (hops_away = 0)
        if self._nodes:
            first_key = list(self._nodes.keys())[0]
            self._nodes[first_key].hops_away = 0

    def get_nodes(self) -> List[SimulatedNode]:
        """Get all simulated nodes"""
        return list(self._nodes.values())

    def get_node(self, node_id: str) -> Optional[SimulatedNode]:
        """Get a specific node by ID"""
        return self._nodes.get(node_id)

    def get_nodes_as_dict(self) -> List[dict]:
        """Get nodes in meshtastic-compatible dict format"""
        return [node.to_dict() for node in self._nodes.values()]

    def add_node(self, node: SimulatedNode):
        """Add a custom node to simulation"""
        self._nodes[node.node_id] = node
        for callback in self._node_callbacks:
            callback("added", node)

    def remove_node(self, node_id: str):
        """Remove a node from simulation"""
        if node_id in self._nodes:
            node = self._nodes.pop(node_id)
            for callback in self._node_callbacks:
                callback("removed", node)

    def send_message(self, from_node: str, message: str, to_node: str = "^all") -> SimulatedMessage:
        """Simulate sending a message"""
        msg = SimulatedMessage(
            from_node=from_node,
            to_node=to_node,
            message=message,
        )
        self._messages.append(msg)

        # Notify callbacks
        for callback in self._message_callbacks:
            callback(msg)

        return msg

    def receive_simulated_message(self, delay_ms: int = 500):
        """Generate a random incoming message (for testing)"""
        if not self._nodes:
            return None

        # Pick random sender (not the first/local node)
        senders = [n for n in self._nodes.values() if n.hops_away > 0 and n.is_online]
        if not senders:
            return None

        sender = random.choice(senders)
        messages = [
            "Hello mesh!",
            "Testing 1 2 3",
            "Good signal here",
            "Anyone copy?",
            "Check check",
            f"Battery at {sender.battery_level}%",
            "Position update",
            "All clear",
        ]

        return self.send_message(
            from_node=sender.node_id,
            message=random.choice(messages),
            to_node="^all"
        )

    def get_messages(self, limit: int = 100) -> List[SimulatedMessage]:
        """Get recent messages"""
        return self._messages[-limit:]

    def clear_messages(self):
        """Clear message history"""
        self._messages.clear()

    def on_message(self, callback: Callable):
        """Register callback for new messages"""
        self._message_callbacks.append(callback)

    def on_node_change(self, callback: Callable):
        """Register callback for node changes"""
        self._node_callbacks.append(callback)

    def update_simulation(self):
        """
        Update simulation state (call periodically).
        Updates battery levels, signal quality, online status, etc.
        """
        now = datetime.now()
        elapsed = (now - self._last_update).total_seconds() * self._simulation_speed
        self._last_update = now

        for node in self._nodes.values():
            # Slowly drain battery
            if node.battery_level > 0:
                drain = elapsed * 0.001  # ~0.1% per 100 seconds
                node.battery_level = max(0, node.battery_level - drain)

            # Fluctuate SNR
            node.snr += random.uniform(-0.5, 0.5)
            node.snr = max(-20.0, min(20.0, node.snr))

            # Fluctuate RSSI
            node.rssi += random.randint(-2, 2)
            node.rssi = max(-140, min(-40, node.rssi))

            # Random online/offline (rare)
            if random.random() < 0.001 * elapsed:
                node.is_online = not node.is_online

            # Update last heard for online nodes
            if node.is_online and random.random() < 0.1 * elapsed:
                node.last_heard = now


class RFSimulator:
    """
    RF propagation simulator for path analysis.

    Simulates realistic RF conditions including:
    - Free space path loss
    - Terrain effects
    - Atmospheric conditions
    - Antenna patterns
    """

    # LoRa frequency presets (MHz)
    FREQUENCIES = {
        "US915": 915.0,
        "EU868": 868.0,
        "AU915": 915.0,
        "JP920": 920.0,
        "CN470": 470.0,
    }

    # Terrain loss presets (dB)
    TERRAIN_PRESETS = {
        "clear_los": 0.0,      # Direct line of sight
        "light_foliage": 5.0,  # Some trees
        "heavy_foliage": 15.0, # Dense forest
        "suburban": 10.0,      # Buildings, moderate obstruction
        "urban": 25.0,         # Dense buildings
        "hilly": 8.0,          # Rolling terrain
        "mountainous": 20.0,   # Significant elevation changes
    }

    def __init__(self, frequency_mhz: float = 915.0):
        self.frequency_mhz = frequency_mhz
        self.tx_power_dbm = 20.0  # Default 100mW
        self.antenna_gain_dbi = 2.0  # Default antenna gain
        self.rx_sensitivity_dbm = -140.0  # Typical LoRa sensitivity

    def calculate_fspl(self, distance_km: float) -> float:
        """Calculate Free Space Path Loss in dB"""
        if distance_km <= 0:
            return 0.0
        # FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)
        # Simplified: FSPL = 20*log10(d_km) + 20*log10(f_MHz) + 32.45
        return 20 * math.log10(distance_km) + 20 * math.log10(self.frequency_mhz) + 32.45

    def calculate_fresnel_radius(self, distance_km: float, position: float = 0.5) -> float:
        """
        Calculate Fresnel zone radius at a point along the path.

        Args:
            distance_km: Total path distance
            position: Position along path (0.0 to 1.0, 0.5 = midpoint)

        Returns:
            Fresnel radius in meters at that position
        """
        if distance_km <= 0:
            return 0.0
        # Fresnel radius at midpoint: F1 = 17.3 * sqrt(d / 4f)
        # where d is in km, f is in GHz
        d1 = distance_km * position
        d2 = distance_km * (1 - position)
        f_ghz = self.frequency_mhz / 1000
        return 17.3 * math.sqrt((d1 * d2) / (distance_km * f_ghz))

    def calculate_earth_bulge(self, distance_km: float, position: float = 0.5) -> float:
        """
        Calculate Earth bulge (curvature effect) at a point.

        Args:
            distance_km: Total path distance
            position: Position along path (0.0 to 1.0)

        Returns:
            Earth bulge in meters
        """
        # h = d1 * d2 / (12.75 * k), where k is Earth radius factor (typically 4/3 for radio)
        d1 = distance_km * position
        d2 = distance_km * (1 - position)
        k = 4/3  # Standard radio propagation factor
        return (d1 * d2) / (12.75 * k)

    def simulate_path(
        self,
        distance_km: float,
        terrain: str = "clear_los",
        weather: str = "clear"
    ) -> RFSimulationResult:
        """
        Simulate an RF path between two points.

        Args:
            distance_km: Path distance in kilometers
            terrain: Terrain type (see TERRAIN_PRESETS)
            weather: Weather condition ("clear", "rain", "heavy_rain", "fog")

        Returns:
            RFSimulationResult with path analysis
        """
        # Base calculations
        fspl = self.calculate_fspl(distance_km)
        fresnel = self.calculate_fresnel_radius(distance_km)
        earth_bulge = self.calculate_earth_bulge(distance_km)

        # Terrain loss
        terrain_loss = self.TERRAIN_PRESETS.get(terrain, 0.0)

        # Weather/atmospheric loss
        weather_losses = {
            "clear": 0.0,
            "fog": 2.0,
            "rain": 3.0,
            "heavy_rain": 8.0,
        }
        atmos_loss = weather_losses.get(weather, 0.0)

        # Add some randomness for realism
        random_fade = random.uniform(-3.0, 3.0)

        # Total path loss
        total_loss = fspl + terrain_loss + atmos_loss + random_fade

        # Calculate received signal strength
        received_dbm = self.tx_power_dbm + (2 * self.antenna_gain_dbi) - total_loss

        # Estimate SNR (noise floor around -125 dBm for LoRa)
        noise_floor = -125.0
        estimated_snr = received_dbm - noise_floor

        # Determine link quality
        if received_dbm > self.rx_sensitivity_dbm + 20:
            quality = "Excellent"
        elif received_dbm > self.rx_sensitivity_dbm + 10:
            quality = "Good"
        elif received_dbm > self.rx_sensitivity_dbm + 5:
            quality = "Marginal"
        elif received_dbm > self.rx_sensitivity_dbm:
            quality = "Poor"
        else:
            quality = "No Link"

        return RFSimulationResult(
            distance_km=distance_km,
            fspl_db=fspl,
            fresnel_radius_m=fresnel,
            earth_bulge_m=earth_bulge,
            estimated_snr=estimated_snr,
            link_quality=quality,
            terrain_loss_db=terrain_loss,
            atmospheric_loss_db=atmos_loss,
            total_path_loss_db=total_loss,
        )

    def simulate_coverage(
        self,
        center_lat: float,
        center_lon: float,
        radius_km: float = 10.0,
        resolution: int = 20,
        terrain: str = "suburban"
    ) -> List[Dict]:
        """
        Simulate coverage area from a central point.

        Returns list of points with signal strength estimates.
        """
        points = []

        for i in range(resolution):
            for j in range(resolution):
                # Calculate offset
                lat_offset = (i - resolution/2) * (radius_km * 2 / resolution) / 111.0
                lon_offset = (j - resolution/2) * (radius_km * 2 / resolution) / (111.0 * math.cos(math.radians(center_lat)))

                point_lat = center_lat + lat_offset
                point_lon = center_lon + lon_offset

                # Calculate distance from center
                distance = math.sqrt(lat_offset**2 + lon_offset**2) * 111.0

                if distance > 0:
                    result = self.simulate_path(distance, terrain=terrain)
                    points.append({
                        "lat": point_lat,
                        "lon": point_lon,
                        "distance_km": distance,
                        "signal_quality": result.link_quality,
                        "snr": result.estimated_snr,
                    })

        return points


# Global simulator instances
_mesh_simulator: Optional[MeshSimulator] = None
_rf_simulator: Optional[RFSimulator] = None


def get_mesh_simulator() -> MeshSimulator:
    """Get or create the global mesh simulator instance"""
    global _mesh_simulator
    if _mesh_simulator is None:
        _mesh_simulator = MeshSimulator()
    return _mesh_simulator


def get_rf_simulator() -> RFSimulator:
    """Get or create the global RF simulator instance"""
    global _rf_simulator
    if _rf_simulator is None:
        _rf_simulator = RFSimulator()
    return _rf_simulator


def is_simulation_enabled() -> bool:
    """Check if simulation mode is enabled"""
    return _mesh_simulator is not None and _mesh_simulator.is_enabled
