# OTA PSK Rotation & Remote Admin — Research Plan

> **Date**: 2026-06-04 · **Status**: lab findings + plan; NO code changes shipped yet
> **Trigger**: the 06-04 ch2 PSK rotation (compromise-driven — key was published in a
> test vector) required ~16 hands-on touchpoints and produced at least two silent
> casualties discovered only by their symptoms (.32 mesh-client feed dark; moc2 radio
> never re-keyed + MQTT unconfigured, masked by a dead antenna). Goal: make the next
> rotation an OTA-distributable, verifiable operation.

---

## 1. The 06-04 rotation as a case study

One 32-byte channel PSK change touched:

| Consumer class | Count | How updated | Casualties |
|---|---|---|---|
| Fleet radios | 5 | operator QR/CLI | — |
| Non-fleet radios w/ meshforge ch | 9 | operator, hands-on | all 9 were late finds |
| Gateway `downlink_psk` (gateway.json) | per gateway box | coordinated commit | — |
| .32 meshing-around client ini | 1 | missed → fixed 06-04 PM | feed silently dark |
| moc2 radio ch2 + mqtt module | 1 | missed → fixed 06-04 PM | invisible (antenna also dead) |

**Lesson**: a PSK rotation is a *distribution problem* with an unverifiable blast
radius unless consumers are enumerated first. Silence is the dominant failure
signature (decrypt failures are DEBUG-level or invisible).

## 2. Lab: PKC remote-admin flow, moc (controller) → moc2 (target)

Setup performed: moc2 `security.admin_key` ← moc's pubkey (operator-authorized);
both nodes fw 2.7.24, `hasPKC: true`.

### Empirically hit failure classes (each one cost a real debugging cycle)

1. **Controller nodedb eviction** — PKC admin requires the controller to hold the
   target's pubkey. moc's 200-cap nodedb churns even actively-heard nodes within
   minutes → `PKI_SEND_FAIL_PUBLIC_KEY` / `refusing to send legacy DM` (correct,
   secure firmware behavior — no downgrade).
   *Cure proven live*: downlink-inject a NodeInfo **carrying `User.public_key`**
   (stock `build_nodeinfo_envelope` in `mqtt_downlink_inject.py` omits the field —
   upstream-able gap), then `--set-favorite-node` to pin against eviction.
   meshtasticd accepted the injected key: `Adding node to database`, pubkey verified
   present and correct in nodedb.
2. **RF islands / asymmetric links** — an implicit ACK proves a *relay* heard you,
   not the target. moc/moc2 RF neighborhoods were fully disjoint until moc2's
   antenna was fixed. Verify both legs with journal `from=0x…` evidence, never ACKs.
3. **TCP API slot contention** — meshtasticd (PORTDUINO) serves ONE TCP API client;
   each new connection logs `Force close previous TCP connection`. moc's
   meshforge-map collector (300s cycle) evicts any long-held admin CLI session →
   `BrokenPipeError` mid-wait. Same behavior reported upstream
   ([firmware #10101](https://github.com/meshtastic/firmware/issues/10101)).
   *Mitigations*: pause the collector (operator call), or fit the admin session into
   the inter-poll gap.

### Proven vs unproven (as of writing)

| Layer | Status |
|---|---|
| admin_key provisioning + survival across config edits | ✅ proven |
| Controller learns target pubkey via pubkey-bearing NodeInfo injection | ✅ proven |
| Favorite-pin against nodedb churn | ✅ applied |
| PKC admin DM encrypt + TX (no legacy downgrade) | ✅ proven |
| RF delivery → remote apply → response round-trip | ⏳ orchestrator retrying in poll gaps |
| Remote channel add/delete (the rotation primitive) | ⏳ same orchestrator, gated on read |

## 3. Upstream landscape — why "OTA is buggy" is earned

- Only the **first of three admin keys** honored on some 2.5 firmware
  ([firmware #5309](https://github.com/meshtastic/firmware/issues/5309)) — don't
  design assuming all 3 slots work on every fleet version; test per-version.
- 2.5-era PKI admin "NO_RESPONSE" interop failures
  ([firmware #4708](https://github.com/meshtastic/firmware/issues/4708)).
- RP2040 crashes during remote-admin decrypt (unaligned extraNonce,
  [firmware #4855](https://github.com/meshtastic/firmware/issues/4855)) — hardware
  class matters; our registry must record hw model.
- Admin **session passkey**: 8-byte key returned by the target, required for writes,
  ~300s validity ([encryption docs](https://meshtastic.org/docs/development/reference/encryption-technical/)).
  Consequence: every remote WRITE is implicitly ≥2 RF round-trips (acquire passkey,
  then write). On marginal links the passkey can expire between legs. Client bugs
  here too ([Meshtastic-Android #1262](https://github.com/meshtastic/Meshtastic-Android/issues/1262)).
- Legacy admin channel is deprecated and 2.5+ nodes can't be managed by it
  ([remote-admin docs](https://meshtastic.org/docs/configuration/remote-admin/)) —
  PKC admin is the only forward path.

## 4. Architecture for the next rotation

1. **Channel-consumer registry** (prereq for everything): every radio + software
   consumer of each managed PSK — node id, owner, hw model, fw version,
   admin-key-provisioned?, last-heard, RF-reachable-from. Candidate: tracked YAML
   beside `fleet_roles.yaml`; software consumers already enumerated in the memory
   checklist (this doc's repo-side sibling).
2. **Pre-provision admin keys in calm times** — cannot be added remotely; every
   hands-on touch of any radio should include it (zero marginal cost). For other
   people's radios: a consent conversation, not a config step.
3. **Two-phase overlap rotation** — OTA-add new PSK as a second slot → verify
   per-node on the new channel → OTA-remove old slot. No flag day; stragglers stay
   reachable. (Channel slots are a finite resource: max 8.)
4. **Compromise vs hygiene distinction** — compromise rotations must never
   distribute the new key over the burned channel; PKC admin DMs or hands-on only.
5. **Verification canary** — post-rotation probe: canary on new channel, diff
   heard-set vs registry, alert on missing. Watchdog-probe-shaped
   (`signal_class` → mini, per the established pattern).
6. **Controller hygiene** — controller radio needs: favorite-pinned targets (nodedb
   churn immunity), a quiet TCP API (or scheduled collector pause), verified
   symmetric RF (or accept relayed multi-hop with longer timeouts).

## 5. Next experiments (ranked)

1. Finish the moc→moc2 round-trip + remote ch-add/ch-del (orchestrator in flight).
2. Re-run the full flow **through a relay** (multi-hop) — rotation targets won't all
   be 1 hop. Measure session-passkey expiry risk vs hop latency.
3. Test all-3 admin-key slots on 2.7.24 (upstream #5309 says don't trust >1).
4. Adversarial: attempt admin from a NON-provisioned key — verify clean refusal
   (the security property the whole design leans on).
5. Prototype the consumer registry + a `rotation_audit.py` that hash-compares a
   channel's PSK across every registry consumer (the manual hash-compare from
   06-04, automated).

## 6. Code-change candidates (deliberately NOT shipped today)

- `build_nodeinfo_envelope(..., public_key=None)` — optional pubkey in the injected
  User proto. Small, additive, test-covered; unlocks OTA-admin bootstrap on
  nodedb-churned controllers. **The only near-term code change this plan needs.**
- Everything else above is config, registry data, ops procedure, or a future probe.

---
*Lab transcript artifacts: orchestrator log `/tmp/ota_admin_test.log` (VolcanoAI →
moc), session memory `project_ch2_psk_consumer_checklist.md`.*
