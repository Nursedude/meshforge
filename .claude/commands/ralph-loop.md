# Persistent Task Loop

**Task:** $ARGUMENTS

---

## Loop Protocol

You are in a persistent development loop. Work autonomously until the task is 100% complete.

### Each Iteration:

1. **Assess**
   - Use TodoWrite to track subtasks
   - Check current state: `git status`, test results
   - Identify what remains

2. **Execute**
   - Do the next step
   - Follow MeshForge rules (no shell=True, use get_real_user_home())
   - Write tests for new functionality

3. **Verify**
   ```bash
   # Run tests
   python3 -m pytest tests/ -v

   # Run auto_review
   cd src && python3 -c "from utils.auto_review import ReviewOrchestrator; r=ReviewOrchestrator(); print(f'Issues: {r.run_full_review().total_issues}')"
   ```

4. **Continue**
   - If not done, loop back to Assess
   - Mark completed todos as you go

---

## Exit Conditions

ALL must be true:
- [ ] Task is 100% complete
- [ ] All tests pass
- [ ] auto_review shows 0 issues (or documented exceptions)
- [ ] Changes committed: `git add -A && git commit -m "..."`
- [ ] Pushed to branch: `git push -u origin <branch>`

---

## MeshForge Context

Key paths:
- Source: `src/`
- Tests: `tests/`
- Gateway: `src/gateway/`
- TUI: `src/launcher_tui/`
- Utils: `src/utils/`

Security rules:
- No `shell=True` in subprocess
- No bare `except:` clauses
- Use `get_real_user_home()` not `Path.home()`
- Add timeouts to subprocess calls

---

## Completion Signal

When ALL exit conditions verified:

`<promise>DONE</promise>`

**Do NOT output the promise until fully verified complete.**

---

*"I'm in danger!"* - Ralph Wiggum (but you're not, keep looping)
