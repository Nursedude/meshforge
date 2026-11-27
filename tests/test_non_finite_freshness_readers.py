"""P3 (11-27): every freshness reader of a box/claw TICK stamp takes it through
the ONE finite rule (``utils.finite_stamp.finite``).

Found by the P2 Fable round (11-27, PRE_EXISTING, reproduced): the claw tick
reader beside ``status_blocks`` still gated on ``isinstance(ts, (int, float))``.
The sweep that followed found the same hole in the readers the operator reads
FIRST every session — the fleet watchers pane and the warm-start banner:

* NaN fails every comparison, so ``age > stale`` is False -> "fresh" / FRESH;
* ``10**400`` (valid JSON) overflows the float maths -> a raise;
* ``int(nan)`` in the human age formatter raises;
* ``True`` is an int (1970-01-01T00:00:01).

Each is a tick whose time is UNOBSERVABLE: never fresh, never a raise, never a
non-finite value echoed into JSON (NaN is not JSON).
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

NOW = 1_780_000_000.0

_NON_FINITE = [("nan", float("nan")), ("inf", float("inf")),
               ("-inf", float("-inf")), ("10**400", 10 ** 400), ("true", True)]
_IDS = [i for i, _ in _NON_FINITE]
_VALS = [v for _, v in _NON_FINITE]


def _tick_file(tick):
    return SimpleNamespace(read_text=lambda encoding="utf-8": json.dumps(tick),
                           name="claw.json")


@pytest.mark.parametrize("ts", _VALS, ids=_IDS)
def test_claw_tick_block_reads_a_non_finite_capture_as_unobservable(ts):
    from utils._map_status_endpoints import StatusEndpointsMixin
    dummy = SimpleNamespace(_CLAW_STALE_S=900.0)
    tick = {"captured_at": ts, "reachable": True, "host": "moc2",
            "device": "dudeclaw-01"}
    block = StatusEndpointsMixin._parse_claw_tick(dummy, _tick_file(tick))
    assert block["ok"] is False, block
    assert block["age_s"] is None and block["captured_at"] is None, block
    json.dumps(block, allow_nan=False)


def test_claw_tick_block_keeps_a_finite_capture():
    from utils._map_status_endpoints import StatusEndpointsMixin
    dummy = SimpleNamespace(_CLAW_STALE_S=900.0)
    ts = int(time.time()) - 10
    tick = {"captured_at": ts, "reachable": True, "host": "moc2",
            "device": "dudeclaw-01"}
    block = StatusEndpointsMixin._parse_claw_tick(dummy, _tick_file(tick))
    assert block["ok"] is True and block["captured_at"] == ts, block
    assert isinstance(block["captured_at"], int), block


@pytest.mark.parametrize("ts", _VALS, ids=_IDS)
def test_watchers_pane_claw_card_is_never_fresh_off_a_non_finite_capture(ts):
    from mini_dudeai.rollup import parse_claw_posture
    card = parse_claw_posture({"captured_at": ts, "reachable": True,
                               "device": "dudeclaw-01"}, NOW)
    assert card["status"] == "unknown", card


@pytest.mark.parametrize("ts", _VALS, ids=_IDS)
def test_watchers_pane_box_is_never_fresh_off_a_non_finite_tick(ts):
    from mini_dudeai.rollup import build_rollup, parse_state_posture
    p = parse_state_posture("moc", {"last_tick_ts": ts, "rules": {}}, NOW)
    assert p["status"] == "unknown", p
    out = build_rollup([p], NOW)
    line = next(ln for ln in out.splitlines() if "**moc**" in ln)
    assert not line.startswith("🟢") and "— fresh ·" not in line, line
    assert "UNKNOWN" in line, line


def test_watchers_pane_box_finite_tick_still_reads_fresh():
    from mini_dudeai.rollup import parse_state_posture
    p = parse_state_posture("moc", {"last_tick_ts": NOW - 20, "rules": {}}, NOW)
    assert p["status"] == "fresh", p


@pytest.mark.parametrize("ts", _VALS, ids=_IDS)
def test_warmstart_banner_is_never_fresh_off_a_non_finite_tick(tmp_path, ts):
    from mini_dudeai.warmstart import render_warmstart
    brief = tmp_path / "brief.md"
    brief.write_text("# brief\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"host": "t", "rules": {}, "last_tick_ts": ts}),
                     encoding="utf-8")
    out = render_warmstart(str(brief), str(state), NOW,
                           ledger_path=str(tmp_path / "no-ledger.json"),
                           handoff_path=str(tmp_path / "no-handoff.md"))
    assert "FRESH" not in out.split("FRESHNESS UNKNOWN")[0], out
    assert "FRESHNESS UNKNOWN" in out, out


def test_every_box_status_the_parser_can_return_is_rendered_and_counted():
    """hfm #7 closed enum, closed consumers: the P3 `unknown` status rendered
    under a header that said "no boxes" — the header kept its own list."""
    from mini_dudeai import rollup as r
    cases = {"fresh": {"last_tick_ts": NOW - 5}, "stale": {"last_tick_ts": NOW - 9999},
             "unknown": {"last_tick_ts": float("nan")}, "no_state": {}}
    for want, st in cases.items():
        p = r.parse_state_posture("moc", {**st, "rules": {}}, NOW)
        assert p["status"] == want, (want, p["status"])
        assert want in r._BANNER and want in r._ORDER, want
        head = r.build_rollup([p], NOW).split("\n", 2)[1]
        assert f"1 {want}" in head and "no boxes" not in head, head


def test_a_stray_status_is_counted_not_hidden():
    from mini_dudeai.rollup import build_rollup
    head = build_rollup([{"host": "x", "status": "martian", "self_box": False,
                         "age": "5s", "rule_count": 1, "src_errors": 0}],
                        NOW).split("\n", 2)[1]
    assert "1 martian" in head and "no boxes" not in head, head


# ── P3 r1 fold (11-27 Fable BY RUNNING, NOT CLEAN ×2) ────────────────────
# (1) "never raise" was false for the claw card / box state: device- and
# remote-supplied NON-stamp fields still took the whole pane down.
_HOSTILE_CLAW = [
    ("heap-nan", {"device_info": {"heap_free_bytes": float("nan")}}),
    ("heap-str", {"device_info": {"heap_free_bytes": "n/a"}}),
    ("heap-huge", {"device_info": {"heap_free_bytes": 10 ** 400}}),
    ("uptime-nan", {"device_info": {"uptime_s": float("nan")}}),
    ("wifi-nan", {"device_info": {"wifi_rssi_dbm": float("nan")}}),
    ("di-list", {"device_info": [1]}),
    ("ble-str", {"ble": "off"}),
    ("ble-adv-inf", {"ble": {"adv_age_s": float("inf")}}),
]


@pytest.mark.parametrize("extra", [e for _, e in _HOSTILE_CLAW],
                         ids=[i for i, _ in _HOSTILE_CLAW])
def test_one_hostile_claw_tick_never_takes_the_pane_down(extra):
    from mini_dudeai.rollup import build_rollup, parse_state_posture
    claw = {"captured_at": NOW - 30, "reachable": True, "device": "dudeclaw-01",
            **extra}
    good = parse_state_posture("moc", {"last_tick_ts": NOW - 5, "rules": {}}, NOW)
    bad = parse_state_posture("moc2", {"last_tick_ts": NOW - 5, "rules": {}}, NOW,
                              claws=[claw])
    out = build_rollup([good, bad], NOW)
    assert "**moc**" in out and "**moc2**" in out and "dudeclaw-01" in out, out
    assert "nan" not in out.lower().replace("nanoseconds", ""), out


@pytest.mark.parametrize("rules", [[1], "x", 5], ids=["list", "str", "int"])
def test_a_non_object_rules_section_never_takes_the_pane_down(rules):
    from mini_dudeai.rollup import build_rollup, parse_state_posture
    p = parse_state_posture("moc", {"last_tick_ts": NOW - 5, "rules": rules}, NOW)
    assert p["status"] == "fresh" and p["active"] == [], p
    assert "**moc**" in build_rollup([p], NOW)


@pytest.mark.parametrize("s", [float("nan"), float("inf"), 10 ** 400, "x", True],
                         ids=["nan", "inf", "10**400", "str", "true"])
def test_uptime_formatter_never_raises(s):
    from mini_dudeai.rollup import _fmt_uptime
    assert _fmt_uptime(s) == "?"


def test_an_unknown_capture_card_withholds_its_numbers():
    from mini_dudeai.rollup import _claw_line, parse_claw_posture
    card = parse_claw_posture({"captured_at": float("nan"), "reachable": True,
                               "device": "dudeclaw-01",
                               "device_info": {"uptime_s": 3600 * 60,
                                               "heap_free_bytes": 108 * 1024}}, NOW)
    line = _claw_line(card)
    assert "UNKNOWN" in line and "heap" not in line and "up " not in line, line


def test_an_unknown_box_sorts_with_the_problems_and_keeps_its_rows():
    from mini_dudeai.rollup import build_rollup, parse_state_posture
    fresh = parse_state_posture("aaa", {"last_tick_ts": NOW - 5, "rules": {}}, NOW)
    rules = {"r": {"rule_id": "ntfy_ack_stale_any", "subject": "ntfy",
                   "currently_active": True, "last_detail": "weekly ack"}}
    unk = parse_state_posture("zzz", {"last_tick_ts": float("nan"), "rules": rules,
                                      "error_count": 2}, NOW)
    out = build_rollup([fresh, unk], NOW)
    assert out.index("**zzz**") < out.index("**aaa**"), out
    zline = out[out.index("**zzz**"):]
    assert "ntfy_ack_stale_any" in zline and "src_errors" in zline, out


def test_an_unknown_box_cannot_vouch_and_still_contributes_to_the_deep_feed():
    from mini_dudeai.rollup import build_box_deep, build_deep_feed
    hist = [{"ts": NOW - 60, "transition": "edge_up", "rule_id": "r1",
             "subject": "s", "detail": "d", "iso": "x"}]
    deep = build_box_deep("zzz", {"last_tick_ts": float("nan"), "rules": {}},
                          hist, NOW)
    assert deep["status"] == "unknown"
    assert deep["fires"] and all(f["stale"] for f in deep["fires"]), deep["fires"]
    out = build_deep_feed([deep], NOW)
    assert "1 boxes reporting" in out, out


# (2) "every reader of a tick stamp" was false: the readers that RENDER a
# mini liveness verdict (TUI, oracle, audit, brief) had the same hole.
@pytest.mark.parametrize("ts", _VALS, ids=_IDS)
def test_tui_mini_posture_never_says_alive_off_a_non_finite_tick(ts):
    from handlers.mini_dudeai import _is_fresh, _posture
    st = {"last_tick_ts": ts, "rules": {}}
    assert _is_fresh(st, NOW) is False
    line = _posture(st, NOW)
    assert "🟢" not in line and "alive" not in line, line


@pytest.mark.parametrize("ts", _VALS, ids=_IDS)
def test_oracle_snapshot_mini_is_never_ok_off_a_non_finite_tick(tmp_path, ts):
    from oracle.snapshot import NocSnapshot, _fill_mini
    p = tmp_path / "mini_state.json"
    p.write_text(json.dumps({"last_tick_ts": ts, "rules": {}}), encoding="utf-8")
    snap = NocSnapshot(now=NOW)
    _fill_mini(snap, p)
    assert snap.mini_ok is False and snap.mini_age_s is None, vars(snap)


@pytest.mark.parametrize("ts", _VALS, ids=_IDS)
def test_audit_liveness_never_passes_off_a_non_finite_tick(tmp_path, ts):
    from mini_dudeai.audit import DEGRADED, AuditContext, check_liveness
    ctx = AuditContext(artifact_dir=tmp_path, now=NOW,
                       state={"last_tick_ts": ts, "rules": {}})
    r = check_liveness(ctx)
    assert r.status == DEGRADED, r
    json.dumps(r.to_dict(), allow_nan=False)


@pytest.mark.parametrize("ts", _VALS, ids=_IDS)
def test_brief_still_renders_off_a_non_finite_tick(ts):
    from mini_dudeai.brief import build_brief
    out = build_brief({"last_tick_ts": ts, "rules": {}, "host": "t"}, [], NOW)
    assert isinstance(out, str) and out, out


# P3 r2 (11-27 Fable BY RUNNING, important #1): the r1 fold fixed
# snap.mini_ok — a field NO renderer reads. The oracle's ANSWER kept saying
# "mini:0act" / "all quiet" for a NaN / absent / corrupt tick, and a `true`
# tick REGRESSED from "mini:stale…" to "all quiet". END state: the answer.
@pytest.mark.parametrize("raw", ['NaN', 'Infinity', '-Infinity', '1' + '0' * 400,
                                 'true', '"x"', None, "CORRUPT"],
                         ids=["nan", "inf", "-inf", "10**400", "true", "str",
                              "absent", "corrupt"])
def test_the_oracle_answer_never_reads_quiet_off_an_unreadable_tick(tmp_path, raw):
    from oracle.intents import answer
    from oracle.snapshot import read_snapshot
    p = tmp_path / "mini_dudeai_state.json"
    if raw == "CORRUPT":
        p.write_text("{not json", encoding="utf-8")
    elif raw is None:
        p.write_text('{"rules": {}}', encoding="utf-8")
    else:
        p.write_text('{"rules": {}, "last_tick_ts": %s}' % raw, encoding="utf-8")
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "no-wd.json",
                         status={}, nodes={}, now=NOW)
    status = answer("status", snap)
    assert "mini:0act" not in status, status
    whatsup = answer("whatsup", snap)
    assert "all quiet" not in whatsup, whatsup


def test_the_oracle_answer_still_reads_a_fresh_tick_as_quiet(tmp_path):
    from oracle.intents import answer
    from oracle.snapshot import read_snapshot
    (tmp_path / "mini_dudeai_state.json").write_text(
        '{"rules": {}, "last_tick_ts": %r}' % (NOW - 10), encoding="utf-8")
    (tmp_path / "wd.json").write_text(
        '{"ts": %r, "ok": true, "signals": []}' % (NOW - 10), encoding="utf-8")
    snap = read_snapshot(home=tmp_path, watchdog_path=tmp_path / "wd.json",
                         status={}, nodes={}, now=NOW)
    assert "mini:0act" in answer("status", snap)
    assert "all quiet" in answer("whatsup", snap)
