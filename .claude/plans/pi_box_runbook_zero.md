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
- [B] **Instrumented**: mini-dudeai ticking (`/warmstart` shows the box fresh
  in the rollup), watchdog signals clean, its crons landing verdicts.
- [B] **Fleet agrees**: `bash scripts/honest_status.sh` — `fleet SHA drift`
  PASS including this box, no new watchdog signals attributable to it.
- [B] **The domain's actual end**: **a message arrives** — send on the TEST
  channel through `tx_guard` and confirm arrival at another box.

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

*Written before the trial, warts and all. If the first drill does not produce
edits to this file, be suspicious of the drill.*
