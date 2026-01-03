# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 4.2.x   | :white_check_mark: |
| 4.1.x   | :white_check_mark: |
| 4.0.x   | :x:                |
| < 4.0   | :x:                |

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report security issues by emailing the maintainers directly. You should receive a response within 48 hours. If the issue is confirmed, we will release a patch as soon as possible depending on complexity.

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Security Measures

MeshForge implements the following security measures as of v4.2.0:

### Input Validation

All user input is validated before use:

- **Journalctl time parameters**: Whitelist validation with safe patterns only
- **Message content**: 230-byte limit, UTF-8 validation
- **Node IDs**: Hexadecimal format validation (8-16 characters)
- **File paths**: Path traversal prevention

### XSS Prevention

- HTML escaping for all dynamic content in Web UI
- `escapeHtml()` function used for node names, messages, user input
- Content Security Policy headers

### Command Injection Prevention

- No `os.system()` calls - all commands use `subprocess.run()`
- No `shell=True` in subprocess calls
- `shlex.split()` for parsing user-provided commands
- Argument lists passed directly to subprocess

### Network Security

- Default binding to `127.0.0.1` (localhost only)
- Optional password authentication for Web UI
- Security headers on all responses:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: SAMEORIGIN`
  - `X-XSS-Protection: 1; mode=block`
  - `Content-Security-Policy`

### Secure Defaults

- Services bind to localhost by default
- Password required for remote access
- Sensitive operations require confirmation

## Security Audit History

### v4.2.0 (2026-01-03) - Comprehensive Security Audit

A thorough security audit was performed identifying and fixing:

| Issue | Severity | Status |
|-------|----------|--------|
| DOM-based XSS via node names/messages | Critical | Fixed |
| Journalctl time parameter injection | Critical | Fixed |
| Insecure default binding (0.0.0.0) | High | Fixed |
| Missing security headers | High | Fixed |
| TUI command injection via split() | High | Fixed |
| Missing message validation | Medium | Fixed |

**Confirmed Secure:**
- Path traversal prevention (already implemented)
- Timing-safe password comparison (already implemented)
- No shell=True usage in subprocess calls

## Development Security Guidelines

When contributing to MeshForge, follow these security practices:

### Do

- Use `subprocess.run()` with argument lists
- Validate all user input before use
- Escape HTML output with `escapeHtml()`
- Use `shlex.split()` for parsing command strings
- Bind to `127.0.0.1` by default
- Use `hmac.compare_digest()` for secret comparison

### Don't

- Use `os.system()` or `os.popen()`
- Use `shell=True` in subprocess
- Trust user input without validation
- Embed user content directly in HTML
- Bind to `0.0.0.0` without explicit user request
- Store secrets in code or config files

### Code Examples

**Safe subprocess usage:**
```python
# Good
subprocess.run(["journalctl", "-u", "meshtasticd", "--since", validated_time])

# Bad - never do this
os.system(f"journalctl --since {user_input}")
subprocess.run(f"journalctl --since {user_input}", shell=True)
```

**Safe HTML output:**
```javascript
// Good
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}
element.innerHTML = `<span>${escapeHtml(userInput)}</span>`;

// Bad - XSS vulnerability
element.innerHTML = `<span>${userInput}</span>`;
```

**Input validation:**
```python
# Good
def validate_node_id(node_id):
    if not node_id:
        return False
    if not re.match(r'^[a-fA-F0-9]{8,16}$', node_id):
        return False
    return True

# Bad - trusting user input
node_id = request.form.get('node_id')
send_message(node_id, message)  # No validation!
```

## Security Contact

For security concerns, contact the maintainers through GitHub.

## Acknowledgments

Thanks to security researchers and contributors who help keep MeshForge secure.
