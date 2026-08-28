# MeshForge Persistent Issues & Resolution Patterns

> **Purpose**: what is still LIVE, and the decision tells worth carrying in
> every conversation. NOT the history of what we fixed — that is the
> resolved-issue index in `persistent_issues_archive.md`.
> **Last audited**: 2026-08-05 — restructured; see the growth rule below.
>
> **Bloat guard**: lint rule MF012 (`scripts/lint.py --all`) fails when this
> file exceeds 40,000 chars. DO NOT raise the limit — the cap exists because
> this file is `@`-included into CLAUDE.md, so its cost is paid on EVERY
> conversation turn, forever, by every session.
>
> **Growth rule (2026-08-05, the structural fix).** This file is bounded by
> VALUE-PER-TURN, not by chronology. A newly-RESOLVED issue goes straight to
> the archive index — it does not land here first and get demoted later. Only
> two things earn a place here:
>   1. something still LIVE (unresolved, or a standing operating rule), or
>   2. a decision tell an operator would reach for at a terminal.
>
> Why the rule exists: the old model was "add here, demote the oldest when it
> trips", and it converged on a permanently-full file. On 2026-08-05 landing
> THREE entries required demoting NINE sections, and the resolved-issue index
> had grown to 17,534 chars — 44% of the file — describing only work that was
> already fixed AND guarded by lint rules and regression tests. Moving it out
> took the file from 48 chars of headroom to ~15,000. If you find yourself
> demoting to make room, the thing you are adding probably belongs in the
> archive too.
>
> **Nothing is deleted, only relocated**: `grep -n "#43"
> .claude/foundations/persistent_issues*.md` spans both files, and that glob
> is what tier-L's corpus indexes and what every "see persistent_issues.md
> Issue #N" comment in the tree resolves through.

---

## RNS / LXMF are MeshForge-owned forks (SSOT, 2026-05-30)

RNS and LXMF are now **hard forks owned by MeshForge** (`Nursedude/reticulum`,
`Nursedude/lxmf`), pinned in `requirements/rns.txt` by tag **and** SHA with a
`# MF-FORK-PIN` SSOT line; `scripts/rns_version_check.py` gates the fleet on the
`+mf.N` marker. Fleet baseline: **rns `1.3.8+mf.0` / lxmf `1.0.1+mf.1`** (rolled
2026-07-19, all 7 MeshForge boxes; the prior `1.2.5+mf.5`/`0.9.4+mf.0` baseline
rolled 2026-06-09). This is the meta-resolution for the entire
**rnsd-RPC fragility class** (#58/#61/#63/#68/#69/#72): fragility we used to work
*around* in `utils/rns_init.py` is now fixed *at the source*. The `+mf.1`→`+mf.5`
patch history (what each cure did, and which ones had to be RE-PORTED onto 1.3.8
rather than carried) is demoted to `persistent_issues_archive.md`.
⚠️ **Still a live operating rule**: do NOT rapid-cycle rnsd restarts fleet-wide —
a 15s-hang+SIGKILL plus slow rebind opens the `@rns` race window; mf.5 makes a
stranding self-healing (~30s outage), but space restarts and verify host-binding
before the next box anyway.

- **Wire-compat invariant (non-negotiable)**: never change crypto primitives
  (Ed25519/X25519/AES-256-CBC/Fernet) or the packet/announce/path-table wire
  format — that forks the *network*, not the code. Fork = maintenance + isolation.
- **Upstream tracking**: stock RNS ships off-GitHub now (Carrier Switch). To adopt
  a future release: `git merge <upstream-tag>` into `meshforge`, re-run Phase-1
  parity (version marker, rnsd ownership, gateway/map/tracer, **public-net interop
  proof**), canary one box, then fleet-roll. Full procedure in each fork's
  `FORK.md`; governance triggers (CVE-no-upstream / wire break / activity ceases)
  in [[project_upstream_dependency_governance_2026_05_29]].
- **1.3.8 / 1.0.1 merge arc — COMPLETE + FLEET-ROLLED 2026-07-19** (`50213071`
  roll, `72acf61e` SSOT bump). Full record:
  `.claude/research/rns_138_merge_eval_2026_07_16.md`. moc3 canary 07-17 →
  soak → per-box roll → SSOT bumped; all 7 boxes verified at the pin, both
  deliberate soak markers (moc3 `rns_version_drift`, moc4 `service_inactive
  rnsd`) cleared. Interop PROVEN 07-17 (cross-version LXMF round-trips:
  direct, public-transport-node, real-net tracer).
  **Findings worth carrying: (1) #72 NOT subsumed** — `_rpc_recv` re-ported
  onto the msgpack framing (21 sites). **(2) mf.4 re-ported, not carried** —
  RLock flaked LOG_EXTREME (A/B-proven; plain Lock, fallback re-log outside
  it). LXMF byte-identical, MF↔MA lockstep safe. **(3) A box has more RNS
  envs than the drift probe can see** — user site + root dist-packages +
  pipx venvs (nomadnet's was silently stock 1.1.4; kiai added a fifth class,
  root user-site). Roll EVERY env per box, together with rnsd.
- **MeshForge-side guards STAY** (`rns_init.py` probe, MF009/MF019 lint, watchdog
  `os._exit` backstop) as defense-in-depth — remove a backstop only after its
  in-library fix has held over a long soak.

See [[project_rns_fork_shipped_2026_05_30]] and
`.claude/plans/do-some-deep-research-delightful-dongarra.md`.

---

## Resolved issues — index moved to the archive (2026-08-05)

The chronological index of ~72 fully-resolved issues now lives in
`persistent_issues_archive.md`, beside the bodies it points at. It was 44% of
this file while describing only work already fixed AND guarded by lint rules +
regression tests — the lowest per-turn value in a file that loads every turn.

**Look one up**: `grep -n "#43" .claude/foundations/persistent_issues*.md`
(the glob covers both files, is what tier-L's corpus already indexes, and is
what every `see persistent_issues.md Issue #N` comment in the tree resolves
through).

### Quick diagnostic tells — the part worth loading every turn

Symptom you can see at a terminal → which class it is. Full bodies in the archive.

| Symptom | Class / first move |
|---|---|
| `[Errno 24]` or climbing fds on `:5000` | fd leak (#73) — restart `meshforge-map`, then find the leak |
| `rnstatus` hangs but the socket accepts | rnsd RPC wedge (#72) — `timeout 8 rnstatus >/dev/null \|\| echo wedged`; restart rnsd THEN its clients |
| `@rns/` listener owned by a non-rnsd process | namespace collision (#69) — `sudo ss -xnpl \| grep "@rns/"`; owner must be rnsd |
| RNS probe `indeterminate`/`clean` while `rnstatus` is healthy | probing a name this box doesn't serve (2026-08-05) — the ss owner must MATCH the watchdog's `instance_name resolved to` line |
| a disposition-collapse fix landed and the fleet went quiet | grep EVERY branch that reaches the same return, not just the one the incident came through (2026-08-09) — the 07-28 dups fix taught the *payload* branch to read the collector-cron declaration, curing every box that ANSWERS on `:5000`; the *transport* branch one line above kept the collapse, so moc3 (gateway-only, no map by design) stayed blind 12 more days. 7 of 8 boxes sharing a shape makes a partial fix look complete |
| a probe is blind on ONE box, clean on the other seven | ask what's DIFFERENT about that box's shape, not what's BROKEN on it (2026-08-09) — `rns_version_drift` blamed "service-user env unreadable" on moc4 for 12.3 days; moc4 is the one box with no `~/.local` (installs to `/usr/local/lib/python3.11/dist-packages`) and was sitting exactly ON the pin. Grep the probe's SIBLINGS: `probe_rns_env_coherence` had listed dist-packages all along and read `clean` on that box the whole time |
| a box pages `DOWN` but its own `uptime` says it never rebooted | you observed a PATH, not a box (2026-08-11) — ask what that box's ONLY route runs through, and whether it was being worked on. kiai reaches the manager solely via the tunnel alaula pins, so the 08-10 T1 drill's factory-reset of alaula produced 54 min of "Fleet box DOWN: kiai" about a box up 17 days. `fleet_offline_check.sh` now takes a per-box `via` dependency and pages **UNOBSERVABLE — state UNKNOWN** when the path is down too. ⚠️ Neither excuse nor exclude by default: a dependency that ANSWERS leaves the box implicated, and that page is real |
| a probe tells you to check a systemd unit, or blames another probe that "owns" it | check that unit EXISTS here FIRST (2026-08-09) — `synth_soak_degraded` said "check meshforge-synth-soak.timer" for 78 days on a box that has never had one; the artifacts were hand-fired lab runs. `systemctl --user status <unit>` → `could not be found`. Probe-side twin (2026-08-12, `32a6998e`): four classes sat `indeterminate` on meshanchor-server blaming "`service_inactive` owns that" for a meshtasticd with **no unit file** — nothing owned them, blind by construction, while `service_inactive` read `clean`. `systemctl show -p MainPID -p LoadState <unit>`: `LoadState=not-found` means the DETECTOR is the defect, and `absent` must read `inert`, never `indeterminate` |
| `systemctl --user is-enabled` says `enabled` but the unit isn't in `timers.target.wants` | enablement is a symlink under **ANY** `*.target.wants` (2026-08-09) — `meshanchor-map-restart.timer` lives in `default.target.wants` while declaring `WantedBy=timers.target`. A reader that opens one dir calls a live timer disabled. `ls ~/.config/systemd/user/*.target.wants/` |
| `:9443` web client deaf while RX is healthy | leaked `TCPInterface` starving PhoneAPI (#17/#75) — restart the map; honest RX record is `grep 'Received text msg'` |
| meshtasticd `/json/report` or `/json/nodes` 404 | ESP32-only, NEVER served by meshtasticd (#76) — not a fault |
| a NEW `http_local_unresponsive` | a NEW class — the GIL-serialization family (#70/#71) is closed by response caches |
| service `active` but its writes vanish | systemd sandbox path drift (#60) — check `ReadWritePaths=` vs where the code writes |
| meshtasticd moved off `:9443` after a HAT change | upstream overlay smuggled `Webserver: Port:` (#58) |
| a probe reports `indeterminate` for DAYS | it is a FINDING, not weather — and `inert` (absent by design) is a different claim (2026-08-05) |
| service crashloops after power loss with `OSError`/`InsufficientDataException` on a state file | zero-byte power-loss truncation (2026-08-27: 13 corpses fleet-wide post-Lala — 12 LXMF `*.ratchets`, 2 NUL jsonl lines). Quarantine the corpse, restart, announce. ⚠️ the gateway's steady-state error is `already registered destination` — scroll the journal to the FIRST failure after start; `quarantine_corrupt_ratchets()` self-heals the LXMF leg since `_rns_bridge_connection.py` 2026-08-27 |
| `uptime` disagrees with wtmp/`who -b`, or crons barely fire while cron is `active` | the clock ran days-stale through a WAN outage (RTC-less Pi: fake-hwclock restores stale time, NTP unreachable can't step it; moc4 ran ~8 days behind, 5 cron fires in 4 days). Wall-clock instruments (cron, verdict freshness, wtmp) all lie together; trust monotonic `uptime`. Fix class = LAN-internal NTP (see Starlink-resilience notes 2026-08-27) |

⚠️ **Growth rule (this is the structural fix, 2026-08-05).** This file is
bounded by VALUE-PER-TURN, not chronology. A newly-resolved issue goes
STRAIGHT to the archive index — it does not land here first and get demoted
later. Only two things earn a place here: something still LIVE, or a decision
tell an operator would reach for at a terminal. Nine sections had to be demoted
in one day to land three entries before this rule existed.

---

## Development Checklist

Before committing, verify:

- [ ] No `Path.home()` — use `get_real_user_home()`
- [ ] Actionable error messages, appropriate log levels
- [ ] Services verified with `check_service()` before use
- [ ] `subprocess` calls have `timeout=` (MF004)
- [ ] Utilities from central location, not duplicated
- [ ] `safe_import` for external deps only; direct imports for first-party

---

## Quick Reference: Import Patterns

```python
# Paths
from utils.paths import get_real_user_home, get_real_username, MeshForgePaths, ReticulumPaths

# Settings / Logging
from utils.common import SettingsManager, CONFIG_DIR
from utils.logging_config import get_logger

# Service checks
from utils.service_check import check_service, check_port, ServiceState

# External deps (safe_import)
from utils.safe_import import safe_import
RNS, _HAS_RNS = safe_import('RNS')
_pub, _HAS_PUBSUB = safe_import('pubsub', 'pub')

# First-party — ALWAYS direct import
from utils.service_check import check_service
from utils.event_bus import emit_message
from gateway.rns_bridge import RNSMeshtasticBridge
```

**Test patching**: Patch `_HAS_*` flags directly, not `sys.modules`:
```python
@patch('gateway.rns_bridge._HAS_RNS', True)  # CORRECT
def test_rns(self): ...
```

---

## Issue #29: Regression Prevention System — ACTIVE

100+ hours of circular regressions led to this 4-layer prevention system.

### Layer 1: Lint Rules (`scripts/lint.py`)
| Rule | Catches |
|------|---------|
| MF007 | Direct `TCPInterface()` outside connection infrastructure |
| MF008 | Raw `systemctl` for service state (use `service_check`) |
| MF009 | `RNS.Reticulum()` without `configdir=` |
| MF010 | `time.sleep()` in daemon loops |
| MF014 | Operator-specific values (hostnames, personal email, `/home/<user>/`) — break repo portability |
| MF019 | `RNS.Reticulum()` constructed outside the chokepoint (use `open_reticulum()` from `utils.rns_init`; #68/#69) |
| MF021 | `subprocess`/`systemctl`/`os.system`/`Popen`/`shell=True` in mini-dudeai engine + built-in sources/actions (observation-only invariant; #79) |
| MF027 | `probe_*` except-handler returning None with no `note_disposition` witness — fail-dark, THE #80 class (build:fix doctrine 2026-07-29); pre-commit also prints the honest_failure_modes checklist when a commit adds `except` lines to monitor code |

### Layer 2: Regression Guard Tests (`tests/test_regression_guards.py`)
- `TestTCPConnectionContract` — No new direct TCPInterface
- `TestFromradioContract` — TX uses `send_text_direct()`
- `TestServiceCheckContract` — Service state via `check_service()` only
- `TestPathHomeContract` — No `Path.home()` violations
- `TestNoShellTrue` — No `shell=True` in subprocess
- `TestKnownServicesConsistency` — KNOWN_SERVICES stays correct
- `TestOperatorValueContract` — No operator-specific values in source/templates/scripts/docs (MF014)
- `TestRNSReticulumChokepoint` — `RNS.Reticulum()` constructed only in `utils/rns_init.py` (MF019; #68 fail-open / #69 fail-loud)

### Layer 3: Pre-Commit Hook (`.githooks/pre-commit`)
Setup: `git config core.hooksPath .githooks`

### Working With This System

**New file needs meshtasticd TCP:**
```python
# Short-lived:
from utils.connection_manager import MeshtasticConnection
with MeshtasticConnection() as conn:
    if conn: nodes = conn.nodes

# Long-lived:
from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK, wait_for_cooldown
if MESHTASTIC_CONNECTION_LOCK.acquire(timeout=10):
    wait_for_cooldown()
    interface = TCPInterface(hostname='localhost')
```

**Adding legitimate TCPInterface creation:**
1. Add to `ALLOWLISTED` in `TestTCPConnectionContract`
2. Add to `lock_aware_files` in lint.py MF007
3. Acquire `MESHTASTIC_CONNECTION_LOCK` before creating

---


## Issue #69: Foreign daemon / boot race claims `@rns/<instance>` — RESOLVED, body in archive (trimmed 2026-06-07)

5th rnsd-RPC-fragility variant. MeshAnchor daemon hijacked VolcanoAI's `@rns`
listener (every RNS client EOF'd, fleet tracer 100% fail to VolcanoAI); boot-race
addendum (06-06, `84a79ca`): a client starting before rnsd boot-claims the
listener — chokepoint now waits for enabled rnsd + the spaced-instance ss-
truncation parser fix. Prevention: `check_rns_listener_owner` preflight in
`_lab_common.py` (allowlist rnsd/reticulum, fail-loud RuntimeError), 12 tests
in `tests/test_lab_common.py`. Detection recipe + invariant (one RNS host per
instance_name per box) in `persistent_issues_archive.md`.
Quick check: `sudo ss -xnpl | grep "@rns/"` — owner must be rnsd.
---

## #77 + #78 — row summaries (demoted 2026-07-31, MF012)

**#77 mqtt_root_drift** (2026-06-07): OBSERVED radio publish root (meshtasticd
journal, never queries the radio #17) vs DECLARED `gateway.json
mqtt_bridge.root_topic`; 2-tick; fix `meshtastic --host localhost --set
mqtt.root <declared>`. **#78 cron_verdict_stale** (2026-06-07): alerter for
wired-cron silence/FAIL (judges only `cron_verdict.sh`-wired crons; cadence
×3, 2h floor; INERT when none). ⚠️ post-07-10 a silent(never) page is REAL
(the pre-07-10 log-cap false leg is fixed, `d0254dae`); eval
`oracle-cron-silent-never-was-false`. Bodies in `persistent_issues_archive.md`.

---

## Delivery probes blind on the gateway-only box shape — RESOLVED (2026-07-31)

`delivery_confirmation_stall` + `delivery_write_canary` read ONLY the map's
`:5000` relay of `/api/gateway/delivery`, so on moc3 (role gateway-only, map
disabled BY DESIGN) they sat permanently `detector_blind_any` while the
gateway's truth was on disk the whole time. ⚠️ **NEVER cure this by starting
the map** — that re-runs the 07-24 deploy incident.
Cure: the gateway publishes full `snapshot()` to
`~/.local/share/meshforge/delivery_snapshot.json` (atomic, ts-stamped, rides
the 60s content_id_view throttle in `rns_bridge`); `_fetch_delivery_payload`
falls back to the file when :5000 is unreachable, refusing stale (>300s) /
misshaped / future-stamped corpses as indeterminate with the failing leg
named. Never the SQLite DB from root (#60 WAL-strand trap). Same-day port:
`probe_queue_backlog` falls back to `queue_stats.json` (writer in
`message_queue.py`, same guards). LXMF propagation probes split to
`watchdog_probes_gateway_lxmf.py` (MF025). Tests: the two
`*FileFallback` classes (16). Eval:
`detector_blind_gateway_only_2026_07_31.jsonl`.

---

## Issue #82: NomadNet boot-race gate hardcoded `@rns/default` — RESOLVED, body in archive (trimmed 2026-07-21)

The #69 fix became a worse fleet-wide bug: the nomadnet user-unit `ExecStartPre`
hardcoded `@rns/default`, so every box whose rnsd `instance_name` differs
crashlooped (NRestarts=7842, ExecStart never ran, **UNDETECTED 10 days**). Cure:
instance-agnostic `rnstatus` host-wait, fail-CLOSED `exit 75`, 120s window (MF
`96aa3d78` + `c3a62c01`). Prevention, 2 layers: `TestNoHardcodedRnsDefaultSocket`
blocks the CODE regression, and **`probe_nomadnet_crashloop`** closes the
DETECTION gap (`probe_service_inactive` is structurally blind to USER units).
Bonus: the "multi-chunk RNS reply drops chunks" symptom was downstream of this —
the bridge was fine, the box's own NomadNet was the broken reader. Full body +
detection recipe in `persistent_issues_archive.md`.

---

## meshtasticd VSZ leak (firmware#10468) — pthread stacks stranded, USB-radio boxes only (2026-07-10)

Symptom: hundreds of GB of **virtual memory** (VSZ) with normal RSS — tens of
thousands of paired 8MB+64KB **anonymous mappings** in `/proc/<pid>/maps`.
Portduino meshtasticd on a **USB (CH341) radio** leaks one joinable 8 MB
pthread **thread stack** per interrupt cycle (~9/min): the CH341 poll thread
runs the RadioLib ISR on ITSELF, so `pinedio_deattach_interrupt`'s self-join
guard SKIPS the join and the stack strands (`pine64/libch341-spi-userspace`;
strace/gdb-pinned 07-10). Live: ~561 GB VSZ / 71k anon maps @ day 5 (Pi5+USB);
SPI-radio boxes clean. **NO published build fixes #10468** — not 2.7.24,
2.7.26, or 2.8.

⚠️ **Our merged fix does NOT reach meshtastic builds (07-27).** pine64 merged
PR#10 (`pthread_detach(pthread_self())`) as `b0694ec8` on 07-19 — but
`meshtastic/firmware` switched `variants/native/portduino.ini` to its OWN fork
`meshtastic/libch341-spi-userspace@03bf505d` on **07-17, two days earlier**, so
it never carried over. That fork kept the self-join guard with NO detach
anywhere → still strands. Ported as **meshtastic/libch341-spi-userspace#2**
(open; operator has `push:false` there — only a maintainer can merge).
**Read the fork's source, never the version string** — "2.8 is newer" is not
"2.8 is fixed".

Cures: (1) patched builds on all 4 USB boxes (VolcanoAI/moc1/moc5/kiai) via
`/usr/local/sbin/meshtasticd-patched` + `50-canary-pinedio-fix.conf` drop-in;
rebuilt at 2.7.26 on 07-27, now pinning pine64 main `b0694ec8` (our fork no
longer needed). Recipe: `~/mtd-build/firmware` @ the release tag, swap the
portduino.ini ch341 pin, `pio run -e native` (~7 min Pi5 / ~26 min Pi4).
⚠️ **Two builds** — trixie links `libgpiod.so.3`, noble lacks it. ⚠️ **STASH
`.pio/libdeps/native/Pine libch341-spi Userspace library` first** — a stale
cache silently overrides the pin (07-27 it held the unsoaked PR#11 refactor);
verify by diffing the fetched source, then confirm `pthread_detach` inside
`pinedio_deattach_interrupt` via `objdump -d` BEFORE deploying.
(2) **weekly restart** band-aid `meshtasticd-restart.timer` STAYS until soak
proven (backstop-outlives-fix); (3) `probe_meshtasticd_vsz_leak` fires only
past the 768 GB weekly-restart envelope (leaking-but-managed stays silent).
Quick check: `wc -l /proc/<pid>/maps` — climbing over 30 min = leaking, flat
(≈8 stack pairs) = patched. ⚠️ `pgrep -x meshtasticd` MISSES the patched boxes
(comm is `meshtasticd-patched`); use `pgrep -f`. Detail:
[[project_updates_design_arc_2026_07_10]].

---

## node cache dropped `service_type` on load (2026-07-21) — row summary; full body in archive

Writer-with-no-reader (#4): `to_dict()` wrote `service_type`, `_load_cache()`
dropped it → false UNHEARD page vs a node heard 7x/25h. Three defects, all
needed (loader drop; `_merge_node` never refreshing `service_*`; once-recorded
name PERMANENT). **Tell**: UNHEARD + node otherwise alive = this cache gap.
⚠️ `rnprobe lxmf.propagation` is NOT a delivery test. MF `e383547c`/`48f5497d`,
MA `87cae734`/`0657c993`. Full body: `persistent_issues_archive.md`.

---

## mf.internal AAAA forwards to the WAN — the 900ms fleet-name tax (2026-07-25)

m1 answers only exact `(name, type)` static matches locally and **forwards
everything else to its WAN upstream**. Fleet names carry A records only, so
every AAAA for `<name>.mf.internal` goes to the internet and returns
NODATA — **with no SOA, so systemd-resolved cannot negatively cache it** and
pays that round trip forever. Every real tool (ssh, curl, urllib, getent)
uses `getaddrinfo` AF_UNSPEC and asks both families:

    m1  moc.mf.internal A     1.1ms      (local static entry)
    m1  moc.mf.internal AAAA 75.5ms      (forwarded; WAN baseline 75.8ms)
    12-host sweep  AF_UNSPEC 902ms  vs  AF_INET 1.7ms

So resolution was **coupled to internet reachability** — a WAN hiccup makes
healthy boxes look dead. Cure: `scripts/gen_fleet_hosts.py --apply` writes a
delimited `/etc/hosts` block (nss `files` precedes `dns`), all 9 boxes; 902ms
→ 4ms, and names resolve with DNS or the uplink down. Hourly per-box
`fleet_hosts_drift` cron, **self-healing since 07-27**
(`scripts/fleet_hosts_selfheal.sh`): drift → `--apply` → re-check the file.
A heal reports **CONCERN** naming what moved and self-clears next run — never
OK, or an hourly-churning box would look identical to a stable one.
UNOBSERVABLE never heals: blindness is not drift, and this file shadows DNS.

**Decision tell**: fleet-wide ~75-90ms per name lookup with A at ~1ms = this,
not a sick resolver. **Quick check**: compare
`getaddrinfo(name, AF_INET)` vs `AF_UNSPEC` timing — a ~75ms gap is the AAAA
leg. ⚠️ `/etc/hosts` SHADOWS DNS, so the block is seeded from **live DNS**,
never from the registry's `ip_fallback` snapshot (that would bake in a stale
copy and shadow the truth — the moc5 reshuffle class).

**Router-side DNS canNOT supersede this — measured 2026-07-26, don't re-open.**
m1's DNS proxy strips the authority section from every relayed answer, so no
negative answer through it is ever cacheable (RFC 2308 needs the SOA). Universal,
not mf.internal-specific; admin access on m1 does not help. And a router-side fix
would still couple fleet names to m1 being up. Full measurement in the archive.

**⚠️ cloud-init owns /etc/hosts too — it wipes the block on EVERY boot
(2026-07-27).** Boot-partition NoCloud user-data sets `manage_etc_hosts: true`
and `update_etc_hosts` runs at frequency **always**. moc5 rebooted 07-27 10:18
and lost all 12 names (`fleet_hosts_drift` caught it in 15 min); proven from
cloud-init's log — read 1214 bytes, wrote 545, byte-identical to
`/etc/hosts.bak-meshforge`. **Latent on all 8 cloud-init boxes**; moc5 was just
the first to reboot. Honest-failure-modes #8 — a writer shipped without
excluding the artifact's other owner.

Cure: `manage_etc_hosts: localhost` in **`/boot/firmware/user-data`** (keeps the
127.0.1.1 entry managed, stops the template render). ⚠️ **A
`/etc/cloud/cloud.cfg.d/` drop-in does NOT work** — user-data merges *over*
cloud.cfg.d; measured, block still wiped 1214→545. Applied + verified on all 8
cloud-init boxes 07-27. **Test without rebooting**: `sudo cloud-init single --name
update_etc_hosts --frequency always` — runs the real consumer-of-record rather
than trusting the config (calibrated_claims #7).

---

## A detector that reads what it audits is self-confirming (2026-07-25)

The `gen_fleet_hosts.py --check` drift detector used `socket.getaddrinfo()`
to fetch "what DNS says". But nss consults `files` (`/etc/hosts`) **before**
`dns`, and systemd-resolved also answers from `/etc/hosts` — so the check
compared the generated block **against itself**. A deliberately corrupted
entry reported `in sync` (rc=0), and `--apply` then said "already current"
and **refused to heal it**, because the corrupted file WAS the notion of
truth. It could never detect, nor repair, the one thing it exists to catch.

13 unit tests passed throughout: they mocked `resolve_a`, so the mock stood
in for the exact layer that was broken. **Only a live drill — corrupt a real
entry, run the real check — exposed it.**

Cure: query the upstream server DIRECTLY over UDP (servers discovered from
the resolved drop-in, never hardcoded — MF014), bypassing NSS. A silent
server is UNKNOWN and falls through, never NXDOMAIN. The test that had
**pinned the broken behaviour** now asserts the opposite: calling
`getaddrinfo` at all is a failure.

**The general rule** (calibrated_claims #7, in checker form): *a checker must
not consume the artifact it validates.* Ask what input would make the
detector and the thing it watches disagree — then feed it that input.

---

## A detector keyed to the wrong NAME reads healthy, not broken (2026-08-05)

Second instance of the rule above, from the other side: the checker did not
consume what it audits — it audited **something that did not exist**.

Both RNS probes build `@rns/<instance_name>`. The federator box's watchdog got
a name nothing served, so for **8.8 days** `rns_shared_instance_unresponsive`
sat `indeterminate` *blaming rnsd* while `rns_namespace_collision` reported an
affirmative **`clean`** — the #69 detector blind and green on the very box #69
happened to. rnsd was fine. Three defects, each enough to hide the others: (1) the name came from `~/.reticulum` via `get_real_user_home()`,
which under a **ROOT service is `/root`** — a stale root config beat rnsd's own
`--config /etc/reticulum`; (2) Linux answers a nonexistent **abstract** socket
with `ECONNREFUSED`, never `ENOENT`, so permanent misconfiguration was
indistinguishable from a transient rnsd shutdown; (3) `namespace_collision`
noted `clean` after matching **zero** listeners. New class
`rns_instance_name_mismatch` (degraded). Second leg: an **omitted**
`instance_name` left probes silently `inert` — RNS resolves the omission to
`default` itself, so absence is knowledge. Full account: that class's
`SIGNAL_CLASSES` comment.

**Decision tell**: an RNS probe `indeterminate`/`clean` while `rnstatus` is
plainly healthy = check the NAME first. **Quick check**:
`sudo ss -xnpl | grep @rns/` must match the watchdog's
`instance_name resolved to` log line.

⚠️ mini escalated this the whole time (`detector_blind_any` + three
`persistent_active` proposals at 70m/170h/170h), all rejected **`known_benign`**
— ⚠️ *corrected 2026-08-10; this line said "unspecified" and that was wrong.*
The rejections carried long, live-verified notes that proved **rnsd** healthy
(true) and concluded the **blindness** was benign — one even NAMED the broken
`~/.reticulum` probe path and filed it under "structural". Worse, the 08-02
rejection cited the 07-26 one as its warrant, so a wrong benign call became
memory and the memory re-justified the dismissal. **`known_benign` on a
`detector_blind` subject asserts "this detector cannot see and that is fine" —
which is only true if the organ is absent BY DESIGN, and then the probe must say
`inert`. A rejection may not cite a prior rejection as its warrant.**
The witness worked; the READ failed. A long-running `detector_blind` is a
finding, not furniture.

**Same day, same class, three more legs** (the other two long-blind
detectors). `delivery_confirmation_stall` never asked whether a gateway runs
here, so every non-gateway box fell through to "no confirmable protocol
recorded" forever — while its sibling `gateway_delivery_degraded` had always
said `inert`. `mqtt_root_drift` collapsed *no gateway.json at all* (nothing
here CAN be deaf → inert) into the same None as *unreadable* (indeterminate),
and separately treated journal silence as unobservable without ever checking
the journal WORKED — leaving four RX-only boxes permanently indeterminate.
**And the hole under the first**: `if not confirmable: return None` meant a box
that had NEVER confirmed could not trip the detector at all — a TOTAL collapse
read as nothing-to-judge while a partial one fired. Real gateways confirm
heavily (moc 16,759 RNS, moc3 26,732), so an empty `confirmed` bucket beside
live RNS traffic is a wiring fact. ⚠️ The federator box's "zero ever" that
prompted this was **test pollution, not telemetry** — see the row below; the
leg's logic stands, its motivating example did not. **Rule**: `inert` and `indeterminate` are different
claims — an organ that is absent by design must never be reported as an
observation that failed, or the real failures have nowhere to stand out.
