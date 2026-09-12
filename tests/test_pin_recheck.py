"""Tests for src/utils/pin_recheck.py — evaluating a pin's re-check predicates.

Every test runs OFFLINE through the injectable runner. That matters more than
usual here: the module's whole reason for existing is to refuse the sentence
"we could not check, so nothing has changed", and a test suite that needed the
network would itself go UNKNOWN and get skipped.
"""

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_platform import Pin, Recheck  # noqa: E402
from utils.pin_recheck import (  # noqa: E402
    HOLDS, MANUAL, MOVED, UNKNOWN, evaluate_pin, fold,
)


def _runner(table):
    """table maps a substring of the argv to (rc, stdout)."""
    def run(argv):
        joined = " ".join(argv)
        for needle, resp in table.items():
            if needle in joined:
                return resp
        return (1, "")
    return run


def _pin(*rechecks):
    return Pin(name="p", mechanism="apt_hold", current="1.0", why="w",
               recheck=list(rechecks))


def _rc(kind, **raw):
    raw["kind"] = kind
    return Recheck(kind=kind, means="m", raw=raw)


REL_LIST = json.dumps([
    {"tagName": "v2.8.0.alpha", "isPrerelease": True, "isDraft": False,
     "publishedAt": "2026-09-01T00:00:00Z"},
    {"tagName": "v2.7.26.54e0d8d", "isPrerelease": False, "isDraft": False,
     "publishedAt": "2026-06-24T00:00:00Z"},
])


class TestPrMerged:
    def test_open_pr_means_the_pin_holds(self):
        r = _runner({"pr view": (0, json.dumps({"state": "OPEN",
                                                "mergedAt": None}))})
        res = evaluate_pin(_pin(_rc("github_pr_merged", repo="a/b", pr=2)), r)
        assert res.verdict == HOLDS

    def test_merged_pr_means_moved(self):
        r = _runner({"pr view": (0, json.dumps(
            {"state": "MERGED", "mergedAt": "2026-09-20T00:00:00Z"}))})
        res = evaluate_pin(_pin(_rc("github_pr_merged", repo="a/b", pr=2)), r)
        assert res.verdict == MOVED
        assert "MERGED" in res.predicates[0].detail

    def test_gh_failure_is_unknown_never_holds(self):
        """THE trap: 'we could not check' must not read as 'nothing changed'."""
        r = _runner({"pr view": (1, "")})
        res = evaluate_pin(_pin(_rc("github_pr_merged", repo="a/b", pr=2)), r)
        assert res.verdict == UNKNOWN
        assert "NOT evidence the pin holds" in res.predicates[0].detail

    def test_gh_timeout_is_unknown(self):
        r = _runner({"pr view": (124, "")})
        res = evaluate_pin(_pin(_rc("github_pr_merged", repo="a/b", pr=2)), r)
        assert res.verdict == UNKNOWN
        assert "timed out" in res.predicates[0].detail

    def test_gh_missing_is_unknown_not_a_crash(self):
        r = _runner({"pr view": (255, "No such file or directory")})
        res = evaluate_pin(_pin(_rc("github_pr_merged", repo="a/b", pr=2)), r)
        assert res.verdict == UNKNOWN

    def test_malformed_predicate_is_unknown(self):
        res = evaluate_pin(_pin(_rc("github_pr_merged")), _runner({}))
        assert res.verdict == UNKNOWN


class TestReleaseStable:
    def test_prerelease_is_not_a_release(self):
        """2026-09-11: the only 'newer' meshtasticd was an ALPHA whose
        predecessor had been revoked. An alpha must not read as movement."""
        r = _runner({"release list": (0, REL_LIST)})
        res = evaluate_pin(_pin(_rc("github_release_stable", repo="a/b",
                                    newer_than="2.7.26.54e0d8d")), r)
        assert res.verdict == HOLDS
        assert "still" in res.predicates[0].detail

    def test_a_newer_stable_release_is_moved(self):
        newer = json.dumps([{"tagName": "v2.9.0", "isPrerelease": False,
                             "isDraft": False,
                             "publishedAt": "2026-10-01T00:00:00Z"}])
        r = _runner({"release list": (0, newer)})
        res = evaluate_pin(_pin(_rc("github_release_stable", repo="a/b",
                                    newer_than="2.7.26.54e0d8d")), r)
        assert res.verdict == MOVED

    def test_drafts_are_excluded(self):
        drafts = json.dumps([{"tagName": "v9.9", "isPrerelease": False,
                              "isDraft": True, "publishedAt": "2026-10-01Z"}])
        r = _runner({"release list": (0, drafts)})
        res = evaluate_pin(_pin(_rc("github_release_stable", repo="a/b",
                                    newer_than="2.7.26")), r)
        assert res.verdict == UNKNOWN
        assert "no non-prerelease" in res.predicates[0].detail

    def test_unreachable_release_list_is_unknown(self):
        r = _runner({"release list": (1, "")})
        res = evaluate_pin(_pin(_rc("github_release_stable", repo="a/b",
                                    newer_than="2.7.26")), r)
        assert res.verdict == UNKNOWN


class TestSourceContains:
    def _content(self, text):
        return (0, base64.b64encode(text.encode()).decode())

    def test_needle_still_present_means_holds(self):
        r = _runner({"release list": (0, REL_LIST),
                     "contents": self._content("pins archive/03bf505d.zip")})
        res = evaluate_pin(_pin(_rc(
            "source_contains", repo="a/b", ref_from="latest_stable_release",
            path="p.ini", needle="03bf505d", expect="present")), r)
        assert res.verdict == HOLDS

    def test_needle_gone_means_moved_and_says_read_the_source(self):
        """The pin's premise changed. The detail must push the reader at the
        SOURCE, because 'newer' told us nothing on 2026-09-11."""
        r = _runner({"release list": (0, REL_LIST),
                     "contents": self._content("pins archive/deadbeef.zip")})
        res = evaluate_pin(_pin(_rc(
            "source_contains", repo="a/b", ref_from="latest_stable_release",
            path="p.ini", needle="03bf505d", expect="present")), r)
        assert res.verdict == MOVED
        assert "READ THE SOURCE" in res.predicates[0].detail

    def test_expect_absent_is_honoured(self):
        r = _runner({"release list": (0, REL_LIST),
                     "contents": self._content("nothing here")})
        res = evaluate_pin(_pin(_rc(
            "source_contains", repo="a/b", ref_from="latest_stable_release",
            path="p.ini", needle="pthread_detach", expect="absent")), r)
        assert res.verdict == HOLDS

    def test_unresolvable_ref_is_unknown_not_holds(self):
        r = _runner({"release list": (1, "")})
        res = evaluate_pin(_pin(_rc(
            "source_contains", repo="a/b", ref_from="latest_stable_release",
            path="p.ini", needle="x")), r)
        assert res.verdict == UNKNOWN
        assert "NOT evidence the pin holds" in res.predicates[0].detail

    def test_unfetchable_file_is_unknown(self):
        r = _runner({"release list": (0, REL_LIST), "contents": (1, "")})
        res = evaluate_pin(_pin(_rc(
            "source_contains", repo="a/b", ref_from="latest_stable_release",
            path="p.ini", needle="x")), r)
        assert res.verdict == UNKNOWN


class TestManual:
    def test_manual_is_reported_not_passed(self):
        """A manual-only pin reports unwatched EVERY render, because it is."""
        res = evaluate_pin(_pin(_rc("manual")), _runner({}))
        assert res.verdict == MANUAL
        assert "human act" in res.predicates[0].detail

    def test_unknown_evaluator_kind_is_unknown(self):
        res = evaluate_pin(_pin(Recheck(kind="vibes", means="m", raw={})),
                           _runner({}))
        assert res.verdict == UNKNOWN


class TestFold:
    def test_moved_beats_holds(self):
        """One changed condition is reason to look; a pile of HOLDS must not
        outvote it."""
        from utils.pin_recheck import PredicateResult as P
        assert fold([P("a", HOLDS, ""), P("b", MOVED, ""),
                     P("c", HOLDS, "")]) == MOVED

    def test_unknown_beats_holds(self):
        """A pin we could not fully check has not been checked."""
        from utils.pin_recheck import PredicateResult as P
        assert fold([P("a", HOLDS, ""), P("b", UNKNOWN, "")]) == UNKNOWN

    def test_moved_beats_unknown(self):
        from utils.pin_recheck import PredicateResult as P
        assert fold([P("a", UNKNOWN, ""), P("b", MOVED, "")]) == MOVED

    def test_manual_only_stands_alone(self):
        from utils.pin_recheck import PredicateResult as P
        assert fold([P("a", MANUAL, "")]) == MANUAL
        assert fold([P("a", MANUAL, ""), P("b", HOLDS, "")]) == HOLDS

    def test_no_predicates_is_unknown_not_holds(self):
        assert fold([]) == UNKNOWN

    def test_unknown_legs_are_listed_for_surfacing(self):
        """Surface the blind spot, do not average it away."""
        r = _runner({"pr view": (0, json.dumps({"state": "OPEN",
                                                "mergedAt": None})),
                     "release list": (1, "")})
        res = evaluate_pin(_pin(_rc("github_pr_merged", repo="a/b", pr=2),
                                _rc("github_release_stable", repo="a/b",
                                    newer_than="1.0")), r)
        assert res.verdict == UNKNOWN
        assert len(res.unknown_legs) == 1
