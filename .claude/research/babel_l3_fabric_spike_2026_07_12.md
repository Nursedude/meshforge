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

## THE finding: wifi-STA jitter flaps default-tuned babel

With defaults (`hello-interval 4`), the router's route to moc2 flapped
**UP → RETRACT → UP every ~15–25 s** while moc2's route stayed solid —
one-directional neighbour loss. Inner-tunnel evidence: **0 % packet loss
but RTT 1.0→235 ms (avg 60, mdev 78)** over 40 pings — the router's
wifi-STA uplink (contention/scan stalls; powersave already off on all
its radios) delays frames in bursts that read as missed hellos.

**Mitigation (protocol-level, portable)**: `interface wg-spike type
tunnel hello-interval 12 rtt-max 456 max-rtt-penalty 48` both sides →
route solid for the whole observation window post-tune; the 48 h soak
extends the sample. Implication for Phase 2: **any babel adjacency that
crosses a wifi/NAT leg needs jitter-tolerant timers**; wired/AREDN-RF
legs can keep faster hellos. This is exactly the kind of environmental
variable a design-from-docs would have missed.

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

## Go/no-go read (interim — final after the 48 h soak)

Convergence, withdrawal, containment, memory: **all inside criteria**,
and the stack is tiny (babeld ~1 MB RSS). The wifi-jitter flap is the
one real risk surfaced, and it tuned out cleanly. **Interim: GO for a
Phase-2 design**, conditional on (a) the 48 h soak staying flat/quiet,
(b) failover-with-second-path measured, (c) AREDN parameters captured
from observed reality before any interop config is written.
