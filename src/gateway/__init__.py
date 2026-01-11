"""
MeshForge Gateway Module
Bridges Reticulum Network Stack (RNS) and Meshtastic networks

Supports two bridge modes:
- message_bridge: Translates messages between RNS/LXMF and Meshtastic
- rns_transport: RNS uses Meshtastic as network transport layer (RNS_Over_Meshtastic)
"""

from .rns_bridge import RNSMeshtasticBridge
from .node_tracker import UnifiedNodeTracker
from .config import GatewayConfig, RNSOverMeshtasticConfig

__all__ = [
    'RNSMeshtasticBridge',
    'UnifiedNodeTracker',
    'GatewayConfig',
    'RNSOverMeshtasticConfig',
]
