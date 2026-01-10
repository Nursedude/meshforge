"""
RNS Panel Sub-modules

Extracted from the main RNS panel for maintainability.
Each module provides a mixin class that adds functionality to RNSPanel.

Usage:
    from gtk_ui.panels.rns import MeshChatMixin, NomadNetMixin, ...

    class RNSPanel(MeshChatMixin, NomadNetMixin, ..., Gtk.Box):
        pass

Note: The main rns.py still contains the full implementation for backwards
compatibility. These mixins can be used gradually to refactor the panel.
"""

# Lazy imports to avoid circular dependencies
def __getattr__(name):
    if name == 'MeshChatMixin':
        from .meshchat import MeshChatMixin
        return MeshChatMixin
    elif name == 'NomadNetMixin':
        from .nomadnet import NomadNetMixin
        return NomadNetMixin
    elif name == 'RNodeMixin':
        from .rnode import RNodeMixin
        return RNodeMixin
    elif name == 'GatewayMixin':
        from .gateway import GatewayMixin
        return GatewayMixin
    elif name == 'ComponentsMixin':
        from .components import ComponentsMixin
        return ComponentsMixin
    elif name == 'ConfigMixin':
        from .config import ConfigMixin
        return ConfigMixin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'MeshChatMixin',
    'NomadNetMixin',
    'RNodeMixin',
    'GatewayMixin',
    'ComponentsMixin',
    'ConfigMixin',
]
