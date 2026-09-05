"""Tests for scripts/bot_deaf_check.py — the ALIVE-but-DEAF probe.

The whole value of this probe is that it refuses to page on a quiet mesh, so
the tests that matter are the ones pinning the difference between "heard
nothing because nothing was sent" and "heard nothing because the feed died".
"""
import importlib.util
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

_spec = importlib.util.spec_from_file_location(
    "bot_deaf_check", os.path.join(_ROOT, "scripts", "bot_deaf_check.py"))
bdc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bdc)

THRESH = 600


# A long-running bot, so the restart leg is out of the way unless a test is
# specifically about it. Anything under THRESH can never be called deaf.
LONG_UPTIME = 86400.0


def _decide(unit=("ok", "mesh_bot active"), age=("ok", 60.0), corr=("ok", 5),
            up=LONG_UPTIME):
    return bdc.decide(unit[0], unit[1], age[0], age[1], corr[0], corr[1],
                      THRESH, unit_up_s=up)


class TestHealthyPaths:
    def test_recent_reception_is_clean(self):
        verdict, detail = _decide(age=("ok", 60.0))
        assert verdict == bdc.CLEAN
        assert "60s ago" in detail

    def test_reception_exactly_at_threshold_is_clean(self):
        verdict, _ = _decide(age=("ok", float(THRESH)))
        assert verdict == bdc.CLEAN


class TestQuietMeshIsNotDeafness:
    """The reason this probe can be trusted at 3am."""

    def test_silent_bot_with_no_corroborating_traffic_is_clean(self):
        verdict, detail = _decide(age=("ok", None), corr=("ok", 0))
        assert verdict == bdc.CLEAN
        assert "quiet mesh" in detail

    def test_stale_bot_with_no_corroborating_traffic_is_clean(self):
        verdict, detail = _decide(age=("ok", 9999.0), corr=("ok", 0))
        assert verdict == bdc.CLEAN
        assert "quiet mesh" in detail


class TestDeafness:
    def test_silent_bot_with_corroborating_traffic_is_deaf(self):
        verdict, detail = _decide(age=("ok", None), corr=("ok", 12))
        assert verdict == bdc.DEAF
        assert "12 message(s)" in detail

    def test_stale_bot_with_corroborating_traffic_is_deaf(self):
        verdict, detail = _decide(age=("ok", 9999.0), corr=("ok", 3))
        assert verdict == bdc.DEAF
        assert "9999s ago" in detail

    def test_the_incident_this_probe_was_written_for(self):
        """2026-09-04: bot active, 'Autoresponder Started', received nothing
        for 12 min while commands were being sent."""
        verdict, _ = _decide(age=("ok", 720.0), corr=("ok", 2))
        assert verdict == bdc.DEAF


class TestInertIsNotAFailure:
    def test_missing_unit_is_inert(self):
        verdict, detail = _decide(unit=(bdc.INERT, "unit mesh_bot does not exist on x"))
        assert verdict == bdc.INERT
        assert "does not exist" in detail

    def test_inactive_unit_is_inert_not_deaf(self):
        verdict, _ = _decide(unit=(bdc.INERT, "unit mesh_bot is inactive"))
        assert verdict == bdc.INERT


class TestUnobservableIsNeverAPass:
    def test_unreachable_bot_box(self):
        verdict, _ = _decide(unit=(bdc.UNOBSERVABLE, "ssh transport failed"))
        assert verdict == bdc.UNOBSERVABLE

    def test_unreadable_bot_journal(self):
        verdict, _ = _decide(age=(bdc.UNOBSERVABLE, None))
        assert verdict == bdc.UNOBSERVABLE

    def test_silent_bot_with_unreadable_corroborator_is_unobservable(self):
        """The trap: without the second witness, deafness and a quiet mesh are
        indistinguishable. Reporting CLEAN here would be the
        absence-of-evidence lie."""
        verdict, detail = _decide(age=("ok", None), corr=(bdc.UNOBSERVABLE, None))
        assert verdict == bdc.UNOBSERVABLE
        assert "quiet mesh" in detail

    def test_unobservable_never_reports_clean_or_deaf(self):
        for unit, age, corr in [
            ((bdc.UNOBSERVABLE, "x"), ("ok", 1.0), ("ok", 0)),
            (("ok", "a"), (bdc.UNOBSERVABLE, None), ("ok", 0)),
            (("ok", "a"), ("ok", None), (bdc.UNOBSERVABLE, None)),
        ]:
            # unit_up_s MUST be passed: omitting it defaults to None, which
            # is itself unobservable, and every case would pass vacuously
            # while testing nothing about the leg it names.
            verdict, _ = bdc.decide(unit[0], unit[1], age[0], age[1],
                                    corr[0], corr[1], THRESH,
                                    unit_up_s=LONG_UPTIME)
            assert verdict == bdc.UNOBSERVABLE


class TestDebounceStateSaver:
    """A debounce whose saver cannot write can never fire (2026-09-02 class)."""

    def test_save_and_load_roundtrip(self, tmp_path):
        p = str(tmp_path / "s" / "state.json")
        assert bdc._save_state(p, {"deaf_streak": 3}) is None
        assert bdc._load_state(p)["deaf_streak"] == 3

    def test_unwritable_state_reports_the_error_instead_of_swallowing(self, tmp_path):
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file, not a directory")
        err = bdc._save_state(str(blocked / "sub" / "state.json"), {"deaf_streak": 1})
        assert err, "a write failure must be reported, never swallowed"

    def test_missing_state_file_loads_empty_not_raises(self, tmp_path):
        assert bdc._load_state(str(tmp_path / "nope.json")) == {}

    def test_corrupt_state_file_loads_empty_not_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        assert bdc._load_state(str(p)) == {}


class TestConfigRefusal:
    def test_absent_config_refuses_loudly(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MESHFORGE_BOT_DEAF_CONFIG",
                           str(tmp_path / "absent.json"))
        rc = bdc.main([])
        assert rc == 2, "a check with no target must not exit 0"
        assert "FATAL" in capsys.readouterr().err

    def test_config_without_hosts_refuses(self, tmp_path, monkeypatch, capsys):
        cfg = tmp_path / "c.json"
        cfg.write_text('{"bot_unit": "mesh_bot"}')
        monkeypatch.setenv("MESHFORGE_BOT_DEAF_CONFIG", str(cfg))
        assert bdc.main([]) == 2
        assert "bot_host" in capsys.readouterr().err



# ── The layer the first draft skipped ────────────────────────────────────────
# Every test above this line exercises decide(), a pure function fed
# hand-built arguments. All 19 of them passed while the probe's journal
# pattern matched NOTHING on the real bot -- the same trap as the
# gen_fleet_hosts drift checker, whose 13 green tests mocked the exact layer
# that was broken. These tests feed lines captured from the real mesh_bot
# journal, so the pattern is pinned against the bot's actual output rather
# than against my belief about it.

# Captured 2026-09-05 from `journalctl -u mesh_bot -o short-unix` on the box
# that actually runs the bot.
#
# The LINE STRUCTURE is byte-exact -- epoch stamp, `unit[pid]:`, the bot's
# ` |     INFO | ` formatter, and the `Device:N Channel:N` body -- because
# that structure is the whole subject of these tests. Only the operator's
# node/host names in the payload are replaced with placeholders (MF014: they
# must not enter the repo), which the pattern under test never looks at.
# Do not "tidy" anything else about these strings.
REAL_RECEPTION_LINES = [
    "1788619421.633229 botbox bash[1100]: 2026-09-05 04:43:41,632 |     INFO | "
    "Device:1 Channel:1 MeshForge: ignoring bridge-machinery ACK From: <node>",
    "1788619426.680918 botbox bash[1100]: 2026-09-05 04:43:46,680 |     INFO | "
    "Device:1 Channel:1 Ignoring Message: [RNS:1f68] ACKBIG seq=1788619413 "
    "orig=<canary-origin> len=512 From: <node>",
]

# The other three reception branches mesh_bot.py takes (lines 2232/2256/2300).
# They did not appear in the captured window, which is precisely why keying
# the probe to whichever branch you happened to observe is the bug.
SYNTHETIC_RECEPTION_LINES = [
    "1788619500.0 host bash[1]: 2026-09-05 04:45:00,000 |     INFO | "
    "Device:1 Channel: 1 Received DM: ping From: somebody",
    "1788619501.0 host bash[1]: 2026-09-05 04:45:01,000 |     INFO | "
    "Device:1 Channel:1 ReceivedChannel: ping From: somebody",
    "1788619502.0 host bash[1]: 2026-09-05 04:45:02,000 |  WARNING | "
    "Device:1 Ignoring DM: junk From: somebody",
]

# Lines the bot writes that are NOT receptions -- they must not count as
# "the bot heard something", or a dead feed would look alive.
NON_RECEPTION_LINES = [
    "1788619400.0 host bash[1]: 2026-09-05 04:43:20,000 |    DEBUG | "
    "System: Scheduler Enabled. Default Device:1 Channel:1",
    "1788619401.0 host bash[1]: 2026-09-05 04:43:21,000 | CRITICAL | "
    "System: Error processing packet: boom Device:1",
]


class TestReceptionPatternMatchesTheRealBot:
    """The defect that kept this probe unshipped on 2026-09-04."""

    def test_the_shipped_pattern_matches_real_reception_lines(self):
        for line in REAL_RECEPTION_LINES:
            assert bdc.RECEPTION_RE.search(line), (
                f"reception line not matched -- the probe would read this bot "
                f"as silent and page DEAF at a healthy feed:\n{line}")

    def test_every_reception_branch_matches_not_just_one(self):
        """`ReceivedChannel` alone matched 0 of 9 real receptions."""
        for line in SYNTHETIC_RECEPTION_LINES:
            assert bdc.RECEPTION_RE.search(line), line

    def test_receivedchannel_alone_would_have_missed_the_real_lines(self):
        """Pins WHY the pattern changed, so nobody narrows it back."""
        assert not any("ReceivedChannel" in ln for ln in REAL_RECEPTION_LINES)

    def test_non_reception_lines_do_not_count_as_hearing(self):
        for line in NON_RECEPTION_LINES:
            assert not bdc.RECEPTION_RE.search(line), (
                f"non-reception line counted as a reception -- a dead feed "
                f"would look alive:\n{line}")

    def test_one_constant_feeds_both_the_grep_and_this_test(self):
        """hfm #5: two consumers of one artifact share ONE constant. The
        first draft had a compiled regex nothing used and a separate literal
        hardcoded into the ssh command, free to drift apart."""
        assert bdc.RECEPTION_RE.pattern == bdc.RECEPTION_PATTERN


class TestEpochParse:
    def test_parses_a_real_short_unix_line(self):
        line = REAL_RECEPTION_LINES[0]
        status, age = bdc.parse_reception_age(line, 1788619521.633229)
        assert status == "ok"
        assert age == pytest.approx(100.0, abs=0.01)

    def test_undateable_line_is_unobservable_not_silence(self):
        status, age = bdc.parse_reception_age("no timestamp here", 1788619521.0)
        assert status == bdc.UNOBSERVABLE
        assert age is None

    def test_epoch_zero_sentinel_is_refused_not_reported_as_57_years(self):
        """2026-09-02: an absent-value sentinel leaked into the measurement
        domain and rendered as an age of 29,806,174 minutes."""
        status, _ = bdc.parse_reception_age("0 host x[1]: msg", 1788619521.0)
        assert status == bdc.UNOBSERVABLE

    def test_future_stamp_clamps_to_zero_rather_than_going_negative(self):
        status, age = bdc.parse_reception_age(
            "1788619600.0 host x[1]: msg", 1788619521.0)
        assert status == "ok"
        assert age == 0.0

    def test_no_timezone_assumption_is_made(self):
        """short-iso + time.mktime() read a REMOTE stamp in the LOCAL tz.
        Epoch seconds are absolute, so the same line yields the same age
        under any TZ."""
        import os as _os
        import time as _time
        line = REAL_RECEPTION_LINES[0]
        ages = []
        original = _os.environ.get("TZ")
        try:
            for tz in ("UTC", "Pacific/Honolulu", "Europe/Berlin"):
                _os.environ["TZ"] = tz
                _time.tzset()
                ages.append(bdc.parse_reception_age(line, 1788619521.633229)[1])
        finally:
            if original is None:
                _os.environ.pop("TZ", None)
            else:
                _os.environ["TZ"] = original
            _time.tzset()
        assert len(set(ages)) == 1, f"age varied by timezone: {ages}"


class TestRestartGrace:
    """The 2026-09-04 shape: lehua's radio died mid-run and the bot was
    restarted TWICE that day. `journalctl -u <unit>` spans restarts, so
    without an uptime anchor a freshly restarted bot reads "heard nothing,
    ever" and can reach DEAF before it has had any chance to hear."""

    def test_fresh_restart_with_empty_history_is_not_deaf(self):
        verdict, detail = _decide(age=("ok", None), corr=("ok", 12), up=30.0)
        assert verdict == bdc.CLEAN, "a bot up 30s cannot have been silent 600s"
        assert "restarted" in detail

    def test_restart_grace_ends_at_the_threshold(self):
        """Up longer than the threshold, still nothing heard, traffic seen."""
        verdict, _ = _decide(age=("ok", None), corr=("ok", 12),
                             up=THRESH + 1.0)
        assert verdict == bdc.DEAF

    def test_silence_can_never_exceed_uptime(self):
        """A pre-restart reception must not be attributed to this process,
        and the process cannot have been silent longer than it has run."""
        silence, bounded = bdc.effective_silence(age=5000.0, unit_up_s=10.0)
        assert silence == 10.0 and bounded is True

    def test_post_restart_reception_is_used_as_is(self):
        silence, bounded = bdc.effective_silence(age=300.0, unit_up_s=10000.0)
        assert silence == 300.0 and bounded is False

    def test_long_uptime_with_empty_window_is_not_called_a_restart(self):
        """Legibility: a bot up 24h that heard nothing in the 6h lookback is
        silent because it heard nothing, not because it restarted. Bounding
        by the lookback also avoids claiming more silence than we looked for."""
        silence, bounded = bdc.effective_silence(
            age=None, unit_up_s=86400.0, lookback_s=21600.0)
        assert silence == 21600.0
        assert bounded is False
        verdict, detail = _decide(age=("ok", None), corr=("ok", 12), up=86400.0)
        assert verdict == bdc.DEAF
        assert "restarted" not in detail

    def test_the_grace_needs_no_second_tunable(self):
        """Silence is bounded by uptime, so any unit up less than the
        threshold is un-deafable by construction — no separate grace knob to
        drift out of sync with deaf_after_s (hfm #5)."""
        for up in (0.0, 1.0, THRESH / 2, THRESH):
            verdict, _ = _decide(age=("ok", None), corr=("ok", 99), up=up)
            assert verdict == bdc.CLEAN, f"up={up} should be un-deafable"


class TestUnknownUptimeIsNeverGuessed:
    def test_missing_uptime_is_unobservable_not_clean_and_not_deaf(self):
        verdict, detail = bdc.decide(
            "ok", "mesh_bot active", "ok", None, "ok", 12, THRESH,
            unit_up_s=None)
        assert verdict == bdc.UNOBSERVABLE
        assert "freshly restarted" in detail

    def test_missing_uptime_does_not_page_even_with_heavy_corroboration(self):
        verdict, _ = bdc.decide("ok", "a", "ok", 99999.0, "ok", 500, THRESH,
                                unit_up_s=None)
        assert verdict != bdc.DEAF


class TestUnitUptimeParse:
    """Monotonic anchors only — both read from the BOT box, so no clock is
    compared across machines and no timezone is parsed (hfm #6: wall clocks
    are forgeable on RTC-less Pis; lehua is a Zero 2W)."""

    # Verbatim shapes from `systemctl show` + /proc/uptime on the bot box.
    def test_real_values_give_the_real_uptime(self):
        fields = {"ActiveEnterTimestampMonotonic": "31725812"}
        up = bdc.parse_unit_uptime(fields, "78217.00 309994.59")
        assert up == pytest.approx(78217.00 - 31.725812, abs=0.01)

    def test_never_activated_unit_reports_unknown_not_zero(self):
        """systemd writes 0 here; a 0 uptime would mean 'just started' and
        silently grant infinite grace."""
        assert bdc.parse_unit_uptime(
            {"ActiveEnterTimestampMonotonic": "0"}, "78217.00 0") is None

    def test_absent_property_is_unknown(self):
        assert bdc.parse_unit_uptime({}, "78217.00 0") is None

    def test_unparseable_values_are_unknown(self):
        assert bdc.parse_unit_uptime(
            {"ActiveEnterTimestampMonotonic": "nope"}, "78217.00 0") is None
        assert bdc.parse_unit_uptime(
            {"ActiveEnterTimestampMonotonic": "31725812"}, "") is None

    def test_disagreeing_anchors_are_unknown_not_negative(self):
        """Unit start after the boot it is measured against = knowledge we do
        not have, not an uptime of zero."""
        assert bdc.parse_unit_uptime(
            {"ActiveEnterTimestampMonotonic": "99000000000"}, "100.0 0") is None

    def test_no_timezone_string_is_ever_parsed(self):
        """ActiveEnterTimestamp is a locale string ('Fri 2026-09-04 09:21:51
        HST'); parsing it would re-introduce the cross-box timezone defect
        removed from the journal leg."""
        import inspect
        src = inspect.getsource(bdc)
        assert "ActiveEnterTimestamp=" not in src
        assert "strptime" not in src, "no wall-clock string parsing anywhere"

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
