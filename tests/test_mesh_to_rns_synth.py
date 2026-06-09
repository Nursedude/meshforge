"""Tests for the Mesh→RNS bridge synth (Thread-2 Phase 2): the deterministic
driver that injects synthetic Meshtastic JSON traffic so the M→R bridge path
can be exercised on demand. The pure builders are pinned against the gateway's
exact MQTT ingest contract; the orchestrator is driven through a fake publisher
(no broker needed).
"""

import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from lab.mesh_to_rns_synth import (
    BROADCAST_NODE_NUM,
    SYNTH_NODE_BASE,
    InjectionRecord,
    MeshToRnsSynth,
    build_json_topic,
    build_meshtastic_text_json,
    synth_node_id,
    synth_node_num,
    _default_text,
)


class _FakePublisher:
    """Records (topic, payload) calls instead of touching a broker."""

    def __init__(self):
        self.calls = []

    def __call__(self, topic, payload):
        self.calls.append((topic, payload))


# ---------------------------------------------------------------------------
# Synthetic node identity
# ---------------------------------------------------------------------------

class TestSynthNodeIdentity:

    def test_node_num_in_reserved_range(self):
        for i in (0, 1, 42, 0xFFFF):
            n = synth_node_num(i)
            assert (n & ~0xFFFF) == SYNTH_NODE_BASE
            assert n == SYNTH_NODE_BASE | i

    def test_node_num_stable(self):
        assert synth_node_num(7) == synth_node_num(7)

    def test_node_ids_distinct(self):
        ids = {synth_node_id(i) for i in range(8)}
        assert len(ids) == 8

    def test_node_id_hex_shape(self):
        assert synth_node_id(0) == "!5e700000"
        assert synth_node_id(1) == "!5e700001"

    def test_index_masked_not_overflow(self):
        # An index beyond 16 bits wraps within the reserved range, never
        # bleeds into the prefix.
        n = synth_node_num(0x1_0001)
        assert (n & ~0xFFFF) == SYNTH_NODE_BASE


# ---------------------------------------------------------------------------
# JSON payload builder — the gateway ingest contract
# ---------------------------------------------------------------------------

class TestBuildJson:

    def test_field_shape(self):
        m = build_meshtastic_text_json(from_num=0x5e700000, text="hi")
        assert m["from"] == 0x5e700000
        assert m["to"] == BROADCAST_NODE_NUM
        assert m["sender"] == "!5e700000"
        assert m["channel"] == 0
        assert m["type"] == "text"
        assert m["payload"] == {"text": "hi"}

    def test_directed_to_num(self):
        m = build_meshtastic_text_json(
            from_num=1, text="x", to_num=0xb03bb70c, channel_index=2)
        assert m["to"] == 0xb03bb70c
        assert m["channel"] == 2

    def test_gateway_extraction_roundtrip(self):
        """Prove a synth message survives the gateway's OWN field extraction
        (mqtt_bridge_handler._process_json_message L435-441 / 493-494):
        from→from_id, payload.text, channel, broadcast detection."""
        m = build_meshtastic_text_json(from_num=0x5e70002a, text="ping body")
        data = json.loads(json.dumps(m))   # through-the-wire round-trip

        from_num = data.get('from', 0)
        from_id = f"!{from_num:08x}" if from_num else (data.get('sender') or "unknown")
        to_num = data.get('to', 0)
        payload = data.get('payload', {})
        text = payload.get('text', '') if isinstance(payload, dict) else str(payload)
        channel = data.get('channel', 0)
        is_broadcast = to_num == 0xFFFFFFFF

        assert from_id == "!5e70002a"
        assert text == "ping body"
        assert channel == 0
        assert is_broadcast is True


# ---------------------------------------------------------------------------
# Topic builder
# ---------------------------------------------------------------------------

class TestBuildTopic:

    def test_region_less(self):
        t = build_json_topic(root="msh", channel_name="LongFast",
                             node_id="!5e700000")
        assert t == "msh/2/json/LongFast/!5e700000"

    def test_region_ful(self):
        t = build_json_topic(root="msh", channel_name="LongFast",
                             node_id="!5e700000", region="US")
        assert t == "msh/US/2/json/LongFast/!5e700000"

    def test_node_id_bang_normalized(self):
        t = build_json_topic(root="msh", channel_name="ch", node_id="5e700000")
        assert t == "msh/2/json/ch/!5e700000"

    def test_matches_gateway_subscription_segments(self):
        """The gateway subscribes {root}/2/json/{ch}/# and {root}/+/2/json/
        {ch}/# — both our shapes slot under one of them."""
        rless = build_json_topic(root="msh", channel_name="LongFast",
                                 node_id="!a")
        rful = build_json_topic(root="msh", channel_name="LongFast",
                                node_id="!a", region="US")
        assert rless.split("/")[1:4] == ["2", "json", "LongFast"]
        assert rful.split("/")[2:5] == ["2", "json", "LongFast"]


# ---------------------------------------------------------------------------
# Loop-guard safety
# ---------------------------------------------------------------------------

class TestLoopGuardSafety:

    def test_default_text_not_rns_tagged(self):
        for seq in range(5):
            assert not _default_text(seq, 0).lstrip().startswith("[RNS:")

    def test_inject_rejects_rns_tagged_text(self):
        synth = MeshToRnsSynth(_FakePublisher())
        with pytest.raises(ValueError):
            synth.inject("[RNS:abcd] looped back")

    def test_inject_rejects_leading_whitespace_rns_tag(self):
        synth = MeshToRnsSynth(_FakePublisher())
        with pytest.raises(ValueError):
            synth.inject("   [RNS:abcd] sneaky")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class TestInject:

    def test_publishes_topic_and_payload(self):
        pub = _FakePublisher()
        synth = MeshToRnsSynth(pub, root="msh", channel_name="LongFast")
        rec = synth.inject("hello", seq=3, user_index=2)
        assert len(pub.calls) == 1
        topic, payload = pub.calls[0]
        assert topic == "msh/2/json/LongFast/!5e700002"
        data = json.loads(payload)
        assert data["from"] == synth_node_num(2)
        assert data["payload"]["text"] == "hello"
        assert isinstance(rec, InjectionRecord)
        assert rec.seq == 3 and rec.broadcast is True

    def test_directed_injection(self):
        pub = _FakePublisher()
        synth = MeshToRnsSynth(pub)
        rec = synth.inject("dm", broadcast=False, to_num=0xb03bb70c)
        data = json.loads(pub.calls[0][1])
        assert data["to"] == 0xb03bb70c
        assert rec.broadcast is False

    def test_channel_index_threads_into_payload(self):
        pub = _FakePublisher()
        synth = MeshToRnsSynth(pub, channel_index=2)
        synth.inject("x")
        assert json.loads(pub.calls[0][1])["channel"] == 2


class TestRun:

    def test_run_injects_count(self):
        pub = _FakePublisher()
        synth = MeshToRnsSynth(pub)
        report = synth.run(4, interval_s=0)
        assert report.injected == 4
        assert report.failed == 0
        assert len(pub.calls) == 4
        assert len(report.records) == 4
        # seq-stamped, monotonically
        seqs = [r.seq for r in report.records]
        assert seqs == [0, 1, 2, 3]

    def test_run_stop_event_interrupts(self):
        pub = _FakePublisher()
        synth = MeshToRnsSynth(pub)
        ev = threading.Event()
        ev.set()                       # pre-set → stop before first inject
        report = synth.run(10, interval_s=5, stop_event=ev)
        assert report.injected == 0

    def test_run_custom_text_fn(self):
        pub = _FakePublisher()
        synth = MeshToRnsSynth(pub)
        synth.run(2, interval_s=0, text_fn=lambda seq, ui: f"custom-{seq}")
        bodies = [json.loads(c[1])["payload"]["text"] for c in pub.calls]
        assert bodies == ["custom-0", "custom-1"]

    def test_run_counts_failures_without_aborting(self):
        """A publisher that raises on one call doesn't sink the whole run."""
        class _FlakyPub:
            def __init__(self):
                self.n = 0
            def __call__(self, topic, payload):
                self.n += 1
                if self.n == 2:
                    raise RuntimeError("broker hiccup")
        synth = MeshToRnsSynth(_FlakyPub())
        report = synth.run(3, interval_s=0)
        assert report.injected == 2
        assert report.failed == 1

    def test_report_as_dict(self):
        pub = _FakePublisher()
        synth = MeshToRnsSynth(pub)
        d = synth.run(1, interval_s=0).as_dict()
        assert d["injected"] == 1 and d["failed"] == 0
        assert d["records"][0]["node_id"] == "!5e700000"


# ---------------------------------------------------------------------------
# CLI (dry-run — no broker)
# ---------------------------------------------------------------------------

class TestCli:

    def test_dry_run_prints_and_exits_zero(self, capsys):
        from lab.mesh_to_rns_synth import main
        rc = main(["--dry-run", "--count", "2", "--interval", "0",
                   "--channel-name", "LongFast"])
        assert rc == 0
        out = capsys.readouterr().out
        # Two PUBLISH lines on the contract topic + broadcast payload.
        assert out.count("PUBLISH msh/2/json/LongFast/!5e700000") == 2
        assert '"to": 4294967295' in out
        assert '"payload": {"text":' in out

    def test_dry_run_custom_text(self, capsys):
        from lab.mesh_to_rns_synth import main
        rc = main(["--dry-run", "--count", "1", "--interval", "0",
                   "--text", "hello world", "--user-index", "3"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "!5e700003" in out
        assert "hello world" in out
