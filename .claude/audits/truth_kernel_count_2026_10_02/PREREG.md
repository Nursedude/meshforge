# Pre-registration — incident-class count for the truth-kernel ADR
Written 2026-10-02 BEFORE any unit is classified. Author (Opus 5.5) has a stated
prior: "a compiler would not have caught most; a shared-failure-domain observer
explains many". Because the author is biased, the author does NOT classify.

## Units
units.tsv — 164 units enumerated mechanically (every `## ` section of
persistent_issues_archive.md + persistent_issues.md, every table row of the live
file). Unit = one id. Rated by TWO contextless classifiers, independently,
identical instructions, read-only.

## Primary class (exactly one per unit, by ROOT CAUSE)
- C1 COMPILE: a mainstream statically-typed compiled language (Rust or Go, default
  compiler + default lints) would have REJECTED the defective code as written —
  type mismatch, None/null deref, missing attribute/field, wrong arity,
  non-exhaustive match on an enum, Rust data race / use-after-move.
- C2 SEMANTIC: the code ran as written and produced a wrong or misleading value
  or decision (wrong quantity measured, wrong default, degraded state mapped to a
  valid-looking value, wrong threshold, logic/ordering error).
- C3 ENV/OPS: deployment, config files, OS/systemd, packaging/deps, network,
  hardware/RF/physical, upstream firmware/library behaviour.
- C4 CONCURRENCY/RESOURCE: races, leaks, contention, starvation, timeouts.
- C5 HUMAN/UX/DOC: operator confusion, naming, docs.
- NA: not an incident (checklists, reference tables, rule lists, index text).

## Flags (yes/no each; NA units get no flags)
- F_SHARED: the defect was CAUSED, PROLONGED or HIDDEN because an observer
  (detector, probe, test, checker, health gate, the operator's tool) shared a
  failure domain or a blind spot with what it observed — same env/interpreter/
  data/artifact/assumption; checker consuming the artifact it validates;
  detector blind by construction.
- F_TRISTATE: a mandatory result type with distinct Seen / Unobservable / Error
  variants and NO implicit default (no `or []`, no `.get(default)` collapse) would
  likely have prevented it at write time.
- F_RUST: rewriting only the affected component in Rust (same author, same design)
  would likely have prevented it.

## Decision rules (fixed now)
1. Domain-wide compiled REWRITE is supported only if C1 ≥ 25% of incidents
   (non-NA) OR F_RUST ≥ 25%. Otherwise: no rewrite.
2. A small independent compiled OBSERVER ("truth kernel") is supported if F_SHARED
   ≥ 20% of incidents AND a majority of the F_SHARED units fall inside the
   kernel's proposed scope (process/unit/install/socket/clock/heartbeat facts —
   judged by the author AFTER the count, listed unit by unit so it can be audited).
3. A typed tri-state result type (in Python now) is supported if F_TRISTATE ≥ 15%.
4. Agreement: report % agreement and Cohen's kappa on the primary class and on
   each flag. A flag with kappa < 0.4 is reported as UNRELIABLE and its rule is
   NOT used to decide; the decision falls back to "insufficient evidence".
5. Headline numbers = the two raters' MEAN share, with both raters' values shown.
   Disagreements are not adjudicated by the author.
