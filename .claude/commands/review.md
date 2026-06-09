# Auto-Claude Review

Run the MeshForge self-audit system and report findings.

## Instructions

1. Run the auto-review system:
```bash
cd /opt/meshforge/src && python3 -c "
from utils.auto_review import ReviewOrchestrator
r = ReviewOrchestrator()
report = r.run_full_review()
print(f'Files: {report.total_files_scanned}')
print(f'Issues: {report.total_issues}')
for cat, result in report.agent_results.items():
    print(f'  {cat.value}: {result.total_issues}')
"
```

2. Run the blocking gate (this is what CI and the pre-commit hook enforce):
```bash
python3 scripts/lint.py --all 1>/tmp/lint.log 2>&1; echo LINT_EXIT=$?
```

3. Analyze auto-review findings for false positives (patterns in documentation/courses)
4. Report actual issues that need fixing, judged from LINT_EXIT plus confirmed findings
5. Suggest prioritized fixes
