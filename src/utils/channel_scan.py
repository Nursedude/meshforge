"""Backward-compatibility shim. Canonical location: core.rf.channel_scan"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.channel_scan')
_sys.modules[__name__] = _real
