# BIC-2 slice — DM delivery truth: DESIGN RECORD (2026-11-25, grilled)

> **Status: DECIDED, NOT BUILT.** Grilled 2026-11-25 12:5x–13:0x HST (`date`),
> Opus 5.5, 16 questions, operator "yes" to every recommendation.
> ⚠️ Decided on a non-frontier session (model_advisor upshift): the fork
> choice (A + D) is QUEUED for a Fable pass (`review_provenance.md`) before
> the first deploy. Groundwork + measured facts:
> `bic2_dm_ack_groundwork_2026_11_25.md` (read it first).
> **Nothing is coded until the measurement session (§4) is done.**

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

## 5. Not in this slice

- Fork D journal cross-check (slice 2).
- Fleet join of relay-heard acks (later; heard-by pattern).
- Custom channel keys (operator decision).
- Bridged legs mesh ↔ RNS (BIC-2's other half; own groundwork).
- MeshAnchor port (after MF acceptance).
