"""Backward-compatibility shim. Canonical location: core.services.connection_manager"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.services.connection_manager')
_sys.modules[__name__] = _real
