"""
MeshForge Core Module

This module contains:
- Plugin system architecture
- Shared utilities and base classes

The edition system (PRO / Amateur / .io feature sets, ``core/edition.py``) was
DELETED 2026-10-22 (BIC-5 credibility sweep): nothing consumed it, and it
advertised editions and features (APRS gateway, Winlink, a plugin
marketplace) that were never built. Feature gating is the deployment
profile's job (``utils/deployment_profiles.py``).
"""

from .plugin_base import Plugin, PluginManager, PluginManifest

__all__ = [
    'Plugin',
    'PluginManager',
    'PluginManifest',
]

__version__ = '1.0.0'
