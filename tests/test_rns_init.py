"""Tests for src/utils/rns_init.py — the guarded RNS-init chokepoint.

Covers the RNS T2-isolate arc sub-arc C: the #68 bounded AF_UNIX connect
probe (fail-OPEN on a wedged rnsd), the host-race guard (require_listener),
idempotent singleton reuse, and the #69 fail-LOUD foreign-owner path as it
flows through ``open_reticulum``. The #69 preflight internals
(``check_rns_listener_owner``) and the construct watchdog
(``init_reticulum_with_watchdog`` / ``bounded_block``) are exercised in
``test_lab_common.py`` (same functions, re-exported).
"""

import os
import socket
import sys

import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import utils.rns_init as ri  # noqa: E402


# --------------------------------------------- _shared_instance_listener_present


@pytest.fixture(autouse=True)
def _allow_rns_tx_in_this_file():
    """This whole file exercises RNS/LXMF paths against MOCKED routers, so it
    declares RNS egress once here rather than per test. The RNS egress guard
    (utils.tx_guard) is fail-closed and cannot tell a mocked router from a real
    one — declaring the intent is the point."""
    from utils import tx_guard
    with tx_guard.allow_rns_egress():
        yield


class TestListenerPresent:
    """Passive /proc/net/unix presence scan."""

    def test_absent_when_no_matching_socket(self):
        assert ri._shared_instance_listener_present(
            "definitely-not-a-real-instance-9b2f"
        ) is False

    def test_present_for_real_abstract_socket(self):
        name = "rns-init-test-present-7c1a"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Abstract namespace: kernel renders the leading NUL as '@', so
            # /proc/net/unix shows "@rns/<name>" — exactly what the function
            # searches for.
            sock.bind("\0rns/" + name)
            sock.listen(1)
            assert ri._shared_instance_listener_present(name) is True
        finally:
            sock.close()


# --------------------------------------------- _probe_shared_instance_connect


class TestConnectProbe:
    """The #68 fail-open gate: active, bounded connect."""

    def test_true_against_a_real_accepting_socket(self):
        name = "rns-init-test-probe-ok-3d5e"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind("\0rns/" + name)
            server.listen(1)
            # A listening socket completes the connect handshake into its
            # accept queue even without an explicit accept() call, so the
            # probe returns True quickly.
            assert ri._probe_shared_instance_connect(name, 2.0) is True
        finally:
            server.close()

    def test_false_on_timeout(self):
        """A connect that blocks past the budget -> wedged -> False (#68)."""
        fake = MagicMock()
        fake.connect.side_effect = TimeoutError()
        with patch.object(ri.socket, "socket", return_value=fake):
            assert ri._probe_shared_instance_connect("inst", 0.5) is False
        fake.close.assert_called_once()

    def test_false_on_connection_refused(self):
        """Listener gone / not accepting -> False (distinct from wedge)."""
        fake = MagicMock()
        fake.connect.side_effect = ConnectionRefusedError()
        with patch.object(ri.socket, "socket", return_value=fake):
            assert ri._probe_shared_instance_connect("inst", 0.5) is False
        fake.close.assert_called_once()

    def test_false_when_socket_absent(self):
        """Probing a non-existent abstract socket refuses fast (real call)."""
        assert ri._probe_shared_instance_connect(
            "no-such-instance-ff01", 1.0
        ) is False


# --------------------------------------------- open_reticulum decision logic


class TestOpenReticulum:

    def test_returns_none_when_rns_unavailable(self):
        with patch.object(ri, "_HAS_RNS", False):
            assert ri.open_reticulum("/tmp/x") is None

    def test_returns_existing_singleton_idempotent(self):
        sentinel = object()
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=sentinel), \
             patch.object(ri, "_construct_reticulum_with_watchdog") as construct:
            assert ri.open_reticulum("/tmp/x") is sentinel
            construct.assert_not_called()

    def test_foreign_listener_raises_fail_loud(self):
        """#69: a foreign @rns owner propagates RuntimeError, never constructs."""
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config", return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner",
                          side_effect=RuntimeError("owned by pid=999 cmd='rogue'")), \
             patch.object(ri, "_construct_reticulum_with_watchdog") as construct:
            with pytest.raises(RuntimeError, match="rogue"):
                ri.open_reticulum("/tmp/x")
            construct.assert_not_called()

    def test_require_listener_absent_degrades_no_construct(self):
        """Host-race guard: a pure consumer never constructs when @rns absent.

        ``_rnsd_unit_enabled`` is patched False so the test stays hermetic —
        on a fleet box the real ``systemctl is-enabled rnsd`` says enabled
        and the #69 boot-race wait would block for its full timeout.
        """
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config", return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_shared_instance_listener_present", return_value=False), \
             patch.object(ri, "_rnsd_unit_enabled", return_value=False), \
             patch.object(ri, "_construct_reticulum_with_watchdog") as construct:
            assert ri.open_reticulum("/tmp/x", require_listener=True) is None
            construct.assert_not_called()

    def test_standalone_constructs_when_listener_absent(self):
        """require_listener=False + rnsd NOT enabled: standalone init is
        legitimate (e.g. gateway with no rnsd)."""
        sentinel = object()
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config", return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_shared_instance_listener_present", return_value=False), \
             patch.object(ri, "_rnsd_unit_enabled", return_value=False), \
             patch.object(ri, "_construct_reticulum_with_watchdog",
                          return_value=sentinel) as construct:
            assert ri.open_reticulum("/tmp/x", require_listener=False) is sentinel
            construct.assert_called_once()

    def test_wedged_listener_degrades_no_construct(self):
        """#68: listener present but connect probe times out -> None, no construct."""
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config", return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_shared_instance_listener_present", return_value=True), \
             patch.object(ri, "_probe_shared_instance_connect", return_value=False), \
             patch.object(ri, "_construct_reticulum_with_watchdog") as construct:
            assert ri.open_reticulum("/tmp/x", require_listener=True) is None
            construct.assert_not_called()

    def test_healthy_listener_constructs(self):
        sentinel = object()
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config", return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_shared_instance_listener_present", return_value=True), \
             patch.object(ri, "_probe_shared_instance_connect", return_value=True), \
             patch.object(ri, "_construct_reticulum_with_watchdog",
                          return_value=sentinel) as construct:
            assert ri.open_reticulum("/tmp/x", require_listener=True) is sentinel
            construct.assert_called_once()

    def test_probe_false_bypasses_gate(self):
        """probe=False skips the #68 connect probe entirely (test seam)."""
        sentinel = object()
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config", return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_probe_shared_instance_connect") as probe, \
             patch.object(ri, "_construct_reticulum_with_watchdog",
                          return_value=sentinel):
            assert ri.open_reticulum("/tmp/x", probe=False) is sentinel
            probe.assert_not_called()

    def test_no_instance_name_falls_back_to_configured(self):
        """configdir without instance_name -> fall back to box's configured one
        so the preflight/probe still have a target."""
        sentinel = object()
        fake_paths = MagicMock()
        fake_paths.get_configured_instance_name.return_value = "boxinst"
        seen = {}

        def _record_check(instance_name):
            seen["checked"] = instance_name
            return None

        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config", return_value=None), \
             patch.dict(sys.modules, {"utils.paths": MagicMock(ReticulumPaths=fake_paths)}), \
             patch.object(ri, "check_rns_listener_owner", side_effect=_record_check), \
             patch.object(ri, "_shared_instance_listener_present", return_value=True), \
             patch.object(ri, "_probe_shared_instance_connect", return_value=True), \
             patch.object(ri, "_construct_reticulum_with_watchdog",
                          return_value=sentinel):
            assert ri.open_reticulum("/tmp/x") is sentinel
            assert seen.get("checked") == "boxinst"


# --------------------------------------------- #69 boot-race guard


class TestBootRaceGuard:
    """Issue #69 boot race (the federator box, 2026-06-06): a client service starting
    before an enabled rnsd must wait for rnsd to claim ``@rns/<instance>``,
    never boot-claim it. lab echo claimed the instance 4s before rnsd
    started; rnsd silently joined as an interface-less client and every RNS
    destination on the box was no-route until manual recovery."""

    def test_absent_listener_rnsd_enabled_waits_then_joins(self):
        """Listener appears during the wait -> proceed as client."""
        sentinel = object()
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config", return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_shared_instance_listener_present", return_value=False), \
             patch.object(ri, "_rnsd_unit_enabled", return_value=True), \
             patch.object(ri, "_wait_for_rnsd_listener", return_value=True) as wait, \
             patch.object(ri, "_probe_shared_instance_connect", return_value=True), \
             patch.object(ri, "_construct_reticulum_with_watchdog",
                          return_value=sentinel) as construct:
            assert ri.open_reticulum("/tmp/x") is sentinel
            wait.assert_called_once_with("inst")
            construct.assert_called_once()

    def test_absent_listener_rnsd_enabled_never_appears_degrades(self):
        """rnsd enabled but never claims -> None even with
        require_listener=False. Constructing standalone would poison the box
        the moment rnsd recovers."""
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config", return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_shared_instance_listener_present", return_value=False), \
             patch.object(ri, "_rnsd_unit_enabled", return_value=True), \
             patch.object(ri, "_wait_for_rnsd_listener", return_value=False), \
             patch.object(ri, "_construct_reticulum_with_watchdog") as construct:
            assert ri.open_reticulum("/tmp/x", require_listener=False) is None
            construct.assert_not_called()

    def test_wait_helper_returns_true_when_listener_appears(self):
        present = iter([False, False, True])
        with patch.object(ri, "_shared_instance_listener_present",
                          side_effect=lambda _name: next(present)):
            assert ri._wait_for_rnsd_listener(
                "inst", timeout_s=2.0, poll_interval_s=0.01
            ) is True

    def test_wait_helper_returns_false_on_timeout(self):
        with patch.object(ri, "_shared_instance_listener_present",
                          return_value=False):
            assert ri._wait_for_rnsd_listener(
                "inst", timeout_s=0.05, poll_interval_s=0.01
            ) is False

    def test_rnsd_unit_enabled_false_on_import_error(self):
        """Any failure resolving service_check degrades to no-wait (legacy
        behavior) rather than blocking RNS init."""
        with patch.dict(sys.modules, {"utils.service_check": None}):
            assert ri._rnsd_unit_enabled() is False

    def test_lab_daemon_tmpdir_configdir_arms_the_guard(self, tmp_path):
        """THE #69 regression (moc5, 2026-09-19).

        The lab echo/tracer daemons are handed a TMPDIR configdir — the
        shipped units set no ``MESHFORGE_LAB_RNS_CONFIGDIR`` — so it holds no
        ``config`` file at all. The guard used to read only the explicit
        ``instance_name`` directive, get None, and skip itself entirely via
        ``if instance_name:`` while RNS went on to bind the kernel-global
        ``@rns/default`` anyway. echo won that unguarded race at boot, rnsd
        lost its bind, and the box had no RNS for 42 minutes.

        With an enabled rnsd that never claims, this MUST raise rather than
        boot-claim. Nothing is patched about the name resolution: the point is
        that an EMPTY configdir still resolves to a real target.
        """
        assert not (tmp_path / "config").exists()
        with patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_shared_instance_listener_present",
                          return_value=False), \
             patch.object(ri, "_rnsd_unit_enabled", return_value=True), \
             patch.object(ri, "_wait_for_rnsd_listener",
                          return_value=False) as wait, \
             patch.object(ri, "_construct_reticulum_with_watchdog") as construct:
            with pytest.raises(RuntimeError, match="boot-claim"):
                ri.init_reticulum_with_watchdog(str(tmp_path))
            wait.assert_called_once_with(ri.RNS_DEFAULT_INSTANCE_NAME)
            construct.assert_not_called()


# --------------------------------------------- _guard_instance_name


class TestGuardInstanceNameAiming:
    """#69: the guard must aim at the socket THIS process will bind.

    Arming it is half the job — pointing it at a name the box happens to
    declare elsewhere is the 2026-08-05 "probing a name this box doesn't
    serve" defect wearing a guard's clothes.
    """

    def test_explicit_directive_wins(self, tmp_path):
        (tmp_path / "config").write_text(
            "[reticulum]\n  instance_name = <site> rns\n", encoding="utf-8")
        assert ri._guard_instance_name(str(tmp_path)) == "<site> rns"

    def test_config_without_directive_aims_at_default(self, tmp_path):
        """`/etc/reticulum` on the fleet: share_instance=Yes, no name. RNS
        binds `@rns/default`, so that is what the guard must watch."""
        (tmp_path / "config").write_text(
            "[reticulum]\n  share_instance = Yes\n", encoding="utf-8")
        assert ri._guard_instance_name(str(tmp_path)) == "default"

    def test_absent_config_aims_at_default(self, tmp_path):
        """The lab daemons' tmpdir — RNS writes its template here and the
        template names the instance `default`. Absent config is NOT 'no
        target' (honest_failure_modes #1: the degraded value must not
        overlap the healthy domain)."""
        assert ri._guard_instance_name(str(tmp_path)) == "default"

    def test_no_configdir_asks_the_box(self):
        """Only with NO configdir does RNS use its own default location —
        then, and only then, the box's config is the right authority."""
        fake = MagicMock()
        fake.get_configured_instance_name.return_value = "box name"
        with patch.dict(sys.modules, {"utils.paths": MagicMock(
                ReticulumPaths=fake)}):
            assert ri._guard_instance_name(None) == "box name"

    def test_box_config_is_not_consulted_when_configdir_given(self, tmp_path):
        """The mis-aim this fix exists to prevent: moc5's `~/.reticulum` says
        `<site> rns` while a tmpdir/`/etc/reticulum` caller binds
        `@rns/default`. Waiting on the box's name would watch a socket the
        caller never binds."""
        fake = MagicMock()
        fake.get_configured_instance_name.return_value = "<site> rns"
        with patch.dict(sys.modules, {"utils.paths": MagicMock(
                ReticulumPaths=fake)}):
            assert ri._guard_instance_name(str(tmp_path)) == "default"
        fake.get_configured_instance_name.assert_not_called()

    def test_default_name_constant_matches_rns_alignment(self):
        """One constant, two consumers (honest_failure_modes #5). The two
        modules stay acyclic by design, so they are TEST-pinned rather than
        importing one another."""
        from utils.rns_alignment import RNS_DEFAULT_INSTANCE_NAME as other
        assert ri.RNS_DEFAULT_INSTANCE_NAME == other


# --------------------------------------------- _existing_instance


class TestExistingInstance:

    def test_none_when_rns_unavailable(self):
        with patch.object(ri, "_HAS_RNS", False):
            assert ri._existing_instance() is None

    def test_none_when_get_instance_raises(self):
        fake_rns = MagicMock()
        fake_rns.Reticulum.get_instance.side_effect = RuntimeError("boom")
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_RNS", fake_rns):
            assert ri._existing_instance() is None

    def test_returns_instance_when_present(self):
        sentinel = object()
        fake_rns = MagicMock()
        fake_rns.Reticulum.get_instance.return_value = sentinel
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_RNS", fake_rns):
            assert ri._existing_instance() is sentinel


# --------------------------------------------- construct lock + reinitialise

class TestConstructConcurrencyAndReinitialise:
    """The chokepoint owns singleton idempotency under concurrency (2026-07-09
    frontier review of the rns_init seam): two racing threads produce ONE
    construct, and RNS's 'Attempt to reinitialise' OSError is absorbed into
    the existing instance — never re-exported to callers (module docstring
    contract #2; rns_bridge and the map collector had each re-implemented
    the catch)."""

    def test_concurrent_callers_single_construct(self):
        import threading
        import time as _time

        holder = {"inst": None}
        construct_calls = []
        instance = object()

        def fake_existing():
            return holder["inst"]

        def fake_construct(configdir, *, loglevel, timeout_s):
            construct_calls.append(threading.get_ident())
            _time.sleep(0.15)  # widen the race window
            holder["inst"] = instance
            return instance

        results = []
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", side_effect=fake_existing), \
             patch.object(ri, "_read_instance_name_from_config",
                          return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_construct_reticulum_with_watchdog",
                          side_effect=fake_construct):
            threads = [
                threading.Thread(
                    target=lambda: results.append(
                        ri.open_reticulum("/tmp/x", probe=False)))
                for _ in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert len(construct_calls) == 1, (
            f"expected exactly one construct, got {len(construct_calls)}")
        assert results == [instance, instance]

    def test_reinitialise_absorbed_returns_existing(self):
        sentinel = object()
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance",
                          side_effect=[None, None, sentinel]), \
             patch.object(ri, "_read_instance_name_from_config",
                          return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(
                 ri, "_construct_reticulum_with_watchdog",
                 side_effect=OSError("Attempt to reinitialise Reticulum")):
            assert ri.open_reticulum("/tmp/x", probe=False) is sentinel

    def test_reinitialise_without_existing_reraises(self):
        """Absorption requires a real instance to hand back — a reinitialise
        error with NO retrievable singleton stays loud (never maps a broken
        state to a healthy-looking return)."""
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config",
                          return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(
                 ri, "_construct_reticulum_with_watchdog",
                 side_effect=OSError("Attempt to reinitialise Reticulum")):
            with pytest.raises(OSError, match="reinitialise"):
                ri.open_reticulum("/tmp/x", probe=False)

    def test_unrelated_oserror_propagates(self):
        with patch.object(ri, "_HAS_RNS", True), \
             patch.object(ri, "_existing_instance", return_value=None), \
             patch.object(ri, "_read_instance_name_from_config",
                          return_value="inst"), \
             patch.object(ri, "check_rns_listener_owner", return_value=None), \
             patch.object(ri, "_construct_reticulum_with_watchdog",
                          side_effect=OSError("boom")):
            with pytest.raises(OSError, match="boom"):
                ri.open_reticulum("/tmp/x", probe=False)
