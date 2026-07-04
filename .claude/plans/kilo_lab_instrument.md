# Kilo — the lab as one honest instrument

> *kilo* (Hawaiian): the observer, the one who reads signs and forecasts.
> Born 2026-07-04 from the operator's dream: 20+ ESP32 / nRF52 / LoRa dev
> nodes + environment sensors around the lab — "do something unique with
> this domain." The unique thing: treat the swarm as ONE distributed
> instrument that measures both the physical lab AND the mesh itself,
> governed by the same epistemics the fleet already runs (calibrated
> claims, honest failure modes, tri-state observability).

## The ladder (evidence-gated, W-style)

| Rung | What | Status |
|------|------|--------|
| **K0** | Node registry (identity/role/cadence expectations) + telemetry ingest spine (SQLite readings) + tri-state presence status + discovery | **SHIPPED + LIVE-VERIFIED 2026-07-04** (`src/kilo/`; moc 🟢) |
| **K0.1** | Claw adapter (reads `claw_last_tick.json` — zero device I/O) + recurring collect via crontab + cron_verdict (#78 alerting for free) | **SHIPPED 2026-07-04** |
| K1 | Link-matrix observatory: every packet a channel sounding; per-edge baseline drift; RF-shadow anomalies | **DESIGNED** — `.claude/plans/kilo_k1_link_matrix_design.md` (build in a fresh session) |
| K2 | Sensor trust ledger: co-located cross-checks, per-sensor held/broke record (the calibration ledger applied to hardware) | planned |
| K3 | Meshtastic vs RNS controlled A/B on identical boards, same air: delivery/latency/airtime envelopes, eval-case scoring | planned |

## K0 surface

- Registry `~/.config/meshforge/kilo_nodes.json` (template
  `configs/kilo_nodes.example.json`) — identity ANCHORS are radio ids;
  IP-shaped anchors refused loudly (DHCP-reshuffle lesson).
- Store `~/.local/share/meshforge/kilo_telemetry.db` — DBSpec'd (MF013),
  30d prune, UNIQUE-guarded idempotent ingest.
- Ingest = bounded window over the existing `MQTTNodelessSubscriber`
  (radio → meshtasticd → mosquitto → decoded `MQTTNode` env/power/RF
  metrics). Zero new RF traffic; PhoneAPI never touched (#17).
- CLI: `PYTHONPATH=src python3 -m kilo collect --seconds N` ·
  `... -m kilo status` (exit 0/1/2, cron_verdict-wireable) ·
  `... -m kilo discover` (unregistered senders → registry authoring).

## Invariants (hold these on every rung)

1. **Airtime is the subject, never the transport** — telemetry rides
   WiFi/MQTT/NATS; the air carries only soundings and experiment traffic
   under a duty-cycle budget.
2. **Identity, not addresses** — registry anchors are radio identities;
   a healthy node must never read dark because DHCP moved it.
3. **Tri-state or it lies** — OK / DARK / UNKNOWN; a node whose anchor
   kind has no adapter yet is UNKNOWN, never OK, never DARK.
4. **No verdict without an envelope** — K2/K3 claims come from measured
   pass envelopes (local_brain_eval pattern), logged, re-derivable.
5. **New resident daemons arrive with their systemd template + watchdog
   probe + seed routing** (deploy-restart class #79; seed-coverage gate).
   K0 deliberately ships NO daemon — collect is bounded.

## Recurring collection (K0.1 wiring)

Crontab + `cron_verdict.sh` — the same regime as claw_metrics_push, so
Issue #78 (`cron_verdict_stale`) alerts on FAIL and on silence with zero
new probe code. Canonical lines (per-box transports; exit 2 = could not
verify → FAIL verdict):

```cron
# mqtt-audible box (gateway with local mosquitto json uplink):
*/10 * * * * cd /opt/meshforge && PYTHONPATH=src timeout 570 python3 -m kilo collect --transport mqtt --seconds 480 --broker localhost --port 1883 --root-topic msh/2/e --channel "+" >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh kilo_collect $?
# claw-brain box (tick file only, instant):
*/10 * * * * cd /opt/meshforge && PYTHONPATH=src timeout 60 python3 -m kilo collect --transport claw >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh kilo_collect $?
```

## Next concrete steps

- Operator: copy the example registry, anchor the real lab nodes as they
  come online (`kilo discover` after a collect window lists what's
  already audible).
- K1 design doc before code: sounding source = existing packet metadata
  (SNR/RSSI per neighbor) already decoded by the subscriber.
