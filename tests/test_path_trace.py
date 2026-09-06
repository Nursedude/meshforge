"""utils.path_trace — locating WAN loss without lying about it (2026-09-06).

Every test here is one of the traps the module exists to avoid. They are
written from a real event: 25-45% loss to two destinations, every intermediate
hop reading "lossy" from the traceroute column while direct echo showed them
clean, two routers answering no echo at all, and a pager whose TCP handshakes
were failing 8 times in 10.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils import path_trace as pt  # noqa: E402

H = pt.Hop


def _hop(ttl, addr, loss, avg=50.0):
    return H(ttl=ttl, addr=addr, echo=pt.ECHO_ANSWERS, sent=10,
             received=int(10 - 10 * loss / 100), loss_pct=loss, avg_ms=avg)


def _opaque(ttl, addr):
    return H(ttl=ttl, addr=addr, echo=pt.ECHO_OPAQUE, sent=10, received=0)


def _tcp(ok, trials=10, port=443):
    return pt.TcpResult(port=port, trials=trials, ok=ok, avg_connect_s=0.1 if ok else None)


class TestTrapOne:
    """Per-hop loss must come from direct echo, never the traceroute column."""

    def test_parse_echo_without_a_summary_is_unmeasured_not_zero(self):
        s = pt.parse_echo("ping: nope\n")
        assert s["sent"] == 0 and s["loss_pct"] is None

    def test_parse_echo_reads_real_loss(self):
        out = ("--- x ping statistics ---\n"
               "20 packets transmitted, 14 received, 30% packet loss, time 1ms\n"
               "rtt min/avg/max/mdev = 1.0/215.4/300.0/0.8 ms\n")
        s = pt.parse_echo(out)
        assert s["sent"] == 20 and s["received"] == 14
        assert abs(s["loss_pct"] - 30.0) < 1e-9 and s["avg_ms"] == 215.4

    def test_a_single_lossy_hop_followed_by_clean_hops_localizes_nothing(self):
        """The rate-limit signature: hop 1 'loses' but everything past it is
        clean. Blaming it would accuse the operator's own gateway."""
        hops = [_hop(1, "10.0.0.1", 50.0), _hop(2, "10.0.0.2", 0.0),
                _hop(3, "10.0.0.3", 0.0)]
        f = pt.localize(hops, target_loss_pct=0.0, tcp=_tcp(10))
        assert f.status == "clean" and f.first_lossy is None


class TestTrapTwo:
    """A hop that answers no echo is opaque — never the culprit."""

    def test_opaque_hops_are_listed_but_never_blamed(self):
        hops = [_hop(1, "10.0.0.1", 0.0), _opaque(2, "10.0.0.2"),
                _hop(3, "10.0.0.3", 30.0)]
        f = pt.localize(hops, target_loss_pct=30.0, tcp=_tcp(4))
        assert f.status == "localized"
        assert f.first_lossy == "10.0.0.3"
        assert f.opaque == ["10.0.0.2"]
        assert "10.0.0.2" not in (f.first_lossy or "")

    def test_an_all_opaque_path_does_not_manufacture_a_culprit(self):
        hops = [_opaque(1, "10.0.0.1"), _opaque(2, "10.0.0.2")]
        f = pt.localize(hops, target_loss_pct=30.0, tcp=_tcp(4))
        assert f.status == "beyond_visibility" and f.first_lossy is None


class TestTrapThree:
    """Loss localizes only when it persists all the way to the target."""

    def test_persistent_loss_names_the_boundary_and_the_last_clean_hop(self):
        hops = [_hop(1, "10.0.0.1", 0.0), _hop(2, "10.0.0.2", 0.0),
                _hop(3, "10.0.0.3", 25.0), _hop(4, "10.0.0.4", 30.0)]
        f = pt.localize(hops, target_loss_pct=30.0, tcp=_tcp(4))
        assert f.status == "localized"
        assert f.first_lossy == "10.0.0.3" and f.last_good == "10.0.0.2"

    def test_clean_hops_with_a_lossy_target_is_beyond_visibility_not_a_guess(self):
        """The 09-06 shape: a hop at essentially full distance reads 0%, yet the
        target loses 30%. The honest answer names the limit of the method
        (commonly the return path), not a hop."""
        hops = [_hop(1, "10.0.0.1", 0.0), _hop(2, "10.0.0.2", 0.0, avg=209.0)]
        f = pt.localize(hops, target_loss_pct=30.0, tcp=_tcp(4))
        assert f.status == "beyond_visibility"
        assert f.first_lossy is None and f.last_good == "10.0.0.2"
        assert "return path" in f.message


class TestTrapFour:
    """ICMP is not the application; TCP decides whether this matters."""

    def test_icmp_loss_with_clean_tcp_is_policing_and_must_not_page(self):
        hops = [_hop(1, "10.0.0.1", 0.0)]
        f = pt.localize(hops, target_loss_pct=40.0, tcp=_tcp(10))
        assert f.status == "policing" and f.confidence == "verified"
        assert "Do not page" in f.message

    def test_a_silent_target_with_clean_tcp_is_a_filtered_echo_not_an_outage(self):
        f = pt.localize([_hop(1, "10.0.0.1", 0.0)], target_loss_pct=None, tcp=_tcp(10))
        assert f.status == "policing" and f.confidence == "verified"

    def test_a_silent_target_with_failing_tcp_is_unreachable(self):
        f = pt.localize([_hop(1, "10.0.0.1", 0.0)], target_loss_pct=None, tcp=_tcp(0))
        assert f.status == "unreachable" and f.confidence == "verified"

    def test_a_silent_target_and_no_tcp_leg_is_unknown_never_down(self):
        f = pt.localize([_hop(1, "10.0.0.1", 0.0)], target_loss_pct=None, tcp=None)
        assert f.status == "unknown" and f.confidence == "believed"

    def test_clean_icmp_and_fast_handshakes_with_failing_tcp_blames_the_service(self):
        """Only when the timing is ALSO clean may this point away from the route."""
        hops = [_hop(1, "10.0.0.1", 0.0)]
        tcp = pt.TcpResult(443, 10, 2, avg_connect_s=0.061)
        f = pt.localize(hops, target_loss_pct=0.0, tcp=tcp, target_avg_ms=60.0)
        assert f.status == "service"
        assert "not a packet-loss problem" in f.message

    def test_icmp_only_findings_stay_believed(self):
        hops = [_hop(1, "10.0.0.1", 0.0), _hop(2, "10.0.0.2", 30.0)]
        f = pt.localize(hops, target_loss_pct=30.0, tcp=None)
        assert f.status == "localized" and f.confidence == "believed"

    def test_tcp_confirmation_upgrades_the_same_finding_to_verified(self):
        hops = [_hop(1, "10.0.0.1", 0.0), _hop(2, "10.0.0.2", 30.0)]
        f = pt.localize(hops, target_loss_pct=30.0, tcp=_tcp(4))
        assert f.status == "localized" and f.confidence == "verified"


class TestDivergence:
    """Comparing a lossy path with a clean one is what names the suspect."""

    def test_divergence_finds_where_two_paths_split(self):
        a = pt.TraceResult(target="bad", hops=[_hop(1, "10.0.0.1", 0.0),
                                               _hop(2, "10.0.0.2", 0.0),
                                               _hop(3, "203.0.113.9", 30.0)])
        b = pt.TraceResult(target="good", hops=[_hop(1, "10.0.0.1", 0.0),
                                                _hop(2, "10.0.0.2", 0.0),
                                                _hop(3, "198.51.100.7", 0.0)])
        assert pt.divergence(a, b) == 2

    def test_identical_paths_do_not_diverge(self):
        a = pt.TraceResult(target="a", hops=[_hop(1, "10.0.0.1", 0.0)])
        b = pt.TraceResult(target="b", hops=[_hop(1, "10.0.0.1", 0.0)])
        assert pt.divergence(a, b) is None

    def test_a_silent_hop_never_counts_as_a_split(self):
        a = pt.TraceResult(target="a", hops=[H(ttl=1, addr=None)])
        b = pt.TraceResult(target="b", hops=[_hop(1, "10.0.0.1", 0.0)])
        assert pt.divergence(a, b) is None

    def test_compare_says_so_when_there_is_no_clean_target_to_compare_against(self):
        bad = pt.TraceResult(target="bad", hops=[_hop(1, "10.0.0.1", 30.0)],
                             finding=pt.Finding("localized", "x"))
        lines = pt.compare([bad])
        assert any("cannot separate" in l for l in lines)

    def test_compare_names_the_split_when_a_clean_target_exists(self):
        bad = pt.TraceResult(target="bad",
                             hops=[_hop(1, "10.0.0.1", 0.0), _hop(2, "203.0.113.9", 30.0)],
                             finding=pt.Finding("localized", "x"))
        good = pt.TraceResult(target="good",
                              hops=[_hop(1, "10.0.0.1", 0.0), _hop(2, "198.51.100.7", 0.0)],
                              finding=pt.Finding("clean", "y"))
        lines = pt.compare([bad, good])
        assert any("203.0.113.9" in l and "198.51.100.7" in l for l in lines)


class TestRender:
    def test_render_marks_opaque_hops_as_not_implicated(self):
        res = pt.TraceResult(target="x", addr="203.0.113.9",
                             hops=[_opaque(1, "10.0.0.1")],
                             target_loss_pct=30.0, tcp=_tcp(4))
        res.finding = pt.localize(res.hops, res.target_loss_pct, res.tcp)
        text = pt.render(res)
        assert "not implicated" in text
        assert "rate-limit time-exceeded" in text

    def test_render_says_when_the_tcp_leg_did_not_run(self):
        res = pt.TraceResult(target="x", addr="203.0.113.9",
                             hops=[_hop(1, "10.0.0.1", 0.0)], target_loss_pct=0.0)
        res.finding = pt.localize(res.hops, res.target_loss_pct, None)
        assert "ICMP-only" in pt.render(res)

    def test_an_unresolvable_target_renders_unmeasured_not_clean(self):
        res = pt.TraceResult(target="nx.example", error="no IPv4 address")
        assert "UNMEASURED" in pt.render(res)


class TestHandshakeInflation:
    """A handshake that succeeds only by retransmitting is proof of loss.

    Written 2026-09-06 after the module's OWN first live run called a path with
    20% loss and 0.62s handshakes against a 216ms RTT "policing — do not page".
    Success rate alone mapped a degraded path onto a healthy value: trap 4,
    sprung on the author of trap 4.
    """

    def test_a_handshake_at_one_round_trip_is_not_inflated(self):
        assert not pt.handshake_inflated(
            pt.TcpResult(443, 10, 10, avg_connect_s=0.061), target_avg_ms=60.3)

    def test_a_handshake_at_three_round_trips_is_inflated(self):
        assert pt.handshake_inflated(
            pt.TcpResult(443, 10, 10, avg_connect_s=0.623), target_avg_ms=216.1)

    def test_a_fast_lan_path_needs_absolute_slack_not_just_a_ratio(self):
        """On a 2 ms path, 2x is jitter — 4 ms must not read as retransmits."""
        assert not pt.handshake_inflated(
            pt.TcpResult(443, 10, 10, avg_connect_s=0.004), target_avg_ms=2.0)

    def test_inflation_is_unknowable_without_an_rtt(self):
        assert not pt.handshake_inflated(
            pt.TcpResult(443, 10, 10, avg_connect_s=0.623), target_avg_ms=None)

    def test_the_live_case_is_no_longer_called_policing(self):
        """The regression this class exists for, end to end."""
        hops = [_hop(1, "10.0.0.1", 0.0), _hop(2, "10.0.0.2", 0.0, avg=209.0)]
        tcp = pt.TcpResult(443, 10, 10, avg_connect_s=0.623)
        f = pt.localize(hops, target_loss_pct=20.0, tcp=tcp, target_avg_ms=216.1)
        assert f.status != "policing"
        assert f.confidence == "verified"
        assert "retransmits" in f.message

    def test_genuine_policing_still_reads_as_policing(self):
        """The guard must not swallow the case it was built around: real echo
        rate-limiting, with handshakes at the expected round trip."""
        hops = [_hop(1, "10.0.0.1", 0.0)]
        tcp = pt.TcpResult(443, 10, 10, avg_connect_s=0.062)
        f = pt.localize(hops, target_loss_pct=40.0, tcp=tcp, target_avg_ms=60.0)
        assert f.status == "policing" and "Do not page" in f.message


class TestClosedStatusVocabulary:
    """A new status must not read as 'fine' just because a consumer forgot it
    (honest_failure_modes #7 — closed enums need closed consumers)."""

    ALL = ("clean", "localized", "beyond_visibility", "intermittent", "policing",
           "service", "unreachable", "unknown")

    def test_every_status_is_classified_as_fault_or_ok_or_unknown(self):
        covered = set(pt.FAULT_STATUSES) | set(pt.OK_STATUSES) | {"unknown"}
        assert set(self.ALL) == covered, "a status is classified nowhere"

    def test_fault_and_ok_never_overlap(self):
        assert not (set(pt.FAULT_STATUSES) & set(pt.OK_STATUSES))

    def test_compare_counts_an_intermittent_target_as_losing(self):
        bad = pt.TraceResult(target="bad", hops=[_hop(1, "10.0.0.1", 0.0)],
                             finding=pt.Finding("intermittent", "x"))
        good = pt.TraceResult(target="good", hops=[_hop(1, "10.0.0.1", 0.0)],
                              finding=pt.Finding("clean", "y"))
        lines = pt.compare([bad, good])
        assert "1 of 2 target(s) losing" in lines[0]


class TestSampledLossHonesty:
    """Ten probes against a path that reads 0/20/30/40% across runs will
    sometimes come up empty. The timing still knows."""

    def test_a_clean_icmp_sample_with_inflated_handshakes_is_intermittent(self):
        hops = [_hop(1, "10.0.0.1", 0.0)]
        tcp = pt.TcpResult(443, 10, 9, avg_connect_s=0.444)
        f = pt.localize(hops, target_loss_pct=0.0, tcp=tcp, target_avg_ms=215.7)
        assert f.status == "intermittent" and f.confidence == "verified"
        assert "missed them" in f.message

    def test_it_does_not_fire_when_the_handshakes_are_at_the_round_trip(self):
        hops = [_hop(1, "10.0.0.1", 0.0)]
        tcp = pt.TcpResult(443, 10, 10, avg_connect_s=0.061)
        f = pt.localize(hops, target_loss_pct=0.0, tcp=tcp, target_avg_ms=60.0)
        assert f.status == "clean"

    def test_the_live_444ms_case_the_max_form_suppressed(self):
        """Regression: 0.444s against a 216ms path IS a retransmit. The first
        threshold used max(2x, +0.25s), and the absolute term won on a slow
        path and called it fine."""
        assert pt.handshake_inflated(
            pt.TcpResult(443, 10, 9, avg_connect_s=0.444), target_avg_ms=215.7)


class TestPerConnectTiming:
    """The mean hides a single retransmit; the individual connect does not.

    Third live catch of the session (2026-09-06): 10/10 handshakes ok at a
    0.314s average against a 216ms path. That average is exactly
    (9 x 0.216 + 1.216)/10 — one retransmitted handshake — and every ratio test
    on the mean called it healthy."""

    RTT_MS = 216.0
    #: nine clean round trips and one that was resent
    TIMES = [0.216] * 9 + [1.216]

    def test_one_retransmit_in_ten_is_counted(self):
        tcp = pt.TcpResult(443, 10, 10, avg_connect_s=sum(TIMES := self.TIMES) / 10,
                           times=TIMES)
        assert pt.slow_handshakes(tcp, self.RTT_MS) == 1
        assert pt.handshake_inflated(tcp, self.RTT_MS)

    def test_the_mean_of_that_same_sample_would_have_been_missed(self):
        """Why the per-connect rule exists: the average alone is under the bar."""
        avg_only = pt.TcpResult(443, 10, 10, avg_connect_s=sum(self.TIMES) / 10)
        assert not pt.handshake_inflated(avg_only, self.RTT_MS)

    def test_ten_clean_connects_count_zero(self):
        tcp = pt.TcpResult(443, 10, 10, avg_connect_s=0.216, times=[0.216] * 10)
        assert pt.slow_handshakes(tcp, self.RTT_MS) == 0

    def test_the_finding_names_the_retransmitted_handshakes(self):
        tcp = pt.TcpResult(443, 10, 10, avg_connect_s=sum(self.TIMES) / 10,
                           times=self.TIMES)
        f = pt.localize([_hop(1, "10.0.0.1", 0.0)], target_loss_pct=0.0,
                        tcp=tcp, target_avg_ms=self.RTT_MS)
        assert f.status == "intermittent"
        assert "1 of 10 handshake(s)" in f.message and "1.22s" in f.message


class TestRepeatedHopAddresses:
    """A router can appear twice on a path. Recovering the position with
    ``list.index()`` would match the FIRST equal Hop — dataclasses compare by
    value — and name the wrong last-clean hop."""

    def test_a_duplicated_clean_hop_does_not_shift_the_boundary(self):
        hops = [_hop(1, "10.0.0.9", 0.0, avg=5.0),   # identical readings...
                _hop(2, "10.0.0.9", 0.0, avg=5.0),   # ...to the one before it
                _hop(3, "10.0.0.3", 30.0),
                _hop(4, "10.0.0.4", 30.0)]
        f = pt.localize(hops, target_loss_pct=30.0, tcp=_tcp(4))
        assert f.first_lossy == "10.0.0.3"
        assert f.last_good == "10.0.0.9"

    def test_a_duplicated_lossy_hop_still_names_the_first_occurrence(self):
        hops = [_hop(1, "10.0.0.1", 0.0),
                _hop(2, "10.0.0.7", 30.0, avg=90.0),
                _hop(3, "10.0.0.7", 30.0, avg=90.0)]
        f = pt.localize(hops, target_loss_pct=30.0, tcp=_tcp(4))
        assert f.first_lossy == "10.0.0.7" and f.last_good == "10.0.0.1"
