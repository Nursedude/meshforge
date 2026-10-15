# NOC read grant + `box_truth/v1` export — design spec (grilled 2026-10-13)

> Status: **DESIGN AGREED** (operator, 2026-10-13, Q1–Q18 all on the
> recommendation). Nothing built. Sessions 2 and 3 below build it.
> **Security pass DONE 2026-10-14 (Fable 5.1, gate 10):**
> `.claude/audits/noc_read_grant_security_pass_2026_10_14.md` — 8 blocking
> amendments B1–B8 + 12 should-fix S1–S12, folded into the "Security-pass
> amendments" section at the end of this file. Session 2 builds to the
> AMENDED spec and the AMENDED acceptance list there, not to Q14 alone.
> **Session 2 BUILT 2026-10-14 (Opus 5.5), NOT DEPLOYED, NO GRANT WRITTEN
> ANYWHERE:** `utils/box_truth{,_collect,_reader,_grants,_center}.py`,
> `scripts/box_truth_{export,grant,grant_drill}.py`, spool writer refactored
> onto the export, collector renders the S1 outcome, guards S9/S11/B7a.
> Live drill against a private sshd: 14/14 legs ×2 runs (A3, A5 incl. `-tt`,
> A9, B4, B8). Deviations: (1) the three payload helpers + `judge_spooled_schedules`
> did NOT move into the shared module — they import MF's watchdog probes, so a
> byte-identical module cannot carry them; that move is session 3's, with the MA
> port. (2) S2 cannot generate known_hosts from the registry (it stores
> FINGERPRINTS): the reader pins identity per read instead — fresh known_hosts,
> learned key compared to `expect_hostkey`, data discarded on mismatch.
> (3) Outcome vocabulary grew `identity_unpinned` + `rate_limited`. (4) Q12
> cron: no installer manages crontabs, so `push` ensures the spool cron line.
> (5) The writer moves PER TARGET: only `<alias> noc` lines read via the export;
> unflagged lines keep the legacy shell read byte-for-byte until migrated
> (switching all at once blinded 4 boxes for ~15 min on 10-14 — touch log).
> Delete the legacy block when no unflagged target is left.
> Reviewed 2026-10-14 by two contextless reviewers (A security, B consumer path):
> 0 blocking, all should-fix findings fixed test-first in `e9b9f513..40abc477`
> (drill now 16/16). OWED before deploy: a non-author pass on those FIXES
> (queue row "review of the REVIEW FIXES"). Then
> Q16 migration per box (S12 order, `push <box> --confirm <box>`, then
> `dedupe --apply` on moc3) — operator-consented, touch-logged.
> Origin: MeshAnchor `/fleet` read DARK (3/8). Four stale peer IPs fixed the
> same night (7/8); the eighth, moc3, is map-less and MA has no way to read a
> box without HTTP. MF reaches it only through a hand-rolled, UNRESTRICTED
> ssh spool. Operator: *"this kind of functionality can be meaningful for
> some users"*; *"it's not a matter of the amount of work, it's whether to do it."*

## What it is (one sentence)

Any operations center (MF or MA, standalone or fleet) can read any box running
either app — including a box with no map — once that box has granted that
center read access through a restricted ssh key; the grant is a TUI action.

## Decisions (Q → answer)

| # | Decision | Answer |
|---|---|---|
| Q1 | Direction | **Any center reads any box, either app.** One contract, MF leads, MA follows. |
| Q2 | What is exported | **`box_truth/v1`** — ONE versioned JSON document the box writes about itself, carrying today's 10 spool sections (slo, status, raw watchdog, deployment role, raw mini, radio LISTEN/USB, services, schedules + cron verdicts, clock, RNS ingress ledger). Carries `app`, `app_version`, `schema`. |
| Q3 | Pull or push | **Pull over ssh**, center owns cadence and staleness. Push over RNS = possible LATER separate leg for no-IP boxes. |
| Q4 | Where the grant is made | **Both; target-side is the base** (paste/select the center's pubkey + typed confirm). Center-side push is a shortcut when the operator already has access. Same resulting line either way. |
| Q5 | Key identity | **Dedicated per-center key** `~/.ssh/<app>_noc_ed25519`, comment `noc:<center-host>`. Never the operator's or the Claude session key. |
| Q6 | Restriction | **`restrict,command="<export>"`** — no pty, no forwarding, no other command. `from=` optional, **off by default** (09-12 renumbering is what broke MA tonight; a pinned key fails the same way). |
| Q7 | Visibility | **Target TUI lists grants** (center, fingerprint, last read) + revoke; **center page** shows "read via grant, last read N min ago". |
| Q8 | TUI doctrine | **Second acting carve-out** (after fleet_power): typed confirm, plan first, ONE CLI backs it, logged. |
| Q9 | Where export lives | **One shared module, byte-identical in both repos** (`parity_check` tier), thin entry at `/opt/<app>/scripts/box_truth_export.py`. A box with both apps → one document, one section per app; MF entry primary. |
| Q10 | HTTP vs grant | **HTTP first, grant fallback, fresh-only** (today's MF semantics). Stale reads DARK, never last-known-healthy. |
| Q11 | Target registration | **The center-side grant writes the target entry** (MF `truth_spool_targets`; MA `fleet.json` peer `ssh` field) and revoke ~~removes it~~ **leaves it listed UNFLAGGED** (amended 2026-10-14, operator: a removed line made the writer skip the box, so `/fleet` read a just-revoked box healthy for up to `SPOOL_STALE_S`=360 s — kiai drill 11:46; now DARK with `refused_grant` + the push named on the next tick; delete the line by hand to stop watching a box). Listed-but-refused reads **"grant missing or refused"** with the fix named, never bare DARK (hfm #4: reader/writer wire together). |
| Q12 | Spool writer | **Shared writer**, cron `*/2`, installed by installer + `update.sh` (never by hand), wired to `cron_verdict`. One writer per box even with both apps. |
| Q13 | Read witness | **Capped jsonl in the box's state dir** (~1,000 lines: center key comment + ts). sshd journal ages out in ~2 h on some boards. |
| Q14 | Acceptance | see below — written BEFORE code. |
| Q15 | Run-as | **Operator user, no sudo, no root** (measured: every read works as the operator; `watchdog.json` is 0644). |
| Q16 | Migrate MF's existing links | **Yes, session 2**: VolcanoAI's spool to kiai, moc3, moc, lehua moves to the restricted NOC key; writer passes `-i <noc key> -o IdentitiesOnly=yes` (kiai/lehua stanzas lack IdentitiesOnly). Remove moc3's DUPLICATE authorized_keys line. Operator + Claude keys stay for interactive ssh. |
| Q17 | Doctrine record | **Amend** `feedback_tui_stays_a_surface` + the fleet_posture handler docstring ("ONLY acting carve-out") to name grant/revoke as the second, same constraints. |
| Q18 | Center-side push vs local-only TUI rule | **Allowed: ONE target per action, target name typed**; bulk stays CLI-only (used for Q16 migration). |

## Facts this rests on (measured 2026-10-13, read-only)

- No fleet `authorized_keys` line carries options today. Spool links: moc/moc3/lehua
  use the Claude session key (`claude@meshforge-fleet`), kiai the operator key
  `wh6gxz@moc3` — the center's access and the session's access are the SAME key.
- OpenSSH 9.2 (moc4, bookworm) / 10.0 (all others, trixie): `restrict`,
  `command=`, `from=` all supported. Effective sshd config identical fleet-wide
  (`sshd -T`): no ForceCommand, no Match, PermitUserEnvironment no.
- Working precedent for the option syntax: VolcanoAI's `root@OpenWrt_Meshforge`
  line (`restrict,port-forwarding,permitlisten=…`).
- Today's payload exists only as the inline `_REMOTE_CMD` shell string in
  `scripts/fleet_truth_spool.py:51-151` — a forced command cannot sensibly
  allow that, which is why the export command must exist first.
- No authorized_keys management code exists in either repo.
- MA `fleet_truth.py` is byte-identical with MF; MA's collector has no spool
  leg. MA `mini_block_from_payload` is an OLDER shape than MF's (no
  future-ts / no-ts branches); MA lacks `watchdog_block_from_payload` and
  `radio_connection_from_probe`. The shared module must carry MF's versions.

## Acceptance (Q14 — the check of record for "done")

1. MA `/fleet` reads **8/8**, moc3 `resolution_method: ssh_spool`.
2. Writer stopped → moc3 **DARK within `SPOOL_STALE_S` (≤ 6 min)** and `cron_verdict` pages the cron.
3. Grant revoked on moc3 → next fetch refused → moc3 reads **"grant refused"**, not "no response".
4. moc3's TUI lists the grant with a last-read time.
5. **The control that can fail:** `ssh -i <noc key> moc3 bash` returns the export JSON, never a shell.
6. A fresh install carries **zero** grants.

## Sessions

1. **This one (10-13):** grill, this spec, ROADMAP entry, doctrine note, memory.
2. **MF build (lead repo), test-first:** shared module (export + spool reader +
   MF's three payload helpers + `judge_spooled_schedules`), export entry script,
   writer refactored onto the export, grant/revoke CLI, Q16 migration of the four
   links, installer/`update.sh` wiring. Two contextless reviewers before deploy.
3. **MA port + TUI:** byte-identical module copy + `parity_check` tier, MA
   collector spool leg + `fleet.json` `ssh` field, TUI grant/revoke/list in BOTH
   apps, deploy both. Acceptance run on moc3 from meshanchor-server.

## Versions and release gates

- App versions are independent (MF `0.6.3-beta`, MA `0.2.0-beta`). **Minor bump on
  BOTH, together, when session 3 ships** — MF `0.7.0-beta`, MA `0.3.0-beta`. Not
  before: one side alone would claim an interop it does not have.
- Interop keys on **`box_truth/v1`**, not app versions. A center receiving an
  unknown `box_truth` major refuses loudly with the version in the reason (gate 2).
- **Gate 1 (contract frozen)** gains `box_truth/v1` + the grant/revoke CLI.
- **Gate 10 (security)** gains a design-level pass on this new trust path
  BEFORE session 2 code — frontier-tier work, queued in
  `.claude/audits/review_provenance.md`, not faked on a smaller model.

## Interim (until session 3)

MA `/fleet` stays honestly DARK on moc3. meshanchor-server carries moc3's host
key pin (verified against `expect_hostkey`, touch log 2026-10-13) — no grant.

## Security-pass amendments (2026-10-14, Fable 5.1 — gate 10, binding on session 2)

Full findings, measurements and the loopback drill transcript:
`.claude/audits/noc_read_grant_security_pass_2026_10_14.md`. Each line below
is a design DECISION that session 2 codes to; each is owed a test or an
acceptance leg (named in the audit). The drill measured OpenSSH 10.0
`restrict,command=` on VolcanoAI: the forced command runs for exec, pty,
`sftp` subsystem and `-L` requests alike, receives the client's stdin and
`SSH_ORIGINAL_COMMAND` verbatim (never executed), and `AcceptEnv LC_*`
lets a client set `LC_*` variables in its environment; forwarding and agent
are refused; `from=` refuses exactly like a revoke.

| # | Decision | Answer |
|---|---|---|
| B1 | stdin / `SSH_ORIGINAL_COMMAND` | `command="/opt/<app>/scripts/box_truth_export.py --grant <fp> </dev/null"`; the export never dispatches on `SSH_ORIGINAL_COMMAND`, it records its first 120 bytes in the witness as data. Acceptance 5 covers `bash`, `-t`, `-s sftp`, stdin payload, `-A`, `-L`. |
| B2 | client env (`AcceptEnv LC_*`) | `python3 -I`; every read/write `encoding="utf-8"`, stdout reconfigured utf-8/replace; the grant plan shows `sshd -T` `acceptenv` + `permituserenvironment`. Test: `LC_ALL=C` + non-ASCII reason → valid JSON. |
| B3 | pasted pubkey = structured input | ONE shared validator: exactly one line `ssh-ed25519 <base64> [noc:<name>]`, blob decoded + wire-type checked, comment `^noc:[A-Za-z0-9._-]{1,64}$`, refuse `\r \n " ,` and any leading option. TUI shows the SHA256 fingerprint from the blob; **typed confirm = fingerprint's last 8 chars**, not the box name. Read-after-write re-parses the file. |
| B4 | grant identity | The fingerprint, bound INTO `command=… --grant SHA256:<fp>`; witness row `{ts, fp, src, orig_cmd[:120], bytes, ms}`; list/revoke by fingerprint; comment is display-only. |
| B5 | revoke semantics | By key blob across BOTH `AuthorizedKeysFile` paths (`authorized_keys`, `authorized_keys2`); refuse-loud (unless `--force`) on a matching line without our marker; report the count removed. Write = temp+fsync+rename in `~/.ssh`, 0600 preserved (`atomic_write_text_preserving`), lstat-refuse a symlink, check `~/.ssh` 0700. Reader passes `-o ControlMaster=no -o ControlPath=none` (kiai's stanza persists a master 10 min; a revoke would lag). |
| B6 | center-side push | Calls the TARGET's `box_truth_grant.py add --pubkey '<line>' --center <name>` over the existing access — never edits `authorized_keys` remotely; refuses when the registry `expect_hostkey` for the target is missing or mismatched. Same for the bulk CLI. |
| B7 | the center's private key | 0600 in `~/.ssh/<app>_noc_ed25519`, never under `~/.claude/ssh`, `fleet_backup.sh` asserted (test) not to sweep `~/.ssh`. `box_truth/v1` = an ALLOWLIST of the ten sections with a producer-side tripwire test (no `ssh-… AAAA`, `-----BEGIN`, `psk`, `token=`, `password` in any section). `box_truth_grant rotate` is a first-class verb (new key → re-grant all → verify → revoke old); the bulk CLI exists for revoke, not only migration. |
| B8 | `from=` | Stays OFF by default (a renumbering would read as "grant refused" — measured). Replaced by: per-fingerprint rate limit in the export (refuse > 6 reads/60 s with a JSON refusal doc), "last read from `<src>`" as a column in the target TUI and the witness, and B7 rotation. `from=` remains an opt-in flag. |
| S1 | spool outcome taxonomy | `transport: {outcome ∈ ok/refused_grant/identity_mismatch/unreachable/export_broken, rc, stderr_head}`; the collector renders each distinctly; "refused" says "(key or source)". |
| S2 | host identity on pull | NOC `known_hosts` generated from `fleet_naming.json` `expect_hostkey`, `StrictHostKeyChecking=yes`; mismatch → `identity_mismatch`, never data. |
| S3 | the control that can fail | Reader pinned: `-i <noc key> -o IdentitiesOnly=yes -o IdentityAgent=none`. Acceptance 3/5 use the pinned reader (the interactive keys Q16 keeps would otherwise satisfy the target after a revoke). |
| S4 | login vs loopback | Test: every URL the export reads is in the map's unauthenticated VIEW set; a section that moves behind the BIC-3 session is read from its file/socket, never via loopback. |
| S5 | dual-app box | Witness at `$XDG_STATE_HOME/box_truth/reads.jsonl` (app-independent); one grant per fingerprint across both apps' entries. |
| S6 | shell startup / nologin | Export frames its document with a first-line marker `__BOX_TRUTH_V1__` (reader parses after it); grant CLI refuses when the login shell is not a real shell. |
| S7 | target resource bound | `flock` on the witness so exports serialise, plus the B8 rate limit. |
| S8 | center resource bound | Read ssh stdout through a pipe with `MAX_BODY_BYTES` (8 MiB) and the HTTP deadline; overflow → `export_broken`. |
| S9 | center page injection | Guard test: every peer value interpolated inside an `innerHTML` assignment in `fleet.html` passes through `esc(` (25 sites, sampled clean; cookie already `HttpOnly; SameSite=Strict`). |
| S10 | carve-out preconditions | Plan shows the exact line, fingerprint, file, existing grant count; CLI measures `~/.ssh` 0700, non-symlink, `sshd -T` readable (else UNKNOWN in the plan), real shell, export script present+executable at the embedded absolute path; read-after-write. |
| S11 | zero grants on fresh install | Regression-guard test: no installer / `update.sh` / provisioning / fleet script writes `authorized_keys` outside the grant CLI. |
| S12 | Q16 migration order | Per box: grant → verify with the pinned reader → repoint the writer → remove moc3's duplicate via revoke-by-blob; interactive lines stay; one touch-log line per box. |

**Amended acceptance (supersedes Q14):**

1. MA `/fleet` 8/8, moc3 `resolution_method: ssh_spool`.
2. Writer stopped → moc3 DARK within `SPOOL_STALE_S` (≤ 6 min); `cron_verdict` pages.
3. Revoke on moc3 → pinned reader → `transport.outcome == refused_grant`; the cell names the fix.
4. moc3's TUI lists the grant: fingerprint, comment, last read time AND source address.
5. Forced command: `bash`, `-t`, `-s sftp`, stdin payload, `-A`, `-L` → the same JSON, forward `000`; `LC_ALL=C` client → valid JSON.
6. Fresh install: zero grants; the installer-write guard test is green.
7. Two-line / option-prefixed / RSA paste → refused with the reason.
8. `rotate` on the center → every target reads with the new key; the old key is refused.
9. A target presenting a different host key → `identity_mismatch`, never rendered as data.

## Session 3 brief (written 2026-10-14 ~16:40 HST by the arc's REVIEWER, Fable 5.1 — for an Opus BUILD session; builder ≠ reviewer)

> Why this section exists: the operator asked whether to start session 3 at
> 16:20 and the answer was no — it is a two-repo build plus a coordinated
> bump, Opus-shaped, and this session reviewed the arc's fix commits and its
> rollout, so building it here would leave no non-author. Everything below
> was MEASURED this session unless tagged READ or ASSERT.

### Where the arc stands (measured 2026-10-14)

- MF `main` carries the whole trust path and it is LIVE: grants on moc, kiai,
  lehua, moc3 (center key `SHA256:8B36…`, `noc:VolcanoAI`); the legacy
  interactive-key spool read is DELETED (`a330d4e1`); an unflagged target reads
  `refused_grant` naming the push; unpush → DARK next tick (`d98b7eaf`, drilled
  live on lehua 14:22). Fleet 9/9 @ `d98b7eaf`. Non-author passes on the fix
  commits, the rollout/deletion and the drill legs: all PASS (table rows
  2026-10-14).
- MeshAnchor `/opt/meshanchor` @ `34b75362` on meshanchor-server has **none of
  it**: no `src/utils/box_truth*.py`, no `scripts/box_truth_*`, no spool writer
  (`fleet_truth_spool.py` never existed there — a carried note said session 3
  must "delete the same legacy block in MA"; there is nothing to delete),
  **0 grant lines** in meshanchor-server's `authorized_keys`. MA's `/fleet` is
  honestly DARK on moc3 by the Interim rule above, host-key pin only.
- Versions today: MF `0.6.3-beta`, MA `0.2.0-beta`. The bump rule below is
  unchanged: both or neither.
- MF's TUI has NO grant/revoke handler yet; `handlers/fleet_posture.py` carries
  the doctrine note that grants THIS action its acting carve-out (Q8/Q17): one
  CLI, plan first, typed confirm = the fingerprint's last 8, logged.
- The byte-lock parity gate exists in MF `.githooks/pre-push` ("Byte-lock
  parity — the twin contract") and both repos run the parity-mirror execution
  gate in `pre-commit`; `scripts/parity_check.py` `BYTE_IDENTICAL` lists
  `canonical_message.py`, `fleet_truth.py`, … but NO box_truth module yet.
  `parity_check.py` reads `in sync` today.
- Adjacent, same shape, same session: MA
  `src/gateway/lxmf_broadcast_bridge.py::_load_or_create_identity` is the exact
  pre-F1 loader (`try: from_file … except: warn → to_file` recreate; `from_file`
  returns None, never raises). `gateway/identity_store.py` + `tests/_fake_rns_identity.py`
  + the refuse-and-witness tests port byte-identical. (`commands/rns.py:101`
  is create-only and `utils/paths.py:345` checks None — NOT defects.)

### The work, in the order that keeps it honest

1. **Shared modules → MA, byte-identical, parity-locked FIRST.** Copy
   `src/utils/box_truth.py`, `box_truth_reader.py`, `box_truth_grants.py`,
   `box_truth_center.py` and the parts of `box_truth_collect.py` that do not
   import MF's watchdog probes; add every copied path to `BYTE_IDENTICAL` in
   **both** repos' `parity_check.py` in the same commit pair, so the existing
   pre-push byte-lock covers them from the first push (this is what the
   handoff's "both-repo builder hook" means — confirm MA's `pre-push` carries
   the same byte-lock leg as MF's; if not, port that leg too). `parity_check`
   must read `in sync` before step 2 starts. MF is the lead repo: a change
   that must differ per app goes behind a seam, never into the shared file.
2. **The deferred move (session 2's deviation 1).** The three payload helpers
   + `judge_spooled_schedules` still import MF watchdog probes, so they could
   not live in the shared module. Give them an app-side seam (MF's helpers,
   MA's helpers, one shared caller) rather than copying MF's probes into MA.
3. **MA export + grant CLI + spool writer + collector S1 render.**
   `scripts/box_truth_export.py` (the forced command), `scripts/box_truth_grant.py`,
   a spool writer (MA has no legacy path to keep — build it on the export
   from day one), `fleet.json` `ssh` field, collector renders the S1 outcome.
   Tests ported with the fake identity (`tests/_fake_rns_identity.py`).
4. **F1 port** into `lxmf_broadcast_bridge.py` via `identity_store.py`, test
   RED on the old code first (a zero-byte identity must REFUSE, corpse kept).
5. **TUI grant / revoke / list in BOTH apps**, within the carve-out: render the
   plan the CLI prints, typed confirm = last 8 of the fingerprint, call the ONE
   CLI, log. Revoke and `list` must work from the TUI (sudo path): F7's
   lesson — chown everything the writer creates under sudo. The TUI stays a
   surface everywhere else.
6. **Bump both, deploy both**: MF `0.7.0-beta`, MA `0.3.0-beta`, same session,
   CI green for BOTH shas, `fleet_sync` THEN `fleet_pull`, full `honest_status`
   rc=0, MA twin verified on meshanchor-server.
7. **Acceptance from meshanchor-server as a SECOND center reading moc3**
   (Q14 legs + the 9-leg amended acceptance): `center-key --create` on
   meshanchor-server (chown under sudo), `push moc3 --confirm moc3` with the
   operator's words, moc3 then carries TWO grant lines (one per center) —
   `dedupe` must leave both; MA `/fleet` shows moc3 via `ssh_spool`/pinned;
   revoke from meshanchor-server does not disturb VolcanoAI's grant. Touch-log
   each box write.
8. **Non-author pass BEFORE any grant is pushed from meshanchor-server** —
   the most capable model available that day (operator rule 10-14), not a
   name. Queue it in `review_provenance.md` when step 6 lands.

### Do not

- Do not let MA's spool writer re-grow a legacy interactive-key read; the
  deleted MF path is the shape to refuse.
- Do not edit `fleet_truth.py` for B3 without the split first: it is
  byte-identical parity AND at the 1500-line cap (MF025) in both repos.
- Do not bump one app alone; do not deploy MF's bump before MA's is ready.
- Do not fake the step-8 review on a smaller model; queue it.

### Carried, not in this session's scope

Deferred designs from the fix-review (N2 hashed known_hosts HMAC — fails
closed today; N5 custom `AuthorizedKeysFile`; N6 concurrent-editor lock; N7
witness path under `XDG_STATE_HOME`; HTTP 4xx bodies dropped; B3 own-reason for
services/radio/schedules/ingress). `reachable.source` is hardcoded `"fanout"`
on every `/fleet` row (`fleet_truth.py:732/735`) — read `resolution_method`;
fixing it is a both-repo edit behind the split.
