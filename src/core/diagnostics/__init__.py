"""
Unified Diagnostic Engine for MeshForge

This module provides a single source of truth for ALL diagnostics.
CLI, GTK, and Web all consume this engine.

Usage:
    from core.diagnostics import DiagnosticEngine, CheckCategory

    engine = DiagnosticEngine.get_instance()
    results = engine.run_all()
    # or
    results = engine.run_category(CheckCategory.MESHTASTIC)
"""

from .models import (
    CheckStatus,
    HealthStatus,
    EventSeverity,
    CheckCategory,
    CheckResult,
    SubsystemHealth,
    DiagnosticEvent,
    DiagnosticReport,
)
from .engine import DiagnosticEngine

__all__ = [
    'DiagnosticEngine',
    'CheckStatus',
    'HealthStatus',
    'EventSeverity',
    'CheckCategory',
    'CheckResult',
    'SubsystemHealth',
    'DiagnosticEvent',
    'DiagnosticReport',
]
