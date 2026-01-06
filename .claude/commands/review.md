# Auto-Claude Review

Run the MeshForge self-audit system and report findings.

## Instructions

1. Run the auto-review system:
```bash
cd /home/user/meshforge/src && python3 -c "
from utils.auto_review import ReviewOrchestrator
r = ReviewOrchestrator()
report = r.run_full_review()
print(f'Files: {report.total_files_scanned}')
print(f'Issues: {report.total_issues}')
for cat, result in report.agent_results.items():
    print(f'  {cat.value}: {result.total_issues}')
"
```

2. Analyze findings for false positives (patterns in documentation/courses)
3. Report actual issues that need fixing
4. Suggest prioritized fixes
