"""Backward-compatibility shim. Canonical location: core.services._service_lifecycle"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.services._service_lifecycle')
_sys.modules[__name__] = _real
