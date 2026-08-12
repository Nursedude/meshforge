"""meshtasticd ABSENT is `inert`, not `indeterminate` (2026-08-12).

Four watchdog classes — ``channel_feed_dark``, ``mqtt_root_drift``,
``meshtasticd_phoneapi_wedge`` and ``meshtasticd_vsz_leak`` — all bailed with
"meshtasticd MainPID unresolved; ``service_inactive`` owns that". On
meshanchor-server that handoff points at nobody: the box is MeshCore-primary
and has ZERO meshtasticd unit files (``systemctl is-enabled`` → ``not-found``,
no binary on PATH, measured 2026-08-12), so ``service_inactive`` cannot own a
unit that does not exist and all four sat ``indeterminate`` permanently, by
construction. An organ absent BY DESIGN must read ``inert``, or real failures
have nowhere to stand out (persistent_issues, 2026-08-05).

The discriminator, measured live that day: ``systemctl show`` exits 0 for a
nonexistent unit as readily as for a running one, so rc carries no signal —
``LoadState`` does (``not-found`` vs ``loaded``), and it rides the same single
subprocess the probes already spend.

⚠️ The danger this file also pins: ``absent → inert`` is right only for probes
that OBSERVE a service. A ``systemctl`` we could not RUN is unobservable, never
absent — the drills below plant that case and require ``indeterminate``, which
is the pre-fix behaviour surviving exactly where it should.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.watchdog_probe_core import (  # noqa: E402
    _resolve_main_pid,
    _resolve_main_pid_status,
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes_channel import probe_channel_feed_dark  # noqa: E402
from utils.watchdog_probes_drift import probe_mqtt_root_drift  # noqa: E402
from utils.watchdog_probes_gateway import (  # noqa: E402
    probe_delivery_confirmation_stall,
)
from utils.watchdog_probes_gateway_flow import (  # noqa: E402
    probe_gateway_delivery_degraded,
)
from utils.watchdog_probes_rns import probe_main_thread_wedge  # noqa: E402
from utils.watchdog_probes_service import (  # noqa: E402
    probe_fd_exhaustion,
    probe_meshtasticd_phoneapi_wedge,
    probe_meshtasticd_vsz_leak,
    probe_phoneapi_tcp_leak,
)


@pytest.fixture
def dispositions():
    reset_dispositions()
    return collect_dispositions


def _systemctl(stdout: str, *, returncode: int = 0, exc=None, calls=None):
    """A fake ``subprocess.run`` standing in for ``systemctl show``."""
    def _runner(argv, *a, **k):
        if calls is not None:
            calls.append(argv)
        if exc:
            raise exc

        class _R:
            pass
        r = _R()
        r.stdout, r.stderr, r.returncode = stdout, "", returncode
        return r
    return _runner


# systemd emits properties in its OWN canonical order, not the order they were
# requested — verified live 2026-08-12 by passing the flags both ways round.
ABSENT_OUT = "MainPID=0\nLoadState=not-found\n"
DOWN_OUT = "MainPID=0\nLoadState=loaded\n"
RUNNING_OUT = "MainPID=4042974\nLoadState=loaded\n"


# ─────────────────────────────────────────────────────────────────────
# The resolver itself — four states out of one subprocess
# ─────────────────────────────────────────────────────────────────────


class TestResolveMainPidStatus:

    def _run(self, **kw):
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(**kw)):
            return _resolve_main_pid_status("meshtasticd.service")

    def test_absent_unit(self):
        assert self._run(stdout=ABSENT_OUT) == ("absent", None)

    def test_loaded_but_down(self):
        assert self._run(stdout=DOWN_OUT) == ("down", None)

    def test_running(self):
        assert self._run(stdout=RUNNING_OUT) == ("ok", 4042974)

    def test_property_order_is_not_assumed(self):
        """The parse is by KEY, so a reversed emission order still pairs
        correctly. Positional parsing (``--value``) would silently swap the
        two facts here."""
        assert self._run(stdout="LoadState=not-found\nMainPID=0\n") == (
            "absent", None)

    def test_pid_one_is_not_a_main_pid(self):
        assert self._run(stdout="MainPID=1\nLoadState=loaded\n") == (
            "down", None)

    @pytest.mark.parametrize("kw", [
        {"stdout": "", "returncode": 1},
        {"stdout": "MainPID=banana\nLoadState=loaded\n"},
        {"stdout": "LoadState=loaded\n"},               # no MainPID at all
        {"stdout": "", "exc": FileNotFoundError("no systemctl")},
        {"stdout": "", "exc": subprocess.TimeoutExpired("systemctl", 3)},
        {"stdout": "", "exc": OSError("boom")},
    ])
    def test_unrunnable_or_unparseable_is_unknown_never_absent(self, kw):
        """A systemctl we could not run tells us NOTHING about whether the
        unit exists. Collapsing that into ``absent`` would silence the four
        probes on a box whose observation channel merely broke — the same
        defect wearing the opposite costume."""
        assert self._run(**kw) == ("unknown", None)

    def test_missing_loadstate_falls_back_to_down(self):
        """Older systemd / unexpected output: keep the pre-2026-08-12
        meaning, which is the conservative one (indeterminate downstream)."""
        assert self._run(stdout="MainPID=0\n") == ("down", None)

    def test_one_subprocess_per_resolution(self):
        calls = []
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(stdout=ABSENT_OUT, calls=calls)):
            _resolve_main_pid_status("meshtasticd.service")
        assert len(calls) == 1, f"expected 1 systemctl call, made {len(calls)}"
        assert "MainPID" in calls[0] and "LoadState" in calls[0]


class TestResolveMainPidShimUnchanged:
    """The flat form is now a shim over the status form. No probe module
    still calls it (``TestTriStateHelperContract`` enforces that), but the hub
    re-exports it and out-of-tree callers may use it, so its contract — a pid
    or None, identical to the pre-split parser — is pinned here."""

    def _run(self, **kw):
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(**kw)):
            return _resolve_main_pid("meshtasticd.service")

    @pytest.mark.parametrize("stdout,expected", [
        (ABSENT_OUT, None),
        (DOWN_OUT, None),
        (RUNNING_OUT, 4042974),
    ])
    def test_shim_returns_pid_or_none(self, stdout, expected):
        assert self._run(stdout=stdout) == expected


# ─────────────────────────────────────────────────────────────────────
# The four observers — absent is inert, everything else is unchanged
# ─────────────────────────────────────────────────────────────────────

#: (class, callable) for each probe that merely OBSERVES meshtasticd.
OBSERVERS = [
    ("channel_feed_dark", probe_channel_feed_dark),
    ("mqtt_root_drift", probe_mqtt_root_drift),
    ("meshtasticd_phoneapi_wedge", probe_meshtasticd_phoneapi_wedge),
    ("meshtasticd_vsz_leak", probe_meshtasticd_vsz_leak),
]


class TestMeshtasticdObserversSplitAbsentFromDown:

    def _run(self, probe, **kw):
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(**kw)):
            return probe()

    @pytest.mark.parametrize("cls,probe", OBSERVERS)
    def test_absent_unit_is_inert(self, cls, probe, dispositions):
        """The meshanchor-server shape: no meshtasticd unit at all."""
        assert self._run(probe, stdout=ABSENT_OUT) is None
        got = dispositions()[cls]
        assert got["disp"] == "inert", (
            f"{cls}: an organ absent by design must not read as an "
            f"observation that failed — got {got}")
        assert "no meshtasticd" in got["reason"]

    @pytest.mark.parametrize("cls,probe", OBSERVERS)
    def test_installed_but_down_stays_indeterminate(self, cls, probe,
                                                    dispositions):
        """THE DRILL (plant the pre-fix case): on a box that HAS meshtasticd,
        a stopped unit must still hand off to ``service_inactive`` — which CAN
        own it here. Reading that as inert would silence a real outage."""
        assert self._run(probe, stdout=DOWN_OUT) is None
        got = dispositions()[cls]
        assert got["disp"] == "indeterminate", (
            f"{cls}: a unit that exists and is stopped is service_inactive's "
            f"to own, not inert — got {got}")

    @pytest.mark.parametrize("cls,probe", OBSERVERS)
    def test_unobservable_systemctl_stays_indeterminate(self, cls, probe,
                                                        dispositions):
        """Unobservable ≠ absent (honest_failure_modes #2)."""
        assert self._run(probe, stdout="", exc=OSError("boom")) is None
        assert dispositions()[cls]["disp"] == "indeterminate"

    @pytest.mark.parametrize("cls,probe", OBSERVERS)
    def test_absent_path_costs_one_subprocess(self, cls, probe):
        """These four run every 30s on every box; the absent box pays this
        path forever. It must not cost a second systemctl to classify."""
        calls = []
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(stdout=ABSENT_OUT, calls=calls)):
            probe()
        assert len(calls) == 1, (
            f"{cls}: expected 1 subprocess on the absent path, "
            f"made {len(calls)}")


class TestArbitraryUnitObserversSplitAbsentFromDown:
    """The three probes that observe an arbitrary unit rather than a fixed
    meshtasticd. Converted in the same commit — not swept: with no unit there
    is no fd table, no owner process and no thread stack to read, and
    ``service_inactive`` still pages for a unit that is expected active and
    missing (``systemctl is-active`` answers ``inactive`` for a nonexistent
    unit, verified live 2026-08-12), so ``inert`` hides nothing.
    """

    ARBITRARY = [
        ("fd_exhaustion",
         lambda: probe_fd_exhaustion("meshforge-map.service")),
        ("phoneapi_tcp_leak",
         lambda: probe_phoneapi_tcp_leak("meshforge-map.service")),
        ("main_thread_wedge",
         lambda: probe_main_thread_wedge("meshforge-map.service")),
    ]

    def _run(self, probe, **kw):
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(**kw)):
            return probe()

    @pytest.mark.parametrize("cls,probe", ARBITRARY)
    def test_absent_unit_is_inert(self, cls, probe, dispositions):
        assert self._run(probe, stdout=ABSENT_OUT) is None
        got = dispositions()[cls]
        assert got["disp"] == "inert", f"{cls}: got {got}"
        assert "meshforge-map.service" in got["reason"]

    @pytest.mark.parametrize("cls,probe", ARBITRARY)
    def test_installed_but_down_stays_indeterminate(self, cls, probe,
                                                    dispositions):
        assert self._run(probe, stdout=DOWN_OUT) is None
        assert dispositions()[cls]["disp"] == "indeterminate"

    @pytest.mark.parametrize("cls,probe", ARBITRARY)
    def test_unobservable_stays_indeterminate(self, cls, probe, dispositions):
        assert self._run(probe, stdout="", exc=OSError("boom")) is None
        assert dispositions()[cls]["disp"] == "indeterminate"


class TestGatewayOrganGatesKeepInertButNotForUnknown:
    """The three gateway-presence gates already answered ``inert`` for a flat
    None — which quietly swallowed "systemctl errored" too, and the wedge
    probe's own comment said so out loud. Absent/stopped stay ``inert``; only
    the unobservable case moved."""

    GATES = [
        ("delivery_confirmation_stall", probe_delivery_confirmation_stall),
        ("gateway_delivery_degraded", probe_gateway_delivery_degraded),
    ]

    def _run(self, probe, **kw):
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(**kw)):
            return probe()

    @pytest.mark.parametrize("cls,probe", GATES)
    @pytest.mark.parametrize("stdout", [ABSENT_OUT, DOWN_OUT])
    def test_no_gateway_here_is_inert(self, cls, probe, stdout, dispositions):
        assert self._run(probe, stdout=stdout) is None
        assert dispositions()[cls]["disp"] == "inert"

    @pytest.mark.parametrize("cls,probe", GATES)
    def test_unobservable_unit_state_is_not_no_gateway(self, cls, probe,
                                                       dispositions):
        assert self._run(probe, stdout="", exc=OSError("boom")) is None
        got = dispositions()[cls]
        assert got["disp"] == "indeterminate", (
            f"{cls}: a unit state we could not READ is not an observation "
            f"that this box has no gateway — got {got}")
        assert "unobservable" in got["reason"]

    def test_wedge_probe_gateway_leg_splits_the_same_way(self, dispositions):
        """meshtasticd is alive (supplied pid); the GATEWAY leg decides."""
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(stdout=ABSENT_OUT)):
            probe_meshtasticd_phoneapi_wedge(main_pid=1234)
        assert dispositions()["meshtasticd_phoneapi_wedge"]["disp"] == "inert"

        reset_dispositions()
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(stdout="", exc=OSError("boom"))):
            probe_meshtasticd_phoneapi_wedge(main_pid=1234)
        got = collect_dispositions()["meshtasticd_phoneapi_wedge"]
        assert got["disp"] == "indeterminate", (
            "an unreadable gateway unit state used to be filed as inert "
            f"alongside 'no gateway on this box' — got {got}")


class TestInjectedMainPidStillWins:
    """``main_pid=`` is a test/runner seam that positively supplies a live
    pid; it must short-circuit before any systemctl runs."""

    @pytest.mark.parametrize("probe", [
        probe_channel_feed_dark,
        probe_mqtt_root_drift,
        probe_meshtasticd_phoneapi_wedge,
        probe_meshtasticd_vsz_leak,
    ])
    def test_supplied_pid_skips_resolution(self, probe, dispositions):
        calls = []
        with patch("utils.watchdog_probe_core.subprocess.run",
                   _systemctl(stdout=ABSENT_OUT, calls=calls)):
            probe(main_pid=1234)
        # ⚠️ Only the MESHTASTICD resolution is asserted away. Two of these
        # probes resolve a SECOND unit for their own reasons (the wedge probe
        # gates itself on meshforge-gateway); those call sites are
        # deliberately untouched by the 08-12 split.
        resolved = [c[-1] for c in calls]
        assert not any("meshtasticd" in u for u in resolved), (
            f"a supplied main_pid must not trigger a meshtasticd MainPID "
            f"resolution — resolved {resolved}")
