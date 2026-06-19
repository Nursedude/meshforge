"""Pins for the 2026-06-09 mini-dudeai engine review (honest failure modes).

The shared defect shape across all of these: a degraded internal state
(read error, source error, null document, unknown key) was mapped to a
VALID-LOOKING value (empty list, absent condition, zero errors, extras
filter) that downstream logic interpreted as a real-world claim ("no rules",
"recovered", "valid", "legal filter"). Every test here pins the loud/held
behavior that replaced a silent one.
"""
import gzip
import http.client
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai import Condition, RuleEngine, Source
from mini_dudeai._util import fetch_json
from mini_dudeai.candidate import lint_rules_document, validate_rules_document
from mini_dudeai.history import HistoryWriter, _rotate_if_needed, append_jsonl
from mini_dudeai.sources.boot_health import BootHealthSource


class RecordingAction:
    """Action that records every (rule_id, transition) it executes."""

    def __init__(self):
        self.calls = []

    def execute(self, rule, cond, transition):
        from mini_dudeai import Outcome
        self.calls.append((rule["id"], cond.subject, transition))
        return Outcome(action="recording", ok=True)


class FlippableSource(Source):
    """Emits a fixed condition list; set .fail to simulate a source error."""

    name = "flippable"

    def __init__(self, conditions=None):
        self.conditions = conditions or []
        self.fail = False
        self.raise_instead = False

    def collect(self):
        if self.fail:
            if self.raise_instead:
                raise RuntimeError("simulated source crash")
            yield Condition(kind="source_error", subject=self.name,
                            detail="simulated read failure", source=self.name)
            return
        yield from self.conditions


RULES_DOC = {
    "rules": [
        {
            "id": "peer_down",
            "match": {"kind": "peer_unhealthy", "subject_glob": "*"},
            "action": {"kind": "recording"},
        }
    ]
}


def make_engine(tmp_path, source, rules_doc=RULES_DOC):
    rules_path = str(tmp_path / "rules.json")
    with open(rules_path, "w") as f:
        json.dump(rules_doc, f)
    action = RecordingAction()
    eng = RuleEngine(
        sources=[source],
        actions={"recording": action},
        rules_path=rules_path,
        state_path=str(tmp_path / "state.json"),
        history_path=str(tmp_path / "history.jsonl"),
        candidate_path=str(tmp_path / "rules.json.candidate"),
    )
    return eng, action


COND = Condition(kind="peer_unhealthy", subject="moc9", detail="down",
                 source="flippable")


class TestSourceErrorHoldsConditions:
    """A blind source must not read as 'everything recovered' (false CLEARED)."""

    def test_source_error_does_not_edge_down_active_condition(self, tmp_path):
        src = FlippableSource([COND])
        eng, action = make_engine(tmp_path, src)
        eng.tick()  # edge_up
        assert action.calls == [("peer_down", "moc9", "edge_up")]
        src.fail = True
        eng.tick()  # source blind — condition must be HELD, not cleared
        assert ("peer_down", "moc9", "edge_down") not in action.calls
        src.fail = False
        eng.tick()  # source recovers, condition still live — no duplicate page
        assert action.calls == [("peer_down", "moc9", "edge_up")]

    def test_raising_source_also_holds(self, tmp_path):
        src = FlippableSource([COND])
        src.raise_instead = True
        eng, action = make_engine(tmp_path, src)
        eng.tick()
        src.fail = True
        eng.tick()
        assert ("peer_down", "moc9", "edge_down") not in action.calls

    def test_real_recovery_still_edge_downs(self, tmp_path):
        src = FlippableSource([COND])
        eng, action = make_engine(tmp_path, src)
        eng.tick()
        src.conditions = []  # healthy collect, condition genuinely gone
        eng.tick()
        assert ("peer_down", "moc9", "edge_down") in action.calls

    def test_hold_survives_restart_via_state(self, tmp_path):
        src = FlippableSource([COND])
        eng, action = make_engine(tmp_path, src)
        eng.tick()
        # New engine instance (restart) with the source ALREADY failing:
        # the hold cache rehydrates from state, so no false edge_down.
        src2 = FlippableSource([COND])
        src2.fail = True
        eng2, action2 = make_engine(tmp_path, src2)
        eng2.tick()
        assert ("peer_down", "moc9", "edge_down") not in action2.calls


class TestRulesUnreadableHoldsEdgeState:
    """'Rules unobservable' must not read as 'nothing active'."""

    def test_unreadable_rules_does_not_mass_deactivate(self, tmp_path):
        src = FlippableSource([COND])
        eng, action = make_engine(tmp_path, src)
        eng.tick()
        state = eng.state_store.load()
        key = "peer_down::moc9"
        assert state["rules"][key]["currently_active"] is True
        # Corrupt the rules file (torn write) — and defeat the in-memory
        # last-good cache to exercise the no-ruleset hold path.
        with open(eng.rules_path, "w") as f:
            f.write("{ torn json")
        eng._last_good_rules = None
        eng._rules_cache = None
        eng.tick()
        state = eng.state_store.load()
        assert state["rules"][key]["currently_active"] is True  # HELD
        # Repair the file: condition still live → no duplicate edge_up.
        with open(eng.rules_path, "w") as f:
            json.dump(RULES_DOC, f)
        eng.tick()
        assert action.calls.count(("peer_down", "moc9", "edge_up")) == 1

    def test_unreadable_rules_degrades_to_last_good(self, tmp_path):
        src = FlippableSource([COND])
        eng, action = make_engine(tmp_path, src)
        eng.tick()
        with open(eng.rules_path, "w") as f:
            f.write("{ torn json")
        eng._rules_cache = None  # force re-read (mtime changed anyway)
        rules, errs = eng.load_rules()
        assert [r["id"] for r in rules] == ["peer_down"]  # last-good held
        assert any("last-good" in e for e in errs)

    def test_rule_removed_while_active_is_on_the_record(self, tmp_path):
        src = FlippableSource([COND])
        eng, action = make_engine(tmp_path, src)
        eng.tick()
        with open(eng.rules_path, "w") as f:
            json.dump({"rules": []}, f)
        eng._rules_cache = None
        eng.tick()
        state = eng.state_store.load()
        assert state["rules"]["peer_down::moc9"]["currently_active"] is False
        with open(eng.history.path) as f:
            entries = [json.loads(l) for l in f if l.strip()]
        removed = [e for e in entries if "removed" in e.get("detail", "")]
        assert removed and removed[0]["transition"] == "edge_down"


class TestPromotionGuards:
    def test_null_rules_document_is_invalid(self):
        valid, errors = validate_rules_document({"rules": None})
        assert errors and "list" in errors[0]

    def test_promotion_refuses_to_wipe_canonical(self, tmp_path):
        src = FlippableSource([])
        eng, _ = make_engine(tmp_path, src)
        with open(eng.candidate_path, "w") as f:
            json.dump({"rules": []}, f)
        promo = eng._promote_candidate()
        assert promo["promoted"] is False
        assert "refusing" in promo["reason"]
        data, _err = json.load(open(eng.rules_path)), None
        assert len(data["rules"]) == 1  # canonical untouched

    def test_lint_flags_typod_structural_key(self):
        warnings = lint_rules_document({"rules": [{
            "id": "dead", "match": {"kind": "x", "suject_glob": "y*"},
            "action": {"kind": "none"},
        }]})
        assert any("suject_glob" in w for w in warnings)

    def test_lint_quiet_on_known_keys(self):
        warnings = lint_rules_document(RULES_DOC)
        assert warnings == []

    def test_lint_quiet_on_class_extras_key(self):
        warnings = lint_rules_document({"rules": [{
            "id": "ok", "match": {"kind": "signal_class", "class": "fd_exhaustion"},
            "action": {"kind": "none"},
        }]})
        assert warnings == []


class TestClockAndGraceHonesty:
    def test_negative_cooldown_age_is_clamped(self, tmp_path):
        import time as _time
        src = FlippableSource([COND])
        eng, action = make_engine(tmp_path, src)
        eng.tick()
        state = eng.state_store.load()
        rs = state["rules"]["peer_down::moc9"]
        # Simulate a backward NTP step: last_fired in the (apparent) future.
        rs["currently_active"] = False
        rs["last_fired_ts"] = _time.time() + 9999.0
        eng.state_store.save(state)
        eng.tick()
        state = eng.state_store.load()
        # The future timestamp was re-anchored to now, not left to suppress
        # the rule for 9999s + cooldown.
        assert state["rules"]["peer_down::moc9"]["last_fired_ts"] <= _time.time()

    def test_grace_requires_observed_ticks_not_just_wall_span(self, tmp_path):
        src = FlippableSource([COND])
        eng, action = make_engine(tmp_path, src)
        with open(eng.rules_path, "w") as f:
            json.dump({"rules": [{
                "id": "peer_down",
                "match": {"kind": "peer_unhealthy", "subject_glob": "*"},
                "action": {"kind": "recording"},
                "grace_s": 120,
            }]}, f)
        eng._interval_s = 30.0  # as run() would set
        eng.tick()  # tick 1: streak starts
        assert action.calls == []
        # Simulate an NTP forward step: wall span now exceeds grace_s but the
        # box has only OBSERVED one matched tick — must not fire yet
        # (grace=120/interval=30 → 4 observed ticks required).
        state = eng.state_store.load()
        state["rules"]["peer_down::moc9"]["pending_since_ts"] -= 100000
        eng.state_store.save(state)
        eng.tick()  # tick 2 — span huge, ticks=2 < 4
        assert action.calls == []
        eng.tick()  # tick 3
        eng.tick()  # tick 4 — observed ticks satisfied, span satisfied
        assert action.calls == [("peer_down", "moc9", "edge_up")]

    def test_startup_resets_persisted_streaks(self, tmp_path):
        src = FlippableSource([COND])
        eng, _ = make_engine(tmp_path, src)
        state = eng.state_store.load()
        state["rules"] = {"peer_down::moc9": {
            "rule_id": "peer_down", "subject": "moc9",
            "currently_active": False, "last_fired_ts": 0.0,
            "fire_count": 0, "fire_count_24h": 0, "fires_window": [],
            "last_detail": "", "pending_since_ts": 12345.0, "pending_ticks": 3,
        }}
        eng.state_store.save(state)
        eng._reset_pending_streaks()
        state = eng.state_store.load()
        assert state["rules"]["peer_down::moc9"]["pending_since_ts"] == 0.0
        assert state["rules"]["peer_down::moc9"]["pending_ticks"] == 0


class TestHistoryTornTail:
    def test_append_repairs_torn_tail_instead_of_fusing(self, tmp_path):
        path = str(tmp_path / "h.jsonl")
        with open(path, "w") as f:
            f.write('{"ts": 1, "ok": true}\n{"ts": 2, "torn')  # no newline
        w = HistoryWriter(path)
        assert w.append([{"ts": 3, "ok": True}]) is None
        with open(path) as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        parsed, bad = [], 0
        for l in lines:
            try:
                parsed.append(json.loads(l))
            except ValueError:
                bad += 1
        # The torn fragment is isolated (1 malformed line), the new record
        # parses — it was NOT fused onto the fragment.
        assert bad == 1
        assert {p["ts"] for p in parsed} == {1, 3}

    def test_rotation_terminates_torn_last_line(self, tmp_path):
        path = str(tmp_path / "h.jsonl")
        line = json.dumps({"x": "y" * 100})
        with open(path, "w") as f:
            for _ in range(50):
                f.write(line + "\n")
            f.write('{"torn')  # no newline
        _rotate_if_needed(path, max_bytes=1000)
        with open(path, "rb") as f:
            assert f.read().endswith(b"\n")

    def test_append_jsonl_shared_posture(self, tmp_path):
        path = str(tmp_path / "ledger.jsonl")
        assert append_jsonl(path, [{"a": 1}], 1000) is None
        assert append_jsonl(path, [], 1000) is None  # no-op contract
        with open(path) as f:
            assert json.loads(f.read()) == {"a": 1}


class TestFetchJsonContract:
    """fetch_json is documented never-raises — pin the escapes the review found."""

    def _serve(self, monkeypatch, body, headers=None, raises=None):
        class FakeResponse:
            def __init__(self):
                self.headers = headers or {}

            def read(self, n=-1):
                if raises:
                    raise raises
                return body if n is None or n < 0 else body[:n]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda *a, **k: FakeResponse())

    def test_truncated_gzip_returns_error_not_raise(self, monkeypatch):
        whole = gzip.compress(b'{"ok": true}')
        self._serve(monkeypatch, whole[:len(whole) - 4],
                    headers={"Content-Encoding": "gzip"})
        data, err = fetch_json("http://x/api")
        assert data is None and err  # EOFError swallowed into the contract

    def test_incomplete_read_returns_error_not_raise(self, monkeypatch):
        self._serve(monkeypatch, b"", raises=http.client.IncompleteRead(b"x"))
        data, err = fetch_json("http://x/api")
        assert data is None and "IncompleteRead" in err

    def test_oversize_body_is_capped(self, monkeypatch):
        self._serve(monkeypatch, b"x" * 100)
        data, err = fetch_json("http://x/api", max_bytes=10)
        assert data is None and "exceeded" in err

    def test_happy_path_still_works(self, monkeypatch):
        self._serve(monkeypatch, b'{"ok": true}')
        data, err = fetch_json("http://x/api")
        assert err is None and data == {"ok": True}


class TestBootHealthClockSteps:
    def _mk(self, tmp_path, uptime="60.0 120.0", boot_id="abc-123"):
        up = tmp_path / "uptime"
        up.write_text(uptime)
        bid = tmp_path / "boot_id"
        bid.write_text(boot_id + "\n")
        return BootHealthSource(
            state_path=str(tmp_path / "state.json"),
            clean_exit_path=str(tmp_path / "clean_exit"),
            assessment_path=str(tmp_path / "assessment.json"),
            uptime_path=str(up),
            boot_id_path=str(bid),
        )

    def test_indeterminate_upgrades_after_clock_sync(self, tmp_path):
        import time as _time
        src = self._mk(tmp_path)
        now = _time.time()
        # Pre-cut facts: mini last ticked at now-300, NO clean-exit marker
        # (hard cut). Stale pre-NTP clock makes boot_time land BEFORE the
        # last tick → indeterminate.
        (tmp_path / "state.json").write_text(
            json.dumps({"last_tick_ts": now - 300}))
        stale_boot_time = now - 400
        v1 = src._assess(stale_boot_time, now)
        assert v1["verdict"] == "indeterminate"
        # NTP syncs: corrected boot_time is AFTER the last pre-cut tick.
        # Same boot_id → re-assessment from STORED facts upgrades to unclean.
        v2 = src._assess(now - 60, now + 30)
        assert v2["verdict"] == "unclean"

    def test_definitive_verdict_immutable_across_big_clock_step(self, tmp_path):
        import time as _time
        src = self._mk(tmp_path)
        now = _time.time()
        (tmp_path / "state.json").write_text(
            json.dumps({"last_tick_ts": now - 300}))
        v1 = src._assess(now - 60, now)  # boot_time after last tick, no marker
        assert v1["verdict"] == "unclean"
        # A >120s step used to break the time-keyed latch and re-assess;
        # the boot_id latch keeps the definitive verdict stable.
        v2 = src._assess(now + 500, now + 600)
        assert v2["verdict"] == "unclean"

    def test_clean_verdict_when_marker_present(self, tmp_path):
        import time as _time
        src = self._mk(tmp_path)
        now = _time.time()
        (tmp_path / "state.json").write_text(
            json.dumps({"last_tick_ts": now - 300}))
        (tmp_path / "clean_exit").write_text(f"{now - 290:.3f}")
        v = src._assess(now - 60, now)
        assert v["verdict"] == "clean"


class TestInstanceLock:
    def test_second_acquire_refused(self, tmp_path):
        from mini_dudeai.daemon import _acquire_instance_lock
        lock_path = str(tmp_path / "state.json.lock")
        first = _acquire_instance_lock(lock_path)
        assert first is not None
        second = _acquire_instance_lock(lock_path)
        assert second is None
        first.close()  # release
        third = _acquire_instance_lock(lock_path)
        assert third is not None
        third.close()


class TestSeedCoversSignalClasses:
    """The class-closing gate: a NEW watchdog signal class must fail here
    until BOTH role seeds carry a rule routing it — otherwise the probe
    fires into a void and the 'every probe flows to mini FREE' pattern is
    silently broken (the exact gap this review found: 6 classes unrouted).
    """

    # Deliberately-unrouted classes go here WITH a reason, never silently.
    ALLOWED_UNROUTED: set = set()

    @pytest.mark.parametrize("seed", [
        "configs/mini_dudeai_rules.fleet_gateway.json",
        "configs/mini_dudeai_rules.federator.json",
    ])
    def test_every_signal_class_has_a_rule(self, seed):
        from utils.watchdog_probes import SIGNAL_CLASSES
        root = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(root, seed)) as f:
            doc = json.load(f)
        covered = set()
        for r in doc["rules"]:
            m = r.get("match", {})
            if m.get("kind") == "signal_class" and m.get("class"):
                covered.add(m["class"])
        missing = set(SIGNAL_CLASSES) - covered - self.ALLOWED_UNROUTED
        assert not missing, (
            f"{seed} has no rule for signal class(es) {sorted(missing)} — "
            f"signals of these classes will never reach ntfy/escalation/brief. "
            f"Add a rule (or an explicit ALLOWED_UNROUTED entry with a reason).")


class TestCleanupBatch:
    """Pins for the residual-cleanup batch (same review, second pass)."""

    def test_prune_retires_dead_state_keys(self, tmp_path):
        import time as _time
        from mini_dudeai.state import StateStore, _empty_rule_state
        now = _time.time()
        state = {"rules": {}}
        dead = _empty_rule_state("old_rule", "gone-peer")
        dead["last_fired_ts"] = now - 30 * 86400  # ancient
        live = _empty_rule_state("live_rule", "moc9")
        live["currently_active"] = True
        recent = _empty_rule_state("recent_rule", "moc1")
        recent["last_fired_ts"] = now - 3600
        pending = _empty_rule_state("pending_rule", "moc2")
        pending["pending_since_ts"] = now - 10
        state["rules"] = {"a": dead, "b": live, "c": recent, "d": pending}
        StateStore.prune_24h(state, now)
        assert set(state["rules"]) == {"b", "c", "d"}  # only the dead key retired

    def test_record_fire_single_derivation(self):
        import time as _time
        from mini_dudeai.state import StateStore, _empty_rule_state
        rs = _empty_rule_state("r", "s")
        now = _time.time()
        StateStore.record_fire(rs, now)
        assert rs["fire_count"] == 1
        assert rs["fires_window"] == [now]
        assert rs["fire_count_24h"] == 1
        assert rs["last_fired_ts"] == now

    def test_brief_skips_rewrite_when_unchanged(self, tmp_path):
        from mini_dudeai.brief import write_brief
        state_path = str(tmp_path / "state.json")
        hist_path = str(tmp_path / "h.jsonl")
        out_path = str(tmp_path / "brief.md")
        # FRESH state (the quiet-healthy box — the common case the SD-wear
        # guard targets). A stale box's banner embeds the age and so
        # legitimately rewrites; alive boxes show a static "🟢 alive" line.
        state = {"last_tick_ts": 1000.0, "rules": {}, "rule_count": 3, "host": "x"}
        with open(state_path, "w") as f:
            json.dump(state, f)
        write_brief(state_path, hist_path, out_path, now_ts=1010.0)
        first = os.stat(out_path).st_mtime_ns
        # Same substance, later stamp — only the volatile _generated line
        # differs, so the write must be skipped (SD wear, every 30s forever).
        write_brief(state_path, hist_path, out_path, now_ts=1040.0)
        assert os.stat(out_path).st_mtime_ns == first
        # Substance changes → write happens.
        state["rule_count"] = 4
        with open(state_path, "w") as f:
            json.dump(state, f)
        write_brief(state_path, hist_path, out_path, now_ts=1070.0)
        assert os.stat(out_path).st_mtime_ns != first

    def test_history_tail_blockwise_matches_full_read(self, tmp_path):
        from mini_dudeai.brief import _read_history_tail
        path = str(tmp_path / "big.jsonl")
        with open(path, "w") as f:
            for i in range(5000):
                f.write(json.dumps({"i": i, "pad": "x" * 200}) + "\n")
        tail = _read_history_tail(path, 60)
        assert len(tail) == 60
        assert [e["i"] for e in tail] == list(range(4940, 5000))

    def test_resolve_home_honors_env(self, monkeypatch, tmp_path):
        from mini_dudeai._util import resolve_home
        monkeypatch.setenv("MINI_DUDEAI_HOME", str(tmp_path))
        assert resolve_home() == str(tmp_path)
        monkeypatch.delenv("MINI_DUDEAI_HOME")
        assert resolve_home() == os.path.expanduser("~")

    def test_log_helpers_prefix_priority_under_journald(self, monkeypatch, capsys):
        from mini_dudeai._util import log_error, log_info
        monkeypatch.setenv("JOURNAL_STREAM", "8:12345")
        log_error("boom")
        log_info("fine")
        out = capsys.readouterr().out
        assert "<3>boom" in out and "<6>fine" in out
        monkeypatch.delenv("JOURNAL_STREAM")
        log_error("plain")
        assert capsys.readouterr().out == "plain\n"

    def test_promotion_writes_provenance_history(self, tmp_path):
        src = FlippableSource([])
        eng, _ = make_engine(tmp_path, src)
        with open(eng.candidate_path, "w") as f:
            json.dump({"rules": [
                {"id": "peer_down",
                 "match": {"kind": "peer_unhealthy", "subject_glob": "*"},
                 "action": {"kind": "recording"}},
                {"id": "brand_new",
                 "match": {"kind": "x", "subject_glob": "*"},
                 "action": {"kind": "none"}},
            ]}, f)
        eng.tick()
        with open(eng.history.path) as f:
            entries = [json.loads(l) for l in f if l.strip()]
        promos = [e for e in entries if e["transition"] == "ruleset_promoted"]
        assert len(promos) == 1
        assert "brand_new" in promos[0]["detail"]

    def test_rollup_deep_fires_respect_window(self):
        from mini_dudeai.rollup import build_box_deep
        now = 1_000_000.0
        history = [
            {"ts": now - 100, "iso": "recent", "transition": "edge_up",
             "rule_id": "r1", "subject": "s", "detail": ""},
            {"ts": now - 90000, "iso": "old", "transition": "edge_up",
             "rule_id": "r2", "subject": "s", "detail": ""},  # > 24h old
        ]
        posture = build_box_deep("box", {"last_tick_ts": now - 5}, history, now)
        assert [f["rule_id"] for f in posture["fires"]] == ["r1"]

    def test_collect_local_distinguishes_corrupt_from_absent(self, tmp_path):
        from mini_dudeai.rollup import collect_local
        absent = str(tmp_path / "nope.json")
        assert collect_local(1000.0, state_path=absent) is None
        corrupt = str(tmp_path / "state.json")
        with open(corrupt, "w") as f:
            f.write("{ torn")
        posture = collect_local(1000.0, state_path=corrupt)
        assert posture is not None
        assert "unreadable" in posture.get("error", "")

    def test_extractor_sources_share_shape(self, tmp_path):
        """json_file and http_json are now one template — same source_error
        shape, same projection."""
        from mini_dudeai.sources import ExtractorSource, JsonFileSource
        src = JsonFileSource(path=str(tmp_path / "missing.json"), kind="k",
                             extractor=lambda d: d)
        assert isinstance(src, ExtractorSource)
        conds = list(src.collect())
        assert len(conds) == 1 and conds[0].kind == "source_error"

    def test_pending_deltas_count_cached_by_mtime(self, tmp_path):
        from mini_dudeai import dreams
        path = str(tmp_path / "deltas.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"key": "k1", "status": "proposed"}) + "\n")
        assert dreams.count_pending_deltas(path) == 1
        # Cache hit: same mtime → no reparse (poke the cache to prove the
        # cached value is what's served).
        mtime = os.stat(path).st_mtime_ns
        dreams._PENDING_COUNT_CACHE[path] = (mtime, 99)
        assert dreams.count_pending_deltas(path) == 99
        # File change → cache invalidated, real count returns.
        os.utime(path, ns=(mtime + 1_000_000, mtime + 1_000_000))
        assert dreams.count_pending_deltas(path) == 1


class TestProbeAlignment:
    def test_role_seed_map_covers_mini_running_roles(self):
        from utils.watchdog_probes import _ROLE_TO_MINI_SEED
        for role in ("primary", "full-gateway", "gateway-only",
                     "collector", "cloud-publisher"):
            assert role in _ROLE_TO_MINI_SEED, (
                f"role {role!r} runs mini but has no seed mapping — "
                f"probe_rules_seed_drift is inert on those boxes")

    def test_memory_index_limit_matches_writer(self):
        from mini_dudeai.memory_apply import MEMORY_INDEX_SOFT_LIMIT_BYTES
        from utils.watchdog_probes import MEMORY_INDEX_LIMIT_BYTES
        assert MEMORY_INDEX_LIMIT_BYTES == MEMORY_INDEX_SOFT_LIMIT_BYTES, (
            "writer (memory_apply) and watcher (probe_memory_index_oversize) "
            "judge MEMORY.md against different limits")


class TestStateUnreadableRefusesToWipe:
    """A degraded read of an EXISTING state file must not read as first-boot.

    StateStore.load() used to discard read_json's error and return
    {"rules": {}} on ANY failure — byte-identical to a fresh box. Because the
    engine re-saves state every tick, a single transient read OSError (SD-card
    EIO on these Pis) would mass-deactivate every live edge (false 'recovered'),
    drop the pending_sends crash-retry queue (#81), then PERSIST the wipe over
    the atomically-written good file. load() now raises StateLoadError on a real
    read/parse error of an EXISTING file; genuine absence still = first-boot
    empty, so the tick aborts before save() and the good file survives.
    """

    def test_missing_state_file_is_first_boot_empty(self, tmp_path):
        from mini_dudeai.state import StateStore
        store = StateStore(str(tmp_path / "nope.json"))
        assert store.load() == {"rules": {}}  # genuine absence = first boot

    def test_corrupt_state_file_raises_not_empty(self, tmp_path):
        from mini_dudeai.state import StateStore, StateLoadError
        p = tmp_path / "state.json"
        p.write_text("{ torn json")  # readable path, unparseable content
        with pytest.raises(StateLoadError):
            StateStore(str(p)).load()

    def test_state_load_error_is_oserror(self):
        # _reset_pending_streaks catches OSError and run()'s except Exception
        # catches everything — subclassing OSError keeps BOTH paths graceful.
        from mini_dudeai.state import StateLoadError
        assert issubclass(StateLoadError, OSError)

    def test_corrupt_state_does_not_clobber_good_file_via_tick(self, tmp_path):
        from mini_dudeai.state import StateLoadError
        src = FlippableSource([COND])
        eng, action = make_engine(tmp_path, src)
        eng.tick()  # edge_up writes a good state file
        assert "peer_down::moc9" in open(eng.state_store.path).read()
        with open(eng.state_store.path, "w") as f:
            f.write("{ torn json")  # transient torn/corrupt read class
        # load() is the FIRST line of tick(), so the raise precedes save():
        # the good file is never overwritten with an empty state, and the
        # raise IS the operator-visible witness (run() logs it + ticks stall).
        with pytest.raises(StateLoadError):
            eng.tick()
        assert open(eng.state_store.path).read() == "{ torn json"  # not wiped
