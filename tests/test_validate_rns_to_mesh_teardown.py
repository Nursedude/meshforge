"""Teardown ordering in scripts/validate_rns_to_mesh.py.

Pins the three defects found by USING the script on 2026-09-17 (its own
output and exit code were the evidence — none of this is reachable from a
code read):

1. It ran inside ``with tempfile.TemporaryDirectory(...)`` and ``return``ed
   from the body, so the tree was deleted while RNS's daemon threads were
   live. The next path-response announce raised ``FileNotFoundError`` from
   ``Thread-N (job)`` on EVERY run — after a successful send, so the script
   still exited 0 and its exit code could not see it.
   ⚠️ The traceback's path (`.../lxmf/lxmf/ratchets/`) reads like a directory
   that was never created, and the first diagnosis said exactly that. Wrong:
   ``LXMRouter.register_delivery_identity`` creates it. It existed and was
   then deleted under a running thread. Read the LIFETIME, not the path.
2. The first cut of the fix called ``RNS.Reticulum.exit_handler()``, which
   ends in ``RNS._detach_stdout()`` — it rebinds stdout/stderr to os.devnull
   WITHOUT flushing. With output redirected (block-buffered) that discarded
   every line the script had printed; a successful run produced an empty log.
3. ``sys.exit(main())`` never reached the shell: RNS's atexit handler ends in
   ``RNS.exit()`` -> ``os._exit(0)``, so the documented exit codes were
   discarded. Measured on the pre-change script: "no path to <gateway>" —
   documented as exit 5 — arrived as **0**. A cron gating on ``$?`` would
   read total failure as success.

The live drills that verified the fix (exit 2 / 5 / 0, zero tracebacks, zero
leaked temp dirs over repeated runs) cannot run in CI — they need a live
rnsd and a reachable gateway. These pin the ordering contract instead.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_rns_to_mesh.py"
_spec = importlib.util.spec_from_file_location("validate_rns_to_mesh", _SCRIPT)
vrm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vrm)


@pytest.fixture
def tmpdir_path(tmp_path):
    d = tmp_path / "configdir"
    (d / "lxmf").mkdir(parents=True)
    (d / "lxmf" / "marker").write_text("x")
    return d


class TestTeardownOrdering:

    def test_flushes_streams_before_anything_can_swap_them(self, tmpdir_path):
        """Defect 2. The flush must happen FIRST — everything after it can
        rebind sys.stdout, and an unflushed buffer that gets swapped out is
        discarded silently, taking the script's whole verdict with it."""
        order = []
        RNS = MagicMock()
        RNS.Transport.detach_interfaces.side_effect = lambda: order.append("detach")
        router = MagicMock()
        router.exit_handler.side_effect = lambda: order.append("lxmf_exit")

        class _Stream:
            def flush(self):
                order.append("flush")

        with patch.object(sys, "stdout", _Stream()), \
             patch.object(sys, "stderr", _Stream()):
            vrm._teardown(RNS, MagicMock(), router, tmpdir_path)

        assert order[0] == "flush" and order[1] == "flush", (
            f"streams were not flushed before teardown: {order}")

    def test_uses_detach_interfaces_not_the_stdout_eating_exit_handler(
            self, tmpdir_path):
        """Defect 2, the specific call. RNS.Reticulum.exit_handler() ends in
        _detach_stdout(); we want the network quiet, not the streams gone."""
        RNS = MagicMock()
        vrm._teardown(RNS, MagicMock(), MagicMock(), tmpdir_path)
        RNS.Transport.detach_interfaces.assert_called_once()
        RNS.Reticulum.exit_handler.assert_not_called()

    def test_removes_the_configdir(self, tmpdir_path):
        """Defect 1's other half: the tree goes, but only AFTER teardown."""
        assert tmpdir_path.exists()
        vrm._teardown(MagicMock(), MagicMock(), MagicMock(), tmpdir_path)
        assert not tmpdir_path.exists()

    def test_skips_rns_teardown_when_reticulum_never_came_up(self, tmpdir_path):
        """On the init-failure path there is no Transport to detach, and
        warning about our own failure would be noise."""
        RNS = MagicMock()
        vrm._teardown(RNS, None, None, tmpdir_path)
        RNS.Transport.detach_interfaces.assert_not_called()
        assert not tmpdir_path.exists()

    def test_teardown_never_raises_and_still_removes_the_tree(self, tmpdir_path):
        """Teardown must not turn a successful validation into a failure —
        so every step is best-effort and the removal still happens."""
        RNS = MagicMock()
        RNS.Transport.detach_interfaces.side_effect = RuntimeError("boom")
        router = MagicMock()
        router.exit_handler.side_effect = RuntimeError("bang")
        vrm._teardown(RNS, MagicMock(), router, tmpdir_path)
        assert not tmpdir_path.exists()


class TestExitCodeIsAuthoritative:

    def test_entrypoint_uses_os_exit_not_sys_exit(self):
        """Defect 3. sys.exit() raises SystemExit, which RNS's atexit handler
        discards on its way to os._exit(0) — so the documented exit codes
        never reached the caller. Pinned as source because the behaviour it
        guards needs a live rnsd to exercise.
        """
        src = _SCRIPT.read_text()
        tail = src.split('if __name__ == "__main__":')[1]
        assert "os._exit(_code)" in tail, (
            "entrypoint must take the exit itself; sys.exit()'s code is "
            "discarded by RNS's atexit handler")
        assert "sys.exit(main())" not in tail
        # and it must flush first — os._exit does not
        assert tail.index("flush") < tail.index("os._exit(_code)")
