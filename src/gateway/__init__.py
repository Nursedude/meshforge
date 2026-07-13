"""
MeshForge Gateway Module
Bridges Reticulum Network Stack (RNS) and Meshtastic networks

Supports three bridge modes:
- message_bridge: Translates messages between RNS/LXMF and Meshtastic
- rns_transport: RNS uses Meshtastic as network transport layer (RNS_Over_Meshtastic)
- mesh_bridge: Bridges two Meshtastic networks with different LoRa presets

Exports resolve lazily (PEP 562): eager imports here pulled the full
RNS/LXMF/meshtastic bridge stack (~400 ms, 600+ modules) into EVERY consumer
of ANY gateway submodule — e.g. the TUI importing gateway.circuit_breaker paid
for rns_bridge at startup. `from gateway import RNSMeshtasticBridge` still
works; the submodule loads on first attribute access instead of package import.
"""

from importlib import import_module

# Public symbol -> defining submodule. Keep in sync with __all__.
_LAZY_EXPORTS = {
    # Submodules (the old eager imports bound these as package attributes;
    # `import gateway; gateway.rns_bridge.X` must keep working)
    'base_handler': '.base_handler',
    'rns_bridge': '.rns_bridge',
    'node_tracker': '.node_tracker',
    'config': '.config',
    'rns_transport': '.rns_transport',
    'mesh_bridge': '.mesh_bridge',
    'meshtastic_protobuf_client': '.meshtastic_protobuf_client',
    'meshtastic_protobuf_ops': '.meshtastic_protobuf_ops',
    # Base handler ABC
    'BaseMessageHandler': '.base_handler',
    # RNS-Meshtastic bridge
    'RNSMeshtasticBridge': '.rns_bridge',
    'UnifiedNodeTracker': '.node_tracker',
    # Configuration
    'GatewayConfig': '.config',
    'RNSOverMeshtasticConfig': '.config',
    'MeshtasticConfig': '.config',
    'MeshtasticBridgeConfig': '.config',
    # RNS Transport
    'RNSMeshtasticTransport': '.rns_transport',
    'RNSMeshtasticInterface': '.rns_transport',
    'TransportStats': '.rns_transport',
    'create_rns_transport': '.rns_transport',
    # Mesh preset bridge
    'MeshtasticPresetBridge': '.mesh_bridge',
    'BridgedMeshMessage': '.mesh_bridge',
    'create_mesh_bridge': '.mesh_bridge',
    # Protobuf-over-HTTP client
    'MeshtasticProtobufClient': '.meshtastic_protobuf_client',
    'get_protobuf_client': '.meshtastic_protobuf_client',
    'reset_protobuf_client': '.meshtastic_protobuf_client',
    'send_text_direct': '.meshtastic_protobuf_client',
    'ProtobufEventType': '.meshtastic_protobuf_ops',
    'ProtobufTransportConfig': '.meshtastic_protobuf_ops',
    'DeviceConfigSnapshot': '.meshtastic_protobuf_ops',
    'ModuleConfigSnapshot': '.meshtastic_protobuf_ops',
    'NeighborEntry': '.meshtastic_protobuf_ops',
    'NeighborReport': '.meshtastic_protobuf_ops',
    'DeviceMetadataResult': '.meshtastic_protobuf_ops',
    'TracerouteResult': '.meshtastic_protobuf_ops',
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name):
    submodule = _LAZY_EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(submodule, __name__)
    value = module if submodule == '.' + name else getattr(module, name)
    globals()[name] = value  # cache: __getattr__ only fires on the first miss
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
