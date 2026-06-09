# Persistent Task Loop

**Task:** $ARGUMENTS

---

## Loop Protocol

You are in a persistent development loop. Work autonomously until the task is 100% complete.

### Each Iteration:

1. **Assess**
   - Track subtasks with the task tools (TaskCreate/TaskUpdate — TodoWrite no longer exists)
   - Check current state: `git status`, test results
   - Identify what remains

2. **Execute**
   - Do the next step
   - Follow the auto-loaded MeshForge rules (CLAUDE.md + `.claude/rules/security.md`)
   - Walk `.claude/rules/honest_failure_modes.md` over every error path you write
   - Write tests for new functionality

3. **Verify** — capture real exit codes; never judge from truncated streams
   ```bash
   python3 scripts/lint.py --all 1>/tmp/lint.log 2>&1; echo LINT_EXIT=$?
   python3 -m pytest tests/ -q 1>/tmp/pytest.log 2>&1; echo TEST_EXIT=$?
   tail -5 /tmp/pytest.log
   ```

4. **Continue**
   - If not done, loop back to Assess
   - Mark completed tasks as you go

---

## Exit Conditions

ALL must be true:
- [ ] Task is 100% complete
- [ ] `scripts/lint.py --all` exits 0
- [ ] All tests pass (exit code 0, not a "passed" line in a truncated stream)
- [ ] Changes committed on `main` (solo workflow — PR/feature-branch flow retired 2026-04-19)
- [ ] Pushed: `git push origin main` (then pull the fleet boxes)

---

## MeshForge Context

Key paths: `src/` (source) · `tests/` · `src/gateway/` · `src/launcher_tui/` · `src/utils/`

Security rules are auto-loaded from `.claude/rules/security.md` — don't restate,
just follow them (lint + pre-commit hook enforce).

---

## Completion Signal

When ALL exit conditions verified:

`<promise>DONE</promise>`

**Do NOT output the promise until fully verified complete.**

---

*"I'm in danger!"* - Ralph Wiggum (but you're not, keep looping)
