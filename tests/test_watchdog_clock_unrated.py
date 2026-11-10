"""meshtasticd_clock_unrated (2026-11-09): read the NTP gate's fail-open witness.

Pre-registered acceptance (operator 11-09, before code):
  FIRES only when the CURRENT invocation carries the gate's TIMEOUT witness AND
  its last 2 DeviceTelemetry lines both read num_online_nodes=0 while non-Admin
  `[Router] Received` lines appear. CLEAN with no witness, or witness + nodes
  online (lehua 11-09: witness present, 35 online — must NOT fire). INERT with
  no meshtasticd. INDETERMINATE when the invocation's head is unreadable, the
  gate drop-in is not loaded, or no telemetry is visible. The detail names the
  cure (restart once timedatectl says synchronized).

Review r1 (code-review medium, 11-09) sharpened three edges, all pinned here:
"received" must come from >= 2 DISTINCT senders (the radio logs its own
packets as `Received ... from=<own id>` — measured on moc2), a start with NO
gate line is never read clean on that absence, and nothing is aged by the wall
clock. Message shapes are copied from lehua's / moc2's live journals (11-09).

REVISED by operator decision after review r5 (11-09): OBSERVE-ONLY. The
journal is mesh-writable (a stranger's text with a newline becomes a separate
entry field-identical to real telemetry), so the probe NEVER returns a
Signal: the frozen shape is an indeterminate disposition carrying
``FROZEN_MARK``; a WITNESSED start reads indeterminate even with nodes online
(the witness is authenticated via journald's trusted _COMM, the node count is
not). Promotion to a page waits for an unforgeable consequence source.
"""

import os
import stat

import pytest

import utils.watchdog_probes_clock as wc
from utils.watchdog_probe_core import collect_dispositions, reset_dispositions

START = 103_434_620          # lehua's real ExecMainStartTimestampMonotonic
S = 1_000_000                # 1 s in µs
NOW_S = 10_000.0             # the probe's monotonic "now" in these tests
NOW = int(NOW_S * S)

WITNESS = ("wait_for_ntp_sync: TIMEOUT after 60s — system clock not "
           "NTP-synchronized. Starting meshtasticd anyway (fail-open).")
SYNCED_AFTER = "wait_for_ntp_sync: clock synchronized after 18s"
RTC = "WARN  | 23:07:17 0 RTC not found (found address 0x00)"
OWN = "0xddfb8065"


def tele(n):
    return ("INFO  | 17:37:40 1153823 [DeviceTelemetry] Sending local stats: "
            f"uptime=1153823, channel_utilization=2.1, air_util_tx=0.79, "
            f"num_online_nodes={n}, num_total_nodes=200, noise_floor=-116")


def rx(kind="position", frm="0xebfa1b11"):
    return f"INFO  | 17:38:01 1153844 [Router] Received {kind} from={frm}, id=0x99, portnum=3"


def ago(sec):
    return NOW - int(sec * S)


PROPS = {"InvocationID": "inv-1", "ExecMainStartTimestampMonotonic": str(START),
         "DropInPaths": "/etc/systemd/system/meshtasticd.service.d/20-wait-for-ntp-sync.conf "
                        "/etc/systemd/system/meshtasticd.service.d/30-log-filter.conf"}


@pytest.fixture(autouse=True)
def _fresh():
    wc._HEAD_CACHE.clear()
    wc._CONSEQUENCE_CACHE.clear()
    reset_dispositions()
    yield
    wc._HEAD_CACHE.clear()
    wc._CONSEQUENCE_CACHE.clear()


def run(head, window, props=PROPS, now_s=NOW_S):
    calls = {"head": 0, "window": 0}

    def head_fn(inv, stop):
        calls["head"] += 1
        assert stop > START, "the head read must stop just past the main start"
        return head

    def window_fn(inv):
        calls["window"] += 1
        return window

    reset_dispositions()
    sig = wc.probe_meshtasticd_clock_unrated(
        main_pid=1390, show_fn=lambda: props, head_fn=head_fn,
        window_fn=window_fn, monotonic=lambda: now_s)
    return sig, collect_dispositions().get(wc.CLS), calls


GATE = "wait_for_ntp_sy"           # journald's trusted _COMM for the gate
MTD = "meshtasticd"
HEAD_WITNESS = [(START - 2 * S, WITNESS, GATE), (START + S, RTC, MTD)]
HEAD_GATE_CLEAN = [(START - 18 * S, SYNCED_AFTER, GATE), (START + S, RTC, MTD)]
HEAD_GATE_SILENT = [(START + S, RTC, MTD)]     # no gate line at all
#: The gate's line WITHOUT journald's _COMM: the gate exited before journald
#: read /proc/<pid>/comm (Fable 5.2 review r2 11-09: 8/30 and 19/30 kept it on
#: the 0 s fast path, 30/30 with a 0.3 s linger).
HEAD_GATE_UNAUTH = [(START - S, "wait_for_ntp_sync: clock synchronized after 0s", None),
                    (START + S, RTC, MTD)]
HEAD_WITNESS_UNAUTH = [(START - 2 * S, WITNESS, None), (START + S, RTC, MTD)]


def frozen(res):
    """Observe-only: the would-be page is an indeterminate disposition with
    FROZEN_MARK — and NEVER a Signal."""
    sig, disp, _ = res
    assert sig is None, "observe-only: the probe must never return a Signal"
    return bool(disp) and disp["disp"] == "indeterminate" and wc.FROZEN_MARK in disp["reason"]


def shape(res):
    """WHICH branch spoke — ONE seam for every acceptance edge (Fable 5.2
    review 11-09 F1: since r5, frozen / online / blind all read
    ``indeterminate``, so a test asserting only the disposition no longer
    pinned the edge — mutants dropping the >=2-senders rule or the 2 h age
    bound survived 87/87). Exactly one mark must match."""
    sig, disp, _ = res
    assert sig is None, "observe-only: the probe must never return a Signal"
    reason = (disp or {}).get("reason", "")
    marks = {"frozen": wc.FROZEN_MARK, "blind": wc.BLIND_MARK,
             "online": wc.ONLINE_MARK}
    hits = [name for name, mark in marks.items() if mark in reason]
    assert len(hits) <= 1, f"ambiguous reason, marks {hits}: {reason!r}"
    return hits[0] if hits else None
FROZEN = [(ago(1800), tele(0)), (ago(1700), rx()), (ago(1600), rx("Admin", "0x1")),
          (ago(1200), rx("nodeinfo", "0x47115e4f")), (ago(1100), rx("routing", OWN)),
          (ago(900), tele(0))]
LEHUA = [(ago(1800), tele(32)), (ago(1000), rx()), (ago(900), tele(35))]


class TestAcceptance:
    def test_witness_and_frozen_shape_is_said_never_paged(self):
        res = run(HEAD_WITNESS, FROZEN)
        assert frozen(res)
        reason = res[1]["reason"]
        assert "sudo systemctl restart meshtasticd" in reason
        assert "timedatectl" in reason
        assert "UNVERIFIED" in reason and "mesh-writable" in reason
        assert "3 other sender(s)" in reason and "3 RX line(s)" in reason   # Admin excluded

    def test_lehua_witness_but_nodes_online_is_indeterminate_not_frozen(self):
        """r5 revision: the witness is authenticated, the excusing node count
        is not — indeterminate, the count named as unverified, never FROZEN."""
        res = run(HEAD_WITNESS, LEHUA)
        assert not frozen(res)
        disp = res[1]
        assert disp["disp"] == "indeterminate" and shape(res) == "online"
        assert "32 then 35 node(s)" in disp["reason"] and "unverified" in disp["reason"]
        # a WITNESSED start's cure re-rates the clock; it never promises clean
        assert "re-rates the clock" in disp["reason"]
        assert "reads clean" not in disp["reason"]

    def test_gate_line_without_timeout_is_clean_and_never_reads_the_window(self):
        sig, disp, calls = run(HEAD_GATE_CLEAN, FROZEN)
        assert sig is None and disp["disp"] == "clean"
        assert calls["window"] == 0

    def test_absent_unit_is_inert(self):
        from unittest.mock import patch
        reset_dispositions()
        with patch.object(wc, "_resolve_main_pid_status", return_value=("absent", None)):
            assert wc.probe_meshtasticd_clock_unrated() is None
        assert collect_dispositions()[wc.CLS]["disp"] == "inert"

    def test_gate_not_loaded_is_indeterminate(self):
        props = dict(PROPS, DropInPaths="/etc/systemd/system/meshtasticd.service.d/30-log-filter.conf")
        res = run(HEAD_WITNESS, FROZEN, props=props)
        sig, disp, calls = res
        assert disp["disp"] == "indeterminate" and shape(res) is None
        assert wc.GATE_DROPIN in disp["reason"]
        assert calls["head"] == 0

    @pytest.mark.parametrize("window", [[], [(ago(60), tele(0))], [(ago(60), rx())]])
    def test_no_telemetry_pair_is_indeterminate(self, window):
        res = run(HEAD_WITNESS, window)
        assert res[1]["disp"] == "indeterminate" and shape(res) == "blind"

    def test_only_the_last_two_telemetry_lines_count(self):
        recovered = [(ago(3000), tele(0)), (ago(2900), rx()), (ago(2000), tele(0)),
                     (ago(900), tele(4))]
        assert not frozen(run(HEAD_WITNESS, recovered))
        wc._HEAD_CACHE.clear(); wc._CONSEQUENCE_CACHE.clear()
        relapsed = [(ago(3000), tele(9)), (ago(2000), tele(0)), (ago(1900), rx()),
                    (ago(1800), rx("text", "0x2")), (ago(900), tele(0))]
        assert frozen(run(HEAD_WITNESS, relapsed))

    def test_one_zero_line_is_not_a_run(self):
        """Alarm on a RUN, not one event (mutant M3 survived without this)."""
        one_zero = [(ago(1800), tele(7)), (ago(1000), rx()), (ago(950), rx("text", "0x2")),
                    (ago(900), tele(0))]
        res = run(HEAD_WITNESS, one_zero)
        assert shape(res) == "online"
        disp = res[1]
        # r1 F6: never "0 node(s) online — rated well enough"; r2 F4: never
        # "reads 7 online" when the NEWEST reading is 0 — both, in order.
        assert "7 then 0 node(s)" in disp["reason"] and "[7, 0]" in disp["reason"]


class TestReviewR1:
    def test_own_packets_alone_never_prove_hearing(self):
        """F5: moc2 logs `Received routing from=<own id>`; a deaf radio on a
        quiet segment must not read as 'clock unrated'."""
        own_only = [(ago(1800), tele(0)), (ago(1700), rx("routing", OWN)),
                    (ago(900), tele(0)), (ago(800), rx("position", OWN))]
        res = run(HEAD_WITNESS, own_only)
        assert res[1]["disp"] == "indeterminate" and shape(res) == "blind"
        assert "only 1 other sender(s) heard" in res[1]["reason"]

    def test_rx_before_the_older_telemetry_line_does_not_count(self):
        old_rx = [(ago(3000), rx()), (ago(2900), rx("text", "0x2")),
                  (ago(1800), tele(0)), (ago(900), tele(0))]
        assert not frozen(run(HEAD_WITNESS, old_rx))

    @pytest.mark.parametrize("age_s", [3 * 3600, -120])
    def test_stale_or_future_telemetry_is_blind(self, age_s):
        """F4/F8 + hfm #6: aged by the MONOTONIC clock; too old or 'ahead of
        now' cannot be judged."""
        w = [(ago(age_s + 900), tele(0)), (ago(age_s + 100), rx()),
             (ago(age_s + 50), rx("text", "0x2")), (ago(age_s), tele(0))]
        res = run(HEAD_WITNESS, w)
        assert res[1]["disp"] == "indeterminate" and shape(res) == "blind"
        assert "the newest telemetry line is" in res[1]["reason"]   # r4 F3: named, not a dict

    def test_long_telemetry_interval_still_judged(self):
        """F8: 90-min telemetry spacing — the newest two are still read."""
        w = [(ago(6000), tele(0)), (ago(5000), rx()), (ago(4000), rx("text", "0x2")),
             (ago(600), tele(0))]
        assert frozen(run(HEAD_WITNESS, w))

    def test_gate_silent_start_never_reads_clean_on_absence(self):
        """F2/F7: no gate line (gate did not run, rotated, or pre-11-09 gate):
        the CONSEQUENCE decides — frozen shape is indeterminate, said loudly,
        never paged on inference and never 'clean'."""
        res = run(HEAD_GATE_SILENT, FROZEN)
        sig, disp, calls = res
        assert disp["disp"] == "indeterminate" and shape(res) == "frozen"
        # r4 F4: no gate line cannot earn "LIKELY" — only a TIMEOUT can
        assert "possibly started before NTP sync" in disp["reason"]
        assert "LIKELY" not in disp["reason"]
        assert wc.FROZEN_MARK in disp["reason"]
        assert "sudo systemctl restart meshtasticd" in disp["reason"]
        assert calls["window"] == 1

    def test_gate_silent_start_with_nodes_online_is_indeterminate(self):
        """Fable 5.2 review 11-09 F2 (operator: indeterminate): the r5 lesson
        holds on EVERY branch — journal telemetry is mesh-writable, so it
        cannot excuse an UNWITNESSED start any more than a witnessed one. One
        forged ``num_online_nodes=7`` line used to flip a frozen gate-silent
        start to ``clean``. The reason names the count as unverified and the
        one-time cure (a restart now runs the always-speaking gate)."""
        res = run(HEAD_GATE_SILENT, LEHUA)
        disp = res[1]
        assert disp["disp"] == "indeterminate" and shape(res) == "online"
        assert "no NTP-gate line" in disp["reason"]
        assert "35 node(s)" in disp["reason"] and "unverified" in disp["reason"]
        assert "restart meshtasticd" in disp["reason"]
        # r2 F3: the head-specific cure — the NEXT start records the line;
        # a gate-silent start is never told its clock gets re-rated.
        assert wc.CURE_NEXT_START in disp["reason"]
        assert wc.CURE_RERATE not in disp["reason"]

    def test_one_forged_online_line_never_clears_a_frozen_silent_start(self):
        """F2's drill, pinned: frozen window + ONE newer telemetry-shaped line
        with a non-zero count must never read clean."""
        forged = FROZEN + [(ago(60), tele(7))]
        res = run(HEAD_GATE_SILENT, forged)
        # r2 F2: the [0, 7] pair is ONLINE — pins `n1 or n2`, not `n1` alone
        assert res[1]["disp"] == "indeterminate" and shape(res) == "online"

    def test_no_journal_consequence_reads_clean(self):
        """Only the authenticated gate line can make this class clean."""
        want = {id(FROZEN): "frozen", id(LEHUA): "online"}
        for head in (HEAD_WITNESS, HEAD_GATE_SILENT, HEAD_GATE_UNAUTH,
                     HEAD_WITNESS_UNAUTH):
            for window in (FROZEN, LEHUA, []):
                wc._HEAD_CACHE.clear(); wc._CONSEQUENCE_CACHE.clear()
                res = run(head, window)
                assert res[1]["disp"] == "indeterminate", (head, window)
                assert shape(res) == want.get(id(window), "blind"), (head, window)

    def test_an_unauthenticated_gate_line_is_named_not_called_absent(self):
        """r2 F1: journald can lose the gate's _COMM (the gate exits before
        journald reads it). The line is PRESENT but unauthenticated — never
        "no NTP-gate line is readable". r3 F1: the CURE follows what the line
        SAYS (a TIMEOUT gets the re-rate cure), not the _COMM race."""
        for head, says in ((HEAD_GATE_UNAUTH, "synchronized"),
                           (HEAD_WITNESS_UNAUTH, "TIMEOUT")):
            wc._HEAD_CACHE.clear(); wc._CONSEQUENCE_CACHE.clear()
            res = run(head, LEHUA)
            reason = res[1]["reason"]
            assert res[1]["disp"] == "indeterminate" and shape(res) == "online"
            assert "no NTP-gate line is readable" not in reason
            assert "unauthenticated" in reason and "_COMM" in reason
            assert f"it reads {says}" in reason
            want = wc.CURE_RERATE if says == "TIMEOUT" else wc.CURE_NEXT_START
            other = wc.CURE_NEXT_START if says == "TIMEOUT" else wc.CURE_RERATE
            assert want in reason and other not in reason
        # only a GATE line's own witness bit says TIMEOUT — a comm-less
        # telemetry/RX line in the head (truthy count/kind) must not
        # (mutant: `c[1]` without `c[0] == "gate"` survived 93/93)
        noisy = HEAD_GATE_UNAUTH + [(START + 2 * S, tele(5), None),
                                    (START + 3 * S, rx(), None)]
        wc._HEAD_CACHE.clear(); wc._CONSEQUENCE_CACHE.clear()
        res = run(noisy, LEHUA)
        assert "it reads synchronized" in res[1]["reason"]

    def test_judge_head_states(self):
        assert wc.judge_head(HEAD_WITNESS, START) == "witness"
        assert wc.judge_head(HEAD_GATE_CLEAN, START) == "gate_clean"
        assert wc.judge_head(HEAD_GATE_SILENT, START) == "gate_silent"
        assert wc.judge_head(HEAD_GATE_UNAUTH, START) == "gate_unauthenticated"
        assert wc.judge_head(HEAD_WITNESS_UNAUTH, START) == "gate_unauthenticated"
        # authenticated beats unauthenticated; a FOREIGN comm is a forgery,
        # never "unauthenticated" (a payload logs under meshtasticd's comm)
        assert wc.judge_head(HEAD_GATE_UNAUTH + HEAD_GATE_CLEAN, START) == "gate_clean"
        forged = [(START + 5 * S, SYNCED_AFTER, MTD), (START + S, RTC, MTD)]
        assert wc.judge_head(forged, START) == "gate_silent"

    def test_empty_head_is_not_cached(self):
        """F1: a just-started unit's lines may not be flushed yet."""
        res = run([], FROZEN)
        assert res[1]["disp"] == "indeterminate" and shape(res) is None
        assert wc._HEAD_CACHE == {}
        assert frozen(run(HEAD_WITNESS, FROZEN))

    def test_stalled_journalctl_head_is_unobservable_not_partial(self, tmp_path):
        """F3: a journalctl that stalls BETWEEN lines must not hang the tick."""
        fake = tmp_path / "journalctl"
        fake.write_text("#!/bin/sh\n"
                        "echo '{\"__MONOTONIC_TIMESTAMP\":\"1\",\"MESSAGE\":\"x\"}'\n"
                        "sleep 30\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        import time
        t0 = time.monotonic()
        got = wc._read_head("inv", 10**12, journalctl_path=str(fake), deadline_s=1.0)
        assert got is None
        assert time.monotonic() - t0 < 8

    def test_head_read_stops_early_on_a_live_stream(self, tmp_path):
        fake = tmp_path / "journalctl"
        lines = "".join(
            f"echo '{{\"__MONOTONIC_TIMESTAMP\":\"{i}\",\"MESSAGE\":\"m{i}\"}}'\n"
            for i in range(1, 6))
        fake.write_text("#!/bin/sh\n" + lines + "sleep 30\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        got = wc._read_head("inv", 3, journalctl_path=str(fake), deadline_s=5.0)
        assert got is not None and [e[0] for e in got] == [1, 2, 3, 4]


class TestUnobservable:
    def test_head_read_failure_is_indeterminate(self):
        res = run(None, FROZEN)
        assert res[1]["disp"] == "indeterminate" and shape(res) is None

    def test_window_read_failure_is_indeterminate(self):
        res = run(HEAD_WITNESS, None)
        assert res[1]["disp"] == "indeterminate" and shape(res) is None

    @pytest.mark.parametrize("props", [
        None, {}, dict(PROPS, InvocationID=""),
        dict(PROPS, ExecMainStartTimestampMonotonic="0"),
        dict(PROPS, ExecMainStartTimestampMonotonic="junk")])
    def test_bad_unit_props_are_indeterminate(self, props):
        res = run(HEAD_WITNESS, FROZEN, props=props)
        assert res[1]["disp"] == "indeterminate" and shape(res) is None


class TestFootprint:
    def test_head_read_once_per_invocation(self):
        heads = []

        def head_fn(inv, stop):
            heads.append(inv)
            return HEAD_GATE_CLEAN
        for _ in range(3):
            wc.probe_meshtasticd_clock_unrated(
                main_pid=1, show_fn=lambda: PROPS, head_fn=head_fn,
                window_fn=lambda inv: FROZEN, monotonic=lambda: NOW_S)
        assert heads == ["inv-1"]

    def test_new_invocation_rereads_the_head(self):
        heads = []

        def head_fn(inv, stop):
            heads.append(inv)
            return HEAD_WITNESS if inv == "inv-1" else HEAD_GATE_CLEAN
        for inv in ("inv-1", "inv-2"):
            props = dict(PROPS, InvocationID=inv)
            reset_dispositions()
            sig = wc.probe_meshtasticd_clock_unrated(
                main_pid=1, show_fn=lambda p=props: p, head_fn=head_fn,
                window_fn=lambda i: FROZEN, monotonic=lambda: NOW_S)
            res = (sig, collect_dispositions().get(wc.CLS), None)
            assert frozen(res) == (inv == "inv-1")   # restart cures it
        assert heads == ["inv-1", "inv-2"]

    def _go(self, t, windows):
        reset_dispositions()
        sig = wc.probe_meshtasticd_clock_unrated(
            main_pid=1, show_fn=lambda: PROPS, head_fn=lambda i, s: HEAD_WITNESS,
            window_fn=lambda inv: windows.append(inv) or FROZEN,
            monotonic=lambda: t[0])
        return frozen((sig, collect_dispositions().get(wc.CLS), None))

    def test_consequence_cached_then_refreshed(self):
        t, windows = [NOW_S], []
        assert self._go(t, windows)
        t[0] += 60
        assert self._go(t, windows)
        assert len(windows) == 1
        t[0] += wc.CONSEQUENCE_CACHE_S
        self._go(t, windows)
        assert len(windows) == 2

    def test_clock_going_backward_refreshes_not_trusts(self):
        """hfm #6: a negative delta must not read as 'fresh cache'."""
        t, windows = [NOW_S], []
        self._go(t, windows)
        t[0] -= 100
        self._go(t, windows)
        assert len(windows) == 2


class TestParsing:
    def test_parse_entry_shapes(self):
        assert wc._parse_entry('{"__MONOTONIC_TIMESTAMP":"5","MESSAGE":"x"}') == (5, "x")
        assert wc._parse_entry('{"__MONOTONIC_TIMESTAMP":"5","MESSAGE":[104,105]}') == (5, "hi")
        for bad in ["", "not json", '{"MESSAGE":"x"}', '{"__MONOTONIC_TIMESTAMP":"z"}']:
            assert wc._parse_entry(bad) is None

    def test_rx_regex_reads_the_live_shape(self):
        x = wc._RX_RE.search("INFO  | 17:38:01 1 [Router] Received routing from=0xddfb8065, id=0x1")
        assert x and x.group(1) == "routing" and x.group(2) == "0xddfb8065"

    def test_marks_match_the_gate_script(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        script = open(os.path.join(root, "scripts", "wait_for_ntp_sync.sh"),
                      encoding="utf-8").read()
        assert wc.WITNESS_MARK in script, (
            "the probe greps a witness the gate no longer prints (hfm #5)")
        assert wc.GATE_MARK in script
        conf = os.path.join(root, "templates", "systemd",
                            "meshtasticd.service.d", wc.GATE_DROPIN)
        assert os.path.exists(conf)


class TestWiring:
    def test_class_registered_and_inherited_app_drift_retired(self):
        from utils.watchdog_probe_core import SIGNAL_CLASSES
        assert wc.CLS in SIGNAL_CLASSES
        assert "inherited_app_drift" not in SIGNAL_CLASSES

    def test_runner_calls_the_probe(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        src = open(os.path.join(root, "src", "utils", "watchdog_runner.py"),
                   encoding="utf-8").read()
        assert "probe_meshtasticd_clock_unrated()" in src
        assert "probe_inherited_app_drift" not in src

    def test_exported_from_watchdog_probes(self):
        import utils.watchdog_probes as wp
        assert wp.probe_meshtasticd_clock_unrated is wc.probe_meshtasticd_clock_unrated
        assert not hasattr(wp, "probe_inherited_app_drift")


# ── review r2 (Fable by running, 11-09): drive the REAL readers ────────────
#
# F1 (load-bearing): `journalctl -n N -g PAT` = newest N MATCHING only from
# systemd 255; on <= 254 it greps inside the newest N RAW entries (v252/v254
# source read by the reviewer; 257 measured). moc4 runs 252 (measured 11-09),
# so the -g path would read it blind forever. F2: nothing drove these readers,
# so two mutants (reader returns [], -n dropped) survived.

FAKE_JOURNALCTL = r'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
ver = int(os.environ["FAKE_SYSTEMD"])
if args == ["--version"]:
    print(f"systemd {ver} (fake)"); sys.exit(0)
with open(os.environ["FAKE_ARGV_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_FAIL"):
    sys.stderr.write("Failed to open journal: Permission denied\n"); sys.exit(1)
rows = [json.loads(l) for l in open(os.environ["FAKE_JOURNAL"])]
pat = args[args.index("-g") + 1] if "-g" in args else None
n = int(args[args.index("-n") + 1]) if "-n" in args else None
match = (lambda r: re.search(pat, r["MESSAGE"])) if pat else (lambda r: True)
if n is None:
    out = [r for r in rows if match(r)]
elif ver >= 255:
    out = [r for r in rows if match(r)][-n:][::-1]       # newest-first, like 257
else:
    out = [r for r in rows[-n:] if match(r)]             # grep INSIDE the raw tail
if pat and not out:
    sys.exit(1)
for r in out:
    e = {"__MONOTONIC_TIMESTAMP": str(r["mono"]), "MESSAGE": r["MESSAGE"]}
    if r.get("comm") and "--output-fields=MESSAGE" not in args:
        e["_COMM"] = r["comm"]
    print(json.dumps(e))
'''


def _fake_journal(tmp_path, ver, rows, fail=False):
    exe = tmp_path / "journalctl"
    exe.write_text(FAKE_JOURNALCTL, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    data = tmp_path / "journal.jsonl"
    data.write_text("".join(
        __import__("json").dumps({"mono": r[0], "MESSAGE": r[1],
                                  "comm": r[2] if len(r) > 2 else None}) + "\n"
        for r in rows), encoding="utf-8")
    argv_log = tmp_path / "argv.log"
    env = {"FAKE_SYSTEMD": str(ver), "FAKE_JOURNAL": str(data),
           "FAKE_ARGV_LOG": str(argv_log)}
    if fail:
        env["FAKE_FAIL"] = "1"
    return str(exe), env, argv_log


def _frozen_hour_with_noisy_tail():
    """A frozen radio: telemetry 0/0 with foreign RX between, then 400 lines
    of chatter newer than the last telemetry line (the shape that blinds
    `-n 2 -g` on systemd 252)."""
    rows = [(ago(1800), tele(0)), (ago(1500), rx()), (ago(1400), rx("text msg", "0x2")),
            (ago(1000), rx("nodeinfo", "0x3")), (ago(900), tele(0))]
    rows += [(ago(800) + i * S, "INFO  | [Router] JSON publish x") for i in range(400)]
    return rows


@pytest.fixture
def fake_env(monkeypatch):
    def apply(env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        wc._JOURNALCTL_VERSION.clear()
    yield apply
    wc._JOURNALCTL_VERSION.clear()


class TestRealReadersR2:
    @pytest.mark.parametrize("ver", [252, 254, 255, 257])
    def test_frozen_radio_fires_on_every_systemd(self, tmp_path, fake_env, ver):
        exe, env, argv_log = _fake_journal(tmp_path, ver, _frozen_hour_with_noisy_tail())
        fake_env(env)
        window = wc._default_window("inv", exe)
        verdict, facts = wc.judge_consequence(window, NOW)
        assert verdict == "fire", (ver, facts)
        assert facts["online_last_two"] == [0, 0]
        calls = [__import__("json").loads(l) for l in argv_log.read_text().splitlines()]
        for argv in calls:
            assert "-n" in argv, "every window read is count-bounded (r2 mutant B)"
            if ver < wc.JOURNALCTL_NEWEST_MATCHING_MIN:
                assert "-g" not in argv, "on <= 254, -n with -g greps the raw tail"

    def test_unreadable_version_takes_the_raw_tail(self, tmp_path, fake_env):
        exe, env, argv_log = _fake_journal(tmp_path, 257, _frozen_hour_with_noisy_tail())
        fake_env(env)
        wc._JOURNALCTL_VERSION[exe] = None
        window = wc._default_window("inv", exe)
        assert wc.judge_consequence(window, NOW)[0] == "fire"
        assert all("-g" not in __import__("json").loads(l)
                   for l in argv_log.read_text().splitlines())

    def test_healthy_radio_reads_online_through_the_real_reader(self, tmp_path, fake_env):
        rows = [(ago(1800), tele(30)), (ago(1000), rx()), (ago(900), tele(33))]
        for ver in (252, 257):
            exe, env, _ = _fake_journal(tmp_path, ver, rows)
            fake_env(env)
            assert wc.judge_consequence(wc._default_window("inv", exe), NOW)[0] == "online"

    def test_reader_returns_the_lines_it_read(self, tmp_path, fake_env):
        """r2 mutant A: a reader returning [] survived the old suite."""
        exe, env, _ = _fake_journal(tmp_path, 257, [(ago(10), tele(4))])
        fake_env(env)
        got = wc._read_last("inv", "num_online_nodes=", 2, journalctl_path=exe)
        assert got == [(ago(10), tele(4))]

    def test_journalctl_error_is_unobservable_everywhere(self, tmp_path, fake_env):
        """r2 F4: an error exit must read None, never 'no lines yet'."""
        exe, env, _ = _fake_journal(tmp_path, 257, [(1, "x")], fail=True)
        fake_env(env)
        assert wc._read_last("inv", None, 5, journalctl_path=exe) is None
        assert wc._read_head("inv", 10**12, journalctl_path=exe, deadline_s=5) is None

    def test_text_msg_kind_counts_as_rx(self):
        """r2 F5: `Received text msg from=` (space in the kind) was missed."""
        x = wc._RX_RE.search(rx("text msg", "0xabc"))
        assert x and x.group(1) == "text msg" and x.group(2) == "0xabc"

    def test_telemetry_landing_during_the_read_is_not_future(self):
        """r2 F3: a line stamped seconds after `now` is ordering, not skew."""
        w = [(ago(900), tele(0)), (ago(800), rx()), (ago(700), rx("text", "0x2")),
             (NOW + 5 * S, tele(0))]
        assert wc.judge_consequence(w, NOW)[0] == "fire"


# ── review r3 (Fable by running, 11-09) ────────────────────────────────────

def _real_witness_text(tmp_path):
    """The witness AS THE GATE PRINTS IT — run the script with an unsynced
    timedatectl stub (never a hand-copied constant: r3 F1 hid behind one)."""
    import subprocess
    stub = tmp_path / "timedatectl"
    stub.write_text("#!/bin/sh\necho 'System clock synchronized: no'\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    p = subprocess.run(["sh", os.path.join(root, "scripts", "wait_for_ntp_sync.sh"), "1"],
                       capture_output=True, text=True, timeout=30,
                       env={"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)})
    lines = [ln for ln in p.stderr.splitlines() if wc.WITNESS_MARK in ln]
    assert len(lines) == 1, p.stderr
    return lines[0]


class TestReviewR3:
    def test_the_witness_is_never_a_telemetry_or_rx_line(self, tmp_path):
        w = _real_witness_text(tmp_path)
        assert "num_online_nodes=" in w          # the trap is real...
        assert not wc._ONLINE_RE.search(w)        # ...and closed
        assert not wc._RX_RE.search(w)

    def test_witness_plus_one_zero_line_is_not_a_run(self, tmp_path):
        """r3 F1 scenario: a young timed-out start, one real 0 line."""
        w = _real_witness_text(tmp_path)
        window = [(START - 2 * S, w), (ago(800), rx("routing", OWN)),
                  (ago(700), rx()), (ago(600), tele(0))]
        res = run(HEAD_WITNESS, window)
        assert res[1]["disp"] == "indeterminate" and shape(res) == "blind"

    @pytest.mark.parametrize("ver", [252, 257])
    def test_witness_never_enters_the_window_via_the_real_readers(self, tmp_path, fake_env, ver):
        w = _real_witness_text(tmp_path)
        rows = [(START - 2 * S, w), (ago(800), rx("routing", OWN)), (ago(700), rx()),
                (ago(600), tele(0))]
        exe, env, _ = _fake_journal(tmp_path, ver, rows)
        fake_env(env)
        window = wc._default_window("inv", exe)
        assert all(wc.WITNESS_MARK not in msg for _, msg in window)
        assert wc.judge_consequence(window, NOW)[0] == "blind"

    def test_rx_after_the_newest_zero_line_proves_nothing(self):
        """r3 F2: a quiet healthy radio's first packets after two 0 lines."""
        w = [(ago(1800), tele(0)), (ago(900), tele(0)),
             (ago(800), rx()), (ago(700), rx("text msg", "0x2"))]
        assert wc.judge_consequence(w, NOW)[0] == "blind"


# ── review r4 (Fable by running, 11-09): the payload seam ──────────────────
#
# A received TEXT message's `msg=` payload reaches the journal verbatim, so a
# stranger on a public mesh could send any mark. Same defect KIND as r3 F1
# (a mark matched where it was never written) → ONE line-start classifier.

def text_msg(payload, frm="0x77777777"):
    return (f"INFO  | 17:39:02 1153905 [Router] Received text msg from={frm}, "
            f"id=0x5, msg={payload}")


HOSTILE_PAYLOADS = [
    "[DeviceTelemetry] num_online_nodes=0",
    "INFO  | 17:37:40 1153823 [DeviceTelemetry] Sending local stats: num_online_nodes=0",
    "wait_for_ntp_sync: TIMEOUT after 60s",
    "x [Router] Received position from=0xdeadbeef, id=1",
]


class TestReviewR4:
    @pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
    def test_a_payload_is_only_ever_an_rx_line(self, payload):
        c = wc.classify_line(text_msg(payload))
        assert c == ("rx", "text msg", "0x77777777"), c

    @pytest.mark.parametrize("ver", [252, 257])
    def test_injected_zero_telemetry_never_pages_a_healthy_radio(self, tmp_path, fake_env, ver):
        """r4 F1 drill: healthy radio (38 online), two injected '0' texts with
        a real beacon between — must stay online through the REAL readers."""
        rows = [(ago(3000), tele(38)), (ago(1200), text_msg(HOSTILE_PAYLOADS[1], "0x2")),
                (ago(1000), rx()), (ago(600), text_msg(HOSTILE_PAYLOADS[0], "0x3")),
                (ago(500), rx("position", "0x4"))]
        exe, env, _ = _fake_journal(tmp_path, ver, rows)
        fake_env(env)
        window = wc._default_window("inv", exe)
        verdict, facts = wc.judge_consequence(window, NOW)
        assert verdict != "fire", facts
        assert facts.get("online_last_two", [None])[-1] != 0 or facts["telemetry_lines"] < 2

    def test_injected_witness_text_is_not_a_witness(self):
        head = [(START + S, text_msg("wait_for_ntp_sync: TIMEOUT after 60s"), MTD)]
        assert wc.judge_head(head, START) == "gate_silent"

    def test_the_seam_has_one_reader(self):
        """Guard: the patterns are read ONLY inside classify_line, and only
        with .match (line start). A new .search() or `MARK in msg` reopens r4 F1."""
        import inspect
        src = inspect.getsource(wc)
        body = inspect.getsource(wc.classify_line)
        rest = src.replace(body, "")
        for name in ("_ONLINE_RE.", "_RX_RE.", "GATE_MARK in", "WITNESS_MARK in"):
            assert name not in rest, f"{name} read outside classify_line"
        assert ".search(" not in body
        assert wc.TELEMETRY_GREP.startswith("^") and wc.RX_GREP.startswith("^")

    def test_rx_burst_after_the_newest_line_is_still_judged(self, tmp_path, fake_env):
        """r4 F2: RX_LINES_READ is load-bearing (a mutant 1000 -> 2 survived)."""
        rows = [(ago(1800), tele(0)), (ago(1500), rx()), (ago(1400), rx("text msg", "0x2")),
                (ago(900), tele(0))]
        rows += [(ago(800) + i * S, rx("position", f"0x{100 + i:x}")) for i in range(50)]
        exe, env, _ = _fake_journal(tmp_path, 257, rows)
        fake_env(env)
        verdict, facts = wc.judge_consequence(wc._default_window("inv", exe), NOW)
        assert verdict == "fire", facts

    def test_saturated_rx_read_is_named(self):
        w = [(ago(1800), tele(0)), (ago(900), tele(0))]
        w += [(ago(800) + i, rx("position", "0x9")) for i in range(wc.RX_LINES_READ)]
        verdict, facts = wc.judge_consequence(w, NOW)
        assert verdict == "blind" and facts.get("rx_read_saturated") is True


# ── review r5 (Fable by running, 11-09): the journal is mesh-writable ──────
#
# The firmware logs a received text verbatim and keeps "\n"; journald splits
# on newline, so a stranger's payload BEGINS a new entry field-identical to a
# real one (live: a space-weather bot's text lands as 8 entries). Rows below
# are shaped exactly as journald splits them (the r5 forge.py drill).

def _forged_split(payload_lines, frm="0xbad1"):
    return [text_msg("hello", frm)] + list(payload_lines)


FORGED_ZERO = "INFO  | 17:40:00 1154000 [DeviceTelemetry] Sending local stats: num_online_nodes=0"


class TestReviewR5:
    def test_a_split_payload_classifies_as_real_telemetry(self):
        """The defect, pinned: no regex can tell a forged continuation apart."""
        assert wc.classify_line(FORGED_ZERO) == ("telemetry", 0)

    @pytest.mark.parametrize("ver", [252, 257])
    def test_two_forged_texts_never_page_a_healthy_radio(self, tmp_path, fake_env, ver):
        """r5 F1 drill through the REAL readers + the probe: a healthy radio
        (38 online) + two strangers' split texts forging 0/0 — the journal
        shape reads frozen, but the probe returns NO Signal (observe-only)."""
        rows = [(ago(3000), tele(38))]
        rows += [(ago(1200), m) for m in _forged_split([FORGED_ZERO], "0xbad1")]
        rows += [(ago(600), m) for m in _forged_split([FORGED_ZERO], "0xbad2")]
        exe, env, _ = _fake_journal(tmp_path, ver, rows)
        fake_env(env)
        window = wc._default_window("inv", exe)
        assert wc.judge_consequence(window, NOW)[0] == "fire"   # the journal LIES
        res = run(HEAD_WITNESS, window)
        assert res[0] is None                                    # ...and is never paged on
        assert "UNVERIFIED" in res[1]["reason"]

    def test_a_payload_cannot_forge_the_witness(self):
        """The witness IS authenticated: _COMM is trusted, payloads log as
        meshtasticd, and nothing meshtasticd writes predates its own start."""
        forged = [(START + 2 * S, WITNESS, MTD), (START + S, RTC, MTD)]
        assert wc.judge_head(forged, START) == "gate_silent"
        # r6: authenticity is _COMM alone; a slightly-late stamp (journald
        # receipt racing the fork) is still the gate's own line.
        late_gate = [(START + 3 * S, WITNESS, GATE)]
        assert wc.judge_head(late_gate, START) == "witness"
        # Each defence pinned ON ITS OWN (mutant P1 survived when both forged
        # rows were also late): the right stamp with the wrong/absent _COMM.
        for comm in (MTD, "sh"):
            early_forged = [(START - 2 * S, WITNESS, comm)]
            assert wc.judge_head(early_forged, START) == "gate_silent", comm
        # r2 (Fable 5.2) revision: NO _COMM is journald's lazy read losing the
        # gate's own line, not a forgery shape (a payload logs under
        # meshtasticd's comm). It is named ``gate_unauthenticated`` — still
        # never a witness and never clean, so it grants no authority.
        assert wc.judge_head([(START - 2 * S, WITNESS, None)], START) == "gate_unauthenticated"
        assert wc.judge_head(HEAD_WITNESS, START) == "witness"

    def test_head_parser_reads_the_trusted_comm(self):
        line = '{"__MONOTONIC_TIMESTAMP":"7","MESSAGE":"wait_for_ntp_sync: x","_COMM":"wait_for_ntp_sy"}'
        assert wc._parse_head_entry(line) == (7, "wait_for_ntp_sync: x", "wait_for_ntp_sy")
        assert wc._parse_head_entry('{"__MONOTONIC_TIMESTAMP":"7","MESSAGE":"m"}') == (7, "m", None)

    def test_the_probe_module_builds_no_signal(self):
        """Observe-only is structural, not a branch: no Signal( in the module."""
        import inspect
        assert "Signal(" not in inspect.getsource(wc)


# ── review r6 (Fable by running, 11-09): pin the witness through the REAL reader

class TestReviewR6:
    def _argv(self, argv_log):
        return [__import__("json").loads(l) for l in argv_log.read_text().splitlines()]

    def test_witness_authenticates_through_the_real_head_reader(self, tmp_path, fake_env):
        """r6 M5/M6 survived: dropping _COMM, or reading the head with
        --output-fields=MESSAGE like its sibling, silently killed the witness."""
        rows = [(START - 2 * S, WITNESS, GATE), (START + S, RTC, MTD), (START + 2 * S, RTC, MTD)]
        exe, env, argv_log = _fake_journal(tmp_path, 257, rows)
        fake_env(env)
        head = wc._read_head("inv-9", START + wc.HEAD_READ_PAST_START_US,
                             journalctl_path=exe, deadline_s=5)
        assert wc.judge_head(head, START) == "witness"
        argv = self._argv(argv_log)[-1]
        assert not any(a.startswith("--output-fields") for a in argv), (
            "the head read needs _COMM — never restrict its fields")

    def test_a_forged_witness_through_the_real_reader_is_not_one(self, tmp_path, fake_env):
        rows = [(START + S, WITNESS, MTD), (START + 2 * S, RTC, MTD)]
        exe, env, _ = _fake_journal(tmp_path, 257, rows)
        fake_env(env)
        head = wc._read_head("inv-9", START + wc.HEAD_READ_PAST_START_US,
                             journalctl_path=exe, deadline_s=5)
        assert wc.judge_head(head, START) == "gate_silent"

    @pytest.mark.parametrize("ver", [252, 257])
    def test_every_read_is_scoped_to_this_invocation(self, tmp_path, fake_env, ver):
        """r6 M11/M12 survived: without the filter the head is the journal's
        OLDEST entries — an old boot's real TIMEOUT would pass as this start's."""
        rows = [(START - 2 * S, WITNESS, GATE)] + _frozen_hour_with_noisy_tail()
        exe, env, argv_log = _fake_journal(tmp_path, ver, rows)
        fake_env(env)
        wc._read_head("inv-9", START + wc.HEAD_READ_PAST_START_US, journalctl_path=exe, deadline_s=5)
        wc._default_window("inv-9", exe)
        calls = self._argv(argv_log)
        assert len(calls) >= 2
        for argv in calls:
            assert "_SYSTEMD_INVOCATION_ID=inv-9" in argv, argv


class TestWiringGuardsAgree:
    def test_cls_constant_equals_the_literal_the_guards_read(self):
        import inspect
        body = inspect.getsource(wc.probe_meshtasticd_clock_unrated)
        assert body.count('"meshtasticd_clock_unrated"') >= 8
        assert wc.CLS == "meshtasticd_clock_unrated"

    def test_declared_observe_only_in_core(self):
        """r8: pin the EXACT set — parking any class here (dodging the
        'fires into a void' gate) must be a deliberate edit of this test."""
        from utils.watchdog_probe_core import OBSERVE_ONLY_CLASSES
        assert OBSERVE_ONLY_CLASSES == {wc.CLS}


class TestShapeSeam:
    def test_every_indeterminate_assertion_names_its_shape(self):
        """The SEAM (review r1 F1 + r2 F2/F3 — the same defect kind twice):
        since r5 every consequence branch reads ``indeterminate``, so an
        assertion on the disposition alone pins no branch. Every test that
        asserts ``"indeterminate"`` must also call ``shape(`` (``is None``
        for the non-consequence branches). Plant: delete one shape() call."""
        import ast
        src = open(__file__, encoding="utf-8").read()
        bare = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_") \
                    and node.name != "test_every_indeterminate_assertion_names_its_shape":
                seg = ast.get_source_segment(src, node) or ""
                calls = any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                            and c.func.id == "shape" for c in ast.walk(node))
                # r3 F3: a CALL, not the substring (a docstring "shape(" passed)
                if '"indeterminate"' in seg and not calls:
                    bare.append(node.name)
        assert bare == [], f"assert the shape too (shape(res) == ... / is None): {bare}"


#: Every head the probe can hand compose_reason, with what the operator
#: must be told. The forged row: an unauth "synchronized" line plus a
#: FOREIGN-comm TIMEOUT (a mesh payload) — must still read synchronized.
HEAD_UNAUTH_SYNC_FORGED = HEAD_GATE_UNAUTH + [(START + 4 * S, WITNESS, MTD)]
HEAD_TABLE = [
    # (name, head entries, why fragment, timed_out)
    ("witness", HEAD_WITNESS, "the NTP gate timed out at this start", True),
    ("unauth_timeout", HEAD_WITNESS_UNAUTH, "unauthenticated (it reads TIMEOUT", True),
    ("unauth_sync", HEAD_GATE_UNAUTH, "unauthenticated (it reads synchronized", False),
    ("unauth_sync_forged", HEAD_UNAUTH_SYNC_FORGED, "unauthenticated (it reads synchronized", False),
    ("silent", HEAD_GATE_SILENT, "no NTP-gate line is readable", False),
]
SHAPE_TABLE = [("online", LEHUA), ("frozen", FROZEN), ("blind", [])]


#: Every consequence input the probe can reach, by WHAT IT IS (review r4):
#: (name, window, shape, cause fragment the reason must carry).
CONSEQUENCE_CASES = [
    ("online", LEHUA, "online", "32 then 35 node(s) online"),
    ("frozen", FROZEN, "frozen", "3 other sender(s) are heard (3 RX line(s))"),
    # senders != lines, so a swapped count cannot hide (r4 F2 mutant survived
    # on FROZEN, where both are 3)
    ("frozen_busy", [(ago(1800), tele(0)), (ago(1700), rx()), (ago(1650), rx()),
                     (ago(1600), rx("text", "0x2")), (ago(1500), rx()), (ago(900), tele(0))],
     "frozen", "2 other sender(s) are heard (4 RX line(s))"),
    ("blind_few", [], "blind", "fewer than 2 DeviceTelemetry lines this start (0 read)"),
    ("blind_stale", [(ago(3 * 3600 + 900), tele(0)), (ago(3 * 3600 + 100), rx()),
                     (ago(3 * 3600 + 50), rx("text", "0x2")), (ago(3 * 3600), tele(0))],
     "blind", "the newest telemetry line is 10800 s old (over the 2 h bound)"),
    ("blind_future", [(ago(-120 + 900), tele(0)), (ago(-120 + 100), rx()),
                      (ago(-120 + 50), rx("text", "0x2")), (ago(-120), tele(0))],
     "blind", "the newest telemetry line is stamped 120 s AHEAD of now"),
    ("blind_quiet", [(ago(1800), tele(0)), (ago(1700), rx("routing", OWN)),
                     (ago(900), tele(0))],
     "blind", "0 nodes online but only 1 other sender(s) heard"),
]
PATHS = ("fresh", "head_cached", "both_cached")


def spec(timed_out, head_name, shape_name):
    """The INDEPENDENT expectation — written from the acceptance, not from
    compose_reason: (fragments that must appear, fragments that must not)."""
    must, never = [], []
    rerate, nxt, check = wc.CURE_RERATE, wc.CURE_NEXT_START, "check the radio's own node list"
    if shape_name == "online":
        (must if timed_out else never).append(rerate)
        (never if timed_out else must).append(nxt)
        never.append(check)
    elif shape_name == "frozen":
        must += [check, rerate]
        never.append(nxt)
        if timed_out:
            must.append("LIKELY started before NTP sync")
        else:
            never.append("LIKELY")
            must.append("possibly started before NTP sync" if head_name == "silent"
                        else "cause UNKNOWN (the gate says synchronized)")
    else:
        never.append(nxt)
        if timed_out:
            must += ["the gate timed out, so", check, rerate]
        else:
            must.append("nothing points at a fault — no action")
            never += [check, rerate]
    never += ["probe bug", "None", "{"]
    return must, never


class TestReasonSpec:
    """Review r4 (Fable 5.2, 11-09): the input space ENUMERATED, including the
    path production runs on ~29 of 30 ticks (head AND consequence cached —
    r4 F1). 3 paths x 5 heads x 6 consequence cases = 90 cells, each checked
    against spec(), and every path must say the same thing."""

    @pytest.mark.parametrize("case", CONSEQUENCE_CASES, ids=[c[0] for c in CONSEQUENCE_CASES])
    @pytest.mark.parametrize("name,head,why,timed_out", HEAD_TABLE, ids=[h[0] for h in HEAD_TABLE])
    def test_cell(self, name, head, why, timed_out, case):
        _, window, shape_name, cause = case
        wc._HEAD_CACHE.clear(); wc._CONSEQUENCE_CACHE.clear()
        reasons = {}
        for path in PATHS:
            if path == "head_cached":
                wc._CONSEQUENCE_CACHE.clear()
            res = run(head, window)
            disp, calls = res[1], res[2]
            where = (name, case[0], path)
            assert disp["disp"] == "indeterminate", where
            assert shape(res) == shape_name, where
            if path == "both_cached":
                assert calls["head"] == 0 and calls["window"] == 0, where
            elif path == "head_cached":
                assert calls["head"] == 0 and calls["window"] == 1, where
            reason = disp["reason"]
            assert why in reason and cause in reason, (where, reason)
            for other_why in {w for _, _, w, _ in HEAD_TABLE} - {why}:
                assert other_why not in reason, (where, other_why)
            must, never = spec(timed_out, name if name == "silent" else "x", shape_name)
            for frag in must:
                assert frag in reason, (where, frag, reason)
            for frag in never:
                assert frag not in reason, (where, frag, reason)
            reasons[path] = reason
        assert len(set(reasons.values())) == 1, reasons

    @pytest.mark.parametrize("args", [
        ("gate_clean", None, "blind", {}), ("gate_forged", None, "online", {}),
        ("witness", None, "bogus", {}), ("gate_unauthenticated", None, "online",
                                         {"online_last_two": [1, 2]})])
    def test_outside_the_closed_enums_is_a_probe_bug(self, args):
        """r4 F5 / hfm #7: never the gate-silent text, never a frozen page."""
        out = wc.compose_reason(*args)
        assert out.startswith("probe bug:")
        for mark in (wc.FROZEN_MARK, wc.ONLINE_MARK, wc.BLIND_MARK, "no NTP-gate line"):
            assert mark not in out
