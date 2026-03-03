"""Backward-compatibility shim. Canonical location: core.rf.antenna_patterns"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.antenna_patterns')
_sys.modules[__name__] = _real
