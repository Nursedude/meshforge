"""Backward-compatibility shim. Canonical location: mapping.tile_cache"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('mapping.tile_cache')
_sys.modules[__name__] = _real
