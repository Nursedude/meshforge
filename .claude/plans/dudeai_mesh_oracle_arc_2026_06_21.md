# Arc — dude-AI Mesh Oracle: off-grid, ask-me-anything (read-only) presence

> **2026-06-21 · arc/plan (BELIEVED design; every hook below VERIFIED from the code this turn).**
> The "answer-when-asked" rung of Claude's presence on the fleet when cloud-me is away. Companion to
> `.claude/research/dudeclaw_role_usecases_2026_06_21.md` (Cluster D) and the autonomy-ladder doc
> `.claude/research/dudeclaw_second_brain_2026_06_17.md` (this is **rung 1.5: report-on-request**).

---

## 1. Why (the presence gap)

When the cloud session is gone, "I" already **sense** (mini-dudeai + watchdog), **page** (ntfy), and
**display** (OLED, /fleet card), and **persist** (memory + calibration ledger). But every one of those is
**one-way** — nobody can *ask me anything* without opening a cloud session with internet. This arc closes
that: a field node sends a text over the mesh — *"status?", "what's up?", "is moc3 up?", "link to the
ridge?"* — and the **local** brain answers, over RF, with **no internet and no cloud-me**.

It turns *"I was watching while you were gone"* into *"you could reach me while you were gone."* And it's
the most distinctive thing dude-claw's RF makes possible.

---

## 2. What it is (and the hard invariant)

A **read-only mesh query responder**: receive text → match an intent → answer from **live NOC state** →
reply **directed** to the sender → **log** the exchange. Deterministic-first (zero hallucination); a local
small model is an **optional phrasing/routing layer**, never the source of truth.

**THE INVARIANT (non-negotiable):** the oracle is **READ-ONLY** — it never triggers an action, config
edit, or service change. It sits at **autonomy rung 1 (report)**, never rung 2/3 (act). This is the
oracle's analog of mini-dudeai's MF021 observation-only boundary, and it must be **test-pinned** (a guard
that fails if any oracle code path reaches a mutating function / `send_*` to anything but a query reply /
subprocess/systemctl).

---

## 3. Architecture

```
 field node ──Meshtastic text──► meshtasticd ──► gateway _handle_text_message ─┐
 RNS node ───LXMF────────────────────────────► gateway _on_lxmf_receive ──────┤
                                                                              ▼
                                              ┌───────────────────────────────────────┐
                                              │  mesh_oracle (NEW, standalone module)   │
                                              │  is_query? → intent → READ-ONLY answer  │
                                              │  (state surface + rf.py math)           │
                                              │  [opt] Ollama phrasing/routing          │
                                              │  truncate ≤237B · per-sender cooldown    │
                                              └───────────────┬─────────────────────────┘
                                          directed reply       │ append_jsonl
              send_text_direct_with_id ◄────(mesh)─────────────┤
              send_to_rns(dest_hash=…)  ◄────(RNS)─────────────┘ → ~/mesh_oracle_log.jsonl
                                                                  → warm brief / cadence (Phase 4)
```

- **Keep the gateway change tiny.** The oracle is its **own module** (`src/gateway/mesh_oracle/` or
  `src/oracle/`); the gateway hooks are one-liners (`oracle.maybe_handle(...)`). Logic, intents, state
  reads, phrasing, cooldown, logging all live in the module → unit-testable in isolation, and the
  parity/contract surface of the gateway stays clean.
- **The claw's role:** the claw is **headers/stats-only — it never decrypts payloads** **[V]**
  (`claw_telemetry.py` parses only `device_info` + `ble_stats`). So **inbound queries arrive via
  meshtasticd→gateway**; the claw is the **outbound independent RF voice + witness** (and, in the gated
  portable future, the only radio — which would require adding RX-decrypt firmware; see Phase 5).

---

## 4. Confirmed hooks (the wiring map — all [V] this turn)

| Need | Hook | Location |
|---|---|---|
| Inbound mesh text (decrypted) | `_handle_text_message(packet, decoded, from_id)` — carries `from_id`, `text`, `channel`, `snr`, `rssi`; already loop-guards `is_already_bridged(text)` | `src/gateway/meshtastic_handler.py:566` |
| Inbound LXMF (RNS) | `_on_lxmf_receive(message)` — `source_hash`, `content`, `title`, `fields` | `src/gateway/rns_bridge.py:1124` |
| Directed mesh reply | `send_text_direct_with_id(text, destination=<int>, ...)` → mint packet_id (ACK via `_handle_routing_ack`); sender `!a1b2c3d4` → `int("a1b2c3d4",16)` | `src/gateway/meshtastic_protobuf_client.py:103` |
| Directed RNS reply | `send_to_rns(message, destination_hash=bytes.fromhex(src), title=, fields=)` — directed-only (perfect) | `src/gateway/bridge_send_mixin.py:24` |
| `@id`/short-name resolve | `_resolve_mesh_destination(token)`; reply-to memory `ReplyContextStore` | `src/gateway/_rns_bridge_xform.py:479`, `reply_context.py` |
| Payload cap | `MESHTASTIC_MAX_PAYLOAD=237` + `_truncate_utf8(text, max_bytes)` | `src/gateway/canonical_message.py:31,502` |
| Read-only state | `/api/status` blocks (directory, federation, radio, watchdog, mini_dudeai, claw) | `src/utils/_map_status_endpoints.py:74` |
| Watchdog facts | `/var/lib/meshforge/watchdog.json` (stale `_WATCHDOG_STALE_S=300`) | `_map_status_endpoints.py:287` |
| mini state/brief | `StateStore(~/mini_dudeai_state.json).load()`; `build_brief(...)` | `src/mini_dudeai/state.py:53`, `brief.py:112` |
| RF math | `link_budget`, `free_space_path_loss`, `radio_horizon_km`, `haversine_distance`, `detailed_link_budget().summary()` | `src/utils/rf.py` |
| LLM phrasing (opt) | `OllamaBackend().complete(system, user)` — qwen2.5:3b, raises on failure (never fakes) | `src/mini_dudeai/chat_compiler.py:54` |
| Continuity log | `append_jsonl(path, entries, max_bytes)` — torn-tail-safe, bounded, swallow-and-report | `src/mini_dudeai/history.py:102` |

**Greenfield:** there is **no** existing keyword/command/ping responder on inbound mesh text **[V]** — the
only prior art is the external meshing-around bot subprocess. The oracle is the first of its kind here.

---

## 5. Phases

### Phase 0 — Intent engine, deterministic, no mesh, no model (the safe high-info core) — ✅ BUILT 2026-06-21
**Shipped:** `src/oracle/` (`__init__.py`, `snapshot.py`, `intents.py`) + `tests/test_oracle.py` (45
tests). Standalone package (stdlib + `utils.rf` + `utils.paths` only — no gateway import). Intents
`status`/`whatsup`/`node`/`wd`/`link`/`help` answer from a read-only `NocSnapshot`, ≤237-byte calibrated
replies (unknown/stale never read as healthy). THE INVARIANT is test-pinned
(`test_oracle_module_is_read_only`); the 237 cap + 300s stale thresholds are text-pinned to
`canonical_message.py` / `_map_status_endpoints.py` (no heavy import). **VERIFIED:** `lint.py --all` exit 0;
`pytest tests/test_oracle.py tests/test_regression_guards.py` 72 passed, exit 0. NOT committed/pushed.

Build the `mesh_oracle` module + an initial intent set (§6). Each intent reads the state surface (§4) and
returns a **≤237-byte, calibrated** answer — real value, or a labelled `unknown/stale (age Xs)` when the
source is stale/blind (never fake; #74/#78 lesson). **Unit-tested against fixture state. No I/O.**
- **PASS Phase 0** = every intent returns a correct answer (or honest abstention) on fixtures; read-only
  guard test green.

### Phase 1 — Mesh round-trip (read-only, deterministic), one box — ✅ BUILT 2026-06-21 (default-OFF, not yet round-tripped on air)
**Shipped:** `src/oracle/responder.py` (`MeshOracleResponder`, injected-deps I/O edge: query-gate → allowlist
(fail-closed) → per-sender cooldown → snapshot → answer → directed reply → append-only audit log;
`from_env` factory, **default OFF** via `MESHFORGE_ORACLE_ENABLED`). Wired into
`meshtastic_handler._handle_text_message` after the loop guard (≤6 lines, fail-safe — a handled query is
consumed, not bridged; `self._oracle=None` by default ⇒ inert). Reply via the handler's existing directed
`send_text`; audit log `~/mesh_oracle_log.jsonl` via `mini_dudeai.history.append_jsonl`. Tests:
`tests/test_oracle_responder.py` (18) + `TestMeshOracleWiring` in `tests/test_meshtastic_handler.py` (5).
**VERIFIED:** `lint.py --all` exit 0; 170 passed (oracle + responder + handler + regression guards), exit 0.
**Snapshot is watchdog+mini only** (no directory/federation injected yet ⇒ `fleet:?`); enriching it +
the on-air round-trip + LXMF leg (Phase 2) remain. NOT pushed (mf.5 soak to 06-24).

One-line hook in `_handle_text_message` → `oracle.maybe_handle(from_id, text, channel)`. On a query:
resolve sender node num → `send_text_direct_with_id` → `append_jsonl` the exchange. Enforce **per-sender
cooldown** (mini's `cooldown_s` house style) + an **airtime check** + the `is_already_bridged` loop guard +
a **sender allowlist** (known/fleet nodes) to start. Prove the round-trip from a handheld on the fleet
channel.
- **PASS Phase 1** = a real handheld gets a correct directed reply; exchange logged; no broadcast storm; no
  self-answer; cooldown holds.

### Phase 2 — RNS / LXMF leg — ✅ BUILT 2026-06-21 (default-OFF, not yet round-tripped on air)
Mirror hook in `rns_bridge._on_lxmf_receive` (after the BridgedMessage, before store/bridge) → same
`MeshOracleResponder` (`transport="rns"`) → `send_to_rns(reply, destination_hash=bytes.fromhex(src_hex))`.
Separate allowlist **`MESHFORGE_ORACLE_RNS_ALLOWLIST`** (keyed on LXMF source-hash hex; shares
`MESHFORGE_ORACLE_ENABLED`); a handled query is consumed (not bridged). `_build_rns_oracle_responder` in
`rns_bridge.py`, default-OFF + fail-safe. Tests: `TestMeshOracleRnsWiring` in `tests/test_rns_bridge.py`
(3) + `from_env` per-leg-allowlist test.
**PLUS snapshot enrichment (Phase 1.5):** `oracle.fetch_api_status()` (read-only localhost `/api/status`
GET, bounded, degrades to None) wired into BOTH legs' `snapshot_fn` → `status`/`whatsup` now report real
`fleet:<dir.total>` + `fed:<ok>/<peers>` (was `fleet:?`). 6 fetch tests.
**VERIFIED:** `lint.py --all` exit 0; 498 passed (oracle + responder + handler + **full rns_bridge** +
regression guards), exit 0. NOT pushed (mf.5 soak to 06-24). Snapshot `nodes` still empty (per-node
directory is 35MB — `node <id>` stays "lookup unavailable"; a lighter per-node lookup is a follow-up).

### Phase 2.5 — Environment/channel-scoped allowlist ("anyone on the channel can ask") — ✅ BUILT 2026-06-22
The fail-closed node-id allowlist meant the oracle answered **no one** until each node was hand-listed
— so the built P0–P2 work was dormant. This phase adds **additive, per-transport membership** so any
node on an allowed environment can use the oracle *without being listed individually*. **One concept**
— *"is this sender a member of an allowed environment?"* — realized via each transport's **native
membership token** (the operator's "composability / agnostic reality", grounded in the RNS manual):

| Transport | Native membership | Whitelist key | Env var |
|---|---|---|---|
| Meshtastic | on the channel (channel PSK) | channel **name** → THIS box's slot index | `MESHFORGE_ORACLE_CHANNELS` (names, e.g. `meshforge`) |
| MeshCore (MeshAnchor) | on the channel / a known contact | channel **index** (chan path) / `source_address` (DM path) | `MESHFORGE_ORACLE_MESHCORE_CHANNELS` + `..._MESHCORE_ALLOWLIST` |
| RNS / LXMF (hawaiinet) | **announced** LXMF delivery identity | source-hash hex | `MESHFORGE_ORACLE_RNS_ALLOWLIST` |
| RNS **GROUP** destination | shared AES-256 PSK = a *true* RNS channel | the GROUP dest itself | (design — Phase 5) |

**Allow logic (additive, fail-closed preserved):** `answer_all OR node∈allowlist OR channel∈allowed_channels`.
Empty everything ⇒ answers no one (an empty channel set can never match, so RNS's hard-coded
`channel=0` cannot leak through).

**Shipped:**
- `responder.py`: `allowed_channels: Set[int]` on `__init__`/`from_env`; `_allowed(node, channel)` made
  additive. Read-only invariant preserved (only a set-membership check added; `test_oracle_*` green).
- Meshtastic PhoneAPI (`meshtastic_handler._resolve_oracle_channels`): resolves `MESHFORGE_ORACLE_CHANNELS`
  channel **names** → THIS box's local slot indices via `_channel_resolver` (the same live channel-list
  query the bridge already uses to reconcile its TX channel — no new PhoneAPI probe, #17/#75-safe).
  Unresolved names are logged + **skipped** (never silently index 0). The hook's inbound
  `packet.get('channel')` is a box-local index, so name→local-index is the correct, fleet-stable key
  (#channel-feed-dark / Issue #42 NAME-vs-slot lesson).
- **Meshtastic MQTT-bridge (`mqtt_bridge_handler._build_mqtt_oracle_responder` + hook in
  `_bridge_text_message`) — THE leg that actually fires on fleet gateways.** Discovered at deploy: moc3
  (and the fleet) ingest Meshtastic via the MQTT bridge in **zero-interference mode**, NOT the PhoneAPI
  `MeshtasticHandler` — so the PhoneAPI hook above never runs there. The MQTT leg keys on the channel
  **NAME** straight from the topic (`_topic_channel_name` → `meshforge`) — fleet-stable AND needs no
  startup radio query (so RX churn at restart can't defeat it). Reply is DIRECTED via
  `send_text → /api/v1/toradio` on the gateway's configured channel index (no TCP, no fromradio read).
  Hooked after the `is_already_bridged` loop guard, before store/bridge; `getattr`-guarded so a
  partially-constructed handler can never break RX.
- MeshCore (`meshcore_handler._build_meshcore_oracle_responder` + hooks): `_on_contact_message` (DM,
  identity-gated, `channel=None`) and `_on_channel_message` (channel-index-gated, replies **DM** to the
  asker — never a channel broadcast, honoring the "broadcast is not auto-answered" rail; per-sender
  cooldown also dedups the event-vs-poll dual delivery). MeshCore channels are numeric indices (no name
  layer). ⚠️ async hooks call the sync `handle()` inline (`send_text` only enqueues, so it won't block
  the radio; the bounded `/api/status` fetch only runs for an allowed, non-cooled-down query).
- Tests: additive channel-allow / channel-only / empty-set-never-matches / `from_env` in
  `test_oracle_responder.py`; name→index resolution in `test_meshtastic_handler.py::TestMeshOracleWiring`;
  `test_meshcore_handler.py::TestMeshOracleMeshcoreWiring`.

**Enabling the mesh-oracle (the deploy interface — previously undocumented).** Set on the gateway
service (a systemd drop-in `Environment=`); default-OFF + fail-closed everywhere.
- `MESHFORGE_ORACLE_ENABLED=1` — master switch (shared by all legs).
- `MESHFORGE_ORACLE_CHANNELS=meshforge` — Meshtastic channel **names** any node may be on (resolved to
  this box's index at startup).
- `MESHFORGE_ORACLE_ALLOWLIST=!a1b2c3d4,…` or `*` — specific Meshtastic node-ids (additive; `*`=all).
- `MESHFORGE_ORACLE_MESHCORE_CHANNELS=0,2` and/or `MESHFORGE_ORACLE_MESHCORE_ALLOWLIST=<pubkey-prefix|name|*>`.
- `MESHFORGE_ORACLE_RNS_ALLOWLIST=<source-hash-hex,…|*>` — announced RNS identities.
- `MESHFORGE_ORACLE_COOLDOWN_S=30` — per-sender minimum seconds between answers.

Apply: write the drop-in, then `systemctl restart meshforge-gateway` — an rnsd **client**; it does NOT
restart rnsd (safe alongside the RNS soak; never rapid-cycle rnsd, #69). Verify with the on-air
round-trip (the Phase-1 PASS criterion) + `~/mesh_oracle_log.jsonl`.

### Phase 3 — Optional LLM phrasing / routing layer
`OllamaBackend` used two bounded ways, **never as a fact source**:
1. **Phrasing:** the deterministic engine produces the FACTS (a struct); the model rewrites to natural
   language within ≤237 bytes, temp ~0.2. Model down/slow/over-budget → **fall back to the deterministic
   templated answer** (never block, never fake).
2. **Routing:** a free-form question → the model maps it to a known intent + params; the deterministic
   engine answers. Unmappable → `"I can answer: status, whatsup, node, link, wd, help."`
- **PASS Phase 3** = free-form questions resolve to correct intents; model-down falls back cleanly; a
  fault-injection test proves the model can never inject an unverified fact.

### Phase 4 — Continuity loop back to cloud-me
Feed `mesh_oracle_log.jsonl` into the **warm brief** / a cadence summary so the next cloud-me wakes up
knowing *"while away: N field queries, e.g. 0613 !abc 'status' → answered."* Turns outward presence into
**inward continuity**. Optionally a watchdog probe (oracle alive + answering) — observe-only.
- **PASS Phase 4** = the warm brief shows the away-window query digest.

### Phase 5 — GATED / DEFERRED: portable bundle + claw-only RX + autonomy
- **True RNS "channel" via a GROUP destination** (the faithful agnostic-channel symmetry — designed
  2026-06-22). RNS GROUP destinations use a **shared AES-256 PSK** — "readable by anyone in possession
  of the key" (RNS reference) — the exact analog of a Meshtastic/MeshCore channel PSK. An oracle GROUP
  destination (`RNS.Destination(identity, IN, GROUP, "meshforge", "oracle")`, key via
  `create_keys()`/`load_private_key()`, `announce()`d) that any key-holder queries would give RNS the
  same "on the channel ⇒ can ask" model as the other transports. **Deferred** because: (a) it is
  net-new RNS plumbing **separate from LXMF** (today's RNS leg is LXMF SINGLE + source-hash, directed +
  forward-secret), and a GROUP listener changes the reply/forward-secrecy posture (a GROUP dest has no
  per-sender link — replies would need a SINGLE/LINK back-channel or a group broadcast); (b) it needs a
  key-distribution story (who holds the PSK = who's "on the channel"). The **wire format is unchanged**
  (uses RNS exactly as designed) so it does NOT touch the fork wire-compat invariant. Because announces
  propagate network-wide across all interfaces, one GROUP dest reaches **every hawaiinet node
  regardless of physical media** (TCP link, RNode LoRa, …) — RNS is one network over mixed interfaces.
- **Portable "NOC in a box"** = the standalone Pi+claw bundle (`standalone_wireclaw_variant.md`) running
  the oracle off-grid. Packaging follows the capability.
- **Claw-only portable** (no Pi radio) would need **RX-decrypt added to the WireClaw fork** so the claw
  can receive queries directly — a future firmware arc on a `pr/` branch (THE INVARIANT: never commit on
  `dudeclaw`), explicitly **out of scope** here.
- **Actuation** stays gated by the autonomy ladder / reset-safe-set. Presence ≠ control.

---

## 6. Initial intent vocabulary (start tiny, grow with the domain)
- `status` / `?` → one-line fleet posture: `fleet:<dir.total> fed:<ok>/<peers> wd:<n> OK|SIG`.
- `whatsup` → top active mini conditions / escalations (from mini state/brief).
- `node <id|short>` → last-seen / SNR / hops for a node (node_tracker / directory).
- `wd` → watchdog: signal count + worst class (+ `stale` if `age_s > 300`).
- `link <A> <B>` (node ids or lat,lon) → `rf.py` link budget / radio horizon — deterministic.
- `help` → list intents.
- (later) `claw` → claw telemetry; `peers` → federation detail; free-form → Phase 3 routing.

Every answer prefixes the **honest identity**: `dude-AI@<box>:` — it's the *local proxy*, not cloud-me; a
question needing deep reasoning replies `noted — cloud-me sees this on cadence` and lands in the log.

---

## 7. Continuity log format (`~/mesh_oracle_log.jsonl`, via `append_jsonl`)
```json
{"ts": 1750540380, "transport": "meshtastic", "from": "!a1b2c3d4",
 "query": "status", "intent": "status", "answer": "dude-AI@volcano: fleet:2731 fed:5/6 wd:1 SIG",
 "facts_ts": 1750540350, "facts_stale": false, "delivered": true, "model_used": false}
```
Bounded (max_bytes rotation), torn-tail-safe, swallow-and-report — identical posture to
history/audit/dreams/calibration. Failed sends, unknown intents, and model failures are all logged
(every swallow leaves a probe-visible witness — honest-failure-modes #9).

---

## 8. Rails (honest-failure-modes + calibration + safety pass)
- **Read-only / rung-1** — test-pinned: no oracle path mutates state or sends anything but a query reply.
- **Calibrated answers** — truth or labelled `unknown/stale (age Xs)`; the engine carries `facts_ts`; stale
  watchdog/mini → say stale, never present stale as live. The model **phrases/routes, never sources**.
- **Loop/feedback guard** — never answer `is_already_bridged` text, own text, or another oracle; broadcast
  is not auto-answered; per-sender cooldown.
- **Airtime/duty** — ≤237 bytes; respect airtime thresholds (warn 7% / crit 10%) + cooldown; on ham bands
  clear-text + callsign-ID (the §97.113/§97.119 gate from the roadmap report).
- **Trust on open RF** — sender allowlist (fleet/known nodes) first; read-only bounds blast radius; widen
  deliberately later. **DONE (Phase 2.5, 2026-06-22):** the deliberate widening is the additive
  **channel/environment allowlist** — a node "on the channel" (the channel PSK = the membership token)
  is trusted, which is exactly the open-RF trust boundary the channel key already establishes. Read-only
  + per-sender cooldown still bound blast radius; the operator opts in per-channel, fail-closed.
- **No-perturb reads** — follow the `/api/status` discipline (read `/proc` listen state, never open the
  PhoneAPI — Issue #17/#75); the oracle adds zero load to the radio path.
- **Honest identity** — replies say `dude-AI@<box>` (local proxy), never imply it's cloud-me.

---

## 9. Decision gate · why this arc · fallbacks
- This is the missing **interactive** rung; it's built almost entirely on **confirmed existing hooks** (§4)
  and is **greenfield** (no responder to conflict with). High value, low infrastructure.
- **If deterministic Q&A proves enough** (Phases 0–2), the model (Phase 3) is genuinely optional — that's a
  *feature*, not a shortfall (no LLM in the hot path; zero hallucination).
- **Fallback if mesh RF reply proves unreliable** for a transport: the oracle answers on whichever leg
  works (mesh vs LXMF) and logs the rest for cadence — degraded ≠ dead.
- Cross-link: the **RNS-transport bench** (`dudeclaw_rns_transport_bench_2026_06_21.md`) later widens the
  oracle's Reticulum reach, but is **not** a prerequisite — Phase 1 (Meshtastic) stands alone.

## 10. Effort & reuse
- **No new infra for Phases 0–2** — reuse `send_text_direct_with_id`, `send_to_rns`, `_truncate_utf8`,
  `append_jsonl`, the `/api/status` blocks, `rf.py`, `OllamaBackend`. Net-new = the `mesh_oracle` module +
  two one-line gateway hooks + tests.
- **Effort:** P0 ~1d (module + tests), P1 ~1d (hook + round-trip), P2 ~½d (LXMF mirror), P3 ~1d (model
  phrasing + fallback + fault-injection), P4 ~½d. P5 deferred.

## 11. Calibration
All design is **[B]** until built. The hooks, signatures, file:lines, payload cap, state surface, Ollama
call shape, and append-ledger posture are **[V]** (read from the code this turn). No claim that the oracle
"works" — Phase 0 is unit-proven, Phase 1 is the first real round-trip; "answered once" ≠ reliable
(require the cooldown/airtime/loop-guard tests + a soak before trusting it on-air).
```
```

> **First step:** Phase 0 — the `mesh_oracle` module with `status`/`whatsup`/`node`/`wd`/`link`/`help`
> intents answering from fixture state, ≤237 bytes, calibrated, read-only-guard test. No mesh, no model.
