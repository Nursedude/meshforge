"""GTK4 UI Panels"""

from .dashboard import DashboardPanel
from .service import ServicePanel
from .install import InstallPanel
from .config import ConfigPanel
from .cli import CLIPanel
from .hardware import HardwarePanel
from .rns import RNSPanel
from .map import MapPanel

__all__ = [
    'DashboardPanel',
    'ServicePanel',
    'InstallPanel',
    'ConfigPanel',
    'CLIPanel',
    'HardwarePanel',
    'RNSPanel',
    'MapPanel'
]
