"""Tests for scripts/loop_gate.py — the circularity gates (operator 2026-11-15).

Born from one session: a self-graded "6 of 16" travelled across pages as an
outside result (outside audit A22); a byte-identical duplicate page carried a
lab banner that pointed nowhere; a reviewer brief carried an "accepted" item
nobody had dated. Three gates, one script:

  A. GROUND   — a VERIFIED tag on an artifact names what it rests on
                (code/git/ci/test/live/radio/operator/upstream). Another AI's
                page is not ground: that claim is "consistent with", not VERIFIED.
  B. TOPIC    — one authoritative page per topic; a registered page is
                published only as what the registry says it is, a superseded
                page links its successor in its header, an unregistered domain
                page is refused.
  C. ACCEPTED — a reviewer brief's accepted list is generated from the dated
                deferred ledger, never hand-written, and expires with its row.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "loop_gate.py")
sys.path.insert(0, os.path.dirname(_SCRIPT))
import loop_gate  # noqa: E402

A1 = "AAAAAAAAAAAAAAAAAAAAA1"
A2 = "AAAAAAAAAAAAAAAAAAAAA2"
A3 = "AAAAAAAAAAAAAAAAAAAAA3"
URL = "https://claude.ai/artifact/{}".format


@pytest.fixture
def env(tmp_path, monkeypatch):
    reg = {"artifacts": {
        A1: {"title": "Live page", "topic": "t-live", "role": "authoritative",
             "ungrounded_verified_baseline": 2},
        A2: {"title": "Old page", "topic": "t-old", "role": "superseded",
             "successor": A1, "ungrounded_verified_baseline": 0},
        A3: {"title": "Paste kit", "topic": "t-live", "role": "variant",
             "ungrounded_verified_baseline": 0},
    }}
    rp = tmp_path / "registry.json"
    rp.write_text(json.dumps(reg))
    ledger = {"tasks": [
        {"id": "acc-one", "status": "open", "review_after": "2026-11-29",
         "review_accepted": "ARC", "accepted_text": "a TUI echo shows twice"},
        {"id": "acc-two", "status": "open", "review_after": "2026-11-29",
         "review_accepted": "ARC", "accepted_text": "id 0 twin shows twice"},
        {"id": "other", "status": "open", "review_after": "2026-11-29"},
    ]}
    lp = tmp_path / "ledger.json"
    lp.write_text(json.dumps(ledger))
    monkeypatch.setenv("LOOP_GATE_REGISTRY", str(rp))
    monkeypatch.setenv("LOOP_GATE_LEDGER", str(lp))
    monkeypatch.setenv("LOOP_GATE_TODAY", "2026-11-15")
    return tmp_path, rp, lp


def _page(tmp_path, body, name="p.html"):
    p = tmp_path / name
    p.write_text("<html><head><title>x</title></head><body><header>"
                 + body + "</header><section>s</section></body></html>")
    return str(p)


def _hook(payload):
    r = subprocess.run([sys.executable, _SCRIPT, "hook"], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=30, env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def _publish(file_path, url=None, edits=None):
    ti = {"file_path": file_path}
    if url:
        ti["url"] = url
    if edits:
        ti["edits"] = edits
    return {"tool_name": "Artifact", "tool_input": ti}


V = '<span class="tag t-v">VERIFIED</span>'
VG = '<span class="tag t-v" data-ground="code">VERIFIED</span>'
VAI = '<span class="tag t-v" data-ground="ai">VERIFIED</span>'


class TestGround:
    def test_edit_adding_ungrounded_verified_is_refused(self, env):
        tmp, _, _ = env
        f = _page(tmp, f"{V}{V} old")
        why = _hook(_publish(f, URL(A1), [{"old_str": "old", "new_str": f"{V} new"}]))
        assert why and "GROUND" in why

    def test_edit_adding_grounded_verified_passes(self, env):
        tmp, _, _ = env
        f = _page(tmp, f"{V}{V} old")
        assert _hook(_publish(f, URL(A1), [{"old_str": "old", "new_str": f"{VG} new"}])) is None

    def test_ai_is_not_ground(self, env):
        tmp, _, _ = env
        f = _page(tmp, "old")
        why = _hook(_publish(f, URL(A1), [{"old_str": "old", "new_str": f"{VAI}"}]))
        assert why and "consistent" in why.lower()

    def test_full_publish_may_not_grow_past_baseline(self, env):
        tmp, _, _ = env
        assert _hook(_publish(_page(tmp, f"{V}{V}"), URL(A1))) is None
        why = _hook(_publish(_page(tmp, f"{V}{V}{V}"), URL(A1)))
        assert why and "baseline" in why

    def test_unmeasured_baseline_refuses_a_full_publish_but_not_an_edit(self, env):
        tmp, rp, _ = env
        reg = json.loads(rp.read_text())
        reg["artifacts"][A1]["ungrounded_verified_baseline"] = None
        rp.write_text(json.dumps(reg))
        why = _hook(_publish(_page(tmp, f"{V}"), URL(A1)))
        assert why and "NOT MEASURED" in why
        f = _page(tmp, f"{V} old", "e.html")
        assert _hook(_publish(f, URL(A1), [{"old_str": "old", "new_str": "new"}])) is None

    def test_new_domain_page_needs_every_verified_grounded(self, env):
        tmp, _, _ = env
        body = f'<meta name="mf:topic" content="t-new">MeshForge {V}'
        why = _hook(_publish(_page(tmp, body)))
        assert why and "GROUND" in why


class TestTopic:
    def test_unregistered_url_is_refused(self, env):
        tmp, _, _ = env
        why = _hook(_publish(_page(tmp, "x"), URL("ZZZZZZZZZZZZZZZZZZZZZZ")))
        assert why and "register" in why.lower()

    def test_superseded_page_must_link_successor_in_header(self, env):
        tmp, _, _ = env
        why = _hook(_publish(_page(tmp, "no link"), URL(A2)))
        assert why and A1 in why
        ok = _page(tmp, f'<a href="{URL(A1)}">now</a>', "ok.html")
        assert _hook(_publish(ok, URL(A2))) is None

    def test_variant_must_link_its_authoritative_page(self, env):
        tmp, _, _ = env
        why = _hook(_publish(_page(tmp, "kit"), URL(A3)))
        assert why and A1 in why

    def test_new_domain_page_without_topic_is_refused(self, env):
        tmp, _, _ = env
        why = _hook(_publish(_page(tmp, "MeshForge news")))
        assert why and "mf:topic" in why

    def test_new_page_on_a_live_topic_is_refused(self, env):
        tmp, _, _ = env
        body = '<meta name="mf:topic" content="t-live">MeshForge again'
        why = _hook(_publish(_page(tmp, body)))
        assert why and A1 in why

    def test_non_domain_new_page_passes(self, env):
        tmp, _, _ = env
        assert _hook(_publish(_page(tmp, "a recipe"))) is None

    def test_reads_and_assets_are_not_gated(self, env):
        assert _hook({"tool_name": "Artifact",
                      "tool_input": {"action": "read", "url": URL("ZZZ")}}) is None
        assert _hook({"tool_name": "Artifact", "tool_input": {
            "url": URL("ZZZ"), "asset": True, "file_path": "/x.png"}}) is None

    def test_registry_check_refuses_two_authoritative_on_one_topic(self, env):
        _, rp, _ = env
        reg = json.loads(rp.read_text())
        reg["artifacts"][A3]["role"] = "authoritative"
        rp.write_text(json.dumps(reg))
        problems = loop_gate.check_registry(loop_gate.load_registry())
        assert any("t-live" in p for p in problems)

    def test_registry_check_clean_fixture(self, env):
        assert loop_gate.check_registry(loop_gate.load_registry()) == []


def _brief(block):
    return {"tool_name": "Agent", "tool_input": {
        "prompt": "Review.\n- Known and ACCEPTED, so do not report these:\n" + block}}


class TestAccepted:
    def test_hand_written_accepted_list_is_refused(self, env):
        why = _hook(_brief("  - something I decided\n"))
        assert why and "loop_gate.py accepted" in why

    def test_generated_block_passes(self, env):
        block = loop_gate.render_accepted("ARC")
        assert "a TUI echo shows twice" in block and "other" not in block
        assert _hook(_brief(block)) is None

    def test_edited_block_is_refused(self, env):
        block = loop_gate.render_accepted("ARC").replace("twice", "once")
        why = _hook(_brief(block))
        assert why and "match" in why

    def test_expired_row_refuses_the_list(self, env, monkeypatch):
        monkeypatch.setenv("LOOP_GATE_TODAY", "2026-11-30")
        with pytest.raises(loop_gate.Refused) as e:
            loop_gate.render_accepted("ARC")
        assert "acc-one" in str(e.value)

    def test_brief_without_accepted_section_passes(self, env):
        assert _hook({"tool_name": "Agent", "tool_input": {"prompt": "Find X."}}) is None


class TestFailOpenWitness:
    def test_unparseable_payload_allows_and_leaves_a_witness(self, env, monkeypatch):
        tmp, _, _ = env
        w = tmp / "witness.log"
        monkeypatch.setenv("LOOP_GATE_WITNESS", str(w))
        r = subprocess.run([sys.executable, _SCRIPT, "hook"], input="{not json",
                           capture_output=True, text=True, timeout=30,
                           env=os.environ.copy())
        assert r.returncode == 0 and r.stdout.strip() == ""
        assert "parse" in w.read_text()
