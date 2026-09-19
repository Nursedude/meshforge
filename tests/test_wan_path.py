"""utils.wan_path — the fleet's eyes on the internet (2026-09-06).

The verdict is a pure function of rung results, and the distinctions it
draws are the ones that cost a night: "my LAN" vs "my ISP's access hop" vs
"the internet beyond my ISP". Every unmeasured rung must stay visible as
unmeasured — a ladder with a missing rung cannot render a clean verdict.
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils import wan_path as wp  # noqa: E402

R = wp.RungResult


def _ladder(lan=0.0, edge=0.0, near=0.0, far=(0.0, 0.0), far_avg=(120.0, 200.0)):
    out = [R("lan", "gateway", "192.0.2.1", 20, 20, lan, 0.5, 0.1),
           R("edge", "isp-hop", "192.0.2.2", 20, 20, edge, 3.0, 0.3),
           R("near", "cloudflare-dns", "1.1.1.1", 20, 20, near, 65.0, 0.3)]
    for i, (l, a) in enumerate(zip(far, far_avg)):
        out.append(R("far", "far%d" % i, "far%d.example" % i, 20, int(20 - 20 * l / 100), l, a, 1.0))
    return out


class TestClassify:
    def test_clean_path_is_ok(self):
        v = wp.classify(_ladder())
        assert v.status == "ok" and v.cause == "clean"

    def test_the_night_this_was_written_reads_as_transit(self):
        # clean to the router and the ISP-near anycast, lossy beyond
        v = wp.classify(_ladder(far=(20.0, 25.0)))
        assert v.status == "fail" and v.cause == "transit", v
        assert "20%" in v.message and "edge 0%" in v.message

    def test_first_hop_loss_is_the_lan_not_the_internet(self):
        v = wp.classify(_ladder(lan=10.0, far=(30.0, 30.0)))
        assert v.cause == "lan" and v.status == "fail"

    def test_isp_access_hop_loss_names_the_edge(self):
        v = wp.classify(_ladder(edge=15.0, far=(15.0, 20.0)))
        assert v.cause == "edge"

    def test_light_far_loss_is_concern_not_fail(self):
        # NOTE: the loss here must be a value the sampler can actually produce
        # (a multiple of 100/PING_COUNT). This test used to pass 2.0%, which
        # 20 packets can never yield, so it proved the branch worked while
        # production could not reach it -- 1008 real samples, 0 concerns.
        one_packet = 100.0 / wp.PING_COUNT
        v = wp.classify(_ladder(far=(one_packet, one_packet)))
        assert v.status == "concern" and v.cause == "transit"
    def test_far_loss_with_lossy_near_cannot_blame_transit_alone(self):
        v = wp.classify(_ladder(near=20.0, far=(20.0, 20.0)))
        assert v.status == "fail" and v.cause == "edge-or-transit"

    def test_no_far_measurement_is_unknown_never_clean(self):
        ladder = _ladder()[:3] + [R("far", "github", "github.com", error="unmeasured: dns")]
        v = wp.classify(ladder)
        assert v.status == "unknown" and "github" in v.message

    def test_an_unmeasured_rung_keeps_a_clean_run_from_reading_ok(self):
        ladder = _ladder() + [R("far", "cloud-vps", "vps.example", error="unmeasured")]
        v = wp.classify(ladder)
        assert v.status == "concern" and "unmeasured: cloud-vps" in v.message


class TestSamplingFloor:
    """The alarm bar must sit ABOVE the smallest loss the sampler can see.

    When LOSS_FAIL_PCT was 5.0 and PING_COUNT 20, one dropped packet was
    exactly a FAIL and the concern rung was unreachable by construction.
    These pin the gap so a future PING_COUNT change cannot silently re-close it.
    """

    def test_one_dropped_packet_is_not_a_failure(self):
        one_packet = 100.0 / wp.PING_COUNT
        v = wp.classify(_ladder(far=(one_packet, 0.0)))
        assert v.status == "concern", (
            "a single dropped packet of %d must not read as FAIL" % wp.PING_COUNT)

    def test_two_dropped_packets_on_every_far_target_are_a_failure(self):
        # 2026-09-18: this used to pass far=(10.0, 0.0) -- one target losing,
        # one clean -- and assert FAIL/transit, so the test PINNED the false
        # fire it should have caught. The sampling-floor subject it exists for
        # (the fail rung is reachable at 20 packets) is kept; the ladder is
        # made transit-shaped, because agreement is now what names transit.
        two_packets = 200.0 / wp.PING_COUNT
        v = wp.classify(_ladder(far=(two_packets, two_packets)))
        assert v.status == "fail" and v.cause == "transit"

    def test_the_concern_rung_is_reachable_at_this_sample_size(self):
        floor = 100.0 / wp.PING_COUNT
        assert floor <= wp.LOSS_CONCERN_PCT < wp.LOSS_FAIL_PCT, (
            "concern is unreachable: the smallest observable loss (%.2f%%) must "
            "be <= LOSS_CONCERN_PCT (%.2f%%) and strictly below LOSS_FAIL_PCT "
            "(%.2f%%)" % (floor, wp.LOSS_CONCERN_PCT, wp.LOSS_FAIL_PCT))

    def test_every_reachable_loss_below_the_bar_reads_concern(self):
        step = 100.0 / wp.PING_COUNT
        seen = set()
        k = 1
        while step * k < wp.LOSS_FAIL_PCT:
            seen.add(wp.classify(_ladder(far=(step * k, 0.0))).status)
            k += 1
        assert seen == {"concern"}, seen
        assert k > 1, "no reachable loss value sits below LOSS_FAIL_PCT"


class TestFarAgreement:
    """A path verdict needs the far targets to AGREE.

    2026-09-18. ``far`` is the MAX over independent distant endpoints, so one
    of them dropping two packets was rendering as "loss beyond the ISP" -- a
    claim about the path built from a single host. Measured over the 80 h after
    the 09-15 threshold cure: 6 FAIL verdicts, 4 of them one far target losing
    beside two reading 0.0%. The threshold was not the whole defect.
    """

    def test_one_lossy_endpoint_beside_clean_siblings_is_not_transit(self):
        v = wp.classify(_ladder(far=(30.0, 0.0)))
        assert v.status == "concern", v
        assert v.cause == "endpoint", "one lossy host must not be called transit"
        assert "far0" in v.message and "not this box's transit" in v.message

    def test_a_dead_endpoint_beside_a_clean_sibling_is_not_a_path_failure(self):
        # A host that is 100% gone while the rest of the path is clean is that
        # host's outage. Still reported -- named, and at concern.
        v = wp.classify(_ladder(far=(100.0, 0.0)))
        assert v.status == "concern" and v.cause == "endpoint", v

    def test_every_far_target_losing_past_the_bar_is_transit(self):
        v = wp.classify(_ladder(far=(10.0, 15.0)))
        assert v.status == "fail" and v.cause == "transit", v
        assert "all 2 far target(s) losing" in v.message

    def test_light_loss_on_every_far_target_stays_transit(self):
        # The founding incident opened at 5% to EVERY distant host before it
        # reached 25%. Agreement below the bar must keep naming transit, or the
        # early shape of the thing this module was built for reads as one host.
        one_packet = 100.0 / wp.PING_COUNT
        v = wp.classify(_ladder(far=(one_packet, one_packet)))
        assert v.status == "concern" and v.cause == "transit", v

    def test_the_only_measured_far_target_can_still_agree(self):
        ladder = _ladder()[:3] + [
            R("far", "pypi", "pypi.org", 20, 17, 15.0, 59.0, 1.0),
            R("far", "github", "github.com", error="unmeasured: dns")]
        v = wp.classify(ladder)
        assert v.status == "fail" and v.cause == "transit", v
        assert "github" in v.message, "the unmeasured target must stay visible"

    def test_endpoint_is_a_traceable_cause(self):
        # honest_failure_modes #7: a closed enum needs closed consumers. If
        # classify() can emit a cause the tracer does not list, localization is
        # silently skipped for exactly the shape it is best at.
        from utils import wan_autotrace as wa
        assert "endpoint" in wa.TRACEABLE_CAUSES


class TestReplayOfRecordedFails:
    """Replay the REAL rung rows behind every FAIL this box recorded in the
    80 h after the 09-15 cure, from wan_path_history.jsonl.

    Four were one far target losing beside clean siblings; two were broad
    events (2026-09-16 09:50, near + two far targets losing; 2026-09-18 01:30,
    both near resolvers 100% and the only measurable far target at 15%). The
    fix has to drop the four WITHOUT dropping the two -- that is the whole
    claim, and it is made against recorded samples rather than invented ones.
    """

    #: (label, lan, edge, near_worst, {far_label: loss_or_None}, expected_status)
    RECORDED = [
        ("09-15 15:40 solo pypi", 0.0, 0.0, 0.0,
         {"cloud-vps": 0.0, "github": 0.0, "pypi": 30.0}, "concern"),
        ("09-16 09:50 near+2 far", 0.0, 0.0, 15.0,
         {"cloud-vps": 5.0, "github": 15.0, "pypi": 15.0}, "fail"),
        ("09-17 15:10 solo cloud-vps", 0.0, 0.0, 0.0,
         {"cloud-vps": 20.0, "github": 0.0, "pypi": 0.0}, "concern"),
        ("09-18 01:30 near dark", 0.0, 0.0, 100.0,
         {"cloud-vps": None, "github": None, "pypi": 15.0}, "fail"),
        ("09-18 11:30 solo github", 0.0, 0.0, 10.0,
         {"cloud-vps": 0.0, "github": 15.0, "pypi": 0.0}, "concern"),
        ("09-18 15:00 solo pypi", 0.0, 0.0, 0.0,
         {"cloud-vps": 0.0, "github": 0.0, "pypi": 10.0}, "concern"),
    ]

    @staticmethod
    def _ladder_from(lan, edge, near, fars):
        out = [R("lan", "gateway", "192.0.2.1", 20, 20, lan, 0.5, 0.1),
               R("edge", "isp-hop", "192.0.2.2", 20, 20, edge, 3.0, 0.3),
               R("near", "cloudflare-dns", "1.1.1.1", 20, 20, near, 65.0, 0.3)]
        for label, loss in fars.items():
            if loss is None:
                out.append(R("far", label, "%s.example" % label, error="unmeasured: dns"))
            else:
                out.append(R("far", label, "%s.example" % label, 20,
                             int(20 - 20 * loss / 100), loss, 80.0, 1.0))
        return out

    def test_recorded_fails_reclassify_as_measured(self):
        got = {}
        for name, lan, edge, near, fars, expected in self.RECORDED:
            v = wp.classify(self._ladder_from(lan, edge, near, fars))
            got[name] = (v.status, v.cause)
        wrong = {n: got[n] for n, _, _, _, _, exp in self.RECORDED
                 if got[n][0] != exp}
        assert not wrong, "reclassification disagrees with the measurement: %r" % wrong

    def test_the_two_broad_events_are_still_failures(self):
        # The half that matters most: a narrowing that also drops the real
        # events is not a fix. Named separately so a regression says WHICH.
        for name, lan, edge, near, fars, expected in self.RECORDED:
            if expected != "fail":
                continue
            v = wp.classify(self._ladder_from(lan, edge, near, fars))
            assert v.status == "fail", "%s must still fail, got %r" % (name, v)

    def test_four_of_the_six_recorded_fails_are_now_concerns(self):
        downgraded = [n for n, lan, edge, near, fars, _ in self.RECORDED
                      if wp.classify(self._ladder_from(lan, edge, near, fars)).status != "fail"]
        assert len(downgraded) == 4, downgraded


class TestTrailingUnclean:
    def test_a_clean_tail_is_zero(self):
        assert wp.trailing_unclean([{"s": "fail"}, {"s": "ok"}]) == 0

    def test_counts_only_the_trailing_run(self):
        rows = [{"s": "fail"}, {"s": "ok"}, {"s": "concern"}, {"s": "fail"}]
        assert wp.trailing_unclean(rows) == 2

    def test_no_history_is_zero(self):
        assert wp.trailing_unclean([]) == 0

    def test_a_corrupt_row_stops_the_count_rather_than_inflating_it(self):
        assert wp.trailing_unclean(["junk", {"s": "fail"}]) == 1

    def test_state_carries_the_streak_and_resets_on_clean(self):
        clean = wp.build_state(_ladder(), wp.classify(_ladder()), now=1.0, prior_unclean=4)
        assert clean["unclean_streak"] == 0
        lossy_l = _ladder(far=(100.0 / wp.PING_COUNT, 0.0))
        lossy = wp.build_state(lossy_l, wp.classify(lossy_l), now=1.0, prior_unclean=1)
        assert lossy["unclean_streak"] == 2
        assert lossy["concern_pct"] == wp.LOSS_CONCERN_PCT


class TestParsing:
    PING_OUT = ("PING x (1.2.3.4) 56(84) bytes of data.\n"
                "--- x ping statistics ---\n"
                "20 packets transmitted, 16 received, 20% packet loss, time 3813ms\n"
                "rtt min/avg/max/mdev = 117.1/117.966/118.9/0.5 ms\n")

    def test_parse_ping_summary(self):
        s = wp.parse_ping(self.PING_OUT)
        assert s["sent"] == 20 and s["received"] == 16
        assert abs(s["loss_pct"] - 20.0) < 1e-9
        assert s["avg_ms"] == 117.966 and s["mdev_ms"] == 0.5

    def test_parse_ping_without_summary_is_unmeasured(self):
        s = wp.parse_ping("ping: github.com: Temporary failure in name resolution\n")
        assert s["sent"] == 0 and s["loss_pct"] is None

    def test_targets_file_parses_and_malformed_lines_stay_visible(self):
        t = wp.parse_targets("# c\nlan gateway 192.0.2.1\nfar vps vps.example\nbogus line\n")
        assert [(x.rung, x.label) for x in t[:2]] == [("lan", "gateway"), ("far", "vps")]
        assert t[2].error and "malformed" in t[2].error


class TestEndpointIsRecorded:
    """The github-rung defect (2026-09-06): ``github.com`` round-robins between
    GitHub's own AS (~118 ms, that day 20-35% lossy behind a transit provider)
    and an Azure edge (~61 ms, clean). Pinging the NAME measured a different
    endpoint run to run and filed both under one label, so the history read as
    one flapping path instead of two steady ones. A measurement must say what
    it measured."""

    def test_ip_literal_resolves_to_itself(self):
        assert wp.resolve_host("192.0.2.1") == ("192.0.2.1", None)

    def test_dns_failure_is_an_error_never_a_fallback_to_the_name(self, monkeypatch):
        import socket as _s

        def boom(*a, **k):
            raise _s.gaierror("Name or service not known")

        monkeypatch.setattr(wp.socket, "getaddrinfo", boom)
        addr, err = wp.resolve_host("nx.example")
        assert addr is None and "no IPv4 address" in err

    def test_empty_dns_answer_is_an_error(self, monkeypatch):
        monkeypatch.setattr(wp.socket, "getaddrinfo", lambda *a, **k: [])
        addr, err = wp.resolve_host("nx.example")
        assert addr is None and err

    def test_measure_probes_the_address_not_the_name(self, monkeypatch):
        """Handing ping the name would let it resolve to a DIFFERENT address
        than the one recorded — the row would claim an endpoint it never hit."""
        monkeypatch.setattr(wp, "resolve_host", lambda h: ("140.82.114.3", None))
        seen = {}

        def fake_run(cmd, timeout):
            seen["cmd"] = list(cmd)
            return 0, TestParsing.PING_OUT

        monkeypatch.setattr(wp, "_run", fake_run)
        r = wp.measure(R("far", "github", "github.com"), count=20)
        assert seen["cmd"][-1] == "140.82.114.3"
        assert "github.com" not in seen["cmd"]
        assert r.addr == "140.82.114.3" and r.host == "github.com"

    def test_unresolvable_target_is_unmeasured_and_never_pinged(self, monkeypatch):
        monkeypatch.setattr(wp, "resolve_host", lambda h: (None, "no IPv4 address for 'x'"))

        def never(*a, **k):
            raise AssertionError("ping must not run when the name did not resolve")

        monkeypatch.setattr(wp, "_run", never)
        r = wp.measure(R("far", "github", "github.com"))
        assert r.loss_pct is None and "no IPv4 address" in r.error

    def test_verdict_message_names_the_endpoint_for_a_resolved_name(self):
        lossy = R("far", "github", "github.com", 20, 15, 25.0, 118.0, 1.0,
                  addr="140.82.114.3")
        assert wp.rung_name(lossy) == "github@140.82.114.3"
        assert "github@140.82.114.3 25%/118ms" in wp._fmt(lossy)

    def test_an_ip_target_is_not_annotated(self):
        assert wp.rung_name(R("near", "cloudflare-dns", "1.1.1.1", addr="1.1.1.1")) \
            == "cloudflare-dns"

    def test_two_samples_of_one_label_stay_distinguishable_in_history(self):
        """The whole point: the same label on two endpoints must not read as
        one path changing."""
        far_a = R("far", "github", "github.com", 20, 15, 25.0, 118.0, 1.0,
                  addr="140.82.114.3")
        far_b = R("far", "github", "github.com", 20, 20, 0.0, 61.0, 0.4,
                  addr="20.29.134.23")
        rows = []
        for i, far in enumerate((far_a, far_b)):
            st = wp.build_state([far], wp.classify(_ladder()), now=1000 + i)
            rows.append(json.loads(wp.history_line(st)))
        assert rows[0]["a"]["far:github"] == "140.82.114.3"
        assert rows[1]["a"]["far:github"] == "20.29.134.23"
        assert rows[0]["r"]["far:github"] == 25.0 and rows[1]["r"]["far:github"] == 0.0

    def test_history_omits_the_endpoint_map_when_nothing_can_vary(self):
        """An all-IP ladder gains no ``a`` key — the row stays as small as it
        was, and a reader of an old row is not misled into thinking one was
        recorded."""
        st = wp.build_state(_ladder(), wp.classify(_ladder()), now=1000)
        assert "a" not in json.loads(wp.history_line(st))


class TestState:
    def test_state_and_history_round_trip_and_trim(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.setattr(wp, "HISTORY_MAX_LINES", 3)
        for i in range(5):
            st = wp.build_state(_ladder(far=(float(i), 0.0)), wp.classify(_ladder()), now=1000 + i)
            wp.write_state(st)
        assert json.loads(wp.state_path().read_text())["generated_at"] == 1004
        lines = wp.history_path().read_text().splitlines()
        assert len(lines) == 3 and json.loads(lines[-1])["t"] == 1004

    def test_read_history_skips_corrupt_lines_but_counts_them(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        wp.history_path().parent.mkdir(parents=True)
        wp.history_path().write_text('{"t": 1000, "s": "ok", "c": "clean", "r": {}}\nnot json\n')
        rows = wp.read_history(since_s=10**9, now=2000)
        assert len(rows) == 1 and rows[0]["_dropped"] == 1
