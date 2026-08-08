"""claw_uplink_node_moved — the claw's UPLINK is a watched entity (2026-07-29).

Born from a live incident: dudeclaw-01 went dark for ~5 h and every signal the
fleet had pointed at the DEVICE (``claw_device_dark``) — which was healthy the
whole time. It booted, associated to its AP, held its DHCP lease, drove its
display, and was dialling exactly the right broker. What it could not do was
reach that broker, because the AREDN node bridging its /28 to the fleet segment
was not where the fleet believed it to be. The investigation cost a power
cycle, an antenna reseat, two chip resets and three dead hypotheses, all aimed
at hardware that was never at fault.

The claws are the fleet's only out-of-band witnesses. A witness whose uplink
can silently relocate — with the outage rendered as "the device is silent" —
is a witness that reports its own blindness as someone else's failure.

THE TRAP THIS PINS (found by testing, not by reading): ``/proc/net/arp`` lists
INCOMPLETE entries — a *failed* resolution attempt — with the target MAC and
``Flags 0x0``. Probing a stale address therefore MANUFACTURES an arp row that
looks like a second location for the node. A probe that read the MAC column
without checking ATF_COM (0x2) would invent drift out of its own probing, and
would do so most reliably at exactly the moment an operator went looking. Only
complete entries are locations.
"""

import json

import pytest

from utils.watchdog_probe_core import (
    SIGNAL_CLASSES,
    collect_dispositions,
    reset_dispositions,
)

MAC = "02:00:5e:00:11:22"   # synthetic (locally-administered); never the operator's real AP

ARP_HEADER = ("IP address       HW type     Flags       "
              "HW address            Mask     Device\n")


def _arp(rows):
    """Render a /proc/net/arp body. rows = [(ip, flags, mac)]."""
    out = ARP_HEADER
    for ip, flags, mac in rows:
        out += f"{ip:<17}0x1         {flags:<12}{mac:<22}*        eth0\n"
    return out


@pytest.fixture(autouse=True)
def _clean_dispositions():
    reset_dispositions()
    yield
    reset_dispositions()


class TestClawUplinkNodeMoved:

    @pytest.fixture(autouse=True)
    def _paths(self, tmp_path):
        self.sp = str(tmp_path / "uplink.json")
        self.cfg = tmp_path / "claw_uplink_nodes.json"
        self.arp = tmp_path / "arp"

    def _write_cfg(self, nodes=None):
        nodes = nodes if nodes is not None else [{
            "name": "VOLCANO-HI-HAP",
            "mac": MAC,
            "expected_ip": "192.0.2.250",
            "serves": ["dudeclaw-01", "bot32"],
        }]
        self.cfg.write_text(json.dumps(nodes))
        return str(self.cfg)

    def _run(self, arp_text=None, cfg_path=None, now=1.0, pinhole_path=None):
        from utils.watchdog_probes import probe_claw_uplink_node_moved
        if arp_text is not None:
            self.arp.write_text(arp_text)
        return probe_claw_uplink_node_moved(
            config_path=cfg_path if cfg_path is not None else self._write_cfg(),
            arp_path=str(self.arp),
            state_path=self.sp,
            now=now,
            # PIN THE PINHOLE (2026-07-29 review). Unpinned, this defaulted to
            # DEFAULT_PINHOLE_PATH = the REAL /etc/nftables.conf of whatever box
            # runs the suite, so these tests' verdicts depended on ambient
            # machine state — green in CI (no such file → the pinhole leg
            # abstains) and RED on moc2, the one box whose 4222 allowlist is the
            # reason the pinhole leg exists, because 192.0.2.250 is never in a
            # real allowlist. feedback_tests_must_pin_ambient_state: ask what you
            # would change about this BOX to make the test fail. Default is a
            # nonexistent path so _read_pinhole_allowlist returns None ("does not
            # gate that port"), which is the state these cases mean to assert.
            pinhole_path=(pinhole_path if pinhole_path is not None
                          else str(self.arp.parent / "no-such-nftables.conf")),
        )

    # ---- inert / indeterminate: never dressed as clean ---------------------

    def test_no_config_is_inert_not_clean(self):
        """A box with no claw uplink declared has nothing to say."""
        sig = self._run(arp_text=_arp([]), cfg_path=str(self.cfg.parent / "absent.json"))
        assert sig is None
        assert collect_dispositions()["claw_uplink_node_moved"]["disp"] == "inert"

    def test_unresolvable_operator_home_is_indeterminate_not_inert(self, monkeypatch):
        """2026-08-08: this branch used to say inert, reason 'no uplink
        declaration here' — a claim about the BOX made from a failure to
        LOOK. With no home we never reach the declaration, so we cannot
        know whether one exists (honest_failure_modes #2)."""
        import utils.watchdog_probes_claw_uplink as mod
        from utils.watchdog_probes import probe_claw_uplink_node_moved
        monkeypatch.setattr(mod, "_operator_home", lambda: None)
        self.arp.write_text(_arp([]))
        sig = probe_claw_uplink_node_moved(
            arp_path=str(self.arp), state_path=self.sp, now=1.0,
            pinhole_path=str(self.arp.parent / "no-such-nftables.conf"))
        assert sig is None
        cell = collect_dispositions()["claw_uplink_node_moved"]
        assert cell["disp"] == "indeterminate"
        assert "cannot read" in cell["reason"]

    def test_unreadable_arp_is_indeterminate_not_clean(self):
        """Losing the observation channel is blindness, not health."""
        sig = self._run(cfg_path=self._write_cfg())  # arp file never written
        assert sig is None
        assert (collect_dispositions()["claw_uplink_node_moved"]["disp"]
                == "indeterminate")

    def test_mac_absent_from_arp_is_indeterminate_not_moved(self):
        """We simply have not talked to it. Absence of evidence is not
        evidence that it relocated — that would page on a quiet neighbour."""
        sig = self._run(arp_text=_arp([("192.0.2.1", "0x2", "aa:bb:cc:dd:ee:ff")]))
        assert sig is None
        assert (collect_dispositions()["claw_uplink_node_moved"]["disp"]
                == "indeterminate")

    # ---- clean -------------------------------------------------------------

    def test_node_at_declared_address_is_clean(self):
        for _ in range(3):
            sig = self._run(arp_text=_arp([("192.0.2.250", "0x2", MAC)]))
            assert sig is None
        assert collect_dispositions()["claw_uplink_node_moved"]["disp"] == "clean"

    def test_declared_match_is_case_insensitive(self):
        """arp renders lowercase; an operator may declare uppercase."""
        self.cfg.write_text(json.dumps([{
            "name": "VOLCANO-HI-HAP", "mac": MAC.upper(),
            "expected_ip": "192.0.2.250", "serves": ["dudeclaw-01"],
        }]))
        for _ in range(3):
            assert self._run(arp_text=_arp([("192.0.2.250", "0x2", MAC)]),
                             cfg_path=str(self.cfg)) is None
        assert collect_dispositions()["claw_uplink_node_moved"]["disp"] == "clean"

    # ---- THE trap ----------------------------------------------------------

    def test_incomplete_arp_entry_is_not_a_location(self):
        """Flags 0x0 = INCOMPLETE: a resolution that FAILED. Probing a stale
        address creates exactly this row, so treating it as a sighting lets the
        probe manufacture drift from its own footprints. Node is at .250; the
        .24 row is the ghost of a failed ping and must be ignored."""
        for _ in range(3):
            sig = self._run(arp_text=_arp([
                ("192.0.2.250", "0x2", MAC),   # real
                ("192.0.2.24", "0x0", MAC),    # ghost of a failed probe
            ]))
            assert sig is None, "an INCOMPLETE arp row was read as a location"
        assert collect_dispositions()["claw_uplink_node_moved"]["disp"] == "clean"

    def test_only_incomplete_entries_is_indeterminate(self):
        """If the ONLY rows for the MAC are failed resolutions, we have not
        observed the node anywhere — that is blindness, not relocation."""
        sig = self._run(arp_text=_arp([("192.0.2.24", "0x0", MAC)]))
        assert sig is None
        assert (collect_dispositions()["claw_uplink_node_moved"]["disp"]
                == "indeterminate")

    # ---- the fire ----------------------------------------------------------

    def test_moved_node_fires_after_debounce(self):
        arp = _arp([("192.0.2.24", "0x2", MAC)])   # complete, but not declared
        assert self._run(arp_text=arp, now=1.0) is None, "must debounce one tick"
        sig = self._run(arp_text=arp, now=2.0)
        assert sig is not None
        assert sig.cls == "claw_uplink_node_moved"
        assert sig.subject == "VOLCANO-HI-HAP"
        assert sig.severity == "degraded"
        # It must name the UPLINK and the claws it strands — the whole point is
        # that the operator stops looking at the claw.
        assert "192.0.2.24" in sig.detail
        assert "192.0.2.250" in sig.detail
        assert "dudeclaw-01" in sig.detail
        assert sig.extra["observed_ips"] == ["192.0.2.24"]
        assert sig.extra["declared_ip"] == "192.0.2.250"

    def test_recovery_resets_streak(self):
        arp_bad = _arp([("192.0.2.24", "0x2", MAC)])
        arp_ok = _arp([("192.0.2.250", "0x2", MAC)])
        self._run(arp_text=arp_bad, now=1.0)
        self._run(arp_text=arp_ok, now=2.0)          # back home -> streak clears
        assert self._run(arp_text=arp_bad, now=3.0) is None, \
            "streak must restart after an observed-clean tick"

    def test_multi_homed_counts_declared_address_as_home(self):
        """Seen at BOTH the declared address and another: it is reachable where
        we expect, so it has not moved. Under-fire rather than false-page."""
        for _ in range(3):
            assert self._run(arp_text=_arp([
                ("192.0.2.250", "0x2", MAC),
                ("192.0.2.24", "0x2", MAC),
            ])) is None

    # ---- never raises into the tick ---------------------------------------

    def test_malformed_config_is_indeterminate_never_raises(self):
        self.cfg.write_text("{not json")
        sig = self._run(arp_text=_arp([("192.0.2.250", "0x2", MAC)]),
                        cfg_path=str(self.cfg))
        assert sig is None
        assert (collect_dispositions()["claw_uplink_node_moved"]["disp"]
                == "indeterminate")

    def test_config_entry_missing_mac_is_skipped_not_fatal(self):
        self.cfg.write_text(json.dumps([
            {"name": "broken", "expected_ip": "192.0.2.9"},
            {"name": "VOLCANO-HI-HAP", "mac": MAC,
             "expected_ip": "192.0.2.250", "serves": ["dudeclaw-01"]},
        ]))
        for _ in range(3):
            assert self._run(arp_text=_arp([("192.0.2.250", "0x2", MAC)]),
                             cfg_path=str(self.cfg)) is None
        assert collect_dispositions()["claw_uplink_node_moved"]["disp"] == "clean"


class TestClawUplinkNodeWiring:

    def test_class_is_registered(self):
        assert "claw_uplink_node_moved" in SIGNAL_CLASSES

    def test_probe_is_exported(self):
        from utils.watchdog_probes import probe_claw_uplink_node_moved
        assert callable(probe_claw_uplink_node_moved)

    def test_runner_invokes_it(self):
        """§3b wiring: a probe that exists but nothing calls is a detector
        that is confidently absent."""
        import inspect
        from utils import watchdog_runner
        src = inspect.getsource(watchdog_runner)
        assert "probe_claw_uplink_node_moved()" in src


class TestUplinkAdmittedByPinhole:
    """The SECOND leg, added same day after the first shipped flawed.

    The 2026-07-29 outage was NOT "the node moved" in the abstract — it was
    "the node's current address is no longer the one moc2's firewall admits".
    moc2 default-denies NATS 4222 and allows a hardcoded allowlist, because
    WireClaw cannot send NATS credentials:

        ip saddr { <brain-lan-ip>, <old-uplink-ip> } tcp dport 4222 accept
        tcp dport 4222 drop

    The uplink's DHCP lease moved .24 -> .250 and the pinhole kept .24, so
    every SYN from a perfectly healthy claw was dropped for 6.5 h.

    The first version of this probe compared the observed address against an
    ``expected_ip`` hand-written into claw_uplink_nodes.json — a THIRD
    independent copy of the same constant, i.e. it reproduced the very drift
    class it exists to catch (honest_failure_modes #5). This leg reads the
    PINHOLE as the authority, so the firewall and the detector cannot
    disagree: the question is not "is it where I declared" but "is it where
    the firewall will actually let it in".
    """

    @pytest.fixture(autouse=True)
    def _paths(self, tmp_path):
        self.sp = str(tmp_path / "uplink.json")
        self.cfg = tmp_path / "claw_uplink_nodes.json"
        self.arp = tmp_path / "arp"
        self.nft = tmp_path / "nftables.conf"

    def _cfg(self, expected_ip="192.0.2.250"):
        node = {"name": "VOLCANO-HI-HAP", "mac": MAC, "serves": ["dudeclaw-01"]}
        if expected_ip:
            node["expected_ip"] = expected_ip
        self.cfg.write_text(json.dumps([node]))
        return str(self.cfg)

    def _nft(self, allow, port=4222):
        """Real moc2 shape, verified against the live file."""
        body = "flush ruleset\ntable inet filter {\n\tchain input {\n"
        body += '\t\tiifname "lo" tcp dport %d accept\n' % port
        if allow:
            body += "\t\tip saddr { %s } tcp dport %d accept\n" % (
                ", ".join(allow), port)
        body += "\t\ttcp dport %d drop\n\t}\n}\n" % port
        self.nft.write_text(body)
        return str(self.nft)

    def _run(self, arp_rows, allow, expected_ip="192.0.2.250", now=1.0):
        from utils.watchdog_probes import probe_claw_uplink_node_moved
        self.arp.write_text(_arp(arp_rows))
        return probe_claw_uplink_node_moved(
            config_path=self._cfg(expected_ip),
            arp_path=str(self.arp),
            pinhole_path=self._nft(allow),
            state_path=self.sp,
            now=now,
        )

    def test_admitted_address_is_clean(self):
        for _ in range(3):
            assert self._run([("192.0.2.250", "0x2", MAC)],
                             ["192.0.2.32", "192.0.2.250"]) is None
        assert collect_dispositions()["claw_uplink_node_moved"]["disp"] == "clean"

    def test_todays_incident_fires_even_when_declaration_agrees(self):
        """THE case. The node is exactly where claw_uplink_nodes.json says
        (.250) — so the declaration leg is happy — but the firewall still
        admits only the OLD .24. A declaration-only probe reports clean while
        the witness is cut off. This must fire."""
        rows = [("192.0.2.250", "0x2", MAC)]
        allow = ["192.0.2.32", "192.0.2.24"]          # pinhole kept the old IP
        assert self._run(rows, allow, expected_ip="192.0.2.250", now=1.0) is None
        sig = self._run(rows, allow, expected_ip="192.0.2.250", now=2.0)
        assert sig is not None, "declaration agreed, so only the pinhole leg can catch this"
        assert sig.cls == "claw_uplink_node_moved"
        assert "192.0.2.250" in sig.detail
        assert "4222" in sig.detail
        assert sig.extra["pinhole_allows"] == ["192.0.2.24", "192.0.2.32"]
        assert sig.extra["admitted"] is False

    def test_unreadable_pinhole_falls_back_not_fires(self):
        """No pinhole on this box (most boxes) must not manufacture a fire —
        the declaration leg judges alone and the extra says the pinhole was
        unobservable, rather than folding blindness into agreement."""
        from utils.watchdog_probes import probe_claw_uplink_node_moved
        self.arp.write_text(_arp([("192.0.2.250", "0x2", MAC)]))
        for _ in range(3):
            sig = probe_claw_uplink_node_moved(
                config_path=self._cfg("192.0.2.250"),
                arp_path=str(self.arp),
                pinhole_path=str(self.nft.parent / "absent.conf"),
                state_path=self.sp, now=1.0)
            assert sig is None
        assert collect_dispositions()["claw_uplink_node_moved"]["disp"] == "clean"

    def test_pinhole_with_no_allowlist_line_is_not_an_empty_allowlist(self):
        """A ruleset that never mentions the port is NOT 'admits nobody' —
        it means this box does not gate that port, so the leg abstains."""
        for _ in range(3):
            assert self._run([("192.0.2.250", "0x2", MAC)], [],
                             expected_ip="192.0.2.250") is None
        assert collect_dispositions()["claw_uplink_node_moved"]["disp"] == "clean"
