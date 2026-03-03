"""Backward-compatibility shim. Canonical location: core.rf.calculator"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.calculator')
_sys.modules[__name__] = _real
