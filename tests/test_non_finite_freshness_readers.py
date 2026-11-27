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
