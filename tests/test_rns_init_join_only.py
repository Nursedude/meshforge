"""open_reticulum join-only construct (#69, 2026-10-01).

When the probe has decided to JOIN a live rnsd, the constructor runs with
``require_shared_instance=True`` so RNS refuses to become the @rns host if rnsd
vanished in between — the check-then-construct race that made a client the
host and a returning rnsd its client. Passed only when the installed RNS
advertises a clean refusal (fork marker ``MF_REQUIRE_SHARED_RETRYABLE``,
rns 1.3.8+mf.4): stock RNS leaves a half-built singleton behind a refusal.
"""
import os
import sys
import types
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import utils.rns_init as ri  # noqa: E402


@pytest.fixture(autouse=True)
def _allow_rns_tx_in_this_file():
    from utils import tx_guard
    with tx_guard.allow_rns_egress():
        yield


def _gate(listener_present, probe_ok=True, rnsd_enabled=False):
    return [
        patch.object(ri, "_HAS_RNS", True),
        patch.object(ri, "_existing_instance", return_value=None),
        patch.object(ri, "_read_instance_name_from_config", return_value="inst"),
        patch.object(ri, "check_rns_listener_owner", return_value=None),
        patch.object(ri, "_shared_instance_listener_present", return_value=listener_present),
        patch.object(ri, "_probe_shared_instance_connect", return_value=probe_ok),
        patch.object(ri, "_rnsd_unit_enabled", return_value=rnsd_enabled),
    ]


def _run(gates, construct, **kw):
    from contextlib import ExitStack
    with ExitStack() as st:
        for g in gates:
            st.enter_context(g)
        st.enter_context(patch.object(ri, "_construct_reticulum_with_watchdog", construct))
        return ri.open_reticulum("/tmp/x", **kw)


class TestOpenReticulumDecidesJoinOnly:
    def test_healthy_listener_constructs_join_only(self):
        seen = {}

        def construct(configdir, **kw):
            seen.update(kw)
            return "inst"
        assert _run(_gate(True), construct, require_listener=True) == "inst"
        assert seen["join_only"] is True

    def test_gateway_joining_a_live_rnsd_is_join_only_too(self):
        # require_listener=False, but the probe found rnsd: still a JOIN.
        seen = {}

        def construct(configdir, **kw):
            seen.update(kw)
            return "inst"
        assert _run(_gate(True), construct, require_listener=False) == "inst"
        assert seen["join_only"] is True

    def test_standalone_is_never_join_only(self):
        seen = {}

        def construct(configdir, **kw):
            seen.update(kw)
            return "inst"
        assert _run(_gate(False, rnsd_enabled=False), construct, require_listener=False) == "inst"
        assert seen["join_only"] is False

    def test_refusal_while_joining_degrades_to_none(self):
        def construct(configdir, **kw):
            raise SystemError("No shared instance available, but application that started Reticulum required it")
        assert _run(_gate(True), construct, require_listener=True) is None

    def test_other_systemerror_while_joining_still_raises(self):
        def construct(configdir, **kw):
            raise SystemError("some unrelated RNS failure")
        with pytest.raises(SystemError):
            _run(_gate(True), construct, require_listener=True)

    def test_systemerror_when_not_joining_still_raises(self):
        def construct(configdir, **kw):
            raise SystemError("something else entirely")
        with pytest.raises(SystemError):
            _run(_gate(False, rnsd_enabled=False), construct, require_listener=False)


class _FakeReticulum:
    calls = []

    def __init__(self, **kw):
        _FakeReticulum.calls.append(kw)


@pytest.mark.parametrize("marker,expect_flag", [(True, True), (False, False)])
def test_construct_passes_flag_only_when_rns_advertises_clean_refusal(monkeypatch, marker, expect_flag):
    cls = type("Reticulum", (_FakeReticulum,), {})
    if marker:
        cls.MF_REQUIRE_SHARED_RETRYABLE = True
    monkeypatch.setitem(sys.modules, "RNS", types.SimpleNamespace(Reticulum=cls))
    _FakeReticulum.calls = []
    ri._construct_reticulum_with_watchdog("/tmp/x", loglevel=2, timeout_s=5, join_only=True)
    assert ("require_shared_instance" in _FakeReticulum.calls[-1]) is expect_flag


def test_construct_never_passes_flag_when_not_joining(monkeypatch):
    cls = type("Reticulum", (_FakeReticulum,), {"MF_REQUIRE_SHARED_RETRYABLE": True})
    monkeypatch.setitem(sys.modules, "RNS", types.SimpleNamespace(Reticulum=cls))
    _FakeReticulum.calls = []
    ri._construct_reticulum_with_watchdog("/tmp/x", loglevel=2, timeout_s=5)
    assert "require_shared_instance" not in _FakeReticulum.calls[-1]


# --- init_reticulum_with_watchdog (lab echo/tracer daemons) -----------------
# The 09-19 moc5 #69 incident came through THIS entry point, not
# open_reticulum; the 10-01 join-only change covered only open_reticulum
# (found by the 10-01 audit's contextless reader). These daemons restart on
# exit, so a join-only refusal here must RAISE loudly, never degrade.

def _lab_gate(listener_present, rnsd_enabled, wait_ok=True, name="inst"):
    return [
        patch.object(ri, "_guard_instance_name", return_value=name),
        patch.object(ri, "check_rns_listener_owner", return_value=None),
        patch.object(ri, "_shared_instance_listener_present", return_value=listener_present),
        patch.object(ri, "_rnsd_unit_enabled", return_value=rnsd_enabled),
        patch.object(ri, "_instance_name_was_declared", return_value=True),
        patch.object(ri, "_wait_for_rnsd_listener", return_value=wait_ok),
    ]


def _run_lab(gates, construct):
    from contextlib import ExitStack
    with ExitStack() as st:
        for g in gates:
            st.enter_context(g)
        st.enter_context(patch.object(ri, "_construct_reticulum_with_watchdog", construct))
        return ri.init_reticulum_with_watchdog("/tmp/x")


class TestLabEntryPointJoinOnly:
    @staticmethod
    def _recorder(seen):
        def construct(configdir, **kw):
            seen["join_only"] = kw.get("join_only", False)
            return "R"
        return construct

    def test_live_listener_constructs_join_only(self):
        seen = {}
        assert _run_lab(_lab_gate(True, rnsd_enabled=True), self._recorder(seen)) == "R"
        assert seen["join_only"] is True

    def test_listener_after_boot_wait_constructs_join_only(self):
        seen = {}
        _run_lab(_lab_gate(False, rnsd_enabled=True), self._recorder(seen))
        assert seen["join_only"] is True

    def test_no_rnsd_and_no_listener_may_host(self):
        seen = {}
        _run_lab(_lab_gate(False, rnsd_enabled=False), self._recorder(seen))
        assert seen["join_only"] is False

    def test_unresolvable_instance_name_is_not_join_only(self):
        seen = {}
        _run_lab(_lab_gate(True, rnsd_enabled=True, name=None), self._recorder(seen))
        assert seen["join_only"] is False

    def test_refusal_while_joining_raises_loud_69_error(self):
        def construct(configdir, **kw):
            raise SystemError("No shared instance available, but application that started Reticulum required it")
        with pytest.raises(RuntimeError, match="#69"):
            _run_lab(_lab_gate(True, rnsd_enabled=True), construct)

    def test_other_systemerror_while_joining_is_not_rewritten(self):
        def construct(configdir, **kw):
            raise SystemError("some unrelated RNS failure")
        with pytest.raises(SystemError):
            _run_lab(_lab_gate(True, rnsd_enabled=True), construct)


def test_join_only_on_rns_without_marker_leaves_a_warning(monkeypatch, caplog):
    cls = type("Reticulum", (_FakeReticulum,), {})
    monkeypatch.setitem(sys.modules, "RNS", types.SimpleNamespace(Reticulum=cls))
    _FakeReticulum.calls = []
    with caplog.at_level("WARNING"):
        ri._construct_reticulum_with_watchdog("/tmp/x", loglevel=2, timeout_s=5, join_only=True)
    assert "require_shared_instance" not in _FakeReticulum.calls[-1]
    assert "MF_REQUIRE_SHARED_RETRYABLE" in caplog.text
