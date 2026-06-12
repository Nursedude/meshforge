"""Tests for the mini-dudeai → remediation-surface loop-closer.

Pins build_findings(): currently-active rules + fresh escalations become
findings; rules with a safe local fix get RemediationActions; remote-subject /
unmapped findings are informational (no action). Escalations come via the
recent_escalations SSOT, so this and the brief/digest never disagree.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "launcher_tui"))

from handlers.mini_dudeai import build_findings, _fixes_for, _posture  # noqa: E402

NOW = 1_780_000_000.0


def _esc_row(ts, rule, subject, detail):
    return {"ts": ts, "outcome": {"extras": {"escalation": {
        "rule": rule, "subject": subject, "detail": detail}}}}


def test_active_source_error_federator_gets_restart_fix():
    state = {"rules": {"k": {"rule_id": "source_error_federator",
                             "subject": "federator", "currently_active": True,
                             "last_detail": "/api/status unreachable"}}}
    findings = build_findings(state, [], NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f["source"] == "active" and f["actions"]
    assert f["actions"][0].label == "Restart meshforge-map"
    assert f["actions"][0].requires_admin is True


def test_inactive_rules_are_not_findings():
    state = {"rules": {"k": {"rule_id": "source_error_federator",
                             "subject": "federator", "currently_active": False}}}
    assert build_findings(state, [], NOW) == []


def test_remote_peer_escalation_has_no_local_fix():
    hist = [_esc_row(NOW - 100, "federation_peer_unhealthy_unexpected",
                     "moc3", "in_backoff mult=10")]
    findings = build_findings({}, hist, NOW)
    assert len(findings) == 1
    assert findings[0]["source"] == "escalation"
    assert findings[0]["actions"] == []          # informational, no pretend-fix


def test_escalation_uses_recent_escalations_ssot_window():
    # An escalation older than the 24h window is dropped — proving we go through
    # recent_escalations, not a private re-read.
    stale = _esc_row(NOW - 90_000, "federation_peer_unhealthy_unexpected",
                     "moc3", "old")
    assert build_findings({}, [stale], NOW) == []


def test_active_rule_dedups_escalation_of_same_rule_subject():
    state = {"rules": {"k": {"rule_id": "source_error_federator",
                             "subject": "federator", "currently_active": True,
                             "last_detail": "d"}}}
    hist = [_esc_row(NOW - 50, "source_error_federator", "federator", "d")]
    findings = build_findings(state, hist, NOW)
    assert len(findings) == 1                     # not shown twice


def test_watchdog_source_error_maps_to_watchdog_restart():
    acts = _fixes_for("source_error_watchdog")
    assert acts and acts[0].label == "Restart meshforge-watchdog"


def test_unmapped_rule_has_no_actions():
    assert _fixes_for("some_unknown_rule") == []


def test_posture_flags_stale():
    out = _posture({"last_tick_ts": NOW - 99999, "rule_count": 12}, NOW)
    assert "STALE" in out


def test_posture_alive():
    out = _posture({"last_tick_ts": NOW - 10, "rule_count": 12, "error_count": 0}, NOW)
    assert "alive" in out and "12 rules" in out


# --- "describe a rule" (chat-compiler front-end) ---------------------------
#
# Flow tests drive the handler through a FakeDialog and a scripted backend —
# no TUI, no HTTP for compile paths. probe_ollama gets one real local HTTP
# round-trip and one honest-failure case.

import json
import os
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from handlers.mini_dudeai import (MiniDudeaiHandler, discover_chat_targets,
                                  probe_ollama)
from mini_dudeai.chat import PRESETS

GOOD_CLAW_RULE = {
    "id": "shop_hot_fan",
    "match": {"kind": "sensor_breach", "subject_glob": "*.shop_temp"},
    "action": {"kind": "nats",
               "payload": {"tool": "gpio_write", "pin": 5, "value": 1},
               "payload_down": {"tool": "gpio_write", "pin": 5, "value": 0}},
    "grace_s": 60,
    "cooldown_s": 600,
    "annotation": "Fan on while the shop sensor breaches; off on recovery.",
}


class FakeDialog:
    def __init__(self, inputs=None, menu_sels=None, yes_answers=None):
        self.inputs = list(inputs or [])
        self.menu_sels = list(menu_sels or [])
        self.yes_answers = list(yes_answers or [])
        self.msgboxes = []
        self.infoboxes = []

    def msgbox(self, title, text, **kw):
        self.msgboxes.append((title, text))

    def infobox(self, title, text):
        self.infoboxes.append((title, text))

    def inputbox(self, title, text, init="", **kw):
        return self.inputs.pop(0) if self.inputs else None

    def menu(self, title, text, choices, **kw):
        return self.menu_sels.pop(0) if self.menu_sels else None

    def yesno(self, title, text, default_no=False, **kw):
        return self.yes_answers.pop(0) if self.yes_answers else False


def _handler(dialog):
    h = MiniDudeaiHandler()
    h.set_context(types.SimpleNamespace(
        dialog=dialog, safe_call=lambda name, fn: fn()))
    return h


class StubBackend:
    url = "http://stub.invalid:0"
    model = "stub"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return self.replies.pop(0)


def _claw_target(tmp_path):
    (tmp_path / "mini_dudeai_claw_rules.json").write_text('{"rules": []}')
    targets = discover_chat_targets(str(tmp_path))
    return next(t for t in targets if t["key"] == "claw")


class TestDiscoverChatTargets:
    def test_fleet_only_when_no_claw_ruleset(self, tmp_path):
        targets = discover_chat_targets(str(tmp_path))
        assert [t["key"] for t in targets] == ["fleet"]
        assert targets[0]["rules_path"] == str(tmp_path / "mini_dudeai_rules.json")

    def test_claw_offered_when_its_ruleset_exists(self, tmp_path):
        (tmp_path / "mini_dudeai_claw_rules.json").write_text('{"rules": []}')
        targets = discover_chat_targets(str(tmp_path))
        assert [t["key"] for t in targets] == ["fleet", "claw"]

    def test_specs_come_from_the_one_presets_table(self, tmp_path):
        # Two consumers, ONE constant: the TUI must see exactly what the chat
        # CLI sees — kinds and units must not fork into a second copy.
        (tmp_path / "mini_dudeai_claw_rules.json").write_text('{"rules": []}')
        targets = {t["key"]: t for t in discover_chat_targets(str(tmp_path))}
        fleet, claw = PRESETS["meshforge_fleet"], PRESETS["standalone"]
        assert targets["fleet"]["condition_kinds"] == fleet["condition_kinds"]
        assert targets["fleet"]["daemon_unit"] == fleet["daemon_unit"]
        assert targets["claw"]["condition_kinds"] == claw["condition_kinds"]
        assert targets["claw"]["daemon_unit"] == claw["daemon_unit"]


class TestProbeOllama:
    def test_reachable_server_reports_version(self):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                out = json.dumps({"version": "9.9-test"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            ok, detail = probe_ollama(f"http://127.0.0.1:{srv.server_port}")
            assert ok and "9.9-test" in detail
        finally:
            srv.shutdown()
            srv.server_close()

    def test_unreachable_is_false_with_reason_never_raises(self):
        ok, detail = probe_ollama("http://127.0.0.1:1", timeout_s=0.5)
        assert ok is False
        assert detail  # the reason travels to the operator verbatim


class TestDescribeRuleFlow:
    def test_unreachable_ollama_stops_before_any_input(self, monkeypatch):
        monkeypatch.setattr("handlers.mini_dudeai.probe_ollama",
                            lambda url, **kw: (False, "connection refused"))
        d = FakeDialog(inputs=["should never be consumed"])
        _handler(d)._describe_rule()
        assert len(d.msgboxes) == 1
        title, text = d.msgboxes[0]
        assert "not reachable" in title.lower()
        assert "connection refused" in text
        assert d.inputs == ["should never be consumed"]  # never asked

    def test_target_menu_cancel_exits_cleanly(self, monkeypatch, tmp_path):
        monkeypatch.setattr("handlers.mini_dudeai.probe_ollama",
                            lambda url, **kw: (True, "ollama test"))
        monkeypatch.setattr("handlers.mini_dudeai.get_real_user_home",
                            lambda: tmp_path)
        (tmp_path / "mini_dudeai_claw_rules.json").write_text('{"rules": []}')
        d = FakeDialog(menu_sels=[None])  # two targets -> menu -> cancel
        _handler(d)._describe_rule()
        assert d.msgboxes == [] and d.infoboxes == []

    def test_unreadable_ruleset_refuses_without_compiling(self, tmp_path):
        target = _claw_target(tmp_path)
        (tmp_path / "mini_dudeai_claw_rules.json").write_text("{not json")
        backend = StubBackend([json.dumps(GOOD_CLAW_RULE)])
        d = FakeDialog(yes_answers=[True])
        _handler(d)._compile_one("fan on when hot", target, backend)
        assert backend.calls == 0  # error must never read as "empty ruleset"
        assert "unreadable" in d.msgboxes[0][0].lower()
        assert not os.path.exists(target["candidate_path"])

    def test_compile_failure_is_surfaced(self, tmp_path):
        target = _claw_target(tmp_path)
        backend = StubBackend(["not json at all", "still not json"])
        d = FakeDialog(yes_answers=[True])
        _handler(d)._compile_one("fan on when hot", target, backend)
        assert any("compile failed" in t.lower() for t, _ in d.msgboxes)
        assert not os.path.exists(target["candidate_path"])

    def test_ratify_no_writes_nothing(self, tmp_path):
        target = _claw_target(tmp_path)
        backend = StubBackend([json.dumps(GOOD_CLAW_RULE)])
        d = FakeDialog(yes_answers=[False])
        _handler(d)._compile_one("fan on when hot", target, backend)
        assert not os.path.exists(target["candidate_path"])
        assert any(t == "Not ratified" for t, _ in d.msgboxes)

    def test_ratify_yes_writes_candidate_and_reports_promotion(
            self, monkeypatch, tmp_path):
        target = _claw_target(tmp_path)
        backend = StubBackend([json.dumps(GOOD_CLAW_RULE)])
        monkeypatch.setattr("mini_dudeai.chat.watch_promotion",
                            lambda *a, **kw: True)
        d = FakeDialog(yes_answers=[True])
        _handler(d)._compile_one("fan on when hot", target, backend)
        with open(target["candidate_path"]) as f:
            doc = json.load(f)
        assert any(r["id"] == "shop_hot_fan" for r in doc["rules"])
        assert any("promoted" in t.lower() for t, _ in d.msgboxes)

    def test_promotion_absence_is_honest_and_names_the_unit(
            self, monkeypatch, tmp_path):
        target = _claw_target(tmp_path)
        backend = StubBackend([json.dumps(GOOD_CLAW_RULE)])
        monkeypatch.setattr("mini_dudeai.chat.watch_promotion",
                            lambda *a, **kw: False)
        d = FakeDialog(yes_answers=[True])
        _handler(d)._compile_one("fan on when hot", target, backend)
        assert os.path.exists(target["candidate_path"])  # written, said so
        title, text = d.msgboxes[-1]
        assert "not promoted" in title.lower()
        assert target["daemon_unit"] in text  # actionable, not generic
