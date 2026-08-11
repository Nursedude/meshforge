# Pi Fleet Box — Runbook Zero (2026-08-11)

> **This document is deliberately unproven.** It is the restore procedure AS
> BELIEVED at write time, committed BEFORE any trial, per
> `standalone_usability_study.md` Phase 0.3. Every step carries a tag:
> **[V]** verified live · **[B]** believed, not drilled · **[U]** unknown — a
> gap the trial must fill. The diff history of this file IS the artifact; if a
> drill does not change it, the drill probably wasn't honest.
>
> Secrets live in the operator's private `~/fleet-configs` (vaulted, encrypted
> off-site as `fleet-vault`), never here.
>
> **Why it exists**: the 7 Pi boxes' identity material was captured 2026-08-11
> and verified as CAPTURE — two transport identities were proven to reproduce
> their live daemons, and all 7 verified current byte-for-byte. Nothing proves
> a **box** rebuilds. T1 taught what that gap looks like: alaula's config diff
> was perfect while the box was *working-but-degraded* — right config, wrong
> binary, missing instrument. **A restore is not done when it works. It is
> done when it is PATCHED + INSTRUMENTED.**

## What you are restoring

One MeshForge fleet Pi, back to being **itself** — same RNS identity, same
role, same instruments — not a fresh node the mesh must re-learn.

| Box | Role | Radio | Notes |
|-----|------|-------|-------|
| moc | gateway + transport | SPI | `enable_transport=True`; busiest gateway |
| moc3 | gateway + transport | SPI | `enable_transport=Yes`; **map disabled BY DESIGN** (07-24) |
| moc1, moc2 | map/maps | — | also run the separate `/opt/meshforge-maps` repo |
| moc4, moc5 | collector | — | `meshforge-map` (singular) |
| kiai | canary brain | USB CH341 | tunnel-only reachability via alaula |

⚠️ **Pick the drill subject deliberately.** moc and moc3 are the only transport
instances — losing one costs the mesh routing. Prefer **moc5 or moc4** for the
first destructive trial; kiai is tempting but its tunnel-only path makes a
failed restore hard to reach (see §6).

## 0. Pre-flight — do not skip

- [B] `~/fleet-configs/<box>/` exists and its `MANIFEST.sha256` verifies:
  `cd ~/fleet-configs/<box> && sha256sum -c MANIFEST.sha256`
- [V] The capture contains: `rns/config`, `rns/transport_identity`,
  `meshforge/*_identity` + `*.json`, `meshtasticd/{config.yaml,config.d,prefs}`,
  `system/{crontab.txt,systemd-user/,wrappers/}`.
- [V] It does **NOT** contain: RNS network caches (rebuild themselves), raw
  channel key material (fingerprint only), the patched meshtasticd binary
  (fingerprint only). §4 and §5 cover where those come from instead.
- [U] Base OS image + version per box. Nowhere recorded. **The first trial
  must write it here** — a restore onto the wrong Debian/Ubuntu base is a
  silent source of dependency drift (mixed py minors, PEP 668).
- [B] Physical/SSH access to the box that does NOT depend on the box
  (console, or another box's tunnel).
- [B] Announce the drill: it WILL page. See §6.

## 1. Base OS + user

1. [U] Flash the base image (version unknown — see Pre-flight).
2. [B] Hostname must match the fleet name exactly — `fleet_naming.json` and
   the managed `/etc/hosts` block are keyed on it. Wrong hostname = the box
   is reachable but invisible to every fleet leg.
3. [B] Create user `wh6gxz`, add to `sudo`; install its authorized_keys (the
   fleet key is `~/.claude/ssh/id_ed25519` on the manager).
4. [B] `sudo loginctl enable-linger wh6gxz` — **user units do not survive
   logout without it**, and most MeshForge organs are user units
   (mini-dudeai, echo, tracer, digest, claw). ⚠️ A box that boots with no
   lingering silently runs zero instruments while looking healthy.
5. [U] cloud-init: on the 8 cloud-init boxes, `/boot/firmware/user-data` must
   carry `manage_etc_hosts: localhost` or it wipes the fleet `/etc/hosts`
   block on EVERY boot (07-27). Verify BEFORE the first reboot.

## 2. Repo + dependencies

1. [B] `git clone` MeshForge to `/opt/meshforge`, checkout the SHA the rest of
   the fleet runs (`git -C /opt/meshforge rev-parse HEAD` on the manager).
   ⚠️ Never `sudo git` — it root-owns `.git` and silently kills that box's
   ability to pull.
2. [U] venv: rebuilt from `requirements/` or restored? Unknown. Route any
   install through `src/utils/pip_install.py`, never bare `pip`
   (PEP 668 / mixed-minor class).
3. [B] `python3 src/launcher.py --profile <role>` or `provision_role.py` —
   the **role authority**, not a what's-running snapshot.
4. [U] On moc/moc1/moc2/moc4 the separate `/opt/meshforge-maps` repo must also
   be cloned. **`fleet_pull.sh` does not touch it** — it drifts independently.

## 3. Identity restore — the irreplaceable part

This is the step the whole fingerprints tier exists for. Everything else can be
rebuilt; these bytes cannot.

1. [B] RNS config + identity, as **root** (rnsd runs as a root service — this
   is why `~/.reticulum` is the wrong place and looking there finds nothing):
   ```
   sudo mkdir -p /etc/reticulum/storage
   sudo cp <capture>/rns/config /etc/reticulum/config
   sudo cp <capture>/rns/transport_identity /etc/reticulum/storage/transport_identity
   ```
2. [U] **Ownership and mode are unverified.** There is a `foundation_perms_drift`
   signal class precisely because a born-correct RNS permission foundation has
   drifted before (mf.4/#73). The trial must record what rnsd actually needs.
3. [V] `enable_transport` is per-box and lives in the restored `rns/config`
   (`True` on moc, `Yes` on moc3, `False` on the other five). Do not normalise it.
4. [B] `instance_name` must match what the box's watchdog probes expect. A
   mismatch is not loud — it made an RNS probe sit `indeterminate` for 8.8
   days while blaming rnsd (08-05). **Check**: `sudo ss -xnpl | grep "@rns/"`
   must match the watchdog's `instance_name resolved to` log line.
5. [B] Service identities:
   `cp <capture>/meshforge/*_identity ~/.config/meshforge/` (gateway, lab_echo,
   lab_tracer — 3–9 per box by role).
6. [B] Config JSON: `cp <capture>/meshforge/*.json ~/.config/meshforge/`.

**Verify identity restored, don't assume** — this leg is already drilled and
[V]: derive the public hash from the restored key and compare to the live
daemon after start (§7). Two independent sources; they must agree.

## 4. meshtasticd

1. [B] Restore `/etc/meshtasticd/config.yaml` + `config.d/` from the capture.
2. [B] Restore `prefs/` (device/module/config/nodes state) from the capture.
3. [U] **Channel key material is NOT in the capture** — deliberately. It comes
   from the alaula sysupgrade backup (LAB-ZERO names it the canonical carrier)
   or the operator's QR. The capture holds only a sha256 so drift is
   detectable. ⚠️ The restore procedure for it is UNWRITTEN. First trial must
   fill this in — it is the single most likely step to fail.
4. [B] **Preset**: the fleet is TWO-PRESET by design — LongFast/ch20, and
   SHORT_TURBO/ch8 on moc2+moc3. RF cannot cross. Read the parsed
   `config.proto`; never infer from a channel name.
5. [U] Install method on a Pi (apt repo? built?) — unrecorded.
6. [B] **USB-radio boxes only** (VolcanoAI/moc1/moc5/kiai): the patched build
   for firmware#10468 is NOT in the capture (88 MB artifact). Restore it from
   the on-box ipk in `/etc/meshforge/pkg/`, or rebuild per the
   `persistent_issues.md` #10468 recipe, then **verify against the captured
   fingerprint** (`system/wrappers/BINARY-FINGERPRINTS.txt`: sha256 + size).
   ⚠️ `pgrep -x meshtasticd` MISSES the patched binary (comm is
   `meshtasticd-patched`); use `pgrep -f`.

## 5. System wiring — what a rebuild silently loses

1. [B] `crontab system/crontab.txt` — these are verdict-wired; a missing cron
   is silence, and `cron_verdict_stale` only judges crons that are wired.
2. [B] `cp system/systemd-user/* ~/.config/systemd/user/`, then
   `systemctl --user daemon-reload` and enable each. ⚠️ Enablement is a symlink
   under **ANY** `*.target.wants` — check `ls ~/.config/systemd/user/*.target.wants/`,
   not one directory (08-09).
3. [B] `sudo cp system/wrappers/* /usr/local/bin/` (scripts only; the binary is §4).
4. [U] System-scope units (meshtasticd, rnsd, meshforge-watchdog, gateway,
   map) come from `templates/systemd/` in the repo — which ones this box needs
   is role-dependent and NOT captured. Derive from `provision_role.py`, and
   record here what the trial actually needed.
5. [B] Manager-side: if the box is tunnel-fronted, its `via` entry in
   `~/.config/meshforge/fleet_offline_boxes.json` must exist or the monitor
   will page it DOWN when only its path is down (08-11).

## 6. Drill protocol — how to run this destructively without lying

T1's residual #4: the drill made the fleet page a box it never touched, and
that false page went unrecorded while T3 pre-registers it as a failure.

1. [B] **Before the destructive step, enumerate who loses their only path to
   what.** Write it down. For a Pi that is usually just the box itself; for
   anything alaula-fronted it is also kiai.
2. [B] Expect and pre-record: `fleet_box_unreachable` for the subject box,
   `cron_verdict_stale` for its crons, `router_scout`/watchdog gaps. These are
   EXPECTED, not noise — every page raised during the window gets explained,
   not waited out.
3. [B] Any test that transmits goes through the `tx_guard` egress gate on a
   **TEST channel** (2026-08-09: a test suite keyed a live statewide channel).
4. [B] Do not run this while a soak is in flight (`mf5_soak_watch`,
   `honest_status --strict`).
5. [U] Rollback: if the restore fails mid-way, what gets the box back? The
   capture is not a disk image. **Unknown — the trial must answer this before
   it is repeatable.**

## 7. Acceptance — PATCHED + INSTRUMENTED, not "working"

A config diff proves config and nothing else (the T1 study-level finding). All
of the following must hold before the restore is accepted:

- [B] **Identity**: derived public hash from `/etc/reticulum/storage/transport_identity`
  == what the live daemon reports. On a transport box, `rnstatus` prints
  `Transport Instance <hash> running`. On the other five that line is ABSENT
  BY DESIGN (`enable_transport=False`) — that is inert, not a failure.
- [B] **Code provenance**: box SHA == fleet SHA (`git -C /opt/meshforge rev-parse --short HEAD`).
- [B] **Binary provenance** (USB boxes): running meshtasticd matches the
  captured fingerprint; `wc -l /proc/<pid>/maps` flat over 30 min (climbing =
  the #10468 leak returned, i.e. a stock build got restored).
- [V] **TIMERS, not just services** (trial 2 — the defect that got past
  everything else). `systemctl --user list-timers` must show a scheduled
  NEXT run for every restored timer; `is-enabled` alone is a lie, and a timer
  left `failed / enabled` looks enabled while never firing. Check both scopes.
  Recover with `systemctl --user reset-failed <t> && systemctl --user start <t>`.
- [B] **Instrumented**: mini-dudeai ticking (`/warmstart` shows the box fresh
  in the rollup), watchdog signals clean, its crons landing verdicts.
- [V] **Web client actually serves**: `curl -sk https://<box>:9443/` → **200**.
  A LISTENING port proves nothing — meshtasticd binds 9443 with or without
  `/etc/meshtasticd/ssl/`, so after a restore that lost the cert the port was
  open and every service check passed while the TLS handshake failed (finding
  15). Same for `:4403`.
- [V] **The map's `/fleet` view must read `box_state: healthy` for the box**,
  and its own `/fleet/slo` `overall_status: ready`. This is the surface a human
  actually looks at, and in trial 2 it was the ONLY one that knew the box was
  degraded — `fleet_offline_state.tsv`, `honest_status`, the mini rollup and
  per-service `NRestarts` all read clean simultaneously. Check the human's
  surface, not only the ones you wrote.
- [B] **Fleet agrees**: `bash scripts/honest_status.sh` — `fleet SHA drift`
  PASS including this box, no new watchdog signals attributable to it.
- [B] **The domain's actual end**: **a message arrives** — send on the TEST
  channel through `tx_guard` and confirm arrival at another box.
  ⚠️ This is the check that answers "what still passes all of the above while
  the box is dead" TODAY: it was deferred in BOTH trials, and until it runs
  **through this box's own radio, on this box's RF segment** (two-preset
  fleet — read the parsed `config.proto`, §4.4), a box with a deaf radio
  (wrong region, wrong preset, dead PA, disconnected antenna) passes every
  other line here — identity, SHA, fingerprint, NRestarts, timers, mini,
  TLS 200, even `/fleet` — while dead at the one thing it exists to do.
  A gateway relaying via RNS/MQTT does not count as this box's RF proof.
- [U] **SURVIVES A REBOOT — the sixth check** (2026-08-11 frontier review of
  this section; never yet drilled). Every line above interrogates a RUNNING
  system whose state is partly inherited from before the drill or hand-started
  by the restore script — trial 1 even noted system units "survived only
  because they were out of destruction scope". The fleet already carries
  boot-triggered destroyers that pass every check above until the first power
  cycle: cloud-init's `/etc/hosts` wipe (§1.5, found only when moc5 rebooted),
  missing `enable-linger` (§1.4 — every user unit dead at next boot while
  today's box looks fully instrumented), enablement symlinks under the wrong
  `*.target.wants`, units running-but-never-enabled. A Pi loses power
  routinely; a restore that holds only until the first power blip is not a
  restore. **Power-cycle the box and re-run this entire list from the top.**
  After the reboot, ALSO verify the clock before trusting any freshness-based
  line (`timedatectl` shows NTP synchronized): an RTC-less Pi with dead time
  sync forges every staleness computation these checks lean on (hfm #6).

## 8. Known unknowns — the list this trial exists to shrink

| # | Unknown | Why it matters |
|---|---------|----------------|
| 1 | Base OS image/version per box | dependency drift, PEP 668, mixed py minors |
| 2 | venv rebuild vs restore | a wrong interpreter is invisible until import time |
| 3 | `/etc/reticulum` ownership + mode | `foundation_perms_drift` exists because this drifted before |
| 4 | Channel key restore procedure | most likely step to fail; deliberately not captured |
| 5 | meshtasticd install method on Pi | unrecorded |
| 6 | Which system units per role | not captured; role authority must be derived |
| 7 | Mid-restore rollback path | without it the drill is not repeatable |
| 8 | Time-to-restore | H1's metric analogue; unmeasured |

---

## 9. TRIAL 1 RESULTS — moc5, 2026-08-11 (application-layer flatten)

**Scope run**: destroyed everything the capture claims to cover — `/etc/reticulum`,
`/etc/meshtasticd`, `~/.config/meshforge`, `~/.config/systemd/user`, crontab; all
services stopped. **NOT destroyed**: base OS, `/opt/meshforge` + venv (unknown #2
made that unrecoverable remotely), `/root/.portduino` prefs. §1 and §2 remain
UNTESTED. Rollback existed but was sealed and never opened.

**Verdict: RESTORE FAILED TWICE before succeeding.** The box came back
byte-identical to baseline only after two out-of-runbook repairs. A cold operator
following this document alone would have been left with a dead rnsd and a
permanently uninstrumented box.

### The two failures

1. **[V] Ownership/mode is NOT captured — rnsd would not start.**
   `sudo cp` landed everything `root:root`; rnsd died with
   `PermissionError: '/etc/reticulum/storage/cache'`. The real ownership:
   `/etc/reticulum` `root:wh6gxz 1775` · `storage/` `wh6gxz:wh6gxz 777` ·
   `transport_identity` `wh6gxz:wh6gxz 666` · `config` `root:root 644`.
   ⚠️ **The capture script itself destroys this evidence** — it `chown`s the
   staging tree to the invoking user before tarring. Fix the capture to record
   `stat` ownership/mode per file, or tar with `--numeric-owner` as root.
2. **[V] §3's "rnsd is a root service" is WRONG, and varies per box.**
   moc5: `User=wh6gxz Group=wh6gxz`. moc1: `User=root`. Never assume; read
   `systemctl cat rnsd`.
3. **[V] The `*.json` capture filter missed required config.**
   `mini_dudeai.env` (109 B) is REQUIRED — without it mini-dudeai crashloops
   (`ValueError: fleet preset requires MINI_DUDEAI_NTFY_TOPIC`), so the box runs
   with **no night watcher**. Also missed: `device_config.yaml`, `lab_peers`,
   `message_queue.db`. **Capture all of `~/.config/meshforge` except `logs/` and
   `*.bak-*`, not just `*.json`.**

3b. **[V] Only part of `/etc/meshtasticd` was captured — the RADIO crashlooped.**
   `config.yaml` + `config.d/` alone left `available.d/` behind; meshtasticd's
   autoconf scans it and dies with
   `*** Exception bad file: /etc/meshtasticd/available.d/<radio>.yaml`.
   `ssl/` (web-client TLS) was missing too. **Capture the whole tree** minus
   `*.dpkg-old`. ⚠️ This was found ~10 minutes AFTER the acceptance diff
   reported IDENTICAL — see #4b.

### The acceptance criteria were themselves wrong

4. **[V] §7's "mini ticking" gave a FALSE PASS.** The fleet rollup reported moc5
   `fresh · last tick 3m ago` while mini-dudeai was crashlooping — state-file
   freshness LAGS the daemon's death by the stale window. **Acceptance must
   assert the UNIT is `active`**, not that a state file looks recent. This is
   the "trust the representation, not the thing" class landing inside the
   acceptance test.

4b. **[V] `systemctl is-active` PASSED a crashlooping daemon.** A unit in
   `Restart=` backoff alternates `activating`/`active`, so a point-in-time
   `is-active` — and therefore the whole before/after acceptance diff, which
   reported **IDENTICAL** — read healthy while meshtasticd was dying every few
   seconds. **Acceptance must assert `NRestarts=0` (or unchanged) and a
   settled state, not `is-active` at one instant.** Two of the three failures
   in this trial were invisible to the acceptance test that was supposed to
   catch them; both were found by reading a daemon's journal instead.

### Other findings

5. **[V] Hostname does NOT match the fleet name** (moc5's is `wh6gxzser`). §1.2
   is wrong — fleet naming does not come from hostname. Corrected above.
6. **[V] `~/.config/systemd` is `root:root`** — the user cannot create/remove its
   own `user/` unit dir. Restore needs `sudo mkdir` + `chown`.
7. **[V] System-scope units are not captured** (confirmed §5.4's `[U]`). They
   survived only because they were out of destruction scope; a bare-metal
   restore has nothing to recreate them from.
8. **[V] The crontab references `~/power_capture.sh`, which is not captured** —
   a restored crontab fires into a missing command (T1 residual #1's shape).
9. **[V] `instance_name` is absent from the RNS config** — RNS resolves the
   omission to `default` itself. Do not "helpfully" add one.
10. **[V] The instruments caught the operator's error.** `foundation_perms_drift`
    fired on the bad ownership and CLEARED on the fix — the exact class it was
    built for (mf.4/#73). `service_inactive`, `user_unit_inactive` and
    `role_drift` all fired and cleared correctly. The watchdog was right about
    the box the whole time.
11. **[U] Time-to-restore**: ~7 min wall clock, but with 3 stop-and-diagnose
    cycles. Not a clean measurement; re-time once the fixes above land.

### Still unknown after trial 1

§1 (base OS), §2 (repo + venv), the channel-key restore path, mid-restore
rollback, and whether a **message arrives** — the domain's actual end was NOT
tested (RF TX needs the tx_guard TEST-channel path, deferred).

## 9b. FINDING 15 — the drill DESTROYED the web client's TLS material, and I
## then recorded the loss as normal

Reported by the operator hours later: `https://<moc5>:9443/` was dead.

**Mechanism, entirely self-inflicted:** `/etc/meshtasticd/ssl/`
(`certificate.pem` + `private_key.pem`, owned `meshtasticd:meshtasticd`) held the
web client's TLS material. Trial 1's restore copied only `config.yaml` +
`config.d/`, so **ssl was destroyed with the rest of `/etc/meshtasticd` and never
restored**. Port 9443 still LISTENED — meshtasticd binds it regardless — so every
service-level check passed while the TLS handshake failed (`curl -> 000`). A
listening port is not a working service.

**The compounding error is the one worth remembering.** When I re-captured, moc5
reported `ssl=no`, and I wrote in the commit message that *"moc5 legitimately has
no ssl/"* — recording as a legitimate absence a thing I had deleted twenty
minutes earlier. Every other box had it. **I had the comparison in front of me
and did not make it.** An absence should be explained, never assumed benign
(honest_failure_modes #2, in the operator's own words: unobservable ≠ absent-by-
design).

**What saved it**: the pre-drill safety bundle still existed *only because the
permission guard refused my `rm`* — I had twice tried to delete it as
"redundant". Had that guard not held, the cert and key were gone.

**Runbook consequences:**
- §4 must restore the WHOLE `/etc/meshtasticd` tree including `ssl/`, with
  ownership `meshtasticd:meshtasticd`, dir `700`, files `644`.
- §7 must check `curl -sk https://<box>:9443/ -> 200`, not that a port listens.
- Do not delete a drill's rollback until the drilled box has been verified on
  every surface — including the human's.

## 10. TRIAL 2 — moc5 re-drill with the fixed capture, 2026-08-11 (the double tap)

**Why re-run**: trial 1's restore only succeeded after three out-of-runbook
repairs, so the fixes were BELIEVED, not drilled. A fix is not proven by the
failure that prompted it.

**Bar**: restore from the capture ALONE — reaching outside it for any file is a
failure, because fetching from the running box only proves the box still works.
The restore was driven by a script (`remote_restore.sh`) whose hard rule is
"read nothing outside the capture", including applying ownership from the new
`system/OWNERSHIP.txt`.

**Result: AMENDED 2026-08-11 — originally recorded as "PASS, zero repairs".
That was WRONG, and wrong the same way trial 1 was.** The restore left BOTH
user timers (`meshforge-tracer.timer`, `meshforge-mini-dudeai-dream.timer`)
`failed / enabled` — their enablement symlinks restored correctly, but the
units went to failed during the destruction-window `daemon-reload` and nothing
restarted them (`systemctl --user daemon-reload` does NOT start a timer). The
tracer therefore had not fired in ~38 min. The honest result is
**PASS on services, FAIL on timers — one repair needed.**

**How it was found**: not by me. Every surface I checked said clean —
`fleet_offline_state.tsv` zeros, `honest_status` `9/9 clean, 0 signals`, the
mini rollup `fresh`, all six services `NRestarts=0`. **The operator saw it on
the map's `/fleet` view**, the one surface I had not looked at. The chain:
`/fleet` `box_state=failed` → moc5's own `/fleet/slo` `overall_status=degraded`
→ `cascade pre_fail=1` → fingerprint `tracer_stale_fire`.

⚠️ **§7 was incomplete: it enumerated SERVICES and never TIMERS.** A whole
class of scheduled work was dead while six of six services read `NRestarts=0`.
Corrected in §7 below. This is the third distinct restore defect in one
evening that the acceptance test could not see — after ownership and the
crashlooping radio — and the pattern is consistent: **the acceptance kept
asserting only what I had already thought to check.**

| check | trial 1 | trial 2 |
|---|---|---|
| rnsd | PermissionError, dead | `active NRestarts=0` |
| meshtasticd | crashloop (`available.d` missing) | `active NRestarts=0` |
| mini-dudeai | crashloop (`mini_dudeai.env` missing) | `active NRestarts=0` |
| map / watchdog / echo | started | `active NRestarts=0` |
| RNS | "No shared RNS instance" | Shared Instance Up, serving 2 programs |
| manual repairs needed | **3** | **0** |
| baseline diff | IDENTICAL *(and lying — see 4b)* | IDENTICAL *(corroborated by NRestarts + T+7)* |

**What this proves**: the three trial-1 defects are fixed at the source, and
`OWNERSHIP.txt` — the artifact invented to fix defect #1 — works: it restored
`root:wh6gxz 1775` on `/etc/reticulum`, `wh6gxz:wh6gxz 777` on `storage/`, and
the rest, without any of it being guessed.

**What it still does NOT prove**: §1 (base OS) and §2 (repo + venv) remain out
of scope and untested; the channel-key path, mid-restore rollback, and "a
message arrives" are all still open. **This is one box, one shape, twice.**
A drill that passes is weaker evidence than one that fails — trial 2 says the
known defects are gone, not that the procedure is complete.

---

*Written before the trial, warts and all. If the first drill does not produce
edits to this file, be suspicious of the drill.*
**Trial 1 produced 13 findings. Trial 2 was recorded as a clean pass and was
NOT — the operator found a 14th defect the acceptance could not see.**
