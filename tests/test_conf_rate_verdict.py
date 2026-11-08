"""scripts/lib/conf_rate_verdict.py — the ONE rate verdict for honest_status's
conf_rate leg (2026-11-07, review round 2: two inline judges disagreed on one
value and the API one fail-opened). Runs the program as the gate does.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "conf_rate_verdict.py"


def run(mode, body, now=None, env_extra=None):
    import os
    env = dict(os.environ)
    env.pop("HONEST_SNAP_SRC", None)
    if now is not None:
        env["HS_SNAP_NOW"] = str(now)
    env.update(env_extra or {})
    out = subprocess.run([sys.executable, str(PROG), mode], input=body,
                         capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0
    lines = out.stdout.strip().splitlines()
    assert len(lines) == 1, out.stdout
    return lines[0]


def snap(rate, ts=None):
    return json.dumps({"schema": 1, "host": "x",
                       "ts": time.time() if ts is None else ts,
                       "snapshot": {"confirmation_rate": rate}})


@pytest.mark.parametrize("body,tok", [
    ('{"confirmation_rate": 0.982}', "OK:0.982"),
    ('{"confirmation_rate": 1}', "OK:1.000"),
    ('{"confirmation_rate": null}', "NONE"),
    ('{"confirmation_rate": 1.64}', "VIOL:1.64"),
    ('{"confirmation_rate": 1.0000001}', "VIOL:1.0000001"),   # legible, not "1.000"
    ('{"confirmation_rate": NaN}', "VIOL:nan"),
    ('{"confirmation_rate": -Infinity}', "VIOL:-inf"),
    ('{"confirmation_rate": "1.64"}', "VIOL:shape=str"),
    ('{"confirmation_rate": true}', "VIOL:shape=bool"),
    ('{"confirmation_rate": 1' + "0" * 400 + '}', "VIOL:1" + "0" * 39 + "..."),
    ('{"confirmation_rate": -1' + "0" * 400 + '}', "OK:-1" + "0" * 38 + "..."),
    ('{"confirmation_rate": 2}', "VIOL:2"),
    ('{"confirmation_rate": 0}', "OK:0.000"),
    ('<html>502</html>', "BLIND:api-unparseable"),
    ('null', "BLIND:api-not-object"),
    ('[]', "BLIND:api-not-object"),
    ('42', "BLIND:api-not-object"),
    ('{"error": "delivery_counters_unavailable", "reason": "boom"}', "BLIND:api-error"),
    ('{"other": 1}', "BLIND:api-no-rate-key"),
])
def test_api(body, tok):
    assert run("api", body) == tok


def test_same_value_same_verdict_either_source():
    for rate in ("1.64", 1.64, float("nan"), None, 0.5):
        a = run("api", json.dumps({"confirmation_rate": rate}))
        s = run("snapshot", snap(rate), now=time.time())
        assert a == s, (rate, a, s)


@pytest.mark.parametrize("body,now,tok", [
    (snap(0.9, ts=0), "1800000000", "BLIND:snapshot-stale"),
    (snap(0.9), "", "BLIND:snapshot-no-box-clock"),
    (snap(0.9), "nan", "BLIND:snapshot-no-box-clock"),
    (snap(0.9), '{"ts": 1}', "BLIND:snapshot-no-box-clock"),   # date line missing (r2 F6)
    ('{"ts": "garbage", "snapshot": {}}', "1800000000", "BLIND:snapshot-no_ts"),
    ('not json', "1800000000", "BLIND:snapshot-unreadable"),
])
def test_snapshot_blind(body, now, tok):
    assert run("snapshot", body, now=now) == tok


def test_snapshot_missing_rate_key_is_blind():
    body = json.dumps({"schema": 1, "host": "x", "ts": time.time(), "snapshot": {}})
    assert run("snapshot", body, now=time.time()) == "BLIND:snapshot-no-rate-key"


def test_unimportable_reader_is_blind_with_cause(tmp_path):
    tok = run("snapshot", snap(0.9), now=time.time(),
              env_extra={"HONEST_SNAP_SRC": str(tmp_path)})
    assert tok == "BLIND:snapshot-reader-unimportable(ModuleNotFoundError)"


def test_bad_mode_is_blind():
    assert run("bogus", "{}") == "BLIND:verdict-bad-mode"
