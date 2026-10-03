"""The truth-path type gate: utils.observation is CHECKED, not just written.

ADR `.claude/plans/adr_truth_kernel_2026_10_02.md`: a Seen / Unobservable /
Failed type would likely have prevented 23% of measured incidents — but only if
a missing variant is REFUSED. mypy ran nowhere in this repo (not CI, not the
hooks) when the type landed, so "enforced by the type checker" would have been
a false claim. This file is the enforcement:

* every module that imports utils.observation is in TRUTH_PATH, and TRUTH_PATH
  type-checks clean — a new adopter cannot quietly stay unchecked;
* a planted non-exhaustive match is REJECTED — the gate can fail;
* in CI a missing mypy FAILS, never skips (a gate that skips where it matters
  is the one-outcome instrument feedback_a_guard_that_never_failed warns about).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from utils.observation import (  # noqa: E402
    Failed, Seen, Unobservable, describe)

#: Modules on the truth path that use utils.observation. Closed list: the
#: adopter-coverage test below fails if a module imports the type and is not here.
TRUTH_PATH = [
    "src/utils/observation.py",
    "scripts/gen_fleet_hosts.py",
    "src/utils/rnsd_restart_order.py",
    "src/utils/claw_watch_verdict.py",
    "src/mini_dudeai/claw_rf_watch.py",
    "src/utils/watchdog_probes_claw_watch.py",
    "src/monitoring/traffic_pulse.py",
]


def _mypy_available() -> bool:
    return subprocess.run([sys.executable, "-m", "mypy", "--version"],
                          capture_output=True, timeout=60).returncode == 0


@pytest.fixture(scope="module")
def mypy_run(tmp_path_factory):
    if not _mypy_available():
        if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
            pytest.fail("mypy is not installed in CI — the truth-path type gate "
                        "would be prose. requirements/ci.txt must pin mypy.")
        pytest.skip("mypy not installed on this box (CI enforces it)")
    cache = tmp_path_factory.mktemp("mypy_cache")

    def run(*files):
        return subprocess.run(
            [sys.executable, "-m", "mypy", "--config-file", str(ROOT / "mypy.ini"),
             "--follow-imports=silent", "--cache-dir", str(cache),
             # An UNANNOTATED consumer is not checked at all by default (the
             # argument is Any), so the exhaustiveness claim would be false
             # for this repo's house style (review C1, measured). The truth
             # path must be fully annotated; possibly-undefined catches a
             # value carried across arms (review C2).
             "--disallow-untyped-defs", "--enable-error-code", "possibly-undefined",
             *files],
            capture_output=True, text=True, timeout=300, cwd=ROOT)
    return run


def test_truth_path_type_checks_clean(mypy_run):
    r = mypy_run(*TRUTH_PATH)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_missing_variant_is_rejected(mypy_run, tmp_path):
    """Plant the defect: a consumer that forgets Failed. mypy must refuse it."""
    bad = tmp_path / "forgets_failed.py"
    bad.write_text(
        "from utils.observation import Observation, Seen, Unobservable, assert_never\n"
        "def use(o: Observation[int]) -> int:\n"
        "    match o:\n"
        "        case Seen(value=v):\n"
        "            return v\n"
        "        case Unobservable():\n"
        "            return 0\n"
        "        case _ as unreachable:\n"
        "            assert_never(unreachable)\n")
    r = mypy_run(str(bad))
    assert r.returncode != 0 and "assert_never" in r.stdout, r.stdout


def test_an_untyped_consumer_is_rejected(mypy_run, tmp_path):
    """`def use(o):` — the house style — must not slip through as Any."""
    bad = tmp_path / "untyped.py"
    bad.write_text(
        "from utils.observation import Seen, Unobservable, assert_never\n"
        "def use(o):\n"
        "    match o:\n"
        "        case Seen(value=v):\n"
        "            return v\n"
        "        case Unobservable():\n"
        "            return 0\n"
        "        case _ as unreachable:\n"
        "            assert_never(unreachable)\n")
    r = mypy_run(str(bad))
    assert r.returncode != 0 and "no-untyped-def" in r.stdout, r.stdout


def test_every_match_on_the_truth_path_ends_in_assert_never():
    """mypy accepts `case _: pass` — a catch-all that swallows a variant
    (review C3). On the truth path every match must close with assert_never."""
    for rel in TRUTH_PATH:
        src = (ROOT / rel).read_text()
        matches = len(re.findall(r"^\s+match \w[^\n]*:\s*$", src, re.M))
        closers = len(re.findall(r"assert_never\(", src))
        assert closers >= matches, (
            f"{rel}: {matches} match statement(s) but {closers} assert_never — "
            "close every match on an Observation with `case _ as u: assert_never(u)`")


def test_every_adopter_is_on_the_checked_list():
    pat = re.compile(r"utils\.observation\b|from\s+(src\.)?utils\s+import\s+[^\n]*\bobservation\b")
    adopters = set()
    for base in ("src", "scripts"):
        for p in (ROOT / base).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            try:
                if pat.search(p.read_text(errors="replace")):
                    adopters.add(str(p.relative_to(ROOT)))
            except OSError:
                continue
    missing = adopters - set(TRUTH_PATH)
    assert not missing, ("imports utils.observation but is not type-checked — "
                         f"add to TRUTH_PATH: {sorted(missing)}")


# ---- runtime refusals (hold in untyped code too) -------------------------

@pytest.mark.parametrize("obs", [Seen(None), Seen([]), Seen(0),
                                 Unobservable("no server answered"),
                                 Failed("malformed reply")])
def test_no_truthiness(obs):
    """`if obs:` / `obs or []` — the collapse this type exists to end."""
    with pytest.raises(TypeError):
        bool(obs)
    with pytest.raises(TypeError):
        _ = obs or []


@pytest.mark.parametrize("cls", [Unobservable, Failed])
@pytest.mark.parametrize("why", ["", "   ", None])
def test_blindness_needs_a_witness(cls, why):
    with pytest.raises(ValueError):
        cls(why)


def test_observed_absence_is_not_blindness():
    assert Seen(None) != Unobservable("x")
    assert describe(Seen(None)) == "seen: None"
    assert describe(Unobservable("dns down")).startswith("UNOBSERVABLE")
    assert describe(Failed("bad")).startswith("FAILED")


def test_ci_pins_mypy():
    assert re.search(r"^mypy==", (ROOT / "requirements" / "ci.txt").read_text(), re.M), \
        "requirements/ci.txt must pin mypy or the gate skips in CI"
