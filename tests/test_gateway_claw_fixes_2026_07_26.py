"""Pins for the 2026-07-26 verified review fixes (gateway + claw battery).

C1  broadcast-bridge synthetic ACK: a propagation-node hand-off (LXMessage
    state SENT) must NOT emit "[delivered:...]" to the originating mesh sender
    — only a true recipient proof (state DELIVERED) may.
C2  Subscriber desired_state: STALE/DEAD ("was alive, went dark") require BOTH
    the age gate AND consecutive_failures >= threshold — one transient failure
    after a quiet week must not flip HEALTHY→DEAD.
C3  claw battery series: append_sample returns a tri-state witness string; an
    io_error is logged (once per path) by the liveness caller; a FROZEN series
    (append failing) makes the trend unobservable instead of serving the stale
    curve — and must not keep suppressing a real drain via soak "expected".
C4  node heard-time: MeshCore ISO-8601 strings parse (#51 shape); genuine
    garbage and absent/0 lastHeard land on the can't-attribute leg, never
    "online / heard now".
C5  message_queue.cleanup_stale: the DROPPED witness derives from what the
    UPDATE actually changed — a row concurrently mark_delivered'ed between
    observation and update is not witnessed as dropped.
C6  claw battery _clean/read_series: a backward wall-clock step is DROPPED,
    not re-sorted into fake chronology — a discharging pack must never
    classify as CHARGING off a backward-stepped pair.
A2  probe_cron_verdict_stale: a fast cron whose single FAIL verdict itself
    goes stale (the cron died after failing) routes to the silence leg, not
    to "unconfirmed" forever.
A6  _prior_verdict_statuses: a line whose first token is not an ISO timestamp
    is skipped (same rule as fleet_snapshot._parse_cron_verdicts), so garbage
    cannot seed a prior FAIL and falsely confirm a first failure.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import gateway.message_queue as mq_mod
import utils.watchdog_probes_liveness as wl
from gateway.config import MeshtasticBroadcastConfig
from gateway.message_queue import PersistentMessageQueue
from gateway.meshtastic_broadcast_bridge import (
    MeshtasticBroadcastBridge,
    STATE_DEAD,
    STATE_DEGRADED,
    STATE_HEALTHY,
    STATE_STALE,
    Subscriber,
    SubscriberStore,
    _DEAD_SINCE_SUCCESS_SEC,
    _DEGRADED_FAIL_THRESHOLD,
    _STALE_SINCE_SUCCESS_SEC,
)
from gateway.node_models import UnifiedNode
from utils import claw_battery as cb


NOW = 2_000_000_000.0
HR = 3600.0


# ---------------------------------------------------------------------------
# C1 — synthetic ACK honesty (bridge fixtures mirror
# tests/test_meshtastic_broadcast_bridge.py)
# ---------------------------------------------------------------------------


@dataclass
class _FakeBridged:
    source_network: str = "meshtastic"
    source_id: str = "!abcd1234"
    destination_id: Optional[str] = None
    content: str = "ping"
    is_broadcast: bool = True
    metadata: Dict[str, Any] = field(default_factory=lambda: {"channel": 0})


def _fake_rns_lxmf():
    rns = MagicMock(name="RNS")
    rns.Identity = MagicMock()
    rns.Identity.from_file = MagicMock(side_effect=Exception("no file"))
    rns.Identity.return_value = MagicMock(name="identity")
    rns.Transport = MagicMock()
    rns.Transport.has_path = MagicMock(return_value=True)
    rns.Identity.recall = MagicMock(return_value=MagicMock(name="dest_identity"))
    rns.Destination = MagicMock()
    rns.Destination.OUT = "OUT"
    rns.Destination.SINGLE = "SINGLE"
    lxmf = MagicMock(name="LXMF")
    lxmf.LXMessage = MagicMock(name="LXMessage")
    router = MagicMock(name="LXMRouter")
    src = MagicMock()
    src.hash = bytes.fromhex("aabbccddeeff0011")
    router.register_delivery_identity.return_value = src
    lxmf.LXMRouter.return_value = router
    return rns, lxmf, router


class _FakeReceipt:
    def __init__(self, state):
        self.state = state


def _ack_bridge(tmp_path, fake, emit_cb):
    rns, lxmf, _router = fake
    queue = MagicMock()
    queue.register_pending_ack = MagicMock(return_value=True)
    cfg = MeshtasticBroadcastConfig(
        enabled=True, channels=[0], display_name="T",
        announce_interval_sec=0,
        prefix_format="[meshtastic ch{channel}:{sender}] {text}",
        autosubscribe=False, ack_required=True,
    )
    return MeshtasticBroadcastBridge(
        broadcast_config=cfg, rns_module=rns, lxmf_module=lxmf,
        identity_path=tmp_path / "identity", db_path=tmp_path / "subs.db",
        storage_path=tmp_path / "storage",
        persistent_queue=queue, ack_emit_callback=emit_cb,
    )


def _grab_delivery_cb(fake):
    _rns, lxmf, _router = fake
    return lxmf.LXMessage.return_value.register_delivery_callback.call_args.args[0]


@pytest.mark.usefixtures("allow_rns_tx")
class TestSyntheticAckHonesty:
    """C1: [delivered] to a human requires a DELIVERED receipt, not a SENT
    (propagation hand-off) — the same event that records the subscriber
    UNCONFIRMED must not tell the mesh sender the message was delivered."""

    def test_sent_state_does_not_emit_delivered_ack(self, tmp_path):
        fake = _fake_rns_lxmf()
        emit_cb = MagicMock()
        b = _ack_bridge(tmp_path, fake, emit_cb)
        b.start()
        try:
            b._subs.add("aaaa000000000001")
            b.on_meshtastic_message(_FakeBridged())
            on_delivered = _grab_delivery_cb(fake)
            on_delivered(_FakeReceipt(state=4))   # LXMessage.SENT (propagated)
            emit_cb.assert_not_called()
        finally:
            b.stop()

    def test_delivered_state_emits_delivered_ack(self, tmp_path):
        fake = _fake_rns_lxmf()
        emit_cb = MagicMock()
        b = _ack_bridge(tmp_path, fake, emit_cb)
        b.start()
        try:
            b._subs.add("aaaa000000000001")
            b.on_meshtastic_message(_FakeBridged())
            on_delivered = _grab_delivery_cb(fake)
            on_delivered(_FakeReceipt(state=8))   # LXMessage.DELIVERED
            emit_cb.assert_called_once()
            assert emit_cb.call_args.args[1] == "delivered"
        finally:
            b.stop()


# ---------------------------------------------------------------------------
# C2 — desired_state two-evidence rule for STALE/DEAD
# ---------------------------------------------------------------------------


def _sub(**kw) -> Subscriber:
    base = dict(lxmf_hash="deadbeef00112233", added_at=datetime(2026, 1, 1))
    base.update(kw)
    return Subscriber(**base)


class TestDesiredStateTwoEvidenceRule:
    def test_one_transient_failure_after_quiet_week_is_not_dead(self):
        # The red-first case: 8 quiet days + ONE failure read DEAD, while the
        # identical history with zero failures read HEALTHY.
        now = datetime(2026, 7, 26)
        sub = _sub(last_confirmed_at=now - timedelta(days=8),
                   consecutive_failures=1)
        state = SubscriberStore.desired_state(sub, now)
        assert state != STATE_DEAD
        assert state == STATE_HEALTHY

    def test_subthreshold_failures_past_stale_age_stay_healthy(self):
        now = datetime(2026, 7, 26)
        sub = _sub(
            last_confirmed_at=now - timedelta(seconds=_STALE_SINCE_SUCCESS_SEC + 60),
            consecutive_failures=_DEGRADED_FAIL_THRESHOLD - 1)
        assert SubscriberStore.desired_state(sub, now) == STATE_HEALTHY

    def test_threshold_failures_and_dead_age_is_dead(self):
        now = datetime(2026, 7, 26)
        sub = _sub(
            last_confirmed_at=now - timedelta(seconds=_DEAD_SINCE_SUCCESS_SEC + 60),
            consecutive_failures=_DEGRADED_FAIL_THRESHOLD)
        assert SubscriberStore.desired_state(sub, now) == STATE_DEAD

    def test_threshold_failures_and_stale_age_is_stale(self):
        now = datetime(2026, 7, 26)
        sub = _sub(
            last_confirmed_at=now - timedelta(seconds=_STALE_SINCE_SUCCESS_SEC + 60),
            consecutive_failures=_DEGRADED_FAIL_THRESHOLD)
        assert SubscriberStore.desired_state(sub, now) == STATE_STALE

    def test_threshold_failures_fresh_success_is_degraded(self):
        now = datetime(2026, 7, 26)
        sub = _sub(last_confirmed_at=now - timedelta(hours=1),
                   consecutive_failures=_DEGRADED_FAIL_THRESHOLD)
        assert SubscriberStore.desired_state(sub, now) == STATE_DEGRADED


# ---------------------------------------------------------------------------
# C3 — series append witness + frozen-series trend honesty
# ---------------------------------------------------------------------------


class TestAppendSampleTriState:
    def test_written_then_duplicate(self, tmp_path):
        p = cb.series_path(str(tmp_path), "d")
        assert cb.append_sample(p, NOW, 4.0) == "written"
        assert cb.append_sample(p, NOW, 4.0) == "duplicate"

    def test_unusable_reading_is_named(self, tmp_path):
        p = cb.series_path(str(tmp_path), "d")
        assert cb.append_sample(p, NOW, None) == "unusable"
        assert cb.append_sample(p, NOW, True) == "unusable"

    def test_io_error_is_distinct_from_duplicate(self, tmp_path):
        d = tmp_path / "ro"
        d.mkdir()
        p = str(d / "s.jsonl")
        os.chmod(d, 0o500)
        try:
            assert cb.append_sample(p, NOW, 4.0) == "io_error"
        finally:
            os.chmod(d, 0o700)


class TestAppendIoErrorWitnessed:
    def test_io_error_logged_once_per_path(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(wl.claw_battery, "append_sample",
                            lambda *a, **k: "io_error")
        wl._SERIES_APPEND_IO_ERROR_WARNED.clear()
        tick = {"device": "dudeclaw-08", "battery": {"volts": 3.9},
                "captured_at": NOW}
        with caplog.at_level(logging.ERROR,
                             logger="utils.watchdog_probes_liveness"):
            wl._claw_battery_view(tick, state_dir=str(tmp_path), now=NOW,
                                  soak_armed=False)
            wl._claw_battery_view(tick, state_dir=str(tmp_path), now=NOW,
                                  soak_armed=False)
        errs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errs) == 1
        assert "append" in errs[0].getMessage()


class TestFrozenSeriesTrendUnobservable:
    def _freeze_series(self, state_dir, device):
        """A declining curve whose newest row is well past the stale window."""
        p = cb.series_path(state_dir, device)
        end = NOW - (wl.CLAW_TICK_STALE_S + 4 * HR)
        for i in range(25):
            cb.append_sample(p, end - (24 - i) * 1800.0, 4.0 - i * 0.02)
        return p

    def test_stale_series_yields_unobservable_trend(self, tmp_path, monkeypatch):
        dev = "dudeclaw-09"
        self._freeze_series(str(tmp_path), dev)
        monkeypatch.setattr(wl.claw_battery, "append_sample",
                            lambda *a, **k: "io_error")
        tick = {"device": dev, "battery": {"volts": 3.45}, "captured_at": NOW}
        view = wl._claw_battery_view(tick, state_dir=str(tmp_path), now=NOW,
                                     soak_armed=True)
        # The live reading, not the frozen curve's newest row.
        assert view["volts"] == 3.45
        assert view["slope_v_per_hr"] is None
        assert view["expected"] is False
        assert "series stale" in view["summary"]

    def test_frozen_curve_does_not_suppress_soak_expected(self, tmp_path,
                                                          monkeypatch):
        dev = "dudeclaw-09"
        sp = str(tmp_path / "bat_debounce.json")
        self._freeze_series(str(tmp_path), dev)
        monkeypatch.setattr(wl.claw_battery, "append_sample",
                            lambda *a, **k: "io_error")
        tick = {"device": dev, "reachable": True,
                "battery": {"volts": 3.30}, "captured_at": NOW}
        wl.probe_claw_battery_low(ticks=[tick], now=NOW, state_path=sp,
                                  soak_devices={dev})
        sig = wl.probe_claw_battery_low(ticks=[tick], now=NOW, state_path=sp,
                                        soak_devices={dev})
        assert sig is not None, (
            "a frozen (append-failing) series must not keep suppressing a "
            "real drain via soak 'expected'")
        assert sig.cls == "claw_battery_low"


# ---------------------------------------------------------------------------
# C4 — heard-time honesty (ISO strings parse; garbage/absent is not heard-now)
# ---------------------------------------------------------------------------


class TestResolveHeardIsoStrings:
    def _mc(self, **extra):
        base = {"adv_name": "MC", "pubkey_prefix": "aa11bb22cc33"}
        base.update(extra)
        return UnifiedNode.from_meshcore(base)

    def test_stale_iso_string_reads_offline(self):
        stale_iso = (datetime.now() - timedelta(hours=2)).isoformat()
        node = self._mc(last_seen=stale_iso)
        assert node.is_online is False
        assert node.last_seen is not None
        assert (datetime.now() - node.last_seen) > timedelta(hours=1)

    def test_recent_iso_string_reads_online(self):
        recent_iso = (datetime.now() - timedelta(minutes=2)).isoformat()
        node = self._mc(last_seen=recent_iso)
        assert node.is_online is True

    def test_garbage_heard_time_is_not_heard_now(self):
        node = self._mc(last_seen="certainly-not-a-date")
        assert node.is_online is False
        assert node.last_seen is None

    def test_live_advertisement_still_heard_now(self):
        node = self._mc()
        assert node.is_online is True


class TestFromMeshtasticAbsentLastHeard:
    def _node(self, **extra):
        base = {"num": 0x12345678,
                "user": {"longName": "X", "shortName": "X"}}
        base.update(extra)
        return UnifiedNode.from_meshtastic(base)

    def test_absent_lastheard_is_not_online(self):
        node = self._node()
        assert node.is_online is False
        assert node.last_seen is None

    def test_zero_lastheard_is_not_online(self):
        node = self._node(lastHeard=0)
        assert node.is_online is False
        assert node.last_seen is None


# ---------------------------------------------------------------------------
# C5 — cleanup_stale witness derives from what the UPDATE changed
# ---------------------------------------------------------------------------


class TestCleanupStaleWitnessDerivesFromUpdate:
    @staticmethod
    def _make_stale(q, msg_id):
        q.mark_in_progress(msg_id)
        old = (datetime.now() - timedelta(seconds=600)).isoformat()
        with q._get_connection() as conn:
            conn.execute("UPDATE messages SET updated_at = ? WHERE id = ?",
                         (old, msg_id))

    @staticmethod
    def _status(q, msg_id):
        with q._get_connection() as conn:
            return conn.execute(
                "SELECT status FROM messages WHERE id = ?",
                (msg_id,)).fetchone()["status"]

    def test_concurrently_delivered_row_not_witnessed_dropped(
            self, tmp_path, monkeypatch):
        q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
        mid = q.enqueue({"text": "x"}, "meshtastic", max_retries=1)
        self._make_stale(q, mid)
        # Force the per-row fallback so the observe→update window exists,
        # then simulate a concurrent mark_delivered inside it.
        monkeypatch.setattr(mq_mod, "_SQLITE_SUPPORTS_RETURNING", False)
        orig = PersistentMessageQueue._stale_exhausted_candidates

        def race(self_q, conn, cutoff):
            rows = orig(self_q, conn, cutoff)
            conn.execute("UPDATE messages SET status = 'delivered' "
                         "WHERE id = ?", (mid,))
            return rows

        monkeypatch.setattr(PersistentMessageQueue,
                            "_stale_exhausted_candidates", race)
        drops = []
        monkeypatch.setattr(mq_mod._dc, "record",
                            lambda *a, **k: drops.append((a, k)))
        assert q.cleanup_stale() == 0
        assert self._status(q, mid) == "delivered"
        assert drops == [], "a delivered row must not be witnessed as DROPPED"
        assert q.get_stats()["failed"] == 0

    def test_fallback_path_still_dead_letters_and_witnesses(
            self, tmp_path, monkeypatch):
        q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
        mid = q.enqueue({"text": "x"}, "meshtastic", max_retries=1)
        self._make_stale(q, mid)
        monkeypatch.setattr(mq_mod, "_SQLITE_SUPPORTS_RETURNING", False)
        assert q.cleanup_stale() == 1
        assert self._status(q, mid) == "dead_letter"
        assert q.get_stats()["failed"] == 1

    @pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35, 0),
                        reason="RETURNING needs SQLite >= 3.35")
    def test_returning_path_dead_letters_and_witnesses(self, tmp_path):
        q = PersistentMessageQueue(db_path=str(tmp_path / "q.db"))
        mid = q.enqueue({"text": "x"}, "meshtastic", max_retries=1)
        self._make_stale(q, mid)
        assert mq_mod._SQLITE_SUPPORTS_RETURNING is True
        assert q.cleanup_stale() == 1
        assert self._status(q, mid) == "dead_letter"
        assert q.get_stats()["failed"] == 1


# ---------------------------------------------------------------------------
# C6 — backward wall-clock step is dropped, never re-sorted into fake order
# ---------------------------------------------------------------------------


class TestBackwardStepNeverCharging:
    def _write(self, path, rows):
        with open(path, "w", encoding="utf-8") as fh:
            for ts, v in rows:
                fh.write(json.dumps({"ts": ts, "volts": v}) + "\n")

    def test_read_series_drops_backward_rows(self, tmp_path):
        p = str(tmp_path / "s.jsonl")
        rows = [(NOW - 3 * HR, 4.00), (NOW - 2 * HR, 3.95),
                (NOW - 1 * HR, 3.90), (NOW, 3.85),
                # clock stepped BACK ~11 h mid-run; pack still discharging
                (NOW - 11 * HR, 3.80), (NOW - 10 * HR, 3.75),
                (NOW - 9 * HR, 3.70), (NOW - 8 * HR, 3.65)]
        self._write(p, rows)
        kept = [r["ts"] for r in cb.read_series(p)]
        assert kept == [NOW - 3 * HR, NOW - 2 * HR, NOW - 1 * HR, NOW]

    def test_discharge_with_backward_step_never_classifies_charging(
            self, tmp_path):
        p = str(tmp_path / "s.jsonl")
        rows = [(NOW - 3 * HR, 4.00), (NOW - 2 * HR, 3.95),
                (NOW - 1 * HR, 3.90), (NOW, 3.85),
                (NOW - 11 * HR, 3.80), (NOW - 10 * HR, 3.75),
                (NOW - 9 * HR, 3.70), (NOW - 8 * HR, 3.65)]
        self._write(p, rows)
        view = cb.classify(cb.read_series(p))
        assert view["state"] != cb.CHARGING, (
            "a backward wall-clock step re-sorted into fake order fabricated "
            "CHARGING from a discharging pack")
        slope = view["slope_v_per_hr"]
        assert slope is None or slope <= 0


# ---------------------------------------------------------------------------
# A2 — a failing fast cron that then dies routes to the silence leg
# ---------------------------------------------------------------------------


class TestFastCronFailThenSilenceGoesStale:
    WIRED = ("*/5 * * * * /opt/job.py >/dev/null 2>&1; "
             "/opt/meshforge/scripts/cron_verdict.sh myjob $?\n")

    @staticmethod
    def _v(name, status, age_s):
        import datetime as _dt
        ts = NOW - age_s
        iso = _dt.datetime.fromtimestamp(
            ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{iso} {name} {status}\n"

    def test_single_fail_gone_stale_fires_as_silence(self, tmp_path):
        # A fast cron FAILs once, then never runs again: its unconfirmed FAIL
        # ages past the stale threshold (2h floor for */5). Pre-fix this was
        # deferred to "unconfirmed" forever — indeterminate for eternity.
        sp = str(tmp_path / "d.json")
        verdicts = self._v("myjob", "FAIL(1)", 3 * 3600)
        wl.probe_cron_verdict_stale(crontab_text=self.WIRED,
                                    verdicts_text=verdicts,
                                    now=NOW, state_path=sp)
        sig = wl.probe_cron_verdict_stale(crontab_text=self.WIRED,
                                          verdicts_text=verdicts,
                                          now=NOW, state_path=sp)
        assert sig is not None, (
            "a failing fast cron that then died must be flagged by the "
            "silence leg, not parked as unconfirmed forever")
        assert any("myjob" in s for s in sig.extra["stale"])
        assert not sig.extra["failed"]

    def test_fresh_single_fail_still_withheld(self, tmp_path):
        # Guard: the unconfirmed-first-failure suppression itself stays.
        sp = str(tmp_path / "d.json")
        verdicts = self._v("myjob", "FAIL(1)", 60)
        for _ in range(2):
            sig = wl.probe_cron_verdict_stale(crontab_text=self.WIRED,
                                              verdicts_text=verdicts,
                                              now=NOW, state_path=sp)
        assert sig is None


# ---------------------------------------------------------------------------
# A6 — prior-verdict parsing rejects lines without an ISO timestamp
# ---------------------------------------------------------------------------


class TestPriorVerdictStatusesIsoValidation:
    def test_garbage_first_token_cannot_seed_prior(self):
        text = ("garbage myjob FAIL x\n"
                "2026-07-26T00:00:00Z myjob FAIL real\n")
        assert "myjob" not in wl._prior_verdict_statuses(text)

    def test_valid_lines_still_step_including_z_suffix(self):
        text = ("2026-07-26T00:00:00Z myjob FAIL a\n"
                "2026-07-26T00:05:00Z myjob OK b\n")
        assert wl._prior_verdict_statuses(text) == {"myjob": "FAIL"}
