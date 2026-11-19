# SRC-1 — the artifact LIBRARY built from the repo (2026-11-18)

Operator 11-16: the repo is the SoT, pages are dated views. Operator 11-18: artifacts are the domain's
library ("dudeai university") — the research wealth, organised by topic, each page complementing the
others. Operator 11-18: "yes, fold 1-4 into SRC-1".

Baseline (`audits/artifact_audit_2026_11_17.md`): 46 pages, 2 sourced, ~200 findings undisposed, 11
misleading. Structure, not more care, is the cure. All of it EXTENDS loop_gate TOPIC — no new gate class
(BRAKE).

Already in the row (11-16): registry `source` path; refuse a page update while that source is dirty in the
tree; each page carries `mf:source` path@sha.

1. **Registry `source` + `sha` per page** — `{topic, role, source: <repo path>, sha}` in
   `.claude/artifacts_registry.json`; a check compares `sha` with the source file's last commit, so a
   stale page reads STALE instead of drifting silently.
2. **Pages GENERATED from the repo** — one generator per page kind (research, audit, plan), the
   `roadmap_html.py` pattern (roadmap v19: 0 rule failures, 0 LAN IPs); "page == repo" is a build step.
3. **A catalog page generated from the registry** — topic, authoritative page, source sha, stale/current;
   superseded pages listed as such.
4. **`scripts/library_publish.py <source>`** — a script, not a skill (description-matching skills fired
   0/112 sessions): generate → stamp source@sha → registry update → TOPIC/GROUND gate.

Acceptance (before code): a planted stale page (source committed after its sha) is reported STALE by
the check and the catalog; a fresh one is not. The control must fail on the pre-change check (BENCH-G
gate rule).

First real use: the 11-18 CS-1 publish (Hawaii Meshnet doc tab, Cascadia pointer) was done by hand —
the GROUND gate refused a whole-file Cascadia republish ("baseline NOT MEASURED") and took an
`edits` publish. That page's ungrounded-VERIFIED baseline is owed in the registry.
