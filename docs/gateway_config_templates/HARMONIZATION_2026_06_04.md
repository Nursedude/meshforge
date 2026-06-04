# Gateway Config Harmonization Runbook — 2026-06-04

Drift items from the 2026-06-04 read-only fleet sweep. **Dated and disposable**
— delete (or mark done) after execution. Box-name specifics + hash values live
in the fleet architecture research doc §7.7
(`.claude/research/fleet_architecture_2026_06_03.md`); this runbook uses role
names so it stays repo-portable.

> **Ground rules**
> - The active full-gateway canary box is mid-soak (downlink injection) — **do
>   not restart its gateway** until the soak verdict is in. Items touching it
>   are marked ⏳ post-soak.
> - Other gateway edits take effect via `sudo systemctl restart
>   meshforge-gateway` on that box only.
> - After each change: `journalctl -u meshforge-gateway -n 20` for a clean
>   start, then check the bridge counters in `/api/status` (where the box
>   serves one).

## 1. Hot-spare `peer_gateway_destinations` misaligned with the active gateway

- **What**: the hot spare's peer list differs from the active full-gateway's at
  position 1 (see arch doc §7.7 for hashes). If the spare were promoted, peer
  relay would target the wrong cluster member.
- **Change**: on the **gateway-only hot spare**, set
  `rns.peer_gateway_destinations` to the same set the active full-gateway uses
  (each gateway lists *the other* gateways, so the lists are mirrors — verify
  which hash is whose `lxmf.delivery` before copying blindly; see
  `reference_fleet_gateway_hashes` memory / each gateway's startup log).
- **Restart**: hot spare gateway only. Safe now (not the soaking box).
- **Open decision**: none — alignment is unambiguous once hash ownership is
  confirmed.

## 2. LXMF destination sets inconsistent across boxes

- **What**: the two bridging boxes carry 4-entry `default_lxmf_destination`
  lists that differ at position 3; the collector and cloud-publisher carry
  different singles.
- **Change**: decide the canonical fan-out list **per role** (bridging boxes
  probably share one list; non-bridging boxes' values are inert while their
  bridge is off). Record the decision in the arch doc §7.7 table.
- **Restart**: each edited gateway box (hot spare safe now; active gateway
  ⏳ post-soak if its list changes).
- **Open decision**: is the position-3 difference intentional (per-box operator
  inbox) or drift? Operator call.

## 3. `rns_bridge_enabled` explicit-vs-absent schema shape

- **What**: explicit `true` on the two gateway boxes; key absent on
  collector/cloud-publisher (default `true` applies — benign, but raw-JSON
  diffs across boxes lie).
- **Change**: add explicit `rns_bridge_enabled: true` to the two boxes where
  it's absent (matches the 2026-06-03 normalization precedent on the hot
  spare). Hygiene only — no behavior change, **no restart needed** (takes
  effect at next natural restart).
- **Open decision**: none.

## 4. Theme-A flags only on the canary box

- **What**: `reply_routing_enabled` / `cross_protocol_identity_enabled` /
  `sessions_enabled` are ON only on the active full-gateway (deliberate
  observe-first rollout).
- **Change**: **decision, not drift** — widen to the hot spare after Theme-A
  field validation passes, or hold. Until decided, add the three keys
  explicitly as `false` on the hot spare (self-documenting, matches the
  full-gateway template).
- **Restart**: hot spare only (if keys added).
- **Open decision**: widen-vs-hold — gate on the Theme-A field validation
  handoff, not on this runbook.

## 5. ⏳ `downlink_psk` plaintext in gateway.json backups (canary box)

- **What**: ~12 `gateway.json.bak*` files on the canary box carry the channel
  PSK in plaintext. Once `downlink_psk` is set, gateway.json **and its
  backups** are secret-bearing.
- **Change (post-soak, in order)**:
  1. Prune stale backups (keep the canary-revert backup until the soak
     verdict graduates the canary, then prune it too).
  2. Rotate the channel PSK on the radio + update `downlink_psk` +
     `meshtasticd` channel config together (coordinated — all channel members
     need the new PSK).
  3. Going forward: `chmod 600` gateway.json + backups on boxes where the PSK
     is set; never copy them off-box unencrypted.
- **Restart**: canary gateway (post-soak by definition).
- **Open decision**: rotation timing — PSK was also embedded in a transient
  /tmp probe and read at runtime; exposure is limited to on-box files, so
  rotation is hygiene, not incident response.

## 6. Manager box `broker_profiles.json` dormant plaintext MQTT creds

- **What**: dormant broker profiles include a private-profile password and the
  well-known public meshtastic default pair. Neither profile is active.
- **Change**: remove the private password from the file (re-enter at
  activation time), or move profiles to a non-committed secret store. The
  public default pair is not a secret (it's published upstream) — keep or
  drop for clarity.
- **Restart**: none (profiles dormant).
- **Open decision**: none — low priority.

## Also queued from this pass (not drift)

- `examples/configs/gateway-mqtt.json` is schema-broken vs the current loader
  — delete `examples/configs/gateway-*.json` once nothing references them
  (superseded by `docs/gateway_config_templates/`).
- `templates/gateway/gateway.json.template` still renders `http_port: 443`
  (loader migrates it, but the literal should become `9443`) — one-line fix,
  separate commit.

---

*Execute top-to-bottom; items 1–4 are safe before the soak verdict (they avoid
the canary box), 5 is post-soak, 6 is whenever.*
