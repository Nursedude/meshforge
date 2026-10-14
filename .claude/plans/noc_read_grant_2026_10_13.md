# NOC read grant + `box_truth/v1` export — design spec (grilled 2026-10-13)

> Status: **DESIGN AGREED** (operator, 2026-10-13, Q1–Q18 all on the
> recommendation). Nothing built. Sessions 2 and 3 below build it.
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
