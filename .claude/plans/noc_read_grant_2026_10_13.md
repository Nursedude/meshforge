# NOC read grant + `box_truth/v1` export — design spec (grilled 2026-10-13)

> Status: **DESIGN AGREED** (operator, 2026-10-13, Q1–Q18 all on the
> recommendation). Nothing built. Sessions 2 and 3 below build it.
> **Security pass DONE 2026-10-14 (Fable 5.1, gate 10):**
> `.claude/audits/noc_read_grant_security_pass_2026_10_14.md` — 8 blocking
> amendments B1–B8 + 12 should-fix S1–S12, folded into the "Security-pass
> amendments" section at the end of this file. Session 2 builds to the
> AMENDED spec and the AMENDED acceptance list there, not to Q14 alone.
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
| Q11 | Target registration | **The center-side grant writes the target entry** (MF `truth_spool_targets`; MA `fleet.json` peer `ssh` field) and revoke removes it. Listed-but-refused reads **"grant missing or refused"** with the fix named, never bare DARK (hfm #4: reader/writer wire together). |
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
