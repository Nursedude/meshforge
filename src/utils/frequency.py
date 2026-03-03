"""Backward-compatibility shim. Canonical location: core.rf.frequency"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.frequency')
_sys.modules[__name__] = _real
