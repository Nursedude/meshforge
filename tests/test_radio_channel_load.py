"""Channel Load — time over the 25 % knee, utilization, noise floor, decode fails.

Born 2026-10-03. The first draft read "knee lines but no samples" as "over the
knee the whole window". Two contextless reviewers showed at the pinned
firmware source (DeviceTelemetry.cpp:26-43) that samples are logged only
while meshtasticd's to-phone queue is empty — i.e. an API client is attached
— so their absence says nothing about the channel. These tests pin the
corrected model; each reproduced reviewer case has its own test.

Fixtures are VERBATIM `journalctl -o short-unix` lines from that day
(hostname/ident replaced, MF014): the `Send:` + `Sending local stats` twins at
one uptime, a `[Router]` knee line, and a live `noise_floor=-120` (the
firmware default) 255 s after a reboot.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from utils import radio_channel_load as cl  # noqa: E402
from utils.observation import Seen, Unobservable  # noqa: E402

TWIN_1 = [
    "1791059228.080453 box mtd[1]: INFO  | 20:27:08 5595 [DeviceTelemetry] Send: air_util_tx=0.028028, channel_utilization=10.655000, battery_level=101, voltage=0.000000, uptime=5595",
    "1791059228.081768 box mtd[1]: INFO  | 20:27:08 5595 [DeviceTelemetry] Sending local stats: uptime=5595, channel_utilization=10.655000, air_util_tx=0.028028, num_online_nodes=84, num_total_nodes=343, noise_floor=-79",
    "1791059228.081768 box mtd[1]: INFO  | 20:27:08 5595 [DeviceTelemetry] num_packets_tx=2, num_packets_rx=1160, num_packets_rx_bad=53",
]
TWIN_2 = [
    "1791060788.087000 box mtd[1]: INFO  | 20:53:08 7155 [DeviceTelemetry] Send: air_util_tx=0.028028, channel_utilization=14.863333, battery_level=101, voltage=0.000000, uptime=7155",
    "1791060788.087121 box mtd[1]: INFO  | 20:53:08 7155 [DeviceTelemetry] Sending local stats: uptime=7155, channel_utilization=14.863333, air_util_tx=0.028028, num_online_nodes=91, num_total_nodes=343, noise_floor=-79",
    "1791060788.087121 box mtd[1]: INFO  | 20:53:08 7155 [DeviceTelemetry] num_packets_tx=3, num_packets_rx=1516, num_packets_rx_bad=70",
]
DEFAULT_NOISE = ["1791053888.068484 box mtd[1]: INFO  | 18:58:08 255 [DeviceTelemetry] Sending local stats: uptime=255, channel_utilization=21.768333, air_util_tx=0.000000, num_online_nodes=71, num_total_nodes=342, noise_floor=-120"]
SKIPS = [
    "1791069379.566601 box mtd[3]: WARN  | 23:16:19 15375 [DeviceTelemetry] Ch. util >25%. Skip send",
    "1791069386.567350 box mtd[3]: WARN  | 23:16:26 15382 [Position] Ch. util >25%. Skip send",
    "1791069837.887500 box mtd[3]: WARN  | 23:23:57 16204 [Router] Ch. util >25%. Skip send",
]
SKIP40 = ["1791069900.000000 box mtd[3]: WARN  | 23:25:00 16267 [NodeInfo] Ch. util >40%. Skip send"]


def _send(ts, util, uptime):
    return (f"{ts:.6f} box mtd[1]: INFO  | x {uptime} [DeviceTelemetry] Send: air_util_tx=0.028, "
            f"channel_utilization={util:.6f}, battery_level=101, voltage=0.000000, uptime={uptime}")


def _skip(ts, mod="Position", pct=25):
    return f"{ts:.6f} box mtd[3]: WARN  | x 1 [{mod}] Ch. util >{pct}%. Skip send"


class TestParse:
    def test_twins_are_one_sample(self):
        f = cl.parse_journal(TWIN_1 + TWIN_2)
        assert [round(x, 3) for x in f.util_samples] == [10.655, 14.863]

    def test_noise_nodes_counters(self):
        f = cl.parse_journal(TWIN_1 + TWIN_2)
        assert f.noise_floor == -79 and f.online_nodes == 91
        assert (f.rx_delta, f.rx_bad_delta) == (356, 17)
        assert f.delta_since == pytest.approx(1791059228.081768)

    def test_minus_120_is_the_firmware_default_not_a_reading(self):
        f = cl.parse_journal(DEFAULT_NOISE)
        assert f.noise_floor is None and f.noise_default_seen

    def test_knee_lines_counted_as_distinct_minutes_by_module(self):
        five_s = [_skip(1791069000 + 5 * i) for i in range(12)]          # one minute of 5 s ticks
        f = cl.parse_journal(five_s + SKIPS)
        assert f.knee_minutes == 3                                         # 23:10, 23:16, 23:23 buckets
        assert f.knee_by_module["Position"] == 13

    def test_counter_decrease_restarts_the_delta_even_without_uptime_reset(self):
        # USB-radio LoRa recovery rebuilds the radio interface: counters
        # restart while uptime keeps rising.
        recovered = [
            "1791061000.0 box mtd[1]: INFO  | x 7200 [DeviceTelemetry] num_packets_tx=0, num_packets_rx=40, num_packets_rx_bad=2",
            "1791061900.0 box mtd[1]: INFO  | x 8100 [DeviceTelemetry] num_packets_tx=1, num_packets_rx=240, num_packets_rx_bad=12",
        ]
        f = cl.parse_journal(TWIN_1 + TWIN_2 + recovered)
        assert (f.rx_delta, f.rx_bad_delta) == (200, 10)
        assert f.delta_since == pytest.approx(1791061000.0)

    def test_single_counter_sample_has_no_delta(self):
        assert cl.parse_journal(TWIN_1).rx_delta is None


def _j(lines):
    return Seen(cl.parse_journal(lines))


class TestVerdict:
    def test_ok(self):
        assert cl.verdict(_j(TWIN_1 + TWIN_2), window_h=3).status == "OK"

    def test_knee_lines_without_samples_is_not_whole_window(self):
        # Reviewer case (B): ONE knee line, no API client → must not claim
        # the channel was over the knee all window.
        v = cl.verdict(_j(SKIPS[-1:]), window_h=3)
        assert v.status == "CROSSES KNEE"
        assert any("NOT a channel reading" in line for line in v.lines)

    def test_samples_all_over_knee_is_mostly_over(self):
        # Reviewer case (A): every sample 30-38 % plus knee lines read NEAR.
        lines = [_send(1791060000 + 60 * i, 30 + i % 9, 9000 + 60 * i) for i in range(10)] + SKIPS
        assert cl.verdict(_j(lines), window_h=3).status == "MOSTLY OVER KNEE"

    def test_knee_minutes_over_half_the_window_is_mostly_over(self):
        lines = [_skip(1791060000 + 60 * i) for i in range(100)]          # 100 of 180 minutes
        assert cl.verdict(_j(lines), window_h=3).status == "MOSTLY OVER KNEE"

    def test_sample_over_40_without_a_40_line_is_over_40(self):
        # CLIENT roles never log >40%; a 52 % sample must still say so.
        assert cl.verdict(_j([_send(1791060000, 52.1, 9000)]), window_h=3).status == "OVER 40%"

    def test_40_line(self):
        assert cl.verdict(_j(TWIN_1 + SKIP40), window_h=3).status == "OVER 40%"

    def test_no_samples_no_knee_lines_cannot_tell(self):
        v = cl.verdict(_j([]), window_h=3)
        assert v.status == "UNOBSERVABLE"
        assert any("quiet channel and an unwatched one look the same" in line for line in v.lines)

    def test_texts_do_not_claim_messages_are_blocked(self):
        v = cl.verdict(_j(SKIPS), window_h=3)
        assert any("NOT throttled" in line for line in v.lines)
        assert not any("sends withheld" in line for line in v.lines)

    def test_decode_fail_label_is_not_collision_only(self):
        v = cl.verdict(_j(TWIN_1 + TWIN_2), window_h=3)
        line = next(x for x in v.lines if x.startswith("decode fails"))
        assert "17/356" in line and "4.8%" in line and "weak signals" in line

    def test_aloha_is_labelled_a_model(self):
        v = cl.verdict(_j(TWIN_1), window_h=3)
        assert any(x.startswith("model") and "not a measurement" in x for x in v.lines)

    def test_default_noise_is_not_rendered_as_dbm(self):
        v = cl.verdict(_j(DEFAULT_NOISE), window_h=3)
        assert not any("-120 dBm" in line for line in v.lines)
        assert any("no sample yet" in line for line in v.lines)

    def test_stopped_service(self):
        v = cl.verdict(_j(TWIN_1), window_h=3, service=Unobservable("meshtasticd is not_running — x"))
        assert v.status == "UNOBSERVABLE" and not any("applied" in line for line in v.lines)

    def test_absent(self):
        assert cl.verdict(_j([]), window_h=3, service=Seen("absent")).status == "ABSENT"

    def test_journal_unreadable(self):
        assert cl.verdict(Unobservable("journalctl timed out"), window_h=3).status == "UNOBSERVABLE"


def _fake_run(rc, out, err):
    def run(argv, **kw):
        assert argv[:2] == ["journalctl", "-u"]
        return subprocess.CompletedProcess(argv, rc, out, err)
    return run


class TestReadJournal:
    def test_rc0_parses(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _fake_run(0, "\n".join(TWIN_1) + "\n", ""))
        match cl.read_journal(3):
            case Seen(value=f):
                assert f.util_samples == [10.655]
            case other:
                pytest.fail(f"expected Seen, got {other}")

    def test_rc1_no_stderr_is_no_matches(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _fake_run(1, "", ""))
        assert cl.read_journal(3) == Seen(cl.parse_journal([]))

    def test_rc1_with_stderr_is_unobservable(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _fake_run(1, "", "Hint: You are currently not seeing messages from other users"))
        assert isinstance(cl.read_journal(3), Unobservable)

    def test_timeout_is_unobservable(self, monkeypatch):
        def boom(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 20)
        monkeypatch.setattr(subprocess, "run", boom)
        assert isinstance(cl.read_journal(3), Unobservable)


class TestHandler:
    def _handler(self):
        from handler_test_utils import make_handler_context
        from handlers.meshtasticd_channel_load import MeshtasticdChannelLoadHandler
        h = MeshtasticdChannelLoadHandler()
        h.set_context(make_handler_context())
        h.ctx.safe_call = lambda title, fn, *a, **k: fn(*a, **k)
        return h

    def test_menu(self):
        from handlers.meshtasticd_channel_load import MeshtasticdChannelLoadHandler
        assert MeshtasticdChannelLoadHandler.menu_section == "meshtasticd"
        assert [t for t, _, _ in MeshtasticdChannelLoadHandler().menu_items()] == ["channel_load"]

    def test_every_status_has_an_explanation(self):
        from handlers.meshtasticd_channel_load import _EXPLAIN
        assert set(_EXPLAIN) == {"OK", "CROSSES KNEE", "MOSTLY OVER KNEE", "OVER 40%", "UNOBSERVABLE", "ABSENT"}

    def test_mostly_over_renders_mesh_levers_not_a_blocked_claim(self, monkeypatch):
        h = self._handler()
        lines = [_skip(1791060000 + 60 * i) for i in range(100)]
        monkeypatch.setattr(cl, "read_all", lambda window_h=3: cl.verdict(_j(lines), window_h=3))
        h.execute("channel_load")
        text = h.ctx.dialog.last_msgbox_text
        assert text.startswith("Status: MOSTLY OVER KNEE")
        assert "hop limit" in text and "NOT throttled" in text

    def test_never_writes(self):
        import ast
        import inspect
        from handlers import meshtasticd_channel_load
        allowed = {("journalctl", "-u")}
        for mod in (meshtasticd_channel_load, cl):
            for node in ast.walk(ast.parse(inspect.getsource(mod))):
                if isinstance(node, ast.Attribute):
                    assert node.attr not in ("write_bytes", "write_text", "TCPInterface", "unlink", "Popen"), node.attr
                if isinstance(node, ast.Name):
                    assert node.id not in ("TCPInterface", "open"), node.id
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                    argv = node.args[0]
                    assert isinstance(argv, ast.List)
                    assert tuple(e.value for e in argv.elts[:2] if isinstance(e, ast.Constant)) in allowed


def test_knee_minutes_are_not_claimed_as_a_duration():
    v = cl.verdict(_j(SKIPS), window_h=3)
    line = next(x for x in v.lines if x.startswith("over knee"))
    assert "NOT a duration" in line and "lower bound" not in line
