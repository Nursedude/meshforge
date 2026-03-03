"""Backward-compatibility shim. Canonical location: core.services.meshtastic_http"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.services.meshtastic_http')
_sys.modules[__name__] = _real
