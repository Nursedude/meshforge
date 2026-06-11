# AREDN Raven pilot on moc5 — Phase 1 execution log (opened 2026-06-11)

> **Goal**: run a Meshtastic↔AREDN bridge (Raven) + MeshForge presence integrated
> with the AREDN site, 100% wired and tested. Multi-session arc.
> **Research SSOT**: `.claude/research/aredn_meshtastic_openwrt_2026_06_11.md`
> (read first — meshtasticd-on-AREDN is a dead end; Raven on the external
> meshtasticd box is the path; moc5 is the wiki's reference rig).
> **Decision menu**: `.claude/plans/fleet_research_lane_distribution.md` is a
> SEPARATE arc; this is the AREDN-bridge arc.
>
> ⚠️ **Process constraint**: the 06-11 VolcanoAI kernel lockups were triggered by
> the multi-agent research workflow that produced the SSOT. Do NOT run `/deep-research`
> fan-outs on VolcanoAI until root-caused — sequential WebFetch + hands-on moc5
> wiring is the safe mode (this session's method). See
> [[project_volcanoai_hard_reset_2026_05_28]].

---

## ✅ PHASE 1 — CLEARED 2026-06-11 (feasibility + RX packet bridge PROVEN)

**Raven runs on moc5 via its undocumented `platforms/debian` port, AND a real
packet bridged + decrypted end to end** (see the functional-verification section
below). The entire dependency chain is built and working on Ubuntu 24.04 aarch64;
Raven initializes fully, generates + persists its node identity, runs its event
loop, and decodes encrypted Meshtastic traffic off the UDP-LAN multicast. The
zero-router-risk pilot is real and the bridge decode direction is verified.

### What was proven (live on moc5)
- meshtasticd 2.7.24 on moc5 **already** owns the UDP-over-LAN multicast socket
  `224.0.0.69:4403` (pid 1009) → `network.enabled_protocols` UDP bit already set;
  Phase-1's "enable UDP-over-LAN" step was already done.
- **#17 contention is a non-issue**: Raven's Meshtastic leg (`meshtastic.uc`,
  `TRANSPORT_MECHANISM_MULTICAST_UDP=6`, `ADDRESS=224.0.0.69 PORT=4403`) joins the
  **multicast group**, NOT the PhoneAPI TCP `:4403`. No persistent TCPInterface,
  no PhoneAPI drain. The PhoneAPI had zero ESTAB connections at baseline (no #75
  leak either).
- **Raven crypto is pure ucode** (`crypto/aes.uc`, `curve25519.uc`, `sha*.uc`) —
  no external crypto lib. Only the **node keypair** shells out to `/usr/bin/usign`.
- Raven module resolution works via `config.uc:setup()` pushing
  `${script_dir}/*.uc` to `REQUIRE_SEARCH_PATH` (the `*` expands dotted names, so
  it resolves `router` AND `platforms.debian.platform`). Config read from
  `${script_dir}/raven.conf`. Platform auto-selected by the `platform_debian` key.
- The Debian `platform.uc` is a **minimal stub**: file-store + curl fetch + auth;
  AREDN mesh-routing/targeting (`getTargetsByIdAndNamekey`, supernode fabric,
  web UI via uhttpd) is stubbed. Enough for a single-node Meshtastic bridge;
  inter-Raven-node relay does NOT work on the Debian port (no targets).
- Run result: `Starting up → Configuring → Configured → Tick → Tick → (SIGTERM)
  Shutting down → Shutdown`. Clean lifecycle. `~/raven-store/node.json` (392 B
  identity) + `nodedb.json` persisted. Event-loop tick blocks on a socket poll
  (~7 s), so "Tick" is sparse — correct, not a hang.

### moc5 artifact inventory (what's installed, where)
| Artifact | Location | Source |
|---|---|---|
| ucode interpreter | `/usr/local/bin/ucode` + `libucode.so` (ldconfig'd) | built from `~/ucode-src` (github jow-/ucode) |
| ucode modules | `/usr/local/lib/ucode/{socket,fs,struct,math,io,log,resolv,debug}.so` | same build |
| usign | `/usr/bin/usign` (220 KB, standalone, no libubox) | built from `~/usign-src` (github openwrt/usign) |
| Raven | `~/Raven/` (clone) | github kn6plv/Raven |
| Raven config | `~/Raven/raven.conf` (debug 1, role client_mute, observe-only) | hand-written |
| Raven node identity | `~/raven-store/{node,nodedb}.json` | generated |
| apt dep added | `libjson-c-dev` | apt (ucode build dep) |

**moc5 production role UNDISTURBED**: meshtasticd / meshforge-map (healthz 200) /
rnsd all active throughout; federation/collector role intact. Everything above is
additive; nothing daemonized yet.

### Reproducible build recipe (for the hAP port later, or a rebuild)
```bash
# ucode (needs cmake gcc make + libjson-c-dev)
sudo apt-get install -y libjson-c-dev
git clone --depth 1 https://github.com/jow-/ucode.git ucode-src
cmake -S ucode-src -B ucode-src/build -DCMAKE_BUILD_TYPE=Release \
  -DUBUS_SUPPORT=OFF -DUCI_SUPPORT=OFF -DULOOP_SUPPORT=OFF \
  -DNL80211_SUPPORT=OFF -DRTNL_SUPPORT=OFF -DZLIB_SUPPORT=OFF -DDIGEST_SUPPORT=OFF
make -C ucode-src/build && sudo make -C ucode-src/build install && sudo ldconfig
# usign (standalone, no libubox)
git clone --depth 1 https://github.com/openwrt/usign.git usign-src
cmake -S usign-src -B usign-src/build -DCMAKE_BUILD_TYPE=Release
make -C usign-src/build && sudo install -m755 usign-src/build/usign /usr/bin/usign
# Raven
git clone --depth 1 https://github.com/kn6plv/Raven.git ~/Raven
# write ~/Raven/raven.conf (platform_debian + meshtastic{} + channels), then:
cd ~/Raven && ucode raven.uc
```

---

## ✅ PHASE 1 FUNCTIONAL VERIFICATION — PASSED 2026-06-11 (RX direction)

**A packet bridged end to end, decrypted with the fleet PSK.** Configured
`~/Raven/raven.conf` (chmod 600) with two channels marked `"meshtastic": true`:
LongFast default + the fleet **"meshforge"** channel (moc5 slot index 2; namekey =
`meshforge <b64psk>` — the live PSK lives ONLY in the on-box config, never in repo
or memory). Ran Raven observe-only (`role client_mute`), sent a tagged text
`RAVENTEST7` on the meshforge channel via `meshtastic --host localhost --sendtext
... --ch-index 2`. Raven received it over the UDP-LAN multicast and **decrypted it
with the configured PSK**:
```json
{ "from": ..., "channel": 162, "transport": "meshtastic",
  "data": { "portnum": 1, "text_message": "RAVENTEST7" } }
```
`channel: 162` = the meshforge channel hash; the plaintext came out only because
the PSK matched. **Observe-only confirmed**: exactly ONE `transport:"meshtastic"`
frame in the log (the received one) — Raven did NOT TX onto RF; its own nodeinfo
went out on the `native` meship transport (UDP 4404), not Meshtastic. moc5
services healthy throughout (map healthz 200). Process stopped cleanly; the
`/tmp` debug log (held the PSK in namekey lines) was scrubbed.

Proven path: **meshtasticd (RF/local TX on encrypted ch2) → UDP multicast
224.0.0.69:4403 → Raven decode with the PSK.** The decode direction of the bridge
is real.

### ▶ NEXT (remaining Phase 1 / Phase 2 prep)
1. **Reverse direction (Raven→Meshtastic TX)**: needs a transmitting `role` (not
   client_mute) so Raven injects onto the Meshtastic channel. Deliberately NOT done
   — TXing onto the fleet RF channel is an operator-go decision (and on the Debian
   stub, AREDN-side relay is stubbed anyway). Decide scope first.
2. **RSS/stability soak**: `ps -o rss` on the ucode process over hours; confirm
   footprint + no leak before daemonizing.
3. **Daemonize** (if moc5 is to host the bridge persistently): adapt
   `platforms/debian/raven.service` (execs `/root/raven/raven.uc`) to moc5's layout
   (user unit, or `/root/raven`); linger already on. Until then, run bounded by hand.
4. **DHCP→reservation** for moc5 eth0 before anything depends on its address.

## Phase 0 (parallel, zero-risk repo work) — MeshForge AREDN organ
Deepen the existing AREDN footprint (`utils/aredn.py` `AREDNClient`,
`_map_collector_aredn.py`, `AREDNHandler`): sysinfo poll cadence, `aredn_*`
watchdog probe, NOC panel, advertise the Raven bridge as an AREDN service.
Graft points in research doc §4. Can proceed independent of the Raven pilot.

## Phase 2 — Raven on the production hAP (operator go/no-go)
The wiki reference shape: `raven_alpha.ipk` (in the clone) on
WH6GXZ-6-VOLCANO-QTH-HAP, bridging to moc5's meshtasticd. Accept: alpha code,
daily file-hash auto-update, postinst restarts firewall+uhttpd ON the federation
gateway (port-forwards ride it — treat like a production gateway change). The
Debian pilot de-risks this. Rollback (opkg remove + firewall restore) untested.

## Phase 3 — dedicated meshtasticd-on-OpenWrt node (hardware)
OpenWrt One ($89, SPI-native, officially tested) or GL-MT3000 (512 MB, USB) as a
NEW vanilla-OpenWrt node, MeshToad via CH341 (cap TX / external power — 900 mA
peak). hAP stays AREDN, untouched.

---

## Operator decisions still open (surface, don't block Phase 1)
1. **Hardware buy** (Phase 3): OpenWrt One / GL-MT3000 / GL-A1300, or zero-hardware
   phases only for now?
2. **Radio for a router node**: second MeshToad, or move the existing one?
3. **"MeshForge on router" scope**: managed-node (NOC polls it) vs on-router agent?
4. **Production-hAP risk posture** (Phase 2): is alpha Raven acceptable on the live
   federation gateway, or Debian-pilot-on-moc5 only until it stabilizes?

This session's implicit answer (operator pointed at moc5): **Debian pilot first**,
zero router risk — which is exactly what Phase 1 executes.

---

## Housekeeping noted (not blocking)
- Stray `/etc/meshtasticd/path/to/venv./` pip-junk dir on moc5 — cruft to remove.
- moc5 eth0 is **DHCP** (`10.143.126.75/28 dynamic`); a stable bridge wants a DHCP
  reservation or static — set before Phase 2 relies on a fixed address.
