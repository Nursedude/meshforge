"""Backward-compatibility shim. Canonical location: mapping._tile_cache"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('mapping._tile_cache')
_sys.modules[__name__] = _real
