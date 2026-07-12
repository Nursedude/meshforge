# Babel L3 fabric — Arc 3 canary spike (2026-07-12)

> OpenWrt-MeshForge arc, Role 3 (plan:
> `~/.claude/plans/openwrt-meshforge-meshtasticd-use-virtual-music.md`).
> Goal: measured go/no-go evidence for a Phase-2 fleet L3 fabric that can
> later meet AREDN's Babel (AREDN 4.26.1.0 is Babel-only; the "zero-config
> interop" claim was REFUTED 0-3 in the Arc-0 deep-research pass).
> Everything here ran live on 2026-07-12 (HST 2026-07-11 PM); all numbers
> below are measured, not quoted from docs.

## Testbed

- **Router**: OpenWrt One (OpenWrt 24.10.7), behind a NAT'd wifi-STA
  uplink to the fleet LAN. `babeld 1.13.1-r2` + `wireguard-tools` from the
  official 24.10 feeds (`opkg install babeld wireguard-tools
  kmod-wireguard`).
- **Fleet peer**: moc2 (Debian Pi, LAN-direct). `babeld 1.13.1+ds-1`,
  `wireguard-tools` via apt. ⚠️ Debian AUTO-STARTS babeld on install —
  `systemctl disable --now babeld` immediately (stock config is inert but
  the default is hostile to a controlled spike).
- **Peer selection lesson**: the first pick (moc1) FAILED — its ssh alias
  fronts an AREDN-hAP NAT (the box's only LAN address is in the AREDN
  172.27/16 host range), so inbound UDP for wg never arrived. `hostname
  -I` ground truth before choosing a listener; this is the same
  addressing-identity murk the Arc-2 naming audit exists to expose.
- **Link**: wg p2p, router dials out through its NAT
  (`persistent-keepalive 25`), moc2 listens on udp/51821. Plain `wg set`
  (never installs routes); `allowed-ips 0.0.0.0/0,::/0` both sides — the
  standard babel-over-wg recipe (cryptokey passes babel's link-local +
  multicast; containment lives in babeld's filters + kernel routes, not
  in cryptokey routing). Tunnel addrs 203.0.113.129/130 (/30) + manual
  `fe80::1`/`fe80::2` (babel REQUIRES IPv6 link-local; wg has none by
  default). Test prefixes on lo: 203.0.113.1/32 (router), .2/32 (moc2) —
  TEST-NET-3 end to end, zero collision surface.
- **Router firewall**: `nft insert rule inet fw4 input iifname "wg-spike"
  counter accept` — RUNTIME-ONLY (a fw4 reload wipes it; the AREDN
  restart-firewall lesson). Symptom without it: wg handshake + counters
  fine, pings answered with ICMP errors.
- **Containment** (filters in `/etc/babeld-spike.conf`, template in
  `templates/openwrt/babeld-spike.conf.template`): redistribute allows
  ONLY the box's own test /32 then denies (incl. `redistribute local
  deny`); `in`/`install` allow only 203.0.113.0/24 then deny. Verified:
  `ip route show proto babel` never contained anything but the peer's
  /32, both sides, throughout.

## Measurements (all VERIFIED live)

| criterion | target | measured |
|---|---|---|
| initial convergence (daemon start → both /32s learned) | ≤30 s | **T+30 s** (first side T+23 s; 5 s poll granularity) |
| withdrawal (prefix removed → forwarding gone on peer) | ≤16 s (4×hello) | **≤1 s** (route flips to `unreachable` retraction hold, then GCs) |
| re-learn (prefix re-added) | — | **11 s** |
| babeld RSS | ≤10 MB, flat 48 h | **720 KB** (router) / **1.3 MB** (moc2) at start; 48 h soak armed |
| unintended route insertions | zero | **zero** (exactly one babel route each side at all times) |
| E2E forwarding over learned routes | works | moc2→router-lo 3/3 @1.4 ms; router→moc2-lo 2/3 (first-packet warmup). busybox `ping -I <lo-addr>` variant fails on the router — ping quirk, not fabric (replies with that src flow fine) |
| failover with a second path | ≤30 s | **NOT MEASURED** — single-path testbed; Phase-2 item |

## THE finding: babel adjacency over the wifi+NAT leg is NOT stable —
## root cause OPEN after a bounded debug ladder

Routes retract/relearn persistently (initially every ~15–25 s at default
4 s hellos; still recurring at ~1–10 min scale after every tuning step).
The debug ladder, with what each rung RULED OUT (all measured live):

1. **Inner-tunnel loss**: 0 % over 40×0.5 s AND over 360×0.5 s pings —
   not loss. But a **~200 ms sawtooth stall every ~6–7 s** (RTT 225→185→
   143→103 decay pattern — wifi-scan-like; powersave already off on all
   router radios) and max 235 ms bursts.
2. **hello-interval 12 + rtt-max 456 + max-rtt-penalty 48**: reduced flap
   frequency (13 min clean, then recurrence) — insufficient.
3. **Multicast-over-wg** (moc2's babeld showed `rxcost 65535`, sparse
   reach mask 9338, while tcpdump PROVED hellos arriving): switched
   `unicast true` both sides — flap continued → not (only) mcast joins.
4. **NAT mapping expiry** (MikroTik fronts the router; moc2 sees endpoint
   `<mikrotik>:59732`): keepalive 25→5 s — port stayed stable the whole
   watch, flap continued → not NAT rebind.
5. **Sender starvation**: 9 hello packets from the router captured in
   60 s — emission adequate.

Where that leaves it: packets arrive, yet babeld's hello ACCOUNTING sees
misses (the reach-mask holes are the proximate cause; whether from the
~6–7 s stall pattern interacting with timestamped hellos, or a
unicast+multicast dual-seqno-stream accounting subtlety in 1.13.1, is
UNRESOLVED — deliberately stopped here; a spike is bounded). During the
same window the rtun ssh tunnel dropped once too — this uplink genuinely
hiccups.

**The decisive next experiment is hardware, not config**: the OpenWrt
One has free ethernet — an A/B with a WIRED uplink to the MikroTik
removes the wifi medium entirely and splits medium-vs-daemon in one
move. OPERATOR DECISION (a cable run). Until then: babel over THIS
wifi-STA leg is not production-grade; nothing here implicates wired or
AREDN-RF legs.

## Passive AREDN lane: LAN leg is blind

`tcpdump udp port 6696` on moc5 (an AREDN-node LAN host, 10.143/8 host
range): **0 packets in 15 s** — AREDN's babel speaks on the hAP's mesh
interfaces and is not bridged to the LAN leg. Populating the AREDN
parameter table (hello interval, rxcost, redistribute policy) needs an
ON-NODE capture, and the production hAP is a standing no-touch box
(56 MB RAM). → OPERATOR DECISION: a bounded read-only `tcpdump -c` on
the hAP, or wait for the Phase-3 bench AREDN node. Until then the
interop parameter table stays honestly EMPTY.

## Soak + teardown

- Router: `/usr/bin/babeld-soak` cron (hourly :23) →
  `/etc/meshforge/babeld-soak.log` (`ts pid rss_kb babel_routes`,
  self-truncating).
- moc2: `~/babeld_spike_soak.sh` cron (hourly :29), wired to
  `cron_verdict.sh babeld_spike_soak` (#78 watches the watcher); exit 1
  on dead pid / RSS ≥10 MB / no babel route.
- **Teardown (one paste per box)** — router: `kill $(cat
  /var/run/babeld-spike.pid); ip link del wg-spike; ip addr del
  203.0.113.1/32 dev lo; rm /etc/babeld-spike.conf /etc/wireguard/spike.key
  /usr/bin/babeld-soak; crontab -l | grep -v babeld-soak | crontab -`;
  moc2 mirror + `apt-get remove babeld` optional. Nothing here touches
  production routing, AREDN, the MikroTik, or the gateways.

## Wired A/B plan (the decisive experiment — needs one cable)

**Hypothesis**: the flap's driver is the wifi-STA medium (the ~200 ms/6–7 s
sawtooth + burst stalls), not babeld or the NAT. A wired uplink removes
exactly one variable: same MikroTik, same NAT, same wg/babeld configs.

**Operator step (the only hardware touch)**: ethernet cable from the
OpenWrt One's WAN port to a MikroTik LAN switch port on the same 88.x
segment its wifi STA joins today.

**Session steps once the cable is in** (each reversible, rtun self-heals
across the transition — drilled ≤20 s):
1. Baseline snapshot FIRST: 24 h of pre-cable soak samples = the wifi arm
   of the A/B (`babeld-soak.log` + moc2 `babeld_spike_soak.log` FAIL
   counts per day). Don't reset the logs.
2. Router: confirm the wan interface DHCPs on 88.x (`ip -br addr`); then
   prefer it for the wg endpoint path — simplest: `uci set
   wireless.<sta>.disabled=1; wifi` for the A/B window (full isolation
   beats metric games; rtun rides the wired path the same way). Verify
   rtun re-establishes + wg handshake resumes before walking away.
3. Re-run the two profiles that characterized the wifi arm, same
   commands: 3-min 0.5 s-interval inner-tunnel ping (expect the sawtooth
   GONE) and a 5-min route-stability watch.
4. Let the soaks run ≥24 h wired. The comparison statistic is
   **babel_routes=0 samples per day, wifi arm vs wired arm** (plus moc2's
   FAIL-verdict count per day — same signal through #78).
5. Optional second experiment if wired is clean: drop `hello-interval`
   back to 4 on both sides for 1 h — confirms defaults are fine on a
   stable medium and the earlier tuning was compensating for the leg,
   not for babeld.

**Decision matrix**:
- wired stable + wifi flappy → medium confirmed. Phase 2 proceeds for
  wired/RF legs; wifi-STA legs need hardware (wired/powerline) or a
  documented degraded-adjacency profile. Revert or keep the cable per
  operator preference; re-enable the STA either way.
- wired ALSO flaps → medium exonerated; suspects narrow to babeld 1.13.1
  hello accounting or the MikroTik itself → next rung is hello-seqno
  tracing (tcpdump both ends, correlate seqno gaps with the reach mask)
  and/or a babeld 1.14/master build.
- Rollback at any point: re-enable the STA (`uci set
  wireless.<sta>.disabled=0; wifi`), unplug — pre-A/B state exactly.

## Go/no-go read (interim — final after the 48 h soak + wired A/B)

Protocol mechanics: **all inside criteria** (converge 30 s, withdraw
≤1 s, relearn 11 s, RSS ≤1.3 MB, zero leakage, E2E forwarding) and the
stack is tiny. Link stability over the wifi+NAT leg: **NOT acceptable
as-is, root cause open** (ladder above). **Interim: CONDITIONAL GO** —
Phase-2 design may proceed for wired/RF legs, gated on (a) the wired-
uplink A/B isolating the wifi medium (operator: one cable), (b) the
48 h soaks (which now double as flap-frequency counters — moc2's
verdict-wired leg will show FAIL samples at each retraction; that
noise IS the measurement), (c) failover-with-second-path, (d) AREDN
parameters captured from observed reality before any interop config.
