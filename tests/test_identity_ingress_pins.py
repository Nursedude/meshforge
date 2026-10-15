"""Pins from the non-author pass on the 10-07 identity/ingress arc
(2026-10-15, Fable 5.1 reviewer; drill ``scripts/identity_ingress_drill.py``).

The gate (`signature_validated is True`) held, but two consumers ran BEFORE
it on the CLAIMED source hash: the oracle RNS leg (finding 1) and the
attribution sinks — node tracker + message history (finding 6). Real RNS
identities, real LXMF pack/unpack with the source field rewritten; skipped
where RNS/LXMF are not installed.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

pytest.importorskip("RNS")
pytest.importorskip("LXMF")
import identity_ingress_drill as d  # noqa: E402

world = d.world
_quiet = d._quiet


def _oracle(w, sent, declined):
    from oracle.responder import MeshOracleResponder
    from oracle.snapshot import NocSnapshot
    o = MeshOracleResponder(snapshot_fn=lambda: NocSnapshot(now=0.0),
                            send_fn=lambda t, dest, ch: sent.append(dest) or True,
                            allowlist={w.b.hash.hex()}, transport="rns")
    real_decline = o.decline

    def spy(from_id, text, *, reason, channel=None):
        declined.append(reason)
        return real_decline(from_id, text, reason=reason, channel=channel)
    o.decline = spy
    return o


def test_the_oracle_never_answers_an_unverified_claim(world, tmp_path):
    w, sent, declined = world, [], []
    o = _oracle(w, sent, declined)
    b = d._bridge(tmp_path, w.b.hash.hex(), oracle=o)
    b._on_lxmf_receive(d._forged_as(w, "help", w.b.hash))
    assert sent == [], "the oracle answered a forged claim"
    assert declined == ["unverified_source"]
    assert b.stats.get("rns_to_mesh_refused_unlisted", 0) == 1, \
        "the forgery must still reach the ingress gate and leave its witness"
    # the real B is NOT in cooldown for a query it never sent
    assert o.handle(w.b.hash.hex(), "help", 0) is not None
    assert sent == [w.b.hash.hex()]


def test_a_genuine_listed_sender_still_reaches_the_oracle(world, tmp_path):
    from LXMF import LXMessage
    w, sent, declined = world, [], []
    b = d._bridge(tmp_path, w.a.hash.hex(), oracle=_oracle_for(w, w.a, sent, declined))
    b._on_lxmf_receive(LXMessage.unpack_from_bytes(d._genuine(w, "help")))
    assert sent == [w.a.hash.hex()] and declined == []


def _oracle_for(w, dest, sent, declined):
    from oracle.responder import MeshOracleResponder
    from oracle.snapshot import NocSnapshot
    return MeshOracleResponder(snapshot_fn=lambda: NocSnapshot(now=0.0),
                               send_fn=lambda t, dst, ch: sent.append(dst) or True,
                               allowlist={dest.hash.hex()}, transport="rns")


def test_a_forged_claim_is_not_recorded_as_the_victim(world, tmp_path, monkeypatch):
    stored = []
    import commands.messaging as messaging
    monkeypatch.setattr(messaging, "store_incoming", lambda **kw: stored.append(kw))
    w = world
    b = d._bridge(tmp_path, w.b.hash.hex())
    b._on_lxmf_receive(d._forged_as(w, "I am B, trust me", w.b.hash))
    assert b.node_tracker.seen == [], "a forged claim must not register the victim as a node"
    assert len(stored) == 1 and stored[0]["content"].startswith("[unverified sender] "), stored


def test_a_genuine_sender_is_recorded_plainly(world, tmp_path, monkeypatch):
    from LXMF import LXMessage
    stored = []
    import commands.messaging as messaging
    monkeypatch.setattr(messaging, "store_incoming", lambda **kw: stored.append(kw))
    w = world
    b = d._bridge(tmp_path, w.a.hash.hex())
    b._on_lxmf_receive(LXMessage.unpack_from_bytes(d._genuine(w, "hello")))
    assert len(b.node_tracker.seen) == 1
    assert stored and not stored[0]["content"].startswith("[unverified")


def test_an_observed_forgery_is_queued_marked_not_as_the_victim(world, tmp_path):
    w = world
    b = d._bridge(tmp_path, w.b.hash.hex(), policy="observe")
    b._on_lxmf_receive(d._forged_as(w, "hello RF", w.b.hash))
    assert b._rns_to_mesh_queue.qsize() == 1
    msg = b._rns_to_mesh_queue.get_nowait()
    assert msg.metadata["source_verified"] is False
    assert msg.content.startswith("[unverified] ")
    assert b.notified and b.notified[0].metadata["source_verified"] is False
