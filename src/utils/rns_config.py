"""Backward-compatibility shim. Canonical location: core.services.rns_config"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.services.rns_config')
_sys.modules[__name__] = _real
