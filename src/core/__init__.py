"""
MeshForge Core Module

Provides shared functionality across all MeshForge editions:
- PRO: Full-featured desktop application
- Amateur: Ham radio focused edition
- .io: Lightweight web-based with plugins

This module contains:
- Edition detection and feature gating
- Plugin system architecture
- Shared utilities and base classes
"""

from .edition import Edition, detect_edition, has_feature, get_edition_features
from .plugin_base import Plugin, PluginManager, PluginManifest

__all__ = [
    'Edition',
    'detect_edition',
    'has_feature',
    'get_edition_features',
    'Plugin',
    'PluginManager',
    'PluginManifest',
]

__version__ = '1.0.0'
