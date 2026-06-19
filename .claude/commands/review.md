# Auto-Claude Review

Run the MeshForge self-audit system and report findings.

## Instructions

1. Run the auto-review system (CLI entrypoint — `python3 -c` is blocked by the
   project deny-list, so the runner lives in `auto_review.py`'s `__main__`):
```bash
cd /opt/meshforge
python3 src/utils/auto_review.py 1>/tmp/autoreview.log 2>&1; echo AUTOREVIEW_EXIT=$?
cat /tmp/autoreview.log
```

2. Run the blocking gate (this is what CI and the pre-commit hook enforce):
```bash
python3 scripts/lint.py --all 1>/tmp/lint.log 2>&1; echo LINT_EXIT=$?
```

3. Analyze auto-review findings for false positives (patterns in documentation/courses)
4. Report actual issues that need fixing, judged from LINT_EXIT plus confirmed findings
5. Suggest prioritized fixes
