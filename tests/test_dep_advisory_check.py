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

#: The security archive's "current" Date every test sees (2026-11-04 21:43:53Z,
#: the trixie-security Date measured live 11-05). Tests never touch the network.
ARCHIVE_TS = 1793828633.0
SEC_FILE = "deb.debian.org_debian-security_dists_trixie-security_InRelease"
#: The stamp shape the CURRENT reporter sends for a box whose periodic update
#: ran 3h ago. Fixtures not ABOUT the stamp carry it: a report with no stamp
#: field is an UNKNOWN by r6 #6, so a bare fixture now tests that instead.
STAMP_OK = {"update_stamp_present": True, "update_stamp_age_h": 3}
REFERENCE_FETCHES = []
#: The real fetch, saved BEFORE the autouse fake — a test that needs it
#: restores this one attribute, never `monkeypatch.undo()` (r4 #8: undo()
#: strips every isolation patch, the network fake included).
_REAL_FETCH = dac.fetch_reference_date


@pytest.fixture(autouse=True)
def _no_network_reference(monkeypatch):
    del REFERENCE_FETCHES[:]

    def fake(url, timeout=20):
        REFERENCE_FETCHES.append(url)
        return ARCHIVE_TS, None
    monkeypatch.setattr(dac, "fetch_reference_date", fake)


def _view(lag_h=1.0, file=SEC_FILE, date_ts="lag"):
    return {"file": file, "suite": "stable-security",
            "date_ts": (ARCHIVE_TS - lag_h * 3600) if date_ts == "lag" else date_ts}


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
                           "pending_security": 0, **STAMP_OK, "lists_age_h": 2})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 1 and finding
        assert "unattended-upgrades ABSENT" in finding

    def test_pending_security_updates_are_a_finding(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": 40,
                           "pending_security": 35, **STAMP_OK, "lists_age_h": 6})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 1 and finding and "35 security update(s) pending" in finding

    def test_stale_lists_are_a_finding_and_counts_are_a_lower_bound(self, home, monkeypatch):
        # r5: judged on the success-only update-stamp, not list content age.
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": 0,
                           "pending_security": 0, **STAMP_OK, "lists_age_h": 2,
                           "update_stamp_present": True, "update_stamp_age_h": 400})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 1 and finding and "in 400h" in finding and "lower bound" in finding

    def test_failed_apt_simulation_is_unknown_not_clean(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": None,
                           "pending_security": None, **STAMP_OK, "lists_age_h": 1})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 2, "pending updates unobservable must not read as a clean box"
        assert "boxA/apt" in status.splitlines()[1]

    def test_healthy_apt_is_clean_and_a_non_deb_box_is_silent(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": 2,
                           "pending_security": 0, **STAMP_OK, "lists_age_h": 1,
                           "security_views": [_view(1)]})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 0 and finding is None and "0 security" in status
        assert re.search(r"^boxA\s+apt\s+-\s+clean ", status, re.M)
        rc, status, finding = _drive(home, monkeypatch, _report(apt=None), [ADV_CVE_A])
        assert rc == 0
        assert not re.search(r"^boxA\s+apt\s", status, re.M), (
            "a box with no dpkg must produce no apt line at all (inert)")


    # 2026-10-25 fleet OS roll: 122 updates had piled up per box while this
    # read "0 security" — unattended-upgrades takes Debian origins only, so
    # firmware, bootloader, kernels and the +rpt openssl never auto-install.
    # Observe before alarm: a visible count, not (yet) a finding.
    def test_rpi_archive_pending_is_shown_but_not_a_finding(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": 9,
                           "pending_security": 0, "pending_rpi": 7, **STAMP_OK, "lists_age_h": 1})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 0 and finding is None
        assert "7 from the Raspberry Pi archive" in status

    def test_rpi_count_absent_from_an_old_reporter_says_nothing(self, home, monkeypatch):
        rep = _report(apt={"unattended_upgrades": "installed", "pending_total": 2,
                           "pending_security": 0, **STAMP_OK, "lists_age_h": 1})
        rc, status, finding = _drive(home, monkeypatch, rep, [ADV_CVE_A])
        assert rc == 0 and "Raspberry Pi" not in status

    def test_reporter_simulates_full_upgrade_and_counts_by_rpi_label(self):
        # `apt-get -s upgrade` keeps back anything that needs a NEW package —
        # exactly how a kernel arrives — so the count would miss the class.
        assert "apt-get\", \"-s\", \"full-upgrade" in dac._REMOTE_SRC
        assert "Raspberry Pi Foundation" in dac._REMOTE_SRC


# 2026-11-04 apt 3.0.4 (CVE-2026-109518): the sweep flagged ONLY moc (lists
# 2 h old) while six trixie boxes carried the same pending security fix behind
# 15-31 h old lists, each reading a bare `clean — 0 security`. "0 security" is
# only as current as the box's view of the security archive; a fix published
# after that view is invisible (honest_failure_modes #1/#2).
class TestZeroSecurityIsOnlyAsFreshAsTheSecurityView:
    def _apt(self, **kw):
        apt = {"unattended_upgrades": "installed", "pending_total": 0,
               "pending_security": 0, **STAMP_OK, "lists_age_h": 31}
        apt.update(kw)
        return apt

    @staticmethod
    def _apt_line(status):
        return [ln for ln in status.splitlines() if re.match(r"^boxA\s+apt\s", ln)]

    def _line(self, home, monkeypatch, **kw):
        rc, status, finding = _drive(home, monkeypatch, _report(apt=self._apt(**kw)), [])
        (line,) = self._apt_line(status)
        return rc, status, finding, line

    def test_a_view_31h_behind_the_archive_is_a_lower_bound_not_clean(self, home, monkeypatch):
        rc, status, finding, line = self._line(home, monkeypatch, security_views=[_view(31)])
        assert " clean " not in line, "a view 31 h behind must not read clean: " + line
        assert "lower-bound" in line and "31h behind the archive" in line
        # observe before alarm: wording + a header count, not a finding or UNKNOWN
        assert rc == 0 and finding is None
        assert "# apt: 1 box(es) read 0 security as a LOWER BOUND" in status

    def test_a_view_close_behind_the_archive_is_clean_and_names_its_lag(self, home, monkeypatch):
        rc, status, finding, line = self._line(home, monkeypatch, security_views=[_view(3)])
        assert " clean " in line and "security view 3h behind the archive" in line
        assert "LOWER BOUND" not in status
        assert rc == 0 and finding is None

    # review r1 #1: a QUIET archive (Date days old, nothing published since)
    # must not make a current box read stale — lag, not age.
    def test_a_quiet_archive_does_not_make_a_current_box_lower_bound(self, home, monkeypatch):
        old = ARCHIVE_TS - 72 * 3600
        monkeypatch.setattr(dac, "fetch_reference_date", lambda url, timeout=20: (old, None))
        _, _, _, line = self._line(home, monkeypatch, security_views=[_view(date_ts=old)])
        assert " clean " in line and "0h behind" in line

    # review r1 #3: compare SECONDS — 24 h 50 min is past a 24 h window.
    def test_the_window_is_the_one_constant_compared_in_seconds(self, home, monkeypatch):
        w = dac.APT_SECURITY_VIEW_FRESH_H
        _, _, _, at = self._line(home, monkeypatch, security_views=[_view(w)])
        _, _, _, past = self._line(home, monkeypatch, security_views=[_view(w + 50 / 60)])
        assert " clean " in at
        assert "lower-bound" in past, "24h50m must not floor to 24 h and pass"

    # review r1 #2: one unmeasurable suite blinds the box; a fresh sibling never vouches.
    def test_an_unreadable_sibling_suite_makes_the_box_unmeasured(self, home, monkeypatch):
        other = SEC_FILE.replace("trixie-security", "extra-security")
        _, _, _, line = self._line(home, monkeypatch, security_views=[
            _view(1), _view(file=other, date_ts=None)])
        assert "lower-bound" in line and "Date unreadable" in line

    def test_the_stalest_suite_bounds_the_claim(self, home, monkeypatch):
        other = SEC_FILE.replace("trixie-security", "bookworm-security")
        _, _, _, line = self._line(home, monkeypatch,
                                   security_views=[_view(1), _view(30, file=other)])
        assert "lower-bound" in line and "30h behind" in line

    @pytest.mark.parametrize("views,why", [
        (None, "no security view reported"),
        ([_view(1, file="mirror.example_debian-security_dists_trixie-security_InRelease")],
         "no reference archive"),
    ])
    def test_unmeasurable_views_are_lower_bound_with_the_reason(self, home, monkeypatch,
                                                                views, why):
        _, _, _, line = self._line(home, monkeypatch, security_views=views)
        assert " clean " not in line and "lower-bound" in line and why in line

    def test_a_reporter_side_error_is_named(self, home, monkeypatch):
        _, _, _, line = self._line(home, monkeypatch, security_views=None,
                                   security_views_error="PermissionError: denied")
        assert "lower-bound" in line and "PermissionError" in line

    def test_an_unreachable_archive_is_lower_bound_not_clean(self, home, monkeypatch):
        monkeypatch.setattr(dac, "fetch_reference_date",
                            lambda url, timeout=20: (None, "archive unreachable: URLError"))
        _, _, _, line = self._line(home, monkeypatch, security_views=[_view(0)])
        assert "lower-bound" in line and "archive unreachable" in line

    def test_one_fetch_per_archive_url_per_run(self, home, monkeypatch):
        _drive(home, monkeypatch, _report(apt=self._apt(security_views=[_view(1)])), [],
               hosts=("boxA", "boxB", "boxC"))
        assert REFERENCE_FETCHES == [
            "https://deb.debian.org/debian-security/dists/trixie-security/InRelease"]

    def test_rpi_backlog_still_shows_on_a_lower_bound_line(self, home, monkeypatch):
        _, _, _, line = self._line(home, monkeypatch, pending_rpi=7,
                                   security_views=[_view(40)])
        assert "lower-bound" in line and "7 from the Raspberry Pi archive" in line

    @pytest.mark.parametrize("name,url", [
        (SEC_FILE, "https://deb.debian.org/debian-security/dists/trixie-security/InRelease"),
        ("security.debian.org_debian-security_dists_bookworm-security_InRelease",
         "https://security.debian.org/debian-security/dists/bookworm-security/InRelease"),
        ("evil.example_debian-security_dists_trixie-security_InRelease", None),
        ("deb.debian.org_.._dists_trixie-security_InRelease", None),
        ("not-a-list-name", None),
    ])
    def test_reference_url_is_allowlisted_and_validated(self, name, url):
        assert dac.security_reference_url(name) == url


def _rendered_reporter_ns():
    """Exec ONLY the embedded utils/apt_lists block of the RENDERED template,
    so the functions under test are the ones the boxes actually run."""
    src = dac._REMOTE_SRC % {"pkgs": [], "import_names": {}, "distro_prefix": "/x",
                             "env_globs": [], "collective": [],
                             "apt_lists_src": dac.APT_LISTS_SRC}
    a = src.index("# ---- utils/apt_lists.py, embedded verbatim")
    b = src.index("# ---- end utils/apt_lists.py", a)
    ns = {}
    exec(compile(src[a:b], "<apt-lists>", "exec"), ns)
    return ns


class TestReporterReadsTheSecurityArchiveDate:
    NOW = _dt.datetime(2026, 11, 5, tzinfo=_dt.timezone.utc).timestamp()

    @pytest.fixture(autouse=True)
    def _fns(self):
        self.ns = _rendered_reporter_ns()

    def _views(self, d):
        return self.ns["security_views"](str(d))

    def _age(self, d):
        return self.ns["apt_lists_scan"](str(d), self.NOW)[0]

    def _inrelease(self, d, name, suite, date):
        f = d / name
        # real shape: PGP armor, a BLANK line, then the Release paragraph
        f.write_text("-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
                     "Origin: Debian\nSuite: %s\nDate: %s\nValid-Until: x\n" % (suite, date))
        return f

    def test_reads_only_security_suites_with_debians_date(self, tmp_path):
        self._inrelease(tmp_path, SEC_FILE, "stable-security", "Wed, 04 Nov 2026 21:43:53 UTC")
        self._inrelease(tmp_path, "deb.debian.org_debian_dists_trixie-updates_InRelease",
                        "stable-updates", "Wed, 04 Nov 2026 23:00:00 UTC")
        assert self._views(tmp_path) == [
            {"file": SEC_FILE, "suite": "stable-security", "date_ts": ARCHIVE_TS}]

    def test_an_unparseable_date_is_kept_as_none_never_dropped(self, tmp_path):
        self._inrelease(tmp_path, SEC_FILE, "stable-security", "yesterday-ish")
        assert self._views(tmp_path) == [
            {"file": SEC_FILE, "suite": "stable-security", "date_ts": None}]

    def test_no_security_suite_is_an_empty_list(self, tmp_path):
        self._inrelease(tmp_path, "deb.debian.org_debian_dists_trixie_InRelease",
                        "stable", "Wed, 04 Nov 2026 23:00:00 UTC")
        assert self._views(tmp_path) == []

    def test_an_unreadable_lists_dir_raises_for_the_caller_to_witness(self, tmp_path):
        with pytest.raises(OSError):
            self._views(tmp_path / "missing")

    # review r1 #6 + the TUI twin: lock, auxfiles and partial/ are touched by a
    # FAILED or locked update — only list files may age the lists.
    def test_list_age_ignores_lock_partial_and_auxfiles(self, tmp_path):
        f = tmp_path / "deb.debian.org_debian_dists_trixie_main_binary-arm64_Packages"
        f.write_text("x")
        os.utime(f, (self.NOW - 10 * 86400, self.NOW - 10 * 86400))
        for extra in ("partial", "auxfiles"):
            (tmp_path / extra).mkdir()
            os.utime(tmp_path / extra, (self.NOW - 60, self.NOW - 60))
        (tmp_path / "lock").write_text("")
        os.utime(tmp_path / "lock", (self.NOW - 60, self.NOW - 60))
        assert self._age(tmp_path) == 240

    def test_list_age_with_no_list_file_is_unknown(self, tmp_path):
        (tmp_path / "lock").write_text("")
        assert self._age(tmp_path) is None

    def test_the_reporter_ships_the_shared_source_and_the_field(self):
        assert "def security_views" in dac.APT_LISTS_SRC
        assert '"security_views"' in dac._REMOTE_SRC

    def test_the_shared_module_is_stdlib_only_with_local_imports(self):
        # It runs on boxes with no checkout: a module-level or first-party
        # import would only fail THERE.
        import ast
        tree = ast.parse(dac.APT_LISTS_SRC)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or node.names[0].name
                assert mod.split(".")[0] in {"os", "email", "re"}, mod


# Non-author review r2 (2026-11-04, `/code-review medium 937e914a..8532e31a`).
class TestReviewR2:
    def _apt(self, **kw):
        apt = {"unattended_upgrades": "installed", "pending_total": 0,
               "pending_security": 0, **STAMP_OK, "lists_age_h": 2, "security_views": [_view(1)]}
        apt.update(kw)
        return apt

    @staticmethod
    def _apt_lines(status):
        return [ln for ln in status.splitlines() if re.match(r"^boxA\s+apt\s", ln)]

    # r2 #2: a box with NO security suite cannot see a security update at all —
    # that is a configuration finding, not a lagging view.
    def test_no_security_suite_is_a_finding(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch,
                                     _report(apt=self._apt(security_views=[])), [])
        assert rc == 1 and finding and "no *-security suite" in finding
        assert not any("lower-bound" in ln for ln in self._apt_lines(status)), \
            "one cause, one line — not a finding AND a lower-bound"

    # r2 #5: list mtimes are the SERVER's Last-Modified — Debian's main stable
    # InRelease was dated 2026-09-12 on 11-05. The 7-day finding must read the
    # success-only `update-stamp` apt.systemd.daily touches, not content age.
    def test_quiet_archive_with_a_fresh_update_stamp_is_not_stale(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(apt=self._apt(
            lists_age_h=1300, update_stamp_age_h=5)), [])
        assert rc == 0 and finding is None and "stale" not in status

    def test_an_old_update_stamp_is_the_stale_finding(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(apt=self._apt(
            lists_age_h=3, update_stamp_age_h=200)), [])
        assert rc == 1 and finding and "no successful periodic apt-get update in 200h" in finding

    # r2 #8: the manager's OWN blindness is one cause, said once in the header.
    def test_unreachable_archive_is_named_once_as_the_managers_blindness(self, home, monkeypatch):
        monkeypatch.setattr(dac, "fetch_reference_date",
                            lambda url, timeout=20: (None, "archive unreachable: URLError"))
        rc, status, _ = _drive(home, monkeypatch, _report(apt=self._apt()), [],
                               hosts=("boxA", "boxB"))
        hdr = [ln for ln in status.splitlines() if ln.startswith("# apt: the MANAGER")]
        assert len(hdr) == 1 and "archive unreachable" in hdr[0]

    # r2 #9: the number and the verdict must never disagree — 24h00m clean
    # and 24h59m lower-bound must not both print "24h".
    def test_displayed_hours_never_contradict_the_verdict(self, home, monkeypatch):
        _, at, _ = _drive(home, monkeypatch, _report(apt=self._apt(
            security_views=[_view(dac.APT_SECURITY_VIEW_FRESH_H)])), [])
        _, past, _ = _drive(home, monkeypatch, _report(apt=self._apt(
            security_views=[_view(dac.APT_SECURITY_VIEW_FRESH_H + 59 / 60)])), [])
        w = dac.APT_SECURITY_VIEW_FRESH_H
        assert "%dh behind" % w in self._apt_lines(at)[0]
        assert "%dh behind" % (w + 1) in self._apt_lines(past)[0]

    # r2 #10 → r4 #6: a formatting helper never does network I/O by default —
    # and a silent "no reference" default made the box vanish from every
    # tally. The reference is REQUIRED: forgetting it is a TypeError, not a
    # quietly unobservable box.
    def test_apt_lines_requires_a_reference(self):
        with pytest.raises(TypeError):
            dac.apt_lines("boxA", self._apt())

    # r2 #7: validate before fetch must survive a redirect.
    # r3 #5: a REAL Request, and the refusal pinned by type + message — with
    # req=None any AttributeError satisfied `raises(Exception)`, so deleting
    # the allowlist check could not fail this test.
    def test_a_redirect_off_the_allowlist_is_refused(self):
        import urllib.error
        import urllib.request
        h = dac._AllowlistRedirect()
        req = urllib.request.Request("https://deb.debian.org/a")
        with pytest.raises(urllib.error.URLError, match="off the allowlist.*evil.example"):
            h.redirect_request(req, None, 302, "Found", {},
                               "https://evil.example/debian-security/dists/x/InRelease")
        with pytest.raises(urllib.error.URLError, match="off the allowlist"):
            h.redirect_request(req, None, 302, "Found", {},
                               "http://deb.debian.org/debian-security/dists/x/InRelease")
        assert h.redirect_request(
            req, None, 302, "Found", {},
            "https://security.debian.org/debian-security/dists/x/InRelease") is not None


# Non-author review r3 (2026-11-04, `/code-review medium 8532e31a..0ea7df21`).
class TestReviewR3:
    NOW = _dt.datetime(2026, 11, 5, tzinfo=_dt.timezone.utc).timestamp()

    def _apt(self, **kw):
        apt = {"unattended_upgrades": "installed", "pending_total": 0,
               "pending_security": 0, **STAMP_OK, "lists_age_h": 2, "security_views": [_view(1)]}
        apt.update(kw)
        return apt

    # r3 #1: a READABLE but truncated security InRelease (0-byte power-loss
    # corpse, persistent_issues 08-27) has no Suite: — it must stay an
    # unknown view by NAME, never vanish into "no security suite".
    @pytest.mark.parametrize("body", ["", "-----BEGIN PGP SIGNED MESSAGE-----\n"])
    def test_a_truncated_security_inrelease_is_kept_unknown(self, tmp_path, body):
        (tmp_path / SEC_FILE).write_text(body)
        ns = _rendered_reporter_ns()
        assert ns["security_views"](str(tmp_path)) == [
            {"file": SEC_FILE, "suite": None, "date_ts": None}]

    # r3 #2: "[]" is also a configured source that never fetched — the
    # finding must not tell the operator to add what is already there.
    def test_no_suite_finding_names_both_causes(self, home, monkeypatch):
        _, _, finding = _drive(home, monkeypatch,
                               _report(apt=self._apt(security_views=[])), [])
        assert "never fetched" in finding and "apt-get update" in finding

    # r3 #4: the manager's blindness is ONE cause — it must not ALSO be
    # counted as N boxes whose "view trails the archive".
    def test_manager_blindness_is_not_counted_as_box_lag(self, home, monkeypatch):
        monkeypatch.setattr(dac, "fetch_reference_date",
                            lambda url, timeout=20: (None, "archive unreachable: x"))
        _, status, _ = _drive(home, monkeypatch, _report(apt=self._apt()), [],
                              hosts=("boxA", "boxB"))
        assert "# apt: the MANAGER" in status
        assert "LOWER BOUND" not in status, "the box count must exclude manager blindness"

    # r4 #1/#2: the MIXED case, actually built this time — trixie measured
    # 40 h behind (readable archive) + bookworm whose archive the manager
    # cannot read. The measured box lag must win: shown AND counted.
    def test_box_lag_is_still_counted_beside_manager_blindness(self, home, monkeypatch):
        other = SEC_FILE.replace("trixie-security", "bookworm-security")

        def ref(url, timeout=20):
            return (None, "archive unreachable: x") if "bookworm" in url else (ARCHIVE_TS, None)
        monkeypatch.setattr(dac, "fetch_reference_date", ref)
        _, status, _ = _drive(home, monkeypatch, _report(apt=self._apt(
            security_views=[_view(40), _view(1, file=other)])), [])
        line = [ln for ln in status.splitlines() if re.match(r"^boxA\s+apt\s", ln)][0]
        assert "40h behind the archive" in line, line
        assert "# apt: 1 box(es) read 0 security as a LOWER BOUND" in status
        assert "# apt: the MANAGER" in status, "the blind suite is still said once"

    # r3 #6: the refusal's words survive to the operator, not just a type name.
    def test_fetch_keeps_the_reason_text(self, monkeypatch):
        import urllib.error

        class Opener:
            def open(self, url, timeout):
                raise urllib.error.URLError("redirect off the allowlist refused: portal.local")
        monkeypatch.setattr(dac, "fetch_reference_date", _REAL_FETCH)   # this one only
        monkeypatch.setattr(dac.urllib.request, "build_opener", lambda *a: Opener())
        ts, err = dac.fetch_reference_date("https://deb.debian.org/x")
        assert ts is None and "portal.local" in err

    # r3 #8: list mtimes are the SERVER's clock; on a box whose clock is behind
    # they are in the FUTURE — unknown, never "0h fresh" (hfm #6, same rule
    # as update_stamp_age_h).
    def test_future_list_mtimes_are_unknown_not_fresh(self, tmp_path):
        f = tmp_path / "deb.debian.org_debian_dists_trixie_main_binary-arm64_Packages"
        f.write_text("x")
        os.utime(f, (self.NOW + 3 * 86400, self.NOW + 3 * 86400))
        assert _rendered_reporter_ns()["apt_lists_scan"](str(tmp_path), self.NOW)[0] is None

    # r3 #9: kept defensive — an empty view list must never compute lag 0.
    def test_lag_of_an_empty_view_list_is_unmeasured_not_zero(self):
        lag, why, cause = dac.security_view_lag({"security_views": []},
                                                lambda u: (ARCHIVE_TS, None))
        assert lag is None and why and cause == dac.CAUSE_BOX

    def test_a_box_defect_is_named_even_when_the_manager_is_blind(self):
        # The manager's blindness must never HIDE a box-side defect.
        other = SEC_FILE.replace("trixie-security", "bookworm-security")
        lag, why, cause = dac.security_view_lag(
            {"security_views": [_view(1), _view(file=other, date_ts=None)]},
            lambda u: (None, "archive unreachable: x"))
        assert cause == dac.CAUSE_BOX and "Date unreadable" in why


# Non-author review r4 (2026-11-04, `/code-review medium 0ea7df21..07d79afc`).
# r4 broke the patch-per-edge loop: views are security by NAME only, and each
# box's verdict is decided ONCE over every view's own state.
class TestReviewR4:
    NOW = _dt.datetime(2026, 11, 5, tzinfo=_dt.timezone.utc).timestamp()

    def _apt(self, **kw):
        apt = {"unattended_upgrades": "installed", "pending_total": 0,
               "pending_security": 0, "lists_age_h": 2, "security_views": [_view(1)]}
        apt.update(kw)
        return apt

    # r4 #5: truncation can cut INSIDE the Suite: value — security is decided
    # by the file NAME, so no truncation point can drop the view.
    @pytest.mark.parametrize("body", [
        "-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\nOrigin: Debian\nSuite: stable-secu",
        "Origin: Debian\nSuite: stable-security\nDate: Wed, 04 Nov 20"])
    def test_truncation_anywhere_keeps_the_view_unknown(self, tmp_path, body):
        (tmp_path / SEC_FILE).write_text(body)
        views = _rendered_reporter_ns()["security_views"](str(tmp_path))
        assert len(views) == 1 and views[0]["file"] == SEC_FILE
        assert views[0]["date_ts"] is None

    # r4 #3 → r5 #4: list age is DISPLAY ONLY now (the stale finding judges
    # the update-stamp alone), so a future-dated file is just skipped — one
    # skewed third-party server must not blind the whole box's age.
    def test_a_future_dated_file_is_skipped_not_blinding(self, tmp_path):
        new = tmp_path / "repo.example_debian_dists_stable_InRelease"
        cur = tmp_path / "deb.debian.org_debian_dists_trixie-updates_InRelease"
        for f, t in ((new, self.NOW + 3600 * 2), (cur, self.NOW - 2 * 3600)):
            f.write_text("x")
            os.utime(f, (t, t))
        assert _rendered_reporter_ns()["apt_lists_scan"](str(tmp_path), self.NOW)[0] == 2

    # r4 #4: a sweep that could not measure ANY security currency is not green.
    def test_manager_blindness_makes_the_run_unknown_not_green(self, home, monkeypatch):
        monkeypatch.setattr(dac, "fetch_reference_date",
                            lambda url, timeout=20: (None, "archive unreachable: x"))
        _finding(home).write_text("# a prior finding\n")
        rc, status, _ = _drive(home, monkeypatch, _report(apt=self._apt()), [])
        assert rc == 2, "the manager's blindness must not exit 0"
        assert "security-archive" in status.splitlines()[1]
        assert _finding(home).exists(), "a partial view must not erase the finding file"

    # r4 #7: one record = one line, whatever the exception text holds.
    def test_reason_text_is_one_line(self, monkeypatch):
        import urllib.error

        class Opener:
            def open(self, url, timeout):
                raise urllib.error.URLError("line one\nline two\r\tthree")
        monkeypatch.setattr(dac, "fetch_reference_date", _REAL_FETCH)
        monkeypatch.setattr(dac.urllib.request, "build_opener", lambda *a: Opener())
        _, err = dac.fetch_reference_date("https://deb.debian.org/x")
        assert "\n" not in err and "\r" not in err and "line one line two three" in err

    # r4 #9: the cause is its own NAMED field — no truthy string a boolean
    # reader can mistake for box lag.
    def test_apt_lines_returns_a_named_cause(self):
        res = dac.apt_lines("boxA", self._apt(security_views=[_view(40)]),
                            lambda u: (ARCHIVE_TS, None))
        assert res.lower_bound_cause == dac.CAUSE_BOX
        blind = dac.apt_lines("boxA", self._apt(), lambda u: (None, "x"))
        assert blind.lower_bound_cause == dac.CAUSE_MANAGER
        ok = dac.apt_lines("boxA", self._apt(), lambda u: (ARCHIVE_TS, None))
        assert ok.lower_bound_cause is None


class TestReviewR2Reporter:
    NOW = _dt.datetime(2026, 11, 5, tzinfo=_dt.timezone.utc).timestamp()

    @pytest.fixture(autouse=True)
    def _fns(self):
        self.ns = _rendered_reporter_ns()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
    def test_an_unreadable_non_security_list_does_not_blind_the_box(self, tmp_path):
        # r2 #4: e.g. a power-loss-truncated, 0-perm Raspberry Pi InRelease.
        f = tmp_path / "archive.raspberrypi.com_debian_dists_trixie_InRelease"
        f.write_text("x")
        f.chmod(0)
        try:
            assert self.ns["security_views"](str(tmp_path)) == []
        finally:
            f.chmod(0o644)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
    def test_an_unreadable_security_list_is_kept_unknown(self, tmp_path):
        f = tmp_path / SEC_FILE
        f.write_text("x")
        f.chmod(0)
        try:
            assert self.ns["security_views"](str(tmp_path)) == [
                {"file": SEC_FILE, "suite": None, "date_ts": None}]
        finally:
            f.chmod(0o644)

    def test_update_stamp_age(self, tmp_path):
        s = tmp_path / "update-stamp"
        s.write_text("")
        os.utime(s, (self.NOW - 5 * 3600, self.NOW - 5 * 3600))
        assert self.ns["update_stamp_age_h"](str(s), self.NOW) == 5
        assert self.ns["update_stamp_age_h"](str(tmp_path / "none"), self.NOW) is None
        os.utime(s, (self.NOW + 86400, self.NOW + 86400))     # clock went backward (hfm #6)
        assert self.ns["update_stamp_age_h"](str(s), self.NOW) is None

    def test_the_reporter_sends_the_update_stamp(self):
        assert '"update_stamp_age_h"' in dac._REMOTE_SRC
        assert "/var/lib/apt/periodic/update-stamp" in dac._REMOTE_SRC


# Non-author review r5 (2026-11-04, `/code-review medium 07d79afc..b8f90e2d`).
class TestReviewR5:
    def _apt(self, **kw):
        apt = {"unattended_upgrades": "installed", "pending_total": 0,
               "pending_security": 0, "lists_age_h": 2, "security_views": [_view(1)]}
        apt.update(kw)
        return apt

    # r5 #2/#3 (MEASURED by the reviewer): truncation inside the time or the
    # zone parses to a plausible WRONG instant — "…21:4" → 21:04, "…21:43:53 U"
    # → a NAIVE datetime read in the box's local zone (HST = 10 h off). Only a
    # complete, zoned RFC 2822 Date is a Date.
    @pytest.mark.parametrize("value", [
        "Wed, 04 Nov 2026 21:43:53 U", "Wed, 04 Nov 2026 21:4", "Wed, 04 Nov 2026",
        "Wed, 04 Nov 2026 21:43:53", "Wed, 04 Nov 2026 21:43:53 UT"])
    def test_a_truncated_date_is_unknown_never_a_wrong_instant(self, value):
        assert dac.apt_lists.parse_release_date("Date: %s\n" % value) is None

    @pytest.mark.parametrize("value", [
        "Wed, 04 Nov 2026 21:43:53 UTC", "Wed, 04 Nov 2026 21:43:53 +0000",
        "Wed, 4 Nov 2026 21:43:53 UTC"])
    def test_a_complete_zoned_date_parses(self, value):
        assert dac.apt_lists.parse_release_date("Date: %s\n" % value) == ARCHIVE_TS

    # r5 #4: the stamp is the ONLY stale judge. Absent = no successful
    # periodic update ever recorded = a FINDING; present but unreadable / in
    # the future = UNKNOWN, said, not a finding.
    def test_an_absent_update_stamp_is_a_finding(self, home, monkeypatch):
        rc, _, finding = _drive(home, monkeypatch, _report(apt=self._apt(
            update_stamp_present=False, lists_age_h=3)), [])
        assert rc == 1 and finding and "update-stamp" in finding

    def test_a_present_but_unreadable_stamp_is_unknown_not_a_finding(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(apt=self._apt(
            update_stamp_present=True, update_stamp_age_h=None, lists_age_h=900)), [])
        assert finding is None and "UNKNOWN — update-stamp age" in status
        assert rc == 2                       # r6 #1: reaches the UNKNOWN list

    def test_list_age_alone_never_makes_the_stale_finding(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(apt=self._apt(
            update_stamp_present=True, update_stamp_age_h=6, lists_age_h=1300)), [])
        assert finding is None and "stale" not in status

    # r5 #5 kept: a manager failure never HIDES a corrupt view. (r5's other
    # half — "never consult the manager once a box-side unknown is seen" —
    # was REVERSED by r6 #3: it hid a measured sibling's lag. See TestReviewR6.)
    def test_a_box_side_unknown_is_not_hidden_by_manager_blindness(self):
        other = SEC_FILE.replace("trixie-security", "bookworm-security")
        v = dac.security_view_lag(
            {"security_views": [_view(1), _view(file=other, date_ts=None)]},
            lambda u: (None, "archive unreachable: x"))
        assert v.cause == dac.CAUSE_BOX and "Date unreadable" in v.why

    # r5 #8: a security dist with an extra path component still counts.
    def test_a_nested_security_dist_is_still_a_security_view(self, tmp_path):
        name = "deb.example.org_debian_dists_foo_bar-security_InRelease"
        (tmp_path / name).write_text("Suite: bar-security\nDate: Wed, 04 Nov 2026 21:43:53 UTC\n")
        views = _rendered_reporter_ns()["security_views"](str(tmp_path))
        assert [v["file"] for v in views] == [name]

    # r5 #9: the lag verdict is a NAMED result too.
    def test_security_view_lag_returns_named_fields(self):
        v = dac.security_view_lag({"security_views": [_view(3)]}, lambda u: (ARCHIVE_TS, None))
        assert (v.lag_s // 3600, v.why, v.cause) == (3, "", None)

    # r5 #7: the manager entry in the UNKNOWN list cannot be read as a host.
    def test_manager_entry_is_not_host_shaped(self, home, monkeypatch):
        monkeypatch.setattr(dac, "fetch_reference_date", lambda url, timeout=20: (None, "x"))
        _, status, _ = _drive(home, monkeypatch, _report(apt=self._apt()), [])
        assert dac.MANAGER_ARCHIVE_UNKNOWN in status.splitlines()[1]

    def test_the_reporter_sends_stamp_presence(self):
        assert '"update_stamp_present"' in dac._REMOTE_SRC


class TestTuiListAgeUsesTheSharedDerivation:
    # r2 #6: the old caveat `age is None or (pending == 0 and age is None)`
    # could never fire on an OLD age; both surfaces now share one constant.
    def test_tui_reads_the_shared_stale_constant_and_the_update_stamp(self):
        src = open(os.path.join(_ROOT, "src", "launcher_tui", "handlers",
                                "platform_posture.py")).read()
        assert "APT_LISTS_STALE_H" in src and "update_stamp_age_h" in src
        assert "pending == 0 and age is None" not in src
        # r3 #7: unknown is not stale — the caveat must not assert staleness
        # when neither the stamp nor the lists could be read.
        assert "stale = judged_h is None or" not in src
        assert dac.APT_LISTS_STALE_H is __import__("utils.apt_lists").apt_lists.APT_LISTS_STALE_H

    def test_platform_screen_does_not_stat_the_lists_directory(self):
        src = open(os.path.join(_ROOT, "src", "launcher_tui", "handlers",
                                "platform_posture.py")).read()
        assert 'os.stat("/var/lib/apt/lists")' not in src
        assert "from utils import apt_lists as _al" in src
        assert 'apt_lists_scan("/var/lib/apt/lists"' in src


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


class TestAVersionlessMetadataDirIsReadBeforeBeingCalledUnknown:
    """Debian ships `cryptography.egg-info/PKG-INFO` beside the versioned
    dist-info (dpkg-owned, bookworm and noble). Reading the NAME alone called
    moc4 and moc5 UNKNOWN for a version one file away — the sweep's own
    defect, carried as "2 genuine UNKNOWNs" until 2026-09-06."""

    def test_egg_info_version_comes_from_pkg_info_and_is_one_row(self, tmp_path):
        site = _plant_env(tmp_path / "usr/lib/python3/dist-packages",
                          "cryptography-38.0.4.dist-info", "cryptography.egg-info")
        (site / "cryptography.egg-info" / "PKG-INFO").write_text(
            "Metadata-Version: 2.1\nName: cryptography\nVersion: 38.0.4\n")
        envs = _walk_envs([("system-dist", str(tmp_path / "usr/lib/python3/dist-packages"))],
                          lambda cmd: (1, ""))
        recs = envs[0]["packages"]["cryptography"]
        assert [r["version"] for r in recs] == ["38.0.4"], recs

    def test_no_version_anywhere_stays_unknown(self, tmp_path):
        site = _plant_env(tmp_path / "usr/lib/python3/dist-packages", "cryptography.egg-info")
        (site / "cryptography.egg-info" / "not-metadata").write_text("")
        envs = _walk_envs([("system-dist", str(tmp_path / "usr/lib/python3/dist-packages"))],
                          lambda cmd: (1, ""))
        recs = envs[0]["packages"]["cryptography"]
        assert [r["version"] for r in recs] == [None], recs


# Non-author review r6 (2026-11-04, `/code-review medium b8f90e2d..56397e87`),
# fixed in a fresh session. #3: r5's box-side-first early return REVERSED
# r4 #1 — a corrupt view returned before a sibling's lag was measured, so a
# view trailing by days was hidden behind "Date unreadable". Order now:
# measure every view that CAN be measured, then
# measured-past-window > box-unknown > manager-unknown, every reason shown.
class TestReviewR6:
    OTHER = SEC_FILE.replace("trixie-security", "bookworm-security")
    THIRD = SEC_FILE.replace("trixie-security", "forky-security")

    def _apt(self, views):
        return {"unattended_upgrades": "installed", "pending_total": 0,
                "pending_security": 0, "lists_age_h": 2,
                "update_stamp_present": True, "update_stamp_age_h": 3,
                "security_views": views}

    def test_measured_lag_wins_over_a_box_side_unknown_sibling(self):
        v = dac.security_view_lag(
            {"security_views": [_view(file=self.OTHER, date_ts=None), _view(40)]},
            lambda u: (ARCHIVE_TS, None))
        assert v.cause == dac.CAUSE_BOX
        assert v.lag_s is not None and v.lag_s // 3600 == 40
        assert "Date unreadable" in v.why          # the second reason is SHOWN

    def test_box_unknown_beats_manager_unknown_and_names_both(self):
        v = dac.security_view_lag(
            {"security_views": [_view(file=self.OTHER, date_ts=None), _view(1)]},
            lambda u: (None, "archive unreachable: timeout"))
        assert v.cause == dac.CAUSE_BOX and v.lag_s is None
        assert "Date unreadable" in v.why and "archive unreachable" in v.why

    def test_measured_lag_wins_and_names_box_and_manager_reasons(self):
        def ref(url):
            return (None, "archive unreachable: timeout") if "forky" in url else (ARCHIVE_TS, None)
        v = dac.security_view_lag(
            {"security_views": [_view(file=self.OTHER, date_ts=None), _view(40),
                                _view(1, file=self.THIRD)]}, ref)
        assert v.cause == dac.CAUSE_BOX and v.lag_s // 3600 == 40
        assert "Date unreadable" in v.why and "archive unreachable" in v.why

    def test_a_measured_view_within_the_window_beside_a_box_unknown_is_box_unknown(self):
        v = dac.security_view_lag(
            {"security_views": [_view(2), _view(file=self.OTHER, date_ts=None)]},
            lambda u: (ARCHIVE_TS, None))
        assert v.cause == dac.CAUSE_BOX and v.lag_s is None and "Date unreadable" in v.why

    def test_the_rendered_line_shows_the_lag_and_the_unknown_sibling(self):
        a = dac.apt_lines("boxA", self._apt([_view(file=self.OTHER, date_ts=None), _view(40)]),
                          lambda u: (ARCHIVE_TS, None))
        line = "\n".join(a.status)
        assert a.lower_bound_cause == dac.CAUSE_BOX
        assert "40h behind the archive" in line and "Date unreadable" in line


# r6 #1 + #6 (hfm #1/#2): a stamp that cannot be judged — present but
# unreadable / future-dated, or a report that carries no stamp field at all —
# must reach the UNKNOWN list (rc 2), never sit as status text beside a box
# that otherwise reads clean. One rule: no readable age AND not reported
# absent = UNKNOWN.
class TestReviewR6StampUnknown:
    def _apt(self, **kw):
        apt = {"unattended_upgrades": "installed", "pending_total": 0,
               "pending_security": 0, "lists_age_h": 2, "security_views": [_view(1)]}
        apt.update(kw)
        return apt

    @pytest.mark.parametrize("kw", [
        {"update_stamp_present": True, "update_stamp_age_h": None},   # #1 unreadable / future
        {"update_stamp_age_h": None},                                 # #6 field missing
        {},                                                           # #6 both missing
    ])
    def test_an_unjudgeable_stamp_is_an_unknown_entry(self, kw):
        a = dac.apt_lines("boxA", self._apt(**kw), lambda u: (ARCHIVE_TS, None))
        assert a.unknown == "boxA/apt-update-stamp"
        assert any("UNKNOWN" in s and "update-stamp" in s for s in a.status)
        assert a.findings == []

    @pytest.mark.parametrize("kw", [
        {"update_stamp_present": True, "update_stamp_age_h": None},
        {"update_stamp_age_h": None},
    ])
    def test_the_run_is_unknown_not_clean(self, home, monkeypatch, kw):
        rc, status, finding = _drive(home, monkeypatch, _report(apt=self._apt(**kw)), [])
        assert rc == 2 and finding is None
        assert "boxA/apt-update-stamp" in status.splitlines()[1]

    def test_a_missing_presence_field_with_a_readable_age_is_judged(self):
        a = dac.apt_lines("boxA", self._apt(update_stamp_age_h=5), lambda u: (ARCHIVE_TS, None))
        assert a.unknown is None and any("clean" in s for s in a.status)

    def test_the_missing_field_says_so_not_unreadable(self):
        a = dac.apt_lines("boxA", self._apt(), lambda u: (ARCHIVE_TS, None))
        line = next(s for s in a.status if "update-stamp" in s)
        assert "not reported" in line


# r6 #7 (writer half): honest_status's PARTIAL-VIEW branch must say how many
# findings the run saw, but a partial run with NO findings deliberately keeps
# the previous finding file — so the count cannot come from that file. The
# status header states THIS run's count; honest_status reads it.
class TestReviewR6FindingCount:
    def _apt(self, **kw):
        apt = {"unattended_upgrades": "installed", "pending_total": 0,
               "pending_security": 0, "lists_age_h": 2, "security_views": [_view(1)]}
        apt.update(STAMP_OK)
        apt.update(kw)
        return apt

    @staticmethod
    def _count(status):
        hits = [ln for ln in status.splitlines() if ln.startswith("# findings this run: ")]
        assert len(hits) == 1, status
        return int(hits[0].split(": ", 1)[1].split()[0])

    def test_clean_run_states_zero(self, home, monkeypatch):
        rc, status, _ = _drive(home, monkeypatch, _report(apt=self._apt()), [])
        assert rc == 0 and self._count(status) == 0

    def test_findings_run_states_its_count(self, home, monkeypatch):
        rc, status, finding = _drive(home, monkeypatch, _report(apt=self._apt(
            pending_security=2, unattended_upgrades="absent")), [])
        n = len([ln for ln in finding.splitlines() if ln and not ln.startswith("#")])
        assert rc == 1 and self._count(status) == n == 2

    def test_partial_run_states_this_runs_count_not_the_kept_file(self, home, monkeypatch):
        # A first run leaves 2 findings; a second, partial (stamp unknown) run
        # finds nothing and KEEPS that file — its header must still say 0.
        _drive(home, monkeypatch, _report(apt=self._apt(
            pending_security=2, unattended_upgrades="absent")), [])
        rc, status, finding = _drive(home, monkeypatch, _report(apt=self._apt(
            update_stamp_present=True, update_stamp_age_h=None)), [])
        assert rc == 2 and finding is not None          # the kept file
        assert self._count(status) == 0


# r6 #10: apt names a list file by writing every `/` as `_` and a literal
# `_` as `%5f`. The URL builder's path and dist groups excluded `_`, so every
# view r5 #8 newly matched (`…_dists_<x>_<y>-security_InRelease`) and every
# multi-component archive path read "no reference archive" — a box-side
# UNKNOWN invented by the parser — and its `.replace('_', '/')` was dead.
class TestReviewR6ReferenceUrl:
    @pytest.mark.parametrize("name,url", [
        ("deb.debian.org_debian-security_dists_foo_bar-security_InRelease",
         "https://deb.debian.org/debian-security/dists/foo/bar-security/InRelease"),
        ("deb.debian.org_debian_security_dists_trixie-security_InRelease",
         "https://deb.debian.org/debian/security/dists/trixie-security/InRelease"),
        ("deb.debian.org_my%5frepo_dists_trixie-security_InRelease",
         "https://deb.debian.org/my_repo/dists/trixie-security/InRelease"),
        # traversal stays refused in EITHER group, however it is spelled
        ("deb.debian.org_debian_.._dists_trixie-security_InRelease", None),
        ("deb.debian.org_debian-security_dists_.._trixie-security_InRelease", None),
        ("deb.debian.org_debian-security_dists__InRelease", None),
    ])
    def test_apt_filename_encoding_round_trips(self, name, url):
        assert dac.security_reference_url(name) == url

    def test_every_name_the_view_matcher_accepts_gets_a_url(self, tmp_path):
        # The two ends of one artifact share ONE notion of a security view
        # (hfm #5): whatever security_views() keeps on an allowlisted host,
        # security_reference_url() must be able to name.
        names = ["deb.debian.org_debian-security_dists_trixie-security_InRelease",
                 "deb.debian.org_debian-security_dists_foo_bar-security_InRelease",
                 "security.debian.org_debian_security_dists_x_y_z-security_InRelease"]
        for n in names:
            (tmp_path / n).write_text("Date: Wed, 04 Nov 2026 21:43:53 UTC\n")
        views = _rendered_reporter_ns()["security_views"](str(tmp_path))
        assert sorted(v["file"] for v in views) == sorted(names)
        assert [v["file"] for v in views if dac.security_reference_url(v["file"]) is None] == []


# r6 #9: the manager's UNKNOWN token is ONE constant (hfm #5). The module
# docstring named a stale spelling; honest_status matches entries by the
# `manager:` PREFIX, so the constant must keep it.
class TestReviewR6ManagerToken:
    def test_one_constant_and_the_prefix_honest_status_matches(self):
        assert dac.MANAGER_ARCHIVE_UNKNOWN.startswith("manager:")
        hs = open(os.path.join(_ROOT, "scripts", "honest_status.sh")).read()
        assert "grep '^manager:'" in hs

    def test_no_stale_spelling_in_the_source(self):
        src = open(os.path.join(_ROOT, "scripts", "dep_advisory_check.py")).read()
        assert "manager/security-archive" not in src
        assert src.count('"%s"' % dac.MANAGER_ARCHIVE_UNKNOWN) == 1   # defined once


# r6 #4: a box whose clock is BEHIND sees every freshly fetched list file
# dated in the future; those are skipped (list age is display-only, r5), so
# the age came from some OLD file and read as a confident "lists 400h old".
# The skip must be SAID, with its count, wherever the age is shown.
class TestReviewR6FutureListFiles:
    NOW = _dt.datetime(2026, 11, 5, tzinfo=_dt.timezone.utc).timestamp()

    def test_the_reporter_counts_future_dated_list_files(self, tmp_path):
        for i, off in enumerate((3 * 86400, 2 * 86400, -400 * 3600)):
            f = tmp_path / ("deb.debian.org_debian_dists_s%d_InRelease" % i)
            f.write_text("x")
            os.utime(f, (self.NOW + off, self.NOW + off))
        (tmp_path / "lock").write_text("")
        ns = _rendered_reporter_ns()
        assert ns["apt_lists_scan"](str(tmp_path), self.NOW) == (400, 2)

    def test_an_unreadable_dir_is_unknown_not_zero(self, tmp_path):
        ns = _rendered_reporter_ns()
        assert ns["apt_lists_scan"](str(tmp_path / "nope"), self.NOW) == (None, None)

    def test_the_reporter_sends_the_count(self):
        assert '"lists_future_n"' in dac._REMOTE_SRC

    def test_the_status_line_says_the_skip(self):
        apt = {"unattended_upgrades": "installed", "pending_total": 0,
               "pending_security": 0, "lists_age_h": 400, "lists_future_n": 12,
               "security_views": [_view(1)]}
        apt.update(STAMP_OK)
        line = "\n".join(dac.apt_lines("boxA", apt, lambda u: (ARCHIVE_TS, None)).status)
        assert "lists 400h old excluding 12 future-dated file(s)" in line

    def test_no_note_when_none_were_skipped(self):
        apt = {"unattended_upgrades": "installed", "pending_total": 0,
               "pending_security": 0, "lists_age_h": 2, "lists_future_n": 0,
               "security_views": [_view(1)]}
        apt.update(STAMP_OK)
        line = "\n".join(dac.apt_lines("boxA", apt, lambda u: (ARCHIVE_TS, None)).status)
        assert "future-dated" not in line


# Non-author review r7 (2026-11-05 00:44 row, `56397e87..3e9dfb18`).
# Accepted input, stated once: the reporter's apt dict, built from file NAMES
# apt itself writes under /var/lib/apt/lists (`<host>_<path>_dists_<dist>_…`,
# `/`→`_`, literal `_`→`%5f`).
class TestReviewR7:
    NOW = _dt.datetime(2026, 11, 5, tzinfo=_dt.timezone.utc).timestamp()

    # r7 #1: age and the future-dated count came from TWO walks with their own
    # copy of the filter + 600 s tolerance — two consumers of one artifact
    # (hfm #5). ONE pass returns both; the duplicates are gone.
    def test_one_scan_returns_age_and_future_count(self, tmp_path):
        for i, off in enumerate((3 * 86400, 2 * 86400, -400 * 3600)):
            f = tmp_path / ("deb.debian.org_debian_dists_s%d_InRelease" % i)
            f.write_text("x")
            os.utime(f, (self.NOW + off, self.NOW + off))
        (tmp_path / "lock").write_text("")
        ns = _rendered_reporter_ns()
        assert ns["apt_lists_scan"](str(tmp_path), self.NOW) == (400, 2)
        assert ns["apt_lists_scan"](str(tmp_path / "nope"), self.NOW) == (None, None)
        assert "apt_lists_age_h" not in ns and "apt_lists_future_n" not in ns

    def test_the_reporter_walks_the_lists_once(self):
        assert dac._REMOTE_SRC.count("apt_lists_scan(") == 1
        assert "apt_lists_age_h" not in dac._REMOTE_SRC

    # r7 #4 REFUTED by measurement: both halves decode `_`→`/` and rejoin at
    # `/dists/`, so WHICH `_dists_` splits the name cannot change the URL.
    # Pinned so a future "fix" that refuses or re-splits must justify itself.
    def test_the_split_point_cannot_change_the_url(self):
        assert dac.security_reference_url(
            "deb.debian.org_a_dists_b_dists_trixie-security_InRelease") == \
            "https://deb.debian.org/a/dists/b/dists/trixie-security/InRelease"
