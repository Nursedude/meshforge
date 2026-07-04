"""Kilo — the lab as one honest instrument (kilo: Hawaiian, the observer).

The 20-node lab swarm (ESP32 / nRF52 / LoRa dev boards + environment
sensors) treated as a single calibrated instrument on top of the NOC:

  K0  node registry (identity, role, expected cadence) + telemetry ingest
      spine (SQLite readings time-series) + tri-state presence status —
      THIS package. Silence per registered node becomes a signal.
  K1  link-matrix observatory — every packet is a channel sounding.
  K2  sensor trust ledger — cross-calibration; per-sensor held/broke record.
  K3  Meshtastic vs RNS controlled A/B — same air, measured envelopes.

Guardrails baked in from day one (see .claude/plans/kilo_lab_instrument.md):
  * LoRa airtime is the measured SUBJECT, never bulk transport — ingest
    rides the existing MQTT json pipeline (zero new RF traffic).
  * Node identity = registry anchors (radio ids), never IP addresses
    (the fleet's DHCP-reshuffle lesson); IP-shaped anchors are refused.
  * The readings DB carries a DBSpec (MF013) and prunes itself.
  * Status is tri-state: OK / DARK / UNKNOWN — unobservable is never
    reported healthy (honest_failure_modes #2).
"""
