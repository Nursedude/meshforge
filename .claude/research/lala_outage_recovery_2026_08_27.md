# Hurricane Lala outage recovery — fleet audit, root causes, and the WAN-decoupling lessons (2026-08-27)

> Context: Hurricane Lala (2026-08-14) took grid power for ~a week; the
> network was degraded/dark for almost two weeks total. This session was the
> first full-fleet audit after restoration. Method: re-derive everything from
> ground truth (`honest_status.sh`, per-box journals, live probes), fix at
> the source, leave a witness per fix, and extract the resilience lessons for
> the coming Starlink uplink move. Companion arc: the 08-14/08-15 pre-storm
> hardening (`project_hurricane_lala_emergency_mode_roadmap_2026_08_14`).

## Starting posture (re-derived, session start)

`honest_status --quick`: fleet SHA drift FAIL (0/8 on HEAD — undeployed, the
manual-deploy model working as designed), watchdog 3 WEDGE + 21 degraded,
4 wired crons failing on the manager, moc4 paged DOWN, nomadnet/lxmd/
meshcore-chat user units failed on 4 boxes.

## Root causes found (each verified live, none guessed)

### 1. THE class of the storm: zero-byte state files (13 corpses, 5 boxes)

Power loss truncates an in-flight write to 0 bytes / NUL fill (ext4 allocates
the block; data never flushes). Every corpse produced a DIFFERENT visible
symptom, which is why it looked like five unrelated failures:

| Corpse | Consumer | Visible symptom |
|---|---|---|
| moc `lxmf_storage` ratchet | meshforge-gateway | **wedged 8+ days**: first attempt `OSError: Could not read ratchet file contents`, every retry `Attempt to register an already registered destination.` — the real cause scrolled away; delivery confirmations 0/94; `gateway_rt_canary` FAIL since ≥08-20 |
| moc lxmd ratchet | meshforge-lxmd (propagation node) | crashloop → start-limit → propagation node dark 182h, store-and-forward drill INDETERMINATE ×122 |
| moc + moc3 + VolcanoAI nomadnet ratchets | nomadnet.service | tmux session dies in ~4 s → start-limit-hit for 8 days |
| meshanchor-server ×3 (MA lxmf/broadcast/nomadnet) | MA consumers | latent — armed for the NEXT restart |
| moc `meshtastic_broadcast_storage`, propagation_soak router ratchets | broadcast bridge, soak drill | latent / drill degraded |
| mini history jsonl ×2 NUL lines | mini audit | `history_integrity` degraded → mini_honest_fire FAIL(2) hourly |

**Mechanics of the gateway wedge** (the expensive one):
`LXMRouter.register_delivery_identity()` registers the destination with
Transport FIRST, then `enable_ratchets()` raises on the corpse — so the
destination stays registered and every reconnect retry fails on
`already registered`, an error that names the wrong cause. The box's own
diagnostic even suggested `restart rnsd` (rnsd was healthy throughout).

**Fixes shipped**:
- Fleet-wide sweep quarantined all 13 corpses (validated with the same
  `umsgpack` read RNS performs; corpses kept as `*.corrupt-2026-08-27`).
- `quarantine_corrupt_ratchets()` in `gateway/_rns_bridge_connection.py`,
  called before LXMRouter setup in BOTH the gateway bridge and the
  meshtastic broadcast bridge — the wedge is now self-healing at the
  consumer. Validator mirrors `RNS.Destination._reload_ratchets` exactly
  (including the membership-test-raises shape: a NUL corpse unpacks to
  int 0 without error — readability alone is NOT the consumer's bar).
  Tests: `tests/test_ratchet_quarantine.py` (7, plant-the-corpse style).
- Eval: `evals/local_brain/power_loss_zero_byte_state_2026_08_27.jsonl`.
- Tells added to `persistent_issues.md`.
- **Proof of end**: `gateway_rt_canary OK peer=meshanchor-server
  confirmed=4.0s ack_back=0.0s` — first OK in the retained verdict log.
- ⚠️ MA parity: `meshanchor-server` corpses quarantined operationally, but
  the `quarantine_corrupt_ratchets` guard itself is MF-side only —
  **port to MeshAnchor's bridge** (twin-map shape-parity tier).

### 2. moc4/.248 RAK HAT bringup stalled on a boot overlay (12 days radio-dark)

New RAK6421 WisBlock + RAK13302 (SX1262, slot 2) installed 08-15; preset
`lora-RAK6421-13302-slot2.yaml` needs kernel CE1 (`spidev0.1`), but
`/boot/firmware/config.txt` still carried `dtoverlay=spi0-0cs` from the
previous HAT — only `spidev0.0` existed, RadioLib got `SX126x init result
-2` (CHIP_NOT_FOUND), meshtasticd hit start-limit the same minute the preset
landed. Fix: drop the overlay (backup kept), reboot → radio init clean,
**181 packets received in the first 25 min**. Offline-monitor logged
`RECOVERED [moc4] (was down ~120m)`.
Tell: preset `spidev` value vs `ls /dev/spidev*` — check BEFORE suspecting
wiring or the module.

### 3. Clocks ran days-stale during the outage (the instrument-forgery leg)

moc4's `uptime` said 4 days while wtmp said continuously up 12 — the box ran
~8 days behind real time (RTC-less Pi: fake-hwclock restored stale time at
boot; NTP unreachable with WAN down; NTP finally stepped it forward on
restoration). Consequences measured: cron fired 5 jobs in 4 days, verdict
freshness lied, `who -b` lied. This is honest_failure_modes #6 at fleet
scale: during a WAN outage every wall-clock instrument on every RTC-less box
degrades TOGETHER.

### 4. pw2lab forward drift — fixed at the WRONG layer on 08-15, so it re-drifted

The 08-15 fix set the rendered `/etc/config/firewall` redirect to :2200 but
left the AREDN SOURCE template `/etc/config.mesh/setup` at
`wan:tcp:22:10.120.250.200:22:1`; AREDN re-rendered and silently restored
the broken form. Fixed at BOTH layers this time (setup template + uci +
fw4 reload; DNAT for :2200 verified live in nft). **AREDN rule: port
forwards live in `/etc/config.mesh/setup` `list port` lines — that is the
SSOT the firewall is re-rendered from; a uci-only fix is a time bomb.**
pw2lab itself is physically dark (no DHCP lease, ARP incomplete, 100% loss
from its own gateway) — needs hands on the bench Pi.

### 5. fleet_front_probe conflated REFUSED with TARGET-DARK

bash `/dev/tcp` returns rc=1 for both ECONNREFUSED and EHOSTUNREACH, so a
physically-off pw2lab read as "forward rule broken" the day after the rule
was fixed — two realities, one claim (the instruments-fail-at-legibility
class). Fixed: python socket probe with errno split; new TARGET-DARK leg →
CONCERN (never FAIL, never OK); unknown errno stays pessimistic (refused).
All four outcomes drilled (open/unreach live; refused/timeout planted).

### 6. mini federation rule aim drift

`federation_peer_unhealthy_unexpected` excluded the MA server by IP glob
(`*192.168.86.29*`); peer_status started publishing `peer_name`, the subject
became `meshanchor-server`, and known-normal backoff escalated. Globs
updated to the name form. (The refusal itself was outage backoff residue —
the endpoint answered 200 from the same vantage during the fix.)

## Also repaired
- Fleet converged 8/8 on `61f3d7c7` (`fleet_pull.sh`).
- moc5 rebooted onto pending kernel 6.8.0-1063 (marker cleared, no failed units).
- Outage-stale daily crons re-fired with verdicts (calibration_reverify,
  harness_audit 14/14 PASS, fleet_naming_drift, pytest_tmp_prune).
- mini history NUL lines quarantined → `mini_honest_fire HONEST/HEALTHY`.
- nomadnet ×3, meshforge-lxmd, meshcore-chat restarted and verified past
  their prior crash window.

## Engineering for the Starlink move — decouple everything from the WAN

The storm measured exactly which fleet functions were still WAN-coupled.
Starlink adds: CGNAT (NO inbound reachability at all), higher latency
jitter, and obstruction/weather micro-outages — so every coupling below
gets WORSE there, and the outage-mode behaviors become routine behaviors.

Already decoupled (held up during Lala):
- **Names**: `/etc/hosts` fleet block (the 07-25 AAAA/WAN lesson) — names
  resolved with the uplink down. KEEP.
- **RNS fabric**: AutoInterface (LAN) + local TCP links — mesh traffic
  never needed the WAN.
- **Deploy**: manual `fleet_pull` meant no auto-pull storms on restore.

Still WAN-coupled — the roadmap, in priority order:
1. **Time — SHIPPED 2026-08-27 (`scripts/ntp_island.sh`, commit ffeef3de)**:
   LAN NTP island live. Two chrony servers (manager + the central gateway
   box) follow WAN pools normally and keep serving at `local stratum 10
   orphan` when the WAN dies; 9 clients prefer them — 7 fleet boxes via
   the script's timesyncd drop-in, the bench-bot box via a hand drop-in +
   2 island /etc/hosts entries (it is outside the fleet-hosts heal loop —
   if the island IPs drift, that file must be updated by hand), and the
   OpenWrt tunnel router via `uci system.ntp.server` (island first, its
   pools as fallback). VERIFIED: `chronyc clients` on the island server
   shows NTP from every applied client (the NAT'd ones arriving as their
   fronts); cross-island peering measured at stratum 10/µs offsets;
   cc_ntp non-interference drilled with the real consumer (`cloud-init
   single --name ntp --frequency always`, drop-in byte-identical).
   BELIEVED (config quoted, not outage-drilled): orphan takeover under
   real WAN loss — drill at the next planned WAN maintenance by pulling
   the uplink and expecting island `Stratum: 10` with clients still
   syncing. The dark bench Pi gets `client-apply` at revival. Fleet
   clocks now converge to each other with the uplink down; cron, verdict
   freshness, and dedup windows stay truthful.
2. **Inbound reachability**: CGNAT kills port-forward fronts. Everything
   inbound must become outbound-initiated — the alaula/kiai reverse-tunnel
   pattern generalizes (or WireGuard to a small anchor host). The AREDN
   fronts keep working on the LAN side; the front-probe now tells the
   truth about them.
3. **NTFY/paging**: ntfy.sh is WAN; the ntfy_loopback probe already
   detects the dark channel. During outages the email backbone is also
   WAN. Consider a LAN-side page path (the mesh itself — LXMF to the
   operator's node — is the in-domain answer).
4. **Boot residue**: `boot_survival_audit` (08-15) worked — extend the
   power-loss drill: the zero-byte class is now self-healing for LXMF
   ratchets; jsonl appenders (history, ledgers) still need their readers
   tolerant (mini's audit correctly flagged rather than crashed — that is
   the right shape; keep it).
5. **Clock-skew detector**: cheap probe candidate — `uptime`-vs-wtmp
   disagreement, or NTP unsynced > N hours, surfaces "this box's wall
   clock is currently forgeable" as its own signal instead of letting
   every time-based verdict silently degrade. (Weigh against footprint
   rule before adding.)

## Verification ledger (end of session)
- Full suite: 10950 passed (honest_status full run, exit 0 leg).
- Lint: exit 0. CI for HEAD: PASS (run 32822950256).
- `gateway_rt_canary OK` — the product end proven cross-box.
- Watchdog: 3 WEDGE + 21 degraded → (snapshot mid-session) 1 wedge + 15
  degraded, most remaining legs being stale-verdict residue that clears on
  cron cadence; the moc wedge (delivery_confirmation_stall) was fixed
  AFTER that snapshot — expected to clear within its window.
- pw2lab: physically dark, operator hands needed. UNKNOWN until powered.
