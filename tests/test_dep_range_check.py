"""scripts/dep_range_check.py — does each DECLARED range still permit safety?

The sibling suite (test_dep_advisory_check.py) exists for "a degraded read
rendering as a healthy answer". This one exists for that class PLUS its
inversion, because this checker's healthy answer is an ASSERTION OF ABSENCE:
"no advisory covers this version." An absence is exactly what a failed fetch
also looks like. So the assertions below are deliberately lopsided toward
proving that missing data can never become "clean":

  * PyPI unreachable must not yield an empty version list — an empty list would
    make `permitted` empty and INVERT the verdict;
  * an advisory query that failed must not yield zero ranges — zero ranges
    means "nothing is vulnerable", the most dangerous possible lie here;
  * a vulnerable range that will not PARSE must poison the whole package —
    we cannot prove a version escapes a constraint we could not read.

And one regression test that matters more than the rest: the historical pin
that sat unnoticed for six months must come back as a FINDING.
"""

import importlib.util
import os
import pathlib
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

_spec = importlib.util.spec_from_file_location(
    "dep_range_check", os.path.join(_ROOT, "scripts", "dep_range_check.py"))
drc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drc)

from packaging.specifiers import SpecifierSet  # noqa: E402
from packaging.version import Version  # noqa: E402


def V(*xs):
    return [Version(x) for x in xs]


def R(*triples):
    return [(g, s, SpecifierSet(r)) for g, s, r in triples]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(drc, "get_real_user_home", lambda: str(tmp_path))
    return tmp_path


def _repo(tmp_path, **files):
    """Build a throwaway repo with the given requirements files."""
    root = pathlib.Path(tmp_path) / "repo"
    (root / "requirements").mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (root / "requirements" / (name + ".txt")).write_text(body)
    return str(root)


# --------------------------------------------------------------------------
# THE regression test
# --------------------------------------------------------------------------

class TestTheHistoricalPinIsCaught:
    """`cryptography>=45.0.7,<47` must come back as a FINDING.

    Every version satisfying it carried a medium plus three highs, for six
    months, while probe_dep_version_drift read `clean` and Dependabot reported
    one medium then flipped to `fixed`. If this assertion ever passes as
    `clean`, the checker has stopped asking its own question.
    """

    def test_a_range_whose_every_release_is_vulnerable_is_a_finding(self):
        state, detail = drc.evaluate(
            SpecifierSet(">=45.0.7,<47"),
            V("45.0.7", "46.0.0", "46.0.5", "46.0.7", "49.0.0", "50.0.1"),
            R(("GHSA-m2h6-j472-rp4c", "medium", ">= 45.0.0, <= 48.0.0"),
              ("GHSA-g6cj-pr64-35w5", "high", ">= 44.0.0, < 50.0.0")))
        assert state == drc.FINDING, (state, detail)
        assert "UNPATCHABLE" in detail

    def test_the_fixed_pin_is_clean_and_names_the_safe_version(self):
        state, detail = drc.evaluate(
            SpecifierSet(">=50.0.1,<51"),
            V("46.0.7", "49.0.0", "50.0.1"),
            R(("GHSA-g6cj-pr64-35w5", "high", ">= 44.0.0, < 50.0.0")))
        assert state == drc.CLEAN, (state, detail)
        assert "50.0.1" in detail


# --------------------------------------------------------------------------
# Missing data must never read as safety
# --------------------------------------------------------------------------

class TestAbsenceIsNeverSafety:
    def test_failed_pypi_fetch_returns_a_reason_not_an_empty_list(self, monkeypatch):
        def boom(*a, **kw):
            raise OSError("network down")
        monkeypatch.setattr(drc.urllib.request, "urlopen", boom)
        versions, err = drc.pypi_versions("cryptography")
        assert versions is None and err, (versions, err)
        assert versions != [], (
            "an empty version list makes `permitted` empty and inverts the "
            "verdict — a failed fetch must be a REASON")

    def test_failed_advisory_query_returns_a_reason_not_zero_ranges(self, monkeypatch):
        monkeypatch.setattr(drc, "_run", lambda cmd, timeout: (1, "", "boom"))
        ranges, err = drc.advisory_ranges("cryptography")
        assert ranges is None and err, (ranges, err)
        assert ranges != [], (
            "zero ranges means 'nothing is vulnerable' — the most dangerous "
            "lie this checker can tell")

    def test_an_unparseable_range_poisons_the_whole_package(self, monkeypatch):
        payload = ('{"ghsa_id":"GHSA-x","severity":"high","vulnerabilities":'
                   '[{"package":{"name":"cryptography"},'
                   '"vulnerable_version_range":"sometime after tuesday"}]}')
        monkeypatch.setattr(drc, "_run", lambda cmd, timeout: (0, payload, ""))
        ranges, err = drc.advisory_ranges("cryptography")
        assert ranges is None, (
            "a range we cannot read must not be silently dropped — dropping it "
            "makes a vulnerable version look safe")
        assert "would not parse" in err, err

    def test_an_advisory_with_no_range_at_all_is_unknown(self, monkeypatch):
        payload = ('{"ghsa_id":"GHSA-y","severity":"high","vulnerabilities":'
                   '[{"package":{"name":"cryptography"},'
                   '"vulnerable_version_range":null}]}')
        monkeypatch.setattr(drc, "_run", lambda cmd, timeout: (0, payload, ""))
        ranges, err = drc.advisory_ranges("cryptography")
        assert ranges is None and "no vulnerable_version_range" in err, (ranges, err)

    def test_yanked_only_releases_are_not_candidates(self, monkeypatch):
        body = ('{"releases": {"1.0": [{"yanked": true}], "2.0": [{"yanked": false}],'
                ' "3.0": []}}')

        class Resp:
            def read(self_inner):
                return body.encode()
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False

        monkeypatch.setattr(drc.urllib.request, "urlopen", lambda *a, **kw: Resp())
        versions, err = drc.pypi_versions("x")
        assert err is None
        assert versions == V("2.0"), versions


# --------------------------------------------------------------------------
# Parsing: what we skip, and what we must never skip
# --------------------------------------------------------------------------

class TestManifestParsing:
    def test_unparseable_requirement_is_returned_not_skipped(self, tmp_path):
        f = tmp_path / "r.txt"
        f.write_text("good>=1.0\n!!!broken!!!\n")
        reqs, bad = drc.parse_manifest(str(f))
        assert [r.name for _, r in reqs] == ["good"]
        assert len(bad) == 1 and bad[0][0] == 2, bad

    def test_comments_blanks_and_includes_are_skipped(self, tmp_path):
        f = tmp_path / "r.txt"
        f.write_text("# c\n\n-r other.txt\n--index-url http://x\npkg>=1\n")
        reqs, bad = drc.parse_manifest(str(f))
        assert [r.name for _, r in reqs] == ["pkg"] and bad == []

    def test_unreadable_file_is_an_error_not_an_empty_manifest(self, tmp_path):
        reqs, bad = drc.parse_manifest(str(tmp_path / "nope.txt"))
        assert reqs == [] and bad and "unreadable" in bad[0][2]

    def test_github_single_version_range_is_normalized(self):
        assert drc._normalize_range("= 1.2.3") == "== 1.2.3"
        assert SpecifierSet(drc._normalize_range("= 1.2.3")).contains(Version("1.2.3"))
        # forms already valid are left alone
        assert drc._normalize_range(">= 1.0, < 2.0") == ">= 1.0,< 2.0"


# --------------------------------------------------------------------------
# Verdicts that are not findings
# --------------------------------------------------------------------------

class TestNonFindings:
    def test_a_specifier_permitting_no_release_is_a_finding(self):
        state, detail = drc.evaluate(SpecifierSet(">=99.0"), V("1.0", "2.0"), [])
        assert state == drc.FINDING and "NO released version" in detail

    def test_no_advisories_at_all_is_clean(self):
        state, detail = drc.evaluate(SpecifierSet(">=1.0"), V("1.0", "2.0"), [])
        assert state == drc.CLEAN and "2.0" in detail


# --------------------------------------------------------------------------
# End-to-end wiring, exit codes, and the artifacts
# --------------------------------------------------------------------------

class TestEndToEnd:
    def _wire(self, monkeypatch, versions, ranges):
        monkeypatch.setattr(drc, "_run",
                            lambda cmd, timeout: (0, "", "")
                            if cmd[:3] == ["gh", "auth", "status"] else (0, "[]", ""))
        monkeypatch.setattr(drc, "pypi_versions", lambda p, timeout=30: versions)
        monkeypatch.setattr(drc, "advisory_ranges", lambda p, timeout=60: ranges)

    def test_finding_writes_the_finding_file_and_exits_1(self, home, tmp_path, monkeypatch):
        root = _repo(tmp_path, rns="cryptography>=45.0.7,<47\n")
        self._wire(monkeypatch,
                   (V("45.0.7", "46.0.5"), None),
                   (R(("GHSA-x", "high", ">= 44.0.0, < 50.0.0")), None))
        assert drc.main(["--root", root, "--quiet"]) == 1
        finding = home / ".meshforge-dep-RANGE-FINDING"
        assert finding.is_file() and "cryptography" in finding.read_text()

    def test_clean_removes_a_stale_finding_file_and_exits_0(self, home, tmp_path, monkeypatch):
        stale = home / ".meshforge-dep-RANGE-FINDING"
        stale.write_text("old\n")
        root = _repo(tmp_path, rns="cryptography>=50.0.1,<51\n")
        self._wire(monkeypatch, (V("50.0.1"), None), ([], None))
        assert drc.main(["--root", root, "--quiet"]) == 0
        assert not stale.exists(), "a cleared finding must clear its artifact"

    def test_partial_blindness_exits_2_and_keeps_the_finding_file(
            self, home, tmp_path, monkeypatch):
        stale = home / ".meshforge-dep-RANGE-FINDING"
        stale.write_text("known finding\n")
        root = _repo(tmp_path, rns="cryptography>=50.0.1,<51\n")
        self._wire(monkeypatch, (None, "PyPI unreachable: URLError"), ([], None))
        assert drc.main(["--root", root, "--quiet"]) == 2
        assert stale.exists(), (
            "a half-read audit must not delete a finding — absence of evidence "
            "from partial blindness is not evidence of absence")

    def test_missing_gh_is_unknown_not_clean(self, home, tmp_path, monkeypatch):
        root = _repo(tmp_path, rns="cryptography>=50.0.1,<51\n")
        monkeypatch.setattr(drc, "_run", lambda cmd, timeout: (1, "", "no gh"))
        assert drc.main(["--root", root, "--quiet"]) == 2
        assert "UNKNOWN" in (home / ".meshforge-dep-ranges").read_text()

    def test_no_manifest_is_unknown_not_a_clean_bill(self, home, tmp_path, monkeypatch):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(drc, "_run", lambda cmd, timeout: (0, "", ""))
        assert drc.main(["--root", str(empty), "--quiet"]) == 2, (
            "zero manifests means we checked NOTHING — that is not a pass")

    def test_fork_pinned_and_unpinned_are_inert_not_judged(self, home, tmp_path, monkeypatch):
        root = _repo(tmp_path, rns=(
            "rns @ git+https://github.com/Nursedude/reticulum.git@deadbeef\n"
            "meshtastic\n"))
        self._wire(monkeypatch, (V("1.0"), None), ([], None))
        assert drc.main(["--root", root, "--quiet"]) == 0
        report = (home / ".meshforge-dep-ranges").read_text()
        assert report.count(drc.INERT) == 2, report

    def test_the_run_actually_evaluated_something(self, home, tmp_path, monkeypatch):
        """Anti-vacuous: exit 0 must mean 'judged and safe', not 'judged nothing'."""
        root = _repo(tmp_path, core="requests>=2.31.0\n", rns="cryptography>=50.0.1\n")
        self._wire(monkeypatch, (V("50.0.1"), None), ([], None))
        assert drc.main(["--root", root, "--quiet"]) == 0
        report = (home / ".meshforge-dep-ranges").read_text()
        assert report.count(" %s " % drc.CLEAN) == 2, (
            "both requirements must appear as judged, not silently dropped:\n" + report)


class TestInertIsNotUnknown:
    """A repo that is ABSENT and a repo that is PRESENT-BUT-UNREADABLE make
    different claims, and collapsing them is a documented failure class here
    (persistent_issues: "`inert` and `indeterminate` are different claims — an
    organ absent by design must never be reported as an observation that
    failed, or the real failures have nowhere to stand out").

    The sister repo is not checked out on every box. If its absence read as
    UNKNOWN, this check would sit permanently un-green on most of the fleet and
    a real blindness would have nowhere to show.
    """

    def _wire(self, monkeypatch):
        monkeypatch.setattr(drc, "_run",
                            lambda cmd, timeout: (0, "", "")
                            if cmd[:3] == ["gh", "auth", "status"] else (0, "[]", ""))
        monkeypatch.setattr(drc, "pypi_versions", lambda p, timeout=30: (V("1.0"), None))
        monkeypatch.setattr(drc, "advisory_ranges", lambda p, timeout=60: ([], None))

    def test_absent_root_is_inert_and_does_not_spoil_the_run(
            self, home, tmp_path, monkeypatch):
        root = _repo(tmp_path, core="pkg>=1.0\n")
        self._wire(monkeypatch)
        rc = drc.main(["--root", root, "--root", str(tmp_path / "never-cloned"),
                       "--quiet"])
        assert rc == 0, "an absent sister repo must not make the run UNKNOWN"
        report = (home / ".meshforge-dep-ranges").read_text()
        assert drc.INERT in report and "not checked out here" in report, report

    def test_present_but_manifestless_root_is_unknown(self, home, tmp_path, monkeypatch):
        empty = tmp_path / "present-but-empty"
        empty.mkdir()
        self._wire(monkeypatch)
        rc = drc.main(["--root", str(empty), "--quiet"])
        assert rc == 2, (
            "a repo that EXISTS but yields no manifest is a failed observation, "
            "not an absence — it must not read as a pass")
        assert drc.UNKNOWN in (home / ".meshforge-dep-ranges").read_text()

    def test_both_repos_are_audited_and_labelled(self, home, tmp_path, monkeypatch):
        a = _repo(tmp_path / "a", core="pkga>=1.0\n")
        b = _repo(tmp_path / "b", core="pkgb>=1.0\n")
        self._wire(monkeypatch)
        assert drc.main(["--root", a, "--root", b, "--quiet"]) == 0
        report = (home / ".meshforge-dep-ranges").read_text()
        assert "2 repo(s)" in report, report
        # each repo's lines are prefixed with its own directory name, so a
        # finding can be traced to the right manifest in the right repo
        assert "repo/requirements/core.txt" in report, report
        assert report.count(" %s " % drc.CLEAN) == 2, report


class TestPaginationIsNotStringSurgery:
    """`gh api --paginate` concatenates raw JSON arrays. The tempting repair is
    to splice "][" into ",", which is string surgery on attacker-adjacent data:
    advisory descriptions are free text, and one containing that sequence would
    corrupt the parse — or parse into something valid and WRONG, which is worse
    because it would silently drop a vulnerable range and read as clean.

    So the reader is newline-delimited, one compact object per line.
    """

    def test_multiple_pages_are_all_read(self, monkeypatch):
        lines = "\n".join(
            '{"ghsa_id":"GHSA-%d","severity":"high","vulnerabilities":'
            '[{"package":{"name":"pkg"},"vulnerable_version_range":"< %d.0"}]}' % (i, i)
            for i in range(1, 4))
        monkeypatch.setattr(drc, "_run", lambda cmd, timeout: (0, lines, ""))
        ranges, err = drc.advisory_ranges("pkg")
        assert err is None and len(ranges) == 3, (ranges, err)

    def test_a_description_containing_the_splice_sequence_is_harmless(self, monkeypatch):
        payload = ('{"ghsa_id":"GHSA-z","severity":"high","description":'
                   '"see table[0][1] for detail","vulnerabilities":'
                   '[{"package":{"name":"pkg"},"vulnerable_version_range":"< 2.0"}]}')
        monkeypatch.setattr(drc, "_run", lambda cmd, timeout: (0, payload, ""))
        ranges, err = drc.advisory_ranges("pkg")
        assert err is None and len(ranges) == 1, (
            "a ']['-bearing description must not disturb the parse: %r" % (err,))
        assert ranges[0][2].contains(Version("1.0"))

    def test_the_query_asks_gh_for_newline_delimited_records(self, monkeypatch):
        seen = {}

        def fake(cmd, timeout):
            seen["cmd"] = cmd
            return 0, "", ""

        monkeypatch.setattr(drc, "_run", fake)
        drc.advisory_ranges("pkg")
        assert "--jq" in seen["cmd"], seen["cmd"]
        assert "--paginate" in seen["cmd"], (
            "dropping --paginate would silently truncate at 100 advisories; "
            "urllib3 alone has 19 and the cap is not far off for others")

    def test_a_malformed_line_is_unknown_not_a_partial_read(self, monkeypatch):
        good = ('{"ghsa_id":"GHSA-a","severity":"high","vulnerabilities":'
                '[{"package":{"name":"pkg"},"vulnerable_version_range":"< 2.0"}]}')
        monkeypatch.setattr(drc, "_run", lambda cmd, timeout: (0, good + "\n{oops", ""))
        ranges, err = drc.advisory_ranges("pkg")
        assert ranges is None and "unparseable advisory record" in err, (ranges, err)


# --------------------------------------------------------------------------
# 2026-09-06 adversarial pass (Fable 5.1) — what the live drills found
# --------------------------------------------------------------------------

import json as _json  # noqa: E402
import re as _re  # noqa: E402


class TestNamesAreCanonicalForTheAdvisoryDB:
    """CONFIRMED by drill: ``affects=python_jose`` returns 0 advisories while
    ``affects=python-jose`` returns 4. PyPI normalises names; the advisory DB
    does not. Our manifests spell ``prometheus_client`` with an underscore, so
    without canonicalisation that requirement reads "clean" forever — an
    assertion of absence built on a query that could never match."""

    def test_underscore_spelling_is_queried_with_a_hyphen(self, monkeypatch):
        seen = []

        def fake_run(cmd, timeout):
            seen.append(cmd)
            return 0, "", ""

        monkeypatch.setattr(drc, "_run", fake_run)
        ranges, err = drc.advisory_ranges("prometheus_client")
        assert err is None and ranges == []
        assert any("affects=prometheus-client&" in c for c in seen[0]), seen[0]
        assert not any("prometheus_client" in c for c in seen[0])

    def test_vulnerability_record_named_canonically_matches_an_underscore_requirement(
            self, monkeypatch):
        rec = {"ghsa_id": "GHSA-t", "severity": "high", "vulnerabilities": [
            {"package": {"name": "prometheus-client", "ecosystem": "pip"},
             "vulnerable_version_range": "< 1.0.0"}]}
        monkeypatch.setattr(drc, "_run",
                            lambda cmd, timeout: (0, _json.dumps(rec) + "\n", ""))
        ranges, err = drc.advisory_ranges("prometheus_client")
        assert err is None and len(ranges) == 1, (ranges, err)

    def test_canonical_name_is_pep503(self):
        assert drc.canonical_name("Zope.Interface") == "zope-interface"
        assert drc.canonical_name("prometheus__client") == "prometheus-client"
        assert drc.canonical_name("rns") == "rns"

    def test_a_withdrawn_advisory_is_not_a_constraint(self, monkeypatch):
        """Latent (none returned today for our packages, drilled): a withdrawn
        record counted as a range would manufacture an UNPATCHABLE page out of
        a retraction."""
        rec = {"ghsa_id": "GHSA-w", "severity": "critical",
               "withdrawn_at": "2026-01-01T00:00:00Z", "vulnerabilities": [
                   {"package": {"name": "rich", "ecosystem": "pip"},
                    "vulnerable_version_range": ">= 0"}]}
        monkeypatch.setattr(drc, "_run",
                            lambda cmd, timeout: (0, _json.dumps(rec) + "\n", ""))
        ranges, err = drc.advisory_ranges("rich")
        assert err is None and ranges == []


class TestTwoConstantsArePinnedTogether:
    """honest_failure_modes #5: two consumers of one artifact share ONE constant
    or are test-pinned together. Neither pair below could be derived at runtime
    cheaply (a bash gate reading systemd calendars; a tuple reading a comment
    convention), so the pin is a test that fails the day they drift."""

    def test_fork_pinned_matches_the_mf_fork_pin_ssot(self):
        rns_txt = pathlib.Path(_ROOT, "requirements", "rns.txt").read_text()
        ssot = set(_re.findall(r"^# MF-FORK-PIN (\S+)", rns_txt, _re.M))
        assert ssot, "no MF-FORK-PIN lines found — the SSOT moved; fix this test"
        assert ssot == set(drc.FORK_PINNED), (
            "FORK_PINNED %r != MF-FORK-PIN names %r — a third fork would be "
            "judged against PyPI and read as a finding" % (drc.FORK_PINNED, ssot))

    def test_gate_leg_staleness_window_is_two_missed_daily_windows(self):
        gate = pathlib.Path(_ROOT, "scripts", "honest_status.sh").read_text()
        m = _re.search(r"^dep_stale_h=(\d+)", gate, _re.M)
        assert m, "dep_stale_h moved or was renamed in honest_status.sh"
        stale_h = int(m.group(1))
        for unit in ("meshforge-dep-advisory.timer", "meshforge-dep-range.timer"):
            body = pathlib.Path(_ROOT, "templates", "systemd", unit).read_text()
            cal = _re.search(r"^OnCalendar=(.+)$", body, _re.M)
            assert cal, unit
            assert cal.group(1).startswith("*-*-* "), (
                "%s is no longer daily (%s) but honest_status.sh still waits "
                "dep_stale_h=%dh = two DAILY windows — change both" % (unit, cal.group(1), stale_h))
        assert stale_h == 2 * 24, (
            "dep_stale_h=%d is not two daily windows; both timers are daily" % stale_h)
