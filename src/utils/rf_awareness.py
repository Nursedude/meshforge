"""Backward-compatibility shim. Canonical location: core.rf.awareness"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.awareness')
_sys.modules[__name__] = _real
