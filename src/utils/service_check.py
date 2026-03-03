"""Backward-compatibility shim. Canonical location: core.services.service_check"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.services.service_check')
_sys.modules[__name__] = _real
