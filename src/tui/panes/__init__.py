"""TUI Panes Module

Modular structure for TUI panes.
DashboardPane is fully extracted; others remain in app.py for now.

Structure:
- dashboard.py - Dashboard status view (EXTRACTED)
- service.py   - Service management (stub)
- config.py    - Config file management (stub)
- cli.py       - Meshtastic CLI (stub)
- tools.py     - System tools (stub)
"""

from .dashboard import DashboardPane

__all__ = [
    'DashboardPane',
]
