# Standalone Enclave — Runbook Zero (2026-08-10)

> **This document is deliberately unproven.** It is the deploy procedure AS
> BELIEVED at write time, committed before any trial per
> `standalone_usability_study.md` Phase 0.3. Every step carries a tag:
> **[V]** verified live 2026-08-10 · **[B]** believed, not drilled ·
> **[U]** unknown — a gap the study must fill. Trials T1/T2 revise this file;
> the diff history of this document IS the study's primary artifact.
> Secrets (wifi PSK, channel keys) live in the operator's private
> config-snapshot repo, never here.

## What you are building

A self-contained mesh environment that lives alone — no upstream services
required — and delivers the domain's end: **a message arrives**.

| Box | Hardware | Role |
|-----|----------|------|
| m1 | MikroTik hAP ac3 (RouterOS 7.22.x) | border router: LAN bridge, DHCP, wifi cell, NAT to whatever uplink exists (or none) |
| alaula | OpenWrt One (MT7981, OpenWrt 24.10.x) | radio node: meshtasticd + secondary wifi path |
| kiai | Raspberry Pi + USB CH341 LoRa radio | brain: meshforge, meshtasticd (patched build), web client |

## 0. Bill of materials & pre-flight

- [B] The three boxes above, PSU each; 2× ethernet cables **labeled at both
  ends** (`UPLINK`, `CONFIG` — the 08-10 incident was an unlabeled cable).
- [B] Radio antennas attached BEFORE power (LoRa PA with no antenna = risk).
- [U] alaula's LoRa radio hardware (built-in? USB? SPI?) — inventory not
  recorded anywhere; first T2 run must document it here.
- [B] The private config-snapshot repo, reachable from your laptop, if
  restoring rather than building fresh (T1 path).

## 1. m1 (border) bring-up

1. [B] Power m1 alone first. Factory RouterOS answers on its default LAN
   subnet (MikroTik ships 192.168.88.0/24) with DHCP on the bridge ports
   (ether2–5) — plug your laptop into ether2, you get a lease.
2. [B] **Restore path**: upload the snapshot `export.rsc`, then
   `/import file=export.rsc` from a terminal. ⚠️ RouterOS exports omit
   secrets — re-set user passwords and the wifi security-profile PSK by
   hand afterward. This restore has NEVER been drilled (T1 target).
3. [B] **Fresh path**: keep defaults; set: bridge = ether2–5 + both wlan;
   DHCP pool on bridge; wlan2 (5 GHz) AP `Meshforge 5` + WPA2-PSK; ether1 =
   WAN DHCP client (leave unplugged if the enclave lives alone).
4. [V] Sanity: a wired client gets a lease from the bridge pool; the 5 GHz
   SSID is visible; `/interface ethernet print stats` shows zero FCS errors.
5. [B] If an uplink exists, plug it into **ether1 only** — never a bridge
   port (that would flatten the enclave onto the upstream LAN).

## 2. alaula (radio node) bring-up

1. [V] Port roles — do not improvise these: **eth0 = UPLINK** (to an m1
   bridge port), **eth1 = CONFIG/LAN** (br-lan, its own 192.168.1.0/24,
   serves DHCP to a directly-attached laptop).
2. [B] **Restore path**: flash stock OpenWrt for the One, install packages
   from `opkg-installed.txt`, apply the snapshot's `uci-export.conf`
   (paste into `uci import` or copy per-file into `/etc/config/`), restore
   the crontab and `/etc/init.d/rtun` + `/root/rtun_watchdog.sh`, re-enter
   the wifi PSK. Never drilled (T1 target).
3. Invariants the 08-10 incident bought — check them explicitly:
   - [V] Default route rides **eth0 (wire)**; the wifi STA to `Meshforge 5`
     is the **metric-100 backup**, nothing else.
   - [V] **No open AP.** The OpenWrt default `OpenWrt` SSID (unencrypted)
     must be `disabled='1'`. An enabled open AP is both a security hole
     into the enclave and a call-killing attractor for nearby laptops.
   - [V] **No placeholder STA configs.** A wifi-iface with an unresolvable
     SSID drags the radio off-channel scanning and breaks the real STA's
     DHCP (~90 s flap). Disable or delete, never leave "harmless".
   - [B] meshtasticd listens on TCP 14403 (`meshtastic --host <alaula>:14403`).
4. [V] Failover proof (60 s, scripted): run `/root/failover_drill.sh` —
   eth0 down → egress + tunnel move to wifi backup with 0 % loss → trap
   restores eth0. Passed 2026-08-10 in 72 s. Re-run after ANY network or
   wireless config change.
5. [B] The `rtun` reverse tunnel is **lab integration, not enclave core** —
   in a true standalone deploy there is no manager box to dial; skip it and
   note the skip. (Its presence in the snapshot is the lab shape.)

## 3. kiai (brain) bring-up

1. [B] Standard Pi bring-up, wired into an m1 bridge port; expect a lease
   from m1's pool. ⚠️ kiai ignores ICMP by design — **never judge it by
   ping**; the liveness check is
   `ssh kiai 'git -C /opt/meshforge rev-parse --short HEAD'`.
2. [B] **The stock meshtasticd package leaks on USB (CH341) radios**
   (firmware #10468 — 2.7.x AND 2.8 as shipped): use the patched build
   (`meshtasticd-patched` + systemd drop-in) per `persistent_issues.md`,
   and keep the weekly restart timer (backstop outlives fix).
   ⚠️ `pgrep -x meshtasticd` misses the patched binary; use `pgrep -f`.
3. [V] Web client: `https://<kiai>:9443/` — binds all interfaces, HTTP 200
   locally in ~17 ms when healthy; self-signed cert warning is expected.
   If the page loads but sits deaf while RX is healthy → PhoneAPI
   starvation class (#17/#75), restart the map service, not the browser.
4. [B] meshforge: `/opt/meshforge` at current main; profile per
   `deployment_profiles.md`.

## 4. Radio / channel configuration

- [B] All enclave boxes on the **enclave test channel/preset** — never a
  live statewide channel. TX from any test or drill goes through the
  tx_guard egress gate (2026-08-09 lesson). Channel names/keys: private repo.
- [B] Preset is a property of a LINK: both radios must share it or the
  enclave's own RF check reads a healthy radio as silent (two-preset fleet
  lesson, 2026-07-30).

## 5. Acceptance — "message arrives" (the only test that counts)

1. [B] From kiai's web client (or `meshtastic --host` CLI), send a text on
   the enclave channel.
2. [B] Confirm RX on alaula's meshtasticd (journal `Received text msg`, the
   honest RX record — not the sender's own echo).
3. [B] Reply from alaula's side; confirm arrival back at kiai.
4. [B] With the uplink CABLE PULLED from m1 ether1 (lives-alone posture),
   repeat 1–3. Success = the enclave delivered both ways with no upstream.

Record: wall-clock per section, every action this document did not tell you
to take, every place it lied. Those three lists are the study's data.

## 6. Known gaps at write time (T2 will find more)

- [U] alaula radio hardware inventory (step 0).
- [U] m1 fresh-path wifi + firewall details are from memory of a default
  config, not a checklist — expect interventions here.
- [U] Whether kiai's meshforge services need any enclave-specific config
  when the fleet is unreachable (probes that expect fleet peers must read
  inert, not indeterminate — honest-failure-modes #2; unverified).
- [B] Restore paths (both boxes) exist only as snapshots — T1 is the proof.
