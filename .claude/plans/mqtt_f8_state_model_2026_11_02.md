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
| V15 | (r3 #1, 2026-11-03, operator "your call - i lean to fix"; author ruled) A Local/Public preset KEEPS the saved channel key (no screen row shows it, so replacing it was a write nobody saw) and every shown save summary names the key's KIND (default / custom (kept) / custom) and the account written - never the PSK. |
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

### F1 fix — 2026-11-03 (Opus 5.5) — operator ruling: Reset only from a screen that SHOWS the settings

- `08e5d963`: profile apply over an unreadable file (at open, or broken mid-apply)
  refuses, writes nothing, points to MQTT Configuration. Red-first (2 tests failed on
  the old code), both halves sabotage-drilled, full suite rc 0 (17523 passed).
- Non-author review (`code-review medium 39fcdd00..08e5d963`, contextless fork):
  8 findings, NO V13-class (all legibility) -> fixed in-round:
  #1/#2 a NEWER-schema file loads `unreadable` -> the refusal advised Reset of a valid
  file; now apply skips Q19 over a non-loading file and lets `save()` classify
  (`newer` before `unreadable`, neither writes); #3 STALE text named a Configuration
  screen profile apply doesn't have + dropped `fail_note`; #4 refusal claimed a
  backup that this path never makes; #5 "was saved but NOT applied" on Apply from the
  menu (nothing saved); #6 refusal implemented twice -> one path; #7
  `settings_screen=False` is now the SAFE default, the 3 Configure sites opt in;
  #8 tests pin the branch (title + classifying phrase), not substrings.
  4 F1 tests fail on `08e5d963`; default-flip mutant caught (3 red).
- Reviewer r2 scratch P1 (`/tmp/claude-1000/f8v2-review2/test_r2.py`) now fails BY
  DESIGN: it asserted the apply writes and shows auto_start; the ruling is refuse.
- NOT deployed (brake: no same-session self-deploy of a review fix). NEXT: a
  non-author read of the review-fix commit, then deploy (fleet_sync -> fleet_pull).
- `mqtt_settings.py` untouched (MA byte parity holds); the TUI half reaches MA with
  the V14 TUI twin.

### r2 LOWs — 2026-11-03 (Opus 5.5)

- FIXED (TUI/tests only, red-first — each new test failed on `f2ab6a2c`):
  F2 every save summary names the auto-start it wrote; the toggles say "on the next
  save ('Save & Exit', or a Local/Public preset)". F4 rebuild after a break never
  claims a saved password the screen knows was absent ("No password was saved for
  user@host…"). F5 Q19 names the account in the file NOW. F7 the Q15 Reset test pins
  the password ask + the typed answer. V11 nit: "Changed Elsewhere" says nothing-written
  once.
- DEFERRED to the V14 MA-twin round (both in `mqtt_settings.py`, MA byte parity —
  a fix now forces an MA byte copy in the same push): F3 handback text says "written"
  when nothing was; F6 root-owned config dir -> root save leaves the file root 0600.
  F6 measured CLEAN on the manager box (`~/.config/meshforge` wh6gxz 700, file 600).
- Reviewer scratch now: P1 + P2 fail BY DESIGN (P1 = F1 ruling; P2 pins the old toggle
  text), P3/P9 = deferred F3/F6, the rest pass.
- NOT deployed. NEXT: one non-author read covering `07e1b492` + this commit, then deploy.

### F8 v2 review r3 — 2026-11-03 (Fable 5.1 SUBAGENT, contextless, by running) on `39fcdd00..b1d3514f`

Scratch: `/tmp/claude-1000/f8v2-review3/test_r3.py` (8 reproductions, 8 controls/held).
Re-run by the author: 8 failed / 8 passed (VERIFIED). Class 2 (change under a held
lease) NOT found; no plaintext; no unsafe default.
- **#1 MEDIUM, V13-class by the record's letter** — Local/Public presets reset a saved
  custom channel `key` to `AQ==`, shown nowhere; the decoder consumes it. PRE-EXISTING
  since `75543842` (deployed). V13 fired; operator: "your call - i lean to fix" ->
  author ruled option 2 (one grill question, then fix): **V15** above. Fixed.
- #1b public preset summary now names the account it writes. #4 the F4 fix missed the
  ABSENT-at-open branch — fixed. #5 Authentication row read "(none)" over an unreadable
  file -> "(unknown — saved file unreadable)". #6 declined Q19 on profile apply now logs.
- DEFERRED to the V14 MA-twin round with F3/F6: #2 Configure calls a NEWER-schema file
  "broken" + offers a rebuild/backup it cannot do; #3 subtitle promises a Reset a newer
  file never gets. Nothing is written in either (save() refuses `newer`); telling newer
  from damaged needs a signal from `mqtt_settings.py` (MA byte parity), not error-text
  matching in the TUI (hfm #5).
- #7 RULED not V13 (author): profile apply over an ABSENT file writes the default
  auto-start values — identical to what every reader already applies; no behaviour change.
- NOT deployed. NEXT: Fable r4 on the r3 fix commit, then deploy (fleet_sync -> fleet_pull).

### F8 v2 review r4 — 2026-11-03 (Fable 5.1 SUBAGENT, contextless, by running) on `e3f89a31` + a field-by-field write sweep — STOPPED (agreed rule: V13-class -> nothing fixed tonight, operator rules)

Scratch: `/tmp/claude-1000/f8v2-review4/test_r4.py` (24 tests; re-run by the author: 6 failed /
18 passed, VERIFIED). Class 2 (change under a held lease) NOT found — every Configure action,
Esc and declined question leaves the file byte-identical; one writer in src/+scripts/. No PSK
or password plaintext in any dialog/log across 24 scenarios. Part A (e3f89a31 itself): only #4.
- **#1 MEDIUM, V13-class** — broker PROFILE APPLY replaces a saved custom channel key with
  `AQ==` (`broker_profiles.py:130` `to_intent` "key": encryption_key or "AQ=="; every factory
  hardcodes AQ==; the profile detail screen never shows a key; apply shows no result).
  Pre-existing since `0cfef4d7` (11-01). V15 covered the presets only — the "grep EVERY
  branch" tell AGAIN. Fix shape = V15 for apply (keep the saved key unless the profile
  carries an operator-set key), or apply shows the save summary.
- **#2 LOW/MED, V13 by the letter** — `_setup_public_broker` auto-applies to the subscriber
  (meshdev account, public password, root, key, tls auto) with `report_success=False`: the
  account written is never shown. Fix shape: profile apply shows the presets' summary.
- #3 LOW — every `_setup_*` flow applies to the subscriber even after "Save Failed … profile
  NOT saved". #4 LOW (introduced by e3f89a31) — the absent-at-open prompt says "(the file no
  longer loads)" for a file that never existed. #5 LOW — summaries print effective TLS on/off,
  not the stored tri-state (`auto`).
- Out of scope, noted: the private-broker "Setup Complete" box shows the NEW generated password
  inside the radio setup commands (intentional radio-setup block).
- **Operator ruling owed** before any fix: (a) #1/#2 by V15's shape (keep key; apply shows the
  summary) as one in-round fix, or (b) park the profile-apply half and grill. NOT deployed;
  e3f89a31 stays pushed-not-deployed.

### r4 fixes — 2026-11-03 (Opus 5.5) — operator: "I agree fixing them the same way we did V 15"

- #1 profile apply: a default/blank profile key (every factory writes `AQ==`) KEEPS the
  saved key; only a key the profile really carries replaces it. #2 apply now SHOWS the save
  summary ("Profile Applied": account, key kind, TLS, auto-start, topics) — reverses the old
  "apply is silent on success" contract (`test_broker_honest_signal` updated, on purpose).
  #3 the three `_setup_*` flows apply only when the profile save succeeded. #4 the
  absent-at-open prompt names why ("no settings file existed then; one appeared and does not
  load"). #5 summaries show the stored TLS mode ("TLS: off (auto)").
- Red-first: 8 tests failed on `0ef3a668`. r4 scratch: b2b/b7 still fail BY DESIGN (b2b wants
  the literal "TLS: auto"; b7 pinned the silent success the ruling replaced).
- NOT deployed. NEXT: Fable r5, NARROW (this commit + re-run the r4 write table), same stop
  rule; then deploy next session + live check a preset AND a profile apply over a custom key.
