"""The peer RF witness — an RF answer for the boxes no claw can hear.

Born 2026-07-30. The claws listen on LONG_FAST/ch20; moc2 and moc3 run
SHORT_TURBO/ch8, so no claw can demodulate them and the pair carrying the RNS
leg had NO RF witness at all — the exact blind spot the ears exist to close,
left open on the boxes that matter most for RNS.

These tests pin the direction the probe must never fail in: a broken observation
channel, an unknown listening window, or a clock that went backwards must all
read as UNOBSERVABLE, never as silence. Manufacturing silence out of blindness
is the defect this whole field exists to prevent.
"""

import json

import pytest

from mini_dudeai.claw_rf_watch import DEFAULT_EXPECTED_TX_INTERVAL_S, DEFAULT_SILENCE_MULTIPLE
from utils.watchdog_probes_peer_rf import (
    JOURNAL_WINDOW_S,
    build_watched,
    load_peer_config,
    probe_segment_peer_silent,
    scan_journal_for_peers,
)

MOC2 = "!ddfb8065"
MOC3 = "!ebfa1b11"
ST = "meshtastic:SHORT_TURBO/ch8"

WINDOW = DEFAULT_EXPECTED_TX_INTERVAL_S * DEFAULT_SILENCE_MULTIPLE  # 9 h
LONG_UPTIME = WINDOW * 2
NOW = 1_785_000_000.0


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "rf_segment_peers.json"
    p.write_text(json.dumps({"segment": ST, "peers": {MOC3: "moc3"}}))
    return str(p)


def _run(cfg_path, tmp_path, *, seen=(), err=None, uptime=LONG_UPTIME,
         now=NOW, ticks=2, windows=None):
    """Drive the probe with a fake journal + uptime; debounce satisfied by default.

    ``now`` advances past the silence window on later ticks so the observation
    gate (which is real, see TestColdStart) does not mask what a test is aiming
    at. ``windows`` collects the scan width each tick when a test cares.
    """
    def scan(_peers, window=None):
        if windows is not None:
            windows.append(window)
        return (None, err) if err else (set(seen), None)
    sig = None
    for i in range(ticks):
        sig = probe_segment_peer_silent(
            config_path=cfg_path, state_path=str(tmp_path / "state.json"),
            now=now + i * (WINDOW if i else 0), _scan_fn=scan,
            _uptime_fn=lambda _n: uptime)
    return sig


class TestInertAndLoud:

    def test_no_config_is_inert(self, tmp_path):
        assert _run(str(tmp_path / "absent.json"), tmp_path) is None

    def test_unreadable_config_is_loud_not_empty(self, tmp_path):
        p = tmp_path / "rf_segment_peers.json"
        p.write_text("{not json")
        peers, seg, err = load_peer_config(str(p))
        assert peers is None and err and "unreadable" in err

    def test_empty_peer_set_is_an_error_not_nothing_to_watch(self, tmp_path):
        p = tmp_path / "rf_segment_peers.json"
        p.write_text(json.dumps({"segment": ST, "peers": {}}))
        peers, seg, err = load_peer_config(str(p))
        assert peers is None
        assert err and "empty peer set" in err, (
            "an empty ruleset that reads as 'nothing to watch' is the fail-dark shape")

    def test_malformed_node_id_is_rejected(self, tmp_path):
        p = tmp_path / "rf_segment_peers.json"
        p.write_text(json.dumps({"peers": {"moc3": "moc3"}}))
        peers, seg, err = load_peer_config(str(p))
        assert peers is None and err and "node id" in err

    def test_all_zero_peer_id_is_rejected(self, tmp_path):
        """!00000000 strips to needle '0' and matches meshtasticd's
        `from=0x0` local-inject lines — a declared all-zero peer would read
        as continuously heard, forever (re-review 2026-07-31)."""
        p = tmp_path / "rf_segment_peers.json"
        p.write_text(json.dumps({"peers": {"!00000000": "ghost"}}))
        peers, _seg, err = load_peer_config(str(p))
        assert peers is None
        assert err and "all-zero" in err


class TestBlindnessNeverBecomesSilence:

    def test_journal_read_failure_does_not_fire(self, cfg, tmp_path):
        assert _run(cfg, tmp_path, err="journal read failed (boom)") is None

    def test_unknown_uptime_does_not_fire(self, cfg, tmp_path):
        assert _run(cfg, tmp_path, uptime=None) is None

    def test_short_uptime_does_not_fire(self, cfg, tmp_path):
        assert _run(cfg, tmp_path, uptime=60.0) is None

    def test_clock_went_backward_reads_unobservable(self):
        """The backward-clock node must land in the BLINDNESS shape.

        This test used to pin ``never is True`` — the broken mapping: never +
        an elapsed window is a QUALIFIED SILENT in classify_watch, so an NTP
        backstep on these RTC-less boxes manufactured a false silence page
        (review 2026-07-31, finding 5). ``parse_error`` is the shape the gate
        reads as unobservable."""
        state = {"last_heard_ts": {MOC3: NOW + 5000}}
        w = build_watched({MOC3: "moc3"}, set(), NOW, state)
        assert w[MOC3]["age_s"] is None
        assert w[MOC3]["parse_error"] is True
        assert w[MOC3]["never"] is False

    def test_clock_backward_does_not_fire_even_past_the_window(self, cfg, tmp_path):
        """End-to-end: a future-stamped last-heard + a fully qualified
        listening window must abstain, not page — the layer the old unit test
        never exercised (it pinned build_watched's fields, not the verdict)."""
        sp = tmp_path / "state.json"
        sp.write_text(json.dumps({
            "observing_since": NOW - WINDOW * 2,
            "seed_window_s": WINDOW,
            # Far enough forward that the stamp stays in the future on every
            # tick _run takes, so the backward-clock branch holds throughout.
            "last_heard_ts": {MOC3: NOW + WINDOW * 10},
        }))
        assert _run(cfg, tmp_path) is None


class TestTheRealAnswers:

    def test_peer_heard_is_clean(self, cfg, tmp_path):
        assert _run(cfg, tmp_path, seen=[MOC3]) is None

    def test_peer_silent_past_the_window_fires(self, cfg, tmp_path):
        sig = _run(cfg, tmp_path, seen=[])
        assert sig is not None
        assert sig.cls == "segment_peer_silent"
        assert sig.severity == "degraded"
        assert MOC3 in sig.detail and "moc3" in sig.detail

    def test_one_tick_is_debounced(self, cfg, tmp_path):
        assert _run(cfg, tmp_path, seen=[], ticks=1) is None

    def test_hearing_it_again_clears(self, cfg, tmp_path):
        assert _run(cfg, tmp_path, seen=[]) is not None
        assert _run(cfg, tmp_path, seen=[MOC3], ticks=1) is None


class TestIncrementalFold:
    """The scan is a SHORT window per tick; the last-heard must persist across
    ticks or every quiet 30 min would read as total silence."""

    def test_sighting_persists_into_later_ticks(self):
        state = {}
        build_watched({MOC3: "moc3"}, {MOC3}, NOW, state)
        assert state["last_heard_ts"][MOC3] == NOW
        # a later tick that saw nothing must still know when we last heard it
        w = build_watched({MOC3: "moc3"}, set(), NOW + 600, state)
        assert w[MOC3]["age_s"] == pytest.approx(600)
        assert w[MOC3]["never"] is False

    def test_never_heard_stays_never(self):
        w = build_watched({MOC3: "moc3"}, set(), NOW, {})
        assert w[MOC3]["never"] is True and w[MOC3]["age_s"] is None


class TestGateIsNotDuplicated:

    def test_verdicts_come_from_claw_rf_watch(self, cfg, tmp_path, monkeypatch):
        """The threshold must live in ONE place. If classify_watch is
        unreachable the probe must ABSTAIN, not fall back to its own rule."""
        import builtins
        real = builtins.__import__

        def blocked(name, *a, **k):
            if name == "mini_dudeai.claw_rf_watch":
                raise ImportError("blocked")
            return real(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", blocked)
        assert _run(cfg, tmp_path, seen=[]) is None


class TestWiring:

    def test_signal_class_registered_and_reachable(self):
        from utils.watchdog_probes import SIGNAL_CLASSES, probe_segment_peer_silent as p
        assert "segment_peer_silent" in SIGNAL_CLASSES
        assert callable(p)


class TestTheDefaultPathResolves:
    """The resolution production actually uses — and the one every other test
    here bypassed by passing config_path explicitly.

    Shipped 2026-07-30 without a home fallback: `_config_path(None)` returned
    None, so the probe reported "no RF segment peers declared" on the two boxes
    whose configs had just been placed and validated. Reader and writer both
    shipped and never met. All 16 tests were green. Only running the real probe
    on the real box found it — so this class exists to make the suite able to.
    """

    def test_config_path_resolves_without_an_explicit_home(self, monkeypatch):
        import utils.watchdog_probes_peer_rf as m
        monkeypatch.setattr(m, "_operator_home", lambda: "/home/someone")
        assert m._config_path() == (
            "/home/someone/.config/meshforge/rf_segment_peers.json"), (
            "the default must resolve, or the probe is INERT on every box that "
            "ever configures it")

    def test_explicit_home_still_wins(self, monkeypatch):
        import utils.watchdog_probes_peer_rf as m
        monkeypatch.setattr(m, "_operator_home", lambda: "/home/someone")
        assert m._config_path("/other").startswith("/other/")

    def test_unresolvable_home_degrades_to_inert_not_a_crash(self, monkeypatch):
        import utils.watchdog_probes_peer_rf as m
        monkeypatch.setattr(m, "_operator_home", lambda: None)
        assert m._config_path() is None

    def test_probe_finds_a_config_placed_in_the_operator_home(self, tmp_path, monkeypatch):
        """End to end through the DEFAULT path: a config where the operator
        writes it must be found with no arguments at all."""
        import utils.watchdog_probes_peer_rf as m
        cfgdir = tmp_path / ".config" / "meshforge"
        cfgdir.mkdir(parents=True)
        (cfgdir / "rf_segment_peers.json").write_text(
            json.dumps({"segment": ST, "peers": {MOC3: "moc3"}}))
        monkeypatch.setattr(m, "_operator_home", lambda: str(tmp_path))

        sig = m.probe_segment_peer_silent(
            state_path=str(tmp_path / "state.json"), now=NOW,
            _scan_fn=lambda _p, _w=None: (set(), None),
            _uptime_fn=lambda _n: LONG_UPTIME)
        # Not the INERT path: the peer was found, judged, and (first tick)
        # debounced. Inert would mean the config was never seen at all.
        assert m.probe_segment_peer_silent(
            state_path=str(tmp_path / "state.json"), now=NOW,
            _scan_fn=lambda _p, _w=None: (set(), None),
            _uptime_fn=lambda _n: LONG_UPTIME) is not None, (
            "a config in the operator home must be found via the default path")


class TestColdStart:
    """The first tick knows only what it scanned — not what the daemon heard.

    Found LIVE on moc3, 2026-07-30, minutes after deploy: the probe produced a
    silent CANDIDATE for a peer moc3 had received 9 times in the previous 6 h.
    An empty persisted history plus a 30 min incremental window reads as "never
    heard", and the meshtasticd uptime gate cannot catch it because that clock
    measures the DAEMON's listening, not the PROBE's. It is the claw field's
    "up for ten seconds" defect wearing a different clock.
    """

    def test_first_tick_seeds_from_the_full_window_not_the_incremental_one(self, cfg, tmp_path):
        windows = []
        _run(cfg, tmp_path, seen=[], ticks=1, windows=windows)
        assert windows == [int(WINDOW)], (
            "a cold 30 min scan says 'never heard' about a radio that beacons "
            "every ~40 min — seed from the full silence window once")

    def test_later_ticks_go_incremental(self, cfg, tmp_path):
        from utils.watchdog_probes_peer_rf import JOURNAL_WINDOW_S
        windows = []

        def scan(_peers, window=None):
            windows.append(window)
            return set(), None
        sp = str(tmp_path / "state.json")
        # CONTINUOUS ticking (60 s apart). A 9 h jump between ticks is an
        # unscanned GAP, which is now scanned (TestCoverageContinuity).
        for i in range(2):
            probe_segment_peer_silent(config_path=cfg, state_path=sp,
                                      now=NOW + i * 60, _scan_fn=scan,
                                      _uptime_fn=lambda _n: LONG_UPTIME)
        assert windows[0] == int(WINDOW)
        assert windows[1] == JOURNAL_WINDOW_S, (
            "re-reading the full window every tick is not a rounding error on a Pi")

    def test_coverage_only_grows_across_ticks(self, tmp_path, cfg):
        """Tick 2 must never claim LESS coverage than tick 1.

        The seed width is remembered in state rather than recomputed from the
        current tick's window. Computing it from the CURRENT (30 min) window
        made tick 2 claim 30 min where tick 1 had claimed 9 h, bouncing a
        qualified verdict back to `unobservable` — a detector that forgets what
        it already observed."""
        def scan(_peers, window=None):
            return set(), None
        sp = str(tmp_path / "s.json")
        first = probe_segment_peer_silent(
            config_path=cfg, state_path=sp, now=NOW, _scan_fn=scan,
            _uptime_fn=lambda _n: LONG_UPTIME)
        second = probe_segment_peer_silent(
            config_path=cfg, state_path=sp, now=NOW + 30, _scan_fn=scan,
            _uptime_fn=lambda _n: LONG_UPTIME)
        assert first is None, "first tick is debounced"
        assert second is not None, (
            "the 9 h seed scan already covered the window; tick 2 must not "
            "regress to unobservable")

    def test_a_young_daemon_still_cannot_claim_silence(self, cfg, tmp_path):
        """The seed scan can only see what the daemon actually received, so the
        window stays bounded by meshtasticd's uptime."""
        def scan(_peers, window=None):
            return set(), None
        sig = None
        for i in range(3):
            sig = probe_segment_peer_silent(
                config_path=cfg, state_path=str(tmp_path / "s.json"),
                now=NOW + i * WINDOW, _scan_fn=scan,
                _uptime_fn=lambda _n: 600.0)
        assert sig is None

    def test_unknown_daemon_uptime_still_abstains(self, cfg, tmp_path):
        """Our own observation age must never substitute for the daemon's: a box
        with no running receiver would otherwise claim a qualified silence."""
        def scan(_peers, window=None):
            return set(), None
        sig = None
        for i in range(3):
            sig = probe_segment_peer_silent(
                config_path=cfg, state_path=str(tmp_path / "s.json"),
                now=NOW + i * WINDOW, _scan_fn=scan, _uptime_fn=lambda _n: None)
        assert sig is None


class TestJournalNeedle:
    """The match against meshtasticd's ``from=0x…`` originator field.

    The daemon logs the node number %x-UNPADDED (live journal shows
    ``from=0x2ecc800``; diag24h_parser zfill(8)s what it reads), while node
    ids are 8-hex zero-padded. The old padded needle could NEVER match a peer
    whose id starts with a zero nibble — a permanent false silence page for a
    radio being received continuously (review 2026-07-31, finding 4). These
    run the real scanner against fake journal text; every prior test mocked
    ``_scan_fn``, so real matching was unpinned.
    """

    def _scan(self, text, peers):
        class R:
            returncode = 0
            stdout = text
        return scan_journal_for_peers(peers, 60, _runner=lambda *a, **k: R())

    def test_leading_zero_id_matches_the_unpadded_journal(self):
        seen, err = self._scan(
            "784349 [Router] Received text msg from=0x2ecc800, id=0x1\n",
            ["!02ecc800"])
        assert err is None
        assert seen == {"!02ecc800"}

    def test_full_8hex_id_still_matches(self):
        seen, err = self._scan(
            "784349 [Router] Received telemetry from=0xe213a228, id=0x2\n",
            ["!e213a228"])
        assert err is None
        assert seen == {"!e213a228"}

    def test_stripped_needle_cannot_prefix_match_a_longer_originator(self):
        """``!0abc1234`` strips to ``abc1234`` — it must not read
        ``from=0xabc12345`` (a different node) as a sighting."""
        seen, err = self._scan(
            "784349 [Router] Received position from=0xabc12345, id=0x3\n",
            ["!0abc1234"])
        assert err is None
        assert seen == set()

    def test_case_is_normalised_both_sides(self):
        seen, err = self._scan(
            "784349 [Router] Received text msg from=0xAbC1234, id=0x4\n",
            ["!0ABC1234"])
        assert err is None
        assert seen == {"!0ABC1234"}


class TestStateWriteWitness:
    """An unwritable state path must be WITNESSED, and must not turn every
    tick into a fresh multi-hour seed scan (review 2026-07-31, finding 6).

    The failure is forced by parenting the state path under a regular FILE —
    ``makedirs`` raises for any uid, so the test's verdict does not invert
    when the suite runs as root (the chmod trap)."""

    def _blocked_path(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("a file where a directory must go\n")
        return str(blocker / "state.json")

    def test_witnessed_and_no_reseed(self, cfg, tmp_path):
        sp = self._blocked_path(tmp_path)
        windows = []

        def scan(_peers, window=None):
            windows.append(window)
            return set(), None

        sig = None
        for i in range(2):
            sig = probe_segment_peer_silent(
                config_path=cfg, state_path=sp,
                now=NOW + i * 60, _scan_fn=scan,
                _uptime_fn=lambda _n: LONG_UPTIME)
        # Tick 2 must be INCREMENTAL: the in-process state copy makes memory,
        # not disk, the record — the old swallow re-seeded the full window
        # every 30 s on the box least able to afford it.
        assert len(windows) == 2
        assert windows[1] == JOURNAL_WINDOW_S, windows
        # And the write failure is stated on what the probe emits, not
        # swallowed: this run ends in a fired signal, whose detail carries it.
        assert sig is not None
        assert "state unwritable" in sig.detail


class TestHeardOnceNeverAgain:
    """2026-11-10 (twin of the claw case): a peer sighted once, then gone,
    carried a growing ``age_s`` that read ``heard`` forever — a peer that went
    mute after one sighting could never fire."""

    def test_peer_heard_once_then_gone_past_the_window_fires(self, cfg, tmp_path):
        sp = str(tmp_path / "state.json")
        def scan_seen(_p, window=None):
            return ({MOC3}, None)
        def scan_none(_p, window=None):
            return (set(), None)
        up = lambda _n: LONG_UPTIME * 4
        probe_segment_peer_silent(config_path=cfg, state_path=sp, now=NOW,
                                  _scan_fn=scan_seen, _uptime_fn=up)
        sig = None
        for i in (1, 2, 3):
            sig = probe_segment_peer_silent(
                config_path=cfg, state_path=sp, now=NOW + WINDOW * i + 60,
                _scan_fn=scan_none, _uptime_fn=up)
        assert sig is not None and sig.cls == "segment_peer_silent"


class TestCoverageContinuity:
    """Review 2026-11-10 finding 1: with a stale heard age now SILENT,
    observed_for must be listening actually DONE. A tick gap is scanned (the
    journal persists); a wall-clock step cannot be scanned and re-earns the
    window from zero."""

    def _tick(self, cfg, sp, now, seen=(), mono=None, windows=None):
        def scan(_p, window=None):
            if windows is not None:
                windows.append(window)
            return set(seen), None
        kw = {}
        if mono is not None:
            kw["_mono_fn"] = lambda: mono
        return probe_segment_peer_silent(config_path=cfg, state_path=sp, now=now,
                                         _scan_fn=scan,
                                         _uptime_fn=lambda _n: LONG_UPTIME * 4, **kw)

    def test_a_tick_gap_is_scanned_not_skipped(self, cfg, tmp_path):
        sp, w = str(tmp_path / "s.json"), []
        self._tick(cfg, sp, NOW, seen=[MOC3], windows=w)
        self._tick(cfg, sp, NOW + 7200, windows=w)
        assert w[1] == 7200 + 60, w

    def test_runner_outage_while_the_peer_beacons_does_not_fire(self, cfg, tmp_path):
        """10 h outage; the peer kept beaconing — the gap scan finds it."""
        sp = str(tmp_path / "s.json")
        self._tick(cfg, sp, NOW, seen=[MOC3])
        sig = None
        for i in range(3):
            sig = self._tick(cfg, sp, NOW + 36000 + i * 60, seen=[MOC3] if i == 0 else [])
        assert sig is None

    def test_forward_clock_step_re_earns_the_window(self, cfg, tmp_path):
        """RTC-less Pi, NTP steps +3 d: the heard stamp looks 3 d old, but only
        60 s of real listening happened. Must not fire."""
        sp = str(tmp_path / "s.json")
        self._tick(cfg, sp, NOW, seen=[MOC3], mono=1000.0)
        sig = None
        for i in range(4):
            sig = self._tick(cfg, sp, NOW + 3 * 86400 + i * 60, mono=1060.0 + i * 60)
        assert sig is None

    def test_backward_clock_re_earns_the_window(self, cfg, tmp_path):
        sp = str(tmp_path / "s.json")
        self._tick(cfg, sp, NOW, seen=[MOC3])
        sig = None
        for i in range(4):
            sig = self._tick(cfg, sp, NOW - 3600 + i * 60)
        assert sig is None

    def test_continuous_listening_past_the_window_still_fires(self, cfg, tmp_path):
        """The cure must not blind the gate: heard once, then 9 h+ of
        CONTINUOUS ticks with nothing -> silent."""
        sp = str(tmp_path / "s.json")
        self._tick(cfg, sp, NOW, seen=[MOC3], mono=0.0)
        sig = None
        step = 1500
        n = int(WINDOW // step) + 3
        for i in range(1, n):
            sig = self._tick(cfg, sp, NOW + i * step, mono=float(i * step))
        assert sig is not None and sig.cls == "segment_peer_silent"


class TestCoverageSeam:
    """Round 2 (Fable, by running): coverage changes are committed ONLY after a
    successful scan, and each branch is pinned by its own state, not by a
    verdict another layer could mask (mutants M2/M4/M7/M8 survived round 1)."""

    def _tick(self, cfg, sp, now, *, seen=(), err=None, mono=None, windows=None):
        def scan(_p, window=None):
            if windows is not None:
                windows.append(window)
            return (None, err) if err else (set(seen), None)
        kw = {"_mono_fn": (lambda: mono)} if mono is not None else {}
        return probe_segment_peer_silent(config_path=cfg, state_path=sp, now=now,
                                         _scan_fn=scan,
                                         _uptime_fn=lambda _n: LONG_UPTIME * 4, **kw)

    def _state(self, sp):
        with open(sp) as f:
            return json.load(f)

    def test_failed_scans_are_not_credited_as_listening(self, cfg, tmp_path):
        """Fable scenario B: continuous 30 s ticks, the journal read fails for
        2 h, the peer beacons inside that span. Recovery must SCAN the failed
        span, and the beaconing peer must not read silent."""
        sp, w = str(tmp_path / "s.json"), []
        t0 = NOW
        self._tick(cfg, sp, t0, seen=[MOC3], mono=0.0)
        t = t0 + 8 * 3600                      # quiet but healthy 8 h, ticking
        for i in range(1, int(8 * 3600 // 1500)):
            self._tick(cfg, sp, t0 + i * 1500, mono=float(i * 1500))
        last_ok = t0 + (int(8 * 3600 // 1500) - 1) * 1500
        for k in range(1, 240):                # 2 h of failing scans
            self._tick(cfg, sp, last_ok + k * 30, err="journalctl timed out",
                       mono=float(last_ok - t0 + k * 30))
        rec = last_ok + 240 * 30
        sig = self._tick(cfg, sp, rec, seen=[MOC3], mono=float(rec - t0), windows=w)
        assert w[-1] >= 240 * 30, "the failed 2 h must be re-scanned: %r" % w[-1:]
        assert sig is None

    def test_a_failed_scan_commits_no_coverage(self, cfg, tmp_path):
        sp = str(tmp_path / "s.json")
        self._tick(cfg, sp, NOW, seen=[MOC3])
        before = self._state(sp)
        self._tick(cfg, sp, NOW + 20 * 3600, err="boom")
        after = self._state(sp)
        for k in ("last_tick_wall", "observing_since", "seed_window_s"):
            assert after.get(k) == before.get(k), k

    def test_gap_wider_than_the_window_reseeds_and_is_capped(self, cfg, tmp_path):
        sp, w = str(tmp_path / "s.json"), []
        self._tick(cfg, sp, NOW, seen=[MOC3])
        self._tick(cfg, sp, NOW + 20 * 3600, windows=w)
        st = self._state(sp)
        assert w == [int(WINDOW)], "scan capped at the silence window"
        assert st["observing_since"] == NOW + 20 * 3600
        assert st["seed_window_s"] == int(WINDOW)

    def test_backward_wall_clock_resets_coverage_itself(self, cfg, tmp_path):
        sp = str(tmp_path / "s.json")
        self._tick(cfg, sp, NOW, seen=[MOC3])
        self._tick(cfg, sp, NOW - 3600)
        st = self._state(sp)
        assert st["seed_window_s"] == 0.0 and st["observing_since"] == NOW - 3600

    def test_reboot_monotonic_reset_is_a_gap_not_a_step(self, cfg, tmp_path):
        """mono below last_mono = a reboot; the 40 min wall gap is SCANNED."""
        sp, w = str(tmp_path / "s.json"), []
        self._tick(cfg, sp, NOW, seen=[MOC3], mono=500000.0)
        self._tick(cfg, sp, NOW + 2400, mono=12.0, windows=w)
        assert w == [2400 + 60]
        assert self._state(sp)["seed_window_s"] != 0.0

    def test_step_note_reports_the_step_not_the_gap(self, cfg, tmp_path):
        """3 h outage carrying a +1 h step must say +3600, not +14400."""
        sp = str(tmp_path / "s.json")
        self._tick(cfg, sp, NOW, seen=[MOC3], mono=0.0)
        from utils.watchdog_probe_core import collect_dispositions, reset_dispositions
        reset_dispositions()
        self._tick(cfg, sp, NOW + 4 * 3600, mono=3 * 3600.0)
        reason = collect_dispositions()["segment_peer_silent"]["reason"]
        assert "+3600s" in reason, reason

    def test_heard_then_stopped_signal_names_the_id_check(self, cfg, tmp_path):
        sp = str(tmp_path / "s.json")
        self._tick(cfg, sp, NOW, seen=[MOC3], mono=0.0)
        sig = None
        for i in range(1, int(WINDOW // 1500) + 3):
            sig = self._tick(cfg, sp, NOW + i * 1500, mono=float(i * 1500))
        assert sig is not None
        assert "WAS heard" in sig.detail and "node id changed" in sig.detail


class TestWritePointIsAtomic:
    """Round 3 (Fable, scenario J): an exception AFTER a successful scan but
    before the save must commit nothing — the span stays a gap, its sightings
    are re-scanned, and a beaconing peer does not read silent."""

    def test_exception_between_scan_and_save_credits_nothing(self, cfg, tmp_path):
        sp, w = str(tmp_path / "s.json"), []
        def scan(_p, window=None):
            w.append(window)
            return ({MOC3} if window and window > 1800 else set()), None
        up_ok = lambda _n: LONG_UPTIME * 4
        def up_boom(_n):
            raise RuntimeError("uptime read blew up")
        t0 = NOW
        probe_segment_peer_silent(config_path=cfg, state_path=sp, now=t0,
                                  _scan_fn=lambda _p, window=None: ({MOC3}, None),
                                  _uptime_fn=up_ok, _mono_fn=lambda: 0.0)
        t1 = t0 + 8 * 3600
        for i in range(1, int(8 * 3600 // 1500) + 1):
            probe_segment_peer_silent(config_path=cfg, state_path=sp, now=t0 + i * 1500,
                                      _scan_fn=lambda _p, window=None: (set(), None),
                                      _uptime_fn=up_ok, _mono_fn=lambda i=i: float(i * 1500))
        last = t0 + int(8 * 3600 // 1500) * 1500
        stall = last + 7200                     # 2 h stall; peer beaconed inside it
        probe_segment_peer_silent(config_path=cfg, state_path=sp, now=stall,
                                  _scan_fn=scan, _uptime_fn=up_boom,
                                  _mono_fn=lambda: float(stall - t0))
        sig = None
        for k in range(1, 4):
            sig = probe_segment_peer_silent(config_path=cfg, state_path=sp,
                                            now=stall + k * 30, _scan_fn=scan,
                                            _uptime_fn=up_ok,
                                            _mono_fn=lambda k=k: float(stall - t0 + k * 30))
        assert w[1] > 1800, "the stalled span must be re-scanned: %r" % w
        assert sig is None

    def test_step_tick_judges_with_the_pending_coverage_not_the_old(self, cfg, tmp_path):
        """Round 4 (Fable N9): observed_for must read the coverage being
        committed THIS tick. With debounce 1 there is no second tick to absorb
        a one-tick error: a +3 d step must not fire off the OLD coverage."""
        sp = str(tmp_path / "s.json")
        up = lambda _n: LONG_UPTIME * 40
        probe_segment_peer_silent(config_path=cfg, state_path=sp, now=NOW,
                                  _scan_fn=lambda _p, window=None: ({MOC3}, None),
                                  _uptime_fn=up, _mono_fn=lambda: 0.0, debounce_ticks=1)
        for i in range(1, int(8 * 3600 // 1500)):
            probe_segment_peer_silent(config_path=cfg, state_path=sp, now=NOW + i * 1500,
                                      _scan_fn=lambda _p, window=None: (set(), None),
                                      _uptime_fn=up, _mono_fn=lambda i=i: float(i * 1500),
                                      debounce_ticks=1)
        last = int(8 * 3600 // 1500) - 1
        sig = probe_segment_peer_silent(
            config_path=cfg, state_path=sp, now=NOW + 3 * 86400,
            _scan_fn=lambda _p, window=None: (set(), None), _uptime_fn=up,
            _mono_fn=lambda: float(last * 1500 + 30), debounce_ticks=1)
        assert sig is None
