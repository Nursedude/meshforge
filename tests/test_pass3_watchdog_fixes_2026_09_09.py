"""Planting tests for the 2026-09-09 pass-3 review findings in the watchdog
probe layer (findings 1-skins, 2, 3, 4, 5, 6, 7, 8, 9, 20, 22).

Each test PLANTS the condition the finding describes and asserts the honest
outcome; every one failed (raised, noted clean, or paged a ghost) before its
fix. Grouped by finding number.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils import cascade_fingerprints as cfp  # noqa: E402
from utils import watchdog_probe_core as core  # noqa: E402
from utils import watchdog_probes_gateway as gw  # noqa: E402
from utils import watchdog_probes_rns as wpr  # noqa: E402
from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions, reset_dispositions,
)
from utils.watchdog_tracker import SignalTracker  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_tick():
    reset_dispositions()
    yield
    reset_dispositions()


_SS_FOREIGN_PID1 = (
    "Netid State    Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
    'u_str LISTEN   0      0      @rns/volcano 99999 * 0 users:(("python3",pid=1,fd=4))\n'
)
_SS_NO_LISTENER = "Netid State Local Address:Port\n"
_NOMADNET = (b"/home/user/.local/share/pipx/venvs/nomadnet/bin/python3\x00"
             b"/home/user/.config/meshforge/nomadnet_wrapper.py\x00"
             b"--rnsconfig\x00/etc/reticulum\x00")


def _ss_mock(outputs):
    """subprocess.run stand-in cycling through ``outputs`` (last repeats)."""
    seq = list(outputs)

    def _run(*a, **k):
        out = seq.pop(0) if len(seq) > 1 else seq[0]
        return subprocess.CompletedProcess(args=a, returncode=0,
                                           stdout=out, stderr="")
    return _run


# ── finding 1 skins ──────────────────────────────────────────────────────


class TestFinding1Skins:

    def test_inverted_tier_without_injected_rnsd_enabled_asks_systemd(
            self, tmp_path):
        """rnsd_enabled=None is what the RUNNER passes — it used to NameError."""
        (tmp_path / "1").mkdir()
        (tmp_path / "1" / "cmdline").write_bytes(_NOMADNET)
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_ss_mock([_SS_FOREIGN_PID1])), \
             patch("utils.rns_init._rnsd_unit_enabled", return_value=True):
            sig = wpr.probe_rns_namespace_collision(
                "volcano", proc_root=str(tmp_path), rnsd_enabled=None)
        assert sig is not None and sig.extra["tier"] == "inverted"
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_ss_mock([_SS_FOREIGN_PID1])), \
             patch("utils.rns_init._rnsd_unit_enabled", return_value=False):
            assert wpr.probe_rns_namespace_collision(
                "volcano", proc_root=str(tmp_path), rnsd_enabled=None) is None

    def test_socket_creation_failure_is_indeterminate_not_a_raise(self):
        """EMFILE (#73) on the watchdog's own socket() must stay inside."""
        with patch("utils.watchdog_probes_rns.socket.socket",
                   side_effect=OSError(24, "Too many open files")):
            assert wpr.probe_rns_shared_instance_responsive("volcano") is None
        got = collect_dispositions()
        for cls in ("rns_shared_instance_unresponsive",
                    "rns_instance_name_mismatch"):
            assert got[cls]["disp"] == "indeterminate"
            assert "socket() failed" in got[cls]["reason"]

    @pytest.mark.parametrize("fetch", ["delivery", "queue"])
    def test_torn_http_response_is_caught_by_the_payload_fetchers(
            self, tmp_path, fetch):
        fn = gw._fetch_delivery_payload if fetch == "delivery" \
            else gw._fetch_queue_payload
        with patch("utils.watchdog_probes_gateway.urlopen",
                   side_effect=IncompleteRead(b"partial")):
            payload, reason = fn("127.0.0.1", 5000, 1.0,
                                 state_path=str(tmp_path / "absent.json"))
        assert payload is None and reason

    def test_torn_http_response_is_caught_by_the_dups_probes(self, tmp_path):
        with patch("utils.watchdog_probes_gateway.urlopen",
                   side_effect=IncompleteRead(b"partial")), \
             patch("utils.watchdog_probes_gateway.operator_cron_wired",
                   return_value=False):
            assert gw.probe_gateway_dup_degraded(
                debounce_path=str(tmp_path / "d.json")) is None
            assert gw.probe_gateway_dual_homed_exposure(
                state_path=str(tmp_path / "s.json")) is None
        assert not isinstance(IncompleteRead(b""), OSError)  # why it matters


# ── finding 2: the RPC latency arm ───────────────────────────────────────


class TestFinding2LatencyArm:

    @pytest.fixture(autouse=True)
    def _clean(self):
        wpr.reset_rns_rpc_timeout_streak()
        wpr.reset_rnstatus_baseline()
        yield
        wpr.reset_rns_rpc_timeout_streak()
        wpr.reset_rnstatus_baseline()

    @staticmethod
    def _status(**kw):
        from utils.rns_status_parser import RNSStatus
        return RNSStatus(**kw)

    def _warm_baseline(self):
        wpr._rnstatus_durations.extend(
            [1.0] * wpr._RNSTATUS_BASELINE_MIN_SAMPLES)

    def _slow_until_fires(self):
        needed = wpr._rpc_confirm_ticks()
        slow = self._status(duration_s=10.0)
        for _ in range(needed - 1):
            assert wpr.probe_rns_rpc_responsive(rnstatus_status=slow) is None
        return wpr.probe_rns_rpc_responsive(rnstatus_status=slow)

    def test_latency_arm_has_its_own_subject(self):
        self._warm_baseline()
        sig = self._slow_until_fires()
        assert sig is not None and sig.severity == "degraded"
        assert sig.subject != "rnsd"
        assert sig.subject == wpr._RPC_LATENCY_SUBJECT

    def test_wedge_after_slow_phase_is_a_new_transition(self):
        """Same (cls, subject) would have been 'still active' in the tracker
        and the wedge page swallowed."""
        import utils.watchdog_runner as wr
        self._warm_baseline()
        slow = self._slow_until_fires()
        timed = self._status(timed_out=True, parse_error="rnstatus timed out")
        wedge = None
        for _ in range(wpr._rpc_confirm_ticks()):
            wedge = wpr.probe_rns_rpc_responsive(rnstatus_status=timed)
        assert wedge is not None and wedge.severity == "wedge"
        tr = SignalTracker()
        tr.update([slow], now=1.0, coverage=wr.build_coverage([slow]))
        reset_dispositions()
        active, cleared, _ = tr.update(
            [wedge], now=2.0, coverage=wr.build_coverage([wedge]))
        assert any(s.severity == "wedge" and ts == 2.0 for s, ts in active)
        assert slow.key() in cleared

    def test_timeout_resets_the_slow_streak(self):
        """slow, slow, TIMEOUT, slow must not fire: 'N consecutive' does not
        span a timeout (or an rnsd restart)."""
        self._warm_baseline()
        assert wpr._rpc_confirm_ticks() == 3, "test assumes the default"
        slow = self._status(duration_s=10.0)
        timed = self._status(timed_out=True, parse_error="timed out")
        down = self._status(parse_error="No shared instance")
        assert wpr.probe_rns_rpc_responsive(rnstatus_status=slow) is None
        assert wpr.probe_rns_rpc_responsive(rnstatus_status=slow) is None
        assert wpr.probe_rns_rpc_responsive(rnstatus_status=timed) is None
        assert wpr.probe_rns_rpc_responsive(rnstatus_status=slow) is None
        assert wpr._rpc_slow_streak == 1
        assert wpr.probe_rns_rpc_responsive(rnstatus_status=slow) is None
        assert wpr.probe_rns_rpc_responsive(rnstatus_status=down) is None
        assert wpr._rpc_slow_streak == 0


# ── finding 3 + 22: journal helpers ──────────────────────────────────────


def _cp(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=rc,
                                       stdout=stdout, stderr=stderr)


class TestFinding3JournalRc1WithStderr:

    BAD = _cp(1, "", "Failed to parse timestamp: -7x")
    EMPTY = _cp(1, "", "")

    def test_match_lines(self):
        with patch("utils.watchdog_probe_core.subprocess.run",
                   return_value=self.BAD):
            assert core._journal_match_lines("u", "p", "7x") is None
        with patch("utils.watchdog_probe_core.subprocess.run",
                   return_value=self.EMPTY):
            assert core._journal_match_lines("u", "p", "7h") == []

    def test_count_match_derives_from_match_lines(self):
        with patch("utils.watchdog_probe_core.subprocess.run",
                   return_value=self.BAD):
            assert core._journal_count_match("u", "p", "7x") is None
        with patch("utils.watchdog_probe_core.subprocess.run",
                   return_value=_cp(0, "a\nb\n\n")):
            assert core._journal_count_match("u", "p", "7h") == 2
        with patch("utils.watchdog_probe_core._journal_match_lines",
                   return_value=["x"]) as m:
            assert core._journal_count_match("u", "p", "7h") == 1
            assert m.called

    def test_user_unit_has_lines(self):
        with patch("utils.watchdog_probe_core.subprocess.run",
                   return_value=self.BAD):
            assert core._journal_user_unit_has_lines("x.service", "7x") is None
        with patch("utils.watchdog_probe_core.subprocess.run",
                   return_value=self.EMPTY):
            assert core._journal_user_unit_has_lines("x.service", "3h") is False

    def test_newest_match_status_still_guards(self):
        core.reset_journal_memo()
        with patch("utils.watchdog_probe_core.subprocess.run",
                   return_value=self.BAD):
            assert core._journal_newest_match_status("u", "p", "7x") == \
                ("unobservable", None)


class TestFinding22MemoKeyIncludesLookback:

    def test_different_window_gets_a_full_scan(self):
        core.reset_journal_memo()
        seen = []

        def _run(argv, **k):
            seen.append(argv[argv.index("--since") + 1])
            return _cp(0, "1700000000.000 hello\n")

        with patch("utils.watchdog_probe_core.subprocess.run", side_effect=_run):
            core._journal_newest_match_status("u", "p", "1h", now=1700000100.0)
            core._journal_newest_match_status("u", "p", "7h", now=1700000110.0)
            core._journal_newest_match_status("u", "p", "1h", now=1700000120.0)
        assert seen[0] == "-1h"
        assert seen[1] == "-7h", "a wider window must not ride the narrow memo"
        assert seen[2].startswith("@"), "the same window DOES ride its memo"
        core.reset_journal_memo()


# ── finding 4: namespace-collision TOCTOU ────────────────────────────────


class TestFinding4VanishedOwner:

    def test_owner_gone_from_proc_is_indeterminate_not_a_wedge(self, tmp_path):
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_ss_mock([_SS_FOREIGN_PID1])):
            sig = wpr.probe_rns_namespace_collision(
                "volcano", proc_root=str(tmp_path), rnsd_enabled=True)
        assert sig is None, "a dead pid must never get a `sudo kill` page"
        got = collect_dispositions()["rns_namespace_collision"]
        assert got["disp"] == "indeterminate"
        assert "unreadable on two consecutive scans" in got["reason"]

    def test_listener_gone_on_rescan_reads_teardown(self, tmp_path):
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_ss_mock([_SS_FOREIGN_PID1, _SS_NO_LISTENER])):
            sig = wpr.probe_rns_namespace_collision(
                "volcano", proc_root=str(tmp_path), rnsd_enabled=True)
        assert sig is None
        got = collect_dispositions()["rns_namespace_collision"]
        assert got["disp"] == "indeterminate"
        assert "teardown" in got["reason"]

    def test_readable_foreign_owner_still_fires(self, tmp_path):
        """A vanished pid beside a REAL squatter must not mute the squatter."""
        (tmp_path / "2").mkdir()
        (tmp_path / "2" / "cmdline").write_bytes(
            b"/usr/bin/python3\x00/opt/meshanchor/src/daemon.py\x00")
        two = _SS_FOREIGN_PID1 + (
            'u_str LISTEN 0 0 @rns/volcano 99998 * 0 users:(("python3",pid=2,fd=4))\n')
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_ss_mock([two])):
            sig = wpr.probe_rns_namespace_collision(
                "volcano", proc_root=str(tmp_path), rnsd_enabled=True)
        assert sig is not None and sig.severity == "wedge"
        assert sig.extra["pid"] == 2


# ── finding 5: dual-homed saver ──────────────────────────────────────────


class TestFinding5DualHomedSaver:

    PAYLOAD = {"status": "ok", "freshness": {"stale": False},
               "dual_homed_recipient_hashes": ["aaaa1111", "bbbb2222"]}

    def test_unwritable_state_fires_once_not_every_tick(self, tmp_path,
                                                        monkeypatch):
        monkeypatch.setattr(gw, "_known_dual_homed_mem", {})
        sp = str(tmp_path / "state.json")
        with patch("utils.watchdog_probes_gateway.os.replace",
                   side_effect=OSError(30, "Read-only file system")), \
             patch("utils.watchdog_probes_gateway.note_state_write_failure") as w:
            first = gw.probe_gateway_dual_homed_exposure(
                payload=dict(self.PAYLOAD), state_path=sp)
            reset_dispositions()
            second = gw.probe_gateway_dual_homed_exposure(
                payload=dict(self.PAYLOAD), state_path=sp)
        assert first is not None
        assert second is None, "known recipients re-announced every tick"
        assert w.called, "the swallow must go through THE witness"
        assert collect_dispositions()["gateway_dual_homed_exposure"]["disp"] == "clean"


# ── finding 6: write canary empty health block ───────────────────────────


class TestFinding6EmptyHealthBlock:

    def test_present_but_empty_health_is_indeterminate(self):
        class _Resp:
            def read(self):
                return b'{"health": {}}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("utils.watchdog_probes_gateway.urlopen", return_value=_Resp()):
            assert gw.probe_delivery_write_canary(gateway_main_pid=5678) is None
        got = collect_dispositions()["delivery_write_canary"]
        assert got["disp"] == "indeterminate"
        assert "missing preflight_ok, consecutive_write_errors" in got["reason"]


# ── finding 7 + 20: cascade fingerprints ─────────────────────────────────


class TestFinding7SocketKey:

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.delenv("MESHFORGE_CASCADE_PROBE_DISABLED", raising=False)
        monkeypatch.setattr(cfp, "_witness_last", {})

    def test_state_filtered_seven_field_rows_key_on_identity(self):
        a = "u_str 0 0 * 1234 @rns/default/rpc 5678"
        b = "u_str 4 9 * 1234 @rns/default/rpc 5678"
        assert cfp._socket_key(a) == cfp._socket_key(b)
        assert cfp._socket_key(a) != cfp._socket_key(
            "u_str 0 0 * 4321 @rns/default/rpc 8765")

    def test_spaced_instance_name_keeps_the_whole_identity(self):
        a = "u_str 0 0 * 1234 @rns/moc 3/rpc 5678"
        b = "u_str 7 7 * 1234 @rns/moc 3/rpc 5678"
        assert cfp._socket_key(a) == cfp._socket_key(b)
        assert "3/rpc" in cfp._socket_key(a)

    def test_unfiltered_eight_field_rows_still_work(self):
        a = "u_str SYN-SENT 0 0 * 1234 @rns/default/rpc 5678"
        b = "u_str SYN-SENT 4 9 * 1234 @rns/default/rpc 5678"
        assert cfp._socket_key(a) == cfp._socket_key(b)

    def test_probe_hit_survives_queue_move_in_the_real_ss_format(self):
        first = MagicMock(returncode=0,
                          stdout="u_str 0 0 * 1234 @rns/default/rpc 5678\n")
        second = MagicMock(returncode=0,
                           stdout="u_str 4 9 * 1234 @rns/default/rpc 5678\n")
        # ``utils.cascade_fingerprints.subprocess`` IS the stdlib module, so
        # patching ``.run`` on it is process-global. A fixed two-item
        # side_effect list was eaten by a stray ``subprocess.run`` from a
        # thread another test leaked, during the probe's 0.4 s re-sample
        # pause (CI 3.11 run 34461082035: StopIteration on the SECOND
        # sample; passed on 3.9 and twice locally). Serve the canned samples
        # only to the probe's own ``ss`` argv; everything else runs for real.
        samples = iter([first, second])
        real_run = cfp.subprocess.run

        def _dispatch(argv, *a, **kw):
            if argv and argv[0] == "ss":
                return next(samples)
            return real_run(argv, *a, **kw)

        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value="/usr/bin/ss"), \
             patch("utils.cascade_fingerprints.subprocess.run",
                   side_effect=_dispatch):
            hit = cfp.probe_rns_rpc_wedge()
        assert hit is not None and hit.metric["syn_sent_count"] == 1

    def test_blind_first_sample_leaves_a_witness(self, caplog):
        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value="/usr/bin/ss"), \
             patch("utils.cascade_fingerprints.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout="", stderr="x")), \
             caplog.at_level(logging.WARNING, logger="utils.cascade_fingerprints"):
            assert cfp.probe_rns_rpc_wedge() is None
        assert any("UNOBSERVABLE" in r.getMessage() for r in caplog.records)

    def test_stop_event_interrupts_the_resample_pause(self):
        ev = threading.Event()
        ev.set()
        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value="/usr/bin/ss"), \
             patch("utils.cascade_fingerprints.subprocess.run",
                   return_value=MagicMock(
                       returncode=0,
                       stdout="u_str 0 0 * 1234 @rns/default/rpc 5678\n")) as m:
            assert cfp.probe_rns_rpc_wedge(stop_event=ev) is None
        assert m.call_count == 1, "no second sample after a stop request"

    def test_no_time_sleep_left_in_the_module(self):
        src = (SRC / "utils" / "cascade_fingerprints.py").read_text()
        assert "time.sleep(" not in src

    def test_detector_passes_its_stop_event_to_declaring_probes(self):
        from utils.cascade_detector import CascadeDetector
        seen = {}

        def _wants(*, stop_event):
            seen["ev"] = stop_event
            return None

        def _plain():
            seen["plain"] = True
            return None

        fps = [
            cfp.Fingerprint(name="a", severity="pre_fail", probe=_wants,
                            cadence_s=1, incident_refs=(), coupled_to=(),
                            wants_stop_event=True),
            cfp.Fingerprint(name="b", severity="pre_fail", probe=_plain,
                            cadence_s=1, incident_refs=(), coupled_to=()),
        ]
        det = CascadeDetector(fingerprints=fps)
        det.evaluate_once()
        assert seen["ev"] is det._stop_event
        assert seen["plain"] is True
        assert cfp.get_fingerprint_by_name("rns_rpc_wedge").wants_stop_event


class TestFinding20Port4403Match:

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.delenv("MESHFORGE_CASCADE_PROBE_DISABLED", raising=False)

    def test_ephemeral_4403x_ports_do_not_count(self):
        stdout = (
            '0 0 127.0.0.1:44031 127.0.0.1:8080 users:(("python3",pid=11,fd=3))\n'
            '0 0 127.0.0.1:44032 127.0.0.1:8080 users:(("python3",pid=12,fd=3))\n'
        )
        with patch("utils.cascade_fingerprints.shutil.which",
                   return_value="/usr/bin/ss"), \
             patch("utils.cascade_fingerprints.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout=stdout)):
            assert cfp.probe_tcp_4403_contention() is None

    def test_real_4403_rows_still_match(self):
        assert cfp._PORT_4403_RE.search("0 0 127.0.0.1:54286 127.0.0.1:4403 users")
        assert cfp._PORT_4403_RE.search("0 0 127.0.0.1:4403 127.0.0.1:54286")
        assert not cfp._PORT_4403_RE.search("0 0 127.0.0.1:44031 127.0.0.1:80 x")


# ── finding 8: tracer stale fire ─────────────────────────────────────────


class TestFinding8TracerStaleFire:

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MESHFORGE_CASCADE_PROBE_DISABLED", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.setattr(cfp, "_witness_last", {})
        self.sd = tmp_path / "meshforge" / "tracer"

    def test_future_stamped_file_surfaces_the_clock_step(self):
        self.sd.mkdir(parents=True)
        f = self.sd / "tracer-1.json"
        f.write_text("{}")
        t = time.time() + 3600
        os.utime(f, (t, t))
        hit = cfp.probe_tracer_stale_fire()
        assert hit is not None and hit.metric.get("clock_stepped") is True
        assert "FUTURE" in hit.evidence

    def test_small_skew_is_not_a_clock_step(self):
        self.sd.mkdir(parents=True)
        f = self.sd / "tracer-1.json"
        f.write_text("{}")
        t = time.time() + 5
        os.utime(f, (t, t))
        assert cfp.probe_tracer_stale_fire() is None

    def test_state_dir_failure_leaves_a_witness(self, caplog):
        with patch("utils.cascade_fingerprints._tracer_state_dir",
                   side_effect=RuntimeError("no operator")), \
             caplog.at_level(logging.WARNING, logger="utils.cascade_fingerprints"):
            assert cfp.probe_tracer_stale_fire() is None
        assert any("blind" in r.getMessage() for r in caplog.records)

    def test_scan_failure_leaves_a_witness(self, caplog):
        self.sd.mkdir(parents=True)
        with patch.object(Path, "iterdir", side_effect=PermissionError("nope")), \
             caplog.at_level(logging.WARNING, logger="utils.cascade_fingerprints"):
            assert cfp.probe_tracer_stale_fire() is None
        assert any("cannot scan" in r.getMessage() for r in caplog.records)

    def test_witness_is_rate_limited(self, caplog):
        with patch("utils.cascade_fingerprints._tracer_state_dir",
                   side_effect=RuntimeError("no operator")), \
             caplog.at_level(logging.WARNING, logger="utils.cascade_fingerprints"):
            cfp.probe_tracer_stale_fire()
            cfp.probe_tracer_stale_fire()
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1


# ── finding 9: declaration path ──────────────────────────────────────────


class TestFinding9ConfiguredButMissingServiceUser:

    def test_deleted_service_user_is_unreadable_not_the_operator(self, tmp_path):
        home = tmp_path / "op"
        (home / ".config" / "meshforge").mkdir(parents=True)
        (home / ".config" / "meshforge" / "deployment.json").write_text(
            '{"role": "full"}')

        class _PW:
            pw_dir = str(home)

        with patch("pwd.getpwnam", side_effect=KeyError("ghost")), \
             patch("utils.fleet_test_runner._find_operator_user",
                   return_value=(1000, "op")), \
             patch("pwd.getpwuid", return_value=_PW()):
            assert core.deployment_declaration_path("ghost") is None
            # The rnsd-absent-by-design shape STILL falls back.
            assert core.deployment_declaration_path(None) == str(
                home / ".config" / "meshforge" / "deployment.json")


if __name__ == "__main__":
    pytest.main([__file__])
