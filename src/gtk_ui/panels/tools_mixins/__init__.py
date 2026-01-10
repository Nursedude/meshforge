"""
Tools Panel Mixins

Extracted functionality from the main tools panel for maintainability.
Each module provides a mixin class that adds functionality to ToolsPanel.

Usage:
    from gtk_ui.panels.tools_mixins import SystemMonitorMixin, NetworkToolsMixin, ...

    class ToolsPanel(SystemMonitorMixin, NetworkToolsMixin, ..., Gtk.Box):
        pass

Note: The main tools.py still contains the full implementation for backwards
compatibility. These mixins can be used gradually to refactor the panel.
"""

# Lazy imports to avoid circular dependencies
def __getattr__(name):
    if name == 'SystemMonitorMixin':
        from .system_monitor import SystemMonitorMixin
        return SystemMonitorMixin
    elif name == 'NetworkToolsMixin':
        from .network_tools import NetworkToolsMixin
        return NetworkToolsMixin
    elif name == 'NetworkDiagnosticsMixin':
        from .network_diagnostics import NetworkDiagnosticsMixin
        return NetworkDiagnosticsMixin
    elif name == 'RFToolsMixin':
        from .rf_tools import RFToolsMixin
        return RFToolsMixin
    elif name == 'HFPropagationMixin':
        from .hf_propagation import HFPropagationMixin
        return HFPropagationMixin
    elif name == 'SDRToolsMixin':
        from .sdr_tools import SDRToolsMixin
        return SDRToolsMixin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'SystemMonitorMixin',
    'NetworkToolsMixin',
    'NetworkDiagnosticsMixin',
    'RFToolsMixin',
    'HFPropagationMixin',
    'SDRToolsMixin',
]
