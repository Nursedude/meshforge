# Security Rules

## MF001: Path.home() - NEVER use directly

```python
# WRONG - returns /root when running with sudo
config = Path.home() / ".config" / "meshforge"

# CORRECT - works with sudo
from utils.paths import get_real_user_home
config = get_real_user_home() / ".config" / "meshforge"
```

**Why**: `Path.home()` returns effective user's home. With `sudo`, that's `/root`, breaking config persistence.

**Linter**: `python3 scripts/lint.py` checks MF001

---

## MF002: shell=True - NEVER use in subprocess

```python
# WRONG - command injection risk
subprocess.run(f"meshtastic --info {user_input}", shell=True)

# CORRECT - safe argument list
subprocess.run(["meshtastic", "--info", user_input], timeout=30)
```

**Why**: Shell injection allows arbitrary code execution.

**Linter**: `python3 scripts/lint.py` checks MF002

---

## MF003: Bare except - Always specify exception type

```python
# WRONG - catches SystemExit, KeyboardInterrupt
except:
    pass

# CORRECT - specific exceptions
except Exception as e:
    logger.error(f"Operation failed: {e}")
```

---

## MF004: subprocess timeout - ALWAYS include

```python
# WRONG - can hang forever
subprocess.run(["long", "command"])

# CORRECT - bounded execution
subprocess.run(["long", "command"], timeout=30)
```

---

## Input Validation

- Validate all user input before use
- Sanitize file paths (no `..`, absolute paths only)
- Validate URLs before fetch
- Escape special characters in displayed text

---

## Secrets

Never commit:
- `.env` files
- `credentials.json`
- API keys in code
- Private keys

Use environment variables or secure config.
