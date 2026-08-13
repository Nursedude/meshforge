"""The other two long-running `detector_blind_any` conditions (2026-08-05).

Both had been continuously `indeterminate` on the federator box for ~170h,
and both for the same reason as the RNS-name defect the same day: a state
that is NOT a failed observation was reported as one, so the disposition
carried no information and a REAL blindness would have been invisible
inside the expected noise.

* ``mqtt_root_drift`` — `_read_declared_root_topic` returned None for a
  MISSING gateway.json (no MQTT bridge consumer on this box: inert) and for
  an unreadable one (indeterminate) alike.
* ``delivery_confirmation_stall`` — never checked whether a gateway runs
  here at all, so on a non-gateway box it fell through to "no confirmable
  protocol recorded" forever. Its sibling `gateway_delivery_degraded` had
  always answered `inert` for the same box.

And the structural hole underneath the second one: ``if not confirmable:
return None`` meant a box that had NEVER recorded a confirmation could not
trip the detector at all — a TOTAL collapse read as nothing-to-judge while
a partial one fired. Measured on the fleet the day this shipped: two real
gateways carried 16,759 and 26,732 confirmed RNS deliveries; the federator
box carried 6,286 RNS sent, 5,585 dropped, and zero confirmed, ever.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _systemd_manager_reachable() -> bool:
    """True when `systemctl show` can actually ask a systemd manager.

    2026-08-12 review: the live-drill test below plants a GHOST UNIT NAME
    through the real systemctl and asserts the resolver answers `absent` —
    which requires a reachable manager (PID 1 systemd + DBus). In a
    container/chroot the systemctl binary may exist while the manager does
    not; `systemctl show` then exits non-zero and the resolver honestly
    answers `unknown`, so the assertion would fail for the ENVIRONMENT, not
    the code. Green on fleet boxes and VM runners, red in docker — the
    un-pinned ambient-state class this repo already names
    (feedback_tests_must_pin_ambient_state)."""
    import subprocess
    try:
        proc = subprocess.run(
            ["systemctl", "show", "-p", "Version", "--no-pager"],
            capture_output=True, text=True, timeout=3,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

from utils.watchdog_probe_core import (  # noqa: E402
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes_drift import (  # noqa: E402
    _declared_root_status,
    probe_mqtt_root_drift,
)
from utils.watchdog_probes_gateway import (  # noqa: E402
    _NEVER_CONFIRMED_MIN_TERMINAL,
    _never_confirmed_signal,
    probe_delivery_confirmation_stall,
)


@pytest.fixture
def dispositions():
    reset_dispositions()
    return collect_dispositions


# ─────────────────────────────────────────────────────────────────────
# mqtt_root_drift — absent is not unreadable
# ─────────────────────────────────────────────────────────────────────


class TestDeclaredRootStatusSeparatesAbsentFromUnreadable:

    def _fake_home(self, tmp_path):
        import pwd as _pwd

        class FakePw:
            pw_dir = str(tmp_path)
        return patch.object(_pwd, "getpwnam", lambda u: FakePw())

    def test_missing_gateway_json_is_absent(self, tmp_path):
        with self._fake_home(tmp_path):
            assert _declared_root_status("u") == ("absent", None)

    def test_unparseable_gateway_json_is_unreadable(self, tmp_path):
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "gateway.json").write_text("{not json")
        with self._fake_home(tmp_path):
            assert _declared_root_status("u") == ("unreadable", None)

    def test_present_config_yields_ok_and_value(self, tmp_path):
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "gateway.json").write_text('{"mqtt_bridge": {"root_topic": "msh/US"}}')
        with self._fake_home(tmp_path):
            assert _declared_root_status("u") == ("ok", "msh/US")

    def test_absent_key_still_ok_because_the_default_IS_the_contract(
            self, tmp_path):
        """A config with no root_topic is not missing information — the
        GatewayConfig default is the consumer's effective root."""
        cfg = tmp_path / ".config" / "meshforge"
        cfg.mkdir(parents=True)
        (cfg / "gateway.json").write_text('{"mqtt_bridge": {}}')
        with self._fake_home(tmp_path):
            status, root = _declared_root_status("u")
        assert status == "ok" and root

    def test_unresolvable_user_is_unreadable_not_absent(self):
        assert _declared_root_status(None) == ("unreadable", None)


class TestMqttRootDriftInertWithoutAConsumer:
    """A box with no gateway.json has nothing that CAN be deaf."""

    def _run(self, status, dispositions):
        # ⚠️ The publish line comes through the INJECTED newest_line_fn seam,
        # not a patch of `_journal_newest_match`. These tests originally
        # patched that module function; the third-leg fix later changed the
        # default reader to `_journal_newest_match_status`, which silently
        # made the patch a no-op — so the real journalctl ran and the verdict
        # depended on whether the box had a publishing meshtasticd. Green
        # here, RED in CI. The seam is part of the probe's contract and
        # cannot go stale under it (feedback_tests_must_pin_ambient_state:
        # when a fix changes what code READS, re-audit that function's tests).
        with patch("utils.watchdog_probes_drift._resolve_main_pid_status",
                   return_value=("ok", 1234)), \
             patch("utils.watchdog_probes_drift._declared_root_status",
                   return_value=status):
            return probe_mqtt_root_drift(
                service_user_fn=lambda: "u",
                newest_line_fn=lambda p: "JSON publish message to msh/US/2/json/x")

    def test_absent_declaration_is_inert(self, dispositions):
        sig = self._run(("absent", None), dispositions)
        assert sig is None
        got = dispositions()["mqtt_root_drift"]
        assert got["disp"] == "inert", (
            "no gateway.json = the organ is legitimately absent; reporting "
            "'cannot observe' buried this box in permanent indeterminate")
        assert "no MQTT bridge consumer" in got["reason"]

    def test_unreadable_declaration_is_still_indeterminate(self, dispositions):
        sig = self._run(("unreadable", None), dispositions)
        assert sig is None
        got = dispositions()["mqtt_root_drift"]
        assert got["disp"] == "indeterminate", (
            "a gateway.json that SHOULD be readable and isn't is a real "
            "failed observation and must not be downgraded to inert")


# ─────────────────────────────────────────────────────────────────────
# delivery_confirmation_stall — organ presence, then the total-collapse hole
# ─────────────────────────────────────────────────────────────────────


class TestStallProbeInertWithoutAGateway:

    def test_no_gateway_process_is_inert(self, dispositions):
        with patch("utils.watchdog_probes_gateway._resolve_main_pid_status",
                   return_value=("absent", None)):
            sig = probe_delivery_confirmation_stall()
        assert sig is None
        got = dispositions()["delivery_confirmation_stall"]
        assert got["disp"] == "inert"
        assert "gateway not running" in got["reason"]

    def test_no_gateway_does_not_even_fetch_the_payload(self):
        """Organ presence is checked FIRST — a non-gateway box must not
        spend an HTTP round trip per tick to conclude it has no gateway."""
        with patch("utils.watchdog_probes_gateway._resolve_main_pid_status",
                   return_value=("absent", None)), \
             patch("utils.watchdog_probes_gateway._fetch_delivery_payload") as fetch:
            probe_delivery_confirmation_stall()
        fetch.assert_not_called()


class TestStallProbeUnknownIsDrillableAgainstRealSystemd:
    """The organ gate's UNKNOWN branch, planted through the REAL resolver.

    2026-08-12, from the end-of-session double tap. Every other branch of this
    gate had been exercised on a live box; UNKNOWN had not, because this probe
    took no ``systemctl_path`` while its twin
    ``probe_gateway_delivery_degraded`` did. Reaching for a live drill I
    planted a nonexistent UNIT NAME instead — which is the ABSENT branch — and
    nearly recorded the result as UNKNOWN. A pair of probes that mirror each
    other must be equally testable, or the drill quietly proves the wrong path.

    No mock of ``_resolve_main_pid_status`` here on purpose: the point is to
    run the real resolver against a systemctl that cannot be executed, which
    is the shape a wedged/absent systemctl actually takes.
    """

    def test_unrunnable_systemctl_is_indeterminate_not_no_gateway(
            self, dispositions):
        sig = probe_delivery_confirmation_stall(
            systemctl_path="/nonexistent/systemctl-xyz")
        assert sig is None
        got = dispositions()["delivery_confirmation_stall"]
        assert got["disp"] == "indeterminate", (
            "a systemctl we could not RUN is not an observation that this box "
            f"has no gateway organ — got {got}")
        assert "unobservable" in got["reason"]

    @pytest.mark.skipif(
        not _systemd_manager_reachable(),
        reason=(
            "the ABSENT plant needs a real, reachable systemd manager: "
            "`systemctl show` answers LoadState=not-found for a ghost unit "
            "only when it can ask PID 1. In a container/chroot (systemctl "
            "binary present, no manager) it exits non-zero, the resolver "
            "honestly answers 'unknown', and this test would fail for the "
            "environment, not the code (feedback_tests_must_pin_ambient_"
            "state). SKIPPED IS NOT PASSED — the drill still runs on every "
            "fleet box and on VM-based CI runners."),
    )
    def test_absent_and_unknown_are_reached_by_DIFFERENT_plants(
            self, dispositions):
        """Guards the mistake itself: a ghost unit NAME must land in inert
        (absent), and only an unrunnable systemctl in indeterminate. If these
        ever collapse, the live drill stops distinguishing them."""
        probe_delivery_confirmation_stall(
            gateway_unit="definitely-not-a-real-unit-xyz.service")
        assert dispositions()["delivery_confirmation_stall"]["disp"] == "inert"

        reset_dispositions()
        probe_delivery_confirmation_stall(
            systemctl_path="/nonexistent/systemctl-xyz")
        assert (collect_dispositions()["delivery_confirmation_stall"]["disp"]
                == "indeterminate")

    def test_the_two_twin_probes_take_the_same_seam(self):
        """Contract pin: the asymmetry that caused this must not come back."""
        import inspect
        from utils.watchdog_probes_gateway_flow import (
            probe_gateway_delivery_degraded)
        for fn in (probe_delivery_confirmation_stall,
                   probe_gateway_delivery_degraded):
            assert "systemctl_path" in inspect.signature(fn).parameters, (
                f"{fn.__name__} lost its systemctl_path seam — its organ-gate "
                f"UNKNOWN branch is no longer drillable against real systemd")


class TestTotalConfirmationCollapseIsVisible:
    """`if not confirmable: return None` made a permanent, total collapse
    the ONE case this probe could never report."""

    REAL_FEDERATOR = {          # measured 2026-08-05
        "confirmed": {},
        "sent": {"rns": 6286, "meshtastic": 19105, "mqtt": 758},
        "dropped": {"rns": 5585, "meshtastic": 23833},
    }
    REAL_GATEWAY = {            # moc, same day
        "confirmed": {"rns": 16759},
        "sent": {"rns": 3264},
        "dropped": {"rns": 100},
    }

    def test_zero_confirmations_ever_fires(self):
        sig = _never_confirmed_signal(self.REAL_FEDERATOR)
        assert sig is not None, (
            "6,286 RNS sent and 5,585 dropped with zero confirmations EVER "
            "is the #74 class at its most extreme — it must not be silent")
        assert sig.cls == "delivery_confirmation_stall"
        assert sig.severity == "degraded"
        assert sig.extra["protocol"] == "rns"
        assert sig.extra["confirmed"] == 0
        assert sig.extra["terminal_events"] == 6286 + 5585

    def test_healthy_gateway_never_fires(self):
        """SELF-GUARD: the helper must be right on its own inputs, not only
        via the caller's `confirmable` pre-check. Written after the first
        version fired on this exact shape."""
        assert _never_confirmed_signal(self.REAL_GATEWAY) is None

    def test_mesh_only_gateway_never_fires(self):
        """Meshtastic has no ACK path yet — its absence of confirmations is
        a fact about the protocol, not the wiring."""
        assert _never_confirmed_signal(
            {"confirmed": {}, "sent": {"meshtastic": 9000},
             "dropped": {"meshtastic": 300}}) is None

    def test_below_threshold_never_fires(self):
        n = _NEVER_CONFIRMED_MIN_TERMINAL - 1
        assert _never_confirmed_signal(
            {"confirmed": {}, "sent": {"rns": n}, "dropped": {}}) is None

    def test_at_threshold_fires(self):
        assert _never_confirmed_signal(
            {"confirmed": {}, "sent": {"rns": _NEVER_CONFIRMED_MIN_TERMINAL},
             "dropped": {}}) is not None

    @pytest.mark.parametrize("by_proto", [
        {},
        {"confirmed": {}, "sent": {"rns": "many"}, "dropped": {}},
        {"confirmed": {}, "sent": {"rns": None}, "dropped": None},
        {"confirmed": {"rns": True}, "sent": {"rns": 9999}, "dropped": {}},
        {"sent": {"rns": -5}, "dropped": {"rns": -5}},
    ])
    def test_misshaped_payload_never_manufactures_traffic(self, by_proto):
        """A garbled payload must not invent terminal events and page."""
        assert _never_confirmed_signal(by_proto) is None

    def test_probe_surfaces_it_end_to_end(self, dispositions):
        with patch("utils.watchdog_probes_gateway._resolve_main_pid_status",
                   return_value=("ok", 999)), \
             patch("utils.watchdog_probes_gateway._fetch_delivery_payload",
                   return_value=({"state_by_protocol": self.REAL_FEDERATOR}, None)):
            sig = probe_delivery_confirmation_stall()
        assert sig is not None and sig.subject == "rns:never-confirmed"

    def test_quiet_gateway_still_reports_indeterminate(self, dispositions):
        """Nothing confirmable and nothing to go on stays honest — the fix
        must not turn every quiet gateway into a page."""
        with patch("utils.watchdog_probes_gateway._resolve_main_pid_status",
                   return_value=("ok", 999)), \
             patch("utils.watchdog_probes_gateway._fetch_delivery_payload",
                   return_value=({"state_by_protocol": {
                       "confirmed": {}, "sent": {"meshtastic": 5},
                       "dropped": {}}}, None)):
            sig = probe_delivery_confirmation_stall()
        assert sig is None
        assert dispositions()["delivery_confirmation_stall"]["disp"] == "indeterminate"


# ─────────────────────────────────────────────────────────────────────
# mqtt_root_drift, third leg — silence means nothing until the channel
# is known to work
# ─────────────────────────────────────────────────────────────────────


class TestJournalSilenceIsNotUnobservable:
    """`_journal_newest_match` returns None for "nothing logged" AND for
    "journalctl is wedged". mqtt_root_drift picked the pessimistic reading
    for both and sat indeterminate forever on four RX-only fleet boxes —
    noise a REAL journal outage would have been invisible inside."""

    from utils.watchdog_probe_core import (  # noqa: E402
        _journal_newest_match_status as _status,
    )

    def _fake_run(self, *, stdout="", returncode=0, exc=None):
        def _runner(*a, **k):
            if exc:
                raise exc

            class _R:
                pass
            r = _R()
            r.stdout, r.returncode = stdout, returncode
            return r
        return _runner

    def test_match_is_ok_with_the_line(self):
        from utils.watchdog_probe_core import _journal_newest_match_status
        with patch("utils.watchdog_probe_core.subprocess.run",
                   self._fake_run(stdout="123 a line\n")):
            assert _journal_newest_match_status("u", "p", "1h") == ("ok", "123 a line")

    def test_ran_and_found_nothing_is_ok_none_not_unobservable(self):
        from utils.watchdog_probe_core import _journal_newest_match_status
        with patch("utils.watchdog_probe_core.subprocess.run",
                   self._fake_run(stdout="", returncode=1)):
            assert _journal_newest_match_status("u", "p", "1h") == ("ok", None)

    @pytest.mark.parametrize("kw", [
        {"exc": FileNotFoundError("no journalctl")},
        {"exc": OSError("boom")},
        {"returncode": 2},
    ])
    def test_broken_channel_is_unobservable(self, kw):
        from utils.watchdog_probe_core import _journal_newest_match_status
        with patch("utils.watchdog_probe_core.subprocess.run",
                   self._fake_run(**kw)):
            assert _journal_newest_match_status("u", "p", "1h") == ("unobservable", None)

    def test_flat_shim_still_collapses_both_for_old_callers(self):
        """The shim keeps its contract — ONE implementation underneath."""
        from utils.watchdog_probe_core import _journal_newest_match
        with patch("utils.watchdog_probe_core.subprocess.run",
                   self._fake_run(stdout="", returncode=1)):
            assert _journal_newest_match("u", "p", "1h") is None
        with patch("utils.watchdog_probe_core.subprocess.run",
                   self._fake_run(exc=OSError("boom"))):
            assert _journal_newest_match("u", "p", "1h") is None


class TestMqttRootDriftSilenceDisposition:

    def _run(self, run_mock, dispositions):
        with patch("utils.watchdog_probes_drift._resolve_main_pid_status",
                   return_value=("ok", 1234)), \
             patch("utils.watchdog_probe_core.subprocess.run", run_mock):
            return probe_mqtt_root_drift()

    def _fake_run(self, *, returncode=0, exc=None):
        def _runner(*a, **k):
            if exc:
                raise exc

            class _R:
                pass
            r = _R()
            r.stdout, r.returncode = "", returncode
            return r
        return _runner

    def test_no_publish_line_with_working_journal_is_inert(self, dispositions):
        """An RX-only box: journalctl ran, this radio publishes no JSON, so
        there is no consumer/radio pair that CAN drift."""
        self._run(self._fake_run(returncode=1), dispositions)
        got = dispositions()["mqtt_root_drift"]
        assert got["disp"] == "inert", (
            "a working journal that found nothing is a positive observation, "
            "not a failure to observe")
        assert "no MQTT JSON uplink" in got["reason"]

    def test_broken_journal_stays_indeterminate(self, dispositions):
        self._run(self._fake_run(exc=OSError("journalctl wedged")), dispositions)
        got = dispositions()["mqtt_root_drift"]
        assert got["disp"] == "indeterminate", (
            "a journal we could not read must NEVER downgrade to inert — "
            "that is the outage this split exists to keep visible")
        assert "journal unavailable" in got["reason"]

    def test_injected_seam_defaults_to_observed(self, dispositions):
        """A test seam that returns None answered positively; it must not be
        treated as a broken channel."""
        with patch("utils.watchdog_probes_drift._resolve_main_pid_status",
                   return_value=("ok", 1234)):
            probe_mqtt_root_drift(newest_line_fn=lambda p: None)
        assert dispositions()["mqtt_root_drift"]["disp"] == "inert"

    def test_default_reader_makes_exactly_one_journal_call(self):
        """Cost guard: the status must come from the call already made, not
        a second subprocess per tick on every RX-only box."""
        calls = []

        def _runner(*a, **k):
            calls.append(a)

            class _R:
                pass
            r = _R()
            r.stdout, r.returncode = "", 1
            return r
        with patch("utils.watchdog_probes_drift._resolve_main_pid_status",
                   return_value=("ok", 1234)), \
             patch("utils.watchdog_probe_core.subprocess.run", _runner):
            probe_mqtt_root_drift()
        assert len(calls) == 1, f"expected 1 journalctl call, made {len(calls)}"
