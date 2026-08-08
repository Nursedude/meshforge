"""Fleet-truth Phase 0 — producer coverage dispositions.

The watchdog producer now emits a per-``SIGNAL_CLASSES`` disposition map
(``coverage`` key of watchdog.json), assembled from the per-tick disposition
recorder in ``watchdog_probe_core``. The contract under test is FAIL-DARK:

- a class nothing noted is ``unknown`` (renders dark) — silence never green;
- an active Signal outranks any note;
- worst-wins merge (indeterminate > inert > clean) per class per tick;
- every member of the closed enum appears in the map (a silently-missing
  class would be indistinguishable from "not watched");
- the ``coverage`` key rides watchdog.json → ``/api/status.watchdog`` →
  ``fleet_truth.merge_coverage`` unchanged, and its absence (legacy writer)
  keeps the pre-Phase-0 all-dark behavior.
"""

import json
import time
from pathlib import Path

import pytest

from utils.watchdog_probe_core import (
    SIGNAL_CLASSES,
    Signal,
    collect_dispositions,
    note_disposition,
    reset_dispositions,
)
from utils.watchdog_runner import build_coverage, write_state


@pytest.fixture(autouse=True)
def _clean_recorder():
    reset_dispositions()
    yield
    reset_dispositions()


class TestDispositionRecorder:
    def test_note_and_collect(self):
        note_disposition("service_inactive", "clean")
        got = collect_dispositions()
        assert got["service_inactive"] == {"disp": "clean"}

    def test_reason_carried(self):
        note_disposition("parity_drift", "inert", reason="no sister repo on this box")
        assert collect_dispositions()["parity_drift"] == {
            "disp": "inert", "reason": "no sister repo on this box"}

    def test_worst_wins_upgrade(self):
        note_disposition("service_inactive", "clean")
        note_disposition("service_inactive", "indeterminate", reason="one unit unreadable")
        assert collect_dispositions()["service_inactive"]["disp"] == "indeterminate"

    def test_worst_wins_never_downgrades(self):
        note_disposition("service_inactive", "indeterminate", reason="x")
        note_disposition("service_inactive", "clean")
        assert collect_dispositions()["service_inactive"]["disp"] == "indeterminate"

    def test_inert_outranks_clean(self):
        note_disposition("host_frozen", "clean")
        note_disposition("host_frozen", "inert", reason="not the brain box")
        assert collect_dispositions()["host_frozen"]["disp"] == "inert"

    def test_invalid_disposition_becomes_indeterminate_not_clean(self):
        """A programming error must never read as a healthy-looking value."""
        note_disposition("service_inactive", "helthy")  # typo'd disp
        got = collect_dispositions()["service_inactive"]
        assert got["disp"] == "indeterminate"
        assert "invalid disposition" in got["reason"]

    def test_reset_clears(self):
        note_disposition("service_inactive", "clean")
        reset_dispositions()
        assert collect_dispositions() == {}


class TestBuildCoverage:
    def test_every_signal_class_present(self):
        """The coverage completeness gate: every member of the closed enum
        gets an entry — pre-noted or not."""
        cov = build_coverage([])
        assert set(cov.keys()) == set(SIGNAL_CLASSES)

    def test_unnoted_class_is_unknown_never_clean(self):
        cov = build_coverage([])
        for cls, entry in cov.items():
            assert entry["disp"] == "unknown", f"{cls} defaulted to {entry['disp']}"

    def test_active_signal_wins_over_note(self):
        note_disposition("service_inactive", "clean")
        sig = Signal(cls="service_inactive", subject="u.service",
                     severity="degraded", detail="d")
        cov = build_coverage([sig])
        assert cov["service_inactive"]["disp"] == "active"

    def test_noted_dispositions_pass_through(self):
        note_disposition("parity_drift", "inert", reason="no sister repo")
        note_disposition("cron_verdict_stale", "clean")
        note_disposition("channel_feed_dark", "indeterminate", reason="no json uplink")
        cov = build_coverage([])
        assert cov["parity_drift"] == {"disp": "inert", "reason": "no sister repo"}
        assert cov["cron_verdict_stale"] == {"disp": "clean"}
        assert cov["channel_feed_dark"]["disp"] == "indeterminate"


class TestWriteStateCoverage:
    def _write(self, tmp_path: Path, **kw) -> dict:
        out = tmp_path / "watchdog.json"
        write_state(out, host="testbox", now=time.time(), probe_count=1,
                    active_signals=[], **kw)
        return json.loads(out.read_text())

    def test_coverage_emitted_when_passed(self, tmp_path):
        payload = self._write(
            tmp_path, coverage={"service_inactive": {"disp": "clean"}})
        assert payload["coverage"] == {"service_inactive": {"disp": "clean"}}

    def test_legacy_shape_unchanged_when_coverage_none(self, tmp_path):
        payload = self._write(tmp_path)
        assert "coverage" not in payload


class TestWatchdogBlockPassthrough:
    """/api/status.watchdog carries the coverage map verbatim; absence on a
    legacy writer keeps the pre-Phase-0 (all-dark) consumer behavior."""

    def _block(self, tmp_path, payload: dict) -> dict:
        from utils.map_http_handler import MapRequestHandler
        state = tmp_path / "watchdog.json"
        state.write_text(json.dumps(payload))
        h = MapRequestHandler.__new__(MapRequestHandler)
        h._WATCHDOG_STATE_PATH = state
        return h._read_watchdog_block()

    def test_coverage_passed_through(self, tmp_path):
        cov = {"service_inactive": {"disp": "clean"},
               "parity_drift": {"disp": "inert", "reason": "no sister repo"}}
        block = self._block(tmp_path, {"ts": time.time(), "ok": True,
                                       "signals": [], "coverage": cov})
        assert block["coverage"] == cov

    def test_absent_coverage_absent_in_block(self, tmp_path):
        block = self._block(tmp_path, {"ts": time.time(), "ok": True, "signals": []})
        assert "coverage" not in block

    def test_malformed_coverage_dropped_not_crashed(self, tmp_path):
        block = self._block(tmp_path, {"ts": time.time(), "ok": True,
                                       "signals": [], "coverage": "garbage"})
        assert "coverage" not in block
        assert block["ok"] is True


class TestMergeCoverageProducerReasons:
    """fleet_truth.merge_coverage consumes the producer map: clean counts
    green; producer reasons ride through to the rendered cell."""

    def test_producer_clean_counts_green(self):
        from utils.fleet_truth import merge_coverage
        wb = {"installed": True, "ok": True, "signals": [],
              "coverage": {"a": {"disp": "clean"}, "b": {"disp": "inert",
                           "reason": "organ absent by role"}}}
        cov = merge_coverage(wb, ["a", "b", "c"])
        assert cov["green"] == 1 and cov["dark"] == 2
        assert cov["classes"]["a"]["disp"] == "clean"
        assert cov["classes"]["b"]["reason"] == "organ absent by role"
        assert cov["classes"]["c"]["disp"] == "unknown"

    def test_indeterminate_reason_passthrough(self):
        from utils.fleet_truth import merge_coverage
        wb = {"installed": True, "ok": True, "signals": [],
              "coverage": {"a": {"disp": "indeterminate",
                                 "reason": "journal unavailable"}}}
        cov = merge_coverage(wb, ["a"])
        assert cov["classes"]["a"] == {"disp": "indeterminate",
                                      "reason": "journal unavailable"}
        assert cov["dark"] == 1 and cov["green"] == 0


class TestRunnerGateInertNotes:
    """run_all_probes notes inert for the probes its own gates skip. Probes
    are stubbed to isolate the runner's gate behavior."""

    def test_no_rns_instance_notes_inert(self, monkeypatch):
        import utils.watchdog_runner as wr
        # Stub every probe call to a no-op so only runner-level notes land.
        for name in dir(wr):
            if name.startswith("probe_"):
                fn = getattr(wr, name)
                if callable(fn):
                    # Probes returning a LIST (0..N signals) must be stubbed with
                    # [] — the runner `extend`s them and is deliberately NOT
                    # defensive, so a None here would TypeError. Adding a new
                    # list-returning probe MUST be declared in this tuple; that
                    # is the closed-consumer half of the closed enum.
                    ret = [] if name in ("probe_lxmf_process_wedge",
                                         "probe_tracer_peer_unreachable",
                                         "probe_memory_cap_engaged") else None
                    monkeypatch.setattr(wr, name, lambda *a, _r=ret, **k: _r)
        monkeypatch.setattr(wr, "run_rnstatus", lambda **k: None)
        signals = wr.run_all_probes(
            rns_instance_name=None,
            services_expected_active=(),   # no map, no rnsd expected
            services_wedge_check=(),
        )
        cov = wr.build_coverage(signals)
        assert cov["rns_namespace_collision"]["disp"] == "inert"
        assert cov["rns_shared_instance_unresponsive"]["disp"] == "inert"
        assert cov["http_local_unresponsive"]["disp"] == "inert"
        assert cov["fd_exhaustion"]["disp"] == "inert"
        assert cov["phoneapi_tcp_leak"]["disp"] == "inert"
        assert cov["aredn_source_dark"]["disp"] == "inert"
        assert cov["foundation_perms_drift"]["disp"] == "inert"
        assert cov["rns_version_drift"]["disp"] == "inert"


# ─────────────────────────────────────────────────────────────────────
# 2026-07-19 adversarial-review fixes — probe disposition honesty
# (S1/S2/S3/S4 in watchdog_probes_service, D1/D2/D3 in
# watchdog_probes_drift): degraded observations must never note clean,
# and a blind tick must HOLD debounce state, not reset it.
# ─────────────────────────────────────────────────────────────────────

import os
from unittest.mock import patch

from utils.watchdog_probes_service import (
    _estab_inodes_to_port,
    probe_http_local,
    probe_meshtasticd_phoneapi_wedge,
    probe_phoneapi_tcp_leak,
)
from utils.watchdog_probes_tracer import probe_tracer_peer_unreachable
from utils.watchdog_probes_drift import (
    probe_dep_install_fragmented,
    probe_mqtt_root_drift,
    probe_role_drift,
)


class TestEstabInodesUnreadableIsNone:
    """S1 helper contract: BOTH /proc/net/tcp{,6} unreadable → None
    (sockets unobservable); read-but-empty stays a real empty set."""

    def test_both_tables_unreadable_returns_none(self, tmp_path):
        # tmp_path has no net/ dir at all → both reads raise OSError.
        assert _estab_inodes_to_port(4403, proc_root=str(tmp_path)) is None

    def test_read_but_empty_returns_empty_set(self, tmp_path):
        net = tmp_path / "net"
        net.mkdir()
        header = ("  sl  local_address rem_address   st tx_queue rx_queue "
                  "tr tm->when retrnsmt   uid  timeout inode\n")
        (net / "tcp").write_text(header)
        (net / "tcp6").write_text(header)
        got = _estab_inodes_to_port(4403, proc_root=str(tmp_path))
        assert got == set()

    def test_one_table_readable_is_still_a_set(self, tmp_path):
        net = tmp_path / "net"
        net.mkdir()
        header = ("  sl  local_address rem_address   st tx_queue rx_queue "
                  "tr tm->when retrnsmt   uid  timeout inode\n")
        (net / "tcp").write_text(header)  # tcp6 absent — one success suffices
        assert _estab_inodes_to_port(4403, proc_root=str(tmp_path)) == set()


class TestPhoneapiLeakTablesUnreadable:
    """S1 in probe_phoneapi_tcp_leak: tables unobservable → indeterminate,
    and the persisted inode counts are HELD (not rewritten/reset)."""

    def _proc_with_fd_only(self, tmp_path, pid, inode):
        fd_dir = tmp_path / str(pid) / "fd"
        fd_dir.mkdir(parents=True)
        os.symlink(f"socket:[{inode}]", fd_dir / "15")
        return str(tmp_path)  # no net/ dir → tcp tables unreadable

    def test_indeterminate_and_state_held(self, tmp_path):
        root = self._proc_with_fd_only(tmp_path, 4242, 99001)
        sp = tmp_path / "state.json"
        seeded = {"pid": 4242, "inode_counts": {"99001": 19}}
        sp.write_text(json.dumps(seeded))
        sig = probe_phoneapi_tcp_leak(
            "meshforge-map.service", proc_root=root, main_pid=4242,
            state_path=str(sp), owner_fetch=lambda: (True, None),
        )
        assert sig is None
        got = collect_dispositions()["phoneapi_tcp_leak"]
        assert got["disp"] == "indeterminate"
        assert "unobservable" in got["reason"]
        # Debounce state HELD: the blind tick neither reset nor advanced it.
        assert json.loads(sp.read_text()) == seeded


class TestPhoneapiWedgeTablesUnreadable:
    """S1 in probe_meshtasticd_phoneapi_wedge: the harm-guard's tables
    unobservable → indeterminate WITHOUT executing the benign-churn streak
    reset (the no-contender path resets; the blind path must hold)."""

    def test_indeterminate_and_streak_not_reset(self, tmp_path):
        sp = tmp_path / "wedge.json"
        sp.write_text(json.dumps({"streak": 1}))  # one confirmed tick already
        sig = probe_meshtasticd_phoneapi_wedge(
            main_pid=1234, gateway_main_pid=5678, threshold=12,
            count_fn=lambda pattern: 40,          # over threshold
            state_path=str(sp),
            proc_root=str(tmp_path),              # no net/ dir → tables None
        )
        assert sig is None
        got = collect_dispositions()["meshtasticd_phoneapi_wedge"]
        assert got["disp"] == "indeterminate"
        assert "unobservable" in got["reason"]
        # Streak HELD at 1 — a blind tick is not a benign-churn observation.
        assert json.loads(sp.read_text())["streak"] == 1

    def test_no_contender_still_resets_streak(self, tmp_path):
        """Contrast pin: a READ-and-empty table set is a real benign-churn
        observation and keeps the pre-fix streak reset."""
        net = tmp_path / "net"
        net.mkdir()
        header = ("  sl  local_address rem_address   st tx_queue rx_queue "
                  "tr tm->when retrnsmt   uid  timeout inode\n")
        (net / "tcp").write_text(header)
        (net / "tcp6").write_text(header)
        sp = tmp_path / "wedge.json"
        sp.write_text(json.dumps({"streak": 1}))
        sig = probe_meshtasticd_phoneapi_wedge(
            main_pid=1234, gateway_main_pid=5678, threshold=12,
            count_fn=lambda pattern: 40,
            state_path=str(sp), proc_root=str(tmp_path),
        )
        assert sig is None
        assert collect_dispositions()["meshtasticd_phoneapi_wedge"]["disp"] == "clean"
        assert json.loads(sp.read_text())["streak"] == 0


class TestPhoneapiWedgeGatewayGateReason:
    """S3: the missing-gateway gate stays inert (legitimate common case;
    a dead gateway pages via service_inactive) but the reason admits the
    conflation with an unobservable unit state."""

    def test_inert_reason_names_unobservable_conflation(self, tmp_path):
        sig = probe_meshtasticd_phoneapi_wedge(
            main_pid=1234, gateway_main_pid=None,
            systemctl_path="/nonexistent/systemctl-xyz",
            threshold=12, count_fn=lambda pattern: 99,
            state_path=str(tmp_path / "wedge.json"),
        )
        assert sig is None
        got = collect_dispositions()["meshtasticd_phoneapi_wedge"]
        assert got["disp"] == "inert"
        assert "unit state unobservable" in got["reason"]


class TestHttpLocalHttpErrorIsAlive:
    """S4: urlopen RAISES HTTPError on non-2xx — but a responding server
    (any status) is an alive handler loop for this class, so a 503 must
    note clean, exactly like the 2xx path, not 'endpoint unobservable'."""

    def test_503_notes_clean_no_signal(self):
        from urllib.error import HTTPError
        def _raise(*args, **kwargs):
            raise HTTPError("http://127.0.0.1:5000/healthz", 503,
                            "Service Unavailable", {}, None)
        with patch("utils.watchdog_probes_service.urlopen", side_effect=_raise):
            sig = probe_http_local("meshforge-map.service")
        assert sig is None
        assert collect_dispositions()["http_local_unresponsive"]["disp"] == "clean"

    def test_connection_refused_still_indeterminate(self):
        from urllib.error import URLError
        def _raise(*args, **kwargs):
            raise URLError("[Errno 111] Connection refused")
        with patch("utils.watchdog_probes_service.urlopen", side_effect=_raise):
            sig = probe_http_local("meshforge-map.service")
        assert sig is None
        assert (collect_dispositions()["http_local_unresponsive"]["disp"]
                == "indeterminate")


class TestTracerAllRowsSkipped:
    """S2: fires present but EVERY result row unparseable → no peer was
    observed — indeterminate, never the all-peers-clean final note."""

    def _write_fire(self, tracer_dir, fire_at_unix, results):
        payload = {"schema_version": 1, "fire_at_unix": fire_at_unix,
                   "fire_at_iso": "2026-07-19T00:00:00Z",
                   "self_short": "moc1", "results": results}
        (tracer_dir / f"tracer-{int(fire_at_unix)}.json").write_text(
            json.dumps(payload))

    def test_all_rows_skipped_notes_indeterminate(self, tmp_path):
        now = time.time()
        # Rows are non-dict or lack a str peer — all silently skippable.
        self._write_fire(tmp_path, now - 300, ["garbage", 42])
        self._write_fire(tmp_path, now - 60, [{"seq": 1, "result": "ok"},
                                              {"peer": None, "result": "ok"}])
        signals = probe_tracer_peer_unreachable(tracer_dir=tmp_path, now=now)
        assert signals == []
        got = collect_dispositions()["tracer_peer_unreachable"]
        assert got["disp"] == "indeterminate"
        assert "unparseable" in got["reason"]

    def test_parseable_healthy_rows_still_clean(self, tmp_path):
        now = time.time()
        self._write_fire(tmp_path, now - 60,
                         [{"peer": "moc2", "seq": 1, "result": "ok"}])
        signals = probe_tracer_peer_unreachable(tracer_dir=tmp_path, now=now)
        assert signals == []
        assert collect_dispositions()["tracer_peer_unreachable"]["disp"] == "clean"


class TestDepFragmentUserScopeDark:
    """D1: with no resolvable non-root service user, _enumerate_pkg_installs
    silently skips the import-priority user-site/user-pipx locations — a
    would-be-clean exit must note indeterminate, not claim fragmentation
    impossible over locations never observed."""

    def test_root_service_user_all_agree_notes_indeterminate(self, tmp_path):
        with patch("utils.watchdog_probes_drift._enumerate_pkg_installs",
                   return_value={"venv": "2.7.9", "system-dist": "2.7.9"}):
            sig = probe_dep_install_fragmented(
                floor="2.7.9", service_user="root",
                state_path=str(tmp_path / "frag.json"), debounce_ticks=1)
        assert sig is None
        got = collect_dispositions()["dep_install_fragmented"]
        assert got["disp"] == "indeterminate"
        assert "user-scope" in got["reason"]

    def test_no_service_user_single_location_notes_indeterminate(self, tmp_path):
        with patch("utils.watchdog_probes_drift._enumerate_pkg_installs",
                   return_value={"venv": "2.7.9"}), \
             patch("utils.rns_tree_perms._read_rnsd_user",
                   return_value=None):
            sig = probe_dep_install_fragmented(
                floor="2.7.9",
                state_path=str(tmp_path / "frag.json"), debounce_ticks=1)
        assert sig is None
        got = collect_dispositions()["dep_install_fragmented"]
        assert got["disp"] == "indeterminate"
        assert "user-scope" in got["reason"]

    def test_nonroot_user_all_observed_stays_clean(self, tmp_path):
        with patch("utils.watchdog_probes_drift._enumerate_pkg_installs",
                   return_value={"venv": "2.7.9", "user-site": "2.7.9"}):
            sig = probe_dep_install_fragmented(
                floor="2.7.9", service_user="svc",
                state_path=str(tmp_path / "frag.json"), debounce_ticks=1)
        assert sig is None
        assert (collect_dispositions()["dep_install_fragmented"]["disp"]
                == "clean")

    def test_caller_supplied_installs_stays_clean(self, tmp_path):
        """Explicit installs= means the caller observed the locations —
        the user-scope-dark re-derivation only applies to self-enumeration."""
        sig = probe_dep_install_fragmented(
            floor="2.7.9", installs={"venv": "2.7.9", "system-dist": "2.7.9"},
            state_path=str(tmp_path / "frag.json"), debounce_ticks=1)
        assert sig is None
        assert (collect_dispositions()["dep_install_fragmented"]["disp"]
                == "clean")


class TestRoleDriftUndeclaredIndeterminate:
    """D2 lineage. This test PINNED the conflation: (None, {}) from
    `_read_deployment_declaration` meant BOTH genuinely-undeclared and
    unreadable/corrupt, so the note had to be the worse one.

    ⚠️ INVERTED 2026-08-07, the same way TestMqttRootNoLineIndeterminate below
    was on 08-05 — and found by the grep that lesson demanded ("a fix applied
    to one instance is not applied to the class"). That resolution left
    meshanchor-server, which has NO deployment.json because it is a MeshAnchor
    box and not MeshForge role-managed, permanently indeterminate on this
    class; a genuinely corrupt declaration on a real fleet box would have been
    invisible inside that standing noise. The reader is now tri-state
    (`_read_deployment_declaration_status`). An INJECTED deployment is a
    positive answer ("observed: no role"), so it is now INERT; the unreadable
    path is exercised against the real reader below."""

    def test_undeclared_box_is_inert(self, tmp_path):
        sig = probe_role_drift(deployment=(None, {}),
                               state_path=str(tmp_path / "rd.json"))
        assert sig is None
        got = collect_dispositions()["role_drift"]
        assert got["disp"] == "inert", (
            "a box that declares no MeshForge role has nothing to drift FROM "
            "— reporting that as a failed observation is what buried the real "
            "unreadable case")
        assert "no MeshForge role" in got["reason"]

    def test_unreadable_declaration_still_indeterminate(self, tmp_path,
                                                        monkeypatch):
        """The other half of the split, driven through the REAL reader: a
        service user that cannot be resolved is unobservable, not undeclared."""
        from utils import watchdog_probes_drift as drift
        monkeypatch.setattr(
            drift, "_read_deployment_declaration_status",
            lambda u: ("unreadable", None, {}))
        sig = probe_role_drift(state_path=str(tmp_path / "rd2.json"))
        assert sig is None
        got = collect_dispositions()["role_drift"]
        assert got["disp"] == "indeterminate"
        assert "unreadable" in got["reason"]


class TestMqttRootNoLineIndeterminate:
    """D3 lineage. This test PINNED the conflation: `_journal_newest_match`
    returns None for both no-match (RX-only box) and journalctl
    unavailable/timeout, and D3 resolved it by noting the pessimistic
    `indeterminate` for both.

    ⚠️ INVERTED 2026-08-05. That resolution left four fleet boxes sitting
    permanently indeterminate on a class they can never be in trouble for,
    and a real journal outage would have been invisible inside that noise
    (`detector_blind_any` was live on three of them). The probe now asks
    whether the channel WORKED before deciding what its silence means —
    `_journal_newest_match_status`. An INJECTED seam returning None is a
    positive answer ("nothing logged"), so it is now INERT; the
    unobservable path is exercised against the real reader in
    tests/test_watchdog_detector_blind_legs_2026_08_05.py
    (TestMqttRootDriftSilenceDisposition.test_broken_journal_stays_indeterminate).
    """

    def test_no_publish_line_from_a_working_channel_is_inert(self, tmp_path):
        sig = probe_mqtt_root_drift(
            main_pid=1002, newest_line_fn=lambda pattern: None,
            declared_root="msh", state_path=str(tmp_path / "mq.json"))
        assert sig is None
        got = collect_dispositions()["mqtt_root_drift"]
        assert got["disp"] == "inert", (
            "a channel that answered 'nothing logged' is an observation; "
            "calling it 'cannot observe' is what buried the RX-only boxes")
        assert "no MQTT JSON uplink" in got["reason"]


class TestDepWatchedTupleClosedConsumer:
    """Closed-enum gate for ``_DEP_VERSION_WATCHED`` (honest_failure_modes #7).

    ``probe_dep_install_fragmented`` watches exactly ONE package and reaches it
    as ``_DEP_VERSION_WATCHED[0]``. That is correct while the tuple is a
    singleton and silently wrong the moment it is not: a second entry would be
    floor-checked by ``probe_dep_version_drift`` but invisible to the
    fragmentation probe — a half-watched dep that reads as watched.

    Structural-dark row 3 ACCEPTED this scope as permanent (2026-07-19), so the
    tuple is expected to stay a singleton. This test exists for the day someone
    revisits that: it fails loudly and names the sites to fix, rather than
    relying on the next author reading the comment.
    """

    def test_indexed_call_sites_require_a_singleton_tuple(self):
        import re
        from utils.watchdog_probes_drift import _DEP_VERSION_WATCHED

        src_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "utils",
            "watchdog_probes_drift.py")
        with open(src_path) as f:
            lines = f.readlines()
        indexed = [i + 1 for i, ln in enumerate(lines)
                   if re.search(r"_DEP_VERSION_WATCHED\[0\]", ln)
                   and not ln.lstrip().startswith("#")]

        if len(_DEP_VERSION_WATCHED) > 1:
            assert not indexed, (
                f"_DEP_VERSION_WATCHED grew to {list(_DEP_VERSION_WATCHED)} but "
                f"{src_path} still indexes [0] at line(s) {indexed} — those "
                f"consumers watch ONLY the first package. Make them iterate (or "
                f"take an explicit package argument) before extending the tuple.")
        else:
            # Pin the precondition itself: if the [0] sites are ever refactored
            # away, this branch stops being load-bearing and the guard above is
            # what matters. Either state is fine; a silent mismatch is not.
            assert indexed, (
                "no _DEP_VERSION_WATCHED[0] call sites found — if the "
                "single-package coupling was refactored away, delete this test "
                "and the ⚠️ comment above the tuple so they can't rot apart.")


class TestClawEdgeHardwareProbes:
    """Structural-dark row 7 (2026-07-19): give the claw its own vocabulary.

    Origin: dudeclaw-02, a battery-powered claw and the fleet's out-of-band
    RF/host witness, drained to 2.41 V and went dark for 17.4 h. The spine's
    only words were "cron_verdict_stale: claw02_metrics FAIL — fix the job" —
    the capture cron's exit code was the sole downstream witness, so a dead
    radio node was laundered into an infrastructure-noise signal. These pin the
    behaviour that makes the claw's own state sayable.
    """

    def _tick(self, device="dudeclaw-02", reachable=True, volts=None,
              errors=None):
        t = {"device": device, "reachable": reachable, "errors": errors or {}}
        if volts is not None:
            t["battery"] = {"volts": volts, "raw": f"Battery: {volts} V"}
        return t

    # ---- claw_device_dark -------------------------------------------------
    def test_no_claw_on_box_is_inert_not_clean(self):
        from utils.watchdog_probes import probe_claw_device_dark
        assert probe_claw_device_dark(ticks=[], now=1000.0,
                                      state_path=self.sp) is None
        assert collect_dispositions()["claw_device_dark"]["disp"] == "inert"

    def test_dark_device_fires_after_debounce(self):
        from utils.watchdog_probes import probe_claw_device_dark
        t = [self._tick(reachable=False, errors={"device_info": "no reply"})]
        assert probe_claw_device_dark(ticks=t, now=1.0, state_path=self.sp) is None
        sig = probe_claw_device_dark(ticks=t, now=2.0, state_path=self.sp)
        assert sig is not None and sig.cls == "claw_device_dark"
        assert sig.subject == "dudeclaw-02" and sig.severity == "degraded"
        # The whole point: it must talk about the DEVICE, not the cron.
        assert "not answering" in sig.detail

    def test_reachable_device_is_clean(self):
        from utils.watchdog_probes import probe_claw_device_dark
        for _ in range(3):
            sig = probe_claw_device_dark(ticks=[self._tick()], now=1.0,
                                         state_path=self.sp)
        assert sig is None
        assert collect_dispositions()["claw_device_dark"]["disp"] == "clean"

    def test_legacy_tick_without_reachable_is_not_called_dark(self):
        """A tick from a pre-2026-07-19 capture has no `reachable` key. Absent
        evidence must not manufacture a dark claim."""
        from utils.watchdog_probes import probe_claw_device_dark
        legacy = [{"device": "dudeclaw-01"}]          # no reachable, no device_info
        for _ in range(3):
            assert probe_claw_device_dark(ticks=legacy, now=1.0,
                                          state_path=self.sp) is None

    # ---- claw_battery_low -------------------------------------------------
    def test_low_pack_fires_with_voltage_in_detail(self):
        from utils.watchdog_probes import probe_claw_battery_low
        t = [self._tick(volts=2.41)]
        assert probe_claw_battery_low(ticks=t, now=1.0, state_path=self.sp2) is None
        sig = probe_claw_battery_low(ticks=t, now=2.0, state_path=self.sp2)
        assert sig is not None and sig.cls == "claw_battery_low"
        assert "2.41" in sig.detail and sig.subject == "dudeclaw-02"

    def test_missing_gauge_is_indeterminate_never_charged(self):
        """No battery reading is NOT a low-battery claim AND NOT a healthy one
        — the #1 trap: an unknown must not land in either real-world bucket."""
        from utils.watchdog_probes import probe_claw_battery_low
        assert probe_claw_battery_low(ticks=[self._tick(volts=None)], now=1.0,
                                      state_path=self.sp2) is None
        assert (collect_dispositions()["claw_battery_low"]["disp"]
                == "indeterminate")

    def test_dark_device_does_not_also_page_battery(self):
        """One fault, one owner: an unreachable claw belongs to
        claw_device_dark; battery must not pile a second page on the same
        event using a stale/absent reading."""
        from utils.watchdog_probes import probe_claw_battery_low
        t = [self._tick(reachable=False, volts=2.41)]
        for _ in range(3):
            assert probe_claw_battery_low(ticks=t, now=1.0,
                                          state_path=self.sp2) is None

    def test_healthy_pack_is_clean(self):
        from utils.watchdog_probes import probe_claw_battery_low
        for _ in range(3):
            sig = probe_claw_battery_low(ticks=[self._tick(volts=4.06)],
                                         now=1.0, state_path=self.sp2)
        assert sig is None
        assert collect_dispositions()["claw_battery_low"]["disp"] == "clean"

    # ---- level + TREND + intent (2026-07-24) ------------------------------
    # Operator: "agreed on the claw batt discharge, it's not failed — a bit
    # more intelligence with these metrics would help." A pack we deliberately
    # run to cutoff and a pack that is failing produced the SAME page, and a
    # claw that died at 4.05 V was told to check its battery.

    def _seed_series(self, device, points):
        """Write a rolling pack series where the probes look for it."""
        from utils import claw_battery as cb
        p = cb.series_path(os.path.dirname(self.sp), device)
        for ts, v in points:
            cb.append_sample(p, ts, v)
        return p

    def _falling(self, v_start, v_per_hr, hours, end_ts=1000.0, step_hr=0.25):
        n = int(hours / step_hr)
        return [(end_ts - (n - i) * step_hr * 3600.0,
                 v_start + v_per_hr * (i * step_hr)) for i in range(n + 1)]

    def test_armed_soak_discharge_does_not_page(self):
        """THE ask: a deliberate discharge is not a failure. It is still
        SAID — with its projection — just not paged."""
        from utils.watchdog_probes import probe_claw_battery_low
        self._seed_series("dudeclaw-02", self._falling(3.9, -0.05, 6))
        t = [self._tick(volts=3.45)]
        for _ in range(3):
            sig = probe_claw_battery_low(ticks=t, now=1000.0, state_path=self.sp2,
                                         soak_devices={"dudeclaw-02"})
            assert sig is None
        disp = collect_dispositions()["claw_battery_low"]
        assert disp["disp"] == "clean"
        # visible, not silenced: the reason carries the state and projection
        assert "dudeclaw-02" in disp["reason"] and "EXPECTED" in disp["reason"]

    def test_same_pack_without_a_soak_config_still_pages(self):
        """The suppression is intent-gated, not voltage-gated: identical
        readings on a box that declared no soak must page exactly as before."""
        from utils.watchdog_probes import probe_claw_battery_low
        self._seed_series("dudeclaw-02", self._falling(3.9, -0.05, 6))
        t = [self._tick(volts=3.45)]
        probe_claw_battery_low(ticks=t, now=1000.0, state_path=self.sp2)
        sig = probe_claw_battery_low(ticks=t, now=1001.0, state_path=self.sp2)
        assert sig is not None and sig.cls == "claw_battery_low"

    def test_armed_soak_that_is_charging_is_not_excused(self):
        """Intent explains a DECLINE. An armed device whose pack is low and
        RISING is not the experiment running — it must not inherit the pass."""
        from utils.watchdog_probes import probe_claw_battery_low
        self._seed_series("dudeclaw-02", self._falling(3.1, +0.05, 6))
        t = [self._tick(volts=3.40)]
        probe_claw_battery_low(ticks=t, now=1000.0, state_path=self.sp2,
                               soak_devices={"dudeclaw-02"})
        sig = probe_claw_battery_low(ticks=t, now=1001.0, state_path=self.sp2,
                                     soak_devices={"dudeclaw-02"})
        assert sig is not None, "a charging armed pack is not an expected decline"

    def test_fast_drain_warns_while_still_above_the_floor(self):
        """New reach: a claw at 3.9 V shedding 100 mV/h is ~5 h from dark and
        used to read as perfectly healthy until it fell into the knee."""
        from utils.watchdog_probes import probe_claw_battery_low
        self._seed_series("dudeclaw-02", self._falling(4.4, -0.10, 5))
        t = [self._tick(volts=3.9)]
        probe_claw_battery_low(ticks=t, now=1000.0, state_path=self.sp2)
        sig = probe_claw_battery_low(ticks=t, now=1001.0, state_path=self.sp2)
        assert sig is not None
        assert "draining fast" in sig.detail
        assert sig.extra["low"][0]["hours_to_cutoff"] is not None
        assert "LINEAR projection" in sig.detail   # never sold as measured

    def test_slow_healthy_drain_does_not_page(self):
        """The early warning must not fire on a pack that is merely in use:
        4.06 V at 5 mV/h is days away, not hours."""
        from utils.watchdog_probes import probe_claw_battery_low
        self._seed_series("dudeclaw-01", self._falling(4.09, -0.005, 6))
        t = [self._tick(device="dudeclaw-01", volts=4.06)]
        for _ in range(3):
            assert probe_claw_battery_low(ticks=t, now=1000.0,
                                          state_path=self.sp2) is None

    def test_dark_claw_says_whether_the_battery_was_the_cause(self):
        """The trip-saving distinction: died flat vs died healthy."""
        from utils.watchdog_probes import probe_claw_device_dark
        self._seed_series("dudeclaw-02", self._falling(4.2, -0.001, 6))
        t = [self._tick(reachable=False, errors={"device_info": "no reply"})]
        probe_claw_device_dark(ticks=t, now=1000.0, state_path=self.sp)
        sig = probe_claw_device_dark(ticks=t, now=1001.0, state_path=self.sp)
        assert sig is not None
        assert "NOT a flat battery" in sig.detail
        assert sig.extra["power"]["dudeclaw-02"]["state"] in ("float", "charging")

    def test_dark_claw_with_a_flat_pack_names_the_battery(self):
        from utils.watchdog_probes import probe_claw_device_dark
        self._seed_series("dudeclaw-02", self._falling(3.9, -0.10, 6))
        t = [self._tick(reachable=False)]
        probe_claw_device_dark(ticks=t, now=1000.0, state_path=self.sp)
        sig = probe_claw_device_dark(ticks=t, now=1001.0, state_path=self.sp)
        assert sig is not None and "BATTERY ran out" in sig.detail

    def test_dark_claw_with_no_readings_says_it_cannot_say(self):
        """No series held → the cause is unknown, and unknown must be stated,
        not guessed in either direction."""
        from utils.watchdog_probes import probe_claw_device_dark
        t = [self._tick(reachable=False)]
        probe_claw_device_dark(ticks=t, now=1000.0, state_path=self.sp)
        sig = probe_claw_device_dark(ticks=t, now=1001.0, state_path=self.sp)
        assert sig is not None and "cannot say whether power was the cause" in sig.detail

    def test_armed_soak_reaching_cutoff_is_the_experiment_ending(self):
        """A soak-armed claw that goes quiet with a flat pack is the soak
        FINISHING; battery_soak owns that verdict."""
        from utils.watchdog_probes import probe_claw_device_dark
        self._seed_series("dudeclaw-02", self._falling(3.9, -0.10, 6))
        t = [self._tick(reachable=False)]
        for _ in range(3):
            sig = probe_claw_device_dark(ticks=t, now=1000.0, state_path=self.sp,
                                         soak_devices={"dudeclaw-02"})
            assert sig is None
        disp = collect_dispositions()["claw_device_dark"]
        assert disp["disp"] == "clean"
        assert "expected end-of-discharge" in disp["reason"]

    def test_armed_soak_that_dies_with_a_healthy_pack_still_pages(self):
        """The gate that keeps this from becoming a silence switch: arming a
        soak must not excuse a CRASH."""
        from utils.watchdog_probes import probe_claw_device_dark
        self._seed_series("dudeclaw-02", self._falling(4.2, -0.001, 6))
        t = [self._tick(reachable=False)]
        probe_claw_device_dark(ticks=t, now=1000.0, state_path=self.sp,
                               soak_devices={"dudeclaw-02"})
        sig = probe_claw_device_dark(ticks=t, now=1001.0, state_path=self.sp,
                                     soak_devices={"dudeclaw-02"})
        assert sig is not None, "a claw that dies at 4.19 V is not a finished soak"

    def test_a_real_death_beside_a_finished_soak_still_pages(self):
        """Suppression may never absorb a claw it wasn't asked about."""
        from utils.watchdog_probes import probe_claw_device_dark
        self._seed_series("dudeclaw-02", self._falling(3.9, -0.10, 6))
        self._seed_series("dudeclaw-03", self._falling(4.2, -0.001, 6))
        t = [self._tick(reachable=False),
             self._tick(device="dudeclaw-03", reachable=False)]
        probe_claw_device_dark(ticks=t, now=1000.0, state_path=self.sp,
                               soak_devices={"dudeclaw-02"})
        sig = probe_claw_device_dark(ticks=t, now=1001.0, state_path=self.sp,
                                     soak_devices={"dudeclaw-02"})
        assert sig is not None and "dudeclaw-03" in sig.detail

    def test_soak_record_reaches_the_probes_trend(self, tmp_path):
        """The 07-24 miss: the probe kept its own 20-minute series while the
        soak's own 37.5 h record sat unread in the operator's home, so a pack
        down 570 mV reported "trend unknown". Merged, the trend is there."""
        from utils import claw_battery as cb
        from utils.watchdog_probes import probe_claw_battery_low
        soak = tmp_path / cb.SOAK_SERIES_BASENAME
        soak.write_text("\n".join(
            json.dumps({"ts": 1000.0 - h * 3600.0, "vbat": 3.46 + 0.0075 * h,
                        "device": "dudeclaw-02"})
            for h in range(12, 0, -1)) + "\n")

        t = [self._tick(volts=3.46)]
        for _ in range(3):
            sig = probe_claw_battery_low(
                ticks=t, now=1000.0, home=str(tmp_path), state_path=self.sp2,
                soak_devices={"dudeclaw-02": 3.41})
            assert sig is None                      # armed soak: still no page
        reason = collect_dispositions()["claw_battery_low"]["reason"]
        assert "mV/h" in reason, reason             # …but now WITH a trend
        assert "too thin to fit" not in reason
        assert "LINEAR projection" in reason        # and a labelled countdown

    def test_soak_record_is_not_read_for_an_unarmed_claw(self, tmp_path):
        """Only an ARMED device's soak record is its own. Merging someone
        else's discharge curve into this pack would be the cross-device blend
        the soak reader already refuses."""
        from utils import claw_battery as cb
        from utils.watchdog_probes import probe_claw_battery_low
        (tmp_path / cb.SOAK_SERIES_BASENAME).write_text("\n".join(
            json.dumps({"ts": 1000.0 - h * 3600.0, "vbat": 4.20 - 0.0,
                        "device": "dudeclaw-02"})
            for h in range(12, 0, -1)) + "\n")
        t = [self._tick(volts=3.46)]
        probe_claw_battery_low(ticks=t, now=1000.0, home=str(tmp_path),
                               state_path=self.sp2)
        sig = probe_claw_battery_low(ticks=t, now=1001.0, home=str(tmp_path),
                                     state_path=self.sp2)
        assert sig is not None                      # unarmed → still pages
        assert "too thin" in sig.detail             # …off its OWN thin series

    def test_injected_ticks_never_consult_the_real_boxs_soak_config(self):
        """Hermeticity: on the claw-brain box a real battery_soak.json exists.
        A test (or any caller passing ticks) must not be steered by it."""
        import inspect

        from utils import watchdog_probes_liveness as wpl
        src = inspect.getsource(wpl.probe_claw_battery_low)
        # the config read sits INSIDE the `ticks is None` branch
        branch = src.split("if ticks is None:")[1].split("else:")[0]
        assert "_soak_armed_devices" in branch

    def test_unreadable_soak_config_arms_nothing(self, tmp_path):
        """Fail toward paging: a malformed config must not make everything
        'expected' (a silence-manufacturing switch)."""
        from utils import claw_battery as cb
        from utils.watchdog_probes_liveness import (CLAW_SOAK_CONFIG_REL,
                                                    _soak_armed_devices)
        cfg = tmp_path / CLAW_SOAK_CONFIG_REL
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{not json")
        assert _soak_armed_devices(str(tmp_path)) == {}
        assert _soak_armed_devices(None) == {}
        # a config that names no device arms nothing either: guessing WHICH
        # pack someone meant to discharge is exactly the inference that must
        # not silence a page (battery_soak's own dudeclaw-01 default is a
        # measurement convenience, not a declaration of intent).
        cfg.write_text('{"battery": {"cutoff_v": 3.41}}')
        assert _soak_armed_devices(str(tmp_path)) == {}
        # named device, no cutoff → the chemistry default
        cfg.write_text('{"claw_device": "dudeclaw-02"}')
        assert _soak_armed_devices(str(tmp_path)) == {"dudeclaw-02": cb.CUTOFF_V}
        # the soak's OWN cutoff rides along, so the probe judges that pack
        # against the same end-of-discharge the experiment measures to
        cfg.write_text('{"claw_device": "dudeclaw-02",'
                       ' "battery": {"cutoff_v": 3.41}}')
        assert _soak_armed_devices(str(tmp_path)) == {"dudeclaw-02": 3.41}

    @pytest.fixture(autouse=True)
    def _paths(self, tmp_path):
        self.sp = str(tmp_path / "dark.json")
        self.sp2 = str(tmp_path / "bat.json")


class TestClawRfSilentWitness:
    """Structural-dark row 9 (2026-07-19): the fleet's only OVER-THE-AIR witness.

    Every other mesh-RF check is a box talking about itself. The claws answer
    lora_stats from a separate radio on separate silicon, so "we think we
    transmitted" becomes distinguishable from "the air is quiet".
    """

    @pytest.fixture(autouse=True)
    def _sp(self, tmp_path):
        self.sp = str(tmp_path / "rf.json")

    def _tick(self, device="dudeclaw-01", reachable=True, age=None):
        t = {"device": device, "reachable": reachable, "errors": {}}
        if age is not None:
            t["lora"] = {"heard_age_s": age, "heard_pkts": 158224,
                         "crc_err": 1461, "last_from": "!79be01d3"}
        return t

    def test_no_claw_is_inert(self):
        from utils.watchdog_probes import probe_claw_rf_silent
        assert probe_claw_rf_silent(ticks=[], now=1.0, state_path=self.sp) is None
        assert collect_dispositions()["claw_rf_silent"]["disp"] == "inert"

    def test_hearing_traffic_is_clean(self):
        from utils.watchdog_probes import probe_claw_rf_silent
        for _ in range(3):
            sig = probe_claw_rf_silent(ticks=[self._tick(age=4)], now=1.0,
                                       state_path=self.sp)
        assert sig is None
        assert collect_dispositions()["claw_rf_silent"]["disp"] == "clean"

    def test_all_claws_silent_fires_after_debounce(self):
        from utils.watchdog_probes import probe_claw_rf_silent
        t = [self._tick("dudeclaw-01", age=5000),
             self._tick("dudeclaw-02", age=4200)]
        assert probe_claw_rf_silent(ticks=t, now=1.0, state_path=self.sp) is None
        sig = probe_claw_rf_silent(ticks=t, now=2.0, state_path=self.sp)
        assert sig is not None and sig.cls == "claw_rf_silent"
        assert sig.extra["threshold_provisional"] is True

    def test_one_claw_still_hearing_keeps_it_clean(self):
        """One deaf claw is that claw's problem; only ALL of them going quiet
        is evidence about the CHANNEL. A single-radio failure must not be
        reported as an RF outage."""
        from utils.watchdog_probes import probe_claw_rf_silent
        t = [self._tick("dudeclaw-01", age=5000),
             self._tick("dudeclaw-02", age=3)]
        for _ in range(3):
            assert probe_claw_rf_silent(ticks=t, now=1.0,
                                        state_path=self.sp) is None

    def test_no_lora_reading_is_indeterminate_not_silence(self):
        """A claw with no ears — or a capture predating lora_stats — knows
        nothing about the air. Unknown must not become 'quiet'."""
        from utils.watchdog_probes import probe_claw_rf_silent
        for _ in range(3):
            assert probe_claw_rf_silent(ticks=[self._tick(age=None)], now=1.0,
                                        state_path=self.sp) is None
        assert (collect_dispositions()["claw_rf_silent"]["disp"]
                == "indeterminate")

    def test_unreachable_claw_is_not_counted_as_silence(self):
        """A dark claw hears nothing by definition; that is
        claw_device_dark's finding, not an RF-outage claim."""
        from utils.watchdog_probes import probe_claw_rf_silent
        t = [self._tick(reachable=False, age=99999)]
        for _ in range(3):
            assert probe_claw_rf_silent(ticks=t, now=1.0,
                                        state_path=self.sp) is None


class TestGatewayDualHomedExposure:
    """The leading indicator behind the row-8 accept (2026-07-19).

    Row 8 accepted the duplicate residual on cost asymmetry — a dup is
    redundancy, a yield-protocol bug is silence — NOT on dups being rare.
    Three human recipients were already dual-homed the day it was accepted, so
    the fleet instruments the CONDITION that permits a duplicate rather than
    only the rare, partly-unobservable outcome.
    """

    @pytest.fixture(autouse=True)
    def _sp(self, tmp_path):
        self.sp = str(tmp_path / "dual.json")

    def _payload(self, hashes, status="ok"):
        return {"status": status, "dual_homed_recipient_hashes": list(hashes),
                "dual_homed_recipients": len(hashes)}

    def test_new_dual_homed_recipient_fires_once(self):
        from utils.watchdog_probes import probe_gateway_dual_homed_exposure
        p = self._payload(["aaaa1111", "bbbb2222"])
        sig = probe_gateway_dual_homed_exposure(payload=p, state_path=self.sp)
        assert sig is not None and sig.cls == "gateway_dual_homed_exposure"
        assert sig.extra["dual_homed_total"] == 2
        # Once known, a recipient stays known — no re-fire on the next tick.
        assert probe_gateway_dual_homed_exposure(payload=p,
                                                 state_path=self.sp) is None
        assert (collect_dispositions()["gateway_dual_homed_exposure"]["disp"]
                == "clean")

    def test_only_the_new_recipient_is_reported(self):
        from utils.watchdog_probes import probe_gateway_dual_homed_exposure
        probe_gateway_dual_homed_exposure(payload=self._payload(["aaaa1111"]),
                                          state_path=self.sp)
        sig = probe_gateway_dual_homed_exposure(
            payload=self._payload(["aaaa1111", "cccc3333"]), state_path=self.sp)
        assert sig is not None and sig.extra["new"] == ["cccc3333"]

    def test_count_churn_alone_does_not_fire(self):
        """The count moves as the rollup window rolls. Only a recipient we
        have never seen dual-homed is a real exposure change."""
        from utils.watchdog_probes import probe_gateway_dual_homed_exposure
        probe_gateway_dual_homed_exposure(
            payload=self._payload(["aaaa1111", "bbbb2222"]), state_path=self.sp)
        # count drops to 1 then back to 2 — same recipients, no new exposure
        assert probe_gateway_dual_homed_exposure(
            payload=self._payload(["aaaa1111"]), state_path=self.sp) is None
        assert probe_gateway_dual_homed_exposure(
            payload=self._payload(["aaaa1111", "bbbb2222"]),
            state_path=self.sp) is None

    def test_indeterminate_rollup_is_not_zero_exposure(self):
        """<2 gateways cannot observe dual-homing at all; that must never read
        as 'no recipients are dual-homed'."""
        from utils.watchdog_probes import probe_gateway_dual_homed_exposure
        assert probe_gateway_dual_homed_exposure(
            payload=self._payload([], status="indeterminate"),
            state_path=self.sp) is None
        assert (collect_dispositions()["gateway_dual_homed_exposure"]["disp"]
                == "indeterminate")

    def test_absent_field_is_indeterminate_never_a_forged_zero(self):
        """A JOIN predating this indicator omits the key entirely. Absent is
        not zero (honest_failure_modes #2/#4 — an un-upgraded manager must not
        forge a clean bill of health)."""
        from utils.watchdog_probes import probe_gateway_dual_homed_exposure
        assert probe_gateway_dual_homed_exposure(
            payload={"status": "ok"}, state_path=self.sp) is None
        assert (collect_dispositions()["gateway_dual_homed_exposure"]["disp"]
                == "indeterminate")

    def test_stale_rollup_is_indeterminate(self):
        from utils.watchdog_probes import probe_gateway_dual_homed_exposure
        p = self._payload(["aaaa1111"])
        p["freshness"] = {"stale": True}
        assert probe_gateway_dual_homed_exposure(payload=p,
                                                 state_path=self.sp) is None
