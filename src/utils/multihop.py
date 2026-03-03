"""Backward-compatibility shim. Canonical location: core.rf.multihop"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.multihop')
_sys.modules[__name__] = _real
