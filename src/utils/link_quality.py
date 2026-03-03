"""Backward-compatibility shim. Canonical location: core.rf.link_quality"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.link_quality')
_sys.modules[__name__] = _real
