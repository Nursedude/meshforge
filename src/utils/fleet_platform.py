"""Fleet platform posture — the SSOT behind the TUI's update surface.

WHAT THIS IS (2026-09-11)
-------------------------
A READ-ONLY model of two questions the fleet could not previously answer
without a session:

  1. Is this box's OS base what we intend, deliberately different, or drifting?
  2. For each pinned dependency: what do we hold, why, and what would change
     the answer?

Nothing here upgrades, installs, holds, or writes anything. The TUI renders it
as a surface; acting stays in scripts the operator runs knowingly. That split
is deliberate — an update path with fleet-wide blast radius behind a menu item
is the 2026-07-24 shape (a "restart every installed unit" sweep started a
service that was off BY DESIGN).

THE THREE-WAY DISTINCTION IS THE WHOLE POINT
--------------------------------------------
``inert`` and ``drift`` are different claims, and collapsing them is the
failure this fleet spent 08-05 through 09-02 unlearning. A box on bookworm
BY DECISION (the standalone-compat canary) must read ``inert``; a box on
bookworm because nobody looked must read ``drift``. A surface that nags about
a decision already made trains its reader to ignore it.

And ``unknown`` is neither. A box we could not observe is UNKNOWN, never OK —
unobservable is not healthy.

DECLARATIONS AGE, THEY DO NOT EXPIRE
------------------------------------
Unlike fleet_posture's dormancy (time-boxed, MANDATORY ``until``), a platform
deviation is an architectural decision, not a temporary state — forcing an
expiry would manufacture churn. But an indefinite declaration is exactly how a
call becomes policy and the policy becomes its own warrant (the harness_restraint
lesson). So a declaration carries ``reviewed``, and goes STALE after
``REVIEW_TTL_S`` — the deviation stays inert, the DECISION becomes visibly old.

Files:
  catalog      docs/fleet_platform.yaml                       (committed, generic)
  declarations ~/.config/meshforge/fleet_platform_declared.json (instance, MF014)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DECLARED_BASENAME = "fleet_platform_declared.json"
DECLARED_ENV = "MESHFORGE_FLEET_PLATFORM_DECLARED"
CATALOG_ENV = "MESHFORGE_FLEET_PLATFORM_CATALOG"

# How long a deviation decision stands before the surface says "nobody has
# looked at this in a while". Not an expiry: the box stays inert.
REVIEW_TTL_S = 180 * 86400          # ~6 months

# Support tiers, closed vocabulary — consumers switch on these.
TIER_TARGET = "target"
TIER_SUPPORTED = "supported"
TIER_DEPRECATED = "deprecated"
TIERS = (TIER_TARGET, TIER_SUPPORTED, TIER_DEPRECATED)

# Verdicts, closed vocabulary.
OK = "ok"                  # on the target base
INERT = "inert"            # deviates, and the deviation is DECLARED — not a fault
DRIFT = "drift"            # deviates with no standing declaration, or is deprecated
UNKNOWN = "unknown"        # not observed, or the base is not in the catalog
VERDICTS = (OK, INERT, DRIFT, UNKNOWN)

# recheck predicate kinds, closed vocabulary.
RECHECK_KINDS = ("github_pr_merged", "github_release_stable",
                 "source_contains", "manual")

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------

@dataclass
class DistroBase:
    name: str
    tier: str
    python: Optional[str] = None
    note: str = ""


@dataclass
class Recheck:
    kind: str
    means: str = ""
    raw: Dict = field(default_factory=dict)


@dataclass
class Pin:
    name: str
    mechanism: str
    current: str
    why: str = ""
    verified_at: str = ""
    blast_radius: str = ""
    recheck: List[Recheck] = field(default_factory=list)

    @property
    def self_monitoring(self) -> bool:
        """True when at least one predicate is machine-checkable.

        A pin whose only predicate is ``manual`` is NOT self-monitoring, and
        the surface must say so rather than let its presence in this file
        imply something is watching it.
        """
        return any(r.kind != "manual" for r in self.recheck)


@dataclass
class Catalog:
    bases: Dict[str, DistroBase] = field(default_factory=dict)
    pins: Dict[str, Pin] = field(default_factory=dict)
    support_floor_python: Optional[str] = None

    def target_bases(self) -> List[str]:
        return sorted(n for n, b in self.bases.items() if b.tier == TIER_TARGET)


def catalog_path() -> Path:
    env = os.environ.get(CATALOG_ENV)
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "docs" / "fleet_platform.yaml"


def load_catalog(path: Optional[str] = None) -> Tuple[Optional[Catalog], List[str]]:
    """(catalog, []) or (None, errors). Never error -> empty catalog: an empty
    baseline would read "nothing is expected, all fine" for the whole fleet.
    """
    p = Path(path) if path else catalog_path()
    try:
        import yaml
    except ImportError:
        return None, ["PyYAML not installed — cannot read the platform catalog"]
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"platform catalog not found: {p}"]
    except (OSError, ValueError) as e:
        return None, [f"platform catalog unreadable: {p}: {e}"]
    if not isinstance(data, dict):
        return None, [f"{p}: top level must be a mapping"]

    errors: List[str] = []
    plat = data.get("platform")
    if not isinstance(plat, dict):
        return None, [f"{p}: needs a top-level 'platform' mapping"]
    raw_bases = plat.get("distro_bases")
    if not isinstance(raw_bases, dict) or not raw_bases:
        # Zero bases cannot mean "every base is fine" (honest_failure_modes #3).
        return None, [f"{p}: 'platform.distro_bases' must be a non-empty mapping"]

    bases: Dict[str, DistroBase] = {}
    for name, raw in raw_bases.items():
        if not isinstance(raw, dict):
            errors.append(f"distro_bases[{name!r}]: must be a mapping")
            continue
        tier = raw.get("tier")
        if tier not in TIERS:
            errors.append(f"distro_bases[{name!r}]: tier {tier!r} not in {list(TIERS)}")
            continue
        bases[str(name)] = DistroBase(name=str(name), tier=tier,
                                      python=raw.get("python"),
                                      note=str(raw.get("note", "")).strip())
    if not any(b.tier == TIER_TARGET for b in bases.values()):
        errors.append("no distro base is tier 'target' — the fleet has nothing "
                      "to converge on, which cannot be intended")

    pins: Dict[str, Pin] = {}
    for name, raw in (data.get("pins") or {}).items():
        if not isinstance(raw, dict):
            errors.append(f"pins[{name!r}]: must be a mapping")
            continue
        missing = [k for k in ("mechanism", "current", "why") if not raw.get(k)]
        if missing:
            # A pin without a WHY is how a hold outlives its reason.
            errors.append(f"pins[{name!r}]: missing {missing}")
            continue
        rechecks: List[Recheck] = []
        for r in (raw.get("recheck") or []):
            if not isinstance(r, dict) or r.get("kind") not in RECHECK_KINDS:
                errors.append(f"pins[{name!r}]: recheck kind "
                              f"{(r or {}).get('kind')!r} not in {list(RECHECK_KINDS)}")
                continue
            rechecks.append(Recheck(kind=r["kind"],
                                    means=str(r.get("means", "")).strip(), raw=r))
        if not rechecks:
            # Silence here would imply the pin is watched when it is not.
            errors.append(f"pins[{name!r}]: no recheck predicate — declare "
                          f"kind 'manual' explicitly rather than leaving it out")
            continue
        pins[str(name)] = Pin(name=str(name), mechanism=str(raw["mechanism"]),
                              current=str(raw["current"]),
                              why=str(raw.get("why", "")).strip(),
                              verified_at=str(raw.get("verified_at", "")),
                              blast_radius=str(raw.get("blast_radius", "")).strip(),
                              recheck=rechecks)

    if errors:
        return None, errors
    return Catalog(bases=bases, pins=pins,
                   support_floor_python=plat.get("support_floor_python")), []


# ---------------------------------------------------------------------------
# declarations (instance values, never committed)
# ---------------------------------------------------------------------------

@dataclass
class Declaration:
    box: str
    base: str                     # the base this box is deliberately on
    reason: str
    reviewed: Optional[str] = None   # YYYY-MM-DD

    def stale(self, now: Optional[float] = None,
              ttl_s: int = REVIEW_TTL_S) -> bool:
        """True when nobody has re-affirmed this decision inside the TTL.

        An unparseable or absent ``reviewed`` counts as STALE, not as fresh —
        a missing review date is not evidence of a recent review.
        """
        if not self.reviewed or not _ISO_RE.match(self.reviewed):
            return True
        try:
            t = time.mktime(time.strptime(self.reviewed, "%Y-%m-%d"))
        except ValueError:
            return True
        return (now if now is not None else time.time()) - t > ttl_s


def declared_path(home: Optional[str] = None) -> Path:
    env = os.environ.get(DECLARED_ENV)
    if env:
        return Path(env)
    if home:
        return Path(home) / ".config" / "meshforge" / DECLARED_BASENAME
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".config" / "meshforge" / DECLARED_BASENAME


def load_declarations(path: Optional[str] = None
                      ) -> Tuple[Dict[str, Declaration], str]:
    """(declarations, status). status is one of declared/undeclared/unreadable/
    invalid — an unreadable file must NEVER read as "no deviations declared",
    which would turn every deliberate box into drift.
    """
    p = Path(path) if path else declared_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "undeclared"
    except OSError:
        return {}, "unreadable"
    except ValueError:
        return {}, "invalid"
    if not isinstance(data, dict) or not isinstance(data.get("boxes"), dict):
        return {}, "invalid"
    out: Dict[str, Declaration] = {}
    for box, raw in data["boxes"].items():
        if not isinstance(raw, dict) or not raw.get("base") or not raw.get("reason"):
            # A declaration without a reason cannot justify anything.
            continue
        out[str(box)] = Declaration(box=str(box), base=str(raw["base"]),
                                    reason=str(raw["reason"]),
                                    reviewed=raw.get("reviewed"))
    return out, "declared"


# ---------------------------------------------------------------------------
# the pure judgement
# ---------------------------------------------------------------------------

@dataclass
class BoxVerdict:
    box: str
    verdict: str
    actual_base: Optional[str]
    actual_python: Optional[str] = None
    detail: str = ""
    stale_declaration: bool = False


def judge_box(box: str, actual_base: Optional[str], catalog: Catalog,
              declarations: Dict[str, Declaration], *,
              actual_python: Optional[str] = None,
              decl_status: str = "declared",
              unobserved_why: Optional[str] = None,
              now: Optional[float] = None) -> BoxVerdict:
    """Pure reduction of one box's observed base to a verdict.

    Order matters: unobserved and uncatalogued both land in UNKNOWN before any
    OK/DRIFT call is made, because both mean "we cannot say", and "we cannot
    say" is not a pass.
    """
    def v(verdict, detail, stale=False):
        return BoxVerdict(box=box, verdict=verdict, actual_base=actual_base,
                          actual_python=actual_python, detail=detail,
                          stale_declaration=stale)

    if not actual_base:
        # Naming the failing leg is not a nicety. An UNKNOWN that cannot say
        # WHY costs its reader a debugging session to distinguish "box is
        # down" from "our probe timed out" — and the second is a bug in the
        # instrument, not news about the box (2026-09-11: moc3 read
        # "not observed" while up and answering, because a 24s apt simulation
        # blew a 25s timeout).
        why = unobserved_why or "no reason recorded — the probe did not say"
        return v(UNKNOWN, f"not observed ({why}) — state unknown, not healthy")
    base = catalog.bases.get(actual_base)
    if base is None:
        return v(UNKNOWN, f"base {actual_base!r} is not in the catalog — "
                          f"unrecognised is not 'fine'; add it with a tier")

    if base.tier == TIER_TARGET:
        return v(OK, f"on the target base ({actual_base})")

    if decl_status in ("unreadable", "invalid"):
        # Cannot read the declarations => cannot know whether this deviation is
        # deliberate. Refusing to guess beats calling a decided box drifted.
        return v(UNKNOWN, f"deviates ({actual_base}, tier={base.tier}) and the "
                          f"declarations file is {decl_status} — cannot tell "
                          f"deliberate from drift")

    decl = declarations.get(box)
    if decl is None:
        return v(DRIFT, f"on {actual_base} (tier={base.tier}) with no declaration"
                        + (" — this base is deprecated" if base.tier == TIER_DEPRECATED
                           else ""))
    if decl.base != actual_base:
        # A declaration that names a base the box is not on is itself the
        # finding: one of the two moved and nobody noticed.
        return v(DRIFT, f"declared {decl.base!r} but is actually on "
                        f"{actual_base!r} — the declaration and the box disagree")
    if base.tier == TIER_DEPRECATED:
        # Declaring a deprecated base does not make it fine; it makes it a
        # KNOWN retirement, which is still work outstanding.
        return v(DRIFT, f"declared ({decl.reason}) but {actual_base} is "
                        f"DEPRECATED — a declaration cannot un-deprecate a base; "
                        f"this is retirement work, not a settled decision",
                 stale=decl.stale(now))
    return v(INERT, f"deliberate: {decl.reason}", stale=decl.stale(now))


def summarize(verdicts: List[BoxVerdict]) -> Dict[str, int]:
    out = {k: 0 for k in VERDICTS}
    for v in verdicts:
        out[v.verdict] = out.get(v.verdict, 0) + 1
    return out
