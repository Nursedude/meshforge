"""C21 cross-preset bridge run — the pure logic behind scripts/c21_bridge_run.py.

Plan: .claude/plans/c21_cross_preset_run_2026_10_18.md. Expected values here
are LITERALS from the plan / the fleet's measured channels, never recomputed
the way the script computes them.
"""
import importlib.util
from pathlib import Path

_p = Path(__file__).parent.parent / "scripts" / "c21_bridge_run.py"
_spec = importlib.util.spec_from_file_location("c21", _p)
c21 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c21)


# --- seam 2: what each radio is told, and the readback that must agree ------

def test_lf_leg_lands_on_the_private_lab_slot_at_one_dbm():
    plan = c21.radio_plan("leg_lf")
    assert plan["lora.modem_preset"] == "LONG_FAST"
    assert plan["lora.channel_num"] == 80
    assert plan["freq_mhz"] == 921.875           # plan table, US slot 80
    assert plan["lora.tx_power"] == 1            # 0 would mean REGION MAX
    assert plan["lora.hop_limit"] == 1
    assert plan["mqtt.enabled"] is False
    assert plan["channel0.name"] == "MFLab"


def test_st_side_shares_one_slot_and_differs_from_lf():
    leg, src = c21.radio_plan("leg_st"), c21.radio_plan("src_st")
    assert leg["lora.modem_preset"] == src["lora.modem_preset"] == "SHORT_TURBO"
    assert leg["freq_mhz"] == src["freq_mhz"] == 924.25   # US slot 45
    assert c21.radio_plan("src_lf")["freq_mhz"] == 921.875


def test_lab_slots_clear_every_fleet_frequency():
    # fleet LF ch20, fleet ST ch8, regional RNS — measured, not derived
    fleet = (906.875, 905.75, 903.625)
    for role in c21.ROLES:
        f = c21.radio_plan(role)["freq_mhz"]
        assert all(abs(f - x) >= 1.0 for x in fleet), (role, f)


def test_readback_agreeing_with_the_plan_has_no_mismatch():
    plan = c21.radio_plan("leg_lf")
    got = {k: v for k, v in plan.items() if k != "freq_mhz"}
    assert c21.readback_mismatches(plan, got) == []


def test_readback_missing_a_field_is_a_mismatch_not_a_pass():
    plan = c21.radio_plan("leg_lf")
    got = {k: v for k, v in plan.items() if k not in ("freq_mhz", "lora.hop_limit")}
    assert [m[0] for m in c21.readback_mismatches(plan, got)] == ["lora.hop_limit"]


def test_readback_at_region_max_power_is_refused():
    plan = c21.radio_plan("src_st")
    got = {k: v for k, v in plan.items() if k != "freq_mhz"}
    got["lora.tx_power"] = 0
    assert c21.readback_mismatches(plan, got) == [("lora.tx_power", 1, 0)]


def test_psk_mismatch_is_reported_without_the_key():
    plan = c21.radio_plan("leg_lf", psk=b"\x01" * 32)
    got = {k: v for k, v in plan.items() if k != "freq_mhz"}
    got["channel0.psk"] = b"\x02" * 32
    (m,) = c21.readback_mismatches(plan, got)
    assert m[0] == "channel0.psk"
    assert b"\x01" not in repr(m).encode() and "01010101" not in repr(m)


# --- seam 3: the run's bridge config, through the REAL gateway loader -------

LF = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_LF-if00-port0"
ST = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_ST-if00-port0"


def test_bridge_config_loads_as_two_serial_legs(tmp_path):
    path = tmp_path / "gateway.json"
    c21.write_bridge_config(path, leg_lf=LF, leg_st=ST)
    cfg = c21.load_bridge_config(path).mesh_bridge
    assert cfg.enabled is True
    assert (cfg.primary.connection_type, cfg.primary.serial_device,
            cfg.primary.preset) == ("serial", LF, "LONG_FAST")
    assert (cfg.secondary.connection_type, cfg.secondary.serial_device,
            cfg.secondary.preset) == ("serial", ST, "SHORT_TURBO")
    assert cfg.channels == [0]
    assert cfg.add_prefix is True and cfg.prefix_format.startswith("[Mesh:")


def test_loading_the_run_config_leaves_the_operators_path_alone(tmp_path):
    from gateway.config import GatewayConfig
    before = GatewayConfig.get_config_path
    path = tmp_path / "gateway.json"
    c21.write_bridge_config(path, leg_lf=LF, leg_st=ST)
    c21.load_bridge_config(path)
    assert GatewayConfig.get_config_path == before


def test_a_non_by_id_port_is_refused(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="by-id"):
        c21.write_bridge_config(tmp_path / "g.json", leg_lf="/dev/ttyUSB0", leg_st=ST)


def test_a_queue_left_over_from_an_earlier_run_is_reported(tmp_path):
    from gateway.message_queue import PersistentMessageQueue
    assert c21.leftover_queue_depth(tmp_path / "absent") == {}
    q = PersistentMessageQueue(db_path=str(tmp_path / "p2s.db"))
    q.enqueue(payload={"content": "old"}, destination="secondary")
    PersistentMessageQueue(db_path=str(tmp_path / "s2p.db"))
    assert c21.leftover_queue_depth(tmp_path) == {"p2s.db": 1}


# --- seam 4: the message text, before and after the bridge tags it ---------

def test_message_text_is_the_planned_shape():
    assert c21.format_msg("r1", 7, "lf2st") == "c21 r1 7 lf2st"


def test_parse_reads_the_untagged_send():
    assert c21.parse_msg("c21 r1 7 lf2st") == ("r1", 7, "lf2st", None)


def test_parse_reads_the_bridged_copy():
    assert c21.parse_msg("[Mesh:LONG_FAST] c21 r1 7 lf2st") == (
        "r1", 7, "lf2st", "LONG_FAST")


def test_parse_ignores_anything_that_is_not_ours():
    for text in ("hello", "c21 r1 x lf2st", "c21 r1 7 sideways", "", "[Mesh:LF] hi"):
        assert c21.parse_msg(text) is None, text


def test_loop_control_carries_a_bridge_tag_before_it_is_sent():
    text = c21.loop_control_msg("r1", 9, "st2lf")
    assert text.startswith("[Mesh:")
    assert c21.parse_msg(text)[:3] == ("r1", 9, "st2lf")


def test_loop_control_is_one_the_real_loop_guard_refuses():
    from gateway.base_handler import is_already_bridged
    assert is_already_bridged(c21.loop_control_msg("r1", 9, "st2lf"))


# --- seam 5: reconcile = the approved acceptance sentence, as a verdict -----
# Events are what the run records; labels below are written out by hand.

def _send(seq, d="lf2st", kind="normal", text=None):
    return {"ev": "send", "kind": kind,
            "text": text or c21.format_msg("r1", seq, d)}


def _rx(seq, d="lf2st", leg=None, text=None):
    leg = leg or ("primary" if d == "lf2st" else "secondary")
    return {"ev": "bridge_rx", "leg": leg,
            "text": text or c21.format_msg("r1", seq, d)}


def _drop(seq, reason, d="lf2st", text=None):
    leg = "primary" if d == "lf2st" else "secondary"
    return {"ev": "bridge_drop", "leg": leg, "reason": reason,
            "text": text or c21.format_msg("r1", seq, d)}


def _tx(seq, d="lf2st"):
    dest, tag = (("secondary", "LONG_FAST") if d == "lf2st"
                 else ("primary", "SHORT_TURBO"))
    return {"ev": "bridge_tx", "dest": dest,
            "text": f"[Mesh:{tag}] " + c21.format_msg("r1", seq, d)}


def _heard(seq, d="lf2st"):
    radio, tag = (("src_st", "LONG_FAST") if d == "lf2st"
                  else ("src_lf", "SHORT_TURBO"))
    return {"ev": "heard", "radio": radio,
            "text": f"[Mesh:{tag}] " + c21.format_msg("r1", seq, d)}


def _delivered(seq, d="lf2st"):
    return [_send(seq, d), _rx(seq, d), _tx(seq, d), _heard(seq, d)]


def _controls():
    rep = c21.format_msg("r1", 900, "lf2st")
    loop = c21.loop_control_msg("r1", 901, "st2lf")
    return [
        _send(900, kind="repeat", text=rep), _send(900, kind="repeat", text=rep),
        _rx(900, text=rep), _rx(900, text=rep), _tx(900), _heard(900),
        _drop(900, "duplicate", text=rep),
        _send(901, "st2lf", kind="loop", text=loop), _rx(901, "st2lf", text=loop),
        _drop(901, "already_bridged", "st2lf", text=loop),
    ]


def _clean_run(n=40):
    ev = []
    for i in range(1, n + 1):
        ev += _delivered(i, "lf2st") + _delivered(i, "st2lf")
    return ev + _controls()


def test_every_message_delivered_with_working_controls_passes():
    r = c21.reconcile(_clean_run(), run="r1")
    assert r["verdict"] == "PASS"
    assert r["labels"][("r1", 3, "lf2st")] == "delivered"
    assert r["labels"][("r1", 900, "lf2st")] == "control_ok"
    assert r["labels"][("r1", 901, "st2lf")] == "control_ok"


def test_bridge_heard_it_and_did_nothing_fails():
    ev = _clean_run() + [_send(41), _rx(41)]
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 41, "lf2st")] == "unaccounted"
    assert r["verdict"] == "FAIL"


def test_far_radio_hearing_it_twice_is_a_duplicate():
    ev = _clean_run() + _delivered(41) + [_heard(41)]
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 41, "lf2st")] == "duplicate"
    assert r["verdict"] == "FAIL"


def test_one_lost_send_is_rf_ingress_loss_and_still_passes():
    ev = _clean_run() + [_send(41, "st2lf")]
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 41, "st2lf")] == "rf_ingress_loss"
    assert r["verdict"] == "PASS"


def test_tx_never_heard_is_rf_egress_loss():
    ev = _clean_run() + [_send(41), _rx(41), _tx(41)]
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 41, "lf2st")] == "rf_egress_loss"


def test_rf_loss_above_five_percent_on_one_leg_is_inconclusive_never_pass():
    ev = _clean_run(n=40)
    ev += [_send(i, "st2lf") for i in range(41, 44)]   # 3 of 43 lost = 7.0 %
    r = c21.reconcile(ev, run="r1")
    assert r["verdict"] == "INCONCLUSIVE"
    assert r["rf_loss"]["st2lf"]["ingress"] == (3, 43)


def test_a_normal_message_dropped_by_the_bridge_is_named_and_fails():
    ev = _clean_run() + [_send(41), _rx(41), _drop(41, "duplicate")]
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 41, "lf2st")] == "dropped:duplicate"
    assert r["verdict"] == "FAIL"


def test_loop_control_forwarded_instead_of_dropped_fails():
    ev = [e for e in _clean_run() if not (e["ev"] == "bridge_drop"
                                          and e["reason"] == "already_bridged")]
    ev.append({"ev": "bridge_tx", "dest": "primary",
               "text": "[Mesh:SHORT_TURBO] " + c21.loop_control_msg("r1", 901, "st2lf")})
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 901, "st2lf")] == "control_failed"
    assert r["verdict"] == "FAIL"


def test_a_control_never_exercised_is_inconclusive():
    ev = [e for e in _clean_run() if "901" not in e["text"]
          or e["ev"] == "send"]          # loop control sent, never reached the bridge
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 901, "st2lf")] == "rf_ingress_loss"
    assert r["verdict"] == "INCONCLUSIVE"


def test_a_message_heard_on_the_wrong_leg_is_an_echo_and_fails():
    ev = _clean_run() + [_rx(5, "lf2st", leg="secondary")]
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 5, "lf2st")] == "echo"
    assert r["verdict"] == "FAIL"


def test_other_runs_and_foreign_text_are_ignored():
    ev = _clean_run() + [{"ev": "heard", "radio": "src_st", "text": "hi"},
                         {"ev": "send", "kind": "normal",
                          "text": c21.format_msg("r0", 1, "lf2st")}]
    r = c21.reconcile(ev, run="r1")
    assert r["verdict"] == "PASS"
    assert not any(k[0] == "r0" for k in r["labels"])


# --- the consumer path: REAL bridge log lines -> adapter -> reconcile -------

def test_adapter_reads_each_line_shape():
    j = "Oct 20 08:00:01 lab python3[123]: "
    assert c21.bridge_event_from_log(j + "mesh_bridge rx leg=primary: c21 r1 1 lf2st") == {
        "ev": "bridge_rx", "leg": "primary", "text": "c21 r1 1 lf2st"}
    assert c21.bridge_event_from_log(
        "mesh_bridge drop leg=secondary reason=already_bridged: [Mesh:LAB] c21 r1 2 st2lf"
    ) == {"ev": "bridge_drop", "leg": "secondary", "reason": "already_bridged",
          "text": "[Mesh:LAB] c21 r1 2 st2lf"}
    assert c21.bridge_event_from_log(
        "mesh_bridge tx dest=secondary: [Mesh:LONG_FAST] c21 r1 1 lf2st") == {
        "ev": "bridge_tx", "dest": "secondary", "text": "[Mesh:LONG_FAST] c21 r1 1 lf2st"}
    assert c21.bridge_event_from_log("Bridged LONG_FAST -> secondary: x...") is None


def _drive_real_bridge(tmp_path, monkeypatch, caplog, break_dedup=False):
    import logging
    from unittest.mock import MagicMock, patch
    import gateway.mesh_bridge as mb
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)
    path = tmp_path / "gateway.json"
    c21.write_bridge_config(path, leg_lf=LF, leg_st=ST)
    bridge = mb.MeshtasticPresetBridge(config=c21.load_bridge_config(path))
    radios = {"primary": MagicMock(), "secondary": MagicMock()}
    bridge._primary_interface, bridge._primary_connected = radios["primary"], True
    bridge._secondary_interface, bridge._secondary_connected = radios["secondary"], True
    if break_dedup:
        monkeypatch.setattr(bridge, "_is_duplicate", lambda msg: False)
    caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")

    sends = []

    def inject(text, d, kind="normal", sender=None):
        sends.append({"ev": "send", "kind": kind, "text": text})
        leg, dest = ("primary", "secondary") if d == "lf2st" else ("secondary", "primary")
        q = bridge._primary_to_secondary if d == "lf2st" else bridge._secondary_to_primary
        pkt = {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": text.encode()},
               "fromId": sender or ("!0000aaaa" if d == "lf2st" else "!0000bbbb"),
               "toId": "^all", "to": 0xFFFFFFFF, "channel": 0}
        bridge._process_receive(pkt, leg, dest, q)

    for i in range(1, 4):
        inject(c21.format_msg("r1", i, "lf2st"), "lf2st")
        inject(c21.format_msg("r1", i, "st2lf"), "st2lf")
    rep = c21.format_msg("r1", 900, "lf2st")
    inject(rep, "lf2st", "repeat")
    inject(rep, "lf2st", "repeat")
    inject(c21.loop_control_msg("r1", 901, "st2lf"), "st2lf", "loop")
    with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
        bridge._primary_to_secondary.process_once()
        bridge._secondary_to_primary.process_once()

    # what each bridge leg was told to transmit = what the far radio hears
    heard = [{"ev": "heard", "radio": "src_st" if leg == "secondary" else "src_lf",
              "text": call.args[0]}
             for leg, radio in radios.items() for call in radio.sendText.call_args_list]
    bridge_ev = [e for e in (c21.bridge_event_from_log(r.getMessage())
                             for r in caplog.records) if e]
    return c21.reconcile(sends + bridge_ev + heard, run="r1")


def test_real_bridge_lines_reconcile_to_pass(tmp_path, monkeypatch, caplog):
    r = _drive_real_bridge(tmp_path, monkeypatch, caplog)
    assert r["verdict"] == "PASS", r["labels"]
    assert r["counts"] == {"delivered": 6, "control_ok": 2}


def test_real_bridge_with_dedup_broken_fails_the_repeat_control(
        tmp_path, monkeypatch, caplog):
    r = _drive_real_bridge(tmp_path, monkeypatch, caplog, break_dedup=True)
    assert r["labels"][("r1", 900, "lf2st")] == "control_failed"
    assert r["verdict"] == "FAIL"


# --- seam 2, protobuf side: the plan written into, and read out of, the real
# meshtastic messages (a wrong field name fails here, not on Monday) ---------

def _pbs():
    from meshtastic.protobuf import channel_pb2, localonly_pb2
    return localonly_pb2.LocalConfig(), localonly_pb2.LocalModuleConfig(), channel_pb2.Channel()


def test_plan_round_trips_through_the_real_protobufs():
    plan = c21.radio_plan("leg_st", psk=b"\x07" * 32)
    lc, mc, ch = _pbs()
    c21.apply_plan(plan, lc, mc, ch)
    assert c21.readback_mismatches(plan, c21.flatten_readback(lc, mc, ch)) == []
    assert ch.role == ch.Role.PRIMARY


def test_a_factory_fresh_radio_reads_back_as_mismatched():
    plan = c21.radio_plan("leg_lf", psk=b"\x07" * 32)
    keys = {m[0] for m in c21.readback_mismatches(plan, c21.flatten_readback(*_pbs()))}
    assert {"lora.tx_power", "lora.channel_num", "lora.region",
            "channel0.name", "channel0.psk"} <= keys


def test_a_frequency_override_on_the_radio_is_refused():
    # override_frequency makes the radio ignore channel_num entirely
    plan = c21.radio_plan("src_lf")
    lc, mc, ch = _pbs()
    c21.apply_plan(plan, lc, mc, ch)
    lc.lora.override_frequency = 906.875
    keys = [m[0] for m in c21.readback_mismatches(plan, c21.flatten_readback(lc, mc, ch))]
    assert keys == ["lora.override_frequency"]


def test_reconcile_cli_reads_the_files_the_hosts_write(tmp_path, monkeypatch, capsys):
    import json
    import logging
    monkeypatch.setattr(c21, "get_real_user_home", lambda: tmp_path)
    d = c21._run_dir("r1")
    d.mkdir(parents=True)
    # bridge.log through the same formatter bridge-host installs
    log = logging.getLogger("c21-test-bridge")
    log.propagate = False
    fh = logging.FileHandler(d / "bridge.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(fh)
    log.setLevel(logging.DEBUG)
    rows = []
    for e in _clean_run(n=30):            # 1 loss in 31 stays under 5 %
        if e["ev"] == "bridge_rx":
            log.debug("mesh_bridge rx leg=%s: %s", e["leg"], e["text"])
        elif e["ev"] == "bridge_drop":
            log.debug("mesh_bridge drop leg=%s reason=%s: %s", e["leg"], e["reason"], e["text"])
        elif e["ev"] == "bridge_tx":
            log.debug("mesh_bridge tx dest=%s: %s", e["dest"], e["text"])
        else:
            rows.append(e)
    fh.close()
    (d / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert c21.main(["reconcile", "--run", "r1"]) == 3       # unfinished: no verdict
    assert "PROVISIONAL" in capsys.readouterr().out
    with open(d / "events.jsonl", "a") as f:
        f.write(json.dumps(dict(_send(31), wall=1.0)) + "\n")      # one RF ingress loss
        f.write(json.dumps({"ev": "traffic_end", "seq": 6, "completed": True}) + "\n")
    assert c21.main(["reconcile", "--run", "r1"]) == 0
    out = capsys.readouterr().out
    assert "C21 run r1: PASS" in out
    assert "31 lf2st rf_ingress_loss" in out
    assert json.loads((d / "reconcile.json").read_text())["labels"]["31 lf2st"] == "rf_ingress_loss"


# --- non-author review 2026-10-19: findings #2 #4 #5 #6 ---------------------

def _end(completed=True):
    return {"ev": "traffic_end", "completed": completed}


def test_a_source_radio_write_error_is_not_injected_not_a_bridge_failure():
    ev = _clean_run() + [{"ev": "send_error", "kind": "normal",
                          "text": c21.format_msg("r1", 41, "lf2st"), "error": "EIO"}]
    r = c21.reconcile(ev, run="r1")
    assert ("r1", 41, "lf2st") not in r["labels"]
    assert r["verdict"] == "PASS"
    assert r["not_injected"] == 1


def test_traffic_stopped_before_its_deadline_is_inconclusive():
    r = c21.reconcile(_clean_run() + [_end(completed=False)], run="r1")
    assert r["verdict"] == "INCONCLUSIVE"
    assert any("before its deadline" in n for n in r["notes"])


def test_a_source_radio_link_loss_is_inconclusive_and_named():
    ev = _clean_run() + [{"ev": "link_lost", "radio": "src_st"}, _end()]
    r = c21.reconcile(ev, run="r1")
    assert r["verdict"] == "INCONCLUSIVE"
    assert any("src_st" in n for n in r["notes"])


def test_a_bridge_leg_link_loss_is_named_not_left_to_read_as_rf_loss():
    line = "2026-10-20 09:00:00,000 primary serial link LOST (lab_lf) — radio gone; reconnecting"
    e = c21.bridge_event_from_log(line)
    assert e["ev"] == "bridge_link_lost" and e["leg"] == "primary"
    r = c21.reconcile(_clean_run() + [e], run="r1")
    assert r["verdict"] == "INCONCLUSIVE"
    assert any("primary" in n and "link" in n for n in r["notes"])


def test_an_rf_loss_names_the_transmissions_that_overlapped_it():
    lost = dict(_send(41), wall=1000.0)
    near = dict(_send(41, "st2lf"), wall=1001.5)       # 1.5 s later: overlaps
    far = dict(_send(42, "st2lf"), wall=1010.0)        # 10 s later: does not
    ev = _clean_run() + [lost, near, far]
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 41, "lf2st")] == "rf_ingress_loss"
    assert r["coincident"][("r1", 41, "lf2st")] == [near["text"]]


def test_bridge_log_lines_carry_their_wall_time():
    e = c21.bridge_event_from_log(
        "2026-10-20 09:00:00,250 mesh_bridge tx dest=secondary: [Mesh:LONG_FAST] c21 r1 1 lf2st")
    import datetime
    assert e["wall"] == datetime.datetime(2026, 10, 20, 9, 0, 0, 250000).timestamp()


def test_sent_and_heard_but_bookkept_as_dead_letter_is_named_truthfully():
    # second review pass #3: mark_delivered DB error -> cleanup_stale reaps the
    # row -> dead_letter line, though the far radio HEARD it. Still a finding
    # (bookkeeping), never a bridge loss.
    ev = _clean_run() + _delivered(41) + [_drop(41, "dead_letter")]
    r = c21.reconcile(ev, run="r1")
    assert r["labels"][("r1", 41, "lf2st")] == "delivered_unrecorded"
    assert r["verdict"] == "FAIL"
