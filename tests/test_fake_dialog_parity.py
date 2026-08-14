"""FakeDialog ↔ DialogBackend parity — the master guardrail (Batch 2, 2026-08-14).

The harness must not certify a phantom API. Before this test existed the
test double carried a ``radiolist()`` the real backend never implemented —
a handler calling it would pass every test and AttributeError live (audit
T1). Any future backend primitive now forces a FakeDialog update in the
same commit, and vice versa.
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from backend import DialogBackend
from handler_test_utils import FakeDialog


def _public_methods(cls):
    """Public callable method names (properties and _-prefixed excluded)."""
    return {
        name
        for name, member in inspect.getmembers(cls)
        if callable(member) and not name.startswith('_')
    }


class TestFakeDialogParity:
    def test_method_sets_equal(self):
        real = _public_methods(DialogBackend)
        fake = _public_methods(FakeDialog)
        assert real == fake, (
            f"FakeDialog and DialogBackend drifted.\n"
            f"  backend-only (add to FakeDialog): {sorted(real - fake)}\n"
            f"  fake-only (phantom API — delete or implement in backend): "
            f"{sorted(fake - real)}"
        )

    def test_fake_accepts_backend_positional_shapes(self):
        """Every shared method's required positional args must bind on the fake.

        Catches signature drift like the old private stub's
        ``textbox(path)`` vs the backend's ``textbox(title, text)``.
        """
        for name in sorted(_public_methods(DialogBackend)):
            real_sig = inspect.signature(getattr(DialogBackend, name))
            fake_sig = inspect.signature(getattr(FakeDialog, name))
            required = [
                p for p in list(real_sig.parameters.values())[1:]  # skip self
                if p.default is inspect.Parameter.empty
                and p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                               inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            placeholders = [object()] * len(required)
            try:
                fake_sig.bind(object(), *placeholders)  # object() stands in for self
            except TypeError as e:
                raise AssertionError(
                    f"FakeDialog.{name} cannot accept DialogBackend.{name}'s "
                    f"required positional args {[p.name for p in required]}: {e}"
                ) from None

    def test_no_radiolist_anywhere(self):
        # The T1 phantom: radiolist existed only on the double. If radiolist
        # is ever wanted, implement it on DialogBackend first — this test
        # then fails on the fake until parity is restored, which is the point.
        assert not hasattr(DialogBackend, 'radiolist')
        assert not hasattr(FakeDialog, 'radiolist')
