"""Backward-compatibility shim. Canonical location: core.rf.preset_impact"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.preset_impact')
_sys.modules[__name__] = _real
