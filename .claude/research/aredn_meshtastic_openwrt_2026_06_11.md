# AREDN × Meshtastic × OpenWrt — domain-differentiation research (2026-06-11)

> **Question**: can we run Meshtastic (meshtasticd) + a MeshForge presence on an OpenWrt
> router integrated with the AREDN site, 100% wired and tested?
> **Method**: 6-lane research workflow + 4 adversarial verifies + completeness pass.
> Workflow `wf_6ac593fd-069` died in the 06-11 08:37 VolcanoAI crash; continuation
> `wf_33352d41-d50` died in the 08:57 crash (both kernel-lockup→watchdog resets, see
> persistent-issues). All results recovered from the on-disk workflow journals;
> the last verify + critic pass completed sequentially post-recovery. Raw harvest:
> `~/.claude/plans/aredn-research-recovered-2026-06-11.json` (VolcanoAI).
> **Ground truth**: site router = MikroTik hAP ac lite (QCA9533 mips_24kc, AREDN
> 4.26.1.0 = OpenWrt 24.10.5, 57 MB RAM / ~10 MB free, 16 MB flash / 8.6 MB overlay
> free) in production as the Volcano-QTH gateway (port-forwards to moc5; federation
> rides it — reflash = fleet outage risk). Behind it: moc5 (Pi, Ubuntu 24.04) running
> meshtasticd 2.7.24 with MeshToad (CH341 USB-SPI, sx1262), RSS ~324 MB, already a
> fleet collector on the AREDN-side subnet.

## Verdict in one paragraph

**meshtasticd cannot run on the hAP ac lite or on any AREDN-firmware node — three
independent walls, each fatal.** The AREDN-native answer is **Raven** (the AREDN core
team's official MeshChat replacement): a ~168 KB ucode package that runs ON the AREDN
node and bridges to Meshtastic over **UDP-multicast-over-LAN to an external
meshtasticd device — exactly what moc5 already is**. Our site is the wiki's reference
topology minus two config lines. True meshtasticd-on-router requires vanilla OpenWrt
on ≥128 MB (realistically ≥256 MB) hardware — OpenWrt One or GL-MT3000 class — as a
new box, not a conversion of the production hAP.

## Findings

### 1. meshtasticd on AREDN: dead end (verify:3 CONFIRMED, hash-level proof)

- **kmod hard wall**: AREDN 4.26.1.0 is built from OpenWrt v24.10.5 source but with 53
  patches → its kernel-dep hash (`6.6.119~e48a1121…`) differs from official 24.10.5
  ath79 (`6.6.119~35ef4dd3…`). meshtasticd's hard dep `kmod-spi-dev` is absent from
  AREDN's 96 self-built kmods and the official build can never resolve. Force-install
  fails at insmod (field report: arednmesh.org kmod-mtd-rw thread).
- **RAM wall**: hAP ac lite has ~10 MB free; daemon class needs tens of MB minimum.
- **Field wall**: zero documented meshtasticd success on ANY mips_24kc/ath79 router,
  AREDN or vanilla (exhaustive multi-source search, re-checked 2026-06-11).
- Userspace .ipks from official 24.10.5 feeds DO install on AREDN (ABI-identical,
  admin-UI sideload, AREDN-dev-endorsed) — the wall is kmods only.
- Shelf-life note: AREDN main has moved to OpenWrt 25.12.4 / **apk** — any opkg
  recipe dies at the next AREDN production release.

### 2. Raven: the AREDN-native bridge (verify:2 sharpened — it is NOT meshtasticd-on-node)

What Raven actually is (refuting the "runs meshtasticd on the node" framing):
- ~168 KB, `Depends: ucode, curl` (both in AREDN base), interpreted ucode by AREDN
  lead dev Tim Wilkinson KN6PLV. Alpha, active (last commit 2026-06-08), official
  MeshChat replacement, `.ipk` targets **exactly AREDN 4.26.1.0** (our version).
- Its Meshtastic leg natively re-implements Meshtastic protobufs + AES/curve25519 in
  ucode and speaks **Meshtastic UDP-over-LAN** (multicast 224.0.0.69:4403, firmware
  2.6+ transport) to an **external LAN Meshtastic device**. The wiki's only
  tested/verified rig: meshtasticd on a Raspberry Pi — *the moc5 shape exactly*.
- Setup at our site = `meshtastic --set network.enabled_protocols 1` (+ static IP) on
  moc5, upload `raven_alpha.ipk` via hAP admin UI, `"meshtastic": {}` in
  `raven.conf.override`, `"meshtastic": true` on the channel (channel matched by
  name+PSK `namekey`).
- **No hard RAM floor published**: messages persist to flash, images RAM-ephemeral
  self-capped at 10% of free RAM. "Any AREDN node" per the announcement — but RSS on
  a 64 MB node is THE go/no-go unknown (empirical: install + `top` on the hAP).
- Part 97 posture **by design**: bridged traffic crosses AREDN unencrypted, tagged
  with the bridge callsign.
- Risk profile for our production hAP: alpha code; **daily file-hash auto-update**
  (`opkg --force-overwrite` from GitHub raw — wire formats can change under any
  observer we build); postinst restarts firewall + uhttpd; community flash-wear
  objection (nc8q). A `platforms/debian` port exists in-repo (undocumented) — Raven
  **on moc5 itself** may sidestep the hAP entirely and is the zero-router-risk pilot.

### 3. meshtasticd on vanilla OpenWrt: real, officially supported, needs new hardware

- Official feed (openwrt.meshtastic.org) serves meshtasticd 2.7.15-r1 for 36 arches
  incl. mips_24kc across 5 channels (verify:1: artifacts byte-verified). Stated min:
  **">2 MB free flash" + USB-or-SPI — docs are silent on RAM** (grep: zero hits).
- Officially tested: OpenWrt One, RPi 3/4/5. Community-proven: BPI-R4 (GPIO SPI),
  IPQ4018 router via **USB CH341 — the MeshToad's exact mechanism** (supported since
  2.5.18; MeshToad V3 auto-configures ≥2.6.5; caveat 900 mA TX peak > USB 2.0 budget
  → cap TX power or external power on router ports).
- RAM floor (verify:4, completed post-crash): **no OpenWrt field RSS measurement
  exists anywhere**. Anchor: Femtofox runs meshtasticd production nodes on **64 MB
  total** (RV1103, trimmed Ubuntu) → true working set is tens of MB; moc5's 324 MB is
  platform overhead (glibc arenas, 64-bit, web/SSL). Guidance (medium confidence):
  128 MB workable daemon-only, 256 MB comfortable, 512 MB+ a non-issue.
- Hardware short-list: **OpenWrt One** ($89, 1 GB, only router both officially tested
  AND with a native SPI header), **GL-MT3000** (~$90, 512 MB, USB3, U-Boot recovery),
  **GL-A1300** (~$65, 256 MB, same IPQ4018 family as the proven USB-CH341 report).
  hAP ac2/ac3 work under *vanilla* OpenWrt only (RouterOS bootloader caveats) — and
  flashing one forfeits its AREDN role; not applicable to the production hAP.

### 4. MeshForge graft points (codebase lane)

Already present: `AREDNClient` (`utils/aredn.py` — sysinfo/hosts/services/lqm),
`ARENDataCollectorMixin` (`_map_collector_aredn.py`, `aredn_local`/`aredn_worldmap`
source origins), `AREDNHandler` (TUI), moc5 federating ~2.7k AREDN-side nodes.
Templates for the next organ: gateway `BaseMessageHandler` + `Protocol` enum
(canonical_message), closed `SIGNAL_CLASSES` probe enum, `fleet_roles.yaml` +
`provision_role.py`, `PublicDataFallbackMixin` HTTP-polling pattern.

## Completeness pass (gaps an implementation plan must address)

1. **Raven-on-Debian pilot** (zero-risk variant) is undocumented upstream — needs a
   bench proof before relying on it.
2. **UDP-over-LAN on meshtasticd 2.7.24** specifically: unverified version compat;
   and although multicast UDP is a separate socket from the PhoneAPI TCP stream, run
   a live #17-contention check on moc5 before enabling in production.
3. **Wire-format instability**: Raven's daily auto-update is file-hash-driven with no
   version gate — any MeshForge observer of Raven topics (`KN6PLV.raven.v1` services
   pub/sub, WebSocket :4404) must tolerate breakage; pin-by-copy is not possible on
   the node (auto-update reinstalls).
4. **Part 97**: Raven's cleartext+callsign design is the compliant posture. Any
   OTHER MeshForge leg over AREDN RF inherits the question — notably **RNS-over-AREDN
   TCPInterface (site-to-site) would put encrypted tunnels over Part 97 links**;
   default answer is no (or document a Part-15-only path) before architecting it.
5. **MQTT-over-AREDN federation** (today's brokers are per-box islands) — viable
   unencrypted; same Part 97 framing needed; not required for Raven.
6. **Single-USB-port routers + MeshToad's 900 mA TX peak**: powered hub or TX cap is
   part of any router-node BOM.
7. **Production-hAP rollback story**: opkg remove + firewall restore is untested for
   Raven; lab-first on a spare AREDN node or the Debian pilot. The hAP carries fleet
   federation — treat like a production gateway change.
8. **Power/PoE for any new remote site node**: deferred (current site is the QTH).
9. **Testing gates per phase** are empirical and cheap: Raven RSS via `top` on-node;
   UDP-over-LAN echo test moc5↔bench client; MeshToad-on-OpenWrt would be the
   first documented CH341 MeshToad router deployment (publishable).

## Recommended path (decision menu for the operator)

- **Phase 0 — zero hardware, zero router risk (do regardless)**: deepen the MeshForge
  AREDN organ (sysinfo poll cadence, service advertisement, `aredn_*` watchdog probe,
  NOC panel). Pure repo work on existing patterns.
- **Phase 1 — Raven pilot, still zero router risk**: enable UDP-over-LAN on moc5 +
  bench-test Raven `platforms/debian` on moc5 (or a spare AREDN node if one exists).
  Gates: RSS, stability, bridge round-trip vs the MeshToad RF net.
- **Phase 2 — operator go/no-go**: Raven on the production hAP (the reference
  deployment shape; serves every Raven user on the mesh through moc5's radio).
  Requires accepting alpha + auto-update churn on the federation gateway.
- **Phase 3 — hardware decision**: OpenWrt One (SPI-native, official) or GL-MT3000
  (USB) as a NEW vanilla-OpenWrt meshtasticd router-node, MeshToad via CH341; joins
  the fleet as its own box. hAP stays AREDN, untouched.

Operator decisions outstanding (from the plan): hardware buy vs zero-hardware first ·
radio for a router node (2nd MeshToad?) · managed-node vs on-router MeshForge agent ·
risk posture on the production hAP.

## Key sources

- Raven: github.com/kn6plv/Raven (+ wiki: Installation, Meshtastic, Bridges,
  Memory-Use, Supernodes), arednmesh.org "meshchat-replacement" (W6BI announcement)
- Feed: openwrt.meshtastic.org (Packages indexes byte-verified),
  meshtastic.org/docs/hardware/devices/openwrt/, github.com/meshtastic/openwrt
- AREDN: aredn/aredn `openwrt.mk` @4.26.1.0 (`OPENWRT_COMMIT=v24.10.5`),
  downloads.arednmesh.org feed indexes, arednmesh.org kmod-mtd-rw + KI5HZZ threads,
  docs.arednmesh.org node_admin
- Hardware: forum.openwrt.org "Meshtastic running on BananaPi BPI-R4 and OpenWrt One"
  (markbirss), github.com/femtofox/femtofox, openwrt.org ToH (One / GL-MT3000 /
  GL-A1300 / hAP ac2/ac3), linuxgizmos.com MeshToad V3
