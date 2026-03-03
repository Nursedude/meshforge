"""Backward-compatibility shim. Canonical location: core.services._iptables_utils"""
import importlib as _importlib
import sys as _sys
_real = _importlib.import_module('core.services._iptables_utils')
_sys.modules[__name__] = _real
