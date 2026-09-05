"""mini rule-exclusion OWNERSHIP solver — split out of watchdog_probes_mini.

Extracted 2026-09-05 for MF025 (the 1,500-line cap) when the peer-universe
cure pushed the parent past it. The cap's rule is that the baseline only
shrinks, so the answer is a split at a real seam, never a new baseline entry.

This is a coherent unit: deciding whether a rule's `subject_exclude_globs`
entry still has an owner. It is imported back into ``watchdog_probes_mini``,
which remains the only caller — import the probes from the
``utils.watchdog_probes`` hub as before.
"""
from __future__ import annotations

import fnmatch
from typing import Optional

# ─────────────────────────────────────────────────────────────────────
# mini_rule_orphaned_exclusion — ownership judged by the ENGINE, on real
# subjects (2026-09-03 frontier pass)
# ─────────────────────────────────────────────────────────────────────
# The first cut decided ownership with a private glob-core solver
# (`g.strip("*")` matched against any of three selector keys). Against
# `mini_dudeai.engine._match_rule` — the thing that actually decides what
# mini watches — it disagreed in five shapes: an owner filtering a DIFFERENT
# `class` extra was credited (false clean; 60 of this box's 68 live rules
# carry that filter), a rule with a missing subject_glob beside a matching
# peer_glob was credited although the engine reads subject_glob alone (false
# clean), and `subject_glob: ""` / an owner more specific than the core /
# a `?` in the glob each paged on an owned subject (false page). A checker
# that re-types the semantics it audits encodes only the narrowness its
# author already thought of; this one asks the engine.
#
# Sample subjects, in order of authority: subjects the box has actually
# recorded in mini's state.json (rule-state keys are ``rule_id::subject``
# and outlive the rule's retirement for STALE_KEY_RETENTION_S — exactly what
# makes a half-landed retirement judgeable on the real name), and only when
# none of those falls inside the exclusion, the glob's literal core as a
# synthetic subject. A glob with no literal core (`*`, `*moc?*`) and no
# observed subject in scope is UNJUDGEABLE, and says so — never a page,
# never an affirmative clean.

_MINI_GLOB_META = ("?", "[")


def _mini_observed_subjects(state: Optional[dict]) -> list:
    """Subject strings mini has recorded, from a state.json document."""
    out: list = []
    seen: set = set()
    for key, ent in ((state or {}).get("rules") or {}).items():
        s = ent.get("subject") if isinstance(ent, dict) else None
        if not isinstance(s, str) or not s:
            _rid, sep, tail = str(key).partition("::")
            s = tail if sep and tail else None
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _mini_peer_universe(observed: list, hosts: list) -> list:
    """Subject names for fleet peers this box has NEVER recorded.

    The 2026-09-05 pass measured a false CLEAN: ownership was decided from the
    subjects mini has OBSERVED, so a peer that exists but has never produced a
    condition was invisible, and an exclusion covering it read owned. Detection
    got weaker as state filled — an empty state paged where a populated one did
    not.

    The missing piece is the SUBJECT UNIVERSE. It cannot come from the globs
    (containment over an unbounded name space always finds a hole, which is the
    false PAGE the 09-03 pass cured), and bare host names are not subjects — an
    owner glob like ``peer-moc3`` does not match ``moc3``, so testing raw hosts
    re-pages that same cured case. So the naming convention is INFERRED from
    what the box has actually recorded: if an observed subject ends with a known
    host, the leading text is a real prefix in use here, and ``prefix + host``
    is a well-formed subject name for every other host.

    Returns ``[]`` — falling back to the structural core — whenever the
    convention cannot be inferred: no host list, no observed subjects, or a
    kind whose subjects are not peer-shaped (no observed subject ends with any
    host, so no prefix is learned). Absence of a universe is not evidence of
    coverage; it just returns the judgement to the older method, which says so.
    """
    if not hosts or not observed:
        return []
    prefixes = set()
    for s in observed:
        for h in hosts:
            if s == h:
                prefixes.add("")
            elif len(s) > len(h) and s.endswith(h):
                prefixes.add(s[:len(s) - len(h)])
    if not prefixes:
        return []
    seen = set(observed)
    out = []
    for p in sorted(prefixes):
        for h in sorted(hosts):
            cand = p + h
            if cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def _mini_judge_exclusion(rule: dict, glob: str, rules: list, observed: list,
                          match_rule, structural_keys, condition_cls,
                          universe: Optional[list] = None) -> tuple:
    """``(verdict, note)`` for one exclude glob of one rule.

    verdict ∈ ``"orphaned"`` (note names the witness subject), ``"owned"``
    (note names the method), ``"unjudgeable"`` (note says why).
    """
    m = rule.get("match") or {}
    kind = m.get("kind")
    extras = {k: v for k, v in m.items() if k not in structural_keys}
    # The rule MINUS its exclusions: "would this rule have watched s?"
    bare = {"match": {k: v for k, v in m.items()
                      if k != "subject_exclude_globs"}}

    def cond(s):
        return condition_cls(kind=kind, subject=s, extras=dict(extras))

    def in_scope(s):
        return bool(match_rule(bare, cond(s))) and fnmatch.fnmatchcase(s, glob)

    def owned(s):
        c = cond(s)
        return any(o is not rule and match_rule(o, c) for o in rules)

    core = glob.strip("*")
    if not core:
        # Excluding everything disables the rule outright — a different
        # pathology (a rule that matches nothing forever), named as such
        # rather than judged subject-by-subject.
        return "unjudgeable", "bare '*' — no owner is nameable for everything"
    scoped = [s for s in observed if in_scope(s)]
    if scoped:
        for s in scoped:
            if not owned(s):
                return "orphaned", f"observed subject {s!r}"
        # Every OBSERVED subject in scope is owned — which is NOT the same as
        # "this glob has no hole". A peer that exists but has never produced a
        # condition here is absent from the sample, so the universe of
        # well-formed peer names is judged too (see _mini_peer_universe).
        # Without this the probe got LESS sensitive as state filled: measured
        # 2026-09-05, excluder '*' excluding '*moc*' with '*moc1*' the only
        # owner read CLEAN while the engine confirmed a never-recorded peer was
        # owned by nothing, and the SAME rules with an empty state paged.
        for cand in (universe or []):
            if in_scope(cand) and not owned(cand):
                return "orphaned", (f"fleet peer {cand!r} — in this exclusion, "
                                    f"owned by no rule, never recorded here")
        method = f"{len(scoped)} observed subject(s)"
        if universe:
            method += f" + {len(universe)} un-recorded fleet peer(s)"
        return "owned", method
    if any(ch in core for ch in _MINI_GLOB_META):
        return "unjudgeable", ("glob metacharacters and no observed subject "
                               "in scope")
    if not in_scope(core):
        return "unjudgeable", (f"synthetic subject {core!r} is outside the "
                               f"rule's own selector and no observed subject "
                               f"is in scope")
    if owned(core):
        return "owned", "structural core"
    return "orphaned", f"synthetic subject {core!r}"

