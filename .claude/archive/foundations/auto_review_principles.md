# MeshForge Auto-Review Principles

## Overview

This document establishes the systematic code review framework for MeshForge, inspired by Auto-Claude's autonomous multi-agent approach. When a user requests an "extensive code review", "security review", or "reliability check", this schema guides the automated review process.

**Foundation**: This system operates under MeshForge's core principles defined in `ai_principles.md`:

```
┌─────────────────────────────────────────────────────┐
│              MESHFORGE ETHICAL HIERARCHY             │
├─────────────────────────────────────────────────────┤
│  1. Safety First    - Never compromise human safety │
│  2. Privacy Default - Encryption is non-negotiable  │
│  3. Accessibility   - Technology serves all users   │
│  4. Transparency    - Open source, open knowledge   │
│  5. Sustainability  - Long-term over short-term     │
└─────────────────────────────────────────────────────┘
```

Every code review decision flows through this ethical framework.

### "When All Else Fails" Review Doctrine

Aligned with the Amateur Radio doctrine guiding MeshForge:

1. **Prepare for the worst** - Review for disaster scenarios (offline, degraded, emergency)
2. **Hope for the best** - Optimize for daily use performance
3. **Serve without expectation** - Code that helps the community
4. **Learn continuously** - Document findings for future reference

### AI Autonomy Spectrum for Reviews

```
┌────────────────────────────────────────────────────────┐
│              AI REVIEW ASSISTANCE SPECTRUM              │
├────────────────────────────────────────────────────────┤
│                                                        │
│  HUMAN CONTROL ◄────────────────────► AI AUTONOMY     │
│                                                        │
│   Report Only  │  Suggest Fix   │  Auto-Fix Safe      │
│  ──────────── │ ──────────────│ ────────────────    │
│   AI finds    │ AI recommends  │ AI fixes low-risk   │
│   issues      │ human approves │ issues directly     │
│                                                        │
│  Examples:    │ Examples:      │ Examples:            │
│  - Arch issues│ - Refactoring  │ - Bare except       │
│  - Credentials│ - API changes  │ - shell=True        │
│  - Design     │ - Singletons   │ - Missing timeout   │
└────────────────────────────────────────────────────────┘
```

## Review Orchestration Schema

### Trigger Phrases
The following user requests should initiate the full review protocol:
- "exhaustive code review"
- "security review"
- "reliability check"
- "code audit"
- "clean up redundancy"
- "optimize meshforge"

### Parallel Agent Architecture

When triggered, spawn 4 specialized review agents simultaneously:

```
┌─────────────────────────────────────────────────────────────┐
│                    REVIEW ORCHESTRATOR                       │
├─────────────┬─────────────┬─────────────┬───────────────────┤
│  Security   │ Redundancy  │ Performance │   Reliability     │
│   Agent     │   Agent     │   Agent     │     Agent         │
├─────────────┼─────────────┼─────────────┼───────────────────┤
│ • shell=True│ • Duplicate │ • Blocking  │ • Bare except     │
│ • Injection │   code      │   I/O       │ • Index bounds    │
│ • Hardcoded │ • Singleton │ • Memory    │ • Null checks     │
│   secrets   │   violations│   leaks     │ • Error handling  │
│ • Path trav │ • Import    │ • Timer     │ • Edge cases      │
│ • OWASP Top │   cycles    │   cleanup   │ • Type safety     │
│   10        │             │             │                   │
└─────────────┴─────────────┴─────────────┴───────────────────┘
```

## Agent Specifications

### 1. Security Agent

**Priority**: HIGH
**Scope**: All Python files, configuration files, shell scripts

**Search Patterns**:
```python
# Critical vulnerabilities
"shell=True"                    # Command injection risk
"eval("                         # Code injection
"exec("                         # Code execution
"pickle.load"                   # Deserialization attacks
"yaml.load" without Loader      # YAML injection
"subprocess.*shell"             # Shell injection
"os.system"                     # Command injection
"__import__"                    # Dynamic imports

# Medium severity
"password.*=.*['\"]"            # Hardcoded credentials
"secret.*=.*['\"]"              # Hardcoded secrets
"api_key.*=.*['\"]"             # Exposed API keys
"token.*=.*['\"]"               # Exposed tokens

# Path traversal
"open(.*\+.*)"                  # Path concatenation without validation
"os.path.join.*request"         # User input in paths
```

**Fix Protocol**:
1. Replace `shell=True` with argument lists via `shlex.split()`
2. Use parameterized queries for database operations
3. Move secrets to environment variables or secure config
4. Validate and sanitize all user inputs
5. Use `pathlib` for safe path operations

### 2. Redundancy Agent

**Priority**: MEDIUM
**Scope**: Code patterns, imports, class instantiation

**Search Patterns**:
```python
# Object instantiation redundancy
"Console()"                     # Should use singleton
"logging.getLogger"             # Should use centralized config
"ArgumentParser()"              # Consider shared parser
"requests.Session()"            # Consider session reuse

# Function duplication
"def check_root"                # Should be centralized
"def get_serial_ports"          # Should be in utils
"def validate_callsign"         # Should be in validators

# Import redundancy
"from rich.console import"      # Should use utils.console
"import logging\nlogger"        # Should use utils.logging_config
```

**Fix Protocol**:
1. Create singleton managers in `utils/` directory
2. Consolidate duplicate functions into appropriate modules
3. Use centralized imports through `utils/__init__.py`
4. Apply DRY (Don't Repeat Yourself) principle

### 3. Performance Agent

**Priority**: MEDIUM
**Scope**: I/O operations, loops, resource management

**Search Patterns**:
```python
# Blocking operations
"subprocess.run"                # Check for timeout
"subprocess.Popen"              # Check for process cleanup
"requests.get" without timeout  # Network calls need timeouts
"serial.Serial"                 # Serial operations

# Memory issues
"GLib.timeout_add"              # Must have cleanup
"threading.Timer"               # Must be cancelled
"while True:"                   # Check for exit conditions
"global "                       # Minimize global state

# Inefficiencies
"for .* in .*\.items()"         # Check if keys/values only needed
"list(.*generator.*)"           # Unnecessary list conversion
"+ " in loops                   # String concatenation in loops
```

**Fix Protocol**:
1. Add timeouts to all network/subprocess calls (default: 30s)
2. Implement cleanup handlers for timers and threads
3. Use `with` statements for resource management
4. Convert string concatenation to f-strings or `.join()`

### 4. Reliability Agent

**Priority**: HIGH
**Scope**: Error handling, edge cases, type safety

**Search Patterns**:
```python
# Poor error handling
"except:"                       # Bare except catches SystemExit
"except Exception:"             # Too broad without logging
"pass" after except             # Silent failures
"# TODO"                        # Unfinished code

# Unsafe operations
"[0]" without length check      # Index out of bounds
"[key]" without key check       # KeyError potential
".split()[" without validation  # Split result access
"int(" without try/except       # ValueError potential

# Type safety
"None" return without check     # NoneType errors
"Optional[" return unchecked    # Must validate Optional returns
```

**Fix Protocol**:
1. Replace bare `except:` with specific exceptions
2. Add bounds checking before index access
3. Use `.get()` with defaults for dictionary access
4. Wrap type conversions in try/except
5. Add None checks for Optional returns

## Review Output Format

Each agent produces a structured report:

```markdown
## [Agent Name] Review Results

### Summary
- Files scanned: N
- Issues found: N (X HIGH, Y MEDIUM, Z LOW)

### HIGH Priority Issues
| File | Line | Issue | Recommendation |
|------|------|-------|----------------|
| path/file.py | 42 | shell=True | Use shlex.split() |

### MEDIUM Priority Issues
...

### Fixes Applied
- [x] Fixed shell=True in path/file.py:42
- [ ] Requires manual review: hardcoded credential in config.py
```

## Automated Fix Protocol

### Safe Auto-Fix Categories
These issues can be automatically fixed:
1. `shell=True` → argument lists
2. Bare `except:` → `except Exception as e:`
3. Missing timeouts → add 30s default
4. Timer cleanup → add unrealize handlers

### Manual Review Required
These require human decision:
1. Hardcoded credentials (need secure storage strategy)
2. Architecture changes (singleton refactoring)
3. API changes (backwards compatibility concerns)
4. Business logic issues

## Integration with MeshForge University

This review system is documented in the **Automated Code Review** course:

### Course: Automated Code Review (AUTO-001)
1. **Understanding the Review Agents** - How each agent works
2. **Security Patterns** - Common vulnerabilities and fixes
3. **Performance Optimization** - Identifying bottlenecks
4. **Reliability Engineering** - Building robust code
5. **Running Automated Reviews** - Triggering and interpreting results
6. **Custom Review Rules** - Extending the review system

## Command Interface

Users can trigger reviews with varying scope:

```
User: "run security review"
→ Activates Security Agent only

User: "exhaustive code review"
→ Activates all 4 agents in parallel

User: "check reliability in amateur_panel.py"
→ Activates Reliability Agent on specific file

User: "optimize performance"
→ Activates Performance Agent
```

## Continuous Integration

For CI/CD integration, reviews can be triggered on:
- Pull request creation
- Pre-commit hooks
- Scheduled nightly scans
- Manual trigger via `/review` command

## User Archetype Validation

Each review must validate code against MeshForge's user archetypes:

| Archetype | Review Focus |
|-----------|--------------|
| **Prepared Pragmatist** | Reliability, documentation, predictable behavior |
| **Technical Explorer** | Advanced options exposed, no artificial limits |
| **Community Builder** | Easy sharing, group features, social tools |
| **Emergency Responder** | Zero-friction critical paths, instant reliability |

### Stress Response Design Review

Code handling critical paths must pass stress response validation:

```
Human Response    → Code Requirement
─────────────────────────────────────────────────
Tunnel vision     → Single obvious primary action
Memory impairment → On-screen guidance, no memorization required
Motor degradation → Large touch targets, forgiving inputs
Time pressure     → Instant feedback, progress indicators
```

## Foundation Document Cross-References

This Auto-Review system integrates with:

| Document | Integration |
|----------|-------------|
| `ai_principles.md` | Ethical hierarchy, user archetypes, stress design |
| `ai_interface_guidelines.md` | UI/UX review standards |
| `ai_development_practices.md` | Debugging methodology, TRACE method |
| `version_framework.md` | Edition-specific review (PRO/Amateur/IO) |

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-05 | Initial Auto-Review principles with MeshForge foundation integration |

---

*This document enables systematic, reproducible code reviews following Auto-Claude's autonomous agent architecture and MeshForge's foundational principles.*
