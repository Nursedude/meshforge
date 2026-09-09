"""Reader/writer artifact-path wiring — the MF side of a per-app divergence.

Moved out of ``test_mini_warmstart.py`` on 2026-09-09. That file is otherwise
entirely app-agnostic (every test pins ``path`` or ``tmp_path`` and touches no
adapter), and it is the only test coverage that exists for the BYTE-LOCKED
``warmstart.py`` — MeshAnchor carried that module with zero tests of its own,
the same shape as the ``test_calibration_ledger.py`` finding parity_check.py
documents. The single test below was the one thing blocking the twin: it
asserts MeshForge's adapter basenames, which MeshAnchor legitimately does not
share, so copying the file wholesale failed here by design rather than by bug.

With it lifted out, ``test_mini_warmstart.py`` byte-locks across both repos and
each app keeps its own adapter pins — MA's equivalent is this same filename in
that repo, and it is the stronger of the two (see its docstring: on MeshForge
the adapter's values coincide with the old hardcodes, so only the MA side can
catch a re-hardcode).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_default_paths_come_from_the_app_adapter(tmp_path, monkeypatch):
    """_default_paths() must equal the adapter's app_artifact_paths() — the
    same function the fleet preset writes through. Hardcoded basenames here
    are exactly how the MA twin's warmstart ended up reading paths its daemon
    never writes and reported "mini has not run here" beside a ticking daemon
    (2026-08-11). MF-side limit, stated honestly: on MeshForge the adapter's
    values coincide with the old hardcodes, so a re-hardcode would still pass
    HERE — the MA twin's test (test_mini_artifact_paths.py in that repo) is
    the one that catches it. This pins the wiring and the MF values."""
    monkeypatch.setenv("MINI_DUDEAI_HOME", str(tmp_path))
    from mini_dudeai import _util, warmstart
    brief, state = warmstart._default_paths()
    a_brief, a_state, _a_hist = _util.app_artifact_paths()
    assert (brief, state) == (a_brief, a_state)
    assert brief == os.path.join(str(tmp_path), "mini_dudeai_brief.md")
    assert state == os.path.join(str(tmp_path), "mini_dudeai_state.json")
