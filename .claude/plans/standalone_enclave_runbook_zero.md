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
2. **Restore path — DRILLED end-to-end 2026-08-10 (T1). The order below is
   the corrected procedure; the original one-shot `restore.sh` FAILED and is
   retired (see §T1).** Each step is `sysupgrade`/`opkg`-native, not raw tar:
   1. [V] Flash/boot stock OpenWrt for the One (same version as the backup —
      24.10.x).
   2. [V] **Config: `sysupgrade -r <sysupgrade-backup>.tar.gz`** — the
      SUPPORTED restore. It commits to the overlay and reboots. Restores
      uci (hostname, network, wireless — verified byte-identical to
      snapshot bar the new package's own section), `/etc/dropbear` host
      keys, `/etc/shadow`, AND every path registered in the backup's
      `/etc/sysupgrade.conf`. Proof: after reboot `uci get
      system.@system[0].hostname` = `OpenWrt_Meshforge` (NOT the `/proc`
      kernel hostname, which reads stale until a reload — check uci).
   3. [V] **Packages — NOT in a config backup.** `opkg update` then
      `opkg install meshtasticd` (resolves to `meshtasticd-full`) plus any
      others from `opkg-installed.txt`. The custom feed
      (`openwrt.meshtastic.org/...`) + its signing keys must be present in
      `/etc/opkg/` first — they ride in the sysupgrade backup only if
      registered (they are now). Without this step the daemon binary and
      its init script are simply absent.
   4. [V] **Custom files** (`/etc/init.d/rtun`, `/root/rtun_watchdog.sh`,
      `/root/failover_drill.sh`, `/root/.ssh/id_dropbear`, `/etc/meshtasticd`)
      apply from `extras.tar.gz`; `chmod +x` the scripts, `chmod 600` the
      key. **Then register them in `/etc/sysupgrade.conf`** so the NEXT
      backup carries them (done on the live box 2026-08-10).
   5. [V] Enable + start: `/etc/init.d/rtun enable && start` (tunnel dials
      home), `/etc/init.d/meshtasticd enable && start`.
   The wifi PSK IS in the uci snapshot (no hand entry). The tunnel's dropbear
   PRIVATE key rode in extras this time; on a fresh build regenerate
   (`dropbearkey -t ed25519 -f /root/.ssh/id_dropbear`) and authorize the new
   pubkey on the manager box — key rotation on restore is fine.
   ⚠️ Getting the bundle onto a reset box: it has no inbound path until rtun
   is back (chicken-and-egg). Serve from the manager over HTTP and
   `uclient-fetch` from the box over its WAN (the same route it dials the
   tunnel on) — `scp` fails (stock dropbear has no sftp-server).
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

## T1 status (2026-08-10) — DRILLED END-TO-END, H3 = PARTIAL PASS

**Full destructive drill run**: operator on the CONFIG port, `firstboot`
factory reset, then restore. Verdict: **the enclave restores to a
config-faithful, mesh-connected box — but ONLY via the corrected procedure
in §2.2, and with two residuals.** The runbook's restore path was wrong;
it is now evidence-based.

Remote-safe pre-legs (before the reset): **LEG1** live-vs-snapshot diff =
zero. **LEG2** 208 packages present in feed catalogs — via `opkg list` (the
catalog), NOT `opkg info` (which self-confirms against installed). **LEG3**
`uci import` round-trips byte-identical (md5; BusyBox has no `diff` — a drill
asserting on `diff` false-FAILs). **LEG4** feed config + signing keys were
MISSING from the original snapshot — captured.

Destructive drill findings (the real value — each cost real time tonight):
1. **The one-shot `restore.sh` (raw `tar -C /` + `reboot`) did NOT persist —
   box booted factory-default** (hostname `OpenWrt`, no root pw). Raw-tar is
   not OpenWrt's restore mechanism. RETIRED. Use `sysupgrade -r`.
2. **`sysupgrade -r` restores CONFIG only, not PACKAGES.** After it, uci was
   perfect but `/etc/init.d/meshtasticd` and the binary were absent —
   `opkg install meshtasticd-full` was a separate required step.
3. **Two self-inflicted mis-reads, both caught by tighter checking, both
   logged against [[feedback_verify_the_verification]]**: (a) "routing works
   + kiai:9443 → box restored" — false; factory OpenWrt NATs by default, so
   routing proved nothing. (b) `pgrep -f meshtasticd` read RUNNING off its
   own command line; `ps w | grep "[m]eshtasticd"` showed the daemon was
   DOWN. Trust the thing, not the proxy.
4. **Access chicken-and-egg**: a reset box has no inbound path until rtun
   redials; the familiar `ssh -p 2222` tunnel is the very thing being
   restored. Bundle delivery went manager-HTTP → box `uclient-fetch`.

5. **Two required packages, not one** (finding #2, sharpened): the box needs
   BOTH `meshtasticd-full` AND `meshtasticd-web` (the latter provides
   `/usr/share/meshtasticd/web`, the webserver's `RootPath` content — without
   it 9443 has nothing to serve). Install the exact set from
   `opkg-installed.txt` (which lists both), not just the daemon.
6. **A third self-inflicted mis-read** (again [[feedback_verify_the_verification]]):
   I reported "API 4403 / web 9443 don't bind" as a residual — FALSE. BusyBox
   `ss` did not show the listeners and bash `/dev/tcp` isn't supported in ash,
   so BOTH my probes lied. `netstat -tlnp` showed `0.0.0.0:4403` and
   `0.0.0.0:9443` LISTEN, and `curl` through the tunnel returned HTTP 200.
   On OpenWrt verify listeners with `netstat -p`, never busybox `ss` or
   `/dev/tcp`.

VERIFIED working post-restore (all with tools that don't self-confirm): uci
config identical to snapshot (only the new package's own section added);
`uci` hostname = OpenWrt_Meshforge; rtun tunnel UP (forwards 2222/ssh,
14403/radio, 19443/web); wire-primary + wifi metric-100 failover routes both
up; meshtasticd RXing LIVE mesh traffic on the E22 radio; **API 4403 + web
9443 LISTENING (netstat), web serves 1132 B locally and HTTP 200 via the
tunnel from the manager**; meshtasticd boot-enabled.

**Reboot-survival DRILLED 2026-08-10 — PASS.** Rebooted the restored box;
it came back UNAIDED in ~90 s with the FULL stack: rtun redialed on its own,
both default routes (wire + wifi metric-100), meshtasticd running + RXing
live mesh (nodeinfo from a real node at boot+48 s), API 4403 + web 9443
LISTENING (netstat), web HTTP 200 via the tunnel from the manager. Nothing
manual. This closes the last H3 leg.

Minor notes (non-blocking):
- meshtasticd logs non-fatal `Unknown module config type 14/15/16` — version
  skew between meshtasticd 2.7.26 and the restored `config.proto` module set;
  daemon + radio + API + web all fine.
- `/etc/init.d/meshtasticd restart` needs ~20 s settle (USB E22
  re-enumeration); a too-short wait reads DOWN mid-restart. (Boot start is
  fine — the drill proved it.)
- Kernel hostname reads stale `OpenWrt` until a reload/reboot (uci correct;
  cosmetic — and the reboot cleared it).

**H3 verdict: PASS (complete).** The enclave restores to a fully working
node AND survives a reboot unaided — config, tunnel, failover, radio RX, and
client API/web all verified against non-self-confirming tools, before and
after a power cycle. H3 CLOSED.

## 6. Known gaps at write time (T2 will find more)

- [V] alaula radio = **USB meshtoad E22 (SX1262)** — answered by T1
  (`config.d/lora-usb-meshtoad-e22.yaml`; meshtasticd RXed live over it).
- [U] m1 fresh-path wifi + firewall details are from memory of a default
  config, not a checklist — expect interventions here.
- [U] Whether kiai's meshforge services need any enclave-specific config
  when the fleet is unreachable (probes that expect fleet peers must read
  inert, not indeterminate — honest-failure-modes #2; unverified).
- [B] Restore paths (both boxes) exist only as snapshots — T1 is the proof.
