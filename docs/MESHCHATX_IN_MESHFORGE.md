# MeshChatX in the MeshForge Domain

How to run **MeshChatX** — a browser-based LXMF chat client — as a first-class
participant in a MeshForge fleet: reliable messaging (propagation node),
visibility into the Meshtastic mesh (gateway enrollment), and bot round-trips.

> MeshChatX is a third-party app (RNS-Things / Quad4-Software). MeshForge does
> not vendor its code — it integrates it as an **opt-in sibling to NomadNet**.
> For the bare install mechanics see [MESHCHATX_DEPLOYMENT.md](MESHCHATX_DEPLOYMENT.md);
> this doc is the *domain integration* — making it behave like NomadNet does.

## TL;DR — the four rungs

| Rung | What it gives you | Section |
|------|-------------------|---------|
| 1. Install (isolated) | A working web LXMF client | [§1](#1-install-isolated) |
| 2. Propagation node | Messages survive offline peers (store-and-forward) | [§2](#2-reliable-delivery--a-propagation-node) |
| 3. Gateway enrollment | See + send Meshtastic traffic as LXMF | [§3](#3-see-the-mesh--gateway-enrollment) |
| 4. Bot round-trips | Command the mesh bot and get the reply back | [§4](#4-talking-to-the-bot) |

NomadNet keeps working alongside MeshChatX throughout — two LXMF identities, one
shared rnsd.

---

## Why "isolated"? (read this first)

A current MeshChatX release requires a **newer RNS/LXMF** than the fleet's
pinned, hard-forked substrate (`rns 1.2.5+mf.N` / `lxmf 0.9.4+mf.0` — see
`persistent_issues.md`). Running MeshChatX's newer RNS against the shared rnsd
would mix versions on the substrate the whole fleet depends on.

So MeshChatX runs **isolated**: its own venv (its own RNS) with its **own private
Reticulum config** (`share_instance = No`) that peers with the same LAN transport
hub the fleet rnsd uses — an independent RNS node, *not* a shared-instance client.
It reaches the same mesh; it just never touches the pinned substrate.

---

## 1. Install (isolated)

```bash
# Build in an isolated pipx venv from source (no PyPI wheel published):
pipx install "git+https://github.com/Quad4-Software/MeshChatX.git@<tag>"

# Fetch + unpack the frontend assets (the source build ships UI-less):
#   the meshchatx-frontend.zip release asset -> point --public-dir at its root.
```

Create a **private Reticulum config** at `~/.local/share/meshchatx-rns/config`:

```ini
[reticulum]
  enable_transport = No
  share_instance = No          # <-- the isolation: own stack, NOT the shared rnsd
  shared_instance_port = 37428
  instance_control_port = 37429

[interfaces]
  [[Transport Hub]]
    type = TCPClientInterface
    interface_enabled = True
    target_host = <your-rns-transport-hub-ip>
    target_port = <hub-port>
```

Run it (a `systemd --user` unit named e.g. `meshchatx-isolated.service`):

```bash
meshchatx --headless --no-https --host 127.0.0.1 --port 8000 \
  --reticulum-config-dir ~/.local/share/meshchatx-rns \
  --storage-dir ~/.local/share/meshchatx \
  --public-dir ~/.local/share/meshchatx-public
```

Open `http://127.0.0.1:8000/` on the box's display (or via `ssh -L 8000:...`).

**Enable auto-announce immediately** (the UI, or `PATCH /api/v1/config`):
`auto_announce_enabled = true`, `auto_announce_interval_seconds = 1800`. A
delivery node that doesn't re-announce goes *stale* — peers' path/identity caches
for it expire (~hours) and **inbound messages start failing silently.** This is
the single most common "why did messages stop arriving" cause.

---

## 2. Reliable delivery — a propagation node

Without an LXMF **propagation node**, delivery is direct-only: both ends must be
online at the same instant, so anything to an offline/flaky peer fails. Stand one
up on an always-on box (a gateway is fine) using LXMF's own daemon, `lxmd`:

```bash
# ~/.config/<app>/lxmd/config
[propagation]
  enable_node = yes
  announce_at_start = yes
  autopeer = yes
  message_storage_limit = 500   # MB
```

Run `lxmd -p --config <lxmd-dir> --rnsconfig <shared-rnsd-client-config>` as a
service. It announces `lxmf.propagation`; clients discover and use it.

Point MeshChatX at it: set `lxmf_preferred_propagation_node_destination_hash` =
`<propagation-node-hash>` and `auto_send_failed_messages_to_propagation_node =
true`. Now a send that can't go direct is held by the node and delivered when the
recipient resyncs. (Point your fleet NomadNet boxes at the same node — NomadNet
only auto-selects *trusted* nodes, so set it explicitly in
`~/.nomadnetwork/storage/peersettings`.)

---

## 3. See the mesh — gateway enrollment

MeshChatX has its own LXMF identity; it does **not** inherit NomadNet's gateway
wiring. To make Meshtastic traffic flow to/from it, enroll it like NomadNet:

- **RX (mesh → you):** add MeshChatX's LXMF hash to **one** gateway's
  `rns.default_lxmf_destination` in that box's `gateway.json`, then restart
  `meshforge-gateway`. The gateway fans channel traffic to you as LXMF DMs from
  its main identity.
- **TX (you → mesh):** message the gateway's main LXMF identity
  (`<gateway-lxmf-hash>`). Broadcasts to the channel; prefix `@!<nodeid>` or
  `@<shortname>` to direct it (Issue #39). Already content-dedup-covered.

> **Deliberately pick ONE gateway.** On an active-active fleet (e.g. two gateways
> bridging the same channel) the two gateways do **not** cross-dedup, so enrolling
> in both delivers every message twice under two different sender hashes. Also
> **do not** *additionally* subscribe to the broadcast-bridge — that second
> fan-out (different identity) doesn't cross-dedup with `default_lxmf_destination`
> either. One RX mechanism, one gateway.
>
> **MeshCore** is a *separate* MeshAnchor subscriber list (different identity).
> Enrolling there too duplicates any message that crosses Meshtastic↔MeshCore —
> defer it until the fleet has one content-identity across bridges.

---

## 4. Talking to the bot

The mesh command bot (e.g. a `meshing-around` instance) responds to commands on
the channel. For its **reply to come back into MeshChatX**, two things matter:

1. **Address the bot directly.** Send `@<bot-shortname> <command>` (or
   `@!<botnodeid> <command>`), *not* a bare broadcast command. A broadcast has no
   per-client identity for the bot to reply to, so the reply can't find you. A
   directed command opens a session/correlation that routes the reply home.
2. **Gateway flags on** (`reply_routing_enabled`, `sessions_enabled` — the
   default on a real gateway). The bot replies as a DM to the gateway, which then
   routes it to you via the live session, **or** — if the session expired or the
   gateway restarted — via the durable correlation store (the
   `session_routed_m2r_durable` path). That durability is what makes the
   round-trip survive a bridge restart.

So: **`@bot ping` → `🏓 PONG` back in MeshChatX.** A bare broadcast `ping`
executes on the mesh but the reply won't thread back to you — that's expected, not
a fault.

> *Known limitation:* bare-broadcast bot round-trips (no `@addr`) are not yet
> correlated back to a specific RNS client — that's the cross-protocol
> identity-correlation work tracked in the reliability/dedup arc. Direct
> addressing is the supported path today.

---

## Coexistence with NomadNet

Both run on the same shared rnsd, each with its own LXMF identity. Share **both**
hashes with a peer if you want them to reach you on either client. NomadNet stays
the low-bandwidth, scriptable, terminal-native client (and the fleet's reliability
test client); MeshChatX is the friendly browser UX with file transfer and group
chat. Neither replaces the other.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Inbound messages stopped | MeshChatX went stale | Enable auto-announce (§1) |
| Messages to offline peers vanish | No propagation node | §2 |
| No Meshtastic traffic appears | Not gateway-enrolled | §3 |
| Same message arrives twice | Enrolled in two gateways / both RX paths | §3 — pick one |
| Bot command runs but no reply | Bare broadcast, not directed | Address the bot: `@bot cmd` (§4) |
| `AuthenticationError` in logs | rpc_key not pinned | `rns_alignment.py normalize` |
