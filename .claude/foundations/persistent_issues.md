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
| meshtasticd crashloops in ~300ms with `gpiod_line_request_reconfigure_lines: Assertion 'request' failed` right after "Final Tx power" | the SPI-HAT overlay claims a line the KERNEL already owns (2026-08-29 lehua/trixie: `CS: 8` vs spi0's CS0=GPIO8). Drop the CS line — spidev manages CS; check `/sys/kernel/debug/gpio` for the double-claim |
| a detector reports an age in the MILLIONS of minutes (29,806,174 min = 56.7 yr) | an absent-value sentinel leaked into the measurement domain (2026-09-02) — that number is `now - 0`, i.e. epoch. Go to the probe's fallback branch, not the subject: `nomadnet_silence_watch.py` ran `stat -c %Y <file> \|\| echo 0`, so "no logfile here" and "NomadNet died" rendered identically, and BOTH landed in the alarm state. ⚠️ the latch is worse than the number — those boxes never left `quiet`, so a REAL silence could no longer fire a transition. Absent-by-design must read `inert`, never the alarm |
| `uptime` disagrees with wtmp/`who -b`, or crons barely fire while cron is `active` | the clock ran days-stale through a WAN outage (RTC-less Pi: fake-hwclock restores stale time, NTP unreachable can't step it; moc4 ran ~8 days behind, 5 cron fires in 4 days). Wall-clock instruments (cron, verdict freshness, wtmp) all lie together; trust monotonic `uptime`. Fix class = LAN-internal NTP (see Starlink-resilience notes 2026-08-27) |
| a debounced probe reads `held by debounce` / `first sighting recorded` on EVERY tick for days while its condition is plainly present | its streak/baseline saver cannot WRITE (2026-09-02, falsifiability phase 2) — `/var/lib/meshforge` unwritable (#60 sandbox class) froze the streak at 1 below a debounce of 2, so `propagation_soak_degraded`, `synth_soak_degraded` and `history_write_stalled` could never fire; the reason named the symptom and hid the cause. THREE copies of one saver existed and only the parity copy had been cured (07-26) — when a mechanism is fixed, `grep` for its copies (hfm #5). Check the state path, not the subject: `sudo touch /var/lib/meshforge/.w && sudo rm /var/lib/meshforge/.w`. ⚠️ the measured half of that audit is `scripts/falsifiability_drill.py` — it kills each probe and reports which classes the suite would let die silently; re-run it, never carry its number |
| an advisory sweep says an apt-managed box carries N advisories, or `dpkg -l` shows a `+deb12uN` / `+deb13uN` / `ubuntuN.M` suffix | the distro backported the CVE without bumping the upstream version (2026-09-06: moc4 `urllib3 1.26.12-1+deb12u4` read as 8 advisories, 5 high — seven were in `changelog.Debian.gz`, the eighth Debian had triaged `ignored`). **Never pip over apt** — that is how the manager grew three cryptography dist-infos; the cure is apt + `unattended-upgrades` (ABSENT on 9 of 10 boxes that day, 35 pending security updates per trixie box). `dep_advisory_check.py` now tags `[apt <ver>]`, credits `distro-patched`, and takes an EXPIRING accept list for what the distro declines. ⚠️ the advisory DB does not normalise `_`→`-` (PyPI does): `prometheus_client` would read clean forever — canonicalise before asking |
| an RNS/RPC alarm (`rns_rpc_unresponsive`, cascade `rns_rpc_wedge`) fires and self-CLEARS inside one tick, or repeats at a FIXED minute past the hour | the detector timed a SUBPROCESS and named the SUBJECT (2026-09-08) — `rnstatus` is a fresh interpreter that imports RNS before it speaks one byte of RPC, so its wall time measures the BOX's CPU headroom. moc3: apt-daily-upgrade (61s CPU, 273 MB, 35 MB swap) tripped it while the gateway logged `rpc[rnsd.path_table_read] ok 0.000s` every 10s straight THROUGH the "wedge". moc: fired only at :07:51-:08:34, 50-72s after the `7 * * * *` kilo-matrix cron, 9x in 7 days, while the direct rnstatus probe fired ZERO times on that box. rnsd listens with a **zero-length accept backlog** (`Send-Q 0` on the `@rns/*/rpc` LISTEN row), so a concurrent connect queues in SYN-SENT correctly. **Quick check before touching rnsd**: `uptime` + `journalctl \| grep 'rpc\[rnsd\.'` during the alarm; sub-ms round trips = healthy. ⚠️ a wedge does NOT heal without a restart — self-clearing IS the tell. Cured `eb8a6529`/`a2827005` (MA `4a24b939`): N consecutive timeouts required (default 3, `MESHFORGE_RNS_RPC_CONFIRM_TICKS`), same-inode 0.4s re-sample for the SYN-SENT sampler, loadavg+PSI on the page. ⚠️ the false page's cure text was "restart rnsd" — the #69 race trigger |
| `ssh <name>` gets Permission denied and an unfamiliar host key, while a sibling name to the same address works | two NAMES sharing one NAT front, not one box with a broken config (2026-09-10, hap/moc1). **Quick check**: `ssh-keyscan -t ed25519 <a> <b>` — no auth attempted; an IDENTICAL fingerprint means one sshd, so the names are one endpoint. hap:22 and moc1:22 match (the front forwards :22 to moc1); hap:2222 is the hAP itself. ⚠️ Do NOT declare `via:` between them — the via probe is `ssh <via> true`, so it would consume the box it excuses and turn a real outage into UNOBSERVABLE forever (the 07-25 self-confirming class) |
| a condition reads RESOLVED while a live check says it is present — classically, N boxes show it active and one does not | the watcher's rate limiter ate the OBSERVATION (2026-09-02) — mini's engine `continue`d on cooldown BEFORE setting `currently_active`, with the key already in `matched_keys`, so a live condition returning inside its own cooldown sat matched-but-inactive; `brief.py`, `rollup.py` and `dreams.detect_persistent_active` all gate on that flag and read it RESOLVED for up to a full cooldown. Measured: moc3's federation backoff live and invisible on the federator for 14h under `cooldown_s=86400` while moc/moc1/moc2 showed it active. **Tell**: a same-condition disagreement BETWEEN boxes is per-box rule TIMING, not the subject — compare `last_fired_ts` against `cooldown_s`; do NOT restart the subject (moc3 is `role=gateway-only`, its map is off BY DESIGN, and starting it re-runs the 07-24 incident). 88 of 144 seeded rules carry cooldown ≥1h, four at 24h including `detector_blind_any`. Cured: cooldown rate-limits the ACTION only; an activation recorded under suppression is `announced=False`, emits `edge_up_suppressed`/`edge_down_suppressed` witnesses, and never pages a CLEAR for an alarm never raised. ⚠️ LIVE on every box whose mini is not yet rolled |
| a checker reports `in sync` / `clean` and its `--apply` then refuses to heal a corruption you PLANTED | it consumes the artifact it validates (2026-07-25, `gen_fleet_hosts.py --check`) — it read "what DNS says" via `getaddrinfo`, but nss answers from `/etc/hosts` FIRST, so it compared the generated block against ITSELF and the corrupt file WAS its notion of truth. ⚠️ 13 unit tests passed throughout: they mocked the exact layer that was broken, so only a LIVE drill exposed it. **Rule** (calibrated_claims #7 in checker form): *a checker must not consume the artifact it validates* — ask what input would make the detector and its subject DISAGREE, then feed it that. Recurs constantly: 2026-09-15 alone, a drift guard comparing two hardcodes it also declared, and a `pgrep -f pytest` preflight that counted itself |
| a fleet name resolves fine, matches the registry, and reaches the WRONG BOX | an AREDN front got REASSIGNED to a different node of ours (2026-09-11: bi-ecom vacated `.249`, volcano-hi-hap took it ~20 min later). `/etc/hosts` SHADOWS DNS, so the name is not unreachable — it is confidently wrong. `fleet_naming_drift_check` was structurally blind: its two inputs are `/etc/hosts` (getaddrinfo — nss `files` precedes `dns`) and the registry's `ip_fallback`, and BOTH went stale in the same event, so they agreed and it printed `OK: 14 hosts resolve+match registry`. **Quick check**: ask the address who it is — `curl -s http://<ip>:8080/a/status \| grep -oE 'WH6GXZ-6-[A-Z-]+'` — and match host keys; "it answers" is not "it is ours". Cured 2026-09-11: registry `ssh_port` + `expect_hostkey`, `fleet_naming_drift_check --verify-identity` hourly on the manager, seeded by `scripts/fleet_hostkey_stamp.py`. ⚠️ **`<alias>:22` is NOT the alias's own sshd on a shared front** — the stamper's collision guard caught TWO pairs live (hap↔moc1, lehua↔trdev); stamping without `ssh_port` records the neighbour's identity. Proof it can fail: `scripts/fleet_identity_drill.py` |
| a whole subnet of boxes reads unreachable after a router/uplink change, while each box's own uptime is days | the MANAGEMENT plane broke, not the boxes (2026-09-12). A router moved to BRIDGE keeps passing frames but stops ANSWERING, so every box holding that subnet's lease kept a valid-looking default route to a gateway that no longer exists — internet and federation dead, rnsd FINE. Two DHCP servers then shared one L2 domain and raced every lease, waking a box on the wrong subnet from its own fleet. **Quick check, ON the box**: `ping $(ip route show default \| awk '{print $3}')` — a dead gateway beside live on-wire neighbours is "no router", never "box down". ⚠️ `/etc/hosts` and DNS went stale in the SAME event so they AGREED; `gen_fleet_hosts.py --apply` would have re-baked the dead map and reported success (09-11's blindness at fleet scale). **What worked when every IPv4 assumption failed: IPv6 link-local** — `ping -6 -c4 -i 1 ff02::1%eth0`, then `ssh user@fe80::...%eth0` identifies a box with no DHCP, no router and no DNS. ⚠️ incomplete alone (one box ignored multicast; the ARP sweep caught it) — use both. The data plane is identity-addressed and survived; every observer we own speaks IP and lied at once. ⚠️ **The cure is ON the box, not in the other DHCP server** (09-13: moc4 taken by the rogue scope ONLY after its .86 renewal went UNANSWERED — a squatter fills a vacuum) — pin the registry address with `nmcli con mod ... ipv4.method manual`, mirroring a working box on that L2 for gateway/DNS. NM then persists a profile to `/etc/NetworkManager/system-connections/`, which is EMPTY while the box is on DHCP. Confirm from a box ON that segment and match `expect_hostkey` — reachable is not ours |
| meshtasticd cycles on `SX126x init result -2` / `No sx1262 radio`, intermittently, on a HAT that worked before | the 40-pin header is a MECHANICAL POWER contact, not a software fault (2026-09-14 lehua, 5 incidents/10 d; operator: *\"power was not adequately supplied to the hat, the slightest change tripped it\"* — failed standoff, cocked cantilever). **Discriminator**: a warm reboot NEVER helps (rails never drop) while a cold cut sometimes does, and it can recover unattended minutes after boot as the Pi warms. ⚠️ `vcgencmd get_throttled`=`0x0` is BLIND to the HAT's rail — it read clean through the failing boot; never rule power out with it. ⚠️ `is-active` is a FALSE GREEN while the unit cycles — read `NRestarts` + the init line. ⚠️ the antenna is a LEVER on that header: re-aiming it is a mounting event, re-verify `init result 0` after. Give the software layer ONE pass (spidev, bus, `CS:` line, GPIO claims), then go physical |
| an RNode's `rnstatus` `Rate` is not what its config's SF/BW give (SF7/250 kHz = 10.94 kbps), or TX grows while RX is flat at BOTH ends of one link | the radio reverted to its STORED defaults and rnsd never re-checks (2026-09-22 VolcanoAI: 585.94 bps = SF12 vs config SF7; moc3 deaf ~2 h). rnsd validates radio params only when it (re)opens the port. **Fix**: restart rnsd on that box, then check the `@rns/` owner; proof = the peer's RX rises by exactly this box's TX. ⚠️ `rf_leg_silent`'s cure text names only promiscuous. OPEN: what resets the board (4 serial drops 09-16→21, no USB event; reads `Battery … discharging`) |
| a MeshCore channel filter refuses EVERYTHING, every channel message reads as slot 0, or a message sent on Public logs as the private channel | **the wire's slot index is `channel_idx`; `from_meshcore` read `channel`, a key the library never sends** (2026-09-18; root cause found by the adversarial review, VERIFIED against the twin's live venv, meshcore_py 2.3.7 `reader.py`: CHANNEL_MSG_RECV sets `channel_idx` + `type` 'CHAN'/'PRIV', never `channel`/`is_channel`/`destination`). So `metadata['channel']` was ALWAYS 0 → the 09-18 index guard refused 100% (`8e9ac049` revert) — and the diagnosis that followed, "the channel is a NAME in the TEXT", was the FOURTH wrong assumption about this field: the text header is the sending node's NAME (`meshanchor p4: wx` from node "meshanchor p4"), which is why two cmds sent on PUBLIC logged `[ch:meshanchor]`. Cured MF `3bcc31b0` / MA `8540d7ec`: read `channel_idx`, `None` when absent (absent is unknown, never Public), ingress INFO line `MeshCore channel rx idx=<n> keys=[…]`, bridge tag `[ch:<idx>]`. ⚠️ **Do NOT revert MF's guard `8c5b5fee`** — an earlier version of this row said to, and named `8910c156`, which IS the ChannelPath seam it said to keep; the guard works once the index is real (inert on the 9 boxes only via `meshcore.enabled:false`). ⚠️ A fixture that fabricates `channel`/`is_channel` pins the author, not the wire — feed `reader.py`'s keys. **Quick check**: the daemon journal's `MeshCore channel rx idx=` line, or `grep -n channel_idx <venv>/meshcore/reader.py`. ⚠️ The oracle gate moved to the slot index in the same cure (MA `8457062e`; live-verified 09-20: Public refused) — a 09-20 note saying STILL OPEN was carried, not re-measured. Channel rx carries NO sender pubkey (a name is only a cooldown key); DMs carry `pubkey_prefix` |
| a radio's owner name is ANOTHER node's name — often a default like `Meshtastic 8d30` — with a `",` fragment, and the short name is that node's too | **the TUI "Set Owner/Node Name" dialog wrote it** (root-caused 2026-09-20): its pre-fill scanned the whole `meshtastic --info` output for any `longName` line, kept the LAST one (a stranger in the node list) and `strip('"')`-ed it, leaving `",`; one Enter on the "current" name sent it with `--set-owner`. The desktop box's radio lost its real name this way more than once; the fleet's node histories still show the aliases. Fixed: parse only the `Owner: <long> (<short>)` line, blank if absent, refuse a `"`. **Quick check**: `journalctl -u meshtasticd \| grep "Send owner"` — the name it announces; `/root/.portduino/default/prefs/device.proto` mtime = when the owner was last written |
| `meshtastic_install_audit.py` says OK while the watchdog pages `dep_install_fragmented` on the same box | **the audit sees only what its CALLER can read** (2026-09-22): run as the operator it cannot enumerate `/root/.local` (root's pipx venv / root user-site), while the watchdog runs as root and can. `sudo python3 scripts/meshtastic_install_audit.py` is the honest form; a roll script run as the operator misses the same class — roll root's copies with `sudo pipx` / `sudo pip --break-system-packages`. Also: the audit reads OK for copies ABOVE the floor, so upward drift (4 boxes carried unrecorded 2.7.10/2.7.11) is invisible to it |
| the radio publishes to an MQTT root nobody declared | `mqtt_root_drift` (#77) — compares the OBSERVED root in the meshtasticd journal (never queries the radio, #17) against `gateway.json` `mqtt_bridge.root_topic`; 2-tick. Fix: `meshtastic --host localhost --set mqtt.root <declared>` |
| `cron_verdict_stale` says a cron has been silent FOREVER (never ran) | since 2026-07-10 that page is REAL — the old log-cap false leg is fixed (`d0254dae`). It judges only `cron_verdict.sh`-wired crons (cadence x3, 2h floor) and reads `inert` when none are wired (#78) |
| a USER systemd unit crashloops and no probe ever says so | `probe_service_inactive` is structurally BLIND to user units (#82, 2026-07-21 — nomadnet crashlooped `NRestarts=7842` for 10 days, undetected). Check user units with `systemctl --user`, never plain `systemctl`; `probe_nomadnet_crashloop` closes that one gap, not the class |
| a boot-time unit dies on a network op while its OWN `ExecStartPre` guard reported `status=0/SUCCESS` | the guard gates on a NAME but the failure moved to ROUTABILITY (2026-09-17) — a 07-21 `getent hosts` bounded wait was silently DISARMED by the 07-25 `/etc/hosts` fleet block: `nsswitch` is `hosts: files ... dns`, so `getent` now succeeds **from a flat file with the NIC down**. It passed in ZERO seconds while ssh died the same second on `Network is unreachable` (was `Could not resolve hostname` in 07-21). **Tell**: a boot guard gating on a NAME is inert on any box carrying a static hosts block — gate on a TCP connect to the real peer instead. ⚠️ TWO CORRECT fixes disarmed each other, so neither commit looks wrong in isolation; ask at write time *what would still pass this check if the feature were dead?* |
| `@rns/<instance>` owned by a **non-rnsd pid** after a reboot | **#69 boot race — root-caused + cured 2026-09-19.** The readiness wait fired correctly and still lost, because it returned on the socket being **PRESENT, owned by anyone**: `check_rns_listener_owner` runs BEFORE the wait — at the one moment the socket is absent and the check is trivially satisfied — and nothing re-asked after. moc5: rnsd started 2.1s AFTER echo, **died on startup** (`status=255`, its only such exit ever, no output logged) and was dead 08:17:51.98→08:17:57.15; both lab daemons resumed at 08:17:56 INSIDE that window and **satisfied each other's wait** — whichever RNS host-fallback put on the socket made the other log `listener appeared — joining as client`. 42 min, no RNS. Cure: the wait polls `_listener_owner_acceptable()` (present AND owner passes), so a transient squatter no longer ends it. ⚠️ **Check ownership AFTER the wait, not only before — a guard that verifies at the one moment the answer cannot be wrong verifies nothing.** ⚠️ **Read user-unit logs with `sudo journalctl _SYSTEMD_USER_UNIT=<unit>`** — plain `journalctl --user` returns "No journal files were found" and reads as data loss; that wrong method cost the first (wrong) root cause. **Check**: `sudo ss -xnpl \| grep "@rns/"` — owner MUST be `rnsd`. ⚠️ **Repair ORDER**: stop squatter → restart rnsd → start squatter. ⚠️ OPEN: why rnsd exited 255. (The `open_reticulum` directive-less mis-aim listed here is CURED 2026-09-20 — see the row below and `_guard_instance_name`.) |
| `ReticulumPaths` names a configdir rnsd NEVER READ — and the box agrees only by coincidence | **rnsd was started with no `--config`** (2026-09-20, meshanchor-server): its unit is `/usr/local/bin/rnsd --service`, so rnsd resolves RNS's OWN default location while `get_config_dir()` reports `/etc/reticulum`. Both land on `@rns/default` TODAY only because NEITHER file declares `instance_name` — declare one in `/etc/reticulum/config` there and every consumer of `get_configured_instance_name()` (generated client configs, the #69 preflight aim, `_rnsd_serves_instance`) computes a name rnsd does not serve, while rnsd keeps hosting `default`. **Quick check**: `systemctl show rnsd -p ExecStart --value` must carry `--config <dir>` AND that dir must equal `ReticulumPaths.get_config_dir()`; then `sudo ss -xnpl \| grep "@rns/"` must show the name that configdir declares. 9 of 10 boxes pass `--config /etc/reticulum`; meshanchor-server is the lone exception and ALSO carries a second, inactive USER-level `rnsd` unit. ⚠️ Do NOT "fix" it by adding `instance_name` to `/etc/reticulum/config` — that is the edit that ARMS the divergence; fix the UNIT. ⚠️ Found while auditing my own fix's premise, not by a probe: no instrument compares rnsd's actual configdir against the one the code assumes |
| CI red on a commit that CANNOT have caused it (docs/comments only), or one interpreter leg failing alone | **re-run the LAST GREEN commit unchanged before believing it was you** (2026-09-20): `cfad33b2` green 21:46 → RED 04:55 on the 3.9 leg, byte-identical code, because the jobs installed BARE package names and pip resolved whatever was newest that minute. ⚠️ **the tell it is not one test's bug is that the VICTIM MOVES** — a constant failing CLASS with a changing member is a shared resource being stolen. Here `patch("utils.cascade_fingerprints.subprocess.run")` READS scoped and is not (`cfp.subprocess is subprocess` → True; a bare `import subprocess` patches the SHARED module object), and the probe holds it open across a 0.4s `ev.wait()`, so any other thread's `subprocess.run` ate a sample → `StopIteration`. ⚠️ **capture the baseline BEFORE re-running anything — a rerun REPLACES the prior attempt's log**; I destroyed the green baseline I was trying to diff against. Cured `d4cffeee` (thread-owned sampler) + `3fb72979` (`requirements/ci.txt`, one constant for three jobs, dry-run on MIN_PY). Transitive deps still float |
| fleet-wide name lookups ~75-90ms while an A-only query is ~1ms | the AAAA leg forwarding to the WAN (2026-07-25) — fleet names had NO AAAA and the upstream returns NODATA with no SOA, so resolved can never negative-cache it; resolution was coupled to internet reachability. Cured by the `/etc/hosts` block (`gen_fleet_hosts.py --apply`, hourly `fleet_hosts_drift` self-heal). ⚠️ cloud-init owns that file too and re-wipes the block on EVERY boot unless `manage_etc_hosts: localhost` is in **`/boot/firmware/user-data`** — a `cloud.cfg.d/` drop-in does NOT work. ⚠️ seed from live DNS, never the registry snapshot: this file SHADOWS DNS. Router-side DNS cannot supersede it (measured 07-26, don't re-open). Body in the archive |

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


## meshtasticd VSZ leak (firmware#10468) — pthread stacks stranded, USB-radio boxes only (2026-07-10)

Symptom: hundreds of GB of **virtual memory** (VSZ) with normal RSS — tens of
thousands of paired 8MB+64KB **anonymous mappings** in `/proc/<pid>/maps`.
Portduino meshtasticd on a **USB (CH341) radio** leaks one joinable 8 MB
pthread **thread stack** per interrupt cycle (~9/min): the CH341 poll thread
runs the RadioLib ISR on ITSELF, so `pinedio_deattach_interrupt`'s self-join
guard SKIPS the join and the stack strands (`pine64/libch341-spi-userspace`;
strace/gdb-pinned 07-10). Live: ~561 GB VSZ / 71k anon maps @ day 5 (Pi5+USB);
SPI-radio boxes clean. **NO published build fixes #10468** — not 2.7.24,
2.7.26, or 2.8. **Re-verified 2026-09-11 AT SOURCE** (not by version
string): `v2.8.0.47db0e3`'s `variants/native/portduino.ini` still pins
`meshtastic/libch341-spi-userspace@03bf505d`, and that ref's
`libpinedio-usb.c` has `pthread_detach` count **0** vs **1** in pine64
`b0694ec8` (what our patched builds carry) — so upgrading the 4 USB boxes
to 2.8 would REGRESS them. Our PR#2 still OPEN, untouched since 07-27.
⚠️ **Do not roll meshtasticd**: 2.7.26.54e0d8d is STILL upstream `Latest`
(2026-06-24); the only newer build is a 2.8.0 *alpha* whose predecessor
`2.8.0.7239fe8` was **revoked** 08-30. meshtasticd is `apt-mark hold`ed on
all 9 boxes (verified 09-11) — 5 of them have the OBS alpha repo enabled at
priority 500, so the hold is the only thing standing between a routine
`apt upgrade` and an alpha fleet-wide. Do not remove it to "unblock" a roll.

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

