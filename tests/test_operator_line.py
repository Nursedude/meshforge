"""Operator line: an LXMF address operators message from Sideband / Columba /
MeshHub, and that pages them when the watchdog sees trouble (2026-10-13).

What these tests pin (the security model is the feature):
  * closed by default — an empty operator list answers NOBODY;
  * only a signature-validated sender counts (a claimed hash is not a sender);
  * strangers get SILENCE (no auto-reply amplification), counted as a witness;
  * answers come from the read-only oracle engine — no new way onto RF;
  * an unobservable watchdog pages BLIND once and never a false CLEARED.
RNS/LXMF are MagicMocks injected through the constructor, as in
test_meshtastic_broadcast_bridge.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gateway.operator_line import (
    AlertTracker,
    OperatorLine,
    compose_page,
    create_from_gateway_config,
    get_active_operator_line,
)
from gateway.operator_line_config import OperatorLineConfig
from oracle import MAX_REPLY_BYTES, NocSnapshot, answer

OP = "a1" * 16
OP2 = "b2" * 16
STRANGER = "cc" * 16


@pytest.fixture(autouse=True)
def _allow_rns_tx_in_this_file():
    """Mocked routers only; the fail-closed RNS egress guard needs the intent declared."""
    from utils import tx_guard
    with tx_guard.allow_rns_egress():
        yield


def _snap(signals=None, *, installed=True, stale=False, reason=None, box="moc3"):
    s = NocSnapshot(now=1000.0, box=box)
    s.wd_installed = installed
    s.wd_stale = stale
    s.wd_reason = reason
    s.wd_ok = installed and not stale
    s.wd_signals = signals or []
    return s


def _sig(cls, subject, severity="degraded"):
    return {"class": cls, "subject": subject, "severity": severity}


def _cfg(**kw):
    base = dict(enabled=True, operators=[OP], display_name="Operator line (test)",
                announce_interval_sec=0, cooldown_s=10.0, alert_poll_s=30.0,
                alert_min_interval_s=120.0)
    base.update(kw)
    return OperatorLineConfig.from_dict(base)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _line(cfg=None, snap=None, clock=None, rns=None, lxmf=None, tmp_path=None):
    sent = []
    line = OperatorLine(
        cfg or _cfg(),
        snapshot_fn=lambda: snap if snap is not None else _snap(),
        deliver_fn=lambda dest, body: sent.append((dest, body)) or True,
        now_fn=clock or Clock(),
        rns_module=rns, lxmf_module=lxmf,
        identity_path=(tmp_path / "id") if tmp_path else None,
        storage_path=(tmp_path / "store") if tmp_path else None,
    )
    return line, sent


# ── config ────────────────────────────────────────────────────────────────

def test_defaults_are_off_and_closed():
    c = OperatorLineConfig.from_dict(None)
    assert c.enabled is False and c.operators == [] and c.closed is True


def test_operator_hashes_are_normalised_and_bad_ones_refused_loudly():
    c = OperatorLineConfig.from_dict({"operators": [OP.upper(), " " + OP2, "nothex", "abc"]})
    assert c.operators == [OP, OP2]
    assert len(c.errors) == 2 and all("operators" in e for e in c.errors)


def test_operators_as_a_string_is_an_error_not_a_list_of_characters():
    c = OperatorLineConfig.from_dict({"operators": OP})
    assert c.operators == [] and c.errors


def test_gateway_config_round_trips_the_section(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig
    monkeypatch.setattr(GatewayConfig, "get_config_path", classmethod(lambda cls: tmp_path / "gateway.json"))
    g = GatewayConfig()
    assert g.operator_line.enabled is False
    g.operator_line = _cfg(operators=[OP, OP2])
    g.save()
    back = GatewayConfig.load()
    assert back.operator_line.enabled is True
    assert back.operator_line.operators == [OP, OP2]
    assert back.operator_line.cooldown_s == 10.0


# ── inbound: who gets an answer ───────────────────────────────────────────

def test_operator_gets_the_oracle_answer():
    snap = _snap([_sig("rns_rpc_unresponsive", "rnsd")])
    line, _ = _line(snap=snap)
    assert line.respond(OP, "status", signature_validated=True) == answer("status", snap)
    assert line.stats["answered"] == 1


def test_empty_message_gets_help():
    snap = _snap()
    line, _ = _line(snap=snap)
    assert line.respond(OP, "   ", signature_validated=True) == answer("help", snap)


def test_unverified_sender_is_ignored_even_when_listed():
    line, _ = _line()
    assert line.respond(OP, "status", signature_validated=False) is None
    assert line.respond(OP, "status", signature_validated=None) is None
    assert line.stats["unverified"] == 2 and line.stats["answered"] == 0


def test_stranger_gets_silence_and_a_counter():
    line, _ = _line()
    assert line.respond(STRANGER, "status", signature_validated=True) is None
    assert line.stats["strangers"] == 1


def test_enabled_with_no_operators_answers_nobody():
    line, _ = _line(cfg=_cfg(operators=[]))
    assert line.respond(OP, "status", signature_validated=True) is None
    assert line.get_status()["closed"] is True


def test_cooldown_per_sender():
    clock = Clock()
    line, _ = _line(cfg=_cfg(operators=[OP, OP2]), clock=clock)
    assert line.respond(OP, "status", signature_validated=True)
    assert line.respond(OP, "status", signature_validated=True) is None   # 0 s later
    assert line.respond(OP2, "status", signature_validated=True)          # other sender unaffected
    clock.t += 10.1
    assert line.respond(OP, "status", signature_validated=True)
    assert line.stats["cooled"] == 1


def test_answers_fit_one_lxmf_over_lora_reply():
    line, _ = _line(snap=_snap([_sig(f"class_{i}", f"subject_{i}") for i in range(40)]))
    for q in ("status", "whatsup", "wd", "help", "node !a1b2c3d4"):
        out = line.respond(OP, q, signature_validated=True)
        assert len(out.encode()) <= MAX_REPLY_BYTES, q
        line._last_reply.clear()


def test_there_is_no_relay_verb():
    """RF egress stays with the bridge's ingress policy — one door, one rule."""
    line, _ = _line()
    out = line.respond(OP, "relay !a1b2c3d4 hello", signature_validated=True)
    assert "relay" not in out.lower() or "try:" in out.lower()
    assert not hasattr(line, "relay")


# ── alerts ────────────────────────────────────────────────────────────────

def test_first_look_reports_what_is_already_wrong_once():
    t = AlertTracker()
    assert t.observe(_snap([_sig("a", "x")])) == ["ACTIVE at start: a(x) degraded"]
    assert t.observe(_snap([_sig("a", "x")])) == []


def test_quiet_start_says_nothing():
    assert AlertTracker().observe(_snap([])) == []


def test_new_cleared_and_escalated():
    t = AlertTracker()
    t.observe(_snap([_sig("a", "x")]))
    lines = t.observe(_snap([_sig("a", "x", "wedge"), _sig("b", "y")]))
    assert "ALERT wedge a(x) (was degraded)" in lines and "ALERT degraded b(y)" in lines
    assert t.observe(_snap([_sig("b", "y")])) == ["CLEARED a(x)"]


def test_info_severity_never_pages():
    t = AlertTracker()
    assert t.observe(_snap([_sig("a", "x", "info")])) == []


def test_blind_watchdog_pages_blind_once_and_never_a_false_clear():
    t = AlertTracker()
    t.observe(_snap([_sig("a", "x")]))
    blind = _snap([], stale=True, reason="stale 900s (threshold 300s)")
    assert t.observe(blind) == ["BLIND watchdog: stale 900s (threshold 300s)"]
    assert t.observe(blind) == []                      # once, not every poll
    # visible again with the condition gone: say so, THEN the real change
    assert t.observe(_snap([])) == ["VISIBLE watchdog readable again", "CLEARED a(x)"]


def test_unreadable_watchdog_is_blind_not_healthy():
    t = AlertTracker()
    t.observe(_snap([_sig("a", "x")]))
    lines = t.observe(_snap(installed=False, reason="no_state_file"))
    assert lines == ["BLIND watchdog: no_state_file"]
    assert t.active == {"a(x)": "degraded"}           # held, not cleared


def test_compose_page_coalesces_and_caps():
    page = compose_page("moc3", [f"ALERT degraded c{i}(s{i})" for i in range(30)])
    assert page.startswith("moc3: ALERT degraded c0(s0)")
    assert len(page.encode()) <= MAX_REPLY_BYTES and "more" in page


def test_pages_are_rate_limited_and_coalesced():
    clock = Clock()
    snaps = iter([_snap([]), _snap([_sig("a", "x")]), _snap([_sig("a", "x"), _sig("b", "y")]),
                  _snap([_sig("a", "x"), _sig("b", "y")])])
    line, sent = _line(cfg=_cfg(operators=[OP, OP2]), clock=clock)
    line._snapshot_fn = lambda: next(snaps)
    line.alert_tick()                                  # quiet baseline
    line.alert_tick()                                  # a(x) -> page now
    assert [d for d, _ in sent] == [OP, OP2] and "a(x)" in sent[0][1]
    clock.t += 30
    line.alert_tick()                                  # b(y) inside min interval -> held
    assert len(sent) == 2
    clock.t += 120
    line.alert_tick()                                  # interval passed -> one coalesced page
    assert len(sent) == 4 and "b(y)" in sent[2][1]


def test_alerts_can_be_switched_off():
    line, sent = _line(cfg=_cfg(alerts_enabled=False), snap=_snap([_sig("a", "x")]))
    line.alert_tick()
    assert sent == []


def test_failed_page_delivery_leaves_a_witness():
    line = OperatorLine(_cfg(), snapshot_fn=lambda: _snap([_sig("a", "x")]),
                        deliver_fn=lambda d, b: False, now_fn=Clock())
    line.alert_tick()
    assert line.stats["deliver_failed"] == 1


# ── LXMF plumbing with mocked modules ─────────────────────────────────────

@pytest.fixture
def fake_rns_lxmf():
    rns = MagicMock(name="RNS")
    rns.Identity.from_file = MagicMock(side_effect=Exception("no file"))
    rns.Transport.has_path = MagicMock(return_value=True)
    rns.Identity.recall = MagicMock(return_value=MagicMock(name="dest_identity"))
    rns.Destination.OUT, rns.Destination.SINGLE = "OUT", "SINGLE"
    lxmf = MagicMock(name="LXMF")
    router = MagicMock(name="LXMRouter")
    src = MagicMock()
    src.hash = bytes.fromhex("0123456789abcdef0123456789abcdef")
    router.register_delivery_identity.return_value = src
    lxmf.LXMRouter.return_value = router
    return rns, lxmf, router


def _real_line(tmp_path, fake, cfg=None):
    rns, lxmf, _ = fake
    return OperatorLine(cfg or _cfg(), snapshot_fn=lambda: _snap(), rns_module=rns,
                        lxmf_module=lxmf, identity_path=tmp_path / "id",
                        storage_path=tmp_path / "store", now_fn=Clock())


def test_start_registers_its_own_identity_and_announces(tmp_path, fake_rns_lxmf):
    _, _, router = fake_rns_lxmf
    line = _real_line(tmp_path, fake_rns_lxmf)
    try:
        assert line.start() is True
        router.register_delivery_identity.assert_called_once()
        assert router.register_delivery_identity.call_args.kwargs["display_name"] == "Operator line (test)"
        router.announce.assert_called_once()
        assert line.destination_hash_hex == "0123456789abcdef0123456789abcdef"
        assert get_active_operator_line() is line
    finally:
        line.stop()
    assert get_active_operator_line() is None


def test_inbound_delivery_verifies_then_queues_the_work(tmp_path, fake_rns_lxmf):
    line = _real_line(tmp_path, fake_rns_lxmf)
    line.start()
    try:
        msg = MagicMock(source_hash=bytes.fromhex(OP), content=b"status",
                        signature_validated=True)
        forged = MagicMock(source_hash=bytes.fromhex(OP), content=b"status",
                           signature_validated=False, unverified_reason=0x02)
        line._on_lxmf_delivery(forged)
        line._on_lxmf_delivery(msg)
        assert line.stats["unverified"] == 1
        assert line._work.qsize() <= 1  # the worker may already have taken it
    finally:
        line.stop()


def test_deliver_builds_an_lxmf_message_from_the_line_identity(tmp_path, fake_rns_lxmf):
    rns, lxmf, router = fake_rns_lxmf
    line = _real_line(tmp_path, fake_rns_lxmf)
    line.start()
    try:
        assert line._deliver_lxmf(OP, "hello") is True
        lxmf.LXMessage.assert_called_once()
        args = lxmf.LXMessage.call_args.args
        assert args[1] is line._lxmf_source and args[2] == "hello"
        router.handle_outbound.assert_called_once()
    finally:
        line.stop()


def test_deliver_without_identity_recall_fails_honestly(tmp_path, fake_rns_lxmf):
    rns, _, router = fake_rns_lxmf
    rns.Identity.recall.return_value = None
    line = _real_line(tmp_path, fake_rns_lxmf)
    line.start()
    try:
        assert line._deliver_lxmf(OP, "hello") is False
        router.handle_outbound.assert_not_called()
    finally:
        line.stop()


def test_factory_honours_enabled_is_true_only():
    gc = MagicMock()
    gc.operator_line = OperatorLineConfig.from_dict({"enabled": False})
    assert create_from_gateway_config(gc) is None
    gc.operator_line = MagicMock()          # a mocked config must never start it
    assert create_from_gateway_config(gc) is None


# ── gateway hooks ─────────────────────────────────────────────────────────

def _gw(cfg):
    from types import SimpleNamespace
    from gateway.rns_bridge import RNSMeshtasticBridge
    gw = SimpleNamespace(config=SimpleNamespace(operator_line=cfg), _operator_line=None)
    gw.start_line = RNSMeshtasticBridge._maybe_start_operator_line.__get__(gw)
    gw.stop_line = RNSMeshtasticBridge._stop_operator_line.__get__(gw)
    return gw


def test_gateway_starts_the_line_only_when_enabled(monkeypatch):
    import gateway.operator_line as ol
    started = []
    monkeypatch.setattr(ol.OperatorLine, "start", lambda self: started.append(self) or True)
    gw = _gw(OperatorLineConfig.from_dict({"enabled": False, "operators": [OP]}))
    gw.start_line()
    assert gw._operator_line is None and started == []
    gw = _gw(_cfg())
    gw.start_line()
    gw.start_line()                       # idempotent on every RNS reconnect
    assert isinstance(gw._operator_line, ol.OperatorLine) and len(started) == 2


def test_gateway_start_failure_is_contained_and_stop_is_safe(monkeypatch):
    import gateway.operator_line as ol
    monkeypatch.setattr(ol.OperatorLine, "start", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    gw = _gw(_cfg())
    gw.start_line()                       # must not raise into the RNS loop
    gw.stop_line()
    _gw(_cfg()).stop_line()               # stop with nothing started


# ── identity warm-up (found on the REAL stack: a listed operator who announced
#    before the line started read "source unknown" and got silence) ─────────

def test_warmup_requests_paths_for_operators_it_cannot_recall(tmp_path, fake_rns_lxmf):
    rns, _, _ = fake_rns_lxmf
    known = {bytes.fromhex(OP2)}
    rns.Identity.recall = MagicMock(side_effect=lambda h, **kw: object() if h in known else None)
    line = _real_line(tmp_path, fake_rns_lxmf, cfg=_cfg(operators=[OP, OP2]))
    out = line.warmup(settle_s=0)
    rns.Transport.request_path.assert_called_once_with(bytes.fromhex(OP))
    assert out == {"operators": 2, "known": 1, "requested": 1, "after": 1, "failed": 0}
    assert line.get_status()["warmup"] == out
    # LXMF validates with recall(_no_use=True) — a plain recall is an RPC to rnsd (#72)
    assert all(c.kwargs.get("_no_use") is True for c in rns.Identity.recall.call_args_list)


def test_unknown_source_from_a_listed_operator_nudges_once(tmp_path, fake_rns_lxmf):
    rns, _, _ = fake_rns_lxmf
    line = _real_line(tmp_path, fake_rns_lxmf)
    line.start()
    try:
        rns.Transport.request_path.reset_mock()
        unknown = dict(source_hash=bytes.fromhex(OP), content=b"status",
                       signature_validated=False, unverified_reason=0x01)
        line._on_lxmf_delivery(MagicMock(**unknown))
        line._on_lxmf_delivery(MagicMock(**unknown))
        for t in list(line._threads):
            if t.name == "OperatorLine-Nudge":
                t.join(timeout=3)
        assert rns.Transport.request_path.call_count == 1      # once per window
        assert line.stats["unverified"] == 2                    # still never answered
        assert line.stats["nudged"] == 1
    finally:
        line.stop()


def test_strangers_are_never_nudged(tmp_path, fake_rns_lxmf):
    rns, _, _ = fake_rns_lxmf
    line = _real_line(tmp_path, fake_rns_lxmf)
    line.start()
    try:
        rns.Transport.request_path.reset_mock()
        line._on_lxmf_delivery(MagicMock(source_hash=bytes.fromhex(STRANGER), content=b"x",
                                         signature_validated=False, unverified_reason=0x01))
        assert rns.Transport.request_path.call_count == 0
    finally:
        line.stop()
