"""Backward-compatibility shim. Canonical location: mapping.data_collector"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('mapping.data_collector')
_sys.modules[__name__] = _real
