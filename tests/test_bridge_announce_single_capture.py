"""An RNS announce is captured ONCE (2026-09-26).

The sniffer's own announce handler (aspect filter None) sees every announce,
and the bridge's announce handler also stored one into the same sniffer — so
every lxmf.delivery announce was stored twice on both gateway boxes.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gateway import rns_bridge
from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin


def _run(hooks_installed):
    import types
    fake_rns = types.ModuleType("RNS")
    fake_rns.Transport = SimpleNamespace(has_path=lambda h: False, hops_to=lambda h: 0)
    sniffer = MagicMock(_running=True, _hooks_installed=hooks_installed)
    host = SimpleNamespace(node_tracker=MagicMock())
    with patch.dict("sys.modules", {"RNS": fake_rns}), \
         patch.object(rns_bridge, "HAS_RNS_SNIFFER", True), \
         patch.object(rns_bridge, "get_rns_sniffer", return_value=sniffer), \
         patch("gateway.bridge_rns_events_mixin.UnifiedNode"):
        BridgeRnsEventsMixin._on_rns_announce(host, b"\x01" * 16, None, b"")
    return sniffer


def test_bridge_does_not_duplicate_the_sniffers_own_capture():
    assert not _run(hooks_installed=True)._store_packet.called


def test_bridge_captures_when_the_sniffer_has_no_hooks():
    _run(hooks_installed=False)._store_packet.assert_called_once()
