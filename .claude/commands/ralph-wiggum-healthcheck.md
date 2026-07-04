# MeshForge Knowledge Healthcheck

Run a comprehensive audit of the MeshForge knowledge base to prevent memory degradation.

---

## Healthcheck Protocol

### 1. Version & State Check
```bash
# Current version
python3 -c "from src.__version__ import __version__; print(__version__)"

# Git state
git status --short
git log --oneline -5
```

### 2. Continuity Check
Cross-reference these critical files:
- `CLAUDE.md` - Main instructions
- `.claude/foundations/persistent_issues.md` - Known gotchas
- `.claude/foundations/domain_architecture.md` - Core vs plugin model
- `.claude/plans/TODO_PRIORITIES.md` - Current priorities
- `src/__version__.py` - Version and changelog

Look for:
- Contradictions between files
- Outdated paths/imports
- Version mismatches
- Stale TODO items

### 3. Codebase Sync
```bash
# Verify documented paths exist
ls -la src/gateway/
ls -la src/launcher_tui/
ls -la src/launcher_tui/handlers/
ls -la tests/

# Check for large files needing split
find src -name "*.py" -exec wc -l {} \; | sort -rn | head -10
```

Compare documented features vs actual `src/` implementation.

### 4. Auto-Review Integration
```bash
cd /opt/meshforge/src && python3 -c "
from utils.auto_review import ReviewOrchestrator
r = ReviewOrchestrator()
report = r.run_full_review()
for cat, result in report.agent_results.items():
    print(f'{cat.value.title()}: {result.total_issues}')
print(f'Total: {report.total_issues}')
"
```

### 5. Test Health
```bash
python3 -m pytest tests/ --tb=no -q 1>/tmp/hc_pytest.log 2>&1; echo EXIT=$?
tail -20 /tmp/hc_pytest.log
```
Judge from EXIT, not from the tail.

### 6. Fragmentation Analysis
- Find duplicated information across `.claude/` files
- Identify orphaned docs (referenced nowhere)
- Check for circular references
- Map information dependencies

### 7. File Size Audit
Flag files over 1,500 lines:
```bash
find src -name "*.py" -exec wc -l {} \; | sort -rn | head -10
```

### 8. Skills & Commands Freshness
Audit `.claude/skills/` and `.claude/commands/` for:
- Version references that don't match `src/__version__.py` (prefer NO hardcoded
  version/counts in skills at all — point at the source of truth instead)
- Architecture references that don't match current codebase
- Hardcoded paths that may have changed
- Stale handler/mixin references (project uses handler registry pattern now)
- **Harness drift** (audited 2026-06-09): tool names that no longer exist in the
  running Claude Code harness (`TodoWrite` → TaskCreate/TaskUpdate), retired
  workflow references (feature-branch/PR flow ended 2026-04-19 — exit conditions
  must say push to `main`), and exit-code-masking patterns
  (`pytest | head`/`| tail` — must be file-redirect + `echo EXIT=$?`)
- **Duplication of auto-loaded context**: CLAUDE.md, `.claude/rules/*.md`, and
  `persistent_issues.md` load into every session — a skill restating them is
  wasted context and a drift source (two consumers of one artifact). Skills
  carry only what lives nowhere else.

### 9. Documentation Freshness Audit
Audit `.claude/` markdown files for staleness and drift:

```bash
# Files not modified in 60+ days (potential staleness)
find .claude -name "*.md" -mtime +60 -printf "%T+ %p\n" | sort

# Check for stale technology references that shouldn't exist
grep -r "gtk_ui\|GLib.idle_add\|main_web.py\|_mixin.py" .claude/ --include="*.md" -l

# Version references — should all match src/__version__.py
grep -rn "v0\.[0-4]\." .claude/ --include="*.md" | grep -v "archive\|timeline\|history\|postmortem\|article"
```

Cross-check:
- Every file listed in `INDEX.md` exists on disk (dead links are real rot)
- `INDEX.md` lists the CURATED entry points, not every file — the
  every-.md-must-be-indexed rule was retired 2026-07-04 (58 plan/research
  one-offs had drifted out and hand-indexing them is busywork): full-corpus
  lore search is the offline oracle's job now
  (`python3 -m mini_dudeai.offline_oracle --retrieve-only "<question>"`
  BM25-ranks ALL of `.claude/` + `docs/` + memory, no index to maintain)
- No version references older than current version (except in historical/archive docs)
- `plans/TODO_PRIORITIES.md` priorities align with actual development activity

Flag: Files with stale content, orphaned docs, version mismatches, naming violations.

---

## Output Format

Produce:
1. **Health Score** - 0-100 based on issues found
2. **Critical Issues** - Must fix immediately
3. **Warnings** - Should fix soon
4. **Suggestions** - Nice to have
5. **Actions Taken** - What was fixed during audit

---

## Completion Signal

When audit is complete and documented:

`<promise>HEALTHCHECK COMPLETE</promise>`

---

*"My cat's breath smells like cat food."* - Ralph Wiggum
