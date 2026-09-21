"""The falsifiability drill's own verdict must show BOTH outcomes.

An instrument with only one outcome is not done (operator, 2026-08-10). The
drill's `--fail-on-survivor` gate had only ever been seen PASSING (58/58,
9/9, 3/3 on 2026-09-02); this pins the classification that makes it FAIL.
"""
import ast
import importlib.util
from pathlib import Path

_p = Path(__file__).parent.parent / "scripts" / "falsifiability_drill.py"
_spec = importlib.util.spec_from_file_location("fdrill", _p)
fdrill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fdrill)

GREEN = {"rc": 0}


def _run(named, collateral, invalid="", passed=1, errors=()):
    return {"failed": named + collateral, "named": named, "collateral": collateral,
            "invalid": invalid, "passed": passed, "errors": list(errors)}


def test_dead_and_loud_both_named_is_caught_both():
    assert fdrill.verdict(GREEN, _run(["t::a"], []), _run(["t::b"], [])) == "caught-both"


def test_no_failure_at_all_is_survived():
    assert fdrill.verdict(GREEN, _run([], []), _run([], [])) == "SURVIVED"


def test_only_sibling_class_failures_is_collateral_only():
    assert fdrill.verdict(GREEN, _run([], ["t::sib"]), _run([], ["t::sib"])) == "collateral-only"


def test_one_polarity_only_is_named_as_such():
    assert fdrill.verdict(GREEN, _run(["t::a"], []), _run([], [])) == "caught-dead-only"
    assert fdrill.verdict(GREEN, _run([], []), _run(["t::a"], [])) == "caught-loud-only"


def test_red_baseline_invalidates_the_row():
    assert fdrill.verdict({"rc": 1}, _run(["t::a"], []), _run(["t::a"], [])) == "baseline-red"


# --- the mutant must be evidence before it is scored (2026-09-21) ----------
#
# `cron_verdict_stale` read `loud: 0n/0c` for a week and that was recorded as a
# finding about the test suite. It was not: the drill prepended its `Signal`
# import above `watchdog_probes_peer_cron.py`'s `from __future__` line, every
# importer died at COLLECTION, and zero FAILED lines were scored as "the suite
# noticed nothing". These pin both halves — the placement, and the refusal to
# score a mutant that never ran.

PROBE_FILES = sorted((Path(__file__).parent.parent / "src" / "utils")
                     .glob("watchdog_probe*.py"))


def test_the_corpus_this_guards_is_not_empty():
    """An instrument with only one outcome is not done — make the loop bite."""
    assert PROBE_FILES, "no probe files found; the placement test is vacuous"
    assert any("from __future__" in f.read_text() for f in PROBE_FILES), (
        "no probe file carries `from __future__`, so nothing here could ever "
        "fail — the regression this guards needs that construct to exist")


def test_the_loud_import_lands_somewhere_legal_in_every_probe_file():
    for f in PROBE_FILES:
        src = f.read_text()
        lines = src.splitlines(keepends=True)
        at = fdrill._import_line(ast.parse(src))
        mutated = "".join(lines[:at] + [fdrill.FDRILL_IMPORT] + lines[at:])
        try:
            # compile(), not ast.parse() — see _breaks_when_prepended.
            compile(mutated, str(f), "exec")
        except SyntaxError as e:  # pragma: no cover - the failure we guard
            raise AssertionError(
                f"{f.name}: loud mutant does not parse at line {at}: {e.msg}")


def test_prepending_at_line_zero_is_what_used_to_break_it():
    """The refuted approach, pinned so nobody 'simplifies' back to it."""
    offenders = [f.name for f in PROBE_FILES
                 if _breaks_when_prepended(f.read_text())]
    assert offenders, ("no probe file breaks under a line-0 prepend any more; "
                       "if that is genuinely true, this test has lost its "
                       "subject and the placement test above still stands")


def _breaks_when_prepended(src):
    """⚠️ `compile`, not `ast.parse`. `ast.parse` ACCEPTS an import above
    `from __future__` — only the compiler rejects it. Written with `ast.parse`
    first, this returned no offenders at all and would have shipped a validity
    check blind to the exact defect it guards."""
    try:
        compile(fdrill.FDRILL_IMPORT + src, "<prepended>", "exec")
    except SyntaxError:
        return True
    return False


def test_a_stillborn_mutant_is_not_a_finding_about_the_suite():
    dead = _run(["t::a"], [])
    stillborn = _run([], [], invalid="mutant does not parse — x.py:46: bad")
    assert fdrill.verdict(GREEN, dead, stillborn) == "MUTANT-INVALID"
    assert fdrill.verdict(GREEN, stillborn, dead) == "MUTANT-INVALID"


def test_mutant_invalid_has_three_independent_legs():
    base = {"passed": 100}
    ok = {"errors": [], "passed": 100}
    assert fdrill.mutant_invalid(base, ok, "") == ""
    assert "does not parse" in fdrill.mutant_invalid(base, ok, "f.py:1: bad")
    assert "collection error" in fdrill.mutant_invalid(
        base, {"errors": ["tests/t.py"], "passed": 0}, "")
    # The backstop: no log parsing, no syntax check — a run that measured
    # nothing cannot report an absence.
    assert "nothing was measured" in fdrill.mutant_invalid(
        base, {"errors": [], "passed": 0}, "")


def test_a_baseline_that_ran_nothing_does_not_invalidate_every_mutant():
    """`passed == 0` on BOTH sides is an empty selection, not a stillborn."""
    assert fdrill.mutant_invalid({"passed": 0}, {"errors": [], "passed": 0}, "") == ""


def test_the_loud_alias_is_never_named_without_being_imported():
    """The bug the plant caught: the stub body contains the alias, so an
    alias-presence guard inserts nothing and the mutant raises instead of
    firing — failing tests for a reason that is not the one being measured."""
    src = "from __future__ import annotations\nimport os\n"
    stub = src + f"def p():\n    return {fdrill.FDRILL_ALIAS}(cls='x')\n"
    out = fdrill._with_loud_import(stub)
    assert fdrill.FDRILL_IMPORT in out, "the alias is named but not imported"
    compile(out, "<t>", "exec")
    assert out.index("from __future__") < out.index(fdrill.FDRILL_IMPORT)


def test_an_existing_loud_import_is_not_duplicated():
    src = "import os\n" + fdrill.FDRILL_IMPORT + "def p():\n    return 1\n"
    assert fdrill._with_loud_import(src).count(fdrill.FDRILL_IMPORT) == 1
