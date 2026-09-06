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
        v = wp.classify(_ladder(far=(2.0, 0.0)))
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
