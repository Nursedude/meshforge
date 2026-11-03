# MQTT-SEC — MQTT security + reliability grill (operator-led, 2026-11-03 12:34 HST)

Operator: "the larger concern with the MQTT is reliability and security" +
"really trying to get more people to use MeshForge and the MeshAnchor
channel". Decisions answered "yes to all" from the operator's phone.

## Facts measured before the grill (2026-11-03)
- Every fleet broker binds loopback only (`ss -ltn sport = :1883`: 127.0.0.1 +
  ::1 on 8 boxes; lehua, meshforge-lab run none). Fleet is NOT LAN-exposed.
- Subscriber TLS verifies certs by default (`ssl.create_default_context`,
  `mqtt_subscriber.py:330`); bypass only via explicit `tls_insecure` (WARNING).
- `broker_profiles.generate_mosquitto_conf` ALWAYS writes `bind_address 0.0.0.0`;
  `create_custom_profile` with a blank username -> `allow_anonymous true`
  (measured: anonymous on all interfaces). Product risk for NEW users.
- No dedicated silent-subscriber probe found (grep; BELIEVED, not proven).
- moc3 bridges `mqtt.meshtastic.org:1883` with `topic msh/US/# both 0`.
- `meshtastic_protobuf_client` skips cert checks; every caller found uses its
  default `localhost` (low priority, out of this grill).
- The fleet `meshforge` channel PSK was public in docs since 2026-04-24;
  operator: ACCEPT the exposure, do not rotate (adoption is the goal).

## Decisions
| # | Decision |
|---|---|
| S1 | Generated broker configs bind 127.0.0.1 by default; LAN only when the operator says a WiFi radio needs it; anonymous on a LAN listener is REFUSED. |
| S2 | Password set + TLS off + non-loopback broker -> Save ASKS ("sends your password unencrypted to host:port"), default No; not a refusal. |
| S3 | moc3 public bridge -> inbound only — after a read-only check that nothing depends on its outbound leg; box change needs the operator's go. |
| S4 | `meshforge` = a PUBLIC community channel; "Join the MeshForge channel" how-to (DOC-1) — only after an audit of what RF->RNS traffic can trigger. |
| S5 | Silent subscriber: observe before alarm — "last message N min ago" in MQTT status + a watchdog counter; alarm only after a soak baseline. |
| S6 | Order: S1+S2 red-first AFTER F8 v2 is deployed + drilled (same save boundary); S4's audit in parallel. |
