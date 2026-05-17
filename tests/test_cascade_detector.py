"""Tests for cascade detector + fingerprints catalog (Track 0C of the
we-have-a-cycle-jolly-wadler stability arc).

Covers:
  * Fingerprint catalog shape (frozen dataclasses, required fields)
  * rns_rpc_wedge probe — subprocess output parsing, both miss and hit
  * CascadeDetector hysteresis: 1 hit → suspected, 2 → escalated to fp.severity
  * Cleared-after-miss state reset
  * get_snapshot() / summary() public contract
  * No-flapping behavior when probes flicker miss/hit
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import cascade_fingerprints as cfp
from utils.cascade_detector import (
    _CONSECUTIVE_HITS_TO_ESCALATE,
    CascadeDetector,
)


# ── Catalog shape ─────────────────────────────────────────────────────────


class TestCatalogShape:
    def test_catalog_has_at_least_rns_rpc_wedge(self):
        names = [fp.name for fp in cfp.FINGERPRINTS]
        assert "rns_rpc_wedge" in names

    def test_get_by_name_returns_match(self):
        fp = cfp.get_fingerprint_by_name("rns_rpc_wedge")
        assert fp is not None
        assert fp.name == "rns_rpc_wedge"

    def test_get_by_name_returns_none_on_miss(self):
        assert cfp.get_fingerprint_by_name("does_not_exist") is None

    def test_fingerprint_is_frozen(self):
        fp = cfp.FINGERPRINTS[0]
        with pytest.raises((AttributeError, Exception)):
            fp.name = "mutated"  # frozen dataclass — must raise

    def test_all_fingerprints_coupled_to_is_tuple_of_strings(self):
        """`coupled_to: Tuple[str, ...]` — a missing trailing comma in
        `coupled_to=("string")` makes it a bare string, which
        `list(fp.coupled_to)` in cascade_detector then serializes as a
        list of individual characters. Caught 2026-05-17 from moc2's
        /fleet/cascade output ("n","e","x","t"," ","l","a","b",...).
        Pin the contract so every entry in the catalog is genuinely a
        tuple-of-strings, including future additions."""
        for fp in cfp.FINGERPRINTS:
            assert isinstance(fp.coupled_to, tuple), (
                f"{fp.name}.coupled_to is {type(fp.coupled_to).__name__}, "
                f"not tuple — likely missing trailing comma in the "
                f"definition (e.g. `coupled_to=(\"x\",)` not `(\"x\")`)"
            )
            assert all(isinstance(s, str) for s in fp.coupled_to), (
                f"{fp.name}.coupled_to must be tuple of str, got "
                f"{[type(s).__name__ for s in fp.coupled_to]}"
            )
            assert all(len(s) > 1 for s in fp.coupled_to), (
                f"{fp.name}.coupled_to has single-char entries — that's "
                f"the iterate-over-string regression. Got: {fp.coupled_to!r}"
            )


# ── rns_rpc_wedge probe ───────────────────────────────────────────────────


class TestProbeRnsRpcWedge:
    """Direct probe-behavior tests. The conftest pytest_configure sets
    `MESHFORGE_CASCADE_PROBE_DISABLED=1` so background daemon threads
    don't leak `subprocess.run` calls into other tests' patches; these
    tests are the explicit exception — they need the probe to actually
    run end-to-end so they delenv via this autouse fixture."""

    @pytest.fixture(autouse=True)
    def _enable_probe(self, monkeypatch):
        monkeypatch.delenv("MESHFORGE_CASCADE_PROBE_DISABLED", raising=False)

    def test_returns_none_when_ss_missing(self):
        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value=None):
            assert cfp.probe_rns_rpc_wedge() is None

    def test_returns_none_when_ss_timeout(self):
        def boom(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 2)
        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value="/usr/bin/ss"), \
             patch("utils.cascade_fingerprints.subprocess.run",
                   side_effect=boom):
            assert cfp.probe_rns_rpc_wedge() is None

    def test_returns_none_when_ss_returns_nonzero(self):
        result = MagicMock(returncode=1, stdout="", stderr="oops")
        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value="/usr/bin/ss"), \
             patch("utils.cascade_fingerprints.subprocess.run",
                   return_value=result):
            assert cfp.probe_rns_rpc_wedge() is None

    def test_returns_none_when_no_matching_lines(self):
        """Healthy: SYN-SENT sockets exist but none target rns RPC."""
        result = MagicMock(
            returncode=0,
            stdout=(
                "u_str  SYN-SENT   0 0  @other/socket   1234  *  5678\n"
                "u_str  SYN-SENT   0 0  /tmp/some.sock  9999  *  10000\n"
            ),
        )
        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value="/usr/bin/ss"), \
             patch("utils.cascade_fingerprints.subprocess.run",
                   return_value=result):
            assert cfp.probe_rns_rpc_wedge() is None

    def test_returns_hit_on_rns_rpc_syn_sent(self):
        """Pathological: at least one SYN-SENT targeting @rns/*/rpc."""
        stdout = (
            "u_str  SYN-SENT  0  0  *  1234  @rns/default/rpc  5678\n"
            "u_str  SYN-SENT  0  0  *  9999  @rns/default/rpc  10000\n"
        )
        result = MagicMock(returncode=0, stdout=stdout)
        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value="/usr/bin/ss"), \
             patch("utils.cascade_fingerprints.subprocess.run",
                   return_value=result):
            hit = cfp.probe_rns_rpc_wedge()
        assert hit is not None
        assert "SYN-SENT" in hit.evidence
        assert hit.metric["syn_sent_count"] == 2

    def test_matches_non_default_instance_name(self):
        """Some hosts use a custom instance_name (per ReticulumPaths.
        get_configured_instance_name). The probe must match @rns/<any>/rpc,
        not just @rns/default/rpc."""
        stdout = (
            "u_str  SYN-SENT  0  0  *  1234  @rns/custom name rns/rpc  5678\n"
        )
        result = MagicMock(returncode=0, stdout=stdout)
        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value="/usr/bin/ss"), \
             patch("utils.cascade_fingerprints.subprocess.run",
                   return_value=result):
            hit = cfp.probe_rns_rpc_wedge()
        assert hit is not None
        assert hit.metric["syn_sent_count"] == 1


# ── tracer_stale_fire probe ──────────────────────────────────────────────


class TestProbeTracerStaleFire:
    """Tracer stale-fire probe: stat newest tracer-*.json mtime; flag
    pre_fail when older than threshold. Open follow-up from
    `project_rnsd_rpc_listener_wedge` recurrence #3 (2026-05-16) — the
    operator-visible signal lag was 2.5 h via cross-fleet rollup; this
    fingerprint cuts that to one detector cadence after the threshold."""

    @pytest.fixture(autouse=True)
    def _enable_probe(self, monkeypatch):
        monkeypatch.delenv("MESHFORGE_CASCADE_PROBE_DISABLED", raising=False)

    @pytest.fixture
    def _state_dir(self, tmp_path, monkeypatch):
        sd = tmp_path / "meshforge" / "tracer"
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        return sd

    def test_returns_none_when_state_dir_missing(self, _state_dir):
        assert not _state_dir.exists()
        assert cfp.probe_tracer_stale_fire() is None

    def test_returns_none_when_state_dir_empty(self, _state_dir):
        _state_dir.mkdir(parents=True)
        assert cfp.probe_tracer_stale_fire() is None

    def test_returns_none_when_only_non_tracer_files(self, _state_dir):
        """A foreign file in the dir must not be mistaken for a tracer fire."""
        _state_dir.mkdir(parents=True)
        (_state_dir / "README.md").write_text("# notes")
        (_state_dir / "tracer-old.txt").write_text("not json")
        assert cfp.probe_tracer_stale_fire() is None

    def test_returns_none_when_newest_file_is_fresh(self, _state_dir):
        _state_dir.mkdir(parents=True)
        f = _state_dir / "tracer-1.json"
        f.write_text("{}")
        # mtime is now → way under threshold
        assert cfp.probe_tracer_stale_fire() is None

    def test_returns_hit_when_newest_file_older_than_threshold(self, _state_dir, monkeypatch):
        _state_dir.mkdir(parents=True)
        old = _state_dir / "tracer-stale.json"
        old.write_text("{}")
        # Backdate beyond default 1500s.
        import os as _os
        old_time = time.time() - 2000
        _os.utime(old, (old_time, old_time))
        hit = cfp.probe_tracer_stale_fire()
        assert hit is not None
        assert hit.metric["age_s"] >= 2000
        assert hit.metric["threshold_s"] == 1500
        assert hit.metric["newest_file"] == "tracer-stale.json"
        assert "tracer fire" in hit.evidence

    def test_uses_newest_mtime_when_multiple_files(self, _state_dir):
        """Several old fires + one recent one = clean. The probe must pick
        the newest, not the oldest, or it would constantly false-alarm
        on boxes with a backlog of historical fires."""
        _state_dir.mkdir(parents=True)
        import os as _os
        for i in range(3):
            f = _state_dir / f"tracer-old-{i}.json"
            f.write_text("{}")
            t = time.time() - 5000
            _os.utime(f, (t, t))
        # One fresh file
        (_state_dir / "tracer-fresh.json").write_text("{}")
        assert cfp.probe_tracer_stale_fire() is None

    def test_threshold_env_override(self, _state_dir, monkeypatch):
        _state_dir.mkdir(parents=True)
        f = _state_dir / "tracer-x.json"
        f.write_text("{}")
        import os as _os
        # 120s old
        t = time.time() - 120
        _os.utime(f, (t, t))
        # Default threshold 1500 → miss
        assert cfp.probe_tracer_stale_fire() is None
        # Override 60 → hit
        monkeypatch.setenv("MESHFORGE_CASCADE_TRACER_STALE_S", "60")
        hit = cfp.probe_tracer_stale_fire()
        assert hit is not None
        assert hit.metric["threshold_s"] == 60

    def test_invalid_env_falls_back_to_default(self, _state_dir, monkeypatch):
        """Garbage env value must not crash the probe or disable the alarm."""
        monkeypatch.setenv("MESHFORGE_CASCADE_TRACER_STALE_S", "not-a-number")
        _state_dir.mkdir(parents=True)
        f = _state_dir / "tracer-x.json"
        f.write_text("{}")
        import os as _os
        t = time.time() - 2000
        _os.utime(f, (t, t))
        hit = cfp.probe_tracer_stale_fire()
        assert hit is not None
        assert hit.metric["threshold_s"] == 1500

    def test_disabled_env_short_circuits(self, _state_dir, monkeypatch):
        """The test-isolation escape hatch must still apply."""
        _state_dir.mkdir(parents=True)
        f = _state_dir / "tracer-stale.json"
        f.write_text("{}")
        import os as _os
        t = time.time() - 9999
        _os.utime(f, (t, t))
        monkeypatch.setenv("MESHFORGE_CASCADE_PROBE_DISABLED", "1")
        assert cfp.probe_tracer_stale_fire() is None

    def test_fingerprint_is_registered_in_catalog(self):
        names = [fp.name for fp in cfp.FINGERPRINTS]
        assert "tracer_stale_fire" in names
        fp = cfp.get_fingerprint_by_name("tracer_stale_fire")
        assert fp is not None
        assert fp.severity == "pre_fail"
        assert fp.cadence_s == 60


# ── CascadeDetector hysteresis ───────────────────────────────────────────


def _scripted_probe(returns):
    """Return a probe callable that yields a scripted sequence of
    (ProbeHit | None) values across successive calls."""
    iterator = iter(returns)

    def _probe():
        try:
            return next(iterator)
        except StopIteration:
            return None
    return _probe


def _make_fp(probe, name="t_fp", severity="pre_fail", cadence_s=0):
    return cfp.Fingerprint(
        name=name, severity=severity, probe=probe, cadence_s=cadence_s,
        incident_refs=("test",), coupled_to=("test_consequence",),
    )


class TestHysteresis:
    def test_clean_when_probe_always_misses(self):
        fp = _make_fp(_scripted_probe([None, None, None]))
        det = CascadeDetector(fingerprints=[fp])
        det.evaluate_once()
        det.evaluate_once()
        snap = det.get_snapshot()
        assert fp.name in snap["clean"]
        assert snap["fingerprints"] == []

    def test_one_hit_yields_suspected_state(self):
        hit = cfp.ProbeHit(evidence="ev1", metric={"k": 1})
        fp = _make_fp(_scripted_probe([hit, None]))
        det = CascadeDetector(fingerprints=[fp])
        det.evaluate_once()
        snap = det.get_snapshot()
        assert len(snap["fingerprints"]) == 1
        entry = snap["fingerprints"][0]
        assert entry["state"] == "suspected"
        assert entry["consecutive_hits"] == 1
        assert entry["evidence"] == "ev1"
        assert entry["metric"] == {"k": 1}

    def test_two_consecutive_hits_escalate_to_severity(self):
        hit = cfp.ProbeHit(evidence="ev", metric={})
        fp = _make_fp(
            _scripted_probe([hit, hit, hit]),
            severity="pre_fail",
        )
        det = CascadeDetector(fingerprints=[fp])
        det.evaluate_once()
        det.evaluate_once()
        snap = det.get_snapshot()
        entry = snap["fingerprints"][0]
        assert entry["state"] == "pre_fail"
        assert entry["consecutive_hits"] == 2
        assert _CONSECUTIVE_HITS_TO_ESCALATE == 2  # contract lock

    def test_miss_after_escalation_resets_to_clean(self):
        hit = cfp.ProbeHit(evidence="ev", metric={})
        fp = _make_fp(_scripted_probe([hit, hit, None]))
        det = CascadeDetector(fingerprints=[fp])
        det.evaluate_once()  # suspected
        det.evaluate_once()  # pre_fail
        det.evaluate_once()  # cleared
        snap = det.get_snapshot()
        assert fp.name in snap["clean"]
        # Counter must reset, not silently linger.
        # (Probe to verify by triggering another single hit.)
        det = CascadeDetector(fingerprints=[_make_fp(
            _scripted_probe([hit, hit, None, hit]),
        )])
        det.evaluate_once()
        det.evaluate_once()
        det.evaluate_once()
        det.evaluate_once()  # single hit AFTER cleared
        snap = det.get_snapshot()
        # Counter is 1 again (not 3), so state is suspected (not pre_fail).
        assert snap["fingerprints"][0]["state"] == "suspected"
        assert snap["fingerprints"][0]["consecutive_hits"] == 1

    def test_probe_exception_treated_as_miss(self):
        def boom():
            raise RuntimeError("kaboom")
        fp = _make_fp(boom)
        det = CascadeDetector(fingerprints=[fp])
        det.evaluate_once()  # must NOT raise
        snap = det.get_snapshot()
        assert fp.name in snap["clean"]

    def test_flicker_never_escalates(self):
        """Operator's biggest fear: false-positive alarms. A flickering
        probe (hit/miss/hit/miss) must never reach pre_fail."""
        hit = cfp.ProbeHit(evidence="ev", metric={})
        fp = _make_fp(_scripted_probe([hit, None, hit, None, hit, None]))
        det = CascadeDetector(fingerprints=[fp])
        for _ in range(6):
            det.evaluate_once()
        snap = det.get_snapshot()
        # Last state: miss → clean. Counter was 1 → 0 → 1 → 0 → 1 → 0.
        assert fp.name in snap["clean"]


# ── Public API contract ──────────────────────────────────────────────────


class TestPublicAPI:
    def test_summary_counts_clean_fingerprints(self):
        fp = _make_fp(_scripted_probe([None]))
        det = CascadeDetector(fingerprints=[fp])
        det.evaluate_once()
        s = det.summary()
        assert s == {"clean": 1}

    def test_summary_counts_escalated_fingerprints(self):
        hit = cfp.ProbeHit(evidence="ev", metric={})
        fp = _make_fp(_scripted_probe([hit, hit]), severity="pre_fail")
        det = CascadeDetector(fingerprints=[fp])
        det.evaluate_once()
        det.evaluate_once()
        s = det.summary()
        assert s == {"clean": 0, "pre_fail": 1}

    def test_snapshot_has_generated_at_and_summary(self):
        fp = _make_fp(_scripted_probe([None]))
        det = CascadeDetector(fingerprints=[fp])
        det.evaluate_once()
        snap = det.get_snapshot()
        assert "generated_at" in snap
        assert "summary" in snap
        assert snap["summary"]["total_fingerprints"] == 1
        assert snap["summary"]["clean"] == 1

    def test_cadence_gate_skips_recent_evaluations(self):
        """If cadence_s > 0, a probe within that window is skipped."""
        clock_holder = {"now": 1000.0}
        hit = cfp.ProbeHit(evidence="ev", metric={})
        call_count = {"n": 0}

        def counted_probe():
            call_count["n"] += 1
            return hit
        fp = _make_fp(counted_probe, cadence_s=30)
        det = CascadeDetector(
            fingerprints=[fp], clock=lambda: clock_holder["now"],
        )
        det.evaluate_once()  # call #1 — runs probe
        assert call_count["n"] == 1
        clock_holder["now"] = 1010.0  # +10s — gated
        det.evaluate_once()
        assert call_count["n"] == 1
        clock_holder["now"] = 1031.0  # +31s — runs again
        det.evaluate_once()
        assert call_count["n"] == 2

    def test_lifecycle_start_stop(self):
        """The daemon thread starts + stops cleanly."""
        fp = _make_fp(_scripted_probe([None] * 100))
        det = CascadeDetector(fingerprints=[fp], check_interval_s=5)
        det.start()
        assert det._thread is not None
        assert det._thread.is_alive()
        det.stop(timeout=2.0)
        assert det._thread is None

    def test_double_start_is_noop(self):
        fp = _make_fp(_scripted_probe([None] * 100))
        det = CascadeDetector(fingerprints=[fp], check_interval_s=5)
        det.start()
        t1 = det._thread
        det.start()  # second start should not spawn a second thread
        assert det._thread is t1
        det.stop(timeout=2.0)
