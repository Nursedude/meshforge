"""Tests for src/utils/fleet_platform.py — the update surface's SSOT.

The surface is read-only, so its whole value is the QUALITY of its
distinctions. These tests exist mostly to pin the three-way split that this
fleet learned the hard way between 08-05 and 09-02:

    inert   = deviates BY DECISION      -> not a fault, do not nag
    drift   = deviates, nobody decided  -> a finding
    unknown = could not tell            -> never a pass

Collapsing any pair produces either a surface people learn to ignore, or one
that reports a decided box as broken.
"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.fleet_platform import (  # noqa: E402
    DRIFT, INERT, OK, UNKNOWN, Catalog, Declaration, DistroBase,
    judge_box, load_catalog, load_declarations, summarize,
)

CATALOG_YAML = """
version: 0
platform:
  distro_bases:
    trixie:   {tier: target,     python: "3.13"}
    bookworm: {tier: supported,  python: "3.11"}
    noble:    {tier: deprecated, python: "3.12"}
  support_floor_python: "3.9"
pins:
  meshtasticd:
    mechanism: apt_hold
    current: "2.7.26"
    why: "upstream regression"
    recheck:
      - {kind: github_pr_merged, repo: x/y, pr: 2, means: "fix landed"}
  rns:
    mechanism: fork_pin
    current: "1.3.8+mf.0"
    why: "six issues collapsed into one fork"
    recheck:
      - {kind: manual, means: "merge + parity + canary"}
"""


def _catalog(tmp_path, text=CATALOG_YAML):
    p = tmp_path / "fleet_platform.yaml"
    p.write_text(text)
    return str(p)


def _cat():
    """A hand-built catalog, so judge tests do not depend on YAML."""
    return Catalog(bases={
        "trixie": DistroBase("trixie", "target", "3.13"),
        "bookworm": DistroBase("bookworm", "supported", "3.11"),
        "noble": DistroBase("noble", "deprecated", "3.12"),
    })


class TestCatalog:
    def test_valid_catalog_loads(self, tmp_path):
        cat, errs = load_catalog(_catalog(tmp_path))
        assert errs == []
        assert cat.target_bases() == ["trixie"]
        assert cat.support_floor_python == "3.9"
        assert set(cat.pins) == {"meshtasticd", "rns"}

    def test_missing_file_is_none_plus_errors(self, tmp_path):
        cat, errs = load_catalog(str(tmp_path / "absent.yaml"))
        assert cat is None and errs

    def test_zero_bases_is_an_error_not_an_empty_baseline(self, tmp_path):
        """An empty baseline would read 'every base is fine' fleet-wide."""
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases: {}
"""))
        assert cat is None and errs

    def test_no_target_tier_is_an_error(self, tmp_path):
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases:
    bookworm: {tier: supported}
"""))
        assert cat is None
        assert any("target" in e for e in errs)

    def test_pin_without_why_is_refused(self, tmp_path):
        """A hold with no reason is how a hold outlives its reason."""
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases:
    trixie: {tier: target}
pins:
  thing: {mechanism: apt_hold, current: "1.0",
          recheck: [{kind: manual, means: m}]}
"""))
        assert cat is None
        assert any("why" in e for e in errs)

    def test_pin_without_recheck_is_refused(self, tmp_path):
        """Silence would imply the pin is watched when nothing watches it —
        'manual' must be stated, not omitted."""
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases:
    trixie: {tier: target}
pins:
  thing: {mechanism: apt_hold, current: "1.0", why: w}
"""))
        assert cat is None
        assert any("recheck" in e for e in errs)

    def test_unknown_recheck_kind_is_refused(self, tmp_path):
        cat, errs = load_catalog(_catalog(tmp_path, """
platform:
  distro_bases:
    trixie: {tier: target}
pins:
  thing: {mechanism: apt_hold, current: "1.0", why: w,
          recheck: [{kind: vibes}]}
"""))
        assert cat is None

    def test_self_monitoring_distinguishes_manual_only_pins(self, tmp_path):
        cat, _ = load_catalog(_catalog(tmp_path))
        assert cat.pins["meshtasticd"].self_monitoring is True
        assert cat.pins["rns"].self_monitoring is False


class TestDeclarations:
    def test_absent_file_is_undeclared_not_an_error(self, tmp_path):
        decls, status = load_declarations(str(tmp_path / "none.json"))
        assert decls == {} and status == "undeclared"

    def test_corrupt_file_is_invalid_never_empty(self, tmp_path):
        """'invalid' must not read as 'no deviations declared' — that would
        turn every deliberate box into drift in one bad write."""
        p = tmp_path / "d.json"
        p.write_text("{not json")
        decls, status = load_declarations(str(p))
        assert decls == {} and status == "invalid"

    def test_declaration_without_reason_is_dropped(self, tmp_path):
        p = tmp_path / "d.json"
        p.write_text(json.dumps({"boxes": {"b": {"base": "bookworm"}}}))
        decls, status = load_declarations(str(p))
        assert status == "declared" and decls == {}

    def test_valid_declaration_loads(self, tmp_path):
        p = tmp_path / "d.json"
        p.write_text(json.dumps({"boxes": {"moc4": {
            "base": "bookworm", "reason": "standalone-compat canary",
            "reviewed": "2026-09-11"}}}))
        decls, status = load_declarations(str(p))
        assert status == "declared"
        assert decls["moc4"].reason == "standalone-compat canary"


class TestStaleness:
    def test_recent_review_is_fresh(self):
        today = time.strftime("%Y-%m-%d")
        assert Declaration("b", "bookworm", "r", today).stale() is False

    def test_missing_review_date_is_stale_not_fresh(self):
        """A missing review date is not evidence of a recent review."""
        assert Declaration("b", "bookworm", "r", None).stale() is True

    def test_old_review_goes_stale(self):
        assert Declaration("b", "bookworm", "r", "2020-01-01").stale() is True


class TestJudge:
    def test_target_base_is_ok(self):
        v = judge_box("moc", "trixie", _cat(), {})
        assert v.verdict == OK

    def test_undeclared_deviation_is_drift(self):
        v = judge_box("moc4", "bookworm", _cat(), {})
        assert v.verdict == DRIFT

    def test_declared_deviation_is_inert_not_drift(self):
        """THE distinction. A decided box must not nag."""
        decls = {"moc4": Declaration("moc4", "bookworm", "standalone canary",
                                     time.strftime("%Y-%m-%d"))}
        v = judge_box("moc4", "bookworm", _cat(), decls)
        assert v.verdict == INERT
        assert "standalone canary" in v.detail
        assert v.stale_declaration is False

    def test_declared_but_unreviewed_is_still_inert_yet_flagged(self):
        """The deviation stays inert; the DECISION ages visibly. An indefinite
        declaration is how a call becomes its own warrant."""
        decls = {"moc4": Declaration("moc4", "bookworm", "canary", "2019-01-01")}
        v = judge_box("moc4", "bookworm", _cat(), decls)
        assert v.verdict == INERT and v.stale_declaration is True

    def test_declaration_naming_a_different_base_is_drift(self):
        """One of the two moved and nobody noticed — that IS the finding."""
        decls = {"moc4": Declaration("moc4", "bookworm", "canary", "2026-09-11")}
        v = judge_box("moc4", "trixie2", _cat(), decls)
        assert v.verdict == UNKNOWN  # uncatalogued base short-circuits first
        decls2 = {"moc5": Declaration("moc5", "bookworm", "canary", "2026-09-11")}
        v2 = judge_box("moc5", "noble", _cat(), decls2)
        assert v2.verdict == DRIFT
        assert "disagree" in v2.detail

    def test_declaring_a_deprecated_base_does_not_settle_it(self):
        """A declaration cannot un-deprecate a base — noble is retirement
        work, and calling it inert would park it forever."""
        decls = {"moc5": Declaration("moc5", "noble", "was experimenting",
                                     time.strftime("%Y-%m-%d"))}
        v = judge_box("moc5", "noble", _cat(), decls)
        assert v.verdict == DRIFT
        assert "DEPRECATED" in v.detail

    def test_unobserved_is_unknown_never_ok(self):
        v = judge_box("kiai", None, _cat(), {})
        assert v.verdict == UNKNOWN
        assert "not healthy" in v.detail

    def test_unobserved_names_the_failing_leg(self):
        """An UNKNOWN that cannot say WHY costs its reader a debugging
        session. 2026-09-11: moc3 read 'not observed' while up and answering,
        because a 24s apt simulation blew a 25s timeout — that is a bug in the
        instrument, not news about the box."""
        v = judge_box("moc3", None, _cat(), {},
                      unobserved_why="probe timed out after 25s — OUR limit")
        assert v.verdict == UNKNOWN
        assert "OUR limit" in v.detail

    def test_unobserved_without_a_reason_says_so(self):
        """Silence about the reason must itself be visible, not blank."""
        v = judge_box("x", None, _cat(), {})
        assert "no reason recorded" in v.detail

    def test_uncatalogued_base_is_unknown_never_ok(self):
        v = judge_box("x", "plan9", _cat(), {})
        assert v.verdict == UNKNOWN
        assert "not in the catalog" in v.detail

    def test_unreadable_declarations_make_deviations_unknown_not_drift(self):
        """Cannot read the decisions => cannot tell deliberate from drift.
        Refusing to guess beats reporting a decided box as broken."""
        v = judge_box("moc4", "bookworm", _cat(), {}, decl_status="unreadable")
        assert v.verdict == UNKNOWN
        assert "cannot tell" in v.detail

    def test_unreadable_declarations_do_not_taint_target_boxes(self):
        """A box on the target base needs no declaration, so a broken
        declarations file must not make the whole fleet unknown."""
        v = judge_box("moc", "trixie", _cat(), {}, decl_status="unreadable")
        assert v.verdict == OK


class TestSummarize:
    def test_counts_every_verdict_class(self):
        vs = [judge_box("a", "trixie", _cat(), {}),
              judge_box("b", "noble", _cat(), {}),
              judge_box("c", None, _cat(), {})]
        s = summarize(vs)
        assert s[OK] == 1 and s[DRIFT] == 1 and s[UNKNOWN] == 1
        assert s[INERT] == 0
