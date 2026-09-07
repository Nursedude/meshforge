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


def _status(home, scoped=True):
    """The status file a run WRITES. Every ``main()`` drive in this module
    narrows by ``--host`` or ``--packages``, so it lands in the ``.scoped``
    sibling (2026-09-06) — pass ``scoped=False`` for the canonical fleet pair."""
    return home / (".meshforge-dep-advisories" + (dac.SCOPED_SUFFIX if scoped else ""))


def _finding(home, scoped=True):
    return home / (".meshforge-dep-ADVISORY" + (dac.SCOPED_SUFFIX if scoped else ""))


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
        assert "UNKNOWN" in (_status(home)).read_text()


class TestSuccessfulEmptyIsAPositiveFinding:
    def test_empty_list_from_a_working_query_is_clean(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, advisories=[])
        assert rc == 0
        assert not (_finding(home)).exists()
        assert "clean" in (_status(home)).read_text()


class TestFindings:
    def test_advisory_exits_one_and_writes_the_finding_file(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, advisories=ADV_HIGH)
        assert rc == 1
        body = (_finding(home)).read_text()
        assert "GHSA-xxxx-yyyy-zzzz" in body and "boxA" in body

    def test_severity_is_carried_into_the_summary(self):
        assert "high" in dac.summarize(ADV_HIGH)


class TestUnobservableIsNotHealthy:
    def test_unreachable_box_is_unknown_never_clean(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, version_rc=255)
        assert rc == 2
        txt = (_status(home)).read_text()
        assert "UNKNOWN" in txt and "clean" not in txt

    def test_no_box_observed_exits_unknown_even_with_zero_findings(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, version_rc=255, advisories=[])
        assert rc == 2, ("zero findings from an observation that never happened "
                         "is not a clean fleet")

    def test_partial_blindness_does_not_delete_a_standing_finding(self, home, monkeypatch):
        stale = _finding(home)
        stale.write_text("previous finding\n")
        _install_runner(monkeypatch, advisories=[])
        # boxA answers clean; boxB is unreachable -> partial view.
        real_collect = dac.collect_installed

        def half_blind(host, packages, timeout=90):
            if host == "boxB":
                return None, None, None, "unreachable"
            return real_collect(host, packages, timeout)

        monkeypatch.setattr(dac, "collect_installed", half_blind)
        rc = dac.main(["--host", "boxA", "--host", "boxB",
                       "--packages", "cryptography", "--quiet"])
        assert rc == 2
        assert stale.exists(), "a partial view must not clear a standing finding"

    def test_unauthenticated_gh_is_unknown(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, gh_auth_rc=1)
        assert rc == 2
        assert "NOT checked" in (_status(home)).read_text()


class TestAbsentPackageIsInert:
    def test_package_not_installed_is_not_a_finding(self, home, monkeypatch):
        rc = _run_main(home, monkeypatch, versions={"cryptography": None})
        assert rc == 0, "absent by design must read inert, never as an advisory"
        assert not (_finding(home)).exists()


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


class TestTheManagerBoxIsNotABlindSpot:
    """The sweep must cover the box it RUNS ON.

    2026-09-05: the fleet host list carries an explicit "do NOT include
    <self>" line — correct for ``fleet_pull.sh``, which ssh-es outward and
    cannot ssh to itself. This sweep inherited that list and so inherited
    the exclusion, for which it has no reason: it can collect locally. The
    result was a report naming nine boxes, read as the fleet's whole story,
    while the tenth — the manager box, running the tracer, the watchdog and this
    very check — sat on cryptography 46.0.3 with three highs and appeared
    nowhere. A checker blind to its own host is blind by construction.
    """

    def _fleet_run(self, home, monkeypatch, hostname, host_list):
        """Run a FLEET-wide sweep (no --host) against a written host list."""
        import json as _json
        seen = []
        f = home / "fleet_hosts"
        f.write_text("\n".join(host_list) + "\n")

        def fake_run(cmd, timeout, stdin_text=None):
            if cmd[:3] == ["gh", "auth", "status"]:
                return 0, "", ""
            if cmd[0] == "gh":
                return 0, "[]", ""
            if cmd[0] == "ssh":
                seen.append(("ssh", cmd[5]))
                return 0, _json.dumps({"cryptography": "46.0.7"}), ""
            # local collection: the reporter python, no ssh wrapper
            seen.append(("local", cmd[0]))
            return 0, _json.dumps({"cryptography": "46.0.3"}), ""

        monkeypatch.setattr(dac, "_run", fake_run)
        monkeypatch.setattr(dac.socket, "gethostname", lambda: hostname)
        rc = dac.main(["--hosts-file", str(f), "--packages", "cryptography",
                       "--quiet"])
        return rc, seen

    def test_local_box_is_swept_even_when_absent_from_the_host_list(
            self, home, monkeypatch):
        rc, seen = self._fleet_run(home, monkeypatch, "boxM",
                                   ["moc", "moc1"])
        assert rc == 0, rc
        assert ("local", dac.REMOTE_PYTHON) in seen, (
            "the manager box was not collected — this is the exact blind spot "
            "the exclusion produced: %r" % (seen,))
        # and it did NOT try to ssh to itself, while the listed boxes still went
        # over ssh by name (pins that `seen` really carries hosts, so the
        # negative assertion above cannot pass vacuously)
        ssh_hosts = [h for kind, h in seen if kind == "ssh"]
        assert ssh_hosts == ["moc", "moc1"], ssh_hosts
        assert "boxM" not in ssh_hosts

    def test_local_box_is_not_swept_twice_when_already_listed(
            self, home, monkeypatch):
        rc, seen = self._fleet_run(home, monkeypatch, "moc", ["moc", "moc1"])
        assert rc == 0, rc
        assert sum(1 for kind, _ in seen if kind == "local") == 1, (
            "a host list that already names this box must not add it again: %r"
            % (seen,))

    def test_explicit_host_selection_is_not_widened(self, home, monkeypatch):
        """``--host moc`` means moc, not moc plus wherever I happen to run."""
        import json as _json
        seen = []

        def fake_run(cmd, timeout, stdin_text=None):
            if cmd[:3] == ["gh", "auth", "status"]:
                return 0, "", ""
            if cmd[0] == "gh":
                return 0, "[]", ""
            seen.append(cmd[5] if cmd[0] == "ssh" else "local")
            return 0, _json.dumps({"cryptography": "46.0.7"}), ""

        monkeypatch.setattr(dac, "_run", fake_run)
        monkeypatch.setattr(dac.socket, "gethostname", lambda: "boxM")
        rc = dac.main(["--host", "moc", "--packages", "cryptography", "--quiet"])
        assert rc == 0 and seen == ["moc"], seen

    def test_a_local_collection_failure_is_unknown_not_clean(self, monkeypatch):
        monkeypatch.setattr(dac.socket, "gethostname", lambda: "boxM")
        monkeypatch.setattr(dac, "_run",
                            lambda cmd, timeout, stdin_text=None: (1, "", "boom"))
        installed, apt, envs, err = dac.collect_installed("boxM", ["cryptography"])
        assert installed is None and apt is None and envs is None and err, (
            installed, apt, envs, err)
        assert "local" in err, (
            "a failed LOCAL collection must say so, not blame ssh: %r" % err)


# --- distro-managed packages + apt hygiene (2026-09-06) -----------------------
#
# The over-report that motivated this: moc4's apt-managed urllib3 reports
# ``1.26.12`` to importlib.metadata while ``1.26.12-1+deb12u4`` carries eight
# CVE fixes. Matching the upstream version against upstream ranges called it
# "8 advisories, 5 high" and pointed at pip — the wrong cure. These tests pin
# the three honest states (distro-patched / accepted-with-expiry / still open)
# and, above all, that an UNREADABLE changelog never reads as patched.

import datetime as _dt  # noqa: E402
import re  # noqa: E402

_TODAY = _dt.date(2026, 9, 6)
ADV_CVE_A = {"ghsa_id": "GHSA-aaaa-aaaa-aaaa", "severity": "high", "cve_id": "CVE-2026-1"}
ADV_CVE_B = {"ghsa_id": "GHSA-bbbb-bbbb-bbbb", "severity": "medium", "cve_id": "CVE-2026-2"}
ADV_NO_CVE = {"ghsa_id": "GHSA-cccc-cccc-cccc", "severity": "high"}


def _report(version="1.26.12", origin="/usr/lib/python3/dist-packages/urllib3/__init__.py",
            dpkg_version="1.26.12-1+deb12u4", changelog=("CVE-2026-1",), apt="skip",
            claimants=None):
    rec = {"version": version, "origin": origin,
           "claimants": claimants if claimants is not None else [version], "dpkg": None}
    if origin and origin.startswith(dac.DISTRO_PREFIX + "/"):
        rec["dpkg"] = {"package": "python3-urllib3", "version": dpkg_version,
                       "changelog_cves": None if changelog is None else list(changelog)}
    if apt == "skip":
        apt = None
    return {"packages": {"urllib3": rec}, "apt": apt}


def _drive(home, monkeypatch, report, advisories, accept_text=None, today=_TODAY,
           hosts=("boxA",)):
    import json as _json

    def fake_run(cmd, timeout, stdin_text=None):
        if cmd[:3] == ["gh", "auth", "status"]:
            return 0, "", ""
        if cmd[0] == "gh":
            return 0, _json.dumps(advisories), ""
        return 0, _json.dumps(report), ""

    monkeypatch.setattr(dac, "_run", fake_run)
    monkeypatch.setattr(dac._dt, "date", _FixedDate)
    _FixedDate.fixed = today
    argv = []
    for h in hosts:
        argv += ["--host", h]
    argv += ["--packages", "urllib3", "--quiet"]
    if accept_text is not None:
        f = home / "accept"
        f.write_text(accept_text)
        argv += ["--accept-file", str(f)]
    else:
        argv += ["--accept-file", str(home / "no-such-accept-file")]
    rc = dac.main(argv)
    status = (_status(home)).read_text()
    fpath = _finding(home)
    finding = fpath.read_text() if fpath.exists() else None
    return rc, status, finding


class _FixedDate(_dt.date):
    fixed = _TODAY

    @classmethod
    def today(cls):
        return cls.fixed


class TestDistroManagedPackages:
    def test_cve_named_in_changelog_is_distro_patched_not_a_finding(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(), [ADV_CVE_A])
        assert rc == 0, status
        assert finding is None
        assert "distro-patched x1" in status
        assert "[apt 1.26.12-1+deb12u4]" in status, (
            "the distro version is the tell a reader needs; it must be shown")

    def test_cve_not_in_changelog_stays_a_finding_tagged_apt_managed(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(), [ADV_CVE_A, ADV_CVE_B])
        assert rc == 1
        assert finding and "GHSA-bbbb-bbbb-bbbb" in finding
        assert "GHSA-aaaa-aaaa-aaaa" not in finding.split("\n")[1].split(":")[1].split("—")[0], (
            "the patched advisory must not be listed among the open ones")
        assert "apt-managed" in finding and "never pip" in finding

    def test_unreadable_changelog_leaves_every_advisory_open(self, home, monkeypatch):
        """THE error path. 'Could not read what the distro fixed' must never
        become 'the distro fixed it' — that is the empty-list-from-a-broken-
        call lie in changelog form."""
        rc, status, finding = _drive(home, monkeypatch, _report(changelog=None), [ADV_CVE_A])
        assert rc == 1
        assert finding and "GHSA-aaaa-aaaa-aaaa" in finding
        assert "UNREADABLE" in status

    def test_advisory_without_a_cve_id_cannot_be_called_patched(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(changelog=("CVE-2026-1",)),
                                     [ADV_NO_CVE])
        assert rc == 1 and finding and "GHSA-cccc-cccc-cccc" in finding

    def test_pip_managed_copy_is_reported_exactly_as_before(self, home, monkeypatch):
        rep = _report(origin="/usr/local/lib/python3.13/dist-packages/urllib3/__init__.py")
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 1
        assert finding and "apt" not in finding
        assert "[apt" not in status

    def test_legacy_flat_report_shape_still_parses(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, {"urllib3": "1.26.12"}, [ADV_CVE_A])
        assert rc == 1 and finding and "urllib3 1.26.12" in finding

    def test_disagreeing_claimants_are_shown(self, home, monkeypatch):
        rep = _report(version="43.0.3", origin="/usr/local/lib/python3.13/dist-packages/x.py",
                      claimants=["43.0.3", "46.0.3"])
        rc, status, finding = _drive(home, monkeypatch, rep, [])
        assert "claimants: 43.0.3, 46.0.3" in status


class TestAcceptList:
    ACCEPT = "GHSA-aaaa-aaaa-aaaa until=2026-12-31 Debian ignored (no-dsa)\n"

    def test_accepted_advisory_on_a_distro_package_is_not_a_finding(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(changelog=()), [ADV_CVE_A],
                                     accept_text=self.ACCEPT)
        assert rc == 0, status
        assert finding is None
        assert "accepted x1" in status and "Debian ignored" in status, (
            "an acceptance must stay visible in the status file, never vanish")

    def test_expired_acceptance_is_a_finding_again_and_says_so(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(changelog=()), [ADV_CVE_A],
                                     accept_text=self.ACCEPT, today=_dt.date(2027, 1, 1))
        assert rc == 1
        assert finding and "GHSA-aaaa-aaaa-aaaa" in finding
        assert "EXPIRED" in status

    def test_acceptance_never_silences_a_pip_managed_copy(self, home, monkeypatch):
        rep = _report(origin="/usr/local/lib/python3.13/dist-packages/urllib3/__init__.py")
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A], accept_text=self.ACCEPT)
        assert rc == 1 and finding and "GHSA-aaaa-aaaa-aaaa" in finding, (
            "a pip-managed copy has a fix one pip away; the accept list must not apply")

    def test_acceptance_without_an_expiry_is_ignored_and_warned(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(changelog=()), [ADV_CVE_A],
                                     accept_text="GHSA-aaaa-aaaa-aaaa some reason, no date\n")
        assert rc == 1 and finding
        assert "# WARN accept list line 1 ignored" in status

    def test_read_accepted_parses_and_rejects(self, tmp_path):
        f = tmp_path / "a"
        f.write_text("# comment\n\nGHSA-x-y-z until=2026-10-01 reason here\n"
                     "GHSA-bad until=not-a-date\nnot-a-ghsa until=2026-10-01\n")
        acc, warns = dac.read_accepted(str(f))
        assert acc == {"GHSA-x-y-z": (_dt.date(2026, 10, 1), "reason here")}
        assert len(warns) == 2


class TestAptHygiene:
    def test_absent_unattended_upgrades_is_a_finding(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "absent", "pending_total": 3,
                           "pending_security": 0, "lists_age_h": 2})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 1 and finding
        assert "unattended-upgrades ABSENT" in finding

    def test_pending_security_updates_are_a_finding(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": 40,
                           "pending_security": 35, "lists_age_h": 6})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 1 and finding and "35 security update(s) pending" in finding

    def test_stale_lists_are_a_finding_and_counts_are_a_lower_bound(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": 0,
                           "pending_security": 0, "lists_age_h": 400})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 1 and finding and "400h stale" in finding and "lower bound" in finding

    def test_failed_apt_simulation_is_unknown_not_clean(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": None,
                           "pending_security": None, "lists_age_h": 1})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 2, "pending updates unobservable must not read as a clean box"
        assert "boxA/apt" in status.splitlines()[1]

    def test_healthy_apt_is_clean_and_a_non_deb_box_is_silent(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": 2,
                           "pending_security": 0, "lists_age_h": 1})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 0 and finding is None and "0 security" in status
        rc, status, finding = _drive(home, monkeypatch, _report(apt=None), [ADV_CVE_A])
        assert rc == 0
        assert not re.search(r"^boxA\s+apt\s", status, re.M), (
            "a box with no dpkg must produce no apt line at all (inert)")


class TestNamesAreCanonicalForTheAdvisoryDB:
    """Same drill as the range check (2026-09-06): the advisory DB does not
    normalise ``_`` to ``-``; PyPI does. ``--packages prometheus_client`` must
    not be told 'no advisories' by a query that could never match."""

    def test_underscore_spelling_is_queried_with_a_hyphen(self, monkeypatch):
        seen = []

        def fake_run(cmd, timeout, stdin_text=None):
            seen.append(cmd)
            return 0, "[]", ""

        monkeypatch.setattr(dac, "_run", fake_run)
        advs, err = dac.query_advisories("prometheus_client", "0.1")
        assert err is None and advs == []
        assert "affects=prometheus-client@0.1" in seen[0][2], seen[0]


class TestVersionlessDistInfoIsUnknownNotAbsent:
    """Live on the manager 2026-09-05: pip left ``requests-2.32.5.dist-info``
    holding only ``REQUESTED``; ``importlib.metadata.version`` returned None
    while ``import requests`` gave 2.34.2. Reporting None as 'not installed'
    silently drops a package that is running from the sweep."""

    def test_importable_but_versionless_is_unknown(self, home, monkeypatch):
        rep = {"packages": {"urllib3": {"version": None,
                                        "origin": "/usr/local/lib/python3.13/dist-packages/urllib3/__init__.py",
                                        "claimants": ["2.3.0", "2.7.0"], "dpkg": None}},
               "apt": None}
        rc, status, finding = _drive(home, monkeypatch, rep, [])
        assert rc == 2, "a package we can import but cannot version is unobservable, not absent"
        assert "boxA/urllib3" in status.splitlines()[1]
        assert "claimants: 2.3.0, 2.7.0" in status

    def test_truly_absent_stays_inert(self, home, monkeypatch):
        rep = {"packages": {"urllib3": {"version": None, "origin": None,
                                        "claimants": [], "dpkg": None}}, "apt": None}
        rc, status, finding = _drive(home, monkeypatch, rep, [])
        assert rc == 0 and finding is None


# --- every interpreter per box, not just the reporter's own (2026-09-06) ------
#
# The blindness these pin: until this date the reporter read ONE interpreter
# (`REMOTE_PYTHON`) and the sweep called that "the box". The manager box carried SIX
# cryptography copies while the report named one, and a roll plan built on that
# report was wrong about five of them -- including a /root/.local shadow the
# root watchdog actually imported. A copy the sweep cannot see is a copy nobody
# patches, so "how many envs" is not cosmetic: it is the whole claim.
class TestEveryEnvIsWalked:

    @staticmethod
    def _versions(envs, primary="50.0.1"):
        return {"packages": {"cryptography": {"version": primary, "origin": None,
                                              "claimants": [], "dpkg": None}},
                "apt": None, "envs": envs}

    def test_a_second_copy_in_another_env_is_a_finding(self, home, monkeypatch):
        """The whole point: the primary interpreter is clean, another env is not."""
        _install_runner(monkeypatch, advisories=ADV_HIGH, versions=self._versions([
            {"root": "/opt/app/venv/lib/python3.13/site-packages", "kind": "venv",
             "readable": True, "reason": None, "collective": False,
             "packages": {"cryptography": [{"version": "41.0.0", "dpkg": None}]}},
        ]))
        rc = dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])
        assert rc == 1, "a vulnerable copy outside the primary env must be a finding"
        body = (_finding(home)).read_text()
        assert "41.0.0" in body and "/opt/app/venv" in body, body

    def test_an_unreadable_env_is_unknown_never_absent(self, home, monkeypatch):
        """/root/.local is 0700. 'could not look' must not render as 'nothing there'."""
        _install_runner(monkeypatch, advisories=[], versions=self._versions([
            {"root": "/root/.local/lib/python3.13/site-packages", "kind": "root-site",
             "readable": False, "reason": "unreadable (not owner; sudo -n unavailable)",
             "collective": False, "packages": {}},
        ]))
        rc = dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])
        assert rc == 2, "an env we could not list makes the run UNKNOWN, not clean"
        status = (_status(home)).read_text()
        assert "UNKNOWN" in status and "/root/.local" in status, status

    def test_an_apt_owned_copy_keeps_its_distro_credit(self, home, monkeypatch):
        """Regression on my own 2026-09-06 first cut, which reported Debian
        backports in an env as open advisories and pointed at pip -- reviving
        the exact over-report the dpkg leg exists to kill."""
        adv = [{"ghsa_id": "GHSA-aaaa-bbbb-cccc", "severity": "high",
                "cve_id": "CVE-2026-27459"}]
        _install_runner(monkeypatch, advisories=adv, versions=self._versions([
            {"root": dac.DISTRO_PREFIX, "kind": "apt", "readable": True,
             "reason": None, "collective": False,
             "packages": {"cryptography": [{"version": "43.0.0", "dpkg": {
                 "package": "python3-cryptography", "version": "43.0.0-3+deb13u1",
                 "changelog_cves": ["CVE-2026-27459"]}}]}},
        ]))
        dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])
        status = (_status(home)).read_text()
        apt_row = [ln for ln in status.splitlines() if dac.DISTRO_PREFIX in ln]
        assert apt_row and "distro-patched" in apt_row[0], apt_row
        assert "ADVISORY" not in apt_row[0], (
            "a CVE named in the distro changelog is patched, not a finding: %s" % apt_row[0])
        findings = _finding(home)
        if findings.exists():
            assert dac.DISTRO_PREFIX not in findings.read_text(), (
                "the apt-owned copy must not be reported as a pip finding")

    def test_an_env_carrying_rns_is_tagged_mesh_not_leaf(self, home, monkeypatch):
        """Which version matters to the COLLECTIVE: an env holding RNS/LXMF is on
        the wire, so its packages are a fleet contract, not a leaf's business."""
        _install_runner(monkeypatch, advisories=ADV_HIGH, versions=self._versions([
            {"root": "/home/u/.local/share/pipx/venvs/client/lib/python3.13/site-packages",
             "kind": "pipx", "readable": True, "reason": None, "collective": True,
             "packages": {"cryptography": [{"version": "49.0.0", "dpkg": None}]}},
        ]))
        dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])
        status = (_status(home)).read_text()
        assert "mesh env" in status, status
        assert "leaf env" not in status, status

    def test_the_primary_version_is_not_reported_twice(self, home, monkeypatch):
        """The env walk necessarily re-finds the primary copy; judging it again
        would double every row and bury the copies that are actually new."""
        _install_runner(monkeypatch, advisories=[], versions=self._versions([
            {"root": "/usr/local/lib/python3.13/dist-packages", "kind": "system-pip",
             "readable": True, "reason": None, "collective": False,
             "packages": {"cryptography": [{"version": "50.0.1", "dpkg": None}]}},
        ]))
        rc = dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])
        assert rc == 0
        status = (_status(home)).read_text()
        assert status.count("50.0.1") == 1, status

    def test_a_reporter_with_no_env_block_still_reads(self, home, monkeypatch):
        """An un-upgraded box must degrade to the old one-interpreter answer,
        never to an exception that makes the whole box UNKNOWN."""
        _install_runner(monkeypatch, advisories=[],
                        versions={"cryptography": "50.0.1"})
        rc = dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])
        assert rc == 0


class TestAbsentEnvIsInertNotUnknown:
    """A root user-site that does not EXIST and one we cannot LOOK AT are
    opposite answers. My first cut collapsed them: any non-zero `find` read as
    "unreadable", so eight boxes reported UNKNOWN when /root/.local/lib simply
    was not there — noise that would drown the one box where it IS there, which
    is the entire reason the root-site leg exists."""

    @staticmethod
    def _versions(envs):
        return {"packages": {"cryptography": {"version": "50.0.1", "origin": None,
                                              "claimants": [], "dpkg": None}},
                "apt": None, "envs": envs}

    def test_absent_root_site_does_not_make_the_run_unknown(self, home, monkeypatch):
        _install_runner(monkeypatch, advisories=[], versions=self._versions([]))
        rc = dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])
        assert rc == 0, "a box with no root user-site is clean, not UNKNOWN"
        assert "UNKNOWN" not in (_status(home)).read_text()

    def test_a_genuinely_unreadable_env_still_reports_unknown(self, home, monkeypatch):
        """The other polarity, which must not be lost to the fix above: when we
        truly cannot look, silence is not clean."""
        _install_runner(monkeypatch, advisories=[], versions=self._versions([
            {"root": "/root/.local/lib/python3.13/site-packages", "kind": "root-site",
             "readable": False, "reason": "unreadable (sudo -n unavailable)",
             "collective": False, "packages": {}},
        ]))
        rc = dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])
        assert rc == 2, "'could not look' must never read as clean"


class TestRecoveredRootEnvIsNotDroppedAsAbsent:
    """A root env recovered via sudo must survive the loop that consumes it.

    2026-09-06, caught by a live drill and NOT by reading the code: the env
    loop guarded each root with ``os.path.isdir(root)``, which RETURNS FALSE
    (it does not raise) for a path the caller cannot stat. So every /root env
    the sudo find had just recovered was silently dropped — unreadable rendered
    as absent, one line after that exact bug was fixed in _roots_for. The
    symptom was indistinguishable from "there is nothing there", and the
    earlier inert-vs-UNKNOWN fix had made the last visible trace disappear.
    Removing the drill's plant then exposed a REAL unreported find: a
    root-owned pipx venv carrying urllib3 2.6.3 (two highs) on the manager box.
    """

    def test_a_root_env_the_reporter_recovered_is_still_judged(self, home, monkeypatch):
        _install_runner(monkeypatch, advisories=ADV_HIGH, versions={
            "packages": {"cryptography": {"version": "50.0.1", "origin": None,
                                          "claimants": [], "dpkg": None}},
            "apt": None,
            "envs": [{
                # The reporter reached this only through sudo; the manager
                # process running these assertions cannot stat it either.
                "root": "/root/.local/share/pipx/venvs/somecli/lib/python3.13/site-packages",
                "kind": "root-pipx", "readable": True, "reason": None,
                "collective": False,
                "packages": {"cryptography": [{"version": "41.0.0", "dpkg": None}]},
            }]})
        rc = dac.main(["--host", "boxA", "--packages", "cryptography", "--quiet"])
        assert rc == 1, "a root-owned env the reporter could read must still be judged"
        body = (_finding(home)).read_text()
        assert "41.0.0" in body and "root-pipx" not in body.split("41.0.0")[0][-5:], body
        assert "/root/.local/share/pipx" in body, body


class TestScopedRunNeverClobbersTheFleetRecord:
    """A narrowed run answers a question; it must not become the fleet record.

    2026-09-06: ``--host kiai`` overwrote ``~/.meshforge-dep-advisories``
    (ten boxes, 22 findings) with a one-box view, twice in one session, and
    ``honest_status.sh`` read 4 and then 7 findings off it. The canonical
    pair is written ONLY by an unnarrowed run and carries a scope stamp;
    narrow runs write ``.scoped`` siblings. Paths, not labels: the narrow
    file cannot be read as the fleet by a reader that never opens it.
    """

    @staticmethod
    def _plant_fleet_record(home):
        s, f = _status(home, scoped=False), _finding(home, scoped=False)
        s.write_text("# fleet dependency advisories\n%s\n"
                     "moc cryptography 1.0 ADVISORY x3\n" % dac.FLEET_SCOPE_STAMP)
        f.write_text("# installed versions carrying published advisories\n"
                     "moc cryptography 1.0: GHSA-1(high)\n")
        return s.read_bytes(), f.read_bytes()

    def test_host_narrowed_run_writes_the_scoped_sibling_only(self, home, monkeypatch):
        s0, f0 = self._plant_fleet_record(home)
        rc = _run_main(home, monkeypatch, advisories=ADV_HIGH)
        assert rc == 1
        assert _status(home).exists() and _finding(home).exists()
        assert "# scope: NARROW" in _status(home).read_text()
        assert _status(home, scoped=False).read_bytes() == s0
        assert _finding(home, scoped=False).read_bytes() == f0

    def test_clean_narrow_run_does_not_erase_the_fleet_finding_file(self, home, monkeypatch):
        """THE dangerous half: 'absent = clean' applied to the fleet's file by
        a one-box run that found nothing on that one box."""
        _s0, f0 = self._plant_fleet_record(home)
        rc = _run_main(home, monkeypatch, advisories=[])
        assert rc == 0
        assert not _finding(home).exists()
        assert _finding(home, scoped=False).read_bytes() == f0

    def test_what_counts_as_narrow(self):
        full = list(dac.DEFAULT_PACKAGES)
        assert dac.run_scope(["moc"], None, full)[0] == "narrow"
        assert dac.run_scope(None, "/some/list", full)[0] == "narrow"
        assert dac.run_scope(None, None, ["cryptography"])[0] == "narrow"
        # the default list in any order is still the fleet
        assert dac.run_scope(None, None, list(reversed(full))) == ("fleet", None)

    def test_unnarrowed_run_writes_the_canonical_pair_with_the_stamp(
            self, home, monkeypatch):
        import json as _json
        hosts = home / "fleet_hosts"
        hosts.write_text("moc\nmoc1\n")
        monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(hosts))
        monkeypatch.setattr(dac.socket, "gethostname", lambda: "moc")

        def fake_run(cmd, timeout, stdin_text=None):
            if cmd[:3] == ["gh", "auth", "status"]:
                return 0, "", ""
            if cmd[0] == "gh":
                return 0, _json.dumps(ADV_HIGH), ""
            return 0, _json.dumps({"cryptography": "46.0.7"}), ""

        monkeypatch.setattr(dac, "_run", fake_run)
        rc = dac.main(["--quiet"])
        assert rc == 1
        status = _status(home, scoped=False).read_text().splitlines()
        assert dac.FLEET_SCOPE_STAMP in status[:4], status[:4]
        assert _finding(home, scoped=False).exists()
        assert not _status(home).exists() and not _finding(home).exists()

    def test_a_fleet_run_that_could_not_look_still_carries_the_stamp(
            self, home, monkeypatch):
        """gh unauthenticated: the canonical file must read 'fleet run, never
        looked' (UNKNOWN under the stamp) — not 'not a fleet run'."""
        hosts = home / "fleet_hosts"
        hosts.write_text("moc\n")
        monkeypatch.setenv("MESHFORGE_FLEET_HOSTS", str(hosts))
        _install_runner(monkeypatch, gh_auth_rc=1)
        rc = dac.main(["--quiet"])
        assert rc == 2
        lines = _status(home, scoped=False).read_text().splitlines()
        assert lines[0] == dac.FLEET_SCOPE_STAMP
        assert lines[1].startswith("UNKNOWN")


# ---------------------------------------------------------------------------
# The reporter's env walk, EXECUTED — not fed back as JSON. Every test above
# hands main() a finished `envs` block, so nothing here had ever run the walk
# that produces one. 2026-09-06 review: the walk's sudo retry fired only for
# patterns spelled /root/...; for everything else glob.glob swallowed EACCES on
# an unreadable ancestor and the env was rendered ABSENT — no row, no UNKNOWN.
# ---------------------------------------------------------------------------

def _walk_envs(env_globs, run, wanted=("cryptography",)):
    """Run the reporter's env-walk slice in-process against tmp globs, with
    the ONE subprocess chokepoint replaced. Slices the real template text, so
    a move of the walk fails here loudly rather than passing vacuously."""
    import fnmatch, glob, re, stat  # noqa: E401
    src = dac._REMOTE_SRC
    a = src.index("# ---- every python env on this box")
    b = src.index("\nprint(json.dumps(", a)
    body = src[a:b]
    assert "def _roots_for" in body and "envs = []" in body, "walk slice moved"
    assert "%" not in body.replace("%%", ""), (
        "the walk slice must not use %-formatting: the template is rendered "
        "with % and this test executes the slice unrendered")
    ns = {"os": os, "glob": glob, "fnmatch": fnmatch, "re": re, "stat": stat,
          "_run": run, "_dpkg_for": lambda p: None,
          "WANTED": set(wanted), "COLLECTIVE_MARKERS": ("rns", "lxmf"),
          "ENV_ROOT_GLOBS": list(env_globs)}
    exec(compile(body, "<env-walk>", "exec"), ns)
    return ns["envs"]


def _plant_env(site, *names):
    site.mkdir(parents=True)
    for n in names:
        (site / n).mkdir()
    return site


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything; the denial cannot be planted")
class TestAnUnreadableAncestorAnywhereIsNotAbsent:

    @pytest.fixture
    def denied_tree(self, tmp_path):
        """/opt/pipx-shaped: the ancestor is 0000, the env under it holds a
        vulnerable copy tagged mesh. Perms restored so tmp cleanup works."""
        site = _plant_env(tmp_path / "opt/pipx/venvs/drillcli/lib/python3.13/site-packages",
                          "cryptography-41.0.0.dist-info", "rns-1.0.0.dist-info")
        top = tmp_path / "opt/pipx"
        top.chmod(0)
        try:
            yield site, top, str(tmp_path / "opt/pipx/venvs/*/lib/python3*/site-packages")
        finally:
            top.chmod(0o755)

    def test_denied_with_no_sudo_is_unknown_not_silence(self, denied_tree):
        """THE 2026-09-06 shape: before, this returned [] — no row at all."""
        site, top, pattern = denied_tree
        calls = []

        def no_sudo(cmd):
            calls.append(cmd)
            return 1, ""

        envs = _walk_envs([("root-pipx", pattern)], no_sudo)
        assert len(envs) == 1, envs
        assert envs[0]["readable"] is False
        assert str(top) in envs[0]["reason"] and "sudo -n unavailable" in envs[0]["reason"]
        assert any("find" in c[2] for c in calls), "sudo find must have been attempted"

    def test_denied_but_sudo_works_is_recovered_and_judged(self, denied_tree):
        site, top, pattern = denied_tree
        names = ["cryptography-41.0.0.dist-info", "rns-1.0.0.dist-info"]

        def sudo(cmd):
            if "/usr/bin/find" in cmd:
                return 0, str(site) + "\n"
            if "/bin/ls" in cmd:
                return 0, "\n".join(names) + "\n"
            return 0, ""

        envs = _walk_envs([("root-pipx", pattern)], sudo)
        assert len(envs) == 1 and envs[0]["readable"] is True, envs
        assert envs[0]["root"] == str(site)
        assert envs[0]["collective"] is True
        assert [r["version"] for r in envs[0]["packages"]["cryptography"]] == ["41.0.0"]

    def test_find_failing_under_working_sudo_names_that(self, denied_tree):
        _site, top, pattern = denied_tree

        def sudo(cmd):
            return (1, "") if "/usr/bin/find" in cmd else (0, "")

        envs = _walk_envs([("root-pipx", pattern)], sudo)
        assert envs[0]["readable"] is False
        assert "find failed under sudo" in envs[0]["reason"], envs[0]["reason"]


class TestAbsentUnderAReadableParentIsInertWithoutSudo:
    def test_nothing_there_asks_nobody(self, tmp_path):
        calls = []
        envs = _walk_envs(
            [("root-site", str(tmp_path / "nope/.local/lib/python3*/site-packages"))],
            lambda cmd: calls.append(cmd) or (1, ""))
        assert envs == [] and calls == [], (envs, calls)

    def test_expander_agrees_with_glob_on_a_readable_tree(self, tmp_path):
        """Same answers as glob.glob where glob is honest, including the
        dot-name rule for magic components."""
        import glob
        for name in ("a", "b", ".hidden"):
            _plant_env(tmp_path / "opt" / name / "venv/lib/python3.13/site-packages")
        (tmp_path / "opt/a/venv/lib/python3.13/site-packages/notadir").write_text("")
        pattern = str(tmp_path / "opt/*/venv/lib/python3*/site-packages")
        envs = _walk_envs([("foreign-venv", pattern)], lambda cmd: (1, ""))
        assert sorted(e["root"] for e in envs) == sorted(glob.glob(pattern))
        assert all(e["readable"] for e in envs)
