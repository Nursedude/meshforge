"""Mtime-gated declaration re-read (2026-08-27).

Born the day the manager box went radio-off: the deployment.json service_override
took effect for probe_role_drift on its next tick, but the PAGING list
(services_expected_active) was computed once at startup — so service_inactive
kept paging a deliberately-stopped meshtasticd until a manual watchdog
restart. Two consumers of ONE declaration disagreeing for hours is the
honest_failure_modes #5 shape at runtime.

These tests pin the gate (pure, no daemon) and the loop's swap behavior
(one drilled tick with a touched declaration file).
"""

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.watchdog_retarget import DeclarationMtimeGate  # noqa: E402


class TestDeclarationMtimeGate(unittest.TestCase):

    def test_untouched_files_report_no_change(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "deployment.json"
            f.write_text("{}")
            gate = DeclarationMtimeGate([str(f)])
            self.assertIsNone(gate.changed())
            self.assertIsNone(gate.changed())

    def test_touch_reports_change_exactly_once(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "deployment.json"
            f.write_text("{}")
            gate = DeclarationMtimeGate([str(f)])
            os.utime(f, (time.time() + 5, time.time() + 5))
            self.assertEqual(gate.changed(), str(f))
            self.assertIsNone(gate.changed(), "change must not re-fire")

    def test_file_appearing_is_a_change(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "deployment.json"
            gate = DeclarationMtimeGate([str(f)])   # tracked while absent
            self.assertIsNone(gate.changed())
            f.write_text("{}")
            self.assertEqual(gate.changed(), str(f))

    def test_missing_path_entries_are_dropped_not_fatal(self):
        gate = DeclarationMtimeGate([None, ""])
        self.assertIsNone(gate.changed())


class TestRunLoopRetargets(unittest.TestCase):
    """Drill the loop: touch the declaration, next tick must use new lists."""

    def test_touched_declaration_swaps_expected_services(self):
        import tempfile

        from utils import watchdog_runner

        seen_lists = []

        def fake_probes(**kwargs):
            seen_lists.append(tuple(kwargs["services_expected_active"]))
            return []

        with tempfile.TemporaryDirectory() as tmp:
            decl = Path(tmp) / "deployment.json"
            decl.write_text("{}")
            out = Path(tmp) / "watchdog.json"
            stop = threading.Event()

            calls = {"n": 0}

            def retarget():
                calls["n"] += 1
                return (("rnsd.service",), ())

            def fake_gatepaths(_user):
                return str(decl)

            ticks = {"n": 0}
            orig_wait = stop.wait

            def wait_and_touch(timeout=None):
                ticks["n"] += 1
                if ticks["n"] == 1:
                    # after the first probe pass, move the declaration
                    os.utime(decl, (time.time() + 5, time.time() + 5))
                    return False    # keep looping for one more tick
                stop.set()
                return True

            stop.wait = wait_and_touch  # type: ignore[method-assign]

            with patch.object(watchdog_runner, "run_all_probes", fake_probes), \
                 patch.object(watchdog_runner, "decide_restarts",
                              lambda *a, **k: []), \
                 patch("utils.watchdog_probe_core.deployment_declaration_path",
                       fake_gatepaths), \
                 patch("utils.rns_tree_perms._read_rnsd_user",
                       lambda: "testuser"):
                watchdog_runner.run_loop(
                    output_path=out,
                    tick_s=0.01,
                    stop_event=stop,
                    services_expected_active=("meshtasticd.service",),
                    services_wedge_check=(),
                    retarget=retarget,
                )
            stop.wait = orig_wait  # type: ignore[method-assign]

        self.assertGreaterEqual(len(seen_lists), 2, seen_lists)
        self.assertEqual(seen_lists[0], ("meshtasticd.service",),
                         "first tick uses startup list")
        self.assertEqual(seen_lists[-1], ("rnsd.service",),
                         "tick after the declaration touch must use the "
                         "re-resolved list")
        self.assertEqual(calls["n"], 1, "retarget fires once per change")


if __name__ == "__main__":
    unittest.main()
