# Kilo K1 — link-matrix observatory (DESIGN — BUILT 2026-07-04, `e1073df8`)

> Written 2026-07-04 at K0.1 close as the fresh-session handoff (author
> context was aging; design-before-code is the K-ladder rule anyway).
> Parent: `.claude/plans/kilo_lab_instrument.md`. K0/K0.1 are LIVE
> (`src/kilo/`, moc + moc2 green, `kilo_collect` crons wired to #78).
>
> **BUILD NOTES (2026-07-04, commit `e1073df8`)**: shipped as designed,
> two deltas. (1) The `_message_callbacks`/`_node_callbacks` hooks named
> in context-gold #1 turned out NOT to fit (message cbs fire only for
> text; node cbs carry neither topic nor per-packet fields) — a new
> `add_packet_callback(topic, data)` hook on the subscriber is the
> capture point. (2) Building against the LIVE payload exposed a latent
> decoder bug: json `from` is NUMERIC on the wire and
> `_handle_position`/`_handle_telemetry` died on a swallowed
> AttributeError for every such packet — fixed at the `_ensure_node`
> chokepoint, pinned by `TestNumericFromCanonicalized`. Live answers to
> the open questions: moc payloads always carried id/snr/hops_away in
> the proof windows; `relay_node` NEVER emitted by current firmware
> (relay_partial stays NULL — resolution is K1.1 if it ever appears);
> hourly per-edge rollup SKIPPED (measured volume ~200 rows/h on moc →
> 7d ≈ 34k rows, fine without it); volume cap deferred with the same
> evidence. Own-uplink self-edges: skipped at parse with a witness count.

## What K1 is

Every packet a receiver hears is a free channel sounding. K1 persists
**edges** — (receiver ← sender) RF observations — and turns them into a
living link matrix with per-edge baseline drift detection. Zero new TX:
passive listening only (invariant #1).

## Context-gold facts (verified during K0 — do NOT re-derive)

1. **Sounding source exists today**: the MQTT json uplink carries
   per-packet `from`, `snr`, `rssi`, `hops_away`, `hop_start`,
   `relay_node`; `MQTTNodelessSubscriber` already decodes it. K0's
   readings keep only the LATEST per node — K1 needs the PER-PACKET
   stream: the subscriber exposes `_message_callbacks` /
   `_node_callbacks` hooks (see `_on_message`); register a callback
   inside the existing collect window rather than a second client
   (#73: one hardened client, no new fd surface).
2. **Receiver identity** = the uplinking gateway's own node id — it is
   the json topic SUFFIX (moc: `msh/2/json/LongFast/!32962f10`). Parse
   from topic; do not query the radio (#17).
3. **Edge semantics nuance**: Meshtastic rx_snr/rssi describe the LAST
   HOP into the receiver. `hops_away==0` → edge is (sender→receiver);
   `hops_away>0` → the RF edge is (relay_node→receiver) and
   `relay_node` is only the LAST BYTE of the relayer's id (the
   subscriber has partial-relay merge logic — reuse, don't reinvent).
   Record hops fields on every edge row so K1 consumers can filter
   direct-only when purity matters.
4. **Matrix rows scale with receivers**: today moc is one receive
   point; every additional gateway box + every flashed lab node that
   uplinks json becomes another row. The 20-node lab = dense matrix.
5. **Predicted side**: `src/utils/rf.py` has the link-budget math
   (CLAUDE.md entry point). BUT indoor absolute prediction is folly —
   the honest K1 detector is **drift vs the edge's OWN rolling
   baseline** (median SNR over N days), not observed-vs-Friis. The
   rf.py diff is a K1.1+ outdoor/calibrated-pair feature.

## Proposed shape (fresh session refines)

- **Storage**: new `edges` table in the EXISTING kilo_telemetry.db
  (same DBSpec — inventory is per-DB): (ts, receiver, sender, snr,
  rssi, hops_away, hop_start, relay_partial). High volume → shorter
  retention than readings (7d?) + optional hourly per-edge rollup
  (median/count) table for the long baseline. Prune with the existing
  prune() pattern; UNIQUE guard like readings.
- **Capture**: message-callback inside `collect_mqtt`'s window (flag
  `--edges`, default on). No resident daemon in K1 unless the operator
  asks — if one arrives it MUST bring systemd template + watchdog probe
  + mini seed routing (invariant #5, #79 class).
- **CLI**: `kilo matrix` — receivers × senders grid, cell = median SNR
  (window) + sample count + drift glyph (🟢 within baseline band /
  🟡 drifting / 🔴 shifted / ⚪ too-few-samples — tri-state honesty:
  sparse data is UNKNOWN, never "fine").
- **Registry**: optional `position` per node (lab-local meters or
  lat/lon) — needed only for K1.1 prediction work, NOT for baselines.
- **Baseline drift**: per (receiver, sender): rolling median ± MAD band;
  alert-worthy = sustained excursion (debounce in ticks, not wall-clock
  — RTC-less fleet, honest_failure_modes #6). Surfacing to mini/ntfy is
  a SEPARATE step with seed-coverage gates — K1 ships the CLI view
  first, evidence before alerting.

## Open questions for the build session

- Edge volume on moc (busy gateway): measure rows/hour in the first
  window before choosing retention; add a cap like node_history's LRU
  if needed (#49 lesson).
- Does the json payload expose rx_snr for OWN-uplinked packets
  consistently across firmware 2.6/2.7? Verify against live payloads
  before trusting zeros (a 0.0 snr can be legitimate — never treat as
  absent; None-vs-0 discipline).
- `channel "+"` windows on boxes with multiple channels: edge rows
  should carry channel name for K3's A/B split later.

## Starter for the fresh session

    Read .claude/plans/kilo_k1_link_matrix_design.md and
    .claude/plans/kilo_lab_instrument.md, then build K1 per the design:
    edges capture in the collect window, kilo matrix CLI, baseline
    drift tri-state. Live-prove on moc like K0 (its collect cron is
    already feeding readings every 10 min).
