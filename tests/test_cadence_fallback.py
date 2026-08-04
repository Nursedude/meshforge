"""Tests for the local-tier cadence fallback (W1 — mini_dudeai.cadence_fallback).

The invariants under test are the HONESTY ones, not the LLM's quality:
a local product is always stamped brain_tier and never_ratifies; a failed
LLM degrades to a deterministic rules-tier note (never a fabricated triage);
the model cannot invent backlog (unknown keys dropped); and the module
never touches ratification machinery (source-scan tripwire).
"""
from __future__ import annotations

import inspect
import json
import os

import pytest

from mini_dudeai import cadence_fallback as cf
from mini_dudeai.brief import build_brief
from mini_dudeai.chat_compiler import CompilerError

NOW = 1_800_000_000.0


def _delta(key, status="proposed", summary="s"):
    return {"ts": NOW - 100, "kind": "escalation", "key": key,
            "status": status, "summary": summary}


def _deltas_file(tmp_path, deltas, torn_tail=False):
    p = tmp_path / "deltas.jsonl"
    text = "\n".join(json.dumps(d) for d in deltas)
    if torn_tail:
        text += '\n{"ts": 1, "key": "torn'
    p.write_text(text + "\n")
    return str(p)


class FakeBackend:
    model = "fake:3b"

    def __init__(self, reply=None, exc=None):
        self.reply = reply
        self.exc = exc
        self.calls = []

    def complete(self, system, user, fmt="json"):
        self.calls.append((system, user, fmt))
        if self.exc:
            raise self.exc
        return self.reply


def _good_reply(keys):
    return json.dumps({
        "summary": "backlog looks routine",
        "deltas": [{"key": k, "assessment": f"about {k}",
                    "suggested_disposition": "needs-live-check"}
                   for k in keys],
    })


class TestLoadProposedDeltas:
    def test_filters_status_caps_and_counts_total(self, tmp_path):
        deltas = [_delta(f"k{i}") for i in range(5)] + [
            _delta("ratified1", status="ratified")]
        path = _deltas_file(tmp_path, deltas)
        got, total = cf.load_proposed_deltas(path, cap=3)
        assert [d["key"] for d in got] == ["k0", "k1", "k2"]
        assert total == 5  # cap limits the fed set, never the honest count

    def test_torn_tail_is_skipped_not_fatal(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")], torn_tail=True)
        got, total = cf.load_proposed_deltas(path)
        assert [d["key"] for d in got] == ["a"] and total == 1

    def test_missing_file_is_zero(self, tmp_path):
        assert cf.load_proposed_deltas(str(tmp_path / "nope")) == ([], 0)


class TestRun:
    def test_local_triage_is_stamped_and_bounded(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a"), _delta("b")])
        be = FakeBackend(reply=_good_reply(["a", "b"]))
        w = cf.run(path, be, frontier_rc=1, now=NOW)
        assert w["brain_tier"] == "local"
        assert w["never_ratifies"] is True
        assert w["frontier_rc"] == 1
        assert w["triaged"] == 2 and w["proposed_total"] == 2
        assert w["model"] == "fake:3b"
        # the schema went down the wire (constrained decoding, not hope)
        assert be.calls[0][2] == cf.TRIAGE_SCHEMA

    def test_llm_failure_degrades_to_rules_note(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")])
        be = FakeBackend(exc=CompilerError("ollama down"))
        w = cf.run(path, be, frontier_rc=None, now=NOW)
        assert w["brain_tier"] == "rules"
        assert w["triaged"] == 0 and w["deltas"] == []
        assert "ollama down" in w["error"]
        assert "pending" in w["summary"]  # honest: backlog exists, untriaged

    def test_garbage_reply_degrades_not_fabricates(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")])
        w = cf.run(path, FakeBackend(reply="not json {"), frontier_rc=2,
                   now=NOW)
        assert w["brain_tier"] == "rules" and "error" in w

    def test_invented_keys_are_dropped(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("real")])
        reply = json.dumps({"summary": "ok", "deltas": [
            {"key": "real", "assessment": "fine",
             "suggested_disposition": "looks-ratifiable"},
            {"key": "invented", "assessment": "??",
             "suggested_disposition": "looks-ratifiable"},
        ]})
        w = cf.run(path, FakeBackend(reply=reply), frontier_rc=1, now=NOW)
        assert w["brain_tier"] == "local"
        assert [d["key"] for d in w["deltas"]] == ["real"]
        assert w["dropped_entries"] == 1

    def test_all_entries_invalid_is_a_failure_not_empty_success(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("real")])
        reply = json.dumps({"summary": "ok", "deltas": [
            {"key": "invented", "assessment": "x",
             "suggested_disposition": "looks-ratifiable"}]})
        w = cf.run(path, FakeBackend(reply=reply), frontier_rc=1, now=NOW)
        assert w["brain_tier"] == "rules" and "error" in w

    def test_assessment_clamped(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")])
        reply = json.dumps({"summary": "ok", "deltas": [
            {"key": "a", "assessment": "x" * 900,
             "suggested_disposition": "needs-live-check"}]})
        w = cf.run(path, FakeBackend(reply=reply), frontier_rc=1, now=NOW)
        assert len(w["deltas"][0]["assessment"]) == 300

    def test_no_proposed_deltas_is_a_rules_note(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("done", status="ratified")])
        be = FakeBackend(reply=_good_reply([]))
        w = cf.run(path, be, frontier_rc=None, now=NOW)
        assert w["brain_tier"] == "rules" and w["proposed_total"] == 0
        assert be.calls == []  # no backlog -> no token spend


class _ChunkBackend:
    """Backend that answers each call with ONLY the keys it was fed.

    The module-level FakeBackend returns one canned reply regardless of input,
    which cannot distinguish "triaged in one batch" from "triaged in slices" —
    so it would pass either way. This one replies per fed set, which is what
    makes the chunking assertions below mean anything.
    """

    model = "fake:3b"

    def __init__(self, fail_keys=None, exc_for=None, durations=None):
        self.calls = []
        self.prompts = []
        self.fail_keys = set(fail_keys or ())      # -> unusable reply (content)
        self.exc_for = set(exc_for or ())          # -> CompilerError (transport)
        self.durations = list(durations or [])
        self.clock = 0.0

    def monotonic(self):
        return self.clock

    def complete(self, system, user, fmt="json"):
        keys = [ln.split('key="', 1)[1].split('"', 1)[0]
                for ln in user.splitlines()
                if ln[:1].isdigit() and 'key="' in ln]
        self.calls.append(keys)
        self.prompts.append(user)
        self.clock += (self.durations.pop(0) if self.durations else 1.0)
        if self.exc_for & set(keys):
            raise CompilerError("ollama down")
        if self.fail_keys & set(keys):
            return "not json {"
        return _good_reply(keys)


class TestChunkedTriage:
    """2026-08-04: a 4-delta backlog was fed as ONE call, hit the client bound,
    and banked NOTHING three tries running — on exactly the busy night the
    fallback exists for. Measured A/B on the real backlog: 0/4 as a batch vs
    4/4 one-at-a-time. These pin the partial-credit contract."""

    def test_each_delta_is_its_own_call_and_all_land(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a"), _delta("b"), _delta("c")])
        be = _ChunkBackend()
        w = cf.run(path, be, frontier_rc=1, now=NOW, monotonic=be.monotonic)
        assert be.calls == [["a"], ["b"], ["c"]]
        assert [d["key"] for d in w["deltas"]] == ["a", "b", "c"]
        assert w["triaged"] == 3 and w["proposed_total"] == 3

    def test_budget_stops_starting_chunks_and_banks_what_landed(self, tmp_path):
        """THE regression: crossing the wall must cost only the deltas not yet
        reached, never the ones already triaged."""
        path = _deltas_file(tmp_path, [_delta(k) for k in "abcd"])
        be = _ChunkBackend(durations=[100.0, 100.0, 100.0, 100.0])
        w = cf.run(path, be, frontier_rc=1, now=NOW, budget_s=150.0,
                   monotonic=be.monotonic)
        # a runs (nothing banked yet), b starts at 100 < 150, c would start at
        # 200 >= 150 and does not.
        assert be.calls == [["a"], ["b"]]
        assert w["brain_tier"] == "local"
        assert w["triaged"] == 2 and w["proposed_total"] == 4
        assert "partial" in w["summary"]

    def test_first_chunk_always_runs_even_with_a_spent_budget(self, tmp_path):
        """A budget of zero must still buy the attempt today's code makes —
        the cure may never do LESS than the defect it replaces."""
        path = _deltas_file(tmp_path, [_delta("a"), _delta("b")])
        be = _ChunkBackend()
        w = cf.run(path, be, frontier_rc=1, now=NOW, budget_s=0.0,
                   monotonic=be.monotonic)
        assert be.calls == [["a"]] and w["triaged"] == 1

    def test_transport_failure_stops_the_run(self, tmp_path):
        """A wedged tier must not be asked N more times: each retry pays a full
        client timeout, and paying them is how a run overruns the outer wall
        and loses its witness entirely."""
        path = _deltas_file(tmp_path, [_delta(k) for k in "abcd"])
        be = _ChunkBackend(exc_for={"b"})
        w = cf.run(path, be, frontier_rc=1, now=NOW, monotonic=be.monotonic)
        assert be.calls == [["a"], ["b"]]           # c and d never attempted
        assert w["triaged"] == 1 and w["brain_tier"] == "local"
        assert "partial" in w["summary"]

    def test_content_failure_continues_to_the_next_delta(self, tmp_path):
        """The mirror case, and the reason the two are not one branch: the tier
        ANSWERED, so a malformed reply about b says nothing about c."""
        path = _deltas_file(tmp_path, [_delta(k) for k in "abcd"])
        be = _ChunkBackend(fail_keys={"b"})
        w = cf.run(path, be, frontier_rc=1, now=NOW, monotonic=be.monotonic)
        assert be.calls == [["a"], ["b"], ["c"], ["d"]]
        assert [d["key"] for d in w["deltas"]] == ["a", "c", "d"]
        assert w["triaged"] == 3 and w["proposed_total"] == 4
        # and the missing delta is not a silent subtraction: 3/4 says HOW MANY,
        # this says WHY (hfm #9).
        assert "partial" in w["summary"] and "unusable" in w["summary"]

    def test_total_transport_failure_still_degrades_to_rules(self, tmp_path):
        """Unchanged contract: nothing landed anywhere -> an honest untriaged
        note, never an empty-but-successful-looking triage (#80)."""
        path = _deltas_file(tmp_path, [_delta("a"), _delta("b")])
        be = _ChunkBackend(exc_for={"a"})
        w = cf.run(path, be, frontier_rc=None, now=NOW, monotonic=be.monotonic)
        assert w["brain_tier"] == "rules" and w["triaged"] == 0
        assert w["deltas"] == [] and "pending" in w["summary"]
        assert "ollama down" in w["error"]

    def test_chunk_context_is_sliced_to_the_chunk(self, tmp_path, monkeypatch):
        """A 1-delta call must not carry the WHOLE backlog's excerpts — the
        prompt is KV cache and KV cache is RAM on a 905 MB box. Grounding is
        still retrieved ONCE for the run; only the rendering is per chunk."""
        path = _deltas_file(tmp_path, [_delta("a"), _delta("b")])
        be = _ChunkBackend()
        ctx = {"a": [{"path": "p/a.md", "heading": "A", "text": "excerpt-a"}],
               "b": [{"path": "p/b.md", "heading": "B", "text": "excerpt-b"}]}
        seen = []

        def _fake_retrieve(proposed, **kw):
            seen.append([d.get("key") for d in proposed])
            return ctx, None

        monkeypatch.setattr(cf, "retrieve_context", _fake_retrieve)
        cf.run(path, be, frontier_rc=1, now=NOW, monotonic=be.monotonic)
        assert seen == [["a", "b"]], "grounding must be retrieved once, not per chunk"
        assert "excerpt-a" in be.prompts[0] and "excerpt-b" not in be.prompts[0]
        assert "excerpt-b" in be.prompts[1] and "excerpt-a" not in be.prompts[1]


class TestTriageBounds:
    """The bound is STRUCTURAL: budget + chunk timeout + margin == wall, so a
    chunk begun at the last permitted instant still lands inside the wall
    without anyone predicting how long it will take."""

    def test_invariant_holds_for_the_default_wall(self):
        wall, chunk_timeout, budget = cf.triage_bounds({})
        assert wall == cf.LOCAL_TRIAGE_WALL_DEFAULT_S
        assert budget + chunk_timeout <= wall
        assert budget > 0                      # a second chunk is reachable

    @pytest.mark.parametrize("wall", ["120", "600", "3600", "45"])
    def test_invariant_holds_for_any_operator_wall(self, wall):
        w, chunk_timeout, budget = cf.triage_bounds(
            {"MINI_CADENCE_LOCAL_TIMEOUT_S": wall})
        assert w == float(wall)
        assert budget + chunk_timeout <= w

    @pytest.mark.parametrize("bad", ["", "abc", "0", "-5", None])
    def test_garbage_override_falls_back_to_the_shell_default(self, bad):
        env = {} if bad is None else {"MINI_CADENCE_LOCAL_TIMEOUT_S": bad}
        wall, _, _ = cf.triage_bounds(env)
        assert wall == cf.LOCAL_TRIAGE_WALL_DEFAULT_S

    def test_wall_default_matches_the_launcher(self):
        """hfm #5, and the exact shape that caused this bug: two consumers of
        one number must not hardcode it twice. If the launcher's timeout moves,
        this fails until the module follows."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(cf.__file__)))
        sh = os.path.join(os.path.dirname(repo), "scripts",
                          "mini_cadence_launch.sh")
        text = open(sh, encoding="utf-8").read()
        assert "MINI_CADENCE_LOCAL_TIMEOUT_S:-600" in text, (
            "launcher's local-tier wall changed; update "
            "LOCAL_TRIAGE_WALL_DEFAULT_S to match")
        assert cf.LOCAL_TRIAGE_WALL_DEFAULT_S == 600.0


class TestNeverRatifiesTripwire:
    def test_module_never_imports_ratification_machinery(self):
        # The fallback's hard invariant, pinned at the source level: the ONLY
        # write is the triage witness. If someone wires memory_apply, rule
        # candidates, or delta resolution into this module, this fails first.
        src = inspect.getsource(cf)
        for forbidden in ("memory_apply", "write_candidate", "resolve_delta",
                          "merge_seed_rules"):
            assert forbidden not in src, (
                f"cadence_fallback must never touch {forbidden} — "
                f"the local tier triages, the frontier ratifies")


class TestMainCli:
    def test_main_writes_witness_and_exits_zero(self, tmp_path, monkeypatch):
        deltas = _deltas_file(tmp_path, [_delta("a")])
        out = tmp_path / "witness.json"
        fake = FakeBackend(reply=_good_reply(["a"]))
        monkeypatch.setattr(cf, "OllamaBackend", lambda **k: fake)
        rc = cf.main(["--deltas", deltas, "--out", str(out),
                      "--frontier-rc", "7"])
        assert rc == 0
        w = json.loads(out.read_text())
        assert w["brain_tier"] == "local" and w["frontier_rc"] == 7

    def test_main_cli_missing_frontier_rc_is_null(self, tmp_path, monkeypatch):
        deltas = _deltas_file(tmp_path, [_delta("a")])
        out = tmp_path / "witness.json"
        monkeypatch.setattr(cf, "OllamaBackend",
                            lambda **k: FakeBackend(reply=_good_reply(["a"])))
        assert cf.main(["--deltas", deltas, "--out", str(out),
                        "--frontier-rc", ""]) == 0
        assert json.loads(out.read_text())["frontier_rc"] is None


class TestBriefSection:
    def _witness(self, tier="local", ts=NOW - 60, frc=1):
        return {"ts": ts, "brain_tier": tier, "frontier_rc": frc,
                "proposed_total": 3, "triaged": 3 if tier == "local" else 0,
                "model": "fake:3b", "summary": "backlog looks routine",
                "never_ratifies": True}

    def test_local_tier_section_renders(self):
        text = build_brief({}, [], NOW, cadence_triage=self._witness())
        assert "cadence ran on LOCAL tier" in text
        assert "SUGGESTIONS ONLY, nothing ratified" in text
        assert "frontier rc=1" in text

    def test_rules_tier_section_renders(self):
        text = build_brief({}, [], NOW,
                           cadence_triage=self._witness(tier="rules", frc=None))
        assert "fell to RULES tier" in text
        assert "UNTRIAGED" in text
        assert "claude CLI missing" in text

    def test_stale_witness_renders_nothing(self):
        old = self._witness(ts=NOW - cf.TRIAGE_FRESH_S - 1)
        text = build_brief({}, [], NOW, cadence_triage=old)
        assert "LOCAL tier" not in text

    def test_absent_witness_renders_nothing(self):
        text = build_brief({}, [], NOW, cadence_triage=None)
        assert "LOCAL tier" not in text

    def test_future_ts_witness_renders_nothing(self):
        # A clock step must not forge freshness (RTC-less discipline).
        w = self._witness(ts=NOW + 3600)
        assert "LOCAL tier" not in build_brief({}, [], NOW, cadence_triage=w)

    def test_pre_score_renders_orientation_not_outage(self):
        # Increment 3: a pre-score is the loop working, not a frontier outage —
        # it must NOT be shown with "frontier rc" / "claude CLI missing".
        w = self._witness()
        w["mode"] = "pre-score"
        w["frontier_rc"] = None            # frontier hasn't run yet
        text = build_brief({}, [], NOW, cadence_triage=w)
        assert "PRE-SCORED the backlog" in text
        assert "feeding the frontier pass" in text
        assert "SUGGESTIONS ONLY, nothing ratified" in text
        assert "claude CLI missing" not in text and "frontier rc" not in text

    def test_fallback_mode_still_renders_silver_line(self):
        w = self._witness()
        w["mode"] = "fallback"
        text = build_brief({}, [], NOW, cadence_triage=w)
        assert "cadence ran on LOCAL tier" in text and "frontier rc=1" in text


class TestClearFlag:
    def test_clear_retires_witness_and_is_idempotent(self, tmp_path):
        out = tmp_path / "witness.json"
        out.write_text("{}")
        assert cf.main(["--clear", "--out", str(out)]) == 0
        assert not out.exists()
        # second clear: nothing to retire is a clean no-op, not an error
        assert cf.main(["--clear", "--out", str(out)]) == 0

    def test_clear_never_runs_the_llm(self, tmp_path, monkeypatch):
        boom = lambda **k: (_ for _ in ()).throw(AssertionError("LLM built"))
        monkeypatch.setattr(cf, "OllamaBackend", boom)
        assert cf.main(["--clear", "--out", str(tmp_path / "w.json")]) == 0


class TestDuplicateKeyDedup:
    def test_duplicate_keys_dropped_not_double_counted(self, tmp_path):
        """2026-07-04 review fix: a model repeating delta A while skipping B
        must read triaged=1 (dup counted as a drop), not 2 — dup inflation
        reached brief.py's 'N/M triaged' line and the eval coverage gate as
        a false full-coverage claim."""
        path = _deltas_file(tmp_path, [_delta("a"), _delta("b")])
        reply = json.dumps({"summary": "ok", "deltas": [
            {"key": "a", "assessment": "x",
             "suggested_disposition": "needs-live-check"},
            {"key": "a", "assessment": "again",
             "suggested_disposition": "needs-live-check"},
        ]})
        w = cf.run(path, FakeBackend(reply=reply), frontier_rc=1, now=NOW)
        assert w["brain_tier"] == "local"
        assert [d["key"] for d in w["deltas"]] == ["a"]
        assert w["triaged"] == 1
        assert w["dropped_entries"] == 1


class TestMode:
    """Increment 3 (WS-C): the local triage records WHY it ran — pre-score
    (frontier will consume it) vs fallback (frontier gone). Both only suggest."""

    def test_mode_defaults_to_fallback(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")])
        w = cf.run(path, FakeBackend(reply=_good_reply(["a"])),
                   frontier_rc=1, now=NOW)
        assert w["mode"] == "fallback"

    def test_pre_score_mode_stamped_and_never_ratifies(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("a")])
        w = cf.run(path, FakeBackend(reply=_good_reply(["a"])),
                   frontier_rc=None, now=NOW, mode="pre-score")
        assert w["mode"] == "pre-score"
        assert w["brain_tier"] == "local" and w["never_ratifies"] is True

    def test_invalid_mode_refused_loud(self, tmp_path):
        # 07-23 audit (hfm #3): a typo'd library-caller mode silently becoming
        # "fallback" rendered in the brief as a frontier outage that never
        # happened — reject what the author cannot have meant.
        path = _deltas_file(tmp_path, [_delta("a")])
        with pytest.raises(ValueError, match="unknown triage mode"):
            cf.run(path, FakeBackend(reply=_good_reply(["a"])),
                   frontier_rc=1, now=NOW, mode="bogus")

    def test_mode_carried_on_empty_backlog_note(self, tmp_path):
        # Even the deterministic no-backlog note carries the mode (base dict).
        path = _deltas_file(tmp_path, [_delta("done", status="ratified")])
        w = cf.run(path, FakeBackend(reply=_good_reply([])),
                   frontier_rc=None, now=NOW, mode="pre-score")
        assert w["mode"] == "pre-score" and w["brain_tier"] == "rules"

    def test_cli_mode_flag_stamps_witness(self, tmp_path, monkeypatch):
        # The --mode flag reaches the witness through main().
        path = _deltas_file(tmp_path, [_delta("done", status="ratified")])
        out = tmp_path / "w.json"
        monkeypatch.setattr(cf, "OllamaBackend",
                            lambda **k: FakeBackend(reply=_good_reply([])))
        rc = cf.main(["--deltas", path, "--out", str(out), "--mode", "pre-score"])
        assert rc == 0
        assert json.loads(out.read_text())["mode"] == "pre-score"


class TestRetrievalGrounding:
    """The ratifier must judge against the fleet's OWN records (2026-07-24).

    WHY THIS EXISTS: the oracle path was fully grounded while THIS path — the one
    deciding what may enter long-term memory — had no retrieval at all, which is
    inverted (the gate protecting memory integrity was the ungrounded one).
    Measured before the fix: tier-L rated 3 of 5 deliberately-wrong proposals
    ``looks-ratifiable`` because the records refuting them were never shown; with
    grounding the same model+cases went 0/3 -> 3/3 first try. These tests are the
    FAST gate for that, so a future edit cannot silently blind the ratifier and
    leave only the weekly eval cron to notice.
    """

    def _corpus(self, tmp_path, name="mem.md", body=None):
        d = tmp_path / "corpus"
        d.mkdir(exist_ok=True)
        (d / name).write_text(body or (
            "# After= in a user unit\n\n"
            "An After= in a --user unit CANNOT order against a SYSTEM unit; the\n"
            "directive is silently a no-op and the boot race still happens.\n"),
            encoding="utf-8")
        return [("memory", str(d / "*.md"))]

    def test_retrieval_finds_the_refuting_record(self, tmp_path):
        roots = self._corpus(tmp_path)
        props = [_delta("nomadnet_after", summary="the unit declares "
                        "After=rnsd.service so the boot race is impossible")]
        ctx, note = cf.retrieve_context(props, roots=roots)
        assert note is None
        hits = ctx["nomadnet_after"]
        assert hits, "no grounding retrieved for a delta the corpus refutes"
        assert "no-op" in " ".join(h["text"] for h in hits)

    def test_unavailable_corpus_yields_a_NOTE_not_silent_emptiness(self, tmp_path):
        """hfm #2: unobservable must not render as 'nothing relevant exists'."""
        ctx, note = cf.retrieve_context(
            [_delta("k")], roots=[("memory", str(tmp_path / "nope" / "*.md"))])
        assert ctx == {} and note, "empty corpus must be reported, not swallowed"
        assert "corpus" in note

    def test_prompt_carries_the_excerpts(self, tmp_path):
        roots = self._corpus(tmp_path)
        props = [_delta("nomadnet_after", summary="After=rnsd.service so the "
                                                 "race cannot happen")]
        ctx, note = cf.retrieve_context(props, roots=roots)
        prompt = cf.build_user_prompt(props, ctx, note)
        assert "DOMAIN CONTEXT" in prompt and "no-op" in prompt
        assert "outrank" in prompt

    def test_missing_hits_say_absence_is_not_corroboration(self, tmp_path):
        props = [_delta("unrelated_key", summary="something with no record")]
        prompt = cf.build_user_prompt(props, {"unrelated_key": []}, None)
        assert "NOT corroboration" in prompt
        assert "needs-live-check" in prompt

    def test_unavailable_context_is_stated_in_the_prompt(self):
        prompt = cf.build_user_prompt([_delta("k")], {}, "corpus unreadable")
        assert "DOMAIN CONTEXT UNAVAILABLE" in prompt
        assert "judging without" in prompt

    def test_no_context_arg_keeps_the_historic_prompt_byte_identical(self):
        props = [_delta("a"), _delta("b")]
        assert cf.build_user_prompt(props) == cf.build_user_prompt(props, None, None)
        assert "DOMAIN CONTEXT" not in cf.build_user_prompt(props)

    def test_system_prompt_forbids_ratifying_on_internal_consistency_alone(self):
        assert "INTERNAL CONSISTENCY IS NOT ENOUGH" in cf._SYSTEM_PROMPT
        assert "Unverified is not ratifiable" in cf._SYSTEM_PROMPT

    def test_run_grounds_the_prompt_and_witnesses_it(self, tmp_path):
        """The production path must actually reach the backend grounded, and the
        witness must SAY whether it did — a blind triage cannot look identical to
        a grounded one downstream."""
        roots = self._corpus(tmp_path)
        path = _deltas_file(tmp_path, [
            _delta("nomadnet_after", summary="After=rnsd.service so the race "
                                             "cannot happen")])
        be = FakeBackend(reply=_good_reply(["nomadnet_after"]))
        monkey = cf.retrieve_context
        try:
            cf.retrieve_context = lambda props, **kw: monkey(props, roots=roots)
            w = cf.run(path, be, frontier_rc=1, now=NOW)
        finally:
            cf.retrieve_context = monkey
        assert w["context_grounded_keys"] == 1
        assert w["context_note"] is None
        sent_user = be.calls[0][1]
        assert "DOMAIN CONTEXT" in sent_user and "no-op" in sent_user

    def test_witness_records_blind_triage_when_grounding_fails(self, tmp_path):
        path = _deltas_file(tmp_path, [_delta("k")])
        be = FakeBackend(reply=_good_reply(["k"]))
        monkey = cf.retrieve_context
        try:
            cf.retrieve_context = lambda props, **kw: ({}, "corpus unreadable")
            w = cf.run(path, be, frontier_rc=1, now=NOW)
        finally:
            cf.retrieve_context = monkey
        assert w["context_grounded_keys"] == 0
        assert w["context_note"] == "corpus unreadable"
        assert "DOMAIN CONTEXT UNAVAILABLE" in be.calls[0][1]

    def test_grounding_never_raises_into_the_triage(self, tmp_path):
        """An UNEXPECTED grounding failure must degrade the triage to
        blind-with-a-witness, never crash the ratifier — grounding is an
        enhancement, and a probe must not die of its own bookkeeping (hfm #9).

        This test caught a real defect: the first implementation called
        retrieve_context OUTSIDE any guard, so a bug in retrieval would have
        taken down the whole ratification path. The test name said 'never
        raises' while the assertion demanded a raise — the name was right.
        """
        path = _deltas_file(tmp_path, [_delta("k")])
        be = FakeBackend(reply=_good_reply(["k"]))
        monkey = cf.retrieve_context
        try:
            def boom(props, **kw):
                raise RuntimeError("index exploded")
            cf.retrieve_context = boom
            w = cf.run(path, be, frontier_rc=1, now=NOW)
        finally:
            cf.retrieve_context = monkey
        # Triage still completed...
        assert w["triaged"] == 1
        assert w["brain_tier"] == "local"
        # ...and the failure left a witness rather than looking grounded.
        assert w["context_grounded_keys"] == 0
        assert "grounding failed" in (w["context_note"] or "")
        assert "index exploded" in w["context_note"]
        assert "DOMAIN CONTEXT UNAVAILABLE" in be.calls[0][1]

    def test_brief_surfaces_a_BLIND_triage(self):
        """The witness must have a READER: a blind triage cannot reach the next
        session looking identical to a grounded one. The first version of this
        change wrote context_note/context_grounded_keys that NOTHING read —
        a writer with no reader (hfm #4), found by reviewing my own diff."""
        w = {"ts": NOW - 60, "brain_tier": "local", "frontier_rc": 1,
             "proposed_total": 1, "triaged": 1, "model": "fake:3b",
             "summary": "s", "never_ratifies": True,
             "context_grounded_keys": 0, "context_note": "corpus unreadable"}
        text = build_brief({}, [], NOW, cadence_triage=w)
        assert "JUDGED BLIND" in text and "corpus unreadable" in text

    def test_brief_shows_grounded_key_count_when_grounded(self):
        w = {"ts": NOW - 60, "brain_tier": "local", "frontier_rc": 1,
             "proposed_total": 2, "triaged": 2, "model": "fake:3b",
             "summary": "s", "never_ratifies": True,
             "context_grounded_keys": 2, "context_note": None}
        text = build_brief({}, [], NOW, cadence_triage=w)
        assert "grounded on 2 key(s)" in text and "JUDGED BLIND" not in text
