# MeshForge Fleet Manifest

> **Status:** v0 (draft, 2026-05-24) — living spec. This is the *method*, not yet
> the automation. The goal it serves: **a fleet should be reproducible by a second
> operator from this document plus the repo — no tribal knowledge required.** That
> reproducibility-by-a-stranger is the 1.0 release gate (see [Reproducibility gate](#reproducibility-gate)).

A MeshForge fleet is a set of single-board nodes that each play a **role**. This
manifest defines roles generically so anyone can duplicate the deployment. It
deliberately separates two things:

- **Role definitions** (this file, committed) — generic: board tier, services,
  config knobs, deltas. Duplicable, no operator specifics.
- **Instance assignment** (local config, never committed) — which host is which
  role, LAN IPs, RNS/LXMF hashes, coordinates. Lives in
  `~/.config/meshforge/` and `~/.config/meshanchor/` (see
  [Instance assignment](#instance-assignment)). Keeping it out of the repo is
  both a reproducibility property *and* the rule MF015 enforces.

---

## Design invariants

These hold across every node. They are the hard-won rules; violate one and the
fleet degrades in a way that's expensive to debug.

1. **One RNS host (`rnsd`) per box.** Every RNS-using daemon joins as a *client*
   via `share_instance = Yes` pointing at that box's `rnsd`. Two daemons hosting
   RNS under the same instance name collide and EOF every client. Corollary: on a
   box that runs both a MeshForge stack and the MeshAnchor daemon, the one that
   must not own the RNS listener is **masked**, not merely disabled (a disabled
   unit can still be started by a `restart` timer or dependency).
2. **One canonical memory/repo writer.** Exactly one node (the `primary` role)
   authors Claude memory and pushes; all others are read-only replicas synced via
   `scripts/fleet_sync.sh`. Authoring memory on a replica is erased on next sync.
3. **One cloud-push publisher.** The VPS snapshot push runs on exactly one node
   (a map node with good uptime), pulling from the primary's local API. More than
   one publisher races; zero means the public demo goes stale.
4. **Software is tuned to fit the box.** Constrained boards (≤1 GB) bear the load
   because of the memory-saving defaults, not because the hardware is roomy. The
   node-directory bounding-box filter, the node-count cap, and the response
   byte-caches are **on by default and must stay on** on small boards. (See the
   [Hardware section of the README](../README.md).)
5. **Fail loud, restart clean.** Every node runs the watchdog; services are
   `systemd`-supervised with `Restart=`. A wedged service should exit and be
   restarted, not hang silently.

---

## Role catalog

| Role | Board tier | meshtasticd | rnsd | mosquitto | bridge | map server | tile server | cloud-push | watchdog | Notes |
|------|-----------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|-------|
| `primary` | Pi 4 / Pi 5 (≥4 GB) | ✓ | ✓ | ✓ | — | ✓ | — | — | ✓ | Canonical memory/repo writer. Drives the cloud-push *source* API. Does not host the bridge. |
| `full-gateway` | Pi 4 (≥4 GB) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | Full NOC node: radio + bridge + map + tiles. |
| `cloud-publisher` | Pi 4 / Pi 5 (≥4 GB) | ✓ | ✓ | ✓ | **disabled** | ✓ | ✓ | ✓ | ✓ | `full-gateway` **+** the cloud-push timer, **−** the bridge (publishes snapshots, does not bridge RF). Exactly one in the fleet. |
| `gateway-only` | Pi 3B+ (~1 GB ok) | ✓ | ✓ | ✓ | ✓ | **disabled** | — | — | ✓ | Constrained board: bridge + radio, **map server off** (too heavy for ~1 GB). |
| `meshanchor-noc` | Pi 4 (≥4 GB) | — | ✓ | ✓ | — | (meshanchor map) | — | — | (meshanchor wd) | Sister app ([MeshAnchor](https://github.com/Nursedude/meshanchor)), MeshCore-primary. No MeshForge stack. Radio-less egress to a peer meshtasticd. Provisioned by the MeshAnchor repo, not the MeshForge provisioner. |
| `bot` *(optional, non-fleet)* | Zero-class (Zero 2 W rec.) | — | — | — | — | — | — | — | — | A dedicated [meshing-around](https://github.com/SpudGunMan/meshing-around) bot. Not a MeshForge node; reaches the mesh via a companion radio. |

> Machine-readable companion: **[`fleet_roles.yaml`](fleet_roles.yaml)** — the same
> catalog + deltas in structured form, intended to drive a role-aware provisioner.

Roles install via the existing profiles (`scripts/install_noc.sh`, `--profile`)
— `full` covers `primary`/`full-gateway`/`cloud-publisher`/`gateway-only` with the
deltas below; `meshanchor-noc` installs from the MeshAnchor repo.

---

## Required deltas (the judgment, written down)

These are the per-role settings that must be applied for the fleet to behave.
Historically they lived in the operator's head and Claude memory — encoding them
here is the point of the manifest.

**All MeshForge map-running roles**
- Node directory: bounding-box filter **on**, node-count cap **set** (default
  15,000), operator region/position set so the bbox auto-derives. Without these a
  busy mesh grows the on-disk DB without bound.
- Response byte-caches for `/api/nodes/directory`, `/api/nodes/geojson`,
  `/api/network/topology`: **on** (default). These are what keep a large mesh from
  wedging the single-threaded HTTP server on GIL-bound serialization.

**`gateway-only`**
- `meshforge-map.service` **disabled + inactive** (deliberate, not a fault). Don't
  run the map server on a ~1 GB board.

**`cloud-publisher`**
- `meshforge-cloud-push.timer` enabled here and **nowhere else**. It pulls a
  regional GeoJSON snapshot from the primary's API and pushes to the VPS.

**`meshanchor-noc`**
- Radio-less egress to a peer meshtasticd uses `want_ack = True` so the lossy
  LongFast hop retransmits (implicit-ACK rebroadcast). Tunable to `False` for
  airtime.
- If this box also has a MeshForge install present, ensure its
  `meshanchor-daemon` does **not** collide with MeshForge's `rnsd` (invariant #1).

**Any box where a non-`rnsd` daemon could claim the RNS listener**
- Mask the loser: `systemctl mask <unit>` (not just `disable`). A `mask` survives
  `restart` timers and dependencies; a `disable` does not.

---

## Peer wiring (pattern, not values)

- **Federation (HTTP):** each node lists its peers; the fleet rollup correlates
  them by `peer_name`. Peers that are permanently gateway-only or down get
  exponential backoff so they don't drown real failures.
- **RNS / LXMF (bridge):** gateways are wired to each other by **LXMF delivery
  destination hash** (not identity hash) in `peer_gateway_destinations` /
  `rns.default_lxmf_destination`.
- The concrete hostnames, IPs, and hashes are **instance values** — they live in
  local config, never here.

---

## Instance assignment

The operator-specific layer. **None of this is committed** (it carries LAN
topology and is per-deployment):

| What | Where |
|------|-------|
| Host list (fleet membership) | `~/.config/meshforge/fleet_hosts` |
| Per-box profile / role | `~/.config/meshforge/deployment.json` |
| Per-box watchdog overrides | `~/.config/meshforge/watchdog.json` |
| Federation peers + `this_host` | `~/.config/meshanchor/fleet.json` |
| Gateway bridge config, peer hashes, egress | `~/.config/meshanchor/gateway.json` |

To duplicate the fleet: install the role's profile on each box, apply the deltas
above, then fill in the instance config for your own hosts/hashes. `fleet_sync.sh`
then keeps code + memory aligned from the primary.

---

## Reproducibility gate

The fleet is "solid" — and MeshForge earns its **1.0** — when a second operator
can stand up an equivalent fleet **from this manifest plus the repo alone**, with
no direct help from the original author, and reach the same operational state.
Until then we are pre-1.0 by definition, regardless of how well *this* fleet runs.

**Suggested version path:** `0.6.0` when fleet mode lands as a documented option
(this manifest + role-aware install), `1.0.0` when the gate above is met *and* the
reliability bar holds (watchdog auto-remediation proven in the field, soak history,
no remaining tribal deltas).

---

## What's still tribal (v0 honesty)

The manifest is the method; it is not yet the automation. Remaining gaps to close
before the reproducibility gate is genuinely met:

- **No role-aware provisioner yet.** Deltas above are applied by hand. Next step:
  a single idempotent command that takes a role and produces a known-good node.
- **Instance config is hand-edited**, with no schema/validation. A documented
  schema (and a `--validate`) would catch drift.
- **Peer wiring is manual.** Deriving LXMF delivery hashes and populating
  `peer_gateway_destinations` is still a recipe, not a tool.
- **Watchdog auto-remediation is gated** (dry-run on one node). The reproducibility
  story is stronger once the system self-heals the characterized failure classes
  instead of relying on operator + agent intervention — *encoding judgment into the
  system.*
