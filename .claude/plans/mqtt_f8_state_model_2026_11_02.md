# MQTT F8 state model — grill record (2026-11-02, operator-led)

Supersedes the parked F8 patch (`~/.local/share/meshforge/parked/f8-*`):
retired, its tests mined as scenarios. Rows folded in:
`mqtt-tui-auth-blank-password-wipes` (F8), `mqtt-tui-unsaved-tls-edit-dropped`,
`mqtt-tui-two-tls-controls-legibility`.

**Root cause (one):** every screen wrote a full SNAPSHOT it loaded earlier,
so a stale screen overwrote another writer's save, and the password travelled
with whichever screen held it.

## Acceptance (Q14 — agreed before code)

On the manager box, two TUI screens open at once each change a different
MQTT field and save, and both changes are on disk. Changing the broker never
carries or silently clears a password — the operator is asked first. Esc
never writes.

## Decisions

| # | Decision |
|---|---|
| Q1 | One state model for F8 + unsaved-TLS-edit + two-TLS-controls. |
| Q2 | Screens send only CHANGES; `mqtt_settings.save_changes` re-reads disk at Save and applies them. No schema change. |
| Q3 | The TUI never holds the saved plaintext password: shown as `(saved)`; a change is absent (keep) / new string / `""` (explicit clear). |
| Q4 | Account = (host, user); host trimmed, lowercased, trailing dot dropped, `localhost`/`127.0.0.1`/`::1` folded. No DNS. |
| Q5 | Esc/Cancel at any prompt of a form abandons it; nothing written. |
| Q6 | The Configure list's `tls` row opens the same TLS menu as the private form (one control). |
| Q7 | Account changed + password "keep" + a saved password → `save_changes` refuses BEFORE writing (`password_owner_mismatch`); the TUI asks "password for user@host (blank = none)". |
| Q8 | A blank username is still an account (password-only brokers follow the same rules). |
| Q9 | `fcntl.flock` across re-read/apply/write, ~5 s wait, else refuse "another save in progress" (stdlib — module stays byte-identical with MA). |
| Q10 | A field changed both by me and on disk since my load (to different values) is refused per field, both values shown; one-sided changes merge. |
| Q11 | Parked patch retired; scenarios mined. |
| Q12 | MF first (build → review → deploy → live drill), then the MA byte copy + TUI twin, gated by parity_check; port MA's test-isolation gate first. |
| Q13 | This session: build, red-first, full suite, non-author review, fixes. Deploy + live drill NEXT session (brake). |
| Q15 | Save over an UNREADABLE file refuses, offering an explicit Reset (full write, keeps the `.unreadable.<n>` backup). Absent → changes over defaults; legacy → over the translation (keeps `.pre-v1`). |
| Q16 | `save()` stays as the internal full write (Reset + inside `save_changes`); a guard fails if a TUI handler writes any other way. |
| Q17 | (r3 #3, operator 2026-11-02 pm) The password joins the Q10 both-sides check: the owner keeps a FINGERPRINT of the saved password (never the value) in what a screen opens with; a password changed on disk since the screen opened AND by the screen refuses like any field. |
| Q18 | (r3 #5, operator 2026-11-02 pm) A conflict resolved then abandoned (Esc) returns to the list with a FRESH read of the file — base and password_saved re-derived together, never a half-mutated base. |

## Next session (blockers before deploy)

r3 #1/#2: Reset = a locked mode of `save_changes` (re-check unreadable under the lock, Secret account check, `.unreadable.<n>` backup). r3 #4: one STABLE lock target (the config dir's parent) + a two-writer first-save test. Then Q17, Q18 — each red-first; then a non-author pass (most capable model available); stop rule: a regressing round parks the TUI half; then deploy + the Q14 live drill.

## Class worth naming (queued, not in scope)

Multi-writer settings files written as full snapshots from several screens
(`gateway.json`, broker profiles, `deployment.json`): once `save_changes`
passes its live drill it is the pattern for those.
