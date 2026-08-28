"""_setup_lxmf retry-loop leak guards (moc3 2026-08-27: 806 threads).

The gateway's reconnect loop re-runs _setup_lxmf after a failed attempt.
LXMRouter() starts its job threads at construction and Transport keeps
every registered announce handler, so a retry loop that constructs fresh
ones each pass leaks until 'can't start new thread' — moc3's gateway hit
806 threads / load 182 on a Pi 3 after 4 days of retries. These tests pin
the reuse behavior: N setup calls, ONE router, ONE announce handler, ONE
delivery-identity registration.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gateway._rns_bridge_connection import RNSConnectionMixin  # noqa: E402


class _Host(RNSConnectionMixin):
    """Minimal host carrying the attributes _setup_lxmf touches."""

    def __init__(self):
        self._lxmf_router = None
        self._lxmf_source = None
        self._identity = None
        self._connected_rns = False
        self.config = MagicMock()
        self.config.rns.gateway_name = "TestGW"
        self.config.rns.propagation_node = ""
        self._notified = []

    def _notify_status(self, event):
        self._notified.append(event)

    def _on_lxmf_receive(self, message):
        pass

    def _on_rns_announce(self, *a):
        pass


def _run_setup_n_times(host, n, mock_rns, mock_lxmf):
    with patch("gateway._rns_bridge_connection.get_real_user_home") as home, \
         patch("gateway._rns_bridge_connection.assert_rns_tx_allowed"), \
         patch("gateway._rns_bridge_connection.quarantine_corrupt_ratchets"):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home.return_value = Path(tmp)
            for _ in range(n):
                host._setup_lxmf(mock_rns, mock_lxmf)


class TestSetupLxmfRetryLeak(unittest.TestCase):

    def _mocks(self):
        mock_rns = MagicMock()
        mock_lxmf = MagicMock()
        router = MagicMock()
        source = MagicMock()
        source.hash = b"\x00" * 16
        router.register_delivery_identity.return_value = source
        mock_lxmf.LXMRouter.return_value = router
        return mock_rns, mock_lxmf, router

    def test_retry_reuses_router(self):
        mock_rns, mock_lxmf, router = self._mocks()
        host = _Host()
        _run_setup_n_times(host, 3, mock_rns, mock_lxmf)
        self.assertEqual(mock_lxmf.LXMRouter.call_count, 1,
                         "each retry constructing a fresh LXMRouter leaks "
                         "its job threads — reuse the first one")

    def test_retry_registers_delivery_identity_once(self):
        mock_rns, mock_lxmf, router = self._mocks()
        host = _Host()
        _run_setup_n_times(host, 3, mock_rns, mock_lxmf)
        self.assertEqual(router.register_delivery_identity.call_count, 1)

    def test_retry_registers_announce_handler_once(self):
        mock_rns, mock_lxmf, router = self._mocks()
        host = _Host()
        _run_setup_n_times(host, 3, mock_rns, mock_lxmf)
        self.assertEqual(mock_rns.Transport.register_announce_handler.call_count, 1,
                         "Transport keeps every handler — duplicates re-process "
                         "every announce")

    def test_single_run_still_wires_everything(self):
        mock_rns, mock_lxmf, router = self._mocks()
        host = _Host()
        _run_setup_n_times(host, 1, mock_rns, mock_lxmf)
        self.assertTrue(host._connected_rns)
        self.assertIsNotNone(host._lxmf_source)
        router.register_delivery_callback.assert_called()
        router.announce.assert_called_once()


if __name__ == "__main__":
    unittest.main()
