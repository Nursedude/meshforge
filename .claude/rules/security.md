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

---

## MF015: No operator-specific local IPs in published docs

**Rule**: Anything under `docs/` (especially `docs/substack/`) is public-facing.
Never paste literal LAN IPs (`192.168.x.y`, `10.x.y.z`, `172.16-31.x.y`) from
the operator's network. They identify the operator's home/office subnet and
leak topology.

```markdown
<!-- WRONG - leaks operator's LAN -->
Could not load `http://192.168.86.249:5000/`.

<!-- ALSO WRONG - link target still contains the IP, hover/click reveals it -->
Could not load [http://<ip>:5000](http://192.168.86.249:5000).

<!-- CORRECT - pure placeholder, no real IP anywhere in source -->
Could not load `http://<ip>:5000/`.
```

The IP must not appear in the source at all — not in display text, not in
link targets, not in HTML comments, not in alt-text. "Hidden" link targets
are still in the rendered HTML and still get crawled.

**Why**: Substack posts, README screenshots, and committed transcripts get
indexed by search engines and archived forever. A single leaked LAN IP plus
a hostname pins the operator's physical network.

**Companion to MF014** (operator-specific values in source/templates). MF015
is the docs equivalent. When transcribing a debugging session into a
published post, sanitize IPs at copy time, not "later."

**Audit before publishing a substack post**:
```bash
grep -rn "192\.168\.[0-9]\+\.[0-9]\+\|10\.[0-9]\+\.[0-9]\+\.[0-9]\+" docs/substack/ \
  | grep -v "x\.x\|<ip>"
```
