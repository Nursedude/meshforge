"""Watchdog lifecycle review fixes, 2026-07-26 (B1/B2/B3/B7/B9).

B1 — adoption gate: every member of the closed SIGNAL_CLASSES enum must have a
``note_disposition`` call somewhere in the probe modules (or the runner's
gates), else the class reads ``unknown`` every tick it doesn't fire and the
Pri-2 hold keeps its signals forever once one fires and heals. The gate is
AST-based because the call sites are legitimately multi-line and some pass the
class via a ``cls`` variable — a single-line grep reads them as absent (the
exact false-zero that produced this review finding).

B2 — rehydration: SignalTracker state was process-memory only, so a routine
watchdog restart (deploy sweeps restart it — #79) silently dropped every HELD
signal: the exact false recovery the hold exists to prevent. The daemon now
adopts the prior watchdog.json's signals at startup.

B3 — partial-active coverage: ``build_coverage`` stamped ``{"disp": "active"}``
unconditionally when a class emitted ANY signal, so a multi-subject probe that
emitted subject B while noting ``indeterminate`` (subject A unobservable) read
as fully observed and the tracker falsely CLEARED subject A.

B7 — a future-stamped tick clamped to age 0 read fresh/ok through the whole
skew window (RTC-less fleet, forward-then-corrected clock steps).

B9 — ``--one-shot`` wrote the daemon's live output path through the same fixed
tmp name, racing the daemon and transiently publishing a hold-free doc.
"""
from __future__ import annotations

import ast
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.watchdog_probe_core import (  # noqa: E402
    SIGNAL_CLASSES,
    Signal,
    note_disposition,
    reset_dispositions,
)
from utils import watchdog_runner  # noqa: E402
from utils.watchdog_runner import build_coverage  # noqa: E402
from utils.watchdog_tracker import SignalTracker, _class_was_observed  # noqa: E402


SRC = Path(__file__).resolve().parents[1] / "src"


def _sig(cls: str, subject: str, severity: str = "degraded") -> Signal:
    return Signal(cls=cls, subject=subject, severity=severity, detail="d")


# ─────────────────────────────────────────────────────────────────────
# B1 — the disposition adoption gate
# ─────────────────────────────────────────────────────────────────────

# Classes exempt from the note_disposition requirement. Deliberately EMPTY:
# every class must note. Adding a class here requires a documented reason for
# why "unknown every non-firing tick → held forever after one fire" is
# acceptable for it.
DISPOSITION_ALLOWLIST: frozenset = frozenset()


def _noted_classes_in_source(src_text: str) -> set:
    """All class names statically passed to ``note_disposition`` in one module.

    Handles the three real call shapes: a literal first argument (possibly on
    the next line), a ``cls`` variable assigned a literal in the same function,
    and a loop variable iterating a tuple/list of literals (the runner's
    gate loops). A shape this walker cannot resolve simply doesn't count as
    noted — the gate then fails loud rather than passing blind.
    """
    tree = ast.parse(src_text)
    noted: set = set()

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, fn):
            name_lits: dict = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if (isinstance(t, ast.Name)
                                and isinstance(node.value, ast.Constant)
                                and isinstance(node.value.value, str)):
                            name_lits.setdefault(t.id, set()).add(node.value.value)
                if (isinstance(node, ast.For)
                        and isinstance(node.target, ast.Name)
                        and isinstance(node.iter, (ast.Tuple, ast.List))):
                    for e in node.iter.elts:
                        if isinstance(e, ast.Constant) and isinstance(e.value, str):
                            name_lits.setdefault(node.target.id, set()).add(e.value)
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    fname = (f.id if isinstance(f, ast.Name)
                             else f.attr if isinstance(f, ast.Attribute) else None)
                    if fname == "note_disposition" and node.args:
                        a = node.args[0]
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            noted.add(a.value)
                        elif isinstance(a, ast.Name):
                            noted.update(name_lits.get(a.id, ()))
            self.generic_visit(fn)

        visit_AsyncFunctionDef = visit_FunctionDef

    V().visit(tree)
    return noted


class TestDispositionAdoptionGate:
    def _union_noted(self) -> set:
        files = sorted(SRC.glob("utils/watchdog_probes*.py"))
        files.append(SRC / "utils" / "watchdog_runner.py")
        noted: set = set()
        for f in files:
            noted |= _noted_classes_in_source(f.read_text(encoding="utf-8"))
        return noted

    def test_every_signal_class_notes_a_disposition(self):
        """A class with zero note_disposition calls is unknown every tick it
        does not fire — once it fires and heals, the Pri-2 hold keeps the
        stale signal forever (repro: held at tick 100)."""
        noted = self._union_noted()
        missing = [c for c in SIGNAL_CLASSES
                   if c not in noted and c not in DISPOSITION_ALLOWLIST]
        assert missing == [], (
            f"signal classes with NO note_disposition call anywhere: {missing} "
            f"— each fires-then-heals into a permanent stale hold. Add honest "
            f"dispositions at the probe's return paths (clean = positively "
            f"observed healthy, inert = absent-by-design, indeterminate = "
            f"channel failed), or add to DISPOSITION_ALLOWLIST with a reason.")

    def test_allowlist_entries_are_real_classes(self):
        stale = DISPOSITION_ALLOWLIST - set(SIGNAL_CLASSES)
        assert not stale, f"allowlist entries not in SIGNAL_CLASSES: {sorted(stale)}"

    @pytest.mark.parametrize("cls", [
        # The four classes the 2026-07-26 review flagged as zero-call — all
        # four DO note (multi-line and cls-variable call shapes defeated the
        # reviewer's single-line grep). Pinned so they stay noted.
        "nomadnet_crashloop",
        "aredn_organ_undeclared",
        "dream_ratification_stalled",
        "local_brain_regressed",
    ])
    def test_reviewed_classes_are_noted(self, cls):
        assert cls in self._union_noted()

    def test_walker_resolves_all_three_call_shapes(self):
        src = (
            'def a():\n'
            '    note_disposition(\n'
            '        "multi_line", "inert")\n'
            'def b():\n'
            '    cls = "via_var"\n'
            '    note_disposition(cls, "clean")\n'
            'def c():\n'
            '    for _c in ("loop_a", "loop_b"):\n'
            '        note_disposition(_c, "inert")\n'
        )
        assert _noted_classes_in_source(src) == {
            "multi_line", "via_var", "loop_a", "loop_b"}


# ─────────────────────────────────────────────────────────────────────
# B2 — rehydration across a daemon restart
# ─────────────────────────────────────────────────────────────────────

CLS = "main_thread_wedge"


class TestRehydrate:
    def _entry(self, **over):
        e = {"class": CLS, "subject": "meshforge-map.service",
             "severity": "wedge", "detail": "stuck", "issue_ref": 68,
             "first_seen": 1000.0, "extra": {"k": "v"}}
        e.update(over)
        return e

    def test_adopts_valid_entries_with_persisted_first_seen(self):
        t = SignalTracker()
        n = t.rehydrate([self._entry()])
        assert n == 1
        # A blind first tick must HOLD it, re-emitted with the hold marker
        # and the PERSISTED first_seen — not this process's start time.
        active, cleared, held = t.update(
            [], now=2000.0, coverage={CLS: {"disp": "indeterminate"}})
        assert cleared == []
        assert [(s.cls, s.subject) for s, _ in active] == [
            (CLS, "meshforge-map.service")]
        sig, fs = active[0]
        assert fs == 1000.0
        assert sig.extra.get("unobserved_hold") is True
        assert (CLS, "meshforge-map.service") in held

    def test_positive_observation_clears_on_first_tick(self):
        t = SignalTracker()
        t.rehydrate([self._entry()])
        active, cleared, held = t.update(
            [], now=2000.0, coverage={CLS: {"disp": "clean"}})
        assert active == []
        assert cleared == [(CLS, "meshforge-map.service")]
        assert held == []

    def test_reobserved_signal_stays_active_with_persisted_first_seen(self):
        t = SignalTracker()
        t.rehydrate([self._entry()])
        live = _sig(CLS, "meshforge-map.service", severity="wedge")
        active, cleared, held = t.update(
            [live], now=2000.0, coverage={CLS: {"disp": "active"}})
        assert cleared == [] and held == []
        assert len(active) == 1
        sig, fs = active[0]
        assert fs == 1000.0  # a blind window must not reset the wedge's age
        assert not (sig.extra or {}).get("unobserved_hold")

    def test_garbage_entries_are_skipped(self):
        t = SignalTracker()
        n = t.rehydrate([
            None,
            "not-a-dict",
            {},                                            # no class
            self._entry(subject=None),                     # bad subject
            self._entry(subject=""),
            self._entry(first_seen="old"),                 # bad first_seen
            self._entry(first_seen=True),                  # bool is not a ts
            self._entry(**{"class": 7}),                   # bad class
            self._entry(),                                 # the one valid row
        ])
        assert n == 1

    def test_rehydrate_none_and_empty_are_noops(self):
        t = SignalTracker()
        assert t.rehydrate(None) == 0
        assert t.rehydrate([]) == 0
        active, cleared, held = t.update([], now=1.0, coverage={})
        assert active == [] and cleared == [] and held == []

    def test_duplicate_key_does_not_overwrite_first_adoption(self):
        t = SignalTracker()
        n = t.rehydrate([self._entry(first_seen=500.0),
                         self._entry(first_seen=900.0)])
        assert n == 1
        active, _, _ = t.update(
            [], now=2000.0, coverage={CLS: {"disp": "indeterminate"}})
        assert active[0][1] == 500.0

    def test_tolerant_of_missing_optional_fields(self):
        t = SignalTracker()
        n = t.rehydrate([{"class": CLS, "subject": "s", "first_seen": 10.0}])
        assert n == 1
        active, _, _ = t.update(
            [], now=20.0, coverage={CLS: {"disp": "unknown"}})
        sig, fs = active[0]
        assert fs == 10.0
        assert sig.severity == "degraded"  # honest floor, not a fabricated wedge


class TestLoadPriorSignals:
    def test_reads_signals_from_prior_state(self, tmp_path):
        p = tmp_path / "watchdog.json"
        p.write_text(json.dumps({"host": "x", "ts": 1.0, "signals": [
            {"class": CLS, "subject": "s", "first_seen": 1.0}]}))
        sigs = watchdog_runner._load_prior_signals(p)
        assert len(sigs) == 1 and sigs[0]["class"] == CLS

    def test_absent_file_starts_empty(self, tmp_path):
        assert watchdog_runner._load_prior_signals(tmp_path / "nope.json") == []

    def test_corrupt_file_starts_empty_never_crashes(self, tmp_path):
        p = tmp_path / "watchdog.json"
        p.write_text('{"signals": [truncated')
        assert watchdog_runner._load_prior_signals(p) == []

    def test_non_object_payload_starts_empty(self, tmp_path):
        p = tmp_path / "watchdog.json"
        p.write_text('["not", "an", "object"]')
        assert watchdog_runner._load_prior_signals(p) == []

    def test_signals_not_a_list_starts_empty(self, tmp_path):
        p = tmp_path / "watchdog.json"
        p.write_text('{"signals": {"class": "x"}}')
        assert watchdog_runner._load_prior_signals(p) == []


class TestRunLoopRehydration:
    def test_restart_holds_prior_signal_into_the_new_state_file(
            self, tmp_path, caplog):
        """End to end: prior watchdog.json carries a signal; the restarted
        loop's FIRST tick goes blind on that class (patched probes return
        nothing and note nothing) → the new state file still carries the
        signal, marked unobserved_hold, with the persisted first_seen."""
        out = tmp_path / "watchdog.json"
        out.write_text(json.dumps({"host": "x", "ts": 1.0, "probe_count": 9,
                                   "ok": True, "signals": [
            {"class": CLS, "subject": "meshforge-map.service",
             "severity": "wedge", "detail": "stuck", "first_seen": 1234.0,
             "extra": {}}]}))
        stop = threading.Event()

        def one_tick(**_kw):
            stop.set()
            reset_dispositions()  # a real tick resets the recorder; keep parity
            return []

        with patch.object(watchdog_runner, "run_all_probes",
                          side_effect=one_tick):
            with caplog.at_level("INFO", logger="watchdog"):
                watchdog_runner.run_loop(
                    output_path=out, tick_s=0.01, stop_event=stop)

        assert "rehydrated 1 signal(s) from prior state" in caplog.text
        doc = json.loads(out.read_text())
        assert len(doc["signals"]) == 1
        sig = doc["signals"][0]
        assert sig["class"] == CLS
        assert sig["first_seen"] == 1234.0
        assert sig["extra"]["unobserved_hold"] is True

    def test_restart_with_corrupt_prior_state_starts_empty(
            self, tmp_path, caplog):
        out = tmp_path / "watchdog.json"
        out.write_text("{corrupt")
        stop = threading.Event()

        def one_tick(**_kw):
            stop.set()
            reset_dispositions()
            return []

        with patch.object(watchdog_runner, "run_all_probes",
                          side_effect=one_tick):
            with caplog.at_level("INFO", logger="watchdog"):
                watchdog_runner.run_loop(
                    output_path=out, tick_s=0.01, stop_event=stop)

        doc = json.loads(out.read_text())
        assert doc["signals"] == []


# ─────────────────────────────────────────────────────────────────────
# B3 — partial-active coverage must hold vanished subjects
# ─────────────────────────────────────────────────────────────────────

PCLS = "tracer_peer_unreachable"


class TestPartialActiveCoverage:
    def test_false_clear_repro_vanished_subject_holds_under_partial(self):
        """The review's repro: tick 1 peerA+peerB active; tick 2 peerB active
        while the probe notes indeterminate (peerA's channel failed) → peerA
        must HOLD, not clear."""
        tracker = SignalTracker()
        a, b = _sig(PCLS, "peerA"), _sig(PCLS, "peerB")

        reset_dispositions()
        cov1 = build_coverage([a, b])
        tracker.update([a, b], now=100.0, coverage=cov1)

        reset_dispositions()
        note_disposition(PCLS, "indeterminate",
                         reason="peerA tracer file unreadable")
        cov2 = build_coverage([b])
        active, cleared, held = tracker.update([b], now=200.0, coverage=cov2)

        assert (PCLS, "peerA") not in cleared
        assert (PCLS, "peerA") in held
        by_subject = {s.subject: (s, fs) for s, fs in active}
        assert by_subject["peerA"][0].extra.get("unobserved_hold") is True
        assert by_subject["peerA"][1] == 100.0
        # The active signal itself is still accepted, unmarked.
        assert not (by_subject["peerB"][0].extra or {}).get("unobserved_hold")
        assert by_subject["peerB"][1] == 100.0

    def test_build_coverage_marks_partial_and_carries_reason(self):
        reset_dispositions()
        note_disposition(PCLS, "indeterminate", reason="peerA unobservable")
        cov = build_coverage([_sig(PCLS, "peerB")])
        assert cov[PCLS]["disp"] == "active"
        assert cov[PCLS]["partial"] is True
        assert "peerA unobservable" in cov[PCLS]["reason"]

    def test_build_coverage_plain_active_is_not_partial(self):
        reset_dispositions()
        cov = build_coverage([_sig(PCLS, "peerB")])
        assert cov[PCLS] == {"disp": "active"}

    def test_build_coverage_clean_note_does_not_mark_partial(self):
        # clean/inert notes beside an active signal are not a blind subject.
        reset_dispositions()
        note_disposition(PCLS, "clean")
        cov = build_coverage([_sig(PCLS, "peerB")])
        assert cov[PCLS] == {"disp": "active"}

    def test_class_was_observed_rejects_partial_active(self):
        assert _class_was_observed({PCLS: {"disp": "active"}}, PCLS) is True
        assert _class_was_observed(
            {PCLS: {"disp": "active", "partial": True}}, PCLS) is False

    def test_full_recovery_after_partial_window_still_clears(self):
        tracker = SignalTracker()
        a, b = _sig(PCLS, "peerA"), _sig(PCLS, "peerB")
        reset_dispositions()
        tracker.update([a, b], now=100.0, coverage=build_coverage([a, b]))
        reset_dispositions()
        note_disposition(PCLS, "indeterminate", reason="peerA unobservable")
        tracker.update([b], now=200.0, coverage=build_coverage([b]))
        # Channel recovers; probe positively observes and emits nothing.
        reset_dispositions()
        note_disposition(PCLS, "clean")
        active, cleared, _held = tracker.update(
            [], now=300.0, coverage=build_coverage([]))
        assert set(cleared) == {(PCLS, "peerA"), (PCLS, "peerB")}
        assert active == []


# ─────────────────────────────────────────────────────────────────────
# B7 — future-stamped ticks are clock skew, not freshness
# ─────────────────────────────────────────────────────────────────────


class TestFutureTimestampSkew:
    def test_mini_future_ts_is_not_ok(self):
        from utils._map_status_endpoints import (
            FUTURE_TS_SLACK_S, mini_block_from_payload)
        now = 1_000_000.0
        block = mini_block_from_payload(
            {"last_tick_ts": now + FUTURE_TS_SLACK_S + 60, "rules": {}},
            now=now)
        assert block["ok"] is False
        assert "clock skew" in block["reason"]

    def test_mini_future_within_slack_still_ok(self):
        from utils._map_status_endpoints import mini_block_from_payload
        now = 1_000_000.0
        block = mini_block_from_payload(
            {"last_tick_ts": now + 30, "rules": {}}, now=now)
        assert block["ok"] is True

    def test_claw_future_ts_is_not_ok(self):
        from utils._map_status_endpoints import (
            FUTURE_TS_SLACK_S, StatusEndpointsMixin)
        dummy = SimpleNamespace(_CLAW_STALE_S=900.0)
        tick = {"captured_at": time.time() + FUTURE_TS_SLACK_S + 3600,
                "reachable": True, "host": "moc2", "device": "dudeclaw-01"}
        p = SimpleNamespace(
            read_text=lambda encoding="utf-8": json.dumps(tick),
            name="claw.json")
        block = StatusEndpointsMixin._parse_claw_tick(dummy, p)
        assert block["ok"] is False
        assert "clock skew" in block["reason"]

    def test_claw_fresh_tick_still_ok(self):
        from utils._map_status_endpoints import StatusEndpointsMixin
        dummy = SimpleNamespace(_CLAW_STALE_S=900.0)
        tick = {"captured_at": time.time() - 10,
                "reachable": True, "host": "moc2", "device": "dudeclaw-01"}
        p = SimpleNamespace(
            read_text=lambda encoding="utf-8": json.dumps(tick),
            name="claw.json")
        block = StatusEndpointsMixin._parse_claw_tick(dummy, p)
        assert block["ok"] is True


# ─────────────────────────────────────────────────────────────────────
# B9 — one-shot must not write the daemon's live path by default
# ─────────────────────────────────────────────────────────────────────


class TestOneShotOutputPath:
    def test_default_one_shot_path_is_distinct_from_daemon_path(self):
        p = watchdog_runner._oneshot_default_output()
        assert p != watchdog_runner.DEFAULT_OUTPUT_PATH
        assert p.parent == watchdog_runner.DEFAULT_OUTPUT_PATH.parent
        assert "oneshot" in p.name
        # The atomic-rename tmp names must differ too — the race was the
        # fixed .tmp sibling, not only the final rename target.
        assert (p.with_suffix(p.suffix + ".tmp")
                != watchdog_runner.DEFAULT_OUTPUT_PATH.with_suffix(
                    watchdog_runner.DEFAULT_OUTPUT_PATH.suffix + ".tmp"))

    def test_explicit_output_is_respected(self):
        out = watchdog_runner._resolve_output_paths(
            Path("/tmp/x.json"), one_shot=True)
        assert out == Path("/tmp/x.json")

    def test_one_shot_without_explicit_output_uses_distinct_path(self):
        out = watchdog_runner._resolve_output_paths(None, one_shot=True)
        assert out == watchdog_runner._oneshot_default_output()

    def test_daemon_without_explicit_output_uses_live_path(self):
        out = watchdog_runner._resolve_output_paths(None, one_shot=False)
        assert out == watchdog_runner.DEFAULT_OUTPUT_PATH
