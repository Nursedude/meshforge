"""Phase B chat-compiler: English → validated rule candidate, honestly.

FakeOllama below speaks just enough of the /api/chat shape to exercise every
failure branch: valid rule, repairable rule, unrepairable rule, non-JSON
output, wrapped output, transport failure. The trust chain under test:
LLM proposes → compiler validates (one repair round, then loud) → caller
ratifies → write_candidate validates again.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_dudeai.candidate import write_candidate
from mini_dudeai.chat_compiler import (
    CompilerError, OllamaBackend, build_system_prompt, compile_rule,
    render_rule,
)
from mini_dudeai.chat import _load_rules, _watch_promotion

SOURCE_KINDS = ["nats_sensor", "json_file"]
ACTION_KINDS = ["nats", "ntfy", "annotate", "none"]

GOOD_RULE = {
    "id": "shop_hot_fan",
    "match": {"kind": "sensor_breach", "subject_glob": "*.shop_temp"},
    "action": {"kind": "nats",
               "payload": {"tool": "gpio_write", "pin": 5, "value": 1},
               "payload_down": {"tool": "gpio_write", "pin": 5, "value": 0}},
    "grace_s": 60,
    "cooldown_s": 600,
    "annotation": "Fan on while the shop sensor breaches; off on recovery.",
}


class FakeOllama:
    """Scripted /api/chat server: pops one canned reply per request."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append(json.loads(body))
                if not outer.replies:
                    self.send_error(500, "no scripted reply left")
                    return
                content = outer.replies.pop(0)
                out = json.dumps({"message": {"role": "assistant",
                                              "content": content}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_ollama():
    servers = []

    def make(replies):
        s = FakeOllama(replies)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def _backend(srv) -> OllamaBackend:
    return OllamaBackend(url=srv.url, model="fake", timeout_s=5)


class TestCompileHappyPath:
    def test_valid_rule_compiles(self, fake_ollama):
        srv = fake_ollama([json.dumps(GOOD_RULE)])
        rule, warnings = compile_rule(
            "fan on when shop is hot", _backend(srv), [],
            SOURCE_KINDS, ACTION_KINDS)
        assert rule["id"] == "shop_hot_fan"
        assert warnings == []
        # prompt carried the live vocabulary + the idempotent constraint
        system = srv.requests[0]["messages"][0]["content"]
        assert "nats_sensor" in system and "'ntfy'" in system
        assert "gpio_write" in system and "idempotent" in system

    def test_wrapped_rule_unwrapped(self, fake_ollama):
        srv = fake_ollama([json.dumps({"rule": GOOD_RULE})])
        rule, _ = compile_rule("fan", _backend(srv), [],
                               SOURCE_KINDS, ACTION_KINDS)
        assert rule["id"] == "shop_hot_fan"

    def test_existing_ids_in_prompt(self, fake_ollama):
        srv = fake_ollama([json.dumps(GOOD_RULE)])
        existing = [{"id": "chip_temp_hot_led", "match": {"kind": "x"},
                     "action": {"kind": "none"}}]
        compile_rule("fan", _backend(srv), existing,
                     SOURCE_KINDS, ACTION_KINDS)
        assert "chip_temp_hot_led" in srv.requests[0]["messages"][0]["content"]

    def test_lint_warning_surfaced_not_swallowed(self, fake_ollama):
        # a non-structural match key is legal but almost always a typo —
        # the warning must reach the ratification display
        rule = dict(GOOD_RULE)
        rule["match"] = {"kind": "sensor_breach", "subjct_glob": "*"}
        srv = fake_ollama([json.dumps(rule)])
        _, warnings = compile_rule("fan", _backend(srv), [],
                                   SOURCE_KINDS, ACTION_KINDS)
        assert any("subjct_glob" in w for w in warnings)


class TestRepairRound:
    def test_invalid_then_valid_repairs(self, fake_ollama):
        broken = {k: v for k, v in GOOD_RULE.items() if k != "match"}
        srv = fake_ollama([json.dumps(broken), json.dumps(GOOD_RULE)])
        rule, _ = compile_rule("fan", _backend(srv), [],
                               SOURCE_KINDS, ACTION_KINDS)
        assert rule["id"] == "shop_hot_fan"
        assert len(srv.requests) == 2
        # the repair prompt carries the validator's words, not a paraphrase
        assert "missing match" in srv.requests[1]["messages"][1]["content"]

    def test_invalid_twice_is_loud(self, fake_ollama):
        broken = {k: v for k, v in GOOD_RULE.items() if k != "match"}
        srv = fake_ollama([json.dumps(broken), json.dumps(broken)])
        with pytest.raises(CompilerError) as exc:
            compile_rule("fan", _backend(srv), [], SOURCE_KINDS, ACTION_KINDS)
        assert any("missing match" in e for e in exc.value.errors)

    def test_id_collision_is_an_error(self, fake_ollama):
        srv = fake_ollama([json.dumps(GOOD_RULE), json.dumps(GOOD_RULE)])
        existing = [{"id": "shop_hot_fan", "match": {"kind": "x"},
                     "action": {"kind": "none"}}]
        with pytest.raises(CompilerError) as exc:
            compile_rule("fan", _backend(srv), existing,
                         SOURCE_KINDS, ACTION_KINDS)
        assert any("already exists" in e for e in exc.value.errors)

    def test_unknown_action_kind_is_an_error(self, fake_ollama):
        rule = dict(GOOD_RULE)
        rule["action"] = {"kind": "telegram"}
        srv = fake_ollama([json.dumps(rule), json.dumps(rule)])
        with pytest.raises(CompilerError) as exc:
            compile_rule("fan", _backend(srv), [], SOURCE_KINDS, ACTION_KINDS)
        assert any("telegram" in e for e in exc.value.errors)

    def test_unknown_top_level_key_is_an_error(self, fake_ollama):
        rule = dict(GOOD_RULE)
        rule["threshold"] = 28  # thresholds live in sensor config, not rules
        srv = fake_ollama([json.dumps(rule), json.dumps(rule)])
        with pytest.raises(CompilerError) as exc:
            compile_rule("fan", _backend(srv), [], SOURCE_KINDS, ACTION_KINDS)
        assert any("threshold" in e for e in exc.value.errors)


class TestHonestTransportFailures:
    def test_server_down_is_loud(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        backend = OllamaBackend(url=f"http://127.0.0.1:{port}", timeout_s=0.5)
        with pytest.raises(CompilerError, match="Ollama"):
            compile_rule("fan", backend, [], SOURCE_KINDS, ACTION_KINDS)

    def test_non_json_output_gets_repair_then_loud(self, fake_ollama):
        srv = fake_ollama(["I think you should use a rule like..."])
        with pytest.raises(CompilerError, match="not JSON"):
            compile_rule("fan", _backend(srv), [], SOURCE_KINDS, ACTION_KINDS)

    def test_empty_intent_refused(self, fake_ollama):
        srv = fake_ollama([])
        with pytest.raises(CompilerError, match="empty intent"):
            compile_rule("   ", _backend(srv), [], SOURCE_KINDS, ACTION_KINDS)


class TestRenderAndCandidate:
    def test_render_is_human_readable(self):
        text = render_rule(GOOD_RULE)
        assert "shop_hot_fan" in text
        assert "sensor_breach" in text
        assert "gpio_write" in text
        assert "ON CLEAR" in text

    def test_ratified_rule_lands_via_write_candidate(self, tmp_path):
        candidate = tmp_path / "rules.json.candidate"
        ok, errors = write_candidate(str(candidate), [GOOD_RULE])
        assert ok and errors == []
        doc = json.loads(candidate.read_text())
        assert doc["rules"][0]["id"] == "shop_hot_fan"

    def test_watch_promotion_sees_rule_land(self, tmp_path):
        rules = tmp_path / "rules.json"
        rules.write_text(json.dumps({"rules": [GOOD_RULE]}))
        assert _watch_promotion(str(rules), "shop_hot_fan", wait_s=1)

    def test_watch_promotion_honest_on_absence(self, tmp_path):
        rules = tmp_path / "rules.json"
        rules.write_text(json.dumps({"rules": []}))
        assert not _watch_promotion(str(rules), "shop_hot_fan", wait_s=0.1)

    def test_load_rules_refuses_corrupt_file(self, tmp_path):
        bad = tmp_path / "rules.json"
        bad.write_text("{not json")
        with pytest.raises(SystemExit, match="refusing"):
            _load_rules(str(bad))

    def test_load_rules_missing_is_empty_with_note(self, tmp_path, capsys):
        assert _load_rules(str(tmp_path / "nope.json")) == []
        assert "does not exist" in capsys.readouterr().out


class TestPromptDerivedNotDuplicated:
    def test_prompt_uses_live_kind_lists(self):
        text = build_system_prompt(["custom_src"], ["custom_act"], [],
                                   ["custom_cond"])
        assert "custom_src" in text
        assert "custom_act" in text
        assert "custom_cond" in text
