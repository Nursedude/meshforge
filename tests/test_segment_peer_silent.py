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
    build_watched,
    load_peer_config,
    probe_segment_peer_silent,
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
         now=NOW, ticks=2):
    """Drive the probe with a fake journal + uptime; debounce satisfied by default."""
    def scan(_peers):
        return (None, err) if err else (set(seen), None)
    sig = None
    for _ in range(ticks):
        sig = probe_segment_peer_silent(
            config_path=cfg_path, state_path=str(tmp_path / "state.json"),
            now=now, _scan_fn=scan, _uptime_fn=lambda _n: uptime)
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


class TestBlindnessNeverBecomesSilence:

    def test_journal_read_failure_does_not_fire(self, cfg, tmp_path):
        assert _run(cfg, tmp_path, err="journal read failed (boom)") is None

    def test_unknown_uptime_does_not_fire(self, cfg, tmp_path):
        assert _run(cfg, tmp_path, uptime=None) is None

    def test_short_uptime_does_not_fire(self, cfg, tmp_path):
        assert _run(cfg, tmp_path, uptime=60.0) is None

    def test_clock_went_backward_reads_unobservable(self):
        state = {"last_heard_ts": {MOC3: NOW + 5000}}
        w = build_watched({MOC3: "moc3"}, set(), NOW, state)
        assert w[MOC3]["age_s"] is None and w[MOC3]["never"] is True


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
            _scan_fn=lambda _p: (set(), None),
            _uptime_fn=lambda _n: LONG_UPTIME)
        # Not the INERT path: the peer was found, judged, and (first tick)
        # debounced. Inert would mean the config was never seen at all.
        assert m.probe_segment_peer_silent(
            state_path=str(tmp_path / "state.json"), now=NOW,
            _scan_fn=lambda _p: (set(), None),
            _uptime_fn=lambda _n: LONG_UPTIME) is not None, (
            "a config in the operator home must be found via the default path")
