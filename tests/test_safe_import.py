"""
Tests for utils/safe_import.py

Tests cover:
- Successful imports (module-level, attribute-level)
- Failed imports (missing module returns None + False)
- Multiple attribute imports
- Relative imports with package parameter
- Edge cases (missing attributes, empty names)

Run with: pytest tests/test_safe_import.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.safe_import import safe_import


class TestSafeImportSuccess:
    """Test successful import cases."""

    def test_import_whole_module(self):
        mod, ok = safe_import('json')
        assert ok is True
        assert mod is not None
        assert hasattr(mod, 'dumps')

    def test_import_single_attribute(self):
        dumps, ok = safe_import('json', 'dumps')
        assert ok is True
        assert callable(dumps)

    def test_import_multiple_attributes(self):
        dumps, loads, ok = safe_import('json', 'dumps', 'loads')
        assert ok is True
        assert callable(dumps)
        assert callable(loads)

    def test_import_class(self):
        Path, ok = safe_import('pathlib', 'Path')
        assert ok is True
        assert Path is not None
        assert Path('/tmp').exists() or True  # Just checking it's the real Path


class TestSafeImportFailure:
    """Test failed import cases."""

    def test_missing_module_whole(self):
        mod, ok = safe_import('nonexistent_module_xyz_12345')
        assert ok is False
        assert mod is None

    def test_missing_module_single_attr(self):
        val, ok = safe_import('nonexistent_module_xyz_12345', 'SomeClass')
        assert ok is False
        assert val is None

    def test_missing_module_multiple_attrs(self):
        a, b, c, ok = safe_import(
            'nonexistent_module_xyz_12345', 'A', 'B', 'C'
        )
        assert ok is False
        assert a is None
        assert b is None
        assert c is None


class TestSafeImportEdgeCases:
    """Test edge cases and special behaviors."""

    def test_missing_attribute_is_a_failed_import(self):
        """Module exists but the name is neither attr nor submodule: the
        flag must be False — (None, True) was the honest-failure-modes lie
        that broke every cold `safe_import('pubsub', 'pub')` call site."""
        val, ok = safe_import('json', 'nonexistent_function_xyz')
        assert ok is False
        assert val is None

    def test_missing_attr_fails_whole_call(self):
        """One missing name fails the call: all attrs None, flag False —
        callers take their designed fallback instead of crashing on the
        one None in an otherwise-populated tuple."""
        dumps, missing, ok = safe_import('json', 'dumps', 'nonexistent_xyz')
        assert ok is False
        assert dumps is None
        assert missing is None

    def test_submodule_fallback_from_import_semantics(self, tmp_path):
        """`safe_import('pkg', 'sub')` must import pkg.sub as a submodule
        when the package doesn't expose it as an attribute — matching
        `from pkg import sub` (the pubsub.pub cold-interpreter case)."""
        import sys
        pkg = tmp_path / "sipkg_txyz"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "subm.py").write_text("MARKER = 42\n")
        sys.path.insert(0, str(tmp_path))
        try:
            subm, ok = safe_import('sipkg_txyz', 'subm')
            assert ok is True
            assert subm is not None
            assert subm.MARKER == 42
        finally:
            sys.path.remove(str(tmp_path))
            for m in list(sys.modules):
                if m.startswith('sipkg_txyz'):
                    del sys.modules[m]

    def test_submodule_that_raises_on_import_degrades_not_crashes(self, tmp_path):
        """2026-07-21 review: unlike getattr, the submodule fallback EXECUTES
        module code — a broken C-extension/driver init raising a
        non-ImportError must degrade to (None, False), never escape the
        'safe' helper."""
        import sys
        pkg = tmp_path / "sipkg_boom"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "dev.py").write_text("raise RuntimeError('driver init failed')\n")
        sys.path.insert(0, str(tmp_path))
        try:
            dev, ok = safe_import('sipkg_boom', 'dev')
            assert ok is False
            assert dev is None
        finally:
            sys.path.remove(str(tmp_path))
            for m in list(sys.modules):
                if m.startswith('sipkg_boom'):
                    del sys.modules[m]

    def test_pubsub_pattern_cold_contract(self):
        """The documented `pub, ok = safe_import('pubsub', 'pub')` pattern:
        with pubsub installed, ok=True must come with a usable module (the
        submodule fallback); without it, (None, False). Never (None, True)."""
        pub, ok = safe_import('pubsub', 'pub')
        if ok:
            assert pub is not None
            assert hasattr(pub, 'getDefaultTopicMgr')
        else:
            assert pub is None

    def test_tuple_length_matches_names(self):
        """Return tuple length = len(names) + 1 (for the flag)."""
        result = safe_import('json', 'dumps', 'loads', 'JSONDecodeError')
        assert len(result) == 4  # 3 names + 1 flag

    def test_no_names_returns_pair(self):
        """With no attribute names, returns (module, flag) pair."""
        result = safe_import('json')
        assert len(result) == 2

    def test_relative_import_with_package(self):
        """Relative import with package parameter."""
        # Import from gateway package (relative)
        tracker, ok = safe_import('.node_models', 'UnifiedNode', package='gateway')
        # This may or may not work depending on sys.path, but should not raise
        assert isinstance(ok, bool)

    def test_failed_relative_import(self):
        """Relative import of nonexistent module returns None + False."""
        val, ok = safe_import('.nonexistent_xyz', 'Foo', package='gateway')
        assert ok is False
        assert val is None
