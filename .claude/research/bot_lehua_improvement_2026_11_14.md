# lehua bot (meshing-around fork) — what to improve, measured (2026-11-14)

**Verdict:** the bot already answers weather by the asker's position; what it
gets wrong is *saying what happened*. "Reply delivered" is the bot's OWN radio
(VERIFIED). Chunking cuts mid-phrase and can drop chunks. Alert feeds that fail
can read as "no alerts". "Missed" commands are LATE, not lost (the reply
blocks the receive thread). Police, fire and power have no public feed in
Hawaiʻi County; internet has one state-level feed. These are the improvement
list, before any Pilina feature.

Operator 2026-11-14 22:3x: "lehua and the meshing-around fork — a bit of deep
research to improve the feature of the bot — Pilina now tied to the bot".
Inputs: lehua read-only survey (this session), two research agents (fork code
at `3db5cef` vs upstream `92280b7`; firmware `meshtastic/firmware@6d41e279` +
public feeds, every page opened), the 11-13 Pilina decision (artifact
13a9roaJrEvPP4NRMYNJmX) and `meshing_around_bot_audit_2026_07_06.md`.

✅ **DECIDED 2026-11-14 (operator): "build Pilina in the fork on lehua —
better integration to the meshforge/meshanchor TUI — a new user could choose
to run the bot among other features — it often is our proof of syn/ack."**
Supersedes the 11-13 line that shipped Pilina from `meshing_around_meshforge`.
Measured integration state: MF's TUI Extensions handler ALREADY installs the
fork (`git clone -b meshforge Nursedude/meshing-around` → `/opt/meshing-around`,
`extensions.py:34-46,224`; the companion supplies only the service template +
profiles). MeshAnchor has a `meshing_around` plugin but NO install/run path
(grep of its `src/`: no `_MA_REPO`/`_MA_BOT_BRANCH`) — a gap. The fleet uses
the bot as the far end of path proofs (lehua log 11-14: `wx From: meshforge
kiai`, `[MC:wio] wx From: meshforge moc` over the MeshCore bridge, `Wx From:
VolcanoAI` — each answered), so the reply arriving back is the real ACK and
the bot's own "delivered" log line must never stand in for it (finding 1).
The MA install gap lives in ROADMAP **BASE-1 stage 2** (HAM + BOT feature
flags; operator 11-14), per the one-app artifact 1Li1CG6aegAnpYTPdgf8N8,
which already named it ("MeshAnchor has no Extensions handler") and the
missing TUI warning for a bot on `tcp` → `localhost:4403` — lehua's config.

## What lehua runs (MEASURED, read-only, 2026-11-14 22:4x)

| Fact | Value |
|---|---|
| unit | `mesh_bot.service` SYSTEM, User=wh6gxz, active since 11-14 04:43, NRestarts 0 |
| code | `/opt/meshing-around` @ `3db5cef` (11-01), origin `Nursedude/meshing-around` |
| own node | `!0daee001` (229564417) — from the bot's own startup log |
| interface | `type = tcp`, `hostname = localhost` → shares meshtasticd's ONE TCP client slot |
| timing | `responsedelay = 2.2`, `splitdelay = 2.5` |
| replies | `respond_by_dm_only = False` (but `antiSpam` still DMs on the public channel — fork `mesh_bot.py:2399-2405`) |
| location | `[location]` lat/lon set (value not recorded here); `usemeteowxapi = False` |
| traffic | 3–19 commands/day, 11-01..11-14; 11-08..now: **0 of N commands without a following reply** |

## Findings (ranked by what a user sees)

1. **"Delivery ACK: reply delivered" is the bot's own radio — never delivery.**
   Every such line this week is `from !0daee001` = lehua (VERIFIED). Firmware:
   a broadcast with want_ack gets an IMPLICIT ack when the radio hears a
   neighbour rebroadcast, built with `from` = own node
   (`ReliableRouter.cpp:39-61`, `Router.cpp:192-205`); a DM gets a REAL ack
   `from` the recipient (`ReliableRouter.cpp:49,96-117`); retries exhausted →
   `MAX_RETRANSMIT` NAK (`NextHopRouter.cpp:280-285`, 3 tries). Fork code
   (`mesh_bot.py:2057-2077`, fork-only) logs "delivered" for any ROUTING_APP
   with no error and never compares `from` to `myNodeNum`. **Fix:** three
   states — `relayed` (from == me), `delivered` (from == DM destination),
   `failed` (NAK) — and no ack = `unconfirmed`. Same class as BIC-2.
2. **"The bot may not catch it" = the reply BLOCKS the receive path.**
   `onReceive` runs `auto_response()` synchronously on the library's single
   publish thread (fork `mesh_bot.py:2498`; meshtastic 2.7.11
   `mesh_interface.py:1669`, `util.py:293-311`): fetches (15 s timeout each,
   `wx` does 3) then `time.sleep` per chunk (`system.py:1022-1043`; 4-chunk
   wx ≈ 13 s). A second command is QUEUED and answered up to ~58 s late —
   a user resends, then gets two answers. MEASURED consistent: for the 10:53
   `wx` reply (receive thread) all 4 acks logged AFTER the last chunk; for the
   05:20 volcano broadcast (watchdog thread) an ack logged BETWEEN chunks 2
   and 3. ⚠️ A packet the radio never handed the bot leaves NO log line —
   "0 unanswered" cannot see that; it needs a drill. **Fix:** reply from a
   worker queue (receive stays free), say "working…" once if a fetch is slow,
   coalesce a repeated identical command.
3. **Chunking cuts mid-phrase and can drop the tail.** `messageChunker`
   (`system.py:859-943`, identical upstream) sizes by CHARACTERS (160), splits
   lines, joins, then hard-splits at the last space — the leftover tail of a
   long line is never re-merged: MEASURED `wx` 10:53 chunk 4/4 = `0.1in.`;
   16:40 the same fragment as chunk 2/4. Emoji can push a 160-char chunk past
   the 233-byte `DATA_PAYLOAD_LEN` → "Data payload too big" → `send_message`
   except (`system.py:1047-1049`) **drops the remaining chunks** (code read;
   not seen on lehua). PKI DMs leave ~220 text bytes (BELIEVED arithmetic from
   `Router.cpp:630,658`). **Fix:** size by UTF-8 bytes to the DM budget; pack
   whole forecast periods; number parts `(1/3)` on air; cap parts (MeshMonitor
   caps at 3); a wx reply that fits ONE packet is the goal (the 11-13
   "every answer fits one DM" rule).
4. **A failed feed can read as "nothing".** IPAWS sub-alerts that fail are
   skipped silently → "No alerts found." (`locationdata.py:693-696`); `wx`
   drops its alert line when that fetch fails (`locationdata.py:370-372`);
   scheduled broadcasts skip on error with no word to the channel
   (`system.py:1451`). Also: NOAA alert broadcast is dead when
   `ignoreEASenable=True` (`locationdata.py:574-584` elif); the broadcast
   window gate (`minute % alert_duration == 0 and second <= 17`,
   `system.py:1422`) can miss a whole window (BELIEVED); `alert_types`
   NameError when US + DE alerts are both off (`system.py:1462`). **Fix:**
   tri-state every alert answer (ok / none / unavailable + age), the
   honest-failure rule.
5. **Weather by lat/lon mostly EXISTS.** `get_node_location`
   (`system.py:720-769`, fork adds a plausibility guard) gives the SENDER's
   position to `wx`, `wxa`, `ealert`, `earthquake`; otherwise the config
   location. Gaps: `valert` and IPAWS ignore the asker (`mesh_bot.py:128`,
   `locationdata.py:858`; IPAWS filters by FIPS/SAME only); scheduled
   broadcasts always use config. **Fix:** say WHICH place was answered when
   it fell back ("for Hilo — your node shares no position"); NWS
   `/points/{lat},{lon}` → zone/county makes alerts per-asker (keyless, needs
   a User-Agent; 200 for Hilo this turn).

## Police / fire / power / internet — what exists (agent opened each page)

| Want | Feed | Machine-readable? | Verdict |
|---|---|---|---|
| Police | HPD alerts moved Nixle → Everbridge 2025-07-15 (sign-up) | no public feed | not buildable from a feed |
| Fire | HFD uses PulsePoint (CPR alerts, 2020) | partner-only; ToS forbids scraping | not buildable |
| Civil Defense | hawaiicounty.gov returned 403 to automated GETs | UNKNOWN | operator checks in a browser |
| Power | HECO Hawaiʻi Island outage map (ArcGIS app), KIUC map | no documented feed | mesh-native instead (below) |
| Internet | IODA v2 `outages/alerts?entityType=region&entityCode=4412` (Hawaiʻi) | keyless, state-level only; reuse licence UNKNOWN | usable with care; Cloudflare Radar needs a token, country/ASN only |
| IPAWS live | FEMA IPAWS feed | MOA + PIN | the open archive is 24 h late — not live |

So the 11-13 decision stands and is now evidence-backed: **power and internet
answers come from what the mesh MEASURES** — mains→battery share of nearby
nodes with a per-node baseline (battery 101 = powered), aggregate-only; our
own uplink probes + gateways losing MQTT — with IODA as an optional,
labelled state-level supplement. Police/fire = relay what Civil Defense
publishes only if a feed is found; never imply coverage the bot does not have.

## Other bots (for parity, not breadth)

MeshMonitor auto-responder: 200 UTF-8 bytes, ≤ 3 parts marked `(1/3)`,
split at line/sentence/comma/space, sends 30 s apart (parts 10 s), tracked
ACKs, per-trigger cooldown, no location token. check-mate: `(n/m)` parts,
2 s gap, node or CLI position. meshing-around upstream: no on-air numbering,
`wantAck = False` default. Sources: meshmonitor.org/features/automation,
github.com/dpup/meshtastic-check-mate, github.com/SpudGunMan/meshing-around.

## Order (proposed — grill first, test-first, one slice per deploy)

1. ACK truth (finding 1) — smallest, highest honesty value, upstream-offerable.
2. Byte-sized, period-packed, numbered chunks (finding 3) + its drill: a wx
   reply on air, count packets and bytes at BOTH ends.
3. Reply off the receive thread (finding 2) + drill: two commands 3 s apart,
   both answered, measured lateness.
4. Tri-state alerts + "answered for <place>" (findings 4–5).
5. Pilina slices (a) profile, (b) power, (c) internet — after REL-1 (operator 11-10).

Upstream offer-back: SpudGunMan = issue text OK WITH the AI-assisted line
(memory feedback_upstream_offer_back); the chunker and ACK fixes are the
likely PRs. Open: confirm the fork-vs-companion repo choice; Civil Defense
feed (browser check); IODA reuse terms.
