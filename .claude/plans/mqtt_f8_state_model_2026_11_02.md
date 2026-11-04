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

### F8 v2 review r5 (narrow) — 2026-11-03 (Fable 5.1 SUBAGENT, by running) on `cdf86fe4` + write table re-derived — STOPPED (agreed rule)

Scratch: `/tmp/claude-1000/f8v2-review5/test_r5.py` (37 tests; author re-run: 7 failed / 30
passed, VERIFIED). **No defect in the lines cdf86fe4 added.** Write table: every path × field
is carried/typed/shown except ONE cell (#2). Class 2 NOT found; no plaintext.
- **#1 MEDIUM, V13-class by the letter (Q5 "Esc never writes")** — `_setup_custom_broker`
  (`broker.py:426-445`): Esc at Username/Password/Region/Channel/Root Topic reads as the
  default (`inputbox(...) or "US"`), so the profile is saved+activated and the subscriber
  file written — Esc on every remaining prompt still ends in "Profile Applied". Pre-existing
  (`3b0c5bf4`, 2026-02-28); public/private setup flows guard each prompt and are clean.
  Fix shape: `None` = abandon (return, nothing written) at every prompt, as the sibling flows do.
- #2 LOW — profile apply over NO saved password writes the profile's password with no
  question, and the summary names the account but not the password STATE. Fix: one summary
  line `Password: set / none` (never the value).
- **Operator ruling owed** (stop rule). NOT deployed; fleet still on `4bc60490`.

### r5 fixes — 2026-11-03 19:21 HST (Opus 5.5) — operator: "fix the Esc-still-saves bug … your call"

- #1 `_setup_custom_broker`: Esc (`None`) at Username/Password/Region/Channel/Root Topic
  abandons the flow — no profile saved, nothing written; a BLANK answer still takes the
  default (control test). #2 every shown save summary now names the password STATE
  (`kept (saved)` / `set` / `none`), never the value.
- Red-first: 7 tests failed on `f232d150`; Fable's r5 scratch now 37/37 pass.
- NOT deployed (brake). NEXT SESSION (fresh context, by the author's call): a narrow
  non-author read of this commit, then deploy (fleet_sync THEN fleet_pull) + live checks:
  a preset AND a profile apply over a custom key; Esc in Custom setup writes nothing.

### F8 v2 review r5b (narrow, non-author) — 2026-11-03 20:23–21:15 HST (Fable 5.1 SUBAGENT `claude-fable-5-1`, by running) on `1006c842` — CLEAN on the r5 lines; DEPLOYED

Scratch (persisted): `~/.local/share/meshforge/reviews/2026-11-03-f8v2/r5b/` (+SHA256SUMS);
61 tests, author re-run `1 failed, 60 passed` (the 1 = deliberate probe for #1). Clean: Esc at
every prompt of custom/public/private setup writes nothing (incl. WITH a saved password);
whiptail inputbox measured via pty (Esc/Cancel -> None, blank -> ""); `Password:` line vs disk
across 16 paths; no password leak in any settings-file flow. UNKNOWN: `dialog` backend (not
installed), root-only paths. All four findings PRE-EXISTING:
- #1 LOW, V13 by the letter (PROFILE store, not settings): Custom "Saved" box omits the
  profile's `username` + derived `use_tls=(port==8883)`; the only box shown when apply is refused.
- #2 LOW: same-name custom setup silently replaces a profile, says "created".
- #3 LOW, Q3-adjacent: private setup + Radio MQTT Setup show plaintext password in the radio
  commands (the latter = the STORED profile password).
- #4 LOW UX: public/private `if not x: return` — blank abandons instead of taking the default.
- **Operator ruling (this session, before the 20:48 deploy t0): "Deploy now, fix #1 next."** Deployed `8ed1700a` (r5 + prune flap
  fix): fleet_sync 169 ok/0 failed -> fleet_pull 10/10 -> honest_status 9/9 PASS exit 0.
- NEXT: #1–#4 as one red-first round + non-author read; live checks (Esc in Custom setup;
  Local preset + profile apply over a custom key) owed — operator's terminal.

### r6 — r5b #1–#4 fixed (Opus 5.5, author) — operator rulings: #3 placeholder + explicit reveal; #4 Esc abandons, BLANK takes the value shown

- #1 Custom "Saved" box names TLS (from port), Account, Password state. #2 Custom + Private
  ask "Replace Profile?" when the name exists (No/Esc = nothing written); every setup box says
  created/replaced. #3 `get_meshtastic_mqtt_setup_commands(reveal_password=False)` masks as
  `<MQTT_PASSWORD>`; Radio MQTT Setup asks "Show Password?" (default No); argv SSOT unchanged.
  #4 every setup prompt goes through `BrokerHandler._ask` (None = abandon, blank/whitespace =
  the shown init); Custom's password stays raw (blank = no password).
- Red-first: `tests/test_broker_setup_r6.py` 14 failed / 11 passed (controls) on the old code.
  `test_broker_profiles::test_private_broker_commands` pinned the plaintext display -> updated
  to the #3 ruling. r5b scratch: the 6 old-behaviour probes now fail, as they should.
- NOT deployed: owes a non-author read.

### FOUND during r6 — profile-store WIPE (V13-class, pre-existing `93727ab6` 2026-02-09) — stop rule: GRILL owed

VERIFIED by running (`scratchpad/r6probe/test_wipe.py`): `load_profiles()` maps an UNREADABLE
`broker_profiles.json` (truncated JSON / OSError) to `{}` (hfm #1), so the next setup flow
saves `{new}` — every other profile and its password destroyed, no backup, success box shown
("Public Broker Set", "Profile Applied"). 6 `save_profiles` call sites; MA has a copy.
Candidate shape (to grill, not decided): Q15's — tri-state load, refuse save over unreadable,
explicit Reset keeping a `.unreadable.<n>` backup. Operator: "commit r6, then grill the wipe".

### r7 — the profile-store WIPE fixed (Opus 5.5, author) — operator 2026-11-03: "your recs on all three - go"

Rulings (all three recs): (1) a profile on disk that does not parse is CARRIED THROUGH a
save byte-for-byte (never seen = never deleted; its `is_active` is cleared when another
profile is active); (2) an UNREADABLE store (bad JSON, zero-byte, wrong shape, OSError)
refuses every save — file untouched — and the Broker menu shows `⚠ PROFILE STORE
UNREADABLE` + a `Reset Profile Store` item that renames it `.unreadable.<n>` (never
deleted, Yes required); (3) writes go through `utils.paths.atomic_write_text` (unique temp,
fsync, 0600 — the old fixed `.tmp` + no fsync is how a power cut makes the zero-byte corpse),
and `load_profiles` tightens an existing group/world-readable store to 600 (live VolcanoAI
store measured `664` 2026-11-03).
- Also: `ensure_default_profiles` no longer shows UNSAVED defaults over an unreadable store
  (the menu called it every pass — it would have overwritten the store just by opening);
  a wrong-shape file no longer CRASHES the load (`AttributeError` on a list); every
  "Save Failed" body names the unreadable state and the Reset.
- API: `ProfileStoreStatus` (`absent`/`ok`/`unreadable` + `skipped`), `profile_store_status()`,
  `reset_unreadable_profile_store()`. The 16 call sites are unchanged — the cure is at the
  WRITE boundary.
- Red-first: `tests/test_broker_profile_store_r7.py` 18 failed / 2 passed (controls) on
  `ce203a13` (`git archive` copy, isolated HOME); 20/20 after.
- Known limit: read-then-write is not locked (a second writer between the read and the
  rename could still be overwritten) — single-writer TUI, accepted; name it if a daemon
  ever writes this store.
- OWED: non-author read; MeshAnchor twin (port its test-isolation gate first); deploy
  + live check (plant a zero-byte store on one box → menu says UNREADABLE, setup refuses,
  Reset keeps the backup; check the store's mode is 600 after the first open).

### F8 v2 review r7 (non-author, Fable 5.1 SUBAGENT, contextless, by running) on `ce203a13` + `196eb885` — STOPPED (V13: data loss without a Yes, introduced by r7)

Scratch (persisted): `~/.local/share/meshforge/reviews/2026-11-03-f8v2/r7/` (+SHA256SUMS, sums OK);
43 tests, reviewer 3×`43 passed`; author re-ran F1/F2/F6a — reproduce. Clean: Esc at every prompt
of all 3 setup flows; Replace No/Esc; refused save over unreadable (all flows); password reveal;
dir-as-store; zero-byte -> no defaults.
- **F1 HIGH, r7** — same-name setup over an UNPARSED profile destroys it with no "Replace
  Profile?" and says "created" (`_confirm_replace` sees parsed only; save drops carried-by-name).
- **F6 MEDIUM, r7** — (a) PermissionError folded into `unreadable` -> Reset offered -> a HEALTHY
  store moved aside, defaults written; (b) BELIEVED: sudo TUI writes root:root 0600 (no
  `chown_to_operator`), so a no-sudo TUI then meets (a).
- **F2 MEDIUM, pre-existing, made common by r7** — Private setup installs mosquitto passwd for a
  profile whose save was REFUSED ("Install Config?" asked before "Save Failed").
- F4 MED pre-existing (stale dict from another TUI instance destroys its profile, no
  changed-since-read check); F5 MED (defaults listed though the write failed); F7 LOW (unreadable
  store + its backup keep 0664); F8 LOW (unknown top-level keys dropped); F9 LOW (Public replaces
  silently); F10/F11 LOW (unparsed active / unreadable store read as "none"/"(0)" in sub-screens);
  F3 LOW (backend strips passwords; FakeDialog does not — fidelity gap); F12 LOW (profile named
  `back` unreachable).
- **Author finding — TEST LEAK into the live store (pre-existing, exposed by r7):**
  `test_broker_honest_signal.py`, `test_broker_input_validation.py`, `test_mqtt_tui_intent.py`
  call the real `load_profiles()` on the operator's home (canary HOME: 664 -> 600 for those 3
  only). The r7 full suite tightened VolcanoAI's real store 664 -> 600 at 22:03:41 (ctime;
  content + mtime 2026-03-13 unchanged — no write). Pre-r7, the same leak could WRITE defaults
  into an empty real store. Gate owed: autouse conftest redirect of `_get_profiles_path`.
- Fleet still on `8ed1700a` (r6/r7 NOT deployed). **Operator ruling owed.**

### r8 — review r7 fixes (Opus 5.5, author) — operator 2026-11-03: "r8 as listed - go, keep the file at 600"

- F1 `_confirm_replace` also sees profiles that did not LOAD ("on disk but could not be
  loaded … Replace it?"; No/Esc = nothing written); the box says replaced, never created.
- F6 new state `denied` (PermissionError — the file may be healthy): saves refuse, Reset is
  NEVER offered; the screen names owner+mode and the in-app remedy — an Admin-mode open calls
  `chown_to_operator` (load AND save), so a sudo-written store goes back to the operator.
  (First wording sent the operator to a shell — MF018 refused it; fixed in-app.)
- F2 Private setup offers the mosquitto install only after the profile SAVED.
- F5 `ensure_default_profiles` lists defaults only if their save succeeded; screens say
  "Default profiles could not be saved — nothing is on disk".
- F7 mode tightened on an unreadable store too, and on the Reset backup.
- F9 Public setup asks "Replace Profile?" (r6 test updated to answer Yes).
- F10 an active profile that did not load is NAMED (Radio MQTT Setup, Test Connection), and an
  unreadable/denied store is named there too — never "no profile is active".
- F11 the Profiles sub-menu carries the same store note as the parent.
- Leak gate: autouse `conftest._isolate_broker_profile_store` (both aliases, imported in the
  fixture). Drill: the 3 leaking files vs a 664 decoy HOME — were 600, now 664 (VERIFIED).
- Red-first: `tests/test_broker_profile_store_r8.py` 13 failed / 1 passed (control) on
  `196eb885` (`git archive`, isolated HOME); 14/14 after.
- Deferred (as ruled): F4 changed-since-read (own round); F8, F3, F12.
- VolcanoAI's real store left at 600 (operator).
- OWED: non-author read of r8; then deploy NEXT session (brake) + live drill; MA twin.

### F8 v2 review r8 (non-author, Fable 5.1 SUBAGENT, contextless, by running) on `5a555e71` — STOPPED (V13 by the letter: #1 pre-existing, #2 r8)

Scratch (persisted): `~/.local/share/meshforge/reviews/2026-11-03-f8v2/r8/` (+SHA256SUMS, sums OK);
reviewer 2× `34 passed, 10 skipped`; author re-ran the 9 FINDING tests — all reproduce. Clean:
Esc at every prompt + every Replace (24 cases), post-save Install Esc, denied store (no Reset),
zero-byte refusal, carry-through, F1/F9. Store gate holds (24-file audit: no test reached it).
- **#1 MED, V13, pre-existing** — `set_active_profile` flips `is_active` in the caller's dict
  BEFORE the save; a refused save leaves the detail screen saying "Active: Yes" (disk unchanged).
- **#2 MED, V13, r8** — `active_profile` derived from LOADED profiles only: deleting a loaded
  profile while the active one is unparsed writes `active_profile: null` and undoes F10.
- #3 MED r8 — defaults write fails over an `ok` EMPTY store -> "Broker Profiles (0)", no ⚠.
- #4 LOW/MED — untraversable config dir: `path.exists()` raises PermissionError outside the try
  -> generic safe_call box; the `denied` state is bypassed.
- #5 LOW r8 — main menu still "Active: None" for an unloaded active profile.
- #6 LOW pre-existing — symlinked store: replace swaps the link for a file, target keeps old bytes.
- #7 LOW r8 — Reset of a DIRECTORY store chmods the backup dir 0600 (owner can't enter).
- #8 LOW, other class — 12 suite tests read the real `~/.config/meshforge/` (lint operator
  patterns via `scripts/lint.py:698`, `fleet.json`) — read-only, verdict-affecting per box.
- Note (BELIEVED): `chown_to_operator` keys on SUDO_USER only — a pkexec launch would not chown.
- CI: `5a555e71` FAILED (4 r8 Private tests unpinned vs runner without mosquitto; 2 reached the
  REAL installer) -> `c43d3d2e` pins it (reproduced CI's 4 locally; 227/227 with mosquitto forced
  missing). **Operator ruling owed.** Fleet still on `8ed1700a`.

### r9 — review r8 fixes #1–#7 (Opus 5.5, author) — operator 2026-11-04: "through R9 now then review as you advise"

- #1 `set_active_profile` stages the flags on COPIES (`dataclasses.replace`); the caller's
  dict — what the detail screen renders — changes only after the save succeeded.
- #2 `active_profile` is derived over loaded AND carried profiles: an unloaded active
  profile stays active through a delete (F10 holds).
- #3 an empty list after `ensure_default_profiles` (no skipped) always reads
  "Default profiles could not be saved — none are on disk", whatever the store state.
- #4 `path.exists()` moved inside the try: an untraversable config dir is `denied`, the
  Broker Manager opens and names it, no Reset.
- #5 main menu: "Active: ⚠ <name> (did not load)" instead of "None".
- #6 a symlinked store is written THROUGH the link (resolved target; link kept).
- #7 `_tighten_mode` gives a DIRECTORY 0700 (a Reset backup dir stays enterable).
- Red-first: `tests/test_broker_profile_store_r9.py` 8 failed / 1 passed (control) on
  `c43d3d2e` (`git archive`, isolated HOME); 9/9 after. 236/236 broker/MQTT tests pass,
  also with mosquitto forced missing. Its autouse fixture pins mosquitto from the start.
- Queued as their own items (NOT r9): #8 suite reads of real `~/.config/meshforge/`
  (lint operator patterns, fleet.json); pkexec has no SUDO_USER so `chown_to_operator`
  would not hand back (shared util); F4 changed-since-read.
- OWED: non-author read of r9; deploy NEXT session (brake) + live drill; MA twin.

### F8 v2 review r9 (non-author, Fable 5.1 SUBAGENT, contextless, by running) on `53119ae5` — STOPPED (V13: F2/F4 pre-existing)

Scratch (persisted): `~/.local/share/meshforge/reviews/2026-11-03-f8v2/r9/` (+SHA256SUMS, OK);
reviewer 2× `18 passed`; author re-ran — all 9 `test_F*` reproduce. Clean: r9 #1/#2/#4/#5/#6/#7
(locked dir, symlink to unwritable dir, link-through tighten/Reset, Esc ×8, wrong-shape ×5).
- F1 MED r9 — after the operator deletes every profile, "Default profiles could not be saved"
  over a healthy store (r9 #3 INFERRED failure from an empty list).
- **F2 MED V13 pre-existing** — the next main-menu render silently RE-CREATES deleted defaults
  (new random private password, no dialog). Same on first run: opening the menu writes a
  store + password nobody was shown.
- F3 MED pre-existing — every save drops unknown TOP-LEVEL keys (carry covers profiles only).
- **F4 MED V13 pre-existing** — a JSON object with no `profiles` key reads `ok`/empty and the
  first menu render overwrites it with defaults, no question.
- F5 LOW/MED — active stored twice (flags + key); reader trusts flags; key rewritten silently.
- F6 LOW — no schema version: a newer-shape store reads "UNREADABLE … wrong shape" + Reset.
- F7 LOW — profile named `back`; F8 LOW — `mosquitto_pub -P <pw>` in argv; F9 LOW — "Show
  Radio Setup Commands" runs the apply flow (gated by propose_remediation; legibility).
- Author: cleaned 2 undeletable `/tmp/pytest-of-wh6gxz/garbage-*` 0600 dirs left by the r9 #7
  red-first run on the OLD code (chmod u+rwx then find -delete; verified gone).
- ROOT of F1/F2/F4 (and r7 F5, r8 #3): `ensure_default_profiles` AUTO-WRITES on render.
  **Operator ruling owed.** Fleet still on `8ed1700a`; r6–r9 pushed, not deployed.

## F8 v2 r10 + review r10 + r10b (2026-11-04)

- **r10 `b64f3a93`** (operator "Yes, go I'll follow your advice"): the
  defaults auto-write is removed — screens load only; "Create Default
  Profiles" is an explicit Yes/No item (r9 F1/F2); a JSON object with no
  `profiles` key is `unreadable` (r9 F4); unknown top-level keys carried
  (r9 F3). CI: first run CANCELLED at the 30-min job cap (every test ran,
  no FAILURES; the `-rA` PASSES print crossed it), rerun SUCCESS 28m11s.
  ⚠️ queued: the CI cap has ~2 min headroom — per-test timeout from a
  `--durations` measurement, not a bigger cap.
- **Review r10** (Fable 5.1 subagent, by running; 37/37 ×2,
  `~/.local/share/meshforge/reviews/2026-11-03-f8v2/r10/`): no V13, no data
  loss. F1 MED a refused Create said "nothing written" AND "Could not
  write"; F2 LOW a profile named `defaults` opened Create; F3 LOW the
  Profiles screen named setup items it lacks; F4 LOW (r8 F7, deliberate)
  wording — opening a store **writes no BYTES**, it may still tighten the
  file mode to 600 / chown it back (logged, not shown); F5 LOW a non-string
  `active_profile` is replaced on save (→ store-format round).
- **r10b**: F1 (one shared `DEFAULTS_WRITE_FAILED` constant; write-failure
  text only when a write was attempted or the store is unreadable/denied),
  F2 (a profile's own name wins over the `defaults` tag, which is offered
  only over an empty store), F3 (per-screen note). Red-first 3 failed / 1
  control passed on `b64f3a93`; the reviewer's F1–F3 now fail (cured),
  its C1–C15 + F4/F5 still pass.
- **Owed (fresh session):** a narrow non-author read of r10b → deploy
  (fleet_sync THEN fleet_pull, honest_status exit 0) → live drill → MA twin
  (port its test-isolation gate first). Queued: store-format round (r9
  F5/F6, r10 F5, old F4 changed-since-read); F7–F9 deferred.

## F8 v2 review r10b + deploy + live drill (2026-11-04, fresh session)

- **Review r10b** (Fable 5.1 subagent, contextless brief, by running;
  `~/.local/share/meshforge/reviews/2026-11-03-f8v2/r10b/` + SHA256SUMS):
  F1–F3 claims HOLD (each FAIL on `b64f3a93`, PASS on `9b0b934a`); 26/26 on
  head, 20/6 on parent (6 = expected flips); repo broker/MQTT files 775
  passed; real store sha256/mtime identical before/after (isolation proven).
  **Stop rule hit, PRE-EXISTING:** `create_default_profiles` TOCTOU — empty
  check (`broker_profiles.py:940`) then `save_profiles` (carries only
  UNPARSED entries) overwrites a PARSED profile another session saved in
  between; the screen never names it. No lease covers this file (grep).
  Operator ruled (11-04): deploy (the fleet's `8ed1700a` auto-wrote defaults
  with no Yes — a larger exposure), fix the race next round.
- **Deploy:** CI success `23c15548` (41892283304); `fleet_sync` rc=0 (10
  hosts ok, 169 actions ok, t0 09:20:40) THEN `fleet_pull` rc=0 (10/10 @
  `23c15548`); honest_status rc=0, 7/9 PASS + 2 pre-existing WARN (VolcanoAI cron_verdict_stale = pytest_tmp_prune; 1 dep advisory); suite 17638 passed on 23c15548.
- **Live drill, moc4 (operator at the TUI, files read from OUTSIDE after
  each step):** start = no store. A: open Broker Manager → nothing written
  (only the TUI log); Create → **No** ×2 (09:41:29, 09:43:04) → nothing
  written, log "Dialog exited 1"; **Yes** (09:38:51, run first, then A.1/A.2
  re-run over a cleared store) → one write, 2 profiles, mode 600, log
  "operator confirmed". B: 0-byte store (mode 600) → screen UNREADABLE with
  the JSONDecodeError, Create not offered, "Saves are refused"; a Use Public
  Broker save at 09:47:24 refused (log witness; refusal screen not seen by
  the author); Reset (09:48:04) → original kept as `.unreadable.1` (0 B,
  600, rename), store absent. **PASS.** Restored: no store; evidence in
  moc4 `~/.local/share/meshforge/drill-f8v2-20261104/` (700).
- **Next round (red-first, then a non-author read):** (1) the defaults
  TOCTOU — save refuses unless the store is still empty (e.g.
  `save_profiles(..., expect_empty=True)`); (2) a profile named `back`
  cannot be opened (`broker.py:265` checks `back` before the lookup — the
  F2 twin); (3) the success text hardcodes the two default names beside
  `DEFAULT_PROFILE_NAMES` (hfm #5); (4) LOW wording: after Reset the screen
  says "store is now empty" while the file is absent. Then the MA twin
  (port its test-isolation gate first) and the store-format round.
