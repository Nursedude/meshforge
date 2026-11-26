# BIC-2 slice — DM delivery truth: DESIGN RECORD (2026-11-25, grilled)

> **Status: DECIDED, NOT BUILT.** Grilled 2026-11-25 12:5x–13:0x HST (`date`),
> Opus 5.5, 16 questions, operator "yes" to every recommendation.
> ⚠️ Decided on a non-frontier session (model_advisor upshift): the fork
> choice (A + D) is QUEUED for a Fable pass (`review_provenance.md`) before
> the first deploy. Groundwork + measured facts:
> `bic2_dm_ack_groundwork_2026_11_25.md` (read it first).
> **§4 measurement session DONE 14:11–14:49 HST — see §4b: it REOPENED and
> superseded Q1, Q2 (window), Q10; added Q17–Q19 + D-1. §4b wins where it
> differs.** Next = slice 1, test-first (red on today's `handed_off`).

## 1. Acceptance sentence (sharpened, Q2)

An `/inbox` DM row — web AND TUI — says, per send, exactly ONE of:

| State | Rule |
|---|---|
| **delivered** | a positive ROUTING_APP ack, `request_id` == this row's packet id, `from` == the row's destination |
| **failed: \<reason\> (said by \<who\>)** | a NAK naming this packet id, from ANYONE — our own radio's give-up after retries (MAX_RETRANSMIT) is real failure evidence; `who` keeps a relay's NAK distinct from the destination's |
| **left, not confirmed** | only positive acks from someone else (own radio, a relay) |
| **waiting** | inside the retry window, nothing back yet — window = MEASURED (§4 #2), never a guessed constant |
| **no ack seen: \<why\>** | window closed, or this box cannot see acks — the why is always shown |

Never "delivered" from our own radio's ack. END (accepted on the operator's
phone, Q9): a DM from `/inbox` to a node that is up turns **delivered**; to a
node that is off, it never does.

## 2. Decisions

| Q | Decision |
|---|---|
| Q1 | **Fork A is the only verdict source**: per-box `/e/` witness in the map's MQTT listener, join by packet id. **D** (meshtasticd journal `[Router] Received routing from=`) is a cross-check that never decides: A empty + D shows a routing packet from the destination in the window → "possible ack, not matched", never delivered. B (gateway spool) and C (TCP sender session) are OUT of the product path; C stays a drill tool. |
| Q2 | The five states above. Classifier = `utils/dm_delivery.classify` (ONE implementation; "waiting" + the `why` reasons are additions to it, not a second classifier). |
| Q4 | **Per-box**: a DM is judged only by the witness on the box that SENT it. No fleet join this slice. |
| Q5 | Widen the encrypted subscription from `{root}/{channel}/#` (one channel today, `mqtt_settings.subscriptions`) to `msh/2/e/+/#`. Decrypt ONLY envelopes with `to ≠ 0xffffffff` (the header is plaintext); keep only ROUTING_APP whose `request_id` matches an outstanding DM of this box. **No own-node-id needed** (own radio's ack is "other" because `from ≠ dest`) — fits "own id never inferred from traffic". PKI-topic envelopes: skipped + COUNTED, never an error. CPU cost measured on moc5 (smallest box) during §4. |
| Q6 | Default key `AQ==` only; no stored secret (11-24 posture). Custom-keyed channel → "no ack seen: channel \<name\> uses a key this box doesn't hold". Storing a custom key = a later operator decision, its own slice. |
| Q7 | In-memory bounded ring of acks in the map. A **final** verdict (delivered / failed) is WRITTEN BACK to the inbox row, so a restart never erases a settled answer. A non-final row sent before the witness started → "no ack seen: witness started after this send". Ring + write-back wire together or fail together (hfm #4). |
| Q8 | Counters, no alarm (observe before alarm): envelopes seen · decrypted · PKI skipped · decrypt failures · acks matched (from-dest / other / NAK) · unmatched routing. Shown in `/api/inbox` status + TUI. |
| Q9 | Web row + TUI inbox view in the SAME slice, plus a witness status line ("acks visible: yes/no, why"). Accept on the operator's phone (END in §1). |
| Q10 | **D = slice 2**, by extending `box_truth_rx` (byte-identical MF/MA → `parity_check`), never a second journal reader. Slice 2 scope driven by whether §4 shows A missing something. |

## 3. Plan / order

| Q | Decision |
|---|---|
| Q14 | **Slice 1 accepted on the operator's phone by 2026-12-11.** Order: §4 measurements → test-first build → Fable review by RUNNING → deploy → phone acceptance. Independent of the 12-04 one-box "cannot see hearings" debt. |
| Q15 | **MeshForge first.** MeshAnchor gets its own groundwork + grilling AFTER MF slice 1 is accepted (MeshCore's ack is a different measurement). Pinned now: the `dm_delivery` vocabulary + `CanonicalMessage` compatibility. |
| Q16 | Fable review by RUNNING (`code-review medium`, invoked from the main session) before the first deploy. Fork choice queued in `review_provenance.md`. |

## 4. Measurement session — owed BEFORE code (Q3, Q11–Q13)

One sitting, ~45 min, operator sends DMs from `/inbox`; n≥2 each; capture =
bounded `timeout mosquitto_sub -t 'msh/2/e/#'` on the SENDER box's local
broker + its meshtasticd journal; **ids + counts only**, no text, no key printed.

1. **Relayed DM** (1+ hops, same preset LongFast/ch20, up, default-key) ×2 — whose ack shows up (destination or a relay), latency. Target named by the operator at the session (Q11; physical fact). None qualifies → recorded UNKNOWN "no relayed target on this fleet", never skipped.
2. **NAK** ×2 — to a spare node the operator unplugs; else a never-seen node id (Q12). **No fleet box powered down.** Does our radio's give-up reach `/e/`, with which error, after how long → sets the "waiting" window.
3. **Another sender box** — DM from moc5's `/inbox` ×2: does 11-25's VolcanoAI result hold on moc5's broker? Sample moc5 CPU during it (Q5 cost).
4. **Custom-keyed channel** — only if the fleet has one; else record "not applicable".
5. **Control that can fail** — witness on the wrong subscription must read "no ack seen", never delivered.

Results → append to the groundwork doc with `date` stamps. If a result
contradicts a decision above (e.g. the NAK never reaches `/e/`, or a relayed
ack comes from the relay), that decision REOPENS — no code against it.

## 4b. §4 RAN 2026-11-25 14:11–14:49 HST — results + the decisions they reopened

Results table: groundwork doc § "design §4 session". §4 #1 ✅, #2 ⚠️ (give-up
invisible), #3 ⚠️ (moc5 half-blind), #4 n/a so far, #5 ✅ offline. Every DM's
row read `handed_off` — refused, failed and delivered alike (the live gap).

**Superseding decisions** (operator "yes" to each, 14:2x–14:5x HST; these
OVERRIDE the rows above where they differ):

| Q | Was | Now |
|---|---|---|
| Q1′ | D never decides | D DECIDES for own-radio NAKs: journal `Alloc an err=N … idFrom=0x<id>` == the row's packet id → **failed: \<name\> (said by our radio)**. Exact id = A's standard. Remote acks via D stay cross-check only (no `request_id` at INFO). |
| Q10′ + Q17 | D = slice 2 | **All of D lands in slice 1**, ONE allowlisted reader extending `box_truth_rx` (`parity_check`). Remote cross-check: routing from the destination in the journal, no `/e/` match → **"possible ack, not matched — this box doesn't uplink the channel the ack came on"** (measured live on moc5). |
| Q2′ | "waiting" window = measured, TBD | **waiting = 60 s** — BELIEVED (n=6 acks, max 24 s; retries span ~17 s; one sender box). A **late-ack counter**: a match after the window still flips the row AND counts as a witness the window is too short. Re-derive after a soak. A closed window never blocks a later "delivered". |
| new | — | **"failed" never comes from a retry give-up** on this fleet (MAX_RETRANSMIT invisible to both witnesses, n=2). A dead destination ends "no ack seen: window closed", with **"sent N×"** from the retransmissions counted on `/e/` (same packet id). |
| Q18 | — | **No radio config change in this arc** (moc5's channel uplink): device config + a broker that may bridge to a public server = its own decision with a full-box survey first. Operator reads moc5's channel list when convenient. |
| Q19 | witness line "acks visible: yes/no" | The witness line NAMES the channels it covers, measured from the broker ("acks visible on: HawaiiNet, meshforge, LongFast — seen here in the last N min"), never inferred from the radio. |
| D-1 | — | **The journal reader ALLOWLISTS the exact lines it parses** (routing, `Alloc an err`, `refusing`, `Resetting next hop`) and keeps ids only — never a blocklist. This session's four blocklist reads all leaked text or a position. |

## 5. Not in this slice

- (Fork D moved INTO slice 1 — Q10′/Q17.)
- Fleet join of relay-heard acks (later; heard-by pattern).
- Custom channel keys (operator decision).
- Bridged legs mesh ↔ RNS (BIC-2's other half; own groundwork).
- MeshAnchor port (after MF acceptance).
