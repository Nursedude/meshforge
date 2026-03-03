"""Backward-compatibility shim. Canonical location: core.rf.space_weather"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.space_weather')
_sys.modules[__name__] = _real
