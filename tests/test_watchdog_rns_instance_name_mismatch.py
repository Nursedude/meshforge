"""The 2026-08-05 finding: the RNS probes were keyed to a name this box
does not serve, so BOTH of them were dark — one honestly (indeterminate,
blaming rnsd) and one dishonestly (an affirmative ``clean``).

Three independent defects, pinned separately here because each one alone
was enough to hide the other two:

1. ``_read_rns_instance_name`` asked ``~/.reticulum/config`` FIRST via
   ``get_real_user_home()``, which under the ROOT watchdog service is
   ``/root`` — not the operator. It read a stale root config while rnsd
   ran ``--config /etc/reticulum``.
2. Linux answers a nonexistent ABSTRACT socket with ECONNREFUSED, never
   ENOENT, so the probe's "listener absent" branch was unreachable and
   permanent misconfiguration was indistinguishable from a transient
   rnsd shutdown.
3. ``probe_rns_namespace_collision`` noted ``clean`` after matching zero
   listeners — a detector reporting healthy while observing nothing.

Every test here PINS its inputs (``ss_output``, a tmp_path config tree).
None may consult the real box: an earlier generation of these tests read
the live listener table and would answer differently on a dev laptop, a
fleet box, and CI (feedback_tests_must_pin_ambient_state).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.watchdog_probe_core import (  # noqa: E402
    SIGNAL_CLASSES,
    collect_dispositions,
    reset_dispositions,
)
from utils.watchdog_probes_rns import (  # noqa: E402
    _rns_listener_tokens,
    _token_is_ours,
    probe_rns_namespace_collision,
    probe_rns_shared_instance_responsive,
)

# The kernel's DISPLAY form: ss splits columns on whitespace, so the
# spaced instance name "volcano ai rns" shows truncated as "@rns/volcano".
_SS_SPACED_LISTENER = """\
Netid State    Recv-Q Send-Q Local Address:Port Peer Address:Port Process
u_str LISTEN   0      0      @rns/volcano 13617 * 0 users:(("rnsd",pid=1349,fd=4))
"""

_SS_NO_RNS_LISTENER = """\
Netid State    Recv-Q Send-Q Local Address:Port Peer Address:Port Process
u_str LISTEN   0      0      /run/dbus/system_bus_socket 99 * 0 users:(("dbus",pid=1,fd=3))
"""

_REAL_NAME = "volcano ai rns"


@pytest.fixture
def dispositions():
    reset_dispositions()
    return collect_dispositions


# ─────────────────────────────────────────────────────────────────────
# Defect 3 — a detector may not report clean while observing nothing
# ─────────────────────────────────────────────────────────────────────


class TestNamespaceCollisionNeverCleanWhileDark:
    """It matched zero listeners. That is only ``clean`` when there is no
    @rns/* listener AT ALL; if the kernel advertises a different one, the
    probe is looking for a name this box does not serve and is DARK."""

    def test_zero_matches_with_other_listener_is_not_clean(
            self, tmp_path, dispositions):
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_mock_ss(_SS_SPACED_LISTENER)):
            sig = probe_rns_namespace_collision(
                "SomeOtherName", proc_root=str(tmp_path))
        assert sig is None, "collision signal is not this probe's to raise here"
        got = dispositions()["rns_namespace_collision"]
        assert got["disp"] == "indeterminate", (
            "matched zero listeners while @rns/volcano exists — reporting "
            "clean is the exact 2026-08-05 defect")
        assert "does not exist here" in got["reason"]

    def test_zero_matches_with_no_rns_listener_at_all_is_clean(
            self, tmp_path, dispositions):
        """The honest clean: nothing is listening, so there IS no collision."""
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_mock_ss(_SS_NO_RNS_LISTENER)):
            sig = probe_rns_namespace_collision(
                _REAL_NAME, proc_root=str(tmp_path))
        assert sig is None
        assert dispositions()["rns_namespace_collision"]["disp"] == "clean"

    def test_ss_nonzero_stays_indeterminate(self, tmp_path, dispositions):
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_mock_ss("", returncode=1)):
            probe_rns_namespace_collision(_REAL_NAME, proc_root=str(tmp_path))
        got = dispositions()["rns_namespace_collision"]
        assert got["disp"] == "indeterminate"
        assert "unobservable" in got["reason"]


# ─────────────────────────────────────────────────────────────────────
# Defect 2 — ECONNREFUSED is three facts wearing one costume
# ─────────────────────────────────────────────────────────────────────


class TestRefusedBranchDisambiguates:
    """A refused connect must be split by the listener table, because on
    Linux a nonexistent abstract address and a genuinely-refusing daemon
    both raise ECONNREFUSED."""

    NOPE = "watchdog-test-no-listener-abc123"

    def test_refused_with_foreign_listener_fires_mismatch(self):
        sig = probe_rns_shared_instance_responsive(
            self.NOPE, timeout_s=0.5, ss_output=_SS_SPACED_LISTENER)
        assert sig is not None, (
            "a name with no listener, while another @rns/* exists, is a "
            "config fault — it sat indeterminate for 8.8 days")
        assert sig.cls == "rns_instance_name_mismatch"
        assert sig.severity == "degraded"
        assert sig.extra["probed_name"] == self.NOPE
        assert sig.extra["listener_tokens"] == ["volcano"]

    def test_refused_with_empty_table_is_indeterminate(self, dispositions):
        """No @rns/* at all → rnsd is down; service_inactive owns that."""
        sig = probe_rns_shared_instance_responsive(
            self.NOPE, timeout_s=0.5, ss_output=_SS_NO_RNS_LISTENER)
        assert sig is None
        got = dispositions()["rns_shared_instance_unresponsive"]
        assert got["disp"] == "indeterminate"
        assert "service_inactive owns" in got["reason"]

    def test_refused_with_unobservable_table_is_indeterminate(
            self, dispositions):
        """ss unavailable must never read as 'no listeners exist'."""
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=FileNotFoundError("no ss")):
            sig = probe_rns_shared_instance_responsive(
                self.NOPE, timeout_s=0.5)
        assert sig is None
        got = dispositions()["rns_shared_instance_unresponsive"]
        assert got["disp"] == "indeterminate"
        assert "unobservable" in got["reason"]

    def test_truncated_display_of_our_own_spaced_name_is_not_a_mismatch(
            self, dispositions):
        """UNDER-FIRE guard: ss shows our spaced name truncated, so
        '@rns/volcano' may BE ours. Ambiguous → indeterminate, never a
        page. A false 'yes' costs a missed mismatch; a false 'no' would
        page every spaced-name box on the fleet.

        ⚠️ The name must be one NOTHING can be listening on, so the
        connect deterministically refuses and we exercise the branch.
        Using the fleet's real spaced name here passed on a laptop and
        failed on this box, where that listener actually exists — the
        very ambient-state trap this file's header warns about.
        """
        spaced_but_absent = "volcano zz-no-such-instance-9f3a"
        sig = probe_rns_shared_instance_responsive(
            spaced_but_absent, timeout_s=0.5, ss_output=_SS_SPACED_LISTENER)
        assert sig is None, (
            "'@rns/volcano' could be the truncated display of this very "
            "name — ambiguity must not page")
        got = dispositions()["rns_shared_instance_unresponsive"]
        assert got["disp"] == "indeterminate"


class TestListenerTableHelpers:
    def test_tokens_are_extracted_from_display_form(self):
        assert _rns_listener_tokens(ss_output=_SS_SPACED_LISTENER) == ["volcano"]

    def test_empty_table_is_a_list_not_none(self):
        """[] means 'observed, nothing there'; None means 'could not
        observe'. Collapsing them is honest_failure_modes #2."""
        assert _rns_listener_tokens(ss_output=_SS_NO_RNS_LISTENER) == []

    def test_unobservable_table_is_none(self):
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=OSError("boom")):
            assert _rns_listener_tokens() is None

    def test_nonzero_returncode_is_none(self):
        with patch("utils.watchdog_probes_rns.subprocess.run",
                   side_effect=_mock_ss("@rns/x", returncode=2)):
            assert _rns_listener_tokens() is None

    @pytest.mark.parametrize("token,name,expected", [
        ("volcano", "volcano ai rns", True),    # truncated display of ours
        ("volcano", "volcano", True),           # exact
        ("volcano", "volcano-x", False),        # prefix must not match
        ("volcano-x", "volcano", False),
        ("other", "volcano ai rns", False),
    ])
    def test_token_ownership(self, token, name, expected):
        assert _token_is_ours(token, name) is expected


# ─────────────────────────────────────────────────────────────────────
# Defect 1 — the name must come from the config rnsd actually runs with
# ─────────────────────────────────────────────────────────────────────


class TestInstanceNameSourceIsRnsdsConfig:
    """``get_real_user_home()`` is ``/root`` under the root watchdog
    service, so a stale ``/root/.reticulum/config`` outranked the config
    rnsd was actually started with."""

    def _write(self, root: Path, rel: str, name: str) -> Path:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"[reticulum]\n  instance_name = {name}\n")
        return p

    def test_rnsd_config_outranks_home_config(self, tmp_path):
        from utils import watchdog_runner

        home_cfg = tmp_path / "roothome"
        self._write(home_cfg, ".reticulum/config", "StaleRootName")
        rnsd_dir = tmp_path / "etc" / "reticulum"
        self._write(tmp_path, "etc/reticulum/config", "the real name")

        with patch("utils.rns_alignment.rnsd_configdir_of_record",
                   return_value=rnsd_dir), \
             patch("utils.paths.get_real_user_home", return_value=home_cfg):
            got = watchdog_runner._read_rns_instance_name()

        assert got == "the real name", (
            "the stale home config won — this is the 2026-08-05 defect")

    def test_falls_back_to_home_when_no_rnsd_unit(self, tmp_path):
        from utils import watchdog_runner

        home_cfg = tmp_path / "operatorhome"
        self._write(home_cfg, ".reticulum/config", "home name")

        with patch("utils.rns_alignment.rnsd_configdir_of_record",
                   return_value=None), \
             patch("utils.paths.get_real_user_home", return_value=home_cfg):
            got = watchdog_runner._read_rns_instance_name()

        assert got == "home name"

    def test_unreadable_source_yields_none_not_a_guess(self, tmp_path):
        """The seam degrades to None — it must not crash the tick, and it
        must not invent a default. The caller marks both RNS probes
        ``inert`` on None; guessing 'default' would silently point them at
        a name this box may not serve, which is the very defect this whole
        file exists to prevent."""
        from utils import watchdog_runner

        with patch("utils.rns_alignment.read_rns_instance_name",
                   side_effect=RuntimeError("stripped box")):
            assert watchdog_runner._read_rns_instance_name() is None

    def test_inner_reader_never_raises(self, tmp_path):
        """Every leg inside is individually guarded, so the seam's except
        is a backstop and not the load-bearing path.

        ⚠️ Asserts only that it does not RAISE. The last candidate is a
        hardcoded ``/etc/reticulum``, which no patch here can shadow — so
        asserting a RETURN VALUE would read the real box and answer
        differently on a fleet box vs CI. (This test asserted
        ``in (None, "default")`` for exactly one commit and failed
        immediately on a box that has an /etc/reticulum config.)
        """
        from utils.rns_alignment import read_rns_instance_name

        with patch("utils.rns_alignment.rnsd_configdir_of_record",
                   return_value=None), \
             patch("utils.paths.get_real_user_home",
                   return_value=tmp_path / "nonexistent"):
            read_rns_instance_name()  # must not raise


class TestConfigdirOfRecordParsesTheUnit:
    """systemd's last-non-empty-``ExecStart=`` wins, so a drop-in that
    resets and re-declares changes the answer. Missing a drop-in reads
    the wrong daemon's config."""

    def test_last_execstart_wins_over_earlier_ones(self, tmp_path):
        from utils.rns_alignment import (
            _read_systemd_unit, _resolve_rnsd_configdir,
        )
        base = tmp_path / "rnsd.service"
        base.write_text(
            "[Service]\nUser=root\n"
            "ExecStart=/home/op/.local/bin/rnsd --service\n")
        dropin = tmp_path / "10-configdir.conf"
        dropin.write_text(
            "[Service]\nUser=operator\nExecStart=\n"
            "ExecStart=/usr/local/bin/rnsd --config /etc/reticulum --service\n")

        unit = _read_systemd_unit(str(base), str(dropin))
        assert unit["user"] == "operator"
        assert _resolve_rnsd_configdir(
            unit["user"], unit["exec_start"]) == Path("/etc/reticulum")

    def test_no_config_flag_falls_back_to_units_user_home(self):
        from utils.rns_alignment import _resolve_rnsd_configdir
        assert _resolve_rnsd_configdir(
            "operator", "/usr/bin/rnsd --service") == Path(
                "/home/operator/.reticulum")


# ─────────────────────────────────────────────────────────────────────
# Closed-enum coverage (honest_failure_modes #7)
# ─────────────────────────────────────────────────────────────────────


def test_new_signal_class_is_registered():
    assert "rns_instance_name_mismatch" in SIGNAL_CLASSES


def _mock_ss(stdout, returncode=0):
    class _Result:
        def __init__(self):
            self.stdout = stdout
            self.returncode = returncode

    def _runner(*args, **kwargs):
        return _Result()
    return _runner
