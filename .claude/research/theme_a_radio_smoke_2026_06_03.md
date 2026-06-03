# Theme-A Radio Smoke — Session Record (2026-06-03)

> Live operator+AI field validation of the addressability arc (steps 1-3, shipped same
> day: `2b1be20` / `2f8af9b` / `0c21ff7`). Protocol: operator drives radios + NomadNet;
> AI drives journals/wire forensics on moc. **Five real findings in ~2 hours — none of
> which 5,645 green tests could have caught.**

## Verdicts

| Leg | Result |
|---|---|
| **A — RNS→mesh reply threading (steps 1+2)** | ✅ **FIELD-PROVEN.** Bare NomadNet message auto-directed `(reply:memory) -> !dd9fb424`; contact auto-discovered ("VolcanoAI nomad"); sessions opened; relayed copies (src=moc3 `f68c…`) correctly excluded — the wx-broadcast-vs-test-directed asymmetry proved the guard live. |
| **B — mesh DM-to-gateway private reply (step 3)** | ⏸ **PARKED at the last RF inch.** Every software layer verified live (wildcard subscription delivering, foreign-channel filter observed working on real packets, session durable across restarts). Blocker: Meshtastic **DM transport** between B424 and moc routes via relay node `…24` and dies there (both `error=-7` CRC casualties today were DM packets; channel broadcasts flood around it). Close-range DM test = deterministic close-out, pending. |

## The five findings

1. **`self._identity` name collision** — step-2 IdentityBinder was stomped by the RNS
   Identity object on RNS connect; every live non-bridge-tagged M→R/R→M errored. Tests
   green because the fixture never connects RNS. **Fixed `89a0870`** (renamed
   `_identity_binder` + regression test pinning the live failure shape).
2. **Stale fan-out** — moc's `default_lxmf_destination` lacked the operator's CURRENT
   NomadNet identity (`VolcanoAI nomad` `9217147e24d1640d204dde3f413eb521`; added,
   operator-confirmed). The legacy 5 hashes all confirm delivery → live older
   identities; **audit/prune pending**. Feeds §7-B: drift organ should compare announced
   operator identities vs configured fan-out.
3. **moc meshtasticd instability** — ABRT crash (signal 6, not OOM) at 09:31 + ~29% CRC
   loss in the worst window (partly self-inflicted bot-flood congestion). Watch-item.
4. **Stale PKI keys + ghost nodedb entries** — `PKC decrypt attempted but failed!`; all
   DMs between moc and the re-flashed portable silently dropped while channel traffic
   (PSK) flowed. Also: "wh6gxz mini mesh" in moc's nodedb = **ghost** `!a2ebdd94` (the
   radio's pre-flash identity); the live device transmits as `!dd9fb424` ("Meshtastic
   B424"). DMs to the friendly name went to a dead address forever. Operator
   forget-node + re-hear fixed the keys (moc→B424 DM then delivered + ACKed).
5. **DM-to-gateway never reached the bridge (structural)** — meshtasticd publishes DMs
   under the **primary channel's** json topic (a name the config doesn't know); the
   mqtt_bridge subscription was scoped to the bridge channel (#34 shapes). A perfect DM
   arrived at the radio, decrypted, published… unseen. **Fixed `e50f3cb`**: when the
   DM-to-gateway leg is armed, subscribe the json wildcard + enforce channel scope
   per-message (foreign channels pass ONLY `to == gateway_node_id`). Observed working
   live (`Foreign-channel packet ignored (…, to=!dd9fb424)`). Dormant = legacy
   subscriptions byte-identical. 9 tests.

## State left on moc

All three Theme-A flags ON (only box); fan-out = 6 inboxes (incl. operator nomad);
sessions durable (`!dd9fb424` + `!32962f10` ↔ `9217147e`, 24h TTL); wildcard MQTT
subscription live. moc3 + fleet: everything default-off, dormant.

## NEXT SESSION (operator-set agenda, 2026-06-03 close)

> "MOC does not get messages on the meshforge channel from the bot or other nodes —
> meshanchor etc — currently gets messages from wh6gxz mini mesh — volcanoai nomadnet
> no bot output."

Investigate moc's **inbound channel visibility**: bot + MeshCore/meshanchor-sourced
traffic not arriving at moc (B424's traffic does). Note for the dig: bot output reaches
moc via RNS injection (`[RNS:a2e95ba4]` prefix) — and the **`is_already_bridged` loop
guard deliberately never re-bridges `[RNS:`-tagged content M→R**, so bot output can
never reach the LXMF fan-out (→ "volcanoai nomadnet no bot output" may be BY DESIGN of
the loop guard, needing a deliberate bot→LXMF path, not a bug-fix). Separate from that:
whether moc's RF/MQTT RX of bot/meshanchor channel traffic regressed today (meshtasticd
restarted 3×; wildcard subscription changed the topic surface; ~29% CRC window).
Also pending: leg-B close-range DM; fan-out audit (legacy 5 hashes); relay `…24`
identification/firmware; ghost `!a2ebdd94` cleanup in moc's nodedb.
