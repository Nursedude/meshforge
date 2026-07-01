# MeshForge Fleet Architecture Map — the hub, its legs, and how to reproduce it

> **Purpose**: one picture of how a MeshForge box is shaped, expressed so it can
> be **reproduced via the TUI with variations** (the Session-C feature). Pairs a
> human map (this file) with a machine-readable catalog (`docs/fleet_presets.yaml`).
> Live per-box snapshot detail lives in `.claude/research/fleet_architecture_2026_06_03.md`;
> this file is the *reproducible model*.
>
> Created 2026-06-24 (Session B of the gateway-dedup/reliability + fleet-map arc).

---

## 1. The model: meshforge is the hub; protocols are legs

```
                 ┌─────────────── meshforge (the hub) ───────────────┐
   Meshtastic ──▶│  PhoneAPI :4403 · MQTT bridge · mesh_bridge        │──▶ RNS / LXMF
   (ST, MQTT)    │  canonical_message · dedup(×5) · retry queue       │   (LF)
   MeshCore  ──▶ │  circuit breakers · bounded_rpc · delivery_counters│──▶ MeshCore
   AREDN src ──▶ │  watchdog probes                                   │──▶ (broadcast/DM)
                 └───────────────────────────────────────────────────┘
```

Every gateway box is the same hub code. What differs box-to-box is **two axes**:

> **A box's identity = (fleet ROLE) × (gateway.json bridge LEGS)**

- **Role** — *which systemd units run.* Source of truth: `docs/fleet_roles.yaml`
  (7 roles). Applied by `scripts/provision_role.py` (diff/enable/disable/mask vs the
  live systemd state via the `service_check` SSOT). A role is the difference between
  "this box bridges" (`meshforge-gateway: enabled`) and "this box only collects"
  (`meshforge-gateway: disabled`).
- **Legs** — *which protocols the bridge daemon wires.* Source of truth:
  `gateway.json` (rendered by `scripts/configure_gateway.sh` from
  `templates/gateway/gateway.json.template`). Each leg is an independently
  toggleable block: `rns_bridge_enabled` (RNS/LXMF), `mqtt_bridge.json_enabled`
  (Meshtastic MQTT ingest), `mesh_bridge.enabled` (dual-radio Meshtastic↔Meshtastic),
  `meshcore.enabled`, `rns_transport.enabled`. `bridge_mode` selects the primary mode.

AREDN is a **data-source** overlay (map/telemetry ingest via `map_settings.json`
`aredn_node_ips`), not a gateway bridge leg.

---

## 2. The roles (from `fleet_roles.yaml`)

| Role | Singleton | `meshforge-gateway` | What it is |
|---|---|---|---|
| `primary` | yes | absent | the hub/manager: federation, canonical memory, map, mini — **no RF bridge** |
| `full-gateway` | no | **enabled** | the real Meshtastic↔RNS/LXMF bridge + map |
| `gateway-only` | no | **enabled** | bridge only, **map disabled** (fits a ~1GB Pi3B) |
| `cloud-publisher` | yes | disabled | publishes the cloud snapshot; bridge off |
| `collector` | no | disabled | RF-sparse site: telemetry + map/tiles, **no bridge** |
| `meshanchor-noc` | — | (external) | sister app (`provisioned_by: meshanchor`) — provisioner skips |
| `bot` | — | (external) | `meshing-around` autoresponder — provisioner skips |

Invariants the catalog honors: **one rnsd per box** (mask rivals), **one canonical
writer** (`primary`), **one cloud publisher**. Per-box `service_overrides` (with a
mandatory `reason`) capture true one-offs — reason over **base role + overrides +
effective gateway config**, never raw role names (the moc2 collector lesson, §7-B).

---

## 3. The live fleet (current snapshot — instances, not the model — audited 2026-07-01)

| Box | HW | Radio(s) | Role / gateway | Notes |
|---|---|---|---|---|
| VolcanoAI | Pi | — | `primary` (manager) | hub: federation, canonical memory, map, mini — no RF bridge |
| moc | Pi 4B | HAT (LF) + CP210x USB (ST) | `full-gateway` **enabled** | dual-radio cross-preset; **TRUE-ORIGIN CANARY LIVE** since 06-30 (inject=downlink, flag on) |
| moc1 | **Pi 5B** | dudeclaw (ESP32) USB | dudeclaw-dev-host + collector; gw **disabled** | beefiest box; **designated FUTURE GATEWAY** (recognition flag pre-set 07-01) |
| moc2 | Pi 4B | HAT + RAK4631 USB + POE | collector; gw **disabled** | RF-equipped; **being PULLED for Axiometa Genesis work** (role in flux) — NOT the "RF-sparse" the old map claimed |
| moc3 | Pi 3B (1GB) | HAT + CP210x USB | `gateway-only` **enabled** (map disabled) | recognition (flag on, no downlink) — the canary's peer |
| moc5 | Pi 4B **Ubuntu** | MeshToad | `collector` + AREDN; no gateway | AREDN-site collector; only non-Debian box |
| bench | OpenWrt | USB meshtoad + meshtasticd | (not deployed) | future gateway candidate |
| meshanchor-server | — | — | external (sister NOC) | RNS↔MQTT |
| .32 bot | — | — | external | autoresponder→AREDN |

> **Transport-truth invariant (2026-07-01):** any active gateway on a segment where
> a peer delivers true-origin (untagged) content MUST have `rns.true_origin_downlink_enabled`
> on, or it re-bridges the untagged echo (Option A cross-box recognition). Now a
> **default in `gateway.json.template`**, so every provisioned gateway inherits it;
> true-origin *delivery* stays opt-in via `meshtastic.injection_mode=downlink` + a
> channel PSK. Live: moc (delivery) + moc3 (recognition) since 06-30; moc1 pre-set as
> the future gateway. Ref for moc2's repurpose: Axiometa Genesis mini starter kit
> (https://hackmakemod.com/products/axiometa-genesis-mini-starter-kit).

---

## 4. The lab-hardened variation catalog

The curated, proof-backed presets a user picks from. **Machine-readable SSOT:
`docs/fleet_presets.yaml`** (validated by `tests/test_fleet_presets.py` — every
preset references a real role + real legs). Summary:

| Preset | Shorthand | Role | Bridge legs | Board | Maturity |
|---|---|---|---|---|---|
| `full-bridge` | ST↔meshforge↔LF | full-gateway | meshtastic_mqtt, rns_lxmf | Pi4 4GB | field-proven |
| `gateway-only-slim` | ST↔meshforge↔LF | gateway-only | meshtastic_mqtt, rns_lxmf | Pi3B 1GB | field-proven |
| `dual-radio-cross-preset` | ST↔meshforge↔ST | full-gateway | + mesh_bridge | Pi4 | field-proven |
| `meshcore-bridge` | ST↔meshforge↔MeshCore | full-gateway | meshtastic_mqtt, meshcore | Pi4 | alpha |
| `monitor-ingest` | ST→meshforge | collector | — (bridge off) | Pi4 | field-proven |
| `aredn-site` | AREDN↔meshforge | collector + AREDN src | — | Pi4 | field-proven |
| `hub-manager` | — | primary (singleton) | — | Pi4/5 | field-proven |
| `cloud-publisher` | — | cloud-publisher (singleton) | — | Pi4/5 | field-proven |

**Operator shorthand → catalog:**
- `ST <> meshforge` → `monitor-ingest` (single-sided ingest, no bridge)
- `LF <> meshforge <> ?` → `full-bridge` / `gateway-only-slim` / `meshcore-bridge`
  (the `?` picks the second leg)
- `? <> meshforge <> ?` → any bridge preset (two-sided)

---

## 5. How to reproduce a box (today, manual)

1. `sudo bash scripts/install_noc.sh` — install the stack (no role set).
2. `python3 scripts/provision_role.py --set-role <role>` — write `{"role": …}` to
   `~/.config/meshforge/deployment.json` (merges; preserves `profile`/overrides).
3. `sudo python3 scripts/provision_role.py --apply` — converge units to the role
   (the permission foundation is applied on every converge).
4. Bridge boxes: `sudo scripts/configure_gateway.sh` + `install_gateway_service.sh`
   — render `gateway.json` (set the preset's legs) and install `meshforge-gateway`.
5. `scripts/rns_alignment.py normalize` if the RNS tree drifted; add the box to
   `~/.config/meshforge/fleet_hosts`.

## 6. How the TUI will reproduce it (Session C — dry-run first)

A new `handlers/fleet_provision.py` (BaseHandler) that **wraps the above**, no new
convergence logic:
1. Read `docs/fleet_presets.yaml` + `fleet_roles.yaml` (via `provision_role.load_roles`/
   `resolve_role`). Show the current box's effective role + legs + drift.
2. User picks a lab-hardened preset → render the **dry-run** diff
   (`provision_role.plan()` + `configure_gateway.sh DRY_RUN=1`). No apply in v1.
3. Apply lands as a guarded follow-on (Session D), with the deployment.json
   key-merge already fixed (`save_profile` 2026-06-24).

> ⚠️ `deployment.json` is shared by the profile and role systems. Always MERGE
> (never overwrite) — `provision_role.write_role()` and (since 2026-06-24)
> `save_profile()` both do.

---

## References
- `docs/fleet_roles.yaml` — role SSOT · `docs/fleet_presets.yaml` — preset catalog
- `scripts/provision_role.py` — convergence engine · `scripts/configure_gateway.sh` — leg config
- `.claude/research/fleet_architecture_2026_06_03.md` — live per-box snapshot + §7-B drift lesson
- `tests/test_fleet_presets.py` — catalog integrity guard
