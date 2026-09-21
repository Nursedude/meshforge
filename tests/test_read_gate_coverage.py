"""The kiai class: a read gate aimed at a subnet the box is not on.

Found 2026-09-20 by a human clicking, after ~2 months. kiai's map unit was
installed 2026-07-12 — before the 07-26 subnet-derivation fix — so it carried
a HARDCODED origin from another box. Its gated endpoints (/api/los,
/api/coverage) refused every client but loopback, silently.

Nothing could see it. /fleet's fan-out fetches /api/status and /fleet/slo,
both UNGATED, so the box read `healthy` the whole time. And a peer cannot be
probed for this from outside: a CORRECTLY gated box also 403s a cross-subnet
caller, so a cross-subnet probe can never separate misaimed from working.
Hence a local self-report, and hence these tests.
"""

import socket
from unittest.mock import patch

import pytest

from utils.map_http_handler import read_gate_self_coverage


class TestPosture:
    """Four postures, closed vocabulary. The pair that matters is
    loopback_only (deliberate) vs misaimed (the bug) — collapsing them is how
    a field like this stops being read."""

    def test_own_lan_admitted(self):
        with patch("utils.map_http_handler._own_primary_ipv4",
                   return_value="198.51.100.248"):
            r = read_gate_self_coverage(["http://198.51.100."])
        assert r["self_covered"] is True
        assert r["posture"] == "lan_admitted"

    def test_the_kiai_shape_reads_misaimed(self):
        """A LAN was opted in, and it is not this box's. THE bug."""
        with patch("utils.map_http_handler._own_primary_ipv4",
                   return_value="192.0.2.243"):          # kiai's real subnet
            r = read_gate_self_coverage(["http://198.51.100."])  # hardcoded
        assert r["self_covered"] is False
        assert r["posture"] == "misaimed"
        assert "MISAIMED" in r["reason"]
        assert "hostname -I" in r["reason"], "a refusal must say what to check"

    @pytest.mark.parametrize("allowed", [None, []])
    def test_no_origin_configured_is_by_design_not_a_fault(self, allowed):
        """The secure default. If this read 'misaimed', the field would alarm
        on every correctly-hardened box until nobody read it."""
        with patch("utils.map_http_handler._own_primary_ipv4",
                   return_value="192.0.2.243"):
            r = read_gate_self_coverage(allowed)
        assert r["posture"] == "loopback_only"
        assert r["self_covered"] is False
        assert "BY DESIGN" in r["reason"]
        assert "ssh -L" in r["reason"], "a refusal must name the way through"

    def test_no_default_route_is_UNKNOWN_not_broken(self):
        """Unobservable != healthy, and != failed (honest_failure_modes #1)."""
        with patch("utils.map_http_handler._own_primary_ipv4",
                   return_value=None):
            r = read_gate_self_coverage(["http://198.51.100."])
        assert r["self_covered"] is None
        assert r["posture"] == "unknown"
        assert "UNKNOWN" in r["reason"]

    def test_posture_vocabulary_is_closed(self):
        """A consumer switching on posture must fail a TEST when the set grows
        (honest_failure_modes #7), not discover it in production."""
        seen = set()
        cases = [("198.51.100.5", ["http://198.51.100."]),
                 ("192.0.2.5", ["http://198.51.100."]),
                 ("192.0.2.5", None)]
        for ip, allowed in cases:
            with patch("utils.map_http_handler._own_primary_ipv4", return_value=ip):
                seen.add(read_gate_self_coverage(allowed)["posture"])
        with patch("utils.map_http_handler._own_primary_ipv4", return_value=None):
            seen.add(read_gate_self_coverage([])["posture"])
        assert seen == {"lan_admitted", "misaimed", "loopback_only", "unknown"}


class TestNoTopologyLeak:
    """/api/status is UNAUTHENTICATED. The networks a box trusts are operator
    LAN topology (MF015) — publish the VERDICT, never the set."""

    @pytest.mark.parametrize("ip,allowed", [
        ("198.51.100.248", ["http://198.51.100."]),
        ("192.0.2.243", ["http://198.51.100.", "http://203.0.113."]),
        ("192.0.2.243", None),
    ])
    def test_no_address_or_cidr_appears_anywhere_in_the_output(self, ip, allowed):
        with patch("utils.map_http_handler._own_primary_ipv4", return_value=ip):
            r = read_gate_self_coverage(allowed)
        blob = repr(r)
        for leak in [ip, *(allowed or [])]:
            bare = leak.replace("http://", "")
            assert bare not in blob, f"{bare!r} leaked into {blob!r}"
        # the octet-level check: no dotted quad from any source. Loopback is a
        # universal constant (it appears in the `ssh -L` remedy), not operator
        # topology — exempt it explicitly rather than loosening the pattern.
        import re
        scrubbed = blob.replace("127.0.0.1", "<loopback>")
        assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", scrubbed), scrubbed

    def test_origin_count_is_published_but_not_the_origins(self):
        """A count is a useful discriminator and leaks nothing."""
        with patch("utils.map_http_handler._own_primary_ipv4",
                   return_value="192.0.2.243"):
            r = read_gate_self_coverage(["http://198.51.100.",
                                         "http://203.0.113."])
        assert r["origins_configured"] == 2


class TestOwnPrimaryIpv4:
    def test_sends_no_packet_and_returns_a_v4_address(self):
        from utils.map_http_handler import _own_primary_ipv4
        ip = _own_primary_ipv4()
        if ip is None:
            pytest.skip("no default route in this environment")
        import ipaddress
        assert ipaddress.ip_address(ip).version == 4

    def test_no_route_returns_None_rather_than_raising(self):
        from utils.map_http_handler import _own_primary_ipv4
        with patch("socket.socket", side_effect=OSError("Network unreachable")):
            assert _own_primary_ipv4() is None


class TestFixtureMatchesTheLiveUnit:
    """A fixture that fabricates a format pins the author, not the wire.

    The first cut of this file used ``http://198.51.100.0/24``. The parser
    rejects that (``.../24/32`` is not a network), yielding ZERO trusted
    networks — so every posture test read 'misaimed' for a reason that had
    nothing to do with the feature. The real form, measured from the running
    process's own cmdline, is a DOT-TERMINATED /24 prefix:

        --cors-origins http://localhost,http://127.0.0.1,http://192.0.2.

    produced by the unit's `for ip in $(hostname -I); ... http://${ip%.*}.`
    """

    def test_dot_terminated_prefix_is_the_form_that_works(self):
        from utils.map_http_handler import _trusted_networks_from_origins
        nets = _trusted_networks_from_origins(["http://192.0.2."])
        assert [str(n) for n in nets] == ["192.0.2.0/24"]

    def test_the_cidr_form_this_test_file_first_guessed_yields_nothing(self):
        """Pinned so the wrong shape can never quietly come back."""
        from utils.map_http_handler import _trusted_networks_from_origins
        assert _trusted_networks_from_origins(["http://192.0.2.0/24"]) == []

    def test_the_real_unit_origin_string_admits_the_box_itself(self):
        """End to end on the exact string the live unit builds."""
        origins = "http://localhost,http://127.0.0.1,http://192.0.2.".split(",")
        with patch("utils.map_http_handler._own_primary_ipv4",
                   return_value="192.0.2.234"):
            r = read_gate_self_coverage(origins)
        assert r["posture"] == "lan_admitted"
        # localhost is skipped as a non-IP host, not treated as a network
        from utils.map_http_handler import _trusted_networks_from_origins
        assert len(_trusted_networks_from_origins(origins)) == 2


class TestTheRealKiaiRegression:
    """The actual historical defect, preserved from kiai's pre-fix unit backup
    (/etc/systemd/system/meshforge-map.service.bak-2026-09-20) and its real
    address. A guard drilled only against a synthetic case is not evidence it
    would have caught the thing it was written for.

    ⚠️ Addresses here are RFC 5737 TEST-NET stand-ins, not the operator's real
    subnets (MF014 — and the lint refused the first version of this file for
    exactly that). What is preserved is the SHAPE that made the bug: the box
    lives on one /24 while its unit hardcodes a different one. The real pair
    is recoverable from the box and its backup unit; it does not belong here."""

    KIAI_IP = "192.0.2.243"
    PRE_FIX = "http://localhost,http://127.0.0.1,http://198.51.100."   # hardcoded
    POST_FIX = "http://localhost,http://127.0.0.1,http://192.0.2."  # derived

    def test_the_two_month_bug_reads_misaimed(self):
        with patch("utils.map_http_handler._own_primary_ipv4",
                   return_value=self.KIAI_IP):
            r = read_gate_self_coverage(self.PRE_FIX.split(","))
        assert r["posture"] == "misaimed", (
            "the check does not catch the defect it was written for")
        assert r["self_covered"] is False

    def test_the_shipped_fix_reads_admitted(self):
        """The control: the verdict above must be about the MISAIM, not about
        the checker refusing everything."""
        with patch("utils.map_http_handler._own_primary_ipv4",
                   return_value=self.KIAI_IP):
            r = read_gate_self_coverage(self.POST_FIX.split(","))
        assert r["posture"] == "lan_admitted"
        assert r["self_covered"] is True
