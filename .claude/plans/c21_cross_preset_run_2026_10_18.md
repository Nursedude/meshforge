# C21 cross-preset bridge run — plan (2026-10-18)

**Purpose test.** END: a message arrives across presets, or the drop is named.
Gate: `docs/capabilities.md` row "Dual-radio mesh_bridge serial — needs
sustained cross-preset traffic to validate" (unproven since 2026-01-14,
re-confirmed by the outside research review 2026-10-18). Matrix: the
Meshtastic↔Meshtastic edge of `.claude/foundations/bridge_contract_matrix.md`.

**Acceptance sentence — APPROVED 2026-10-18 (operator: "your call go"; edited
so an RF loss can never hide as, or be blamed on, the bridge):**
"Over 24 h of scripted traffic both ways, every injected message is accounted
for at each hop: either the bridge logged it as received or it is labelled
RF-ingress loss. Each received message is then either heard by the far
source radio, counted under a named bridge drop reason, or labelled
RF-egress loss (the bridge logged the TX, the far radio did not hear it).
Bridge-attributed unaccounted messages are 0, duplicates are 0, and the
planted controls (a repeat and a pre-tagged loop) are dropped under their
named reasons. RF losses are reported with their rate and any coincidence
with moc4 HAT TX; they do not fail the bridge, but a rate above 5% per leg
makes the run INCONCLUSIVE (re-site the radios), never a pass."

## Operator decisions (2026-10-18)
- Host: **moc4** (Pi 4, 4 GB, RAK6421 HAT + sensors stay on fleet duty;
  `meshforge-gateway` inactive by role — NOT enabled; the test bridge runs
  as a transient `systemd-run` unit with its own config). 24 h undisturbed: yes.
- Radios: spare Heltecs (two V3 + two V4, Monday AM) on **direct USB ports** (a hub
  was declined: cross-over issues). Antennas on all. Best-effort spacing;
  moc4 is already surrounded by antennas.
- Portables "meshforge v4" (near moc4) and "meshforge p5" (near moc5) stay on
  the fleet channel, UNTOUCHED (RF-only reach — retuning strands them).
  They are stage-2 human witnesses via the phone app, not stage-1 counters.
- LXMF / RNS: NOT in this run (one variable; D1 standalone needs no RNS).
- Mesh Bridge (IceNet-01) head-to-head: skipped for now.

## Stage 1 — isolated desk run on moc4
| Radio | Role | Channel |
|---|---|---|
| V3 #1 | bridge primary (serial) | `MFLab` LONG_FAST, US slot 80 = 921.875 MHz |
| V3 #2 | bridge secondary (serial) | `MFLab` SHORT_TURBO, US slot 45 = 924.25 MHz |
| V4 #1 | scripted source/sink, LF side | as primary |
| V4 #2 | scripted source/sink, ST side | as secondary |

**Hardware (operator, 2026-10-18):** likely two V3 + two V4, installed on
moc4 USB **Monday AM** (2026-10-20). The legs are the V3s (the model the
bridge was already live-probed on); the sources are the V4s. ⚠️ The script
must be BUILT and REVIEWED before then — nothing transmits until it is.

All: random 256-bit PSK (never committed), ~1 dBm, hop_limit 1, MQTT uplink
off, position off. Frequencies are PHYSICS (slot formula, reproduces fleet
LF 906.875 MHz) until each radio's own readback confirms them. Clear of fleet
LF 906.875, fleet ST 905.75, HawaiiNet RNS 903.625.

⚠️ Topology differs from the field row (LF via HAT/meshtasticd + ST USB):
stage 1 is serial+serial and proves the bridge LOGIC; the HAT leg is not in it.

## Script (to build, test-first, non-author review before first use)
`scripts/c21_bridge_run.py` — subcommands:
1. `configure` — per port: region US, preset, channel_num, tx_power, hop_limit,
   channel name + PSK, MQTT off, position off; READ BACK and refuse on mismatch.
   Raw SerialInterface construction is MF007's territory — use the sanctioned
   path or allowlist it explicitly.
2. `bridge` — write the run's gateway config (both legs `connection_type:
   serial`, by-id paths) and start a transient unit; never touch
   `meshforge-gateway`.
3. `traffic` — one numbered message each way every 2 min (~0.4 % LF airtime),
   text `c21 <run> <seq> <dir>`; log every send and every receive with
   monotonic + wall time.
4. Controls: a repeat inside the dedup window, and a pre-tagged loop
   (`[Mesh:…]` prefix) — each must appear under its named drop counter.
5. `reconcile` — sent vs delivered vs the bridge's drop counters; duplicates;
   every loss correlated against moc4 HAT TX times (meshtasticd journal) and
   labelled "RF blanking" when it coincides.
6. **Abort canary** (direct USB ports share the 5 V rail with the HAT): any
   change in moc4 `meshtasticd` `NRestarts` or an `init result` ≠ 0 stops the
   test radios. `get_throttled` is logged but is BLIND to the HAT rail.

## Stage 2 — after stage 1 passes
Same radios spread to Pis around VolcanoAI (distance, walls, fading) — tests
RF legs, not logic. Plus phone-app human sends via the portables through
moc's live bridge.

## Result
Lands in the C21 capabilities row: proven, or not proven with the numbers.
