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


# ── rns_rpc_wedge probe ───────────────────────────────────────────────────


class TestProbeRnsRpcWedge:
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
