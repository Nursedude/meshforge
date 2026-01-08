"""
MeshChat Plugin for MeshForge

HTTP API integration with Reticulum MeshChat for enhanced LXMF messaging.
MeshChat runs as an external service - MeshForge connects via HTTP/WebSocket.

Repository: https://github.com/liamcottle/reticulum-meshchat
"""

from .client import MeshChatClient, MeshChatError
from .service import MeshChatService, ServiceStatus, ServiceState

__all__ = ['MeshChatClient', 'MeshChatError', 'MeshChatService', 'ServiceStatus', 'ServiceState']
__version__ = '1.0.0'
