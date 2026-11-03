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
| Q18r | (round-4 non-author finding 1, operator 2026-11-02 17:58 HST — REVISES Q18) Esc at a "Changed Elsewhere" question DROPS the still-unanswered conflicting edits (password included) from the screen's changes; the list then re-reads fresh. Q18 as first built re-anchored a kept edit to the fresh read, so the next Save overwrote the other writer silently — the F8 class again. |
| Q19 | (round-4 finding 2, operator 2026-11-02 17:58 HST) A preset (Local/Public) or a broker-profile apply that would REPLACE or CLEAR a saved password asks first (yes/no naming the account); No writes nothing. Q14 sentence 2 has no preset exemption. |

## Next session (blockers before deploy)

r3 #1/#2: Reset = a locked mode of `save_changes` (re-check unreadable under the lock, Secret account check, `.unreadable.<n>` backup). r3 #4: one STABLE lock target (the config dir's parent) + a two-writer first-save test. Then Q17, Q18 — each red-first; then a non-author pass (most capable model available); stop rule: a regressing round parks the TUI half; then deploy + the Q14 live drill.

## Class worth naming (queued, not in scope)

Multi-writer settings files written as full snapshots from several screens
(`gateway.json`, broker profiles, `deployment.json`): once `save_changes`
passes its live drill it is the pattern for those.

## PARKED — 2026-11-02 18:35 HST (stop rule, operator decision)

Round 5 (contextless non-author, Fable) on `e9fcae66` found the F8 class
AGAIN: any refused/abandoned Save re-anchors the screen's base to a fresh
read while its kept edits survive, so the next Save overwrites the other
writer — fields and password, no question (5 reproduced paths). Plus: Reset
over a file that broke AFTER the screen opened writes the public defaults,
not the screen (zero-byte power-loss class). Q18r covered only the conflict
menu; Q18 itself is the mechanism.

- Operator: revert on main + bundle; next attempt = a FRESH GRILL first.
- Bundle (both review reports, MF+MA patches, reviewer scratch tests that
  fail on e9fcae66): `~/.local/share/meshforge/parked/f8-state-model-2026-11-02/`.
- Main's MQTT code = `85c99bc1` again (MF src/tests/docs; MA module byte
  copy restored). Never deployed; the live config file was last written
  2026-11-01 19:29, before F8 reached this box.

**Grill opener (owner questions):** who owns an edit's ANCHOR — the value
the edit was made against — and can it ever move without asking? (reviewer
shape: each kept edit carries its anchor, as `Secret` carries its account).
When the screen opened readable and the file broke later, is Reset's record
the defaults or the screen? Does validation run before the conflict check?

## F8 v2 — LEASE model (grill 2026-11-03 06:54 HST, operator-led, Opus 5.5)

**Fact that reopened the root (grep, main, 2026-11-03):** the only MQTT
settings writer is `save_settings()` at `launcher_tui/handlers/mqtt.py:80`;
`daemon.py` and `mqtt_subscriber.py` only read. Every "other writer" in the
five round-5 paths was a second TUI screen.

**Root cause, restated:** the merge model needed "a kept edit's anchor never
moves" to hold across every refusal/Esc/retry path, and five rounds showed it
did not. v2 removes the anchor instead of owning it.

| # | Decision |
|---|---|
| V1 | One MQTT editor at a time: an `fcntl.flock` EDIT LEASE held for the edit session; a second opener is view-only. The OS releases it on process death. |
| V2 | Reset when the file was readable at open and broke later = rebuild FROM THE SCREEN (shown first), password asked by name as Q7. File already unreadable at open → defaults, as Q15. |
| V3 | The r4/r5 reviewer scratch tests stay the red-first acceptance: setup adapted to the lease, ASSERTION kept; each must fail on `e9fcae66` and pass on v2, or be written down as impossible under v2 with the reason. |
| V4 | MQTT only; no generic multi-writer framework until the live drill passes (then a separate grill for `gateway.json` / profiles / `deployment.json`). |
| V5 | Lease = the whole MQTT configure menu session; nested screens (presets, private form, TLS menu, profile apply) REUSE it; a screen opened outside the menu takes its own. |
| V6 | Second opener: view-only, holder named (user, pid, tty, since); NO takeover; no idle expiry in F8. |
| V7 | At lease acquire, fingerprint the file; at Save, a mismatch = written without the lease → refuse loudly, write nothing, no merge. |
| V8 | DROP Q10, Q17, Q18, Q18r (merge machinery). KEEP Q3, Q5, Q6, Q7, Q8, Q15, Q16, Q19. Build from `85c99bc1`; the bundle is scenarios, not code. |
| V9 | `mqtt_settings.save()` takes the lease as an argument and REFUSES without it; a guard test plants a lease-less write and expects the refusal. stdlib only (MA byte-identity). |
| V10 | Lease file at the r3 #4 stable target (config dir's parent), via `get_real_user_home()`; a test runs a sudo-shaped holder vs a user-shaped opener; `_hand_to_dir_owner` stays UNKNOWN until the live drill. |
| V12 | Acquire is `LOCK_NB` once (immediate view-only, never a pause). The holder writes user/pid/tty/since into the lease file; an unreadable record reads "held by an unknown session", never "free". |
| V13 | Stop rule: ONE review finding of "a screen writes something it did not show" or "settings changed while the lease is held" parks the TUI half again → grill, not another fix. Message/legibility gaps are fixed in-round. |
| V14 | Order: MF first → red-first build → full suite → non-author review with a NEUTRAL brief (find a write the screen did not show / a change under a held lease — never "confirm the lease cures it") → fixes → deploy + live drill NEXT session (brake) → MA byte copy + TUI twin, porting MA's test-isolation gate first. |

**Acceptance (V11 — replaces Q14, agreed before code):** On the manager box,
with two TUI sessions open, the first enters MQTT configure and the second is
shown view-only, naming the holder. The first saves a change; the second,
after the first leaves, opens and sees that change, and saves its own; both
changes are on disk. A write attempted without the lease is refused and
writes nothing. Changing the broker never carries or silently clears a
password — the operator is asked first. Esc never writes.

**Closed as moot by V1/V7/V8:** edit-anchor ownership; validation-before-
conflict order (no conflict check remains). Model confidence in V1 is
BELIEVED — the same reasoning produced the Q18 advice; the reviewer brief
must not carry it.

### F8 v2 build — 2026-11-03 (Opus 5.5) — BUILT + reviewed r1; NOT deployed (V14 brake)

- MF `75543842` (build) + `038c768d` (review r1 fixes); MA `ce853c9f` + `cd690b24`
  (byte copy + INTERIM per-save lease binding — MA behaviour unchanged; the
  stale-screen class stays OPEN in MA until its TUI twin holds the lease).
- Evidence: full suite rc 0 (17512 passed) on the r1 tree; MA suite rc 0
  (9318); 15/15 mutants caught on the build; CI success for `75543842`
  (run 41689112766); live sudo drill A/B for r1 #1 (old: dir uid=0, operator
  save refused; new: uid=1000, saved).
- Review r1 (Fable, contextless, by running): NO V13-class finding (searched,
  not proven absent). Fixed in-round: #1 root-created config dir, #2 Esc in
  the private form kept list edits, #3 unreadable-file Q19 ask, #5 handback
  witness, #6 log wording, #7 half-built lease. LEFT: #4 a post-replace chmod
  error reads "NOT saved" while the file changed (exotic fs only).
- Interpretation recorded (V2 x V7): under a lease, "readable at open, broke
  later" can only be a lease-less writer; Save then offers the V2 rebuild only
  when the file NO LONGER LOADS, and refuses (reopen) when it is readable.
- NEXT: (1) non-author r2 on `038c768d` (fixes are reviewer-unseen); (2)
  deploy (fleet_sync then fleet_pull) + the V11 live drill with two real
  whiptail sessions on the manager box — operator; (3) MA TUI twin (V14).

### F8 v2 review r2 — 2026-11-03 12:44 HST (Fable, contextless, by running) — on `75543842^..038c768d`

Scratch: `/tmp/claude-1000/f8v2-review2/test_r2.py` (6 reproductions). NOT fixed this
session (V13 + the same-session brake). **OPERATOR RULING OWED on F1 before deploy.**
- **F1 MEDIUM — V13 candidate:** profile apply over an UNREADABLE file -> "Reset now?"
  writes auto_start/auto_start_telemetry from DEFAULTS, never shown (apply has no
  settings screen). Author's recommendation: not a park — one grill question: Reset is
  offered only from a screen that SHOWS the settings; profile apply over unreadable
  refuses and points to MQTT Configure.
- F2 LOW: a preset writes an Auto-Start toggle the toggle dialog said was unwritten.
- F3 LOW (root, mock): a failed dir handback says "written" when nothing was.
- F4 LOW: Reset after a break asks for "the saved password" when none was saved
  (`password_saved` False known at open).
- F5 LOW: Q19 dialog names the account at open, not the live file's.
- F6 LOW pre-existing: a root-owned config dir left by an earlier root save -> root
  save reports ok, file stays root 0600 (subscriber locked out). Pre-deploy check on
  the manager box: `stat -c '%U %a' ~/.config/meshforge ~/.config/meshforge/mqtt_nodeless.json`.
- F7 LOW: `test_q15_unreadable_at_open_save_asks_for_reset_then_writes` passes through
  the fake inputbox default; pins neither the password ask nor the answer.
- Held: no stale-screen write, no plaintext, holder always named, no fd leak, every
  save path refuses a lease-less change.
