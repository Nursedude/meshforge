"""Backward-compatibility shim. Canonical location: mapping._map_proxy_routes"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('mapping._map_proxy_routes')
_sys.modules[__name__] = _real
