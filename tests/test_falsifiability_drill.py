"""The falsifiability drill's own verdict must show BOTH outcomes.

An instrument with only one outcome is not done (operator, 2026-08-10). The
drill's `--fail-on-survivor` gate had only ever been seen PASSING (58/58,
9/9, 3/3 on 2026-09-02); this pins the classification that makes it FAIL.
"""
import importlib.util
from pathlib import Path

_p = Path(__file__).parent.parent / "scripts" / "falsifiability_drill.py"
_spec = importlib.util.spec_from_file_location("fdrill", _p)
fdrill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fdrill)

GREEN = {"rc": 0}


def _run(named, collateral):
    return {"failed": named + collateral, "named": named, "collateral": collateral}


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
