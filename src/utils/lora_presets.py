"""Backward-compatibility shim. Canonical location: core.rf.lora_presets"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.rf.lora_presets')
_sys.modules[__name__] = _real
