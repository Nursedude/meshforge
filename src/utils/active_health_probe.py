"""Backward-compatibility shim. Canonical location: core.services.active_health_probe"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.services.active_health_probe')
_sys.modules[__name__] = _real
