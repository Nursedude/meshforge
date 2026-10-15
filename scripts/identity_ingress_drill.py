"""NON-AUTHOR adversarial drill of the 2026-10-07 identity/ingress arc.

Every ``test_ATTACK_*`` PASSES WHEN THE ATTACK SUCCEEDS (it documents a
confirmed weakness, not a guard). ``test_PIN_*`` are the refutations: real
library behaviour the gate relies on, measured here with the installed
RNS/LXMF and NO Reticulum instance / rnsd / network.

Real objects everywhere the review is aimed: real ``RNS.Identity`` key pairs,
real ``LXMF.LXMessage.pack()`` / ``unpack_from_bytes`` (forged source field),
real ``MeshOracleResponder``, real ``IngressLedger``, real housekeeping
``remove()``/``build_rows()``, real census ``scan()``. Sinks (send_fn, the
node tracker, messages.store_incoming, the MeshCore handler) are recorders.

Reviewer worktree only — not for the suite as-is.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import types
from pathlib import Path
from queue import Queue

import pytest

RNS = pytest.importorskip("RNS")
LXMF = pytest.importorskip("LXMF")
from LXMF import APP_NAME, LXMessage  # noqa: E402

from gateway import rns_ingress_policy as pol  # noqa: E402
from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin  # noqa: E402
from gateway.config import GatewayConfig, RNSConfig  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import lxmf_identity_census as census  # noqa: E402
import rns_identity_housekeeping as hk  # noqa: E402

DL = LXMessage.DESTINATION_LENGTH
MA_SRC = Path("/opt/meshanchor/src")


# ── real LXMF material (no Reticulum instance) ───────────────────────────
def _dest(ident):
    # OUT: IN would call Transport.register_destination (needs an owner).
    # OUT with a full identity hashes identically and can sign.
    return RNS.Destination(ident, RNS.Destination.OUT, RNS.Destination.SINGLE,
                           APP_NAME, "delivery")


@pytest.fixture
def world():
    A, B, G = RNS.Identity(), RNS.Identity(), RNS.Identity()
    a, b, g = _dest(A), _dest(B), _dest(G)
    for d, i in ((a, A), (b, B), (g, G)):  # the receiver heard their announces
        RNS.Identity.remember(packet_hash=os.urandom(32), destination_hash=d.hash,
                              public_key=i.get_public_key())
    return types.SimpleNamespace(A=A, B=B, G=G, a=a, b=b, g=g)


def _genuine(w, text: str) -> bytes:
    m = LXMessage(w.g, w.a, text, "t", desired_method=LXMessage.DIRECT)
    m.defer_stamp = True
    m.pack()
    return m.packed


def _forged_as(w, text: str, claimed: bytes) -> LXMessage:
    """Signed by A, source field rewritten to ``claimed`` — what an attacker
    who knows a listed hash (announces publish them) can put on the wire."""
    raw = _genuine(w, text)
    return LXMessage.unpack_from_bytes(raw[:DL] + claimed + raw[2 * DL:])


# ── the gateway host (same shape the author's own tests use) ────────────
class _Tracker:
    def __init__(self):
        self.seen = []

    def add_node(self, node):
        self.seen.append(node)


def _bridge(tmp_path, listed_hex, policy="enforce", oracle=None):
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.rns = RNSConfig(bridge_source_identities=[listed_hex],
                        bridge_source_policy=policy,
                        peer_gateway_destinations=[])
    b = types.SimpleNamespace()
    b.config = cfg
    b.stats = {"errors": 0}
    b._stats_lock = threading.Lock()
    b._rns_to_mesh_queue = Queue()
    b._oracle_rns = oracle
    b.node_tracker = _Tracker()
    b.notified = []
    b._notify_message = lambda m: b.notified.append(m)
    b._router = types.SimpleNamespace(should_bridge=lambda m: True)
    b._peer_gateway_hash_set = lambda: set()
    b._rns_ingress_ledger_path = tmp_path / "ledger.json"
    b._lxmf_identity_registry_dir = tmp_path
    b._rns_request_path = lambda h: None
    for name in ("_on_lxmf_receive", "_rns_ingress_ledger", "rns_ingress_posture",
                 "_rns_ingress_admits", "_lxmf_identity_registry",
                 "_rns_ingress_path_nudge"):
        setattr(b, name, getattr(BridgeRnsEventsMixin, name).__get__(b))
    return b


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("gateway.rns_bridge.HAS_RNS_SNIFFER", False)


# ═════════════════════════════ PINS (refuted bypass) ═════════════════════
def test_PIN_library_marks_forged_source_SIGNATURE_INVALID(world):
    w = world
    assert LXMessage.unpack_from_bytes(_genuine(w, "hi")).signature_validated is True
    f = _forged_as(w, "hi", w.b.hash)
    assert f.source_hash == w.b.hash                      # the CLAIM survives
    assert f.signature_validated is False
    assert f.unverified_reason == LXMessage.SIGNATURE_INVALID
    u = _forged_as(w, "hi", os.urandom(DL))
    assert u.signature_validated is False
    assert u.unverified_reason == LXMessage.SOURCE_UNKNOWN


def test_PIN_forged_listed_claim_is_REFUSED_under_enforce(world, tmp_path):
    w = world
    b = _bridge(tmp_path, w.b.hash.hex())
    b._on_lxmf_receive(_forged_as(w, "hello RF", w.b.hash))
    assert b._rns_to_mesh_queue.empty()
    assert b.stats.get("rns_to_mesh_refused_unlisted", 0) == 1
    snap = b._rns_ingress_ledger().snapshot()
    assert snap["unlisted_recent"][0]["unverified"] == 1


# ═══════════════════════════════ ATTACKS ═════════════════════════════════
def test_ATTACK_forged_listed_claim_is_answered_by_oracle_before_the_gate(
        world, tmp_path):
    """The oracle RNS leg runs BEFORE the ingress gate and keys its allowlist
    on the CLAIMED hash. A stranger claiming an oracle-allowlisted hash gets
    the gateway to answer — the reply is addressed to the VICTIM's hash,
    burns the victim's cooldown bucket, and (consume=True) the message never
    reaches the ingress ledger, so the forgery leaves no refusal witness."""
    from oracle.responder import MeshOracleResponder
    from oracle.snapshot import NocSnapshot
    w = world
    sent = []
    oracle = MeshOracleResponder(
        snapshot_fn=lambda: NocSnapshot(now=0.0),
        send_fn=lambda t, dest, ch: sent.append(dest) or True,
        allowlist={w.b.hash.hex()}, transport="rns")
    b = _bridge(tmp_path, w.b.hash.hex(), oracle=oracle)
    b._on_lxmf_receive(_forged_as(w, "help", w.b.hash))
    assert sent == [w.b.hash.hex()], "oracle answered a forged claim"
    assert b.stats.get("rns_to_mesh_refused_unlisted", 0) == 0, \
        "consumed before the gate: no refusal witness"
    # and the real B is now in cooldown for a query it never sent
    sent.clear()
    assert oracle.handle(w.b.hash.hex(), "help", 0) is None
    assert sent == []


def test_ATTACK_forged_claim_poisons_node_tracker_and_message_history(
        world, tmp_path, monkeypatch):
    """Attribution forgery: node_tracker and messages.db record the CLAIMED
    (victim) hash for a message the victim never sent — under ENFORCE, where
    the bridge itself refused it."""
    stored = []
    import commands.messaging as messaging
    monkeypatch.setattr(messaging, "store_incoming",
                        lambda **kw: stored.append(kw))
    w = world
    b = _bridge(tmp_path, w.b.hash.hex())
    b._on_lxmf_receive(_forged_as(w, "I am B, trust me", w.b.hash))
    assert b._rns_to_mesh_queue.empty()                   # gate held …
    assert [n.id for n in b.node_tracker.seen] == [f"rns_{w.b.hash.hex()[:16]}"]
    assert stored and stored[0]["from_id"] == w.b.hash.hex()   # … history lied
    assert b.notified and b.notified[0].source_id == w.b.hash.hex()


_MA_SCRIPT = r'''
import os, sys, types, threading
from queue import Queue
sys.path.insert(0, "/opt/meshanchor/src")
import RNS
from LXMF import APP_NAME, LXMessage
DL = LXMessage.DESTINATION_LENGTH
def dest(i): return RNS.Destination(i, RNS.Destination.OUT, RNS.Destination.SINGLE, APP_NAME, "delivery")
A, B, G = RNS.Identity(), RNS.Identity(), RNS.Identity()
a, b, g = dest(A), dest(B), dest(G)
for d, i in ((a, A), (b, B), (g, G)):
    RNS.Identity.remember(packet_hash=os.urandom(32), destination_hash=d.hash, public_key=i.get_public_key())
def genuine(text):
    m = LXMessage(g, a, text, "t", desired_method=LXMessage.DIRECT); m.defer_stamp = True; m.pack(); return m.packed
raw = genuine("[meshtastic ch0:evil] hello MeshCore")
forged = LXMessage.unpack_from_bytes(raw[:DL] + b.hash + raw[2*DL:])
assert forged.signature_validated is False
# 1) re-emit bridge: allowlist keyed on the CLAIMED hash, hook never sees the signature
from gateway.config import MeshtasticReemitConfig
from gateway.meshtastic_reemit_bridge import MeshtasticReemitBridge
sent = []
class H:
    def send_text(self, *a, **k): sent.append((a, k)); return True
br = MeshtasticReemitBridge(MeshtasticReemitConfig(enabled=True, source_identities=[b.hash.hex()]), handler_getter=lambda: H())
br.start()
ok = br.on_lxmf_message(forged.source_hash, forged.content)
print("REEMIT", ok, len(sent), br.stats.get("reemitted"))
# 2) no ingress gate: a validated STRANGER (A, listed nowhere) is queued for RF
from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin
import gateway.rns_bridge as hub
hub.HAS_RNS_SNIFFER = False
host = types.SimpleNamespace()
host.node_tracker = types.SimpleNamespace(add_node=lambda n: None)
host.stats = {"errors": 0}; host._stats_lock = threading.Lock()
host._rns_to_mesh_queue = Queue()
host._router = types.SimpleNamespace(should_bridge=lambda m: True)
host._notify_message = lambda m: None
host._meshtastic_reemit = None
host._on_lxmf_receive = BridgeRnsEventsMixin._on_lxmf_receive.__get__(host)
stranger = LXMessage.unpack_from_bytes(genuine("stranger on your RF"))
assert stranger.signature_validated is True
host._on_lxmf_receive(stranger)
print("QUEUED", host._rns_to_mesh_queue.qsize())
print("HAS_GATE", hasattr(BridgeRnsEventsMixin, "_rns_ingress_admits"))
'''


@pytest.mark.skipif(not MA_SRC.exists(), reason="MeshAnchor checkout absent")
def test_ATTACK_meshanchor_reemits_forged_claim_and_has_no_ingress_gate(tmp_path):
    """MeshAnchor: (1) the LXMF→MeshCore re-emit allowlist is keyed on the
    claimed source hash and its hook receives no signature → a forged claim
    of the broadcast-bridge identity is re-emitted onto MeshCore RF.
    (2) ``_on_lxmf_receive`` has no ingress gate at all → any validated
    stranger is queued for RF when the router's rules allow."""
    script = tmp_path / "ma_drill.py"
    script.write_text(_MA_SCRIPT, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(MA_SRC))
    r = subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, timeout=120, env=env, cwd=str(tmp_path))
    out = r.stdout
    assert r.returncode == 0, r.stderr[-2000:]
    assert "REEMIT True 1 1" in out, out
    assert "QUEUED 1" in out, out
    assert "HAS_GATE False" in out, out


def test_ATTACK_newly_listed_identity_reads_stale_the_hour_it_is_added(tmp_path):
    """watch_since is a LEDGER-wide clock, not per identity: a hash added to
    the allowlist on a gateway whose ledger has watched >14 d is judged
    'stale' immediately, and the housekeeping report prints the --remove
    command for it."""
    now = [1_800_000_000.0]
    led = pol.IngressLedger(tmp_path / pol.LEDGER_FILENAME, now_fn=lambda: now[0])
    old, new = "a" * 32, "b" * 32
    led.stamp(policy="enforce", listed=1, identities=[old])
    led.note_listed(old)
    now[0] += 15 * 86400                                  # 15 d of watching
    led.note_listed(old)
    led.stamp(policy="enforce", listed=2, identities=[old, new])  # operator adds B, restarts
    now[0] += 3600                                        # one hour later
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "gateway.json").write_text(json.dumps(
        {"rns": {"bridge_source_identities": [old, new]}}), encoding="utf-8")
    rows = {r["hash"]: r for r in hk.build_rows(cfg, led.path, now=now[0])}
    assert rows[old]["verdict"] == "active"
    assert rows[new]["verdict"] == "stale", rows[new]


def test_ATTACK_remove_backup_copies_secrets_at_umask_mode(tmp_path):
    """--remove backs gateway.json (PSK / MQTT password live in it) up with
    plain write_text: a 0600 original yields a 0644 backup. The tmp file is
    also created at umask mode and chmod'ed only afterwards."""
    cfg = tmp_path
    p = cfg / "gateway.json"
    victim = "c" * 32
    p.write_text(json.dumps({"rns": {"bridge_source_identities": [victim],
                                     "downlink_psk": "SECRET"}}), encoding="utf-8")
    os.chmod(p, 0o600)
    old = os.umask(0o022)
    try:
        hk.remove(cfg, [victim], stamp="T")
    finally:
        os.umask(old)
    bak = cfg / "gateway.json.bak-housekeeping-T"
    assert "SECRET" in bak.read_text()
    assert stat.S_IMODE(bak.stat().st_mode) == 0o644, oct(bak.stat().st_mode)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600      # the live file is fine


def test_ATTACK_remove_is_lost_to_a_concurrent_config_save(tmp_path, monkeypatch):
    """No lock / no re-read: a process holding gateway.json in memory (TUI,
    launcher, the gateway itself) that saves after --remove writes the
    removed hash straight back."""
    p = tmp_path / "gateway.json"
    keep, victim = "d" * 32, "e" * 32
    p.write_text(json.dumps({"rns": {"bridge_source_identities": [keep, victim]}}),
                 encoding="utf-8")
    monkeypatch.setattr(GatewayConfig, "get_config_path", classmethod(lambda cls: p))
    cfg = GatewayConfig.load()                            # TUI loaded earlier …
    assert set(cfg.rns.get_bridge_source_identities()) == {keep, victim}
    hk.remove(tmp_path, [victim], stamp="T")              # … operator removes …
    assert victim not in json.loads(p.read_text())["rns"]["bridge_source_identities"]
    assert cfg.save() is True                             # … TUI toggles anything
    after = json.loads(p.read_text())["rns"]["bridge_source_identities"]
    assert victim in after, after                         # resurrected


def test_ATTACK_census_is_blind_to_symlinked_identities_without_witness(tmp_path):
    """A symlinked directory (``~/.reticulum -> /etc/reticulum``) or a
    symlinked identity file is skipped silently: zero rows, zero BLIND
    lines, zero depth-capped lines — 'unobservable is not empty' violated."""
    home = tmp_path / "home"
    outside = tmp_path / "etc_reticulum" / "storage"
    (home / ".config" / "meshforge").mkdir(parents=True)
    outside.mkdir(parents=True)
    RNS.Identity().to_file(str(outside / "transport_identity"))
    RNS.Identity().to_file(str(tmp_path / "real_gateway_identity"))
    assert (outside / "transport_identity").stat().st_size == census.IDENTITY_SIZE
    os.symlink(outside.parent, home / ".reticulum")                      # dir link
    os.symlink(tmp_path / "real_gateway_identity",
               home / ".config" / "meshforge" / "gateway_identity")      # file link
    rows = census.scan(home)
    found, blind, capped, *_ = census._walk(home)
    assert rows == [] and found == []
    assert blind == [] and capped == []                   # and nobody said so


def test_ATTACK_census_under_sudo_never_walks_root_or_etc(monkeypatch, tmp_path):
    """Docstring: 'Run with sudo to include root-owned identities (e.g. a
    root rnsd's)'. The walk root is get_real_user_home() → SUDO_USER's home;
    /root and /etc/reticulum (the fleet rnsd configdir) are never visited,
    so sudo only lifts PermissionError inside the operator's own tree."""
    from utils.paths import get_real_user_home
    monkeypatch.setenv("SUDO_USER", "operator")
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setattr("utils.paths._resolve_home_for_user",
                        lambda u: Path("/home") / u)
    assert get_real_user_home() == Path("/home/operator")
    src = (REPO / "scripts" / "lxmf_identity_census.py").read_text()
    assert "/etc/reticulum" not in src and "/root" not in src
