"""scripts/dep_advisory_check.py — the fleet's installed-version advisory check.

These tests exist for ONE defect class, the one that produced the 2026-09-04
incident this script was written after: **a degraded read rendering as a
healthy answer.** Every surface that day was honest about the thing it
measured and wrong about the fleet —

  * a pin ceiling forbidding the patched version, with every box "compliant";
  * ``probe_dep_version_drift`` reading ``clean`` because it only ever asks
    about the floor;
  * Dependabot reporting ONE medium where the installed version carried FOUR
    advisories, three of them high, then flipping to ``fixed`` untouched.

So the assertions below are deliberately lopsided toward the error paths. The
happy path gets one test; "a broken query must not look clean" gets several,
because that is the branch that actually hurts.
"""

import importlib.util
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

_spec = importlib.util.spec_from_file_location(
    "dep_advisory_check", os.path.join(_ROOT, "scripts", "dep_advisory_check.py"))
dac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dac)


ADV_HIGH = [{"ghsa_id": "GHSA-xxxx-yyyy-zzzz", "severity": "high"}]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(dac, "get_real_user_home", lambda: str(tmp_path))
    return tmp_path


def _install_runner(monkeypatch, *, gh_auth_rc=0, versions=None, version_rc=0,
                    advisories=ADV_HIGH, advisory_rc=0, advisory_body=None):
    """Drive the script by replacing its ONE subprocess chokepoint."""
    import json as _json

    def fake_run(cmd, timeout, stdin_text=None):
        if cmd[:3] == ["gh", "auth", "status"]:
            return gh_auth_rc, "", ""
        if cmd[0] == "gh" and cmd[1] == "api":
            if advisory_rc != 0:
                return advisory_rc, "", "boom"
            if advisory_body is not None:
                return 0, advisory_body, ""
            return 0, _json.dumps(advisories), ""
        if cmd[0] == "ssh":
            if version_rc != 0:
                return version_rc, "", "ssh died"
            return 0, _json.dumps(versions or {"cryptography": "46.0.7"}), ""
        raise AssertionError("unexpected command: %r" % (cmd,))

    monkeypatch.setattr(dac, "_run", fake_run)


def _run_main(home, monkeypatch, **kw):
    _install_runner(monkeypatch, **kw)
    return dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])


class TestABrokenQueryIsNeverClean:
    """THE test. An advisory query that FAILED must not be reported as
    'no advisories' — that is how a vulnerable fleet reads healthy."""

    def test_failed_query_returns_none_not_empty_list(self, monkeypatch):
        _install_runner(monkeypatch, advisory_rc=1)
        advs, err = dac.query_advisories("cryptography", "46.0.7")
        assert advs is None and err, (advs, err)
        assert advs != [], "a failed query must never be indistinguishable from zero advisories"

    def test_unparseable_response_is_unknown(self, monkeypatch):
        _install_runner(monkeypatch, advisory_body="not json at all")
        advs, err = dac.query_advisories("cryptography", "46.0.7")
        assert advs is None and "unparseable" in err

    def test_non_list_response_is_unknown(self, monkeypatch):
        _install_runner(monkeypatch, advisory_body='{"message":"rate limited"}')
        advs, err = dac.query_advisories("cryptography", "46.0.7")
        assert advs is None and "not a list" in err

    def test_failed_query_exits_unknown_not_zero(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, advisory_rc=1)
        assert rc == 2, "a fleet we could not assess must never exit 0"
        assert "UNKNOWN" in (home / ".meshforge-dep-advisories").read_text()


class TestSuccessfulEmptyIsAPositiveFinding:
    def test_empty_list_from_a_working_query_is_clean(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, advisories=[])
        assert rc == 0
        assert not (home / ".meshforge-dep-ADVISORY").exists()
        assert "clean" in (home / ".meshforge-dep-advisories").read_text()


class TestFindings:
    def test_advisory_exits_one_and_writes_the_finding_file(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, advisories=ADV_HIGH)
        assert rc == 1
        body = (home / ".meshforge-dep-ADVISORY").read_text()
        assert "GHSA-xxxx-yyyy-zzzz" in body and "boxA" in body

    def test_severity_is_carried_into_the_summary(self):
        assert "high" in dac.summarize(ADV_HIGH)


class TestUnobservableIsNotHealthy:
    def test_unreachable_box_is_unknown_never_clean(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, version_rc=255)
        assert rc == 2
        txt = (home / ".meshforge-dep-advisories").read_text()
        assert "UNKNOWN" in txt and "clean" not in txt

    def test_no_box_observed_exits_unknown_even_with_zero_findings(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, version_rc=255, advisories=[])
        assert rc == 2, ("zero findings from an observation that never happened "
                         "is not a clean fleet")

    def test_partial_blindness_does_not_delete_a_standing_finding(self, home, monkeypatch):
        stale = home / ".meshforge-dep-ADVISORY"
        stale.write_text("previous finding\n")
        _install_runner(monkeypatch, advisories=[])
        # boxA answers clean; boxB is unreachable -> partial view.
        real_collect = dac.collect_installed

        def half_blind(host, packages, timeout=40):
            if host == "boxB":
                return None, "unreachable"
            return real_collect(host, packages, timeout)

        monkeypatch.setattr(dac, "collect_installed", half_blind)
        rc = dac.main(["--host", "boxA", "--host", "boxB",
                       "--packages", "cryptography", "--quiet"])
        assert rc == 2
        assert stale.exists(), "a partial view must not clear a standing finding"

    def test_unauthenticated_gh_is_unknown(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, gh_auth_rc=1)
        assert rc == 2
        assert "NOT checked" in (home / ".meshforge-dep-advisories").read_text()


class TestAbsentPackageIsInert:
    def test_package_not_installed_is_not_a_finding(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, versions={"cryptography": None})
        assert rc == 0, "absent by design must read inert, never as an advisory"
        assert not (home / ".meshforge-dep-ADVISORY").exists()


class TestFleetHostList:
    def test_unreadable_host_list_is_an_error_not_an_empty_fleet(self, home):
        hosts, err = dac.read_fleet_hosts(explicit=str(home / "nope"))
        assert hosts == [] and err, "a missing list must not silently mean 'no boxes'"

    def test_empty_host_list_is_an_error(self, home):
        f = home / "hosts"
        f.write_text("# only comments\n")
        hosts, err = dac.read_fleet_hosts(explicit=str(f))
        assert hosts == [] and "empty" in err

    def test_comments_and_blanks_are_skipped(self, home):
        f = home / "hosts"
        f.write_text("# c\n\nmoc\nmoc3\n")
        hosts, err = dac.read_fleet_hosts(explicit=str(f))
        assert hosts == ["moc", "moc3"] and err is None


class TestQueriesAreSharedAcrossBoxes:
    def test_identical_version_on_many_boxes_queries_once(self, home, monkeypatch):
        calls = []
        import json as _json

        def fake_run(cmd, timeout, stdin_text=None):
            if cmd[:3] == ["gh", "auth", "status"]:
                return 0, "", ""
            if cmd[0] == "gh":
                calls.append(cmd[2])
                return 0, "[]", ""
            return 0, _json.dumps({"cryptography": "46.0.7"}), ""

        monkeypatch.setattr(dac, "_run", fake_run)
        rc = dac.main(["--host", "a", "--host", "b", "--host", "c",
                       "--packages", "cryptography", "--quiet"])
        assert rc == 0
        assert len(calls) == 1, ("three boxes on one version must cost ONE "
                                 "advisory query, got %d" % len(calls))
