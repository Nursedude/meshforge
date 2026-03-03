"""Backward-compatibility shim. Canonical location: mapping.coverage_map"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('mapping.coverage_map')
_sys.modules[__name__] = _real
