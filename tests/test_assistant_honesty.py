"""The Claude assistant says which AI assist the box ACTUALLY has, and why an
answer did not come from the API (item 5, 2026-09-30).

Measured defects this pins: the reply's first block can be a THINKING block
(thinking is on by default) — `content[0].text` raised, a broad except ate
it, and every answer silently came from the knowledge base while the banner
said "Mode: PRO"; the banner was derived from ANTHROPIC_API_KEY's PRESENCE,
even with no `anthropic` package installed; the TUI launcher runs under sudo,
whose env_reset drops that variable. Driven through the REAL ClaudeAssistant
with a fake SDK client (no network, no key).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT / "src"), str(_ROOT / "src" / "launcher_tui"), str(_ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import utils.claude_assistant as ca  # noqa: E402

APP_DIR = "meshforge"


# The keywords the real anthropic 1.10.0 `messages.create` takes that this
# code sends — a misspelled one must fail here, not in production (reader I9).
_REAL_CREATE_KWARGS = {"model", "max_tokens", "system", "messages",
                       "cache_control", "output_config"}


class _FakeClient:
    def __init__(self, reply=None, exc=None, replies=None):
        self.calls = []
        self._reply, self._exc = reply, exc
        self._replies = list(replies or [])
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        unknown = set(kw) - _REAL_CREATE_KWARGS
        if unknown:
            raise TypeError(f"unexpected keyword(s) {unknown}")
        self.calls.append(kw)
        if self._replies:
            nxt = self._replies.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        if self._exc:
            raise self._exc
        return self._reply


CLIENT_KW = []


def _assistant(monkeypatch, tmp_path, client, has_pkg=True):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(ca, "_HAS_ANTHROPIC", has_pkg)
    CLIENT_KW.clear()
    monkeypatch.setattr(ca, "_anthropic_mod", SimpleNamespace(
        Anthropic=lambda **kw: (CLIENT_KW.append(kw), client)[1]))
    return ca.ClaudeAssistant()


def _reply(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


THINK = SimpleNamespace(type="thinking", thinking="")        # no .text attribute
TEXT = SimpleNamespace(type="text", text="Check the LoRa region first.")


def test_a_thinking_block_first_still_yields_the_api_answer(monkeypatch, tmp_path):
    client = _FakeClient(_reply(THINK, TEXT))
    a = _assistant(monkeypatch, tmp_path, client)
    r = a.ask("why is my node silent?")
    assert r.mode is ca.AssistantMode.PRO, (r.mode, getattr(r, "fallback_reason", "<no field>"))
    assert r.answer == "Check the LoRa region first."


def test_request_uses_the_current_model_at_low_effort(monkeypatch, tmp_path):
    client = _FakeClient(_reply(TEXT))
    _assistant(monkeypatch, tmp_path, client).ask("q")
    kw = client.calls[0]
    assert kw["model"] == ca.DEFAULT_ASSISTANT_MODEL
    assert kw["output_config"] == {"effort": ca.ASSISTANT_EFFORT}
    assert "thinking" not in kw                     # cannot be disabled on the current Opus


@pytest.mark.parametrize("reply, exc, why", [
    (_reply(THINK, stop="refusal"), None, "refusal"),
    (_reply(THINK, stop="max_tokens"), None, "no text in the reply"),
    (None, RuntimeError("boom 503"), "RuntimeError: boom 503"),
])
def test_a_fallback_says_why(monkeypatch, tmp_path, reply, exc, why):
    a = _assistant(monkeypatch, tmp_path, _FakeClient(reply, exc))
    r = a.ask("q")
    assert r.mode is ca.AssistantMode.STANDALONE
    assert why in r.fallback_reason, r.fallback_reason


def test_the_reason_survives_every_later_answer(monkeypatch, tmp_path):
    # the first fallback flipped the mode; the 2nd+ answers then said nothing
    a = _assistant(monkeypatch, tmp_path, _FakeClient(_reply(TEXT)), has_pkg=False)
    reasons = [a.ask(f"q{i}").fallback_reason for i in range(3)]
    assert all("anthropic" in r for r in reasons), reasons


def test_configured_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(ca, "_HAS_ANTHROPIC", True)
    av = ca.check_availability()
    assert av.api == "configured" and "not contacted yet" in av.detail, av


def test_history_is_clean_api_pairs_only(monkeypatch, tmp_path):
    # a KB answer must never be replayed as the model's own words, and a
    # trimmed window must start on a user turn (reader pair I6/J5/J6)
    replies = [RuntimeError("503")] + [_reply(TEXT) for _ in range(14)]
    client = _FakeClient(replies=replies)
    a = _assistant(monkeypatch, tmp_path, client)
    first = a.ask("q0")
    assert first.mode is ca.AssistantMode.STANDALONE          # the 503 fell back
    for i in range(1, 15):
        a.ask(f"q{i}")
    for kw in client.calls:
        roles = [m["role"] for m in kw["messages"]]
        assert roles[0] == "user", roles
        assert all(r1 != r2 for r1, r2 in zip(roles, roles[1:])), roles
        assert first.answer not in [m["content"] for m in kw["messages"]]


def test_non_api_answers_never_claim_pro(monkeypatch, tmp_path):
    a = _assistant(monkeypatch, tmp_path, _FakeClient(_reply(TEXT)), has_pkg=False)
    assert a.get_mode() is ca.AssistantMode.STANDALONE and not a.is_pro_enabled()
    a2 = _assistant(monkeypatch, tmp_path, _FakeClient(_reply(TEXT)), has_pkg=True)
    assert a2.analyze_logs(["ERROR connection refused"]).mode is ca.AssistantMode.STANDALONE


def test_truncation_is_said(monkeypatch, tmp_path):
    a = _assistant(monkeypatch, tmp_path, _FakeClient(_reply(TEXT, stop="max_tokens")))
    assert "(truncated" in a.ask("q").answer


def test_client_is_bounded(monkeypatch, tmp_path):
    a = _assistant(monkeypatch, tmp_path, _FakeClient(_reply(TEXT)))
    a.ask("q")
    assert CLIENT_KW and CLIENT_KW[0]["timeout"] <= 120 and CLIENT_KW[0]["max_retries"] <= 1


def test_key_file_symlink_and_fifo_are_refused_without_blocking(monkeypatch, tmp_path):
    import os
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    d = tmp_path / ".config" / APP_DIR
    d.mkdir(parents=True)
    key = d / "anthropic.key"
    secret = tmp_path / "some-other-secret"
    secret.write_text("not-an-api-key\n")
    secret.chmod(0o600)
    with patch("utils.paths.get_real_user_home", return_value=tmp_path):
        key.symlink_to(secret)
        got, why = ca.resolve_api_key()
        assert got is None and "symlink" in why, why
        key.unlink()
        os.mkfifo(key, 0o600)                      # a blocking read would hang here
        got, why = ca.resolve_api_key()
        assert got is None and "regular file" in why, why


def test_missing_package_is_reported_not_claimed(monkeypatch, tmp_path):
    a = _assistant(monkeypatch, tmp_path, _FakeClient(_reply(TEXT)), has_pkg=False)
    r = a.ask("q")
    assert r.mode is ca.AssistantMode.STANDALONE
    assert "anthropic" in r.fallback_reason
    av = ca.check_availability()
    assert av.api == "no_package" and "requirements/ai.txt" in av.detail


def test_no_key_means_nothing_was_tried(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("utils.paths.get_real_user_home", return_value=tmp_path):
        a = ca.ClaudeAssistant()
        r = a.ask("q")
        av = ca.check_availability()
    assert r.mode is ca.AssistantMode.STANDALONE and r.fallback_reason == ""
    assert av.api == "no_key" and "sudo" in av.detail


def test_key_file_must_be_private(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key = tmp_path / ".config" / APP_DIR / "anthropic.key"
    key.parent.mkdir(parents=True)
    key.write_text("file-key-not-real\n")
    with patch("utils.paths.get_real_user_home", return_value=tmp_path):
        key.chmod(0o644)
        got, why = ca.resolve_api_key()
        assert got is None and "chmod 600" in why, why
        key.chmod(0o600)
        got, why = ca.resolve_api_key()
        assert got == "file-key-not-real" and why is None


def test_tui_banner_never_claims_an_api_that_is_not_ready(monkeypatch, tmp_path):
    from handler_test_utils import make_handler_context
    monkeypatch.setenv("ANTHROPIC_API_KEY", "set-but-unusable")
    monkeypatch.setattr(ca, "_HAS_ANTHROPIC", False)       # the old banner said PRO here
    handler = __import__("handlers.ai_tools", fromlist=["AIToolsHandler"]).AIToolsHandler()
    handler.set_context(make_handler_context())
    handler.ctx.dialog._inputbox_returns = [""]            # exit after the banner
    handler._claude_assistant()
    banner = next(a[1] for n, a, kw in handler.ctx.dialog.calls if n == "msgbox")
    assert "PRO" not in banner and "not in use" in banner, banner
    assert "`anthropic` package is not installed" in banner, banner   # the package, said plainly
