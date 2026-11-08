"""judge_gateway_state — the ONE shape/freshness judgement for gateway state
envelopes, shared by the watchdog's file fallback and honest_status.sh's
conf_rate leg (2026-11-07; review r1 F6/F7: the gate used to re-derive codes
from the reader's English by substring).
"""
import json
import math

import pytest

from utils import watchdog_probes_gateway as g

NOW = 1_800_000_000.0


def _env(ts, body=None):
    return {"schema": 1, "host": "x", "ts": ts,
            "snapshot": {"confirmation_rate": 0.9} if body is None else body}


@pytest.mark.parametrize("wrapped,code", [
    ([], "misshaped"),
    ({"ts": NOW}, "misshaped"),
    ({"ts": NOW, "snapshot": []}, "misshaped"),
    (_env(None), "no_ts"),
    (_env("garbage"), "no_ts"),
    (_env(True), "no_ts"),
    (_env(float("nan")), "no_ts"),
    (_env(float("inf")), "no_ts"),
    (_env(NOW + g._DELIVERY_SNAPSHOT_FUTURE_SLOP_S + 1), "future"),
    (_env(NOW - g._DELIVERY_SNAPSHOT_FRESH_S - 1), "stale"),
])
def test_blind_codes(wrapped, code):
    payload, got, _age = g.judge_gateway_state(wrapped, now=NOW, key="snapshot")
    assert payload is None and got == code


def test_codes_are_the_closed_vocabulary():
    """Every code the judge can emit is declared (the gate prints them)."""
    assert set(g.GATEWAY_STATE_BLIND_CODES) == {
        "misshaped", "no_ts", "no_clock", "future", "stale"}


@pytest.mark.parametrize("now", [float("nan"), float("inf"), None, True])
def test_non_finite_now_never_testifies(now):
    """r2 F4: now=NaN made a day-old corpse read fresh."""
    payload, code, _ = g.judge_gateway_state(_env(NOW - 86400), now=now, key="snapshot")
    assert payload is None and code == "no_clock"


def test_fresh_envelope_testifies():
    payload, code, age = g.judge_gateway_state(_env(NOW - 10), now=NOW, key="snapshot")
    assert code is None and payload == {"confirmation_rate": 0.9} and age == 10


def test_file_reader_refuses_a_nan_ts(tmp_path):
    """A NaN ts made every age comparison False, so the corpse read FRESH."""
    p = tmp_path / "s.json"
    p.write_text('{"schema": 1, "host": "x", "ts": NaN, "snapshot": {}}')
    payload, why = g._read_gateway_state_file(
        state_path=str(p), api="delivery API", noun="delivery snapshot",
        key="snapshot", subpath="unused", now=NOW)
    assert payload is None and "ts missing/garbage" in why


@pytest.mark.parametrize("ts,needle", [
    (NOW - g._DELIVERY_SNAPSHOT_FRESH_S - 50, "stale ("),
    (NOW + g._DELIVERY_SNAPSHOT_FUTURE_SLOP_S + 50, "in the future"),
])
def test_file_reader_reasons_unchanged(tmp_path, ts, needle):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(_env(ts)))
    payload, why = g._read_gateway_state_file(
        state_path=str(p), api="delivery API", noun="delivery snapshot",
        key="snapshot", subpath="unused", now=NOW)
    assert payload is None and why.startswith("delivery API unreachable") and needle in why
