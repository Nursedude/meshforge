"""`--preset auto` must never resolve to a preset that cannot start.

2026-09-12, moc5's rebuild. The old rule was two lines:

    resolved = "meshforge_fleet" if hosts else "standalone"

`fleet_hosts` is a MANAGER-side artifact -- moc1, moc2, moc3, moc4 and kiai
carry none and run a pinned preset -- so a freshly installed member box
resolved to `standalone`, which raises without MINI_DUDEAI_NATS_SERVER, and
systemd crashlooped it forever (`status=1/FAILURE`, auto-restart). The unit
template that install_noc ships carries `--preset auto`, so this was latent on
every future install, not only the one box it was found on.
"""
import os
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai.daemon import _resolve_preset_name  # noqa: E402


class TestAutoPresetResolution(unittest.TestCase):

    def _resolve(self, hosts, env):
        with patch("mini_dudeai.rollup.resolve_fleet_hosts", return_value=hosts), \
             patch.dict(os.environ, env, clear=False):
            if "MINI_DUDEAI_NATS_SERVER" not in env:
                os.environ.pop("MINI_DUDEAI_NATS_SERVER", None)
            return _resolve_preset_name("auto")

    def test_declared_hosts_pick_the_fleet_preset(self):
        self.assertEqual(self._resolve(["moc1", "moc2"], {}), "meshforge_fleet")

    def test_no_hosts_but_nats_configured_picks_standalone(self):
        got = self._resolve([], {"MINI_DUDEAI_NATS_SERVER": "localhost:4222"})
        self.assertEqual(got, "standalone")

    def test_no_hosts_and_no_nats_refuses_the_unstartable_preset(self):
        """THE regression: this is the moc5 crashloop shape."""
        self.assertEqual(self._resolve([], {}), "meshforge_fleet")

    def test_explicit_preset_passes_through_untouched(self):
        """Deployed units that pin a preset must keep their exact behavior."""
        for name in ("meshforge_fleet", "standalone", "some.dotted.path"):
            self.assertEqual(_resolve_preset_name(name), name)


class TestTheFailureThisPrevents(unittest.TestCase):
    """Ground the guard in the real failure rather than in my description of
    it: standalone genuinely raises without a NATS server, so resolving to it
    on a box that has none is resolving to a certain crash."""

    def test_standalone_really_does_raise_without_nats(self):
        from mini_dudeai.presets import standalone
        env = dict(os.environ)
        env.pop("MINI_DUDEAI_NATS_SERVER", None)
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as ctx:
                standalone.build_engine()
        self.assertIn("MINI_DUDEAI_NATS_SERVER", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
