# Storm prep: fleet power engineering + the 2-week Starlink migration (2026-08-27)

> Context: another storm expected within weeks; Starlink cutover planned in
> ~2 weeks; goal is an emergency-comms (eComm) posture that is measured, not
> guessed. Companion: `lala_outage_recovery_2026_08_27.md` (what the last
> storm broke and the WAN-decoupling already shipped: names, time, deploy).

## Measured fleet power + PSU-health audit (2026-08-27 sweep)

Instrument: Pi 5 PMIC ADC (real per-rail watts), `vcgencmd get_throttled`
(brownout/thermal HISTORY — what a storm exposes), lsusb, storage bus.

| Box | Model | Measured/est. W | PSU/thermal history | Notes |
|---|---|---|---|---|
| VolcanoAI | Pi 5 | **4.10 W (PMIC)** | clean 0x0 | CH341 LoRa; has RTC (no battery) |
| moc1 | Pi 5 | **2.25 W (PMIC)** | clean 0x0 | CH341 + ESP32; has RTC (no battery) |
| moc | Pi 4 | ~3.5 W est. | clean 0x0 | CP210x radio; island NTP server, NO RTC |
| moc2 | Pi 4 | ~3.5 W est. | clean 0x0 | ESP32 |
| moc3 | Pi 3 | ~2.5 W est. | 0x60002 = load-induced freq-cap (the 806-thread gateway leak, fixed this session) — NO undervoltage bits | CP210x |
| moc4 | Pi 4 | ~3.5 W est. | clean 0x0 | RAK6421 HAT |
| moc5 | Pi 4 | ~3.5 W est. | clean 0x0 | CH341 + ESP32 |
| meshanchor-server | Pi 4 | ~4 W est. | **0x80000 = soft temp limit has occurred → check case/cooling before the storm** | RAK4631 USB |
| kiai | Pi 4 | ~3.5 W est. | clean 0x0 | CH341 |
| trdev | Pi Zero W | ~1 W est. | clean 0x0 | bench bot |
| pw2lab | Pi Zero 2 W | ~1 W est. | OFF (operator; wifi issues) | **MeshAdv Mini HAT** |

**Compute subtotal ≈ 31–33 W.** Network gear (est.): m1 MikroTik ~6–10 W,
alaula (OpenWrt travel router) ~3–5 W, AREDN nodes ~5–7 W each ×3.
**Ecosystem today ≈ 55–65 W ≈ 1.3–1.6 kWh/day.**

**Every root filesystem is SD-card (mmc).** The zero-byte ratchet class was
the mild face of power-loss on SD; the severe face is card corruption.
Ranked mitigation: island/gateway boxes to SSD or high-endurance cards.

## Starlink changes the power problem more than the network problem

| Uplink | Avg draw | Fleet total | 24 h battery need |
|---|---|---|---|
| today (cable/whatever m1 has) | ~0 marginal | ~60 W | ~1.5 kWh |
| **Starlink Mini** | 20–40 W | ~85–100 W | ~2.2 kWh |
| Starlink Standard (Gen3) | 50–75 W | ~115–135 W | ~3 kWh |

The dish dominates the eComm battery budget — **for storm resilience the
Mini is roughly half the battery cost of the Standard**. Decision is the
operator's (throughput vs runtime); the fleet itself is a rounding error
next to either dish.

**Battery sizing** (LiFePO4, 80% usable): fleet-only 24 h ≈ 1.2 kWh → one
12 V 100 Ah (1.28 kWh). Fleet + Mini 24 h ≈ 2.2 kWh → 2 kWh–2.5 kWh station.
Fleet + Standard 24 h ≈ 3 kWh. Tier the loads: Tier-1 (m1 + moc gateway +
one radio box + dish) rides the battery; Tier-2 (map/monitor boxes) sheds.

## Time resilience — state after this session

- NTP island live (see lala doc): fleet clocks converge to each other
  WAN-down.
- **fake-hwclock installed on both island servers** (chrony replaced
  timesyncd there, which silently removed timesyncd's boot clock-file
  restore — closed 2026-08-27; trixie splits it into enabled
  fake-hwclock-load/save units and masks the legacy combined unit, which
  is packaging, not a fault).
- **Cheap high-value purchase: RTC batteries for the two Pi 5s**
  (VolcanoAI, moc1 — rpi-rtc present, unbatteried). With batteries, real
  time SURVIVES power loss. Then consider moving the second island server
  moc (Pi 4, no RTC) → moc1 (Pi 5 + RTC): one `ntp_island.sh server-apply`
  on moc1, `client-apply` on moc, update client args fleet-wide.

## Found live during this audit (fixed)

moc3's gateway at **806 threads, load 182, 'can't start new thread'** —
every failed LXMF setup retry constructed a fresh LXMRouter (job threads
leak) and a duplicate announce handler, ×4 days on a Pi 3. Cured (restart),
class-fixed in both repos (`acc7b870` MF / `db18d00b` MA, pinned by tests),
trigger-fixed by the ratchet guard. The power audit's throttle flags are
what surfaced it — the instrument earned its keep on day one.

## Bench enclave posture (operator judgment 2026-08-27)

trdev/pw2lab sit behind the AREDN node front (`WH6GXZ-6-VOLCANO-HI-HAP`,
WAN .250, wifi dudeNET, node LAN 10.120.250.193) — NOT behind m1 like the
fleet. Lala showed that path is the most fragile: outside fleet_hosts
healing, its own DNS island, forwards that re-render from
`/etc/config.mesh/setup`. Operator's call: a valid USE CASE, questionable
as INFRASTRUCTURE. Standing recommendation: treat the enclave as
expendable-in-storm (nothing Tier-1 lives behind it), OR re-home the mesh
bot behind m1 before the next storm. pw2lab revival checklist when powered:
lease at 10.120.250.200 → `:2200` forward already fixed → hand-apply the
timesyncd island drop-in (box has no /opt/meshforge) → boot_survival_audit
reports itself.

## Two-week Starlink migration checklist

**Week 1 — inventory + stand up the outbound-only posture**
1. Enumerate every inbound dependency that CGNAT kills: port-forwards on
   the current WAN router, anything that dials INTO the LAN. (The fleet's
   own fronts are LAN-side and survive.)
2. Prove the reverse-tunnel pattern (alaula-style) or a WireGuard anchor
   covers remote access from outside; test from a phone hotspot NOW, not
   on cutover day.
3. Decide Mini vs Standard (power table above); order RTC batteries; stage
   the battery tier wiring.
4. Verify ntfy + brain_git_push + claude-memory sync all work outbound-only
   (they should — they already dial out).
5. m1 WAN-failover config: if m1 can hold both uplinks, storm posture is
   Starlink primary / old uplink secondary (or vice versa until cutover).

**Week 2 — cutover, and the cutover IS the drill**
6. Swap m1's WAN to Starlink during a planned window. While the WAN is
   down mid-swap, run the **NTP island takeover drill** (the one BELIEVED
   from the island build): expect island `Stratum: 10`, clients still
   syncing — upgrade it to VERIFIED for free.
7. Post-cutover gate: `honest_status.sh --strict`, `fleet_front_probe`,
   ntfy loopback, CI poll from a fleet box, `chronyc sources` back on
   pools. AAAA latency check (the 07-25 class) against Starlink DNS.
8. Watch for CGNAT surprises: Starlink DNS quirks, IPv6-preference (CGNAT
   is v4; Starlink hands out v6 — getaddrinfo ordering may change failure
   modes; re-run the AF_UNSPEC vs AF_INET timing sweep).

## Storm-readiness delta vs Lala (what's already closed)

| Lala failure | Now |
|---|---|
| zero-byte state files wedged gateways/lxmd/nomadnet | self-healing guard, both repos, live-drilled ×3 boxes |
| clocks 8 days stale | NTP island + fake-hwclock on servers; RTC batteries pending |
| pw2lab forward re-drift | fixed at the AREDN render SSOT |
| dark box invisible | boot_survival_audit on all 11 (08-15) + offline monitor |
| front drift unwatched | fleet_front_probe (now errno-honest) |
| configs unrestorable | fleet-vault + AREDN/RouterOS capture (08-15) |
| retry-loop thread leak (silent 4 days) | fixed + pinned both repos |
| PSU/thermal health unknown | measured; 1 thermal finding (meshanchor-server cooling) |

## Post-storm triage: "the watchdog failed first" — overturned (2026-08-27)

Forensics across all 9 boxes: **every watchdog daemon survived the entire
storm** — NRestarts=0 fleet-wide, most processes ran continuously from
Aug 15-16 onward, and the journals show them working hard: 31 NEW signals
on the manager and 19 on moc during Aug 14-21 (tracer_peer_unreachable
everywhere, an rns_rpc_unresponsive wedge caught on meshanchor-server
Aug 18, moc's synth soak measuring delivery decay at 0.93/0.94 as the
outage bit). The sensors never failed.

What failed first was the **delivery of their findings**: ntfy (WAN-
dependent), the manager box itself (down through parts of the window —
taking /fleet aggregation and paging with it), and clock-skewed
freshness that made surviving verdicts look dead. From the operator's
chair that is indistinguishable from "the watchdog died" — the fleet
degraded loudly into local journals nobody could read. Caveat: moc3's
storm-window journal is gone (retention churned by its 806-thread
thrash), so that one box's watchdog behavior is UNOBSERVABLE, not known-
good.

Lesson for the roadmap (already on it as #3): the observability spine's
last WAN coupling is the PAGING path. A LAN/mesh-side page route (LXMF to
the operator's node) is what turns "sensors survived" into "operator saw".

## Domain assessment: portable / scalable / reproducible (2026-08-27)

**Portable** — app layer yes, fleet layer by-design-with-config.
MF014 lint + TestOperatorValueContract force operator values out of the
repo (it rejected this very session's first ntp_island commit); MeshAnchor
proves the port path (twin tiers + parity_check); standalone.py is the
zero-dependency offering. The hard dependency is the FORKED RNS/LXMF pips
(SHA-pinned, MF-owned) — portable only with the forks installed. Fleet
organs assume the operator's naming/topology (fleet_hosts, .mf.internal,
a manager box) supplied as config — portable in the "new site, new
config" sense, not the "unzip anywhere" sense. mini: engine+presets are
repo-clean (MF014), operator env external — portable; its 73-rule
canonical JSON is per-box EVOLVED state (see reproducible). claw:
templated instanced units (mini-dudeai-claw@) — portable pattern, bound
to its ESP32 bench hardware by nature.

**Scalable** — to tens of boxes, with two real constraints, neither CPU.
Fleet organs are O(N) serial ssh loops (fleet_pull, offline_check,
honest_status legs): fine at 9, parallelize before ~30. RNS/LXMF scale by
protocol; the map federates per-vantage. The measured constraints: (1)
footprint on the SMALLEST box (watchdog = 7.1% RAM + 4.3s/tick on the
Pi 3; the subtraction arc exists because probe count compounds), (2)
frontier/operator attention for mini's ratification loop — rules scale
per-box, JUDGMENT does not (the Max-x5 downshift makes this the explicit
budget). Manager visibility is the SPOF-shaped piece (deadman cron on
moc1 mitigates; both page via WAN — same roadmap #3).

**Reproducible** — code fully; state mostly; the RESTORE PATH is the
untested guard. Strong: repos+CI, SHA-pinned forks, templates/systemd,
db_inventory, guard drills, 3-tier fleet-vault (body/mind/fingerprints)
with LAB-ZERO.md restore order, monthly captures. This storm was a
partial live test: the fleet recovered on git pull + state-file
quarantine, zero reimages. Measured gaps: pw2lab had NO captured config
(found during its revival attempt); /etc drift added this session
(chrony, timesyncd drop-ins, fake-hwclock) awaits the next capture cycle
— refresh fleet-vault after storm-prep changes land, not monthly;
hostname inconsistency (wh6gxzmesh, wh6gxzser vs meshforge-*) is
reproducibility friction; mini's rules rebuild-from-seeds is unproven.
**LAB-ZERO has never been drilled — and pw2lab is the perfect drill**: if
its SD is storm-corrupt anyway, rebuild it from the restore path and the
reproducibility claim gets its first VERIFIED leg.
