"""Backward-compatibility shim. Canonical location: mapping.topology_snapshot"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('mapping.topology_snapshot')
_sys.modules[__name__] = _real
