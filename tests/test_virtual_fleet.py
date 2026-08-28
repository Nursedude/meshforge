"""virtual_fleet config invariants — the sandbox's isolation is TESTED, not trusted.

The virtual fleet's entire safety story is isolation by construction
(2026-08-09 tx-guard incident: a mock is not isolation when the code has
fallbacks). These tests pin the construction: loopback-only, vfleet-
namespaced, no AutoInterface, unique ports — and, guard-drill style, PLANT
each violation and require the checker to catch it.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lab.virtual_fleet import (  # noqa: E402
    NODES,
    config_violations,
    render_node_config,
)

BASE = 14200


class TestRenderedConfigsAreIsolated(unittest.TestCase):

    def test_every_node_renders_clean(self):
        for name in NODES:
            viols = config_violations(render_node_config(name, BASE))
            self.assertEqual(viols, [], f"{name}: {viols}")

    def test_no_autointerface_anywhere(self):
        for name in NODES:
            self.assertNotIn("AutoInterface", render_node_config(name, BASE))

    def test_instance_names_are_namespaced_and_unique(self):
        names = set()
        for name in NODES:
            cfg = render_node_config(name, BASE)
            line = next(l for l in cfg.splitlines()
                        if l.strip().startswith("instance_name"))
            value = line.split("=")[1].strip()
            self.assertTrue(value.startswith("vfleet-"), value)
            names.add(value)
        self.assertEqual(len(names), len(NODES), "instance names must be unique")

    def test_ports_are_unique_across_nodes(self):
        ports = []
        for name in NODES:
            for line in render_node_config(name, BASE).splitlines():
                s = line.strip()
                if s.startswith(("shared_instance_port", "instance_control_port")):
                    ports.append(int(s.split("=")[1]))
        self.assertEqual(len(ports), len(set(ports)), f"port collision: {ports}")

    def test_only_transport_enables_transport(self):
        for name in NODES:
            cfg = render_node_config(name, BASE)
            expected = "True" if name == "transport" else "False"
            self.assertIn(f"enable_transport = {expected}", cfg, name)

    def test_leaves_link_only_to_loopback_transport(self):
        for name in NODES:
            cfg = render_node_config(name, BASE)
            if name == "transport":
                self.assertIn("listen_ip = 127.0.0.1", cfg)
                self.assertIn(f"listen_port = {BASE}", cfg)
            else:
                self.assertIn("target_host = 127.0.0.1", cfg)
                self.assertIn(f"target_port = {BASE}", cfg)


class TestCheckerCatchesPlantedViolations(unittest.TestCase):
    """A checker that has only ever said 'clean' is not evidence it checks."""

    def test_planted_autointerface_is_caught(self):
        cfg = render_node_config("gw", BASE) + (
            "  [[Sneak]]\n    type = AutoInterface\n    interface_enabled = True\n"
        )
        self.assertTrue(any("AutoInterface" in v for v in config_violations(cfg)))

    def test_planted_wildcard_listener_is_caught(self):
        cfg = render_node_config("transport", BASE).replace(
            "listen_ip = 127.0.0.1", "listen_ip = 0.0.0.0")
        self.assertTrue(any("non-loopback listener" in v
                            for v in config_violations(cfg)))

    def test_planted_external_link_target_is_caught(self):
        cfg = render_node_config("gw", BASE).replace(
            "target_host = 127.0.0.1", "target_host = 192.0.2.7")
        self.assertTrue(any("non-loopback link target" in v
                            for v in config_violations(cfg)))

    def test_planted_unnamespaced_instance_is_caught(self):
        cfg = render_node_config("gw", BASE).replace(
            "instance_name = vfleet-gw", "instance_name = default")
        self.assertTrue(any("not vfleet-namespaced" in v
                            for v in config_violations(cfg)))


if __name__ == "__main__":
    unittest.main()
