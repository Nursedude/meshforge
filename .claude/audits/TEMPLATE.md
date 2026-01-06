# MeshForge Third-Party Security Audit Template

> **Auditor**: [Tool/Service Name - e.g., Snyk, SonarQube, CodeQL, Manual]
> **Date**: YYYY-MM-DD
> **Commit**: [git hash at time of audit]
> **Scope**: [Full repository / Specific modules]

---

## Executive Summary

[2-3 sentence overview of findings]

- **Total Issues**: X
- **Critical**: X | **High**: X | **Medium**: X | **Low**: X | **Info**: X

---

## Findings by Severity

### CRITICAL

| ID | File | Line | Issue | CWE | Status |
|----|------|------|-------|-----|--------|
| C-001 | src/example.py | 42 | SQL Injection in query | CWE-89 | Open |

**Details:**
```python
# Vulnerable code snippet if available
```

**Recommendation:** [How to fix]

---

### HIGH

| ID | File | Line | Issue | CWE | Status |
|----|------|------|-------|-----|--------|
| H-001 | | | | | |

---

### MEDIUM

| ID | File | Line | Issue | CWE | Status |
|----|------|------|-------|-----|--------|
| M-001 | | | | | |

---

### LOW

| ID | File | Line | Issue | CWE | Status |
|----|------|------|-------|-----|--------|
| L-001 | | | | | |

---

### INFORMATIONAL

| ID | File | Line | Issue | Notes |
|----|------|------|-------|-------|
| I-001 | | | | |

---

## False Positive Analysis

[List any findings that are false positives with reasoning]

| ID | Reason for False Positive |
|----|---------------------------|
| | |

---

## Remediation Priority

1. **Immediate** (Critical/High with exploit path):
   - [ ] Item

2. **Short-term** (High/Medium):
   - [ ] Item

3. **Long-term** (Low/Hardening):
   - [ ] Item

---

## Methodology

[How was the audit performed?]
- Static analysis tools used
- Manual review areas
- Excluded files/patterns

---

## Notes for Dude AI

[Any context that helps me understand the findings better]
- Known patterns in codebase
- Intentional design decisions
- Educational code vs production code

---

*Template version: 1.0*
*For use with MeshForge Auto-Claude integration*
