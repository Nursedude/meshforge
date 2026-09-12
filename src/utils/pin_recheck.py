"""Pin re-check — evaluate the predicates declared in docs/fleet_platform.yaml.

WHAT THIS ANSWERS (2026-09-11)
------------------------------
A pin in the catalog says what we hold and why. Its ``recheck`` block says what
would CHANGE that answer. This module runs those predicates and reports, per
pin, whether its reason still stands.

READ-ONLY. It asks GitHub questions and prints verdicts. It does not unhold,
upgrade, install, or edit the catalog — deciding a pin has lifted is a human
act, and the whole point of writing the predicate down was to make that
decision cheap, not automatic.

THE TRAP THIS MODULE EXISTS TO AVOID
------------------------------------
"We could not check, so nothing has changed."

That is the defect class this codebase has paid for repeatedly (#80, and the
08-05 detector-blindness arc): a degraded observation rendered as a healthy
value. A predicate that could not be evaluated — no network, no ``gh``, rate
limited, repo moved — is UNKNOWN. It is never HOLDS. And a pin's summary must
SURFACE its unknowns rather than average them into a reassuring total
(calibrated_claims #5).

Verdict vocabulary, per predicate and per pin:
  holds    - the condition that would move this pin has NOT occurred
  moved    - it HAS: go look, the pin may no longer be justified
  unknown  - could not evaluate; says which leg failed
  manual   - no machine-checkable predicate exists, and that is DECLARED

``manual`` is deliberately not a pass. A pin whose only predicate is manual
reports as unwatched every single time it is rendered, because it is.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

HOLDS = "holds"
MOVED = "moved"
UNKNOWN = "unknown"
MANUAL = "manual"
RESULTS = (HOLDS, MOVED, UNKNOWN, MANUAL)

GH_TIMEOUT_S = 30


@dataclass
class PredicateResult:
    kind: str
    result: str
    detail: str
    means: str = ""


@dataclass
class PinResult:
    name: str
    verdict: str
    predicates: List[PredicateResult] = field(default_factory=list)

    @property
    def unknown_legs(self) -> List[PredicateResult]:
        return [p for p in self.predicates if p.result == UNKNOWN]


# ---------------------------------------------------------------------------
# the gh leg — injectable so every test runs offline
# ---------------------------------------------------------------------------

Runner = Callable[[List[str]], Tuple[int, str]]


def _run(argv: List[str]) -> Tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=GH_TIMEOUT_S)
        return p.returncode, (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError as e:
        return 255, str(e)


def _gh_json(runner: Runner, args: List[str]) -> Tuple[Optional[object], str]:
    """(parsed, "") or (None, why-unknown). Every failure names its leg."""
    rc, out = runner(["gh"] + args)
    if rc == 124:
        return None, f"gh timed out after {GH_TIMEOUT_S}s"
    if rc == 255:
        return None, f"could not run gh ({out[:80]})"
    if rc != 0:
        return None, f"gh rc={rc}"
    if not out:
        return None, "gh returned nothing"
    try:
        return json.loads(out), ""
    except ValueError:
        return None, "gh output was not JSON"


# ---------------------------------------------------------------------------
# predicate evaluators
# ---------------------------------------------------------------------------

def _eval_pr_merged(raw: Dict, runner: Runner) -> PredicateResult:
    repo, pr = raw.get("repo"), raw.get("pr")
    if not repo or pr is None:
        return PredicateResult(raw.get("kind", "?"), UNKNOWN,
                               "predicate is missing repo/pr")
    data, why = _gh_json(runner, ["pr", "view", str(pr), "--repo", str(repo),
                                  "--json", "state,mergedAt,title"])
    if data is None:
        return PredicateResult(raw["kind"], UNKNOWN,
                               f"{repo}#{pr}: {why} — NOT evidence the pin holds")
    if data.get("mergedAt"):
        return PredicateResult(raw["kind"], MOVED,
                               f"{repo}#{pr} MERGED {data['mergedAt']}")
    return PredicateResult(raw["kind"], HOLDS,
                           f"{repo}#{pr} still {data.get('state', '?').lower()}")


def _latest_stable(repo: str, runner: Runner) -> Tuple[Optional[str], str]:
    """Newest release that is neither prerelease nor draft. An alpha is not a
    release for our purposes — 2026-09-11, the only 'newer' meshtasticd was an
    alpha whose predecessor had been REVOKED."""
    data, why = _gh_json(runner, ["release", "list", "--repo", repo,
                                  "--limit", "30", "--json",
                                  "tagName,isPrerelease,isDraft,publishedAt"])
    if data is None:
        return None, why
    if not isinstance(data, list):
        return None, "release list was not a list"
    stable = [r for r in data
              if not r.get("isPrerelease") and not r.get("isDraft")]
    if not stable:
        return None, "no non-prerelease release in the newest 30"
    stable.sort(key=lambda r: r.get("publishedAt") or "", reverse=True)
    return stable[0].get("tagName"), ""


def _eval_release_stable(raw: Dict, runner: Runner) -> PredicateResult:
    repo = raw.get("repo")
    floor = str(raw.get("newer_than") or "")
    if not repo:
        return PredicateResult(raw.get("kind", "?"), UNKNOWN,
                               "predicate is missing repo")
    tag, why = _latest_stable(str(repo), runner)
    if tag is None:
        return PredicateResult(raw["kind"], UNKNOWN,
                               f"{repo}: {why} — NOT evidence the pin holds")
    # Substring, not a version parse: these tags carry build hashes
    # (v2.7.26.54e0d8d) that no ordering scheme handles honestly. Asking
    # "is the newest stable still the one we pinned?" is the question we can
    # actually answer; anything cleverer would be guessing.
    if floor and floor in tag:
        return PredicateResult(raw["kind"], HOLDS,
                               f"{repo} latest stable is still {tag}")
    return PredicateResult(raw["kind"], MOVED,
                           f"{repo} latest stable is {tag}, we pinned {floor}")


def _eval_source_contains(raw: Dict, runner: Runner) -> PredicateResult:
    repo, path = raw.get("repo"), raw.get("path")
    needle = raw.get("needle")
    expect = raw.get("expect", "present")
    if not (repo and path and needle):
        return PredicateResult(raw.get("kind", "?"), UNKNOWN,
                               "predicate is missing repo/path/needle")
    ref = raw.get("ref_from") or raw.get("ref") or "HEAD"
    if ref == "latest_stable_release":
        tag, why = _latest_stable(str(repo), runner)
        if tag is None:
            return PredicateResult(raw["kind"], UNKNOWN,
                                   f"{repo}: could not resolve latest stable "
                                   f"({why}) — NOT evidence the pin holds")
        ref = tag
    rc, out = runner(["gh", "api",
                      f"repos/{repo}/contents/{path}?ref={ref}",
                      "--jq", ".content"])
    if rc != 0 or not out:
        return PredicateResult(raw["kind"], UNKNOWN,
                               f"{repo}:{path}@{ref}: gh rc={rc} — NOT "
                               f"evidence the pin holds")
    import base64
    try:
        text = base64.b64decode(out).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return PredicateResult(raw["kind"], UNKNOWN,
                               f"{repo}:{path}@{ref}: content not decodable")
    present = str(needle) in text
    want_present = (expect != "absent")
    if present == want_present:
        return PredicateResult(raw["kind"], HOLDS,
                               f"{path}@{ref}: {needle!r} "
                               f"{'present' if present else 'absent'} as expected")
    return PredicateResult(raw["kind"], MOVED,
                           f"{path}@{ref}: {needle!r} is now "
                           f"{'present' if present else 'ABSENT'} — the pin's "
                           f"premise changed, READ THE SOURCE before believing "
                           f"any version string")


_EVALUATORS = {
    "github_pr_merged": _eval_pr_merged,
    "github_release_stable": _eval_release_stable,
    "source_contains": _eval_source_contains,
}


def evaluate_pin(pin, runner: Optional[Runner] = None) -> PinResult:
    """Run every predicate on one pin and fold them into a verdict."""
    r = runner or _run
    results: List[PredicateResult] = []
    for rc_ in pin.recheck:
        if rc_.kind == "manual":
            results.append(PredicateResult(
                "manual", MANUAL,
                "no machine-checkable predicate — re-evaluating this is a "
                "human act", rc_.means))
            continue
        fn = _EVALUATORS.get(rc_.kind)
        if fn is None:
            results.append(PredicateResult(rc_.kind, UNKNOWN,
                                           f"no evaluator for kind {rc_.kind!r}",
                                           rc_.means))
            continue
        out = fn(rc_.raw, r)
        out.means = rc_.means
        results.append(out)
    return PinResult(name=pin.name, verdict=fold(results), predicates=results)


def fold(results: List[PredicateResult]) -> str:
    """Pure reduction of predicate results to one pin verdict.

    MOVED wins over everything: one changed condition is reason to look, and
    letting a pile of HOLDS outvote it would be averaging away the finding.
    UNKNOWN then wins over HOLDS, for the same reason in the other direction —
    a pin we could not fully check has not been checked. MANUAL only stands
    alone; beside anything else the real results speak.
    """
    if not results:
        return UNKNOWN
    kinds = {r.result for r in results}
    if MOVED in kinds:
        return MOVED
    if UNKNOWN in kinds:
        return UNKNOWN
    if kinds == {MANUAL}:
        return MANUAL
    return HOLDS
