"""scripts/tui_journey.py — the verdict logic and the write guard.

The driver's promise is that PASS means "the screen agrees with an
independent oracle AND a planted lie was caught". These pin the ways that
promise could quietly weaken: a check that cannot fail, an oracle that
cannot answer, a journey that wrote, a script that went somewhere else.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import tui_journey as tj  # noqa: E402
import tui_journeys as tjs  # noqa: E402


def _result(text="rnsd running", **kw):
    r = {"owned": True, "screens": [{"kind": "msgbox", "title": "T",
                                     "text": text, "choices": []}],
         "stdout": "", "blocked": [], "diverged": [], "error": None,
         "unused_answers": []}
    r.update(kw)
    return r


def _journey(check, plant=lambda t: t.replace("running", "stopped")):
    return {"name": "x", "section": "s", "tag": "t", "check": check, "plant": plant}


def _says_running(text, oracle):
    return [("running" in text, "screen says running")]


def test_pass_requires_the_planted_lie_to_be_caught():
    v, notes = tj.judge(_journey(_says_running), _result())
    assert v == "PASS"
    assert any(n.startswith("control: lie caught") for n in notes)


def test_a_check_that_cannot_fail_is_unfalsified_not_pass():
    v, _ = tj.judge(_journey(lambda t, o: [(True, "always")]), _result())
    assert v == "UNFALSIFIED"


def test_a_plant_that_changes_nothing_is_unfalsified():
    v, notes = tj.judge(_journey(_says_running, plant=lambda t: t), _result())
    assert v == "UNFALSIFIED"
    assert "control never ran" in notes[0]


def test_oracle_unknown_is_never_a_pass():
    v, _ = tj.judge(_journey(lambda t, o: [(None, "oracle down")]), _result())
    assert v == "UNKNOWN"


def test_no_results_is_an_error_not_a_pass():
    v, _ = tj.judge(_journey(lambda t, o: []), _result())
    assert v == "ERROR"


def test_disagreement_is_fail():
    v, notes = tj.judge(_journey(_says_running), _result(text="rnsd stopped"))
    assert v == "FAIL" and notes == ["screen says running"]


@pytest.mark.parametrize("kw,verdict", [
    ({"blocked": ["subprocess sudo: ['sudo']"]}, "BLOCKED"),
    ({"diverged": ["no menu item 'x'"]}, "DIVERGED"),
    ({"unused_answers": [{"kind": "menu", "pick": "x"}]}, "DIVERGED"),
    ({"error": "did not return within 60s"}, "ERROR"),
    ({"owned": False}, "ERROR"),
])
def test_a_journey_that_wrote_or_wandered_is_never_judged(kw, verdict):
    v, _ = tj.judge(_journey(_says_running), _result(**kw))
    assert v == verdict


def test_a_safe_call_rescue_is_an_error():
    title = sorted(tj.SAFE_CALL_ERROR_TITLES)[0]
    r = _result()
    r["screens"].append({"kind": "msgbox", "title": title, "text": "boom", "choices": []})
    v, _ = tj.judge(_journey(_says_running), r)
    assert v == "ERROR"


def test_every_registered_journey_carries_a_plant_that_changes_its_screen():
    # A plant that no-ops on the screen it targets makes the control vacuous.
    samples = {
        "noc_home": "  [ UP ] rnsd               running\n",
        "service_status": "  ● mosquitto          running\n",
        "stack_health": "[ OK ]  RNS path table            40 network destinations, 5 local IPC peers\n",
        "set_owner": "[msgbox] Success\nOwner settings updated:\n\nLong name: SANDBOX-OWNER\n",
        "preset_one_enter": "Frequency slot: 8 (unchanged)\n",
        "primary_channel_one_enter": "No change\n",
        "mqtt_root_one_enter": "No change\n",
        "preset_deliberate": "Frequency slot: 12\n",
        "primary_channel_deliberate": "Setting channel name to Fleet1...\n",
        "mqtt_root_deliberate": "MQTT root topic set to: msh/US/MAUI\n",
    }
    names = {j["name"] for j in tjs.JOURNEYS}
    assert names <= set(samples), f"add a sample for {names - set(samples)}"
    for j in tjs.JOURNEYS:
        assert j["plant"](samples[j["name"]]) != samples[j["name"]], j["name"]


def test_guard_refuses_writes_in_a_real_child():
    """The guard that has never refused is not evidence — run the selftest."""
    r = tj.run_child({"section": "__guard_selftest__", "tag": "", "path": []})
    assert r.get("selftest") is True, r
    assert len(r["blocked"]) == tj.GUARD_EXPECTED, r["blocked"]
    assert r["read_ok"] is True
    assert r["timeout_ok"] is True, "the guard must not block our OWN timeout kill"


def test_sandbox_journey_that_ran_outside_a_sandbox_is_an_error():
    j = dict(_journey(_says_running), sandbox=True)
    v, notes = tj.judge(j, _result(sandbox=False))
    assert v == "ERROR" and "did not run in one" in notes[0]


def test_sandbox_oracle_reads_only_the_childs_readback():
    """In a sandbox the oracle must be what the child read from the SAME sim —
    a live host command would ask the REAL radio."""
    seen = {}

    def check(text, oracle):
        seen["out"] = oracle(["meshtastic", "--host", "localhost", "--info"])
        seen["other"] = oracle(["systemctl", "show", "rnsd"])
        return [("running" in text, "x")]

    j = dict(_journey(check), sandbox=True)
    tj.judge(j, _result(sandbox=True, readback={
        "meshtastic --host localhost --info": "Owner: A (B)"}))
    assert seen == {"out": "Owner: A (B)", "other": ""}
