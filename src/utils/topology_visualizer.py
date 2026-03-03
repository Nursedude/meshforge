"""Backward-compatibility shim. Canonical location: mapping.topology_visualizer"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('mapping.topology_visualizer')
_sys.modules[__name__] = _real
