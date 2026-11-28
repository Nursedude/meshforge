"""The ONE mini state reader (``utils.mini_state_view``, P3 seam 11-27) and
the readers SHARED by both twins: the warm brief, the warm-start banner and
the audit. Byte-locked with MeshAnchor beside the modules it pins
(parity_check BYTE_IDENTICAL) — the app-specific readers (MeshForge's pane,
TUI, oracle) are pinned in each repo's own reader test.

Each hostile field goes through each shared reader, which must not raise,
must not emit non-finite JSON, and must not render a healthy-shaped value
off an unreadable one (src_errors=0 off an unknown count, "never ticked" off
a present tick).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

NOW = 1_780_000_000.0
_GARBAGE = [float("nan"), float("inf"), 10 ** 400, "x", True, [1], {"a": 1}]
_GIDS = ["nan", "inf", "10**400", "str", "true", "list", "dict"]
_ACTIVE = {"r": {"rule_id": "ntfy_ack_stale_any", "subject": "ntfy",
                 "currently_active": True, "last_detail": "weekly ack"}}


def _esc_row(ts):
    return {"ts": ts, "transition": "edge_up", "rule_id": "ntfy_ack_stale_any",
            "subject": "ntfy", "detail": "d", "iso": "2026-11-27T10:00:00",
            "outcome": {"extras": {"escalation": {
                "rule": "ntfy_ack_stale_any", "subject": "ntfy",
                "detail": "weekly ack"}}}}


def _base(**kw):
    return {"host": "t", "last_tick_ts": NOW - 5, "rules": dict(_ACTIVE),
            "error_count": 0, "source_errors": [], **kw}


_HOSTILE = (
    [(f"rules={i}", {"rules": v}) for i, v in
     [("list1", [1]), ("str", "x"), ("int", 5), ("row_list", {"r": [1]})]]
    + [(f"source_errors={i}", {"source_errors": v, "error_count": 1}) for i, v in
       [("list1", [1]), ("null_item", [None]), ("int", 5), ("str", "boom")]]
    + [(f"error_count={i}", {"error_count": v}) for i, v in zip(_GIDS, _GARBAGE)]
    + [(f"rule_count={i}", {"rule_count": v}) for i, v in zip(_GIDS, _GARBAGE)]
    + [(f"host={i}", {"host": v}) for i, v in [("int", 5), ("list", [1])]]
    + [(f"fire_count={i}", {"fire_count": v}) for i, v in
       [("nan", float("nan")), ("10**400", 10 ** 400)]]
    + [(f"last_tick_ts={i}", {"last_tick_ts": v}) for i, v in
       zip(_GIDS, _GARBAGE)]
)
_HIDS = [i for i, _ in _HOSTILE]
_BAD_HIST = [_esc_row(NOW - 60), _esc_row("x"), _esc_row(10 ** 400),
             _esc_row([1]), _esc_row(float("nan")),
             {"ts": NOW - 30, "outcome": "x"}, 5, None, "x"]


def _brief(state, hist, tmp_path):
    from mini_dudeai.brief import build_brief
    return build_brief(state, hist, NOW)


def _warmstart(state, hist, tmp_path):
    from mini_dudeai.warmstart import render_warmstart
    bp = tmp_path / "brief.md"
    bp.write_text("# brief\n", encoding="utf-8")
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps(state), encoding="utf-8")
    return render_warmstart(str(bp), str(sp), NOW,
                            ledger_path=str(tmp_path / "no-ledger.json"),
                            handoff_path=str(tmp_path / "no-handoff.md"))


def _audit(state, hist, tmp_path):
    from mini_dudeai.audit import AuditContext, check_liveness, check_outcome_truth
    ctx = AuditContext(artifact_dir=tmp_path, now=NOW, state=state,
                       history=[h for h in hist if isinstance(h, dict)])
    out = []
    for check in (check_liveness, check_outcome_truth):
        res = check(ctx)
        json.dumps(res.to_dict(), allow_nan=False)
        out.append(res.detail)
    return "\n".join(out)


_READERS = [("brief", _brief), ("warmstart", _warmstart), ("audit", _audit)]


@pytest.mark.parametrize("reader", [f for _, f in _READERS],
                         ids=[i for i, _ in _READERS])
@pytest.mark.parametrize("case", _HOSTILE, ids=_HIDS)
def test_no_shared_reader_raises_or_echoes_non_finite(reader, case, tmp_path):
    out = reader(_base(**case[1]), [_esc_row(NOW - 60)], tmp_path)
    low = out.lower()
    for bad in ("nan rules", "=nan", " nan ", "inf rules", "=inf"):
        assert bad not in low, (bad, out)


@pytest.mark.parametrize("reader", [f for _, f in _READERS],
                         ids=[i for i, _ in _READERS])
def test_no_shared_reader_raises_on_hostile_history_rows(reader, tmp_path):
    assert isinstance(reader(_base(), list(_BAD_HIST), tmp_path), str)


def test_untimed_escalations_are_kept_not_dropped():
    """NaN silently DROPPED the row (NaN < cutoff is False, then sorted
    anywhere); "x" raised. Unreadable = untimed = kept, like absent."""
    from mini_dudeai.brief import recent_escalations
    for ts in ("x", float("nan"), 10 ** 400, [1], None):
        esc = recent_escalations([_esc_row(ts)], NOW)
        assert len(esc) == 1, (ts, esc)


@pytest.mark.parametrize("v", _GARBAGE, ids=_GIDS)
def test_an_unreadable_error_count_never_renders_as_zero_in_the_brief(v):
    from mini_dudeai.brief import build_brief
    out = build_brief(_base(error_count=v), [], NOW)
    line = next(ln for ln in out.splitlines() if "src_errors" in ln)
    assert "src_errors=0" not in line and "src_errors=?" in line, line


@pytest.mark.parametrize("v", _GARBAGE, ids=_GIDS)
def test_the_brief_never_says_never_ticked_off_a_present_tick(v):
    from mini_dudeai.brief import build_brief
    out = build_brief(_base(last_tick_ts=v), [], NOW)
    assert "never ticked" not in out and "no state yet" not in out, out
    assert "UNKNOWN" in out, out


def test_the_brief_still_says_no_state_when_the_tick_is_absent():
    from mini_dudeai.brief import build_brief
    st = _base()
    del st["last_tick_ts"]
    assert "no state yet" in build_brief(st, [], NOW)


# ---- the view itself -----------------------------------------------------

def test_the_view_contract_on_a_healthy_document():
    from utils.mini_state_view import mini_state_view
    v = mini_state_view(_base(rule_count=3, pending_deltas=2, fire_count=9))
    assert v["last_tick"] == NOW - 5 and v["tick_present"] is True
    assert v["rules"] == _ACTIVE and v["rule_count"] == 3
    assert v["error_count"] == 0 and v["source_errors"] == []
    assert v["host"] == "t" and v["pending_deltas"] == 2 and v["fire_count"] == 9


@pytest.mark.parametrize("case", _HOSTILE + [("state=list", None),
                                             ("state=str", None)],
                         ids=_HIDS + ["state=list", "state=str"])
def test_the_view_is_always_json_and_typed(case):
    from utils.mini_state_view import mini_state_view
    doc = {"state=list": [1], "state=str": "x"}.get(case[0]) or _base(**case[1])
    v = mini_state_view(doc)
    json.dumps(v, allow_nan=False)
    assert isinstance(v["rules"], dict)
    assert all(isinstance(r, dict) for r in v["rules"].values())
    assert all(isinstance(s, str) for s in v["source_errors"])
    assert isinstance(v["rule_count"], int)
    assert v["error_count"] is None or isinstance(v["error_count"], int)
    assert v["host"] is None or isinstance(v["host"], str)


def test_unreadable_sections_are_witnessed_not_absorbed():
    from utils.mini_state_view import mini_state_view
    v = mini_state_view({"rules": [1], "source_errors": 5})
    assert v["rules_unreadable"] and v["source_errors_unreadable"]
    v = mini_state_view({"rules": {"a": {}, "b": [1], "c": "x"}})
    assert v["rules_dropped"] == 2 and list(v["rules"]) == ["a"]


def test_a_present_tick_that_is_not_a_time_is_present_and_untimed():
    from utils.mini_state_view import mini_state_view
    for raw in (0, -5, False, "x", float("nan")):
        v = mini_state_view({"last_tick_ts": raw})
        assert v["tick_present"] is True and v["last_tick"] is None, raw
    v = mini_state_view({"last_tick_ts": None})
    assert v["tick_present"] is False and v["last_tick"] is None


def test_every_refused_present_field_is_named():
    from utils.mini_state_view import mini_state_view, unreadable_note
    v = mini_state_view(_base(rules=[1], error_count=float("nan"),
                              source_errors=5, rule_count="x"))
    assert v["unreadable"] == ["rules", "error_count", "source_errors",
                               "rule_count"]
    assert "unreadable: rules" in unreadable_note(v)
    clean = mini_state_view(_base())
    assert clean["unreadable"] == [] and unreadable_note(clean) == ""
    assert mini_state_view({"rules": {"a": {}, "b": 5}})["unreadable"] == ["rules"]


@pytest.mark.parametrize("rules", [[1], "x", 5, {"r": [1]}],
                         ids=["list", "str", "int", "row_list"])
def test_the_brief_names_an_unreadable_rules_section_and_never_says_quiet(rules):
    from mini_dudeai.brief import build_brief
    out = build_brief(_base(rules=rules), [], NOW)
    assert "unreadable: rules" in out, out
    assert "Quiet" not in out and "Nothing demands attention" not in out, out


def test_absent_error_count_is_zero_but_present_garbage_is_unknown():
    from utils.mini_state_view import errors_label, mini_state_view
    assert mini_state_view({})["error_count"] == 0
    for raw in (float("nan"), -1, 2.5, True, "3", None):
        v = mini_state_view({"error_count": raw})
        assert v["error_count"] is None and errors_label(v) == "?", raw
    assert mini_state_view({"error_count": 3.0})["error_count"] == 3


def test_history_view_keeps_untimed_rows_and_counts_them():
    from utils.mini_state_view import history_view
    rows, bad, dropped = history_view([{"ts": 1.0}, {"ts": "x"}, {}, 5, None])
    assert [r["ts"] for r in rows] == [1.0, None, None]
    assert bad == 1 and dropped == 2
    assert history_view("x") == ([], 0, 0)


# ---- r3 (Fable BY RUNNING, 11-27): the seam one level DOWN ---------------
# The view normalized the top-level fields and left rule ROWS and escalation
# PAYLOADS raw: a list `rule` raised `unhashable` in the brief's last-seen
# index (the engine swallows it -> the brief FREEZES behind a FRESH banner).

_ROW_HOSTILE = [("rule_id=list", {"rule_id": [1]}),
                ("subject=dict", {"subject": {"a": 1}}),
                ("rule_id=nan", {"rule_id": float("nan")}),
                ("detail=list", {"last_detail": [1]}),
                ("active=str", {"currently_active": "yes"}),
                ("active=nan", {"currently_active": float("nan")}),
                ("fc24=str", {"fire_count_24h": "x"})]
_ESC_HOSTILE = [("rule=list", {"rule": [1]}), ("subject=dict", {"subject": {"a": 1}}),
                ("detail=list", {"detail": [1]}), ("rule=nan", {"rule": float("nan")}),
                ("note=list", {"note": [1]})]


def _hostile_row(extra):
    row = {"rule_id": "r1", "subject": "s1", "currently_active": True,
           "last_detail": "d", **extra}
    return {"r1": row}


def _hostile_esc_rows(extra, transition="edge_up", legacy=False):
    esc = {"rule": "ntfy_ack_stale_any", "subject": "ntfy", "detail": "d", **extra}
    outcome = {"escalation": esc} if legacy else {"extras": {"escalation": esc}}
    return [{"ts": NOW - 60, "transition": transition, "rule_id": "x",
             "subject": "s", "detail": "d", "outcome": outcome}]


@pytest.mark.parametrize("reader", [f for _, f in _READERS],
                         ids=[i for i, _ in _READERS])
@pytest.mark.parametrize("extra", [e for _, e in _ROW_HOSTILE],
                         ids=[i for i, _ in _ROW_HOSTILE])
def test_no_shared_reader_raises_on_a_hostile_rule_row(reader, extra, tmp_path):
    out = reader(_base(rules=_hostile_row(extra)), [], tmp_path)
    assert "nan · nan" not in out.lower(), out


@pytest.mark.parametrize("legacy", [False, True], ids=["extras", "legacy"])
@pytest.mark.parametrize("transition", ["edge_up", "edge_down"])
@pytest.mark.parametrize("extra", [e for _, e in _ESC_HOSTILE],
                         ids=[i for i, _ in _ESC_HOSTILE])
def test_the_brief_never_raises_on_a_hostile_escalation_payload(extra, transition,
                                                               legacy):
    from mini_dudeai.brief import build_brief
    out = build_brief(_base(), _hostile_esc_rows(extra, transition, legacy), NOW)
    assert "nan · nan" not in out.lower(), out


def test_a_hostile_escalation_never_freezes_the_written_brief(tmp_path):
    """END state of r3 I1: write_brief must WRITE (the engine swallows a
    raise, leaving yesterday's brief behind a FRESH banner)."""
    from mini_dudeai.brief import write_brief
    sp, hp, op = tmp_path / "s.json", tmp_path / "h.jsonl", tmp_path / "b.md"
    sp.write_text(json.dumps(_base()), encoding="utf-8")
    rows = _hostile_esc_rows({"rule": [1]}) + _hostile_esc_rows({"subject": {"a": 1}})
    hp.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    text = write_brief(str(sp), str(hp), str(op), now_ts=NOW)
    assert op.read_text(encoding="utf-8") == text and "Look here first" in text


def test_a_non_list_pending_sends_and_a_non_object_escalation_never_raise():
    from mini_dudeai.brief import _escalation_of, build_brief
    rules = {"r": {**_ACTIVE["r"], "pending_sends": 5}}
    assert build_brief(_base(rules=rules), [], NOW)
    assert _escalation_of({"outcome": {"extras": {"escalation": "x"}}}) is None
    assert _escalation_of({"outcome": {"escalation": [1]}}) is None


def test_rule_rows_are_copied_hashable_and_conservative():
    from utils.mini_state_view import mini_state_view
    raw = {"rule_id": [1], "subject": float("nan"), "currently_active": "yes",
           "last_detail": [1], "fire_count_24h": "x"}
    v = mini_state_view(_base(rules={"r": raw}))
    row = v["rules"]["r"]
    hash((row["rule_id"], row["subject"]))
    assert row["currently_active"] is True          # garbled flag = ACTIVE
    assert row["fire_count_24h"] == 0 and isinstance(row["last_detail"], str)
    assert raw["rule_id"] == [1]                    # the caller's row untouched
    assert "rules" in v["unreadable"]
    clean = mini_state_view(_base())
    assert clean["rules"] == _ACTIVE and clean["unreadable"] == []


def test_a_rule_count_with_no_rules_section_is_unreadable_never_quiet():
    """r3 I4: rules null/absent + rule_count 7 read quiet on every surface."""
    from mini_dudeai.brief import build_brief
    from utils.mini_state_view import mini_state_view
    for st in (_base(rules=None, rule_count=7), {k: v for k, v in
               _base(rule_count=7).items() if k != "rules"}):
        assert "rules" in mini_state_view(st)["unreadable"], st
        out = build_brief(st, [], NOW)
        assert "Quiet" not in out and "unreadable: rules" in out, out
    assert mini_state_view(_base(rules=None, rule_count=0))["unreadable"] == []


@pytest.mark.parametrize("extra", [{"error_count": 2},
                                   {"error_count": 0, "source_errors": ["boom"]}],
                         ids=["known_errors", "named_source_error"])
def test_a_known_error_never_reads_quiet_in_the_brief(extra):
    from mini_dudeai.brief import build_brief
    out = build_brief(_base(rules={}, **extra), [], NOW)
    assert "Quiet" not in out and "Nothing demands attention" not in out, out


def test_quiet_ok_is_the_one_rule():
    from utils.mini_state_view import mini_state_view, quiet_ok
    assert quiet_ok(mini_state_view(_base()))
    for extra in ({"error_count": 2}, {"error_count": float("nan")},
                  {"source_errors": ["boom"]}, {"rules": [1]}, {"host": "a\nb"}):
        assert not quiet_ok(mini_state_view(_base(**extra))), extra


def test_a_host_is_a_printable_bounded_label():
    from utils.mini_state_view import mini_state_view
    for bad in ("a\nb **x** | 🟢 fresh", "x" * 65, 5, [1]):
        v = mini_state_view(_base(host=bad))
        assert v["host"] is None and "host" in v["unreadable"], bad
    assert mini_state_view(_base(host="moc-2.lan"))["host"] == "moc-2.lan"


def test_counts_and_texts_are_bounded():
    from utils.mini_state_view import MAX_TEXT, mini_state_view
    v = mini_state_view(_base(error_count=1e308, rule_count=10 ** 300,
                              source_errors=["e" * 20000]))
    assert v["error_count"] is None and "rule_count" in v["unreadable"]
    assert len(v["source_errors"][0]) <= MAX_TEXT


def test_history_view_copies_rows():
    from utils.mini_state_view import history_view
    rows = [{"ts": "x"}]
    history_view(rows)
    assert rows == [{"ts": "x"}]


# r3 I3: the audit certified HONEST/HEALTHY beside refused sections.
@pytest.mark.parametrize("extra", [{"rules": "x"}, {"rules": [1]},
                                   {"source_errors": {"a": 1}},
                                   {"rule_count": float("nan")},
                                   {"error_count": float("nan")},
                                   {"error_count": True}, {"error_count": "3"}],
                         ids=["rules_str", "rules_list", "se_dict", "rc_nan",
                              "ec_nan", "ec_true", "ec_str"])
def test_the_audit_never_passes_liveness_beside_an_unreadable_section(extra, tmp_path):
    from mini_dudeai.audit import PASS, AuditContext, check_liveness
    ctx = AuditContext(artifact_dir=tmp_path, now=NOW, state=_base(**extra))
    r = check_liveness(ctx)
    assert r.status != PASS and "unreadable" in r.detail, r
    json.dumps(r.to_dict(), allow_nan=False)


def test_the_audit_keeps_absent_error_count_as_none(tmp_path):
    from mini_dudeai.audit import PASS, AuditContext, check_liveness
    st = _base()
    del st["error_count"]
    r = check_liveness(AuditContext(artifact_dir=tmp_path, now=NOW, state=st))
    assert r.status == PASS and r.data["error_count"] is None, r
    assert "error_count=None" in r.detail, r


def test_the_audit_outcome_check_reads_the_count_through_the_view(tmp_path):
    from mini_dudeai.audit import AuditContext, check_outcome_truth
    hist = [{"ts": NOW - 5, "transition": "edge_up", "outcome": {"ok": True}}]
    for raw in (True, float("nan"), "3"):
        r = check_outcome_truth(AuditContext(artifact_dir=tmp_path, now=NOW,
                                             state=_base(error_count=raw),
                                             history=hist))
        assert "state_error_count" not in r.data, (raw, r.data)


# /api/status + the ssh-spool path (status_blocks) — the raw reader r3 found.
@pytest.mark.parametrize("extra", [{"rules": [1]}, {"rules": "x"},
                                   {"error_count": float("nan")},
                                   {"rule_count": float("nan"), "host": 5},
                                   {"rules": {"a": {"rule_id": "a", "fire_count_24h": "x"},
                                              "b": {"rule_id": "b", "fire_count_24h": 3}}},
                                   {"rules": {"a": {"rule_id": [1], "subject": "s",
                                                    "currently_active": True}}}],
                         ids=["rules_list", "rules_str", "ec_nan", "rc_nan_host",
                              "fc24_mixed", "rid_list"])
def test_the_status_block_never_raises_or_echoes_non_finite(extra):
    from utils.status_blocks import mini_block_from_payload
    b = mini_block_from_payload(_base(**extra), now=NOW)
    json.dumps(b, allow_nan=False)
    assert b.get("unreadable"), b
