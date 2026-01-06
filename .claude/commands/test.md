# Run Tests

Execute the MeshForge test suite and report results.

## Instructions

1. Run all test files:
```bash
cd /home/user/meshforge
python3 -m pytest tests/ -v --tb=short 2>&1 | head -100
```

2. If pytest not available, run individually:
```bash
python3 tests/test_security.py
python3 tests/test_rf_utils.py
python3 tests/test_gateway_diagnostic.py
python3 tests/test_plugins.py
```

3. Report pass/fail counts and any failures
