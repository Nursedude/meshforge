# Testing Rules

```bash
python3 scripts/lint.py --all && python3 -m pytest tests/ -q   # lint first
python3 -m pytest tests/test_rf.py -v                          # one file
python3 -m pytest tests/ --cov=src --cov-report=term-missing   # coverage
python3 -m py_compile src/**/*.py                              # syntax
```

- **Before a push that adds a file, run the FULL suite** — targeted runs miss
  the guard tests. Capture the exit code to a file; a long run goes through
  `systemd-run --user`, not a backgrounded shell.
- Always test RF math, data parsing (edge cases), path utilities (sudo), and
  business logic before TUI wiring; test handler logic apart from the TUI.
- Mock external services (`patch('requests.get')`); patch `_HAS_*` flags,
  not `sys.modules`. Shared fixtures live in `tests/conftest.py`.
- Test pure functions first; set up with fixtures; test error conditions,
  not just the happy path.

Counts and the old tree listing: `.claude/foundations/rules_evidence.md`
(stale — count live with `pytest --collect-only -q`).
