"""Tests for the gateway RESOURCE round-trip canary + its watchdog probe.

The load-bearing properties:
  * the SQL LIKE predicates are pinned against rows the REAL PersistentMessageQueue
    writes — if the schema drifts these break instead of the canary silently
    mis-reading production;
  * classify() maps every leg outcome to a DISTINCT, honest verdict — the
    "control back, resource NOT" branch is the 2026-06-20 EROFS signature and
    MUST be a FAIL, while a both-missing return is a CONCERN (peer/path, not
    resource-specific), and a DB read error is never folded into a false FAIL;
  * the verdict envelope the canary writes and the probe reads share ONE state
    dir (honest_failure_modes #5 — two consumers of one path drift unless pinned);
  * the probe self-guards INERT off-box, fires on FAIL/CONCERN/silence after a
    2-tick debounce, and treats an absent verdict as indeterminate (held).
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lab.gateway_resource_canary import (  # noqa: E402
    DEFAULT_REPLY_BYTES,
    EXIT_CONCERN,
    EXIT_FAIL,
    EXIT_OK,
    ResourceCanaryResult,
    STATE_DIR_LEAF,
    classify,
    control_like_pattern,
    default_state_dir,
    poll_both_rows,
    resource_like_pattern,
    write_envelope,
)


# ---------------------------------------------------------------- predicates


class TestPredicates:
    def test_control_pattern_shape(self):
        assert control_like_pattern(42, "rescanary-moc") == (
            "%ACK seq=42 orig=rescanary-moc%")

    def test_resource_pattern_shape(self):
        assert resource_like_pattern(42, "rescanary-moc") == (
            "%ACKBIG seq=42 orig=rescanary-moc%")

    def test_patterns_are_mutually_exclusive(self):
        # "ACK seq=" is NOT a substring of "ACKBIG seq=" (BIG separates them),
        # so the two reply bodies can never cross-match. This is what lets one
        # fire carry both a control and a resource reply with the same seq.
        from lab._lab_common import make_ack_body, make_bigack_body
        ctrl_body = make_ack_body(7, "rescanary-moc")
        big_body = make_bigack_body(7, "rescanary-moc", 512)
        ctrl_pat = control_like_pattern(7, "rescanary-moc").strip("%")
        res_pat = resource_like_pattern(7, "rescanary-moc").strip("%")
        assert ctrl_pat in ctrl_body and ctrl_pat not in big_body
        assert res_pat in big_body and res_pat not in ctrl_body


# ---------------------------------------------------------------- leg 2 SQL


class TestPollBothRows:
    """Pin the return-poll predicates against rows the REAL queue class writes."""

    def _queue(self, tmp_path):
        from gateway.message_queue import PersistentMessageQueue
        return PersistentMessageQueue(db_path=str(tmp_path / "queue.db"))

    def _enqueue_bridged(self, q, body):
        # Shape mirrors the R->M bridge enqueue: a leading [RNS:xxxx] tag then
        # the echo's reply body, destination 'meshtastic'.
        q.enqueue(payload={"message": f"[RNS:ab12cd34] {body}", "channel": 0},
                  destination="meshtastic")

    def test_both_found(self, tmp_path):
        q = self._queue(tmp_path)
        self._enqueue_bridged(q, "ACK seq=100 orig=rescanary-moc recv_at=z")
        self._enqueue_bridged(q, "ACKBIG seq=100 orig=rescanary-moc len=512 recv_at=z")
        c, r, err, _ = poll_both_rows(
            Path(q._db_path), control_like_pattern(100, "rescanary-moc"),
            resource_like_pattern(100, "rescanary-moc"), timeout_s=0)
        assert (c, r, err) == (True, True, None)

    def test_control_only(self, tmp_path):
        q = self._queue(tmp_path)
        self._enqueue_bridged(q, "ACK seq=101 orig=rescanary-moc recv_at=z")
        c, r, err, _ = poll_both_rows(
            Path(q._db_path), control_like_pattern(101, "rescanary-moc"),
            resource_like_pattern(101, "rescanary-moc"), timeout_s=0)
        assert (c, r, err) == (True, False, None)

    def test_resource_only(self, tmp_path):
        q = self._queue(tmp_path)
        self._enqueue_bridged(q, "ACKBIG seq=102 orig=rescanary-moc len=512 recv_at=z")
        c, r, err, _ = poll_both_rows(
            Path(q._db_path), control_like_pattern(102, "rescanary-moc"),
            resource_like_pattern(102, "rescanary-moc"), timeout_s=0)
        assert (c, r, err) == (False, True, None)

    def test_neither(self, tmp_path):
        q = self._queue(tmp_path)
        c, r, err, _ = poll_both_rows(
            Path(q._db_path), control_like_pattern(103, "rescanary-moc"),
            resource_like_pattern(103, "rescanary-moc"), timeout_s=0)
        assert (c, r, err) == (False, False, None)

    def test_wrong_destination_does_not_match(self, tmp_path):
        q = self._queue(tmp_path)
        # An rns-destined row carrying the marker must NOT count as bridged-back.
        q.enqueue(payload={"message": "ACKBIG seq=104 orig=rescanary-moc len=512"},
                  destination="rns")
        c, r, _, _ = poll_both_rows(
            Path(q._db_path), control_like_pattern(104, "rescanary-moc"),
            resource_like_pattern(104, "rescanary-moc"), timeout_s=0)
        assert (c, r) == (False, False)

    def test_unreadable_db_is_error_not_false(self, tmp_path):
        # Honest failure mode: unobservable != not-arrived.
        bogus = tmp_path / "not-a-dir" / "queue.db"
        c, r, err, _ = poll_both_rows(
            bogus, "%ACK seq=1%", "%ACKBIG seq=1%", timeout_s=0)
        assert err is not None and "unreadable" in err


# ---------------------------------------------------------------- verdicts


def _leg1(outcome, detail=None, elapsed=2.0):
    return (outcome, detail, elapsed)


def _leg2(control, resource, db_error=None, elapsed=8.0):
    return (control, resource, db_error, elapsed)


class TestClassify:
    """Every leg outcome → a DISTINCT, honest verdict."""

    def _c(self, leg1, leg2):
        return classify(leg1, leg2, seq=42, msg_id="m", reply_bytes=512)

    def test_leg1_dropped_is_fail(self):
        r = self._c(_leg1("dropped", "rns_delivery_failed"), None)
        assert r.verdict == "FAIL" and r.exit_code == EXIT_FAIL
        assert "rns_delivery_failed" in r.reason

    def test_leg1_timeout_is_fail(self):
        r = self._c(_leg1("timeout", None, 120.0), None)
        assert r.verdict == "FAIL"
        assert "not confirmed" in r.reason

    def test_leg1_db_error_is_fail_distinct(self):
        r = self._c(_leg1("error", "counters DB unreadable: boom", 0.1), None)
        assert r.verdict == "FAIL"
        assert "unreadable" in r.reason
        assert "not confirmed" not in r.reason  # never folded into timeout

    def test_both_back_is_ok(self):
        r = self._c(_leg1("confirmed", None, 3.2), _leg2(True, True, None, 8.0))
        assert r.verdict == "OK" and r.exit_code == EXIT_OK
        assert r.control_back is True and r.resource_back is True
        assert r.confirm_s == pytest.approx(3.2)

    def test_control_back_resource_missing_is_fail(self):
        """THE money signal: single-packet works, the Resource does not.

        Reason is path-AGNOSTIC (corrected 2026-06-27): it no longer hardcodes
        ``/etc/reticulum/storage`` — the gateway's real resourcepath is its
        runtime ``RNS.Reticulum.resourcepath``, verified to be PrivateTmp
        ``/tmp/meshforge_rns_client`` on moc, not /etc/reticulum. The reason
        points at the discovery script + the path-agnostic journal witness.
        """
        r = self._c(_leg1("confirmed", None, 3.0), _leg2(True, False, None, 150.0))
        assert r.verdict == "FAIL" and r.exit_code == EXIT_FAIL
        assert r.control_back is True and r.resource_back is False
        assert "EROFS" in r.reason
        assert "gateway_resourcepath.sh" in r.reason
        # MUST NOT hardcode the (wrong) path any more — that misled the drill.
        assert "/etc/reticulum/storage" not in r.reason

    def test_resource_back_control_missing_is_concern(self):
        r = self._c(_leg1("confirmed", None, 3.0), _leg2(False, True, None, 9.0))
        assert r.verdict == "CONCERN" and r.exit_code == EXIT_CONCERN
        assert "transient single-packet loss" in r.reason

    def test_neither_back_is_concern_not_fail(self):
        r = self._c(_leg1("confirmed", None, 3.0), _leg2(False, False, None, 150.0))
        assert r.verdict == "CONCERN"
        assert "NOT resource-specific" in r.reason

    def test_leg2_db_error_is_concern_distinct(self):
        r = self._c(_leg1("confirmed", None, 3.0),
                    _leg2(False, False, "queue DB unreadable: boom", 0.1))
        assert r.verdict == "CONCERN"
        assert "unreadable" in r.reason


# ------------------------------------------------------------ reply-size ceiling


class TestReplyByteCeiling:
    """Pin the 512 default + ceiling (2026-06-27 finding).

    The handoff plan was "make the reply bigger to force a disk write." That
    was based on a misobservation. VERIFIED 2026-06-27 (strace, live gateway):
      - 512 ALREADY forces an on-disk Resource assembly (write to
        <resourcepath>/<hash>), so A1 is NOT blind to the #60 EROFS class; and
      - larger replies (1024/2048) assemble fine but get DROPPED by the RNS→Mesh
        bridge before the meshtastic queue the canary polls, producing a FALSE
        resource_back=false FAIL — a mesh-bridge artifact, not a resource fault.
    So the reply must stay >319 (to be a Resource) and <=512 (to round-trip
    cleanly). These guards stop a future session re-raising it.
    """

    def test_default_reply_bytes_forces_a_resource(self):
        # > LINK_PACKET_MAX_CONTENT (319) so LXMF delivers it as an RNS Resource
        # the gateway must assemble (and write to disk) — the #60 EROFS step.
        assert DEFAULT_REPLY_BYTES > 319

    def test_default_reply_bytes_stays_at_or_below_512(self):
        # CEILING: above ~512 the RNS→Mesh bridge drops the bridged reply before
        # the meshtastic queue → false FAIL. Do NOT raise. See the module-level
        # DEFAULT_REPLY_BYTES comment + the 2026-06-27 handoff.
        assert DEFAULT_REPLY_BYTES <= 512


# ---------------------------------------------------------------- envelope


class TestEnvelope:
    def test_write_envelope_atomic_round_trip(self, tmp_path):
        sdir = str(tmp_path / "gateway_resource_canary")
        result = ResourceCanaryResult(
            verdict="FAIL", reason="control back, resource NOT", seq=99,
            reply_bytes=512, control_back=True, resource_back=False,
            peer="meshanchor-server", host="moc", ts=1718900000.0)
        write_envelope(sdir, result)
        with open(os.path.join(sdir, "last.json")) as fh:
            doc = json.load(fh)
        assert doc["verdict"] == "FAIL"
        assert doc["control_back"] is True and doc["resource_back"] is False
        assert doc["seq"] == 99

    def test_write_envelope_never_raises_on_bad_dir(self, tmp_path, capsys):
        # A persistently-unwritable dir leaves a stderr witness, not a crash —
        # the probe then reads it as DARK (stale mtime), the honest answer.
        bad = "/proc/cannot/make/this"  # unwritable
        write_envelope(bad, ResourceCanaryResult(verdict="OK", reason="x"))
        assert "could not write envelope" in capsys.readouterr().err


# ----------------------------------------------------------- state-dir contract


class TestStateDirContract:
    """One state dir, three consumers — pinned together (honest_failure_modes #5)."""

    LEAF = os.path.join("meshforge", "gateway_resource_canary")

    def test_canary_default_dir_tail(self):
        assert default_state_dir().endswith(self.LEAF)

    def test_canary_and_probe_share_the_leaf(self):
        from utils.watchdog_probes_gateway import RESOURCE_CANARY_STATE_LEAF
        assert STATE_DIR_LEAF == RESOURCE_CANARY_STATE_LEAF == (
            "gateway_resource_canary")

    def test_wrapper_default_state_dir_matches(self):
        wrapper = (Path(__file__).resolve().parent.parent / "scripts"
                   / "gateway_resource_canary.sh").read_text()
        # The wrapper's STATE_DIR default must end in the same leaf the canary
        # and probe use, under ~/.local/state.
        assert "meshforge/gateway_resource_canary" in wrapper


# ---------------------------------------------------------------- the probe


from utils.watchdog_probes_gateway import (  # noqa: E402
    probe_resource_canary_degraded,
)


def _write_verdict(state_dir, *, verdict="OK", reason="ok", seq=1,
                   reply_bytes=512, control_back=True, resource_back=True,
                   raw=None, age_s=0.0, now=None):
    """Write last.json into state_dir; age_s/now sets mtime = now - age_s."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "last.json")
    if raw is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(raw)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "verdict": verdict, "reason": reason, "seq": seq,
                "reply_bytes": reply_bytes, "control_back": control_back,
                "resource_back": resource_back, "peer": "meshanchor-server",
            }, fh)
    if now is not None:
        os.utime(path, (now - age_s, now - age_s))
    return path


class TestProbeResourceCanary:
    NOW = 1_000_000.0

    def test_inert_when_dir_absent(self, tmp_path):
        sig = probe_resource_canary_degraded(
            state_dir=str(tmp_path / "nope"),
            debounce_path=str(tmp_path / "s.json"))
        assert sig is None

    def test_inert_when_no_envelope(self, tmp_path):
        sdir = tmp_path / "gateway_resource_canary"
        sdir.mkdir()
        sig = probe_resource_canary_degraded(
            state_dir=str(sdir), debounce_path=str(tmp_path / "s.json"))
        assert sig is None

    def test_healthy_ok_no_fire(self, tmp_path):
        sdir = str(tmp_path / "grc")
        _write_verdict(sdir, verdict="OK", age_s=60, now=self.NOW)
        sig = probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=str(tmp_path / "s.json"))
        assert sig is None

    def test_fail_verdict_fires_after_debounce(self, tmp_path):
        sdir = str(tmp_path / "grc")
        sp = str(tmp_path / "s.json")
        _write_verdict(sdir, verdict="FAIL",
                       reason="RESOURCE round-trip BROKEN ... EROFS signature",
                       control_back=True, resource_back=False,
                       age_s=60, now=self.NOW)
        # First tick: candidate, debounce suppresses.
        assert probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp) is None
        # Second tick: fires.
        sig = probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp)
        assert sig is not None
        assert sig.cls == "resource_canary_degraded"
        assert sig.severity == "degraded"
        assert sig.subject == "meshforge-gateway"
        assert sig.issue_ref is None
        assert "EROFS signature" in sig.detail
        assert sig.extra["control_back"] is True
        assert sig.extra["resource_back"] is False

    def test_concern_verdict_fires(self, tmp_path):
        sdir = str(tmp_path / "grc")
        sp = str(tmp_path / "s.json")
        _write_verdict(sdir, verdict="CONCERN", reason="echo peer down",
                       control_back=False, resource_back=False,
                       age_s=60, now=self.NOW)
        assert probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp) is None
        sig = probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp)
        assert sig is not None and "CONCERN" in sig.detail

    def test_silence_fires_regardless_of_old_verdict(self, tmp_path):
        """A present-but-stale envelope (timer dark) fires even if its old
        verdict was OK — staleness must win over a stale-trustworthy value."""
        sdir = str(tmp_path / "grc")
        sp = str(tmp_path / "s.json")
        _write_verdict(sdir, verdict="OK", age_s=4 * 3600, now=self.NOW)
        assert probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp) is None
        sig = probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp)
        assert sig is not None and "DARK" in sig.detail

    def test_unparseable_fresh_rides_debounce(self, tmp_path):
        sdir = str(tmp_path / "grc")
        sp = str(tmp_path / "s.json")
        _write_verdict(sdir, raw='{"verdict": "FA', age_s=10, now=self.NOW)
        assert probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp) is None
        # Writer finished — file now valid + OK → streak resets, no fire.
        _write_verdict(sdir, verdict="OK", age_s=5, now=self.NOW)
        assert probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp) is None

    def test_verdict_absent_is_indeterminate_held(self, tmp_path):
        """A parseable fresh file with NO verdict key must NOT read as healthy
        AND must NOT fire — indeterminate is held (a shape regression is not OK)."""
        sdir = str(tmp_path / "grc")
        sp = str(tmp_path / "s.json")
        _write_verdict(sdir, raw='{"seq": 5, "reply_bytes": 512}',
                       age_s=60, now=self.NOW)
        # Held across two ticks — never fires, never resets a real streak.
        assert probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp) is None
        assert probe_resource_canary_degraded(
            state_dir=sdir, now=self.NOW, debounce_path=sp) is None
