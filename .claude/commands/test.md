# Run Tests

Execute the MeshForge test suite and report results.

## Instructions

1. Run the full suite, capturing the REAL exit code (never judge from a
   `| head`/`| tail`-truncated stream — truncation masks failures):
```bash
cd /opt/meshforge
python3 -m pytest tests/ -v --tb=short 1>/tmp/pytest.log 2>&1; echo EXIT=$?
tail -20 /tmp/pytest.log
```

2. If EXIT is non-zero, pull the failure detail from the log:
```bash
awk '/short test summary info/{flag=1} flag' /tmp/pytest.log | head -50
```

3. Report pass/fail counts and any failures, judged from EXIT — not from the
   presence of a "passed" line.

Note: pytest is the only supported runner. Do NOT fall back to running test
files directly (`python3 tests/test_x.py`) — pytest-style files exit 0 having
executed zero tests, which reads as a false green.
