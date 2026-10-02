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

