"""Backward-compatibility shim. Canonical location: mapping.gps_integration"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('mapping.gps_integration')
_sys.modules[__name__] = _real
