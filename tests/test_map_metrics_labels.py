"""QA maps audit 2026-07-05 — Prometheus label hygiene in map_metrics.

Two findings, both about the network-labeled gauges:
  F3: a federated peer's `network` string is untrusted; a hostile peer emitting
      one position-less node per fabricated network would explode /metrics
      cardinality. Unknown networks now bucket to "other".
  F6: a network that drops out of a cycle used to freeze its gauge at the last
      non-zero value forever (honest_failure_modes #2). It now gets .set(0).
"""
import pytest

from utils import map_metrics as m

pytestmark = pytest.mark.skipif(not m._HAS_PROM, reason="prometheus_client not installed")


def _gauge_value(gauge, **labels):
    return gauge.labels(**labels)._value.get()


class TestNetworkBucketing:
    def test_known_networks_pass_through(self):
        assert m._bucket_network("meshtastic") == "meshtastic"
        assert m._bucket_network("RNS") == "rns"  # case-insensitive
        assert m._bucket_network("aredn") == "aredn"

    def test_unknown_networks_bucket_to_other(self):
        assert m._bucket_network("net-000001") == "other"
        assert m._bucket_network(None) == "other"
        assert m._bucket_network("") == "other"

    def test_counts_aggregate_by_bucket(self):
        # Two hostile network names collapse into one "other" series.
        assert m._bucket_counts({"meshtastic": 3, "net-x": 1, "net-y": 2}) == {
            "meshtastic": 3, "other": 3,
        }


class TestGaugeZeroOut:
    def test_dropped_network_goes_to_zero(self):
        # Cycle 1: aredn present with 2.
        m.set_node_inventory(10, {"meshtastic": 5, "aredn": 2})
        assert _gauge_value(m.NODES_WITHOUT_POSITION, network="aredn") == 2
        # Cycle 2: aredn dropped out — must be explicitly zeroed, not frozen at 2.
        m.set_node_inventory(10, {"meshtastic": 3})
        assert _gauge_value(m.NODES_WITHOUT_POSITION, network="aredn") == 0
        assert _gauge_value(m.NODES_WITHOUT_POSITION, network="meshtastic") == 3

    def test_directory_dropped_network_goes_to_zero(self):
        m.set_directory_inventory(7, {"meshcore": 4, "rns": 3})
        assert _gauge_value(m.DIRECTORY_BY_NETWORK, network="rns") == 3
        m.set_directory_inventory(4, {"meshcore": 4})
        assert _gauge_value(m.DIRECTORY_BY_NETWORK, network="rns") == 0
