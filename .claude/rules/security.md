# Security Rules

- **MF001** — never `Path.home()` (returns `/root` under sudo); use
  `utils.paths.get_real_user_home()`.
- **MF002** — never `shell=True`; pass an argument list.
- **MF003** — never bare `except:`; name the exception.
- **MF004** — every `subprocess` call gets `timeout=`.
- **Input** — validate all user input; paths absolute with no `..`; validate
  URLs before fetch; escape displayed text.
- **Secrets** — never commit `.env`, `credentials.json`, API keys, private
  keys; use env vars or secure config.
- **MF015** — nothing under `docs/` (public, indexed forever) carries a
  literal operator LAN IP (`192.168.x.y`, `10.x.y.z`, `172.16–31.x.y`) — not
  in text, link targets, comments or alt-text. Use `<ip>`. Sanitize at copy
  time. Audit before publishing:
  `grep -rn "192\.168\.[0-9]\+\.[0-9]\+\|10\.[0-9]\+\.[0-9]\+\.[0-9]\+" docs/substack/ | grep -v "x\.x\|<ip>"`
  (MF014 is the source/template twin.)

Linter: `python3 scripts/lint.py --all`. Examples: `.claude/foundations/rules_evidence.md`.
