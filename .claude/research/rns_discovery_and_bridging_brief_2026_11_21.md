# Research brief — RNS interface discovery (rmap), and how an RNS client reaches Meshtastic/MeshCore

> **For:** a cloud Claude session (no access to the fleet). **Written:** 2026-11-21 08:0x HST by the
> local session on the lab manager box, which CAN read the fleet. **Operator ask (11-21):** "our nodes
> are not on rmap — consider `discoverable = yes` … q: how does a rns client communicate to
> meshtastic/meshcore — via channel — maybe this is a research article for artifacts and cloud claude".
>
> **Division of labour.** The FACTS SHEET (part A) was measured here, from the pinned source and the
> live configs — treat it as ground truth for *our* side, and say so if upstream contradicts it. Your
> job is part B: the UPSTREAM / PUBLIC side we cannot see from here. The local session re-measures
> every claim you make before anything is adopted.
>
> Published view: artifact `FojjbFHFQvDQQ2BiBr5LFu` (this file is the source of truth). Dispatched
> 2026-11-21 08:2x to a remote cloud session with this brief inline.

---

## Rules of engagement (read first)

1. **Output is an ARTIFACT, never `main`.** Deliver one Markdown report (and, if useful, a claude.ai
   artifact page). No commits, no PRs, no branches pushed to this repo. The local session lands what
   survives re-measurement.
2. **Primary sources only, version-pinned.** The Reticulum/LXMF source at a named tag or commit, the
   official manual, release notes, the project's own repo. Quote file:line or section. A blog or forum
   post is a lead, not a source — label it.
3. **Tag every claim:** `SOURCE` (read in code at tag X, cite it) · `DOCUMENTED` (official docs say so,
   cite it) · `OBSERVED` (you ran it — say how) · `ASSERTED` (inference; say why). "I don't know" is a
   first-class answer; an unanswered question beats a guessed one.
4. **Pin the version you read.** Our fleet runs **RNS 1.3.9 + our patch series (`+mf.N`)** and LXMF
   1.0.x forks (`requirements/rns.txt`, `MF-FORK-PIN` lines). Where upstream behaviour differs across
   versions, say which version introduced it.
5. **Contact nobody.** No issues, comments, or messages to any upstream project or person. (Our rule
   for `markqvist/*`: findings reach upstream only in the operator's own words.)
6. **Privacy.** Don't put the operator's name, call sign, location, IPs or hostnames into any search
   query or external request. Nothing in this brief needs them.

---

## Part A — FACTS SHEET (measured locally, 2026-11-21)

### A1. RNS interface discovery, as implemented in our pinned RNS (1.3.9+mf.0)

Read from the installed source (`RNS/Reticulum.py` ~L905–990, `RNS/Discovery.py` L34–211):

- Per-interface config key `discoverable = yes`. Optional: `discovery_name`, `latitude`, `longitude`,
  `height` (or `location_cmd`, an executable printing `lat,lon,height`), `reachable_on` (host/IP, or
  an executable printing one), `announce_interval` (minutes; **min 5 min, default 6 h**),
  `discovery_stamp_value`, `discovery_encrypt`, `publish_ifac`, and `discovery_frequency` /
  `_bandwidth` / `_modulation` for KISS/Weave.
- `DISCOVERABLE_INTERFACE_TYPES` include Backbone, TCPServer, TCPClient, I2P, RNode, Weave
  (`supports_discovery = True` in those interface classes); KISS via TCPClient with kiss framing.
- The announce is an RNS announce of `rnstransport.discovery.interface`, carrying name, lat/lon/height,
  the transport flag, and per type: RNode → frequency/bandwidth/SF/CR; TCPServer → bind port +
  `reachable_on`; I2P → b32. **`publish_ifac = yes` adds the IFAC network name to it.**
- It is **proof-of-work stamped** (LXStamper; `DEFAULT_STAMP_VALUE = 14`, `WORKBLOCK_EXPAND_ROUNDS =
  20`; cached per content hash). The receiving side (`InterfaceDiscovery`) drops announces below
  `Reticulum.required_discovery_value()`.
- **⚠️ Side effect:** if a `discoverable` interface's mode is not gateway / access_point / internal,
  rnsd **switches it automatically**: an RNode becomes **access_point**, anything else becomes
  **gateway** (logged at NOTICE). So enabling discovery is not only a publication. It changes how that
  interface handles paths and announces.

### A2. Our RNS fleet, in shape (no hosts named)

- 10 boxes. `enable_transport` is on for 3 of them (the lab manager and the two gateway boxes).
- **HawaiiNet RF:** 3 boxes carry an `RNodeInterface` on **903.625 MHz, 250 kHz, SF7**. It is a
  PUBLIC community RNS network by design (published parameters, adopters welcome). Its parameters
  never change casually.
- 2 boxes run a `TCPServerInterface` (one is the regional HawaiiNet TCP entry point); most boxes are
  `TCPClientInterface` clients of it. Every box runs `AutoInterface`.
- **No interface on any box has `discoverable` set.** The operator reports our nodes are absent from
  rmap.world.
- Our map *consumes* rmap.world as an optional public fallback with an area filter
  (`enable_rmap_fallback`, `tests/test_map_collector_public.py`). That controls what we PULL, not what
  we publish.

### A3. How an RNS client reaches Meshtastic / MeshCore today (our code)

There is no "RNS channel". The gateway is an LXMF endpoint that translates:

- **RNS → Meshtastic:** an LXMF DM to the gateway's identity → `src/gateway/rns_bridge.py` →
  transmitted on a Meshtastic **channel index** resolved from the channel NAME at startup
  (`src/gateway/_channel_resolver.py`, which refuses loudly when the name isn't on the radio), via
  `send_text_direct()`. If `src/gateway/contact_mapping.py` maps the sender to a Meshtastic node id
  (persistent cross-protocol table: Meshtastic `!xxxxxxxx`, RNS 16-hex destination, MeshCore 12-hex
  pubkey prefix), it goes as a **DM** instead.
- **Meshtastic → RNS:** `src/gateway/meshtastic_broadcast_bridge.py` runs its own LXMF router and
  identity. LXMF users DM it `subscribe` / `unsubscribe` / `channels` / `help`, and channel traffic on
  an allowlist is fanned out to subscribers as LXMF DMs.
- **MeshCore:** the sister app MeshAnchor runs the mirror (`LXMFBroadcastBridge`). The shared message
  contract is `src/gateway/canonical_message.py`, byte-identical across both repos.
- **Trust boundary:** `src/gateway/rns_ingress_policy.py`. An LXMF source hash is a signed identity,
  so RNS is the one leg where a sender policy is sound (a Meshtastic node id can be forged). Postures:
  open / observe (bridged and ledgered) / enforce (refused and witnessed).
- **Design stance:** RNS is the HUB. Cross-mesh traffic routes RNS-first; pairwise radio bridges are
  N² duplicates.

### A4. Clients

- **Sideband** runs on no fleet box. The operator will test the latest release on an Android tablet.
- **MeshChatX** runs as a user unit on the MeshAnchor box, and a leftover `reticulum-meshchat`
  directory sits on the lab box. The operator is considering removing it.
- **Already decided on 2026-10-27** (artifact "After MeshChatX", `9vfKj6cjtsgP3dDYv3xdQc`): NomadNet
  is the supported LXMF client, and MeshChatX is installer-only, with end of life on 2028-01-01. It is
  also the fleet's one **stock-RNS interop witness**: an environment deliberately kept on upstream
  RNS, so it can show our fork still talks to upstream (`scripts/fork_roll_install.py:224`).
  Removing it removes that proof unless another client takes over.

---

## Part B — QUESTIONS (yours)

Answer each with tagged claims (rule 3) and sources (rule 2). Order is priority.

**B1. rmap.world — how does a node get on it?**
- Does rmap.world build its map from RNS **interface-discovery announces** (A1)? From something else
  (its own path-table collector, a submission form, nomadnet pages)? From both?
- Is its code public? Pin the repo/commit, and name the ingest path in it.
- Which fields does it display (name, coordinates and their precision, frequency/SF, reachable_on)?
  Does it require a reachable TCP entry point, or will an RF-only (RNode) interface appear?
- Is there a stamp-value or other threshold below which it ignores an announce?

**B2. Interface discovery — semantics and risk.**
- In which RNS version did discovery land, and what do the manual and release notes say? Diff the
  semantics against A1 for anything our pin lacks.
- Spell out the access_point and gateway modes (path expiry, announce rebroadcast, rate limits) that
  the auto-switch in A1 imposes. Concretely: what changes for an RNode on a shared public LoRa
  channel, and for a TCP entry point?
- What are the security and privacy trade-offs: publishing coordinates, `reachable_on`, `publish_ifac`
  (should it ever be on?), `discovery_encrypt`?
- How long does a stamp-14 PoW take on a Raspberry Pi-class CPU? A published figure or a reasoned
  estimate, labelled.
- What does an *adopter* need in order to join from a discovered RNode announce?

**B3. Bridging landscape — RNS ↔ Meshtastic / MeshCore.**
- What other public projects bridge LXMF or RNS to Meshtastic and/or MeshCore? For each: how they map
  channel ↔ identity, DM vs broadcast, sender trust, acks and delivery truth, and loop/duplicate
  prevention.
- Compare each with A3, neutrally. Where is our design weaker (be specific)? Where do they solve
  something we haven't (per-leg delivery confirmation, identity binding)?
- Does Meshtastic or MeshCore upstream document anything for gateways that we ignore (rate limits,
  channel etiquette, "ok-to-MQTT"-style consent flags)?

**B4. Sideband — current release.**
- What is the current version, and which RNS/LXMF versions does it bundle? Is it wire-compatible with
  RNS 1.3.x / LXMF 1.0.x? Name any announce or packet format change since then.
- How does Android connect to an existing network: AutoInterface on the same LAN, a TCP client to an
  entry point, an RNode over Bluetooth or USB? List known Android pitfalls (battery optimisation
  killing the service, multicast on wifi).

**B5. Which client becomes the stock-RNS interop witness?** Don't redo the MeshChatX study; A4
has its outcome.
- Which client tracks upstream RNS/LXMF closely enough to replace MeshChatX: Sideband, Columba, or
  NomadNet installed against stock RNS? Give each one's cadence of upstream bumps, pinned to tags.
- Can it run headless on a Raspberry Pi, attached to a shared rnsd or with its own instance, so the
  check can be automated? If not, what is the minimum manual check?
- Has anything changed since 2026-10-27 (a MeshChatX v5 release, or a change to its maintenance or
  end-of-life plan)?

---

## Deliverable

`rns_discovery_and_bridging_report_<date>.md` (an artifact; see rule 1):

1. A one-paragraph verdict per question.
2. Tagged findings with citations.
3. **"What we should do"**: a ranked list of recommendations. Each has the measurement that would
   confirm it locally, and which part of A it depends on.
4. **"Contradictions with Part A"**: anywhere upstream disagrees with our facts sheet.
5. Open questions you could not answer, and why.

Keep it skimmable: verdict first, one fact per line, story last.

---

## What the local session does with it

Re-measure every SOURCE/DOCUMENTED claim against the pinned source or the live box, then adopt it,
refute it in `.claude/audits/review_provenance.md`, or queue it. No fleet change follows without the
operator's words: rmap discoverability needs a one-box pilot plus the operator's choice of name and
coordinate precision. MeshChatX removal needs an explicit go.
