"""The P3 state-field SEAM (11-27): ONE normalizer, every reader, every shape.

P3 review rounds r1 and r2 (Fable, by running) found the same defect kind
twice: a reader of a REMOTE mini state document guarded the fields it had
been told about, and the next field raised the whole surface or read
healthy (``rules:[1]``, ``source_errors:[null]``, ``error_count:NaN``,
``host:5``, a history ``ts`` of ``"x"``/``10**400``). The operator rule —
same kind twice, find the seam — put every reader behind
``utils.mini_state_view``. This file is the seam's guard: each hostile shape
goes through EVERY reader that renders mini state, and each reader must

* not raise (a pane / brief / banner that is not drawn is a blind operator),
* not emit non-finite JSON (NaN is not JSON),
* not render a healthy-shaped value from an unreadable one (src_errors=0 off
  an unknown count, "never ticked" off a present tick, quiet off garbage).

A NEW reader of mini state belongs in ``_READERS``; a new hostile field in
``_HOSTILE``.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "launcher_tui"))

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


# One hostile field at a time on an otherwise fresh, healthy document.
_HOSTILE = (
    [(f"rules={i}", {"rules": v}) for i, v in
     [("list1", [1]), ("str", "x"), ("int", 5), ("row_list", {"r": [1]}),
      ("row_str", {"r": "x"})]]
    + [(f"source_errors={i}", {"source_errors": v, "error_count": 1}) for i, v in
       [("list1", [1]), ("null_item", [None]), ("int", 5), ("str", "boom"),
        ("dict", {"a": 1})]]
    + [(f"error_count={i}", {"error_count": v}) for i, v in zip(_GIDS, _GARBAGE)]
    + [(f"rule_count={i}", {"rule_count": v}) for i, v in zip(_GIDS, _GARBAGE)]
    + [(f"host={i}", {"host": v}) for i, v in
       [("int", 5), ("list", [1]), ("null", None), ("nan", float("nan"))]]
    + [(f"pending_deltas={i}", {"pending_deltas": v}) for i, v in
       [("nan", float("nan")), ("inf", float("inf")), ("str", "x")]]
    + [(f"fire_count={i}", {"fire_count": v}) for i, v in
       [("nan", float("nan")), ("10**400", 10 ** 400)]]
    + [(f"last_tick_ts={i}", {"last_tick_ts": v}) for i, v in
       zip(_GIDS, _GARBAGE)]
    + [("state=list", None), ("state=str", None)]
)
_HIDS = [i for i, _ in _HOSTILE]


def _doc(case_id, extra):
    if case_id == "state=list":
        return [1]
    if case_id == "state=str":
        return "x"
    return _base(**extra)


_HIST_TS = [("ts=" + i, v) for i, v in zip(_GIDS, _GARBAGE)] + [("ts=absent", None)]


def _hist(ts_case=None):
    good = [_esc_row(NOW - 60)]
    if ts_case is None:
        return good
    return good + [_esc_row(ts_case), 5, "x", None]


def _json_ok(o):
    json.dumps(o, allow_nan=False, default=str)


# ---- the readers ---------------------------------------------------------

def _pane(state, hist, tmp_path):
    from mini_dudeai import rollup as r
    r_render = r._render_uplink
    r._render_uplink = lambda: ""
    try:
        p = r.parse_state_posture("moc", state, NOW)
        _json_ok(p)
        good = r.parse_state_posture("aaa", _base(), NOW)
        return r.build_rollup([p, good], NOW)
    finally:
        r._render_uplink = r_render


def _local_pane(state, hist, tmp_path):
    from mini_dudeai import rollup as r
    sp = tmp_path / "state.json"
    sp.write_text(json.dumps(state), encoding="utf-8")
    p = r.collect_local(NOW, state_path=str(sp)) if hasattr(r, "collect_local") \
        else None
    if p is None:
        return ""
    _json_ok(p)
    r_render = r._render_uplink
    r._render_uplink = lambda: ""
    try:
        good = r.parse_state_posture("aaa", _base(), NOW)
        return r.build_rollup([p, good], NOW)
    finally:
        r._render_uplink = r_render


def _deep(state, hist, tmp_path):
    from mini_dudeai import rollup as r
    d = r.build_box_deep("moc", state, hist, NOW)
    _json_ok(d)
    good = r.build_box_deep("aaa", _base(), _hist(), NOW)
    return r.build_deep_feed([d, good], NOW)


def _local_deep(state, hist, tmp_path):
    from mini_dudeai import rollup as r
    sp = tmp_path / "state.json"
    hp = tmp_path / "hist.jsonl"
    sp.write_text(json.dumps(state), encoding="utf-8")
    hp.write_text("\n".join(json.dumps(h) for h in hist), encoding="utf-8")
    d = r.collect_local_deep(NOW, str(sp), str(hp))
    if d is None:
        return ""
    _json_ok(d)
    good = r.build_box_deep("aaa", _base(), _hist(), NOW)
    return r.build_deep_feed([d, good], NOW)


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


def _tui(state, hist, tmp_path):
    from handlers.mini_dudeai import _is_fresh, _posture, build_findings
    st = state if isinstance(state, dict) else {}
    _is_fresh(st, NOW)
    build_findings(st, hist, NOW)
    return _posture(st, NOW)


def _oracle(state, hist, tmp_path):
    from oracle.intents import answer
    from oracle.snapshot import read_snapshot
    (tmp_path / "mini_dudeai_state.json").write_text(json.dumps(state),
                                                     encoding="utf-8")
    (tmp_path / "wd.json").write_text(
        '{"ts": %r, "ok": true, "signals": []}' % (NOW - 10), encoding="utf-8")
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "wd.json",
                         status={}, nodes={}, now=NOW)
    return answer("status", snap) + "\n" + answer("whatsup", snap)


def _audit(state, hist, tmp_path):
    from mini_dudeai.audit import AuditContext, check_liveness, check_outcome_truth
    st = state if isinstance(state, dict) else None
    ctx = AuditContext(artifact_dir=tmp_path, now=NOW, state=st,
                       history=[h for h in hist if isinstance(h, dict)])
    out = []
    for check in (check_liveness, check_outcome_truth):
        res = check(ctx)
        _json_ok(res.to_dict())
        out.append(res.detail if hasattr(res, "detail") else str(res.to_dict()))
    return "\n".join(out)


_READERS = [("pane", _pane), ("local_pane", _local_pane), ("deep", _deep),
            ("local_deep", _local_deep), ("brief", _brief),
            ("warmstart", _warmstart), ("tui", _tui), ("oracle", _oracle),
            ("audit", _audit)]
_RIDS = [i for i, _ in _READERS]


@pytest.mark.parametrize("reader", [f for _, f in _READERS], ids=_RIDS)
@pytest.mark.parametrize("case", _HOSTILE, ids=_HIDS)
def test_no_reader_raises_or_emits_non_finite_on_a_hostile_state_field(
        reader, case, tmp_path):
    case_id, extra = case
    out = reader(_doc(case_id, extra), _hist(), tmp_path)
    assert isinstance(out, str)
    low = out.lower()
    for bad in ("nan rules", "=nan", " nan ", "inf rules", "=inf"):
        assert bad not in low, (bad, out)


@pytest.mark.parametrize("reader", [f for _, f in _READERS], ids=_RIDS)
@pytest.mark.parametrize("ts", [v for _, v in _HIST_TS], ids=[i for i, _ in _HIST_TS])
def test_no_reader_raises_on_a_hostile_history_row(reader, ts, tmp_path):
    out = reader(_base(), _hist(ts), tmp_path)
    assert isinstance(out, str)


# ---- healthy-shaped from unreadable --------------------------------------

@pytest.mark.parametrize("v", _GARBAGE, ids=_GIDS)
def test_an_unreadable_error_count_never_renders_as_zero(v, tmp_path):
    st = _base(error_count=v)
    for name, reader in (("pane", _pane), ("brief", _brief), ("tui", _tui)):
        out = reader(st, [], tmp_path)
        line = next(ln for ln in out.splitlines()
                    if "src_errors" in ln and ("**moc**" in ln or name != "pane"))
        assert "src_errors=0" not in line and "src_errors=?" in line, (name, line)
    oracle = _oracle(st, [], tmp_path)
    assert "mini:0act" not in oracle and "mini:1act" not in oracle, oracle


@pytest.mark.parametrize("v", _GARBAGE, ids=_GIDS)
def test_the_brief_never_says_never_ticked_off_a_present_tick(v):
    from mini_dudeai.brief import build_brief
    out = build_brief(_base(last_tick_ts=v), [], NOW)
    assert "never ticked" not in out and "no state yet" not in out, out
    assert "UNKNOWN" in out, out


def test_unreadable_history_stamps_are_counted_in_the_deep_feed(tmp_path):
    # _hist("x") = 1 bad stamp + 3 non-object rows; + one 10**400 stamp
    out = _deep(_base(), _hist("x") + [_esc_row(10 ** 400)], tmp_path)
    assert "unreadable" in out and "5 history row" in out, out
    clean = _deep(_base(), _hist(), tmp_path)
    assert "unreadable stamp" not in clean, clean


def test_a_hostile_state_host_never_breaks_the_local_pane_sort(tmp_path):
    out = _local_pane(_base(host=5), [], tmp_path)
    assert "**aaa**" in out, out


# The view contract itself lives in the twin-locked tests/test_mini_state_view.py.


# Fold (11-27, author self-review): a `rules:[1]` box read "fresh · 0 rules"
# and the oracle "all quiet" — healthy-shaped off a section nobody could read.
_UNREADABLE = [("rules=list", {"rules": [1]}), ("rules=row", {"rules": {"r": 5}}),
               ("error_count=nan", {"error_count": float("nan")}),
               ("source_errors=int", {"source_errors": 5})]


@pytest.mark.parametrize("extra", [e for _, e in _UNREADABLE],
                         ids=[i for i, _ in _UNREADABLE])
def test_every_verdict_reader_names_an_unreadable_field(extra, tmp_path):
    st = _base(**extra)
    pane = next(ln for ln in _pane(st, [], tmp_path).splitlines() if "**moc**" in ln)
    assert "unreadable:" in pane, pane
    assert "unreadable:" in _brief(st, [], tmp_path)
    assert "unreadable:" in _tui(st, [], tmp_path)
    oracle = _oracle(st, [], tmp_path)
    assert "all quiet" not in oracle and "0act" not in oracle, oracle


def test_the_tui_never_gives_a_clean_bill_beside_an_unreadable_field(tmp_path):
    from handlers import mini_dudeai as h
    import time
    # _render reads the REAL clock: a fixed NOW made this pass vacuously on
    # the "not fresh" branch. A live tick puts it on the clean-bill branch.
    st = _base(rules=[1], last_tick_ts=time.time() - 5)
    shown = []
    ctx = type("C", (), {})()
    ctx.dialog = type("D", (), {"msgbox": lambda self, t, m: shown.append(m)})()
    handler = h.MiniDudeaiHandler.__new__(h.MiniDudeaiHandler)
    handler.ctx = ctx
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(st), encoding="utf-8")
    orig = h._mini_paths
    h._mini_paths = lambda: (sp, tmp_path / "h.jsonl")
    try:
        h.MiniDudeaiHandler._render(handler)
    finally:
        h._mini_paths = orig
    assert shown and "✓" not in shown[0] and "NOT a clean bill" in shown[0], shown


# ---- r3 (Fable BY RUNNING, 11-27): rows + payloads, every MF reader ------
_ROW_BAD = [{"rule_id": [1]}, {"subject": {"a": 1}}, {"rule_id": float("nan")},
            {"currently_active": "yes"}, {"last_detail": [1]}]


def _esc_hist(**esc_extra):
    row = _esc_row(NOW - 60)
    row["outcome"]["extras"]["escalation"].update(esc_extra)
    return [row]


@pytest.mark.parametrize("reader", [f for _, f in _READERS], ids=_RIDS)
@pytest.mark.parametrize("extra", _ROW_BAD,
                         ids=["rid_list", "subj_dict", "rid_nan", "active_str",
                              "detail_list"])
def test_no_reader_raises_on_a_hostile_rule_row(reader, extra, tmp_path):
    rules = {"r": {"rule_id": "r1", "subject": "s1", "currently_active": True,
                   "last_detail": "d", **extra}}
    out = reader(_base(rules=rules), _hist(), tmp_path)
    assert "nan · nan" not in out.lower(), out


@pytest.mark.parametrize("reader", [f for _, f in _READERS], ids=_RIDS)
@pytest.mark.parametrize("extra", [{"rule": [1]}, {"subject": {"a": 1}},
                                   {"detail": [1]}],
                         ids=["rule_list", "subj_dict", "detail_list"])
def test_no_reader_raises_on_a_hostile_escalation_payload(reader, extra, tmp_path):
    assert isinstance(reader(_base(), _esc_hist(**extra), tmp_path), str)


@pytest.mark.parametrize("extra", [{"error_count": 2},
                                   {"error_count": 0, "source_errors": ["boom"]},
                                   {"rules": None, "rule_count": 7}],
                         ids=["known_errors", "named_source_error", "count_no_rules"])
def test_no_surface_reads_quiet_beside_a_known_error(extra, tmp_path):
    st = _base(rules={}, **extra) if "rules" not in extra else _base(**extra)
    oracle = _oracle(st, [], tmp_path)
    assert "all quiet" not in oracle and "0act" not in oracle, oracle
    assert "Quiet" not in _brief(st, [], tmp_path)


def test_the_pane_shows_named_source_errors_whatever_the_count(tmp_path):
    for ec in (0, None, float("nan")):
        extra = {"source_errors": ["boom"]}
        if ec is not None:
            extra["error_count"] = ec
        out = _pane(_base(**extra), [], tmp_path)
        line = next(ln for ln in out.splitlines() if "**moc**" in ln)
        assert "boom" in line, (ec, line)


def test_the_oracle_never_names_the_box_off_a_hostile_host(tmp_path):
    from oracle.snapshot import read_snapshot
    (tmp_path / "mini_dudeai_state.json").write_text(
        json.dumps(_base(host=5)), encoding="utf-8")
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "no.json",
                         status={}, nodes={}, now=NOW)
    assert snap.box != 5


@pytest.mark.parametrize("v", ["x", float("nan"), 0, False],
                         ids=["str", "nan", "zero", "false"])
def test_the_tui_says_unknown_for_a_present_unreadable_tick(v):
    from handlers.mini_dudeai import _posture
    line = _posture(_base(last_tick_ts=v), NOW)
    assert "UNKNOWN" in line and "never ticked" not in line, line


def test_the_tui_clean_bill_needs_a_known_zero_error_count(tmp_path):
    import time
    from handlers import mini_dudeai as h
    for extra in ({"error_count": 2}, {"source_errors": ["boom"]}):
        st = _base(rules={}, last_tick_ts=time.time() - 5, **extra)
        shown = []
        ctx = type("C", (), {})()
        ctx.dialog = type("D", (), {"msgbox": lambda self, t, m: shown.append(m)})()
        handler = h.MiniDudeaiHandler.__new__(h.MiniDudeaiHandler)
        handler.ctx = ctx
        sp = tmp_path / "s.json"
        sp.write_text(json.dumps(st), encoding="utf-8")
        orig = h._mini_paths
        h._mini_paths = lambda: (sp, tmp_path / "h.jsonl")
        try:
            h.MiniDudeaiHandler._render(handler)
        finally:
            h._mini_paths = orig
        assert shown and "✓" not in shown[0], (extra, shown)



# status_blocks (/api/status) is pinned in the twin-locked tests/test_mini_state_view.py.
