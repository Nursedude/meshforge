"""Backward-compatibility shim. Canonical location: core.services.meshtastic_connection"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.services.meshtastic_connection')
_sys.modules[__name__] = _real
