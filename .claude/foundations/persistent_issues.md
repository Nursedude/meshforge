# MeshForge Persistent Issues & Resolution Patterns

> What is still LIVE, and the decision tells an operator reaches for at a
> terminal — not the history of fixes (that is `persistent_issues_archive.md`).
> `@`-included by CLAUDE.md, so it loads on EVERY turn; MF012 caps it — **do not
> raise the cap.** A newly-resolved issue goes STRAIGHT to the archive; if you
> are demoting to make room, what you are adding belongs there too.
> Look one up across both files: `grep -n "#43" .claude/foundations/persistent_issues*.md`.
> Every story, date and count trimmed on 2026-10-29 is verbatim in the archive
> ("Tell rows — verbatim before the 2026-10-29 trim").

---

## RNS / LXMF are MeshForge-owned forks (SSOT)

`Nursedude/reticulum` + `Nursedude/lxmf`, pinned by tag AND SHA in
`requirements/rns.txt` (`# MF-FORK-PIN` — the pin lives there, not here);
`scripts/rns_version_check.py` gates the fleet on the `+mf.N` marker. The forks
cure the rnsd-RPC fragility class (#58/#61/#63/#68/#69/#72) at source; the
`rns_init.py` probe, MF009/MF019 and the watchdog `os._exit` backstop STAY until
a long soak proves the in-library fix.
- ⚠️ **Never rapid-cycle rnsd restarts fleet-wide** — opens the `@rns` race (#69); space them and check the owner before the next box.
- **Wire-compat invariant**: never change crypto primitives or the packet/announce/path-table format — that forks the NETWORK.
- **A box has more RNS envs than one probe sees** (user site, root dist-packages, pipx venvs, root user-site) — roll EVERY env per box, together with rnsd.
- Adopting upstream: `git merge <tag>` into `meshforge`, parity + public-net interop proof, canary one box, then roll — each fork's `FORK.md`.

---

## Resolved issues → `persistent_issues_archive.md` (index + bodies)

### Quick diagnostic tells — the part worth loading every turn

Symptom you can see at a terminal → class, the ONE quick check, the trap. Full bodies in the archive.

| Symptom | Class / first move |
|---|---|
| `[Errno 24]` or climbing fds on `:5000` | fd leak (#73) — restart `meshforge-map`, then find the leak |
| `rnstatus` hangs but the socket accepts | rnsd RPC wedge (#72) — `timeout 8 rnstatus >/dev/null \|\| echo wedged`; restart rnsd THEN its clients |
| `@rns/` listener owned by a non-rnsd process | namespace collision (#69) — `sudo ss -xnpl \| grep "@rns/"`; owner must be rnsd |
| RNS probe `indeterminate`/`clean` while `rnstatus` is healthy | probing a name this box doesn't serve (2026-08-05) — the ss owner must MATCH the watchdog's `instance_name resolved to` line |
| a disposition-collapse fix landed and the fleet went quiet | grep EVERY branch that reaches the same return, not just the one the incident came through (2026-08-09) — the 07-28 dups fix taught the *payload* branch to read the collector-cron declaration, curing every box that ANSWERS on `:5000`; the *transport* branch one line above kept the collapse, so moc3 (gateway-only, no map by design) stayed blind 12 more days. 7 of 8 boxes sharing a shape makes a partial fix look complete |
| a probe is blind on ONE box, clean on the other seven | ask what's DIFFERENT about that box's shape, not what's BROKEN on it (2026-08-09) — `rns_version_drift` blamed "service-user env unreadable" on moc4 for 12.3 days; moc4 is the one box with no `~/.local` (installs to `/usr/local/lib/python3.11/dist-packages`) and was sitting exactly ON the pin. Grep the probe's SIBLINGS: `probe_rns_env_coherence` had listed dist-packages all along and read `clean` on that box the whole time |
| a box pages `DOWN` but its own `uptime` says it never rebooted | you observed a PATH, not a box (2026-08-11) — ask what that box's ONLY route runs through, and whether it was being worked on. kiai reaches the manager solely via the tunnel alaula pins, so the 08-10 T1 drill's factory-reset of alaula produced 54 min of "Fleet box DOWN: kiai" about a box up 17 days. `fleet_offline_check.sh` now takes a per-box `via` dependency and pages **UNOBSERVABLE — state UNKNOWN** when the path is down too. ⚠️ Neither excuse nor exclude by default: a dependency that ANSWERS leaves the box implicated, and that page is real |
| a probe tells you to check a systemd unit, or blames another probe that "owns" it | check that unit EXISTS here FIRST (08-09: `synth_soak_degraded` named a timer this box never had, 78 days). `systemctl show -p MainPID -p LoadState <unit>`: `LoadState=not-found` means the DETECTOR is the defect (08-12 twin: four classes `indeterminate` blaming `service_inactive` for a unit with no unit file — nothing owned them) — `absent` must read `inert`, never `indeterminate` |
| `systemctl --user is-enabled` says `enabled` but the unit isn't in `timers.target.wants` | enablement is a symlink under **ANY** `*.target.wants` (2026-08-09) — `meshanchor-map-restart.timer` lives in `default.target.wants` while declaring `WantedBy=timers.target`. A reader that opens one dir calls a live timer disabled. `ls ~/.config/systemd/user/*.target.wants/` |
| `:9443` web client deaf while RX is healthy | leaked `TCPInterface` starving PhoneAPI (#17/#75) — restart the map; honest RX record is `grep 'Received text msg'` |
| meshtasticd `/json/report` or `/json/nodes` 404 | ESP32-only, NEVER served by meshtasticd (#76) — not a fault |
| a NEW `http_local_unresponsive` | a NEW class — the GIL-serialization family (#70/#71) is closed by response caches |
| service `active` but its writes vanish | systemd sandbox path drift (#60) — check `ReadWritePaths=` vs where the code writes |
| meshtasticd moved off `:9443` after a HAT change | upstream overlay smuggled `Webserver: Port:` (#58) |
| a probe reports `indeterminate` for DAYS | it is a FINDING, not weather — and `inert` (absent by design) is a different claim (2026-08-05) |
| service crashloops after power loss with `OSError`/`InsufficientDataException` on a state file | zero-byte power-loss truncation (2026-08-27: 13 corpses fleet-wide post-Lala — 12 LXMF `*.ratchets`, 2 NUL jsonl lines). Quarantine the corpse, restart, announce. ⚠️ the gateway's steady-state error is `already registered destination` — scroll the journal to the FIRST failure after start; `quarantine_corrupt_ratchets()` self-heals the LXMF leg since `_rns_bridge_connection.py` 2026-08-27 |
| meshtasticd crashloops in ~300ms with `gpiod_line_request_reconfigure_lines: Assertion 'request' failed` right after "Final Tx power" | the SPI-HAT overlay claims a line the KERNEL already owns (2026-08-29 lehua/trixie: `CS: 8` vs spi0's CS0=GPIO8). Drop the CS line — spidev manages CS; check `/sys/kernel/debug/gpio` for the double-claim |
| a detector reports an age in the MILLIONS of minutes (29,806,174 min = 56.7 yr) | an absent-value sentinel leaked into the measurement domain (2026-09-02) — that number is `now - 0`, i.e. epoch. Go to the probe's fallback branch, not the subject: `nomadnet_silence_watch.py` ran `stat -c %Y <file> \|\| echo 0`, so "no logfile here" and "NomadNet died" rendered identically, and BOTH landed in the alarm state. ⚠️ the latch is worse than the number — those boxes never left `quiet`, so a REAL silence could no longer fire a transition. Absent-by-design must read `inert`, never the alarm |
| `uptime` disagrees with wtmp/`who -b`, or crons barely fire while cron is `active` | the clock ran days-stale through a WAN outage (RTC-less Pi: fake-hwclock restores stale time, NTP unreachable can't step it; moc4 ran ~8 days behind, 5 cron fires in 4 days). Wall-clock instruments (cron, verdict freshness, wtmp) all lie together; trust monotonic `uptime`. Fix class = LAN-internal NTP (see Starlink-resilience notes 2026-08-27) |
| a debounced probe reads `held by debounce` / `first sighting recorded` on EVERY tick for days while its condition is plainly present | its streak saver cannot WRITE (09-02, #60 sandbox class) — the streak froze 1 below the debounce, so it can never fire. Check the state path, not the subject: `sudo touch /var/lib/meshforge/.w && sudo rm /var/lib/meshforge/.w`. ⚠️ a fixed mechanism has COPIES — `grep` them (hfm #5). Measure the class with `scripts/falsifiability_drill.py`; re-run it, never carry its number |
| an advisory sweep says an apt-managed box carries N advisories, or `dpkg -l` shows a `+deb12uN` / `+deb13uN` / `ubuntuN.M` suffix | the distro backported the CVE without bumping the upstream version (09-06 moc4 urllib3: 7 of 8 in `changelog.Debian.gz`). **Never pip over apt** — cure = apt + `unattended-upgrades`; `dep_advisory_check.py` tags `[apt <ver>]` and takes an EXPIRING accept list. ⚠️ the advisory DB does not normalise `_`→`-`: canonicalise before asking |
| an RNS/RPC alarm (`rns_rpc_unresponsive`, cascade `rns_rpc_wedge`) fires and self-CLEARS inside one tick, or repeats at a FIXED minute past the hour | the detector timed a SUBPROCESS and named the SUBJECT (09-08) — `rnstatus` imports RNS first, so its wall time measures the BOX's CPU (apt-daily, an hourly cron). **Check before touching rnsd**: `uptime` + `journalctl \| grep 'rpc\[rnsd\.'`; sub-ms round trips = healthy. ⚠️ a real wedge does NOT self-heal — self-clearing IS the tell. Cured: 3 consecutive timeouts (`MESHFORGE_RNS_RPC_CONFIRM_TICKS`). ⚠️ the false page said "restart rnsd" — the #69 trigger |
| `ssh <name>` gets Permission denied and an unfamiliar host key, while a sibling name to the same address works | two NAMES sharing one NAT front, not one box with a broken config (2026-09-10, hap/moc1). **Quick check**: `ssh-keyscan -t ed25519 <a> <b>` — no auth attempted; an IDENTICAL fingerprint means one sshd, so the names are one endpoint. hap:22 and moc1:22 match (the front forwards :22 to moc1); hap:2222 is the hAP itself. ⚠️ Do NOT declare `via:` between them — the via probe is `ssh <via> true`, so it would consume the box it excuses and turn a real outage into UNOBSERVABLE forever (the 07-25 self-confirming class) |
| a condition reads RESOLVED while a live check says it is present — classically, N boxes show it active and one does not | the watcher's cooldown ate the OBSERVATION (09-02) — mini skipped setting `currently_active` inside a rule's cooldown, so brief/rollup read RESOLVED. **Tell**: a same-condition disagreement BETWEEN boxes is rule TIMING — compare `last_fired_ts` with `cooldown_s`; do NOT restart the subject (moc3's map is off BY DESIGN). Cured: cooldown limits the ACTION only (`edge_*_suppressed` witnesses). ⚠️ live on any box whose mini isn't rolled |
| a checker reports `in sync` / `clean` and its `--apply` then refuses to heal a corruption you PLANTED | it consumes the artifact it validates (07-25 `gen_fleet_hosts.py --check`: getaddrinfo reads `/etc/hosts` FIRST, so it compared the file to itself). ⚠️ unit tests mocked the broken layer; only a LIVE drill caught it. **Rule**: *a checker must not consume the artifact it validates* — feed it the input that makes detector and subject DISAGREE |
| a fleet name resolves fine, matches the registry, and reaches the WRONG BOX | an AREDN front was REASSIGNED to another node of ours (09-11); `/etc/hosts` shadows DNS and BOTH went stale, so the drift check agreed with itself. **Check**: ask the address who it is — `curl -s http://<ip>:8080/a/status \| grep -oE 'WH6GXZ-6-[A-Z-]+'` — and match host keys (`fleet_naming_drift_check --verify-identity`). ⚠️ `<alias>:22` on a shared front is the NEIGHBOUR's sshd — stamp with `ssh_port`. Drill: `scripts/fleet_identity_drill.py` |
| a whole subnet of boxes reads unreachable after a router/uplink change, while each box's own uptime is days | the MANAGEMENT plane broke, not the boxes (09-12: router to bridge, rogue DHCP). **On the box**: `ping $(ip route show default \| awk '{print $3}')` — a dead gateway beside live neighbours = no router. No IPv4 truth: `ping -6 -c4 ff02::1%eth0` AND an ARP sweep, then `ssh user@fe80::…%eth0`. ⚠️ `/etc/hosts` and DNS go stale TOGETHER — never `gen_fleet_hosts.py --apply` then. Cure ON the box: `nmcli con mod … ipv4.method manual` to the registry address; match `expect_hostkey` |
| meshtasticd cycles on `SX126x init result -2` / `No sx1262 radio`, intermittently, on a HAT that worked before | the 40-pin header is a MECHANICAL POWER contact (09-14 lehua: failed standoff). **Discriminator**: a warm reboot NEVER helps, a cold cut sometimes does. ⚠️ `vcgencmd get_throttled`=`0x0` is BLIND to the HAT rail. ⚠️ `is-active` is a false green while it cycles — read `NRestarts` + the init line. ⚠️ re-aiming the antenna is a mounting event: re-verify `init result 0`. One software pass (spidev, `CS:`, GPIO claims), then go physical |
| an RNode's `rnstatus` `Rate` is not what its config's SF/BW give (SF7/250 kHz = 10.94 kbps), or TX grows while RX is flat at BOTH ends of one link | the radio reverted to its STORED defaults and rnsd never re-checks (2026-09-22 VolcanoAI: 585.94 bps = SF12 vs config SF7; moc3 deaf ~2 h). rnsd validates radio params only when it (re)opens the port. **Fix**: restart rnsd on that box, then check the `@rns/` owner; proof = the peer's RX rises by exactly this box's TX. ⚠️ `rf_leg_silent`'s cure text names only promiscuous. OPEN: what resets the board (4 serial drops 09-16→21, no USB event; reads `Battery … discharging`) |
| a Heltec **v3** RNode reads a steady `Intrfrnc −71 dBm` and its TX is slow or LOST while a v4 beside it is clean | **the v3 itself makes the −71; interference avoidance then holds its TX** (10-28 bench: IA on 9/20 delivered, off 20/20). **Fix**: `rnodeconf <port> --config` → if `Interference avoidance : Enabled`, `rnodeconf <port> -X` (stop rnsd via the ordered restart first). Firmware fix (bounded hold, IA can stay on): `Nursedude/RNode_Firmware` `meshforge` `11d51d7`, accepted 40/40 and 60/60 (max = the 5 s cap, `5296b02c`). ⚠️ read each unit — a once-configured v3 reads Enabled. ⚠️ `Intrfrnc … N ago` is LAST-SEEN — filter by age |
| every node offline while meshtasticd logs RX (`num_online_nodes=0`) | started before NTP sync → clock rated Device, rx_time 0, lastHeard frozen (10-02 moc4, moc2). Restart once synced; gate `20-wait-for-ntp-sync.conf` |
| a radio's owner name is ANOTHER node's name — often a default like `Meshtastic 8d30` — with a `",` fragment, and the short name is that node's too | **the TUI "Set Owner/Node Name" pre-fill wrote it** (09-20): it took the LAST `longName` in `--info` (a stranger) and left `",`; one Enter sent it. Fixed: parse only the `Owner:` line, refuse a `"`. **Check**: `journalctl -u meshtasticd \| grep "Send owner"`; `/root/.portduino/default/prefs/device.proto` mtime = last owner write |
| `meshtastic_install_audit.py` says OK while the watchdog pages `dep_install_fragmented` on the same box | **the audit sees only what its CALLER can read** (2026-09-22): run as the operator it cannot enumerate `/root/.local` (root's pipx venv / root user-site), while the watchdog runs as root and can. `sudo python3 scripts/meshtastic_install_audit.py` is the honest form; a roll script run as the operator misses the same class — roll root's copies with `sudo pipx` / `sudo pip --break-system-packages`. Also: the audit reads OK for copies ABOVE the floor, so upward drift (4 boxes carried unrecorded 2.7.10/2.7.11) is invisible to it |
| a radio setting REVERTS on restart | uncommitted edit txn (web client): TUI TX Power Truth → PENDING |
| our tooling says `Timeout while stopping <unit>` and the unit later reads `failed` and stays down | **the systemctl CLIENT timed out; the stop JOB outlived it and landed** (2026-09-29 F1: a 30 s client wait vs TimeoutStopSec 90 s/5 min). A client timeout is not the unit's state — `is-active` AFTER; an explicit stop during `deactivating` cancels `Restart=`. Cured in `hold_rns_clients`: wait the unit's own `TimeoutStopUSec`, re-ask, `release.ok` refuses while `not_stopped` |
| the radio publishes to an MQTT root nobody declared | `mqtt_root_drift` (#77) — compares the OBSERVED root in the meshtasticd journal (never queries the radio, #17) against `gateway.json` `mqtt_bridge.root_topic`; 2-tick. Fix: `meshtastic --host localhost --set mqtt.root <declared>` |
| `cron_verdict_stale` says a cron has been silent FOREVER (never ran) | since 2026-07-10 that page is REAL — the old log-cap false leg is fixed (`d0254dae`). It judges only `cron_verdict.sh`-wired crons (cadence x3, 2h floor) and reads `inert` when none are wired (#78) |
| a USER systemd unit crashloops and no probe ever says so | `probe_service_inactive` is structurally BLIND to user units (#82, 2026-07-21 — nomadnet crashlooped `NRestarts=7842` for 10 days, undetected). Check user units with `systemctl --user`, never plain `systemctl`; `probe_nomadnet_crashloop` closes that one gap, not the class |
| a boot-time unit dies on a network op while its OWN `ExecStartPre` guard reported `status=0/SUCCESS` | the guard gates on a NAME, the failure is ROUTABILITY (09-17): with the fleet `/etc/hosts` block, `getent hosts` succeeds from a flat file with the NIC down. **Tell**: a NAME-gated boot guard is inert on any box with a static hosts block — gate on a TCP connect to the real peer. ⚠️ two correct fixes disarmed each other; ask *what would still pass this check if the feature were dead?* |
| `@rns/<instance>` owned by a **non-rnsd pid** after a reboot | #69 boot race (cured 09-19): a readiness wait that accepted the socket present-by-ANYONE ended on a squatter. ⚠️ **check ownership AFTER the wait** — a guard that verifies at the one moment the answer cannot be wrong verifies nothing. **Check**: `sudo ss -xnpl \| grep "@rns/"` — owner MUST be `rnsd`. **Repair order**: stop squatter → restart rnsd → start squatter. ⚠️ rnsd `status=255` at boot = AutoInterface on a `tentative` link-local → drop-in `10-wait-for-ipv6-ll.conf`, which NO deploy script installs: `ls /etc/systemd/system/rnsd.service.d/`. User-unit logs: `sudo journalctl _SYSTEMD_USER_UNIT=<unit>` |
| Config Doctor / drift check says rnsd's configdir is "not determinable; assuming default resolution matches" | the detector was blind to a NON-ROOT rnsd (09-28 meshanchor-server). RNS resolves `/etc/reticulum` FIRST for ANY uid when `/etc/reticulum/config` exists, then the service user's home; the check resolved uid 0 only. Cured MF+MA. **Check**: `systemctl show rnsd -p ExecStart -p User` + `ls /etc/reticulum/config` — a missing `--config` matters only if that file is absent |
| CI red on a commit that CANNOT have caused it (docs/comments only), or one interpreter leg failing alone | **re-run the LAST GREEN commit unchanged before believing it was you** (09-20: pip resolved newest-that-minute → `requirements/ci.txt`). ⚠️ a failing CLASS whose VICTIM MOVES is a shared resource being stolen (a `patch("mod.subprocess.run")` patches the SHARED module). ⚠️ capture the baseline BEFORE re-running — a rerun REPLACES the prior log |
| fleet-wide name lookups ~75-90ms while an A-only query is ~1ms | the AAAA leg forwarding to the WAN (07-25: NODATA with no SOA, never negative-cached). Cured by the `/etc/hosts` block (`gen_fleet_hosts.py --apply`, hourly self-heal). ⚠️ cloud-init re-wipes it every boot unless `manage_etc_hosts: localhost` is in **`/boot/firmware/user-data`** (a `cloud.cfg.d/` drop-in does NOT work). ⚠️ seed from live DNS — this file SHADOWS DNS |

---

**Before committing** (beyond CLAUDE.md's NEVER list): actionable error messages
at the right log level; utilities from their central module, never duplicated.

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


## meshtasticd VSZ leak (firmware#10468) — USB-radio boxes only

Portduino meshtasticd on a USB (CH341) radio strands one 8 MB pthread stack per
interrupt cycle (~9/min): hundreds of GB VSZ, normal RSS. **Fixed in firmware
`develop` (ch341 `d85aceb7`, `pthread_detach` ×2) — in NO release yet**
(re-verified at source 2026-09-30).
- ⚠️ **Do not roll meshtasticd**: `apt-mark hold` on all 9 boxes; the OBS alpha repo is disabled. A stock roll today REGRESSES the 4 USB boxes. Keep the hold anyway: beta moves too. For the next release, grep its portduino.ini ch341 ref's `libpinedio-usb.c` for the detach — **read the pinned source, never the version string.**
- USB boxes (VolcanoAI/moc1/moc5/kiai) run `/usr/local/sbin/meshtasticd-patched` via `50-canary-pinedio-fix.conf`. Rebuild recipe and its traps (two builds trixie/noble; stash the stale `.pio/libdeps` cache; `objdump` the detach before deploying): the archive.
- `meshtasticd-restart.timer` (weekly) STAYS until the soak proves the fix; `probe_meshtasticd_vsz_leak` fires only past the 768 GB envelope.
- **Quick check**: `wc -l /proc/<pid>/maps` climbing over 30 min = leaking, flat = patched. ⚠️ `pgrep -x meshtasticd` MISSES the patched boxes — use `pgrep -f`.
