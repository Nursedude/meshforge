"""Traffic Classifier screens say they cannot see the gateway's classifier
(live-truth pass 2026-09-25): they built a fresh, empty classifier and printed
"No routing decisions recorded yet" on every box — gateway boxes included."""
import contextlib
import io
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "..", "src"), os.path.join(HERE, "..", "src", "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from handler_test_utils import FakeDialog, make_handler_context  # noqa: E402
import handlers.classifier as hc  # noqa: E402


@pytest.mark.parametrize("view", ["_show_routing_stats", "_show_notification_stats",
                                  "_show_recent_receipts", "_show_bounced_items"])
def test_empty_local_classifier_is_not_reported_as_the_gateways(monkeypatch, view):
    empty = SimpleNamespace(get_stats=lambda: {"total": 0}, get_receipts=lambda *a, **k: [],
                            get_recent_receipts=lambda *a, **k: [], get_bounced=lambda *a, **k: [],
                            receipts=[], bounced=[], get_bounced_items=lambda *a, **k: [], bouncer=None)
    monkeypatch.setattr(hc, "create_routing_system", lambda *a, **k: empty)
    monkeypatch.setattr(hc, "create_notification_system", lambda *a, **k: empty)
    monkeypatch.setattr(hc, "_HAS_CLASSIFIER", True)
    monkeypatch.setattr(hc, "clear_screen", lambda: None)
    h = hc.ClassifierHandler()
    h.set_context(make_handler_context(dialog=FakeDialog()))
    h.ctx.wait_for_enter = lambda *a, **k: None
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        getattr(h, view)()
    text = out.getvalue()
    assert "UNKNOWN from here" in text and "Dashboard › Delivery" in text
    assert "recorded yet" not in text and "classified yet" not in text
