"""
MeshForge Gateway Module
Bridges Reticulum Network Stack (RNS) and Meshtastic networks

Supports three bridge modes:
- message_bridge: Translates messages between RNS/LXMF and Meshtastic
- rns_transport: RNS uses Meshtastic as network transport layer (RNS_Over_Meshtastic)
- mesh_bridge: Bridges two Meshtastic networks with different LoRa presets
"""

from .rns_bridge import RNSMeshtasticBridge
from .node_tracker import UnifiedNodeTracker
from .config import (
    GatewayConfig,
    RNSOverMeshtasticConfig,
    MeshtasticConfig,
    MeshtasticBridgeConfig,
)
from .rns_transport import (
    RNSMeshtasticTransport,
    RNSMeshtasticInterface,
    TransportStats,
    create_rns_transport,
)
from .mesh_bridge import (
    MeshtasticPresetBridge,
    BridgedMeshMessage,
    create_mesh_bridge,
)
from .meshtastic_protobuf_client import (
    MeshtasticProtobufClient,
    get_protobuf_client,
    reset_protobuf_client,
    send_text_direct,
)
from .meshtastic_protobuf_ops import (
    ProtobufEventType,
    ProtobufTransportConfig,
    DeviceConfigSnapshot,
    ModuleConfigSnapshot,
    NeighborEntry,
    NeighborReport,
    DeviceMetadataResult,
    TracerouteResult,
)

__all__ = [
    # RNS-Meshtastic bridge
    'RNSMeshtasticBridge',
    'UnifiedNodeTracker',
    # Configuration
    'GatewayConfig',
    'RNSOverMeshtasticConfig',
    'MeshtasticConfig',
    'MeshtasticBridgeConfig',
    # RNS Transport
    'RNSMeshtasticTransport',
    'RNSMeshtasticInterface',
    'TransportStats',
    'create_rns_transport',
    # Mesh preset bridge
    'MeshtasticPresetBridge',
    'BridgedMeshMessage',
    'create_mesh_bridge',
    # Protobuf-over-HTTP client
    'MeshtasticProtobufClient',
    'get_protobuf_client',
    'reset_protobuf_client',
    'send_text_direct',
    'ProtobufEventType',
    'ProtobufTransportConfig',
    'DeviceConfigSnapshot',
    'ModuleConfigSnapshot',
    'NeighborEntry',
    'NeighborReport',
    'DeviceMetadataResult',
    'TracerouteResult',
]
