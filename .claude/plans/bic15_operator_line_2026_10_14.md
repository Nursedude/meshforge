# BIC-15 — Operator line spec (2026-10-14)

> **Status:** code LANDED on `main`, **OFF on every box** (default
> `enabled: false`, `operators: []`). Built by a cloud session (artifact
> `2ent1ZJfgo5Yi2wZunJ8v1`, `patches/opline.patch.txt`, sha256 `e1776642…`),
> landed verbatim, then F1 fixed test-first here. **Gate before ANY box enables
> it: a frontier (Fable) security pass on this spec — new external surface,
> gate 10.** Written by Opus 5.5 from the code as landed; every claim below is
> tagged MEASURED (checked this session) or READ (from code, not run live).

## What it is

An LXMF address per box that the box's operators message from any LXMF app
(Sideband, Columba, MeshChatX). A listed, signature-verified operator gets a
read-only oracle answer; everyone else gets silence. The line also pages the
operators when the watchdog's degraded/wedge set changes — over RNS, so the
page arrives when the WAN (and ntfy) is gone. That is the product case: the
one alert path that survives the outage it reports.

## The surface (what a stranger can reach)

| Leg | Behaviour | Tag |
|---|---|---|
| Identity | own `~/.config/meshforge/operator_line_identity`, own `LXMRouter` (LXMF caps one delivery identity per router) | READ |
| Announce | at start, then every `announce_interval_sec` (default 1800 s). Display name defaults to `MeshForge Operator` — no hostname (B1, landed 2026-10-14) | READ |
| Inbound, unsigned / unknown source | dropped, counted `unverified`; a LISTED operator with "source unknown" gets one path request per 300 s so the next message validates | READ |
| Inbound, signed stranger | dropped silently, counted `strangers` — no auto-reply (an auto-reply to anyone who heard the announce is an airtime amplifier) | READ |
| Inbound, listed operator | per-operator cooldown 10 s, work queue max 64 (overflow counted `queue_full`), answered off the delivery thread | READ |
| Reply content | oracle intents only: `status`, `whatsup`, `wd`, `node <id>`, `link <a> <b>`, `help`; ≤ 237 bytes. `node` returns name, last-seen, SNR, hops. `link` computes a budget from coordinates but does not print them | READ |
| Pages | watchdog transitions (ALERT / CLEARED / BLIND once / VISIBLE again), at most one page per 300 s per box, to every listed operator | READ |
| Path onto Meshtastic | **none** — `operator_line.py` and `oracle/` import nothing that reaches a Meshtastic radio | MEASURED (imports grepped) |
| Path onto RF at all | **yes, via RNS** — announces, path requests and replies ride every rnsd interface, including an RNode on HawaiiNet. All three go through `tx_guard.assert_rns_tx_allowed` | READ |
| Data source | `GET http://localhost:5000/api/status` (answered 200 on VolcanoAI, 16,686 B) + local watchdog/mini state | MEASURED |

Relation to the RNS→RF allowlist (enforcing on moc + moc3): **independent.**
That allowlist gates the gateway's own LXMF→Meshtastic ingress inside
`_on_lxmf_receive`. The operator line has its own router and destination, and
nothing it receives can reach that callback or the radio. Its own allowlist is
`operator_line.operators`. The `rns_bridge.py` hook starts the line just before
the gateway's ingress identity warm-up; a failed or slow start is caught and
cannot stop the gateway (READ). One ordering cost: an enabled line's synchronous
start (router construction, register, first announce) runs before that warm-up.

## F1 — identity load (FIXED this session, test-first)

The cloud audit described F1 as "unreadable → warning + new identity over the
corpse". Measured against the pinned library, the real shape was different and
present in **three** places:

* `RNS.Identity.from_file` never raises. `Identity.load` catches every error
  and returns `None`. So the `except … recreating` branch was dead code, and
  `None` flowed into `register_delivery_identity`.
* Red tests on the old code, with a library-faithful fake: the operator line
  and the broadcast bridge both **started** with a zero-byte identity, and the
  gateway's `_setup_lxmf` **registered** with one. The cloud tests had modelled
  `from_file` as raising, so they pinned the author's assumption.
* `to_file` is a plain `open("wb")`: not atomic, which is the write that makes
  the zero-byte corpse.

Cure: `gateway/identity_store.py::load_or_create_identity`, used by all three.
An existing-but-unreadable file (or dangling symlink) is refused and kept
byte-for-byte. The error names the path and both ways out (restore it, or
`mv` it aside to accept a NEW address). An absent one is created 0600,
fsync'd, hard-linked into place so it never overwrites a racer, then read back
before use. Witnesses: an ERROR log line; `identity_error` + `identity_refused`
in the operator line's and broadcast bridge's status; `lxmf_identity_error` in
the gateway's `get_status()`. The gateway's refusal also skips the rnsd
diagnosis, which used to blame rnsd for an identity fault.

## Config (`gateway.json` → `operator_line`)

```json
"operator_line": {
  "enabled": false,
  "operators": ["<32-hex LXMF delivery hash>"],
  "display_name": "",
  "announce_interval_sec": 1800,
  "cooldown_s": 10.0,
  "alerts_enabled": true,
  "alert_poll_s": 30.0,
  "alert_min_interval_s": 300.0,
  "identity_file": ""
}
```

A malformed operator entry is refused and listed in `config_errors`, never
coerced. `enabled: true` with no operators logs a warning and answers nobody.

## Owed before ANY box enables it

1. **Frontier security pass on this spec** (gate 10). Questions to put to it:
   - **Hostname on a public network.** The default display name puts the box
     hostname into announces on HawaiiNet, a public RNS network. The gateway
     already does this (`MeshForge Gateway (<hostname>)`), so there is
     precedent, but a second announcing identity per box doubles it. Should the
     default be a non-identifying name?
   - **Airtime.** Each enabled box adds an announce every 30 min and
     replies/pages on whatever interface holds the path, including LoRa
     RNodes. Is 1800 s right for a fleet of nine on one RNode channel?
   - **What `whatsup` / `status` / `node` disclose** to a listed operator whose
     phone is lost. The allowlist is a hash; there is no second factor and no
     revoke short of editing `gateway.json` and restarting the gateway.
   - **Signature validation is the whole gate.** It relies on LXMF's
     `signature_validated is True` and on `Identity.recall` in this process.
     Is "listed hash + valid signature" sufficient, or should a page-capable
     line also require a per-operator shared secret?
   - **Page flooding.** A flapping watchdog class can page every 300 s
     indefinitely. Is a daily cap or a flap damper owed?
2. **In-app surface.** The status now reaches `get_status()`, but no TUI pane
   or `/fleet` line renders it. Per the 10-11 rule, it is not done until the
   app shows: line on/off, its address, operator count, last page, refusals
   (`identity_error`, `strangers`, `unverified`).
3. **Live acceptance on ONE box** (after 1 and 2), each leg with a control
   that can fail:
   - a listed operator's `status` is answered; the same text from an unlisted
     identity gets nothing, and `strangers` rises by exactly 1;
   - a planted watchdog degraded signal produces exactly one page, and its
     clear produces exactly one CLEARED;
   - stopping the watchdog produces one BLIND page and no CLEARED;
   - a zero-byte `operator_line_identity` refuses the start and the corpse's
     inode, size and mtime are unchanged afterwards.

## Not in scope

Sending onto Meshtastic from the line (stays with the bridge and its ingress
policy). Remote access to the web UI (lab-only by operator choice, 10-13).

## Security-pass verdict — gate 10 (Fable 5.1, 2026-10-14 ~16:00 HST, non-author)

**Verdict: the trust model is sound — PASS for a single-box live acceptance
once B1 lands. B1 is the only blocker, and it blocks because the FIRST
announce is irreversible.** Every claim below is MEASURED (run or read at the
pinned source this session) unless tagged CALC (a computation) or BELIEVED.

Checks run: `test_operator_line.py` + `test_identity_store.py` + the five
touched suites → 604 passed, rc=0; `lint --all` rc=0; the line's own default
snapshot path executed on this box (`_default_snapshot()` → `answer()` for
every verb); pinned LXMF `1.0.1+mf.8` / RNS `1.3.8+mf.5` read at source; moc
(a live gateway) read: unit runs as the operator user, `ProtectHome=read-only`
+ `ReadWritePaths=~/.config/meshforge`, `gateway_identity` 0600 64 B, no
`operator_line` key in its `gateway.json`.

### The gate itself (spec Q4) — sufficient, measured at source

- `LXMessage.unpack_from_bytes` sets `signature_validated=True` only when the
  identity RECALLED for the claimed source hash validates the signature over
  the signed part; otherwise `SIGNATURE_INVALID` or `SOURCE_UNKNOWN`.
- A stranger cannot poison that recall: RNS `Identity.validate_announce`
  recomputes `expected_hash` from the announced public key and refuses an
  announce whose destination hash does not match. Binding a different key to
  an operator's hash needs a hash preimage.
- Replay of a captured valid message is dropped by the router before the
  delivery callback (`lxmf_delivery`: `if not allow_duplicate and
  self.has_message(message.hash)`).
- Every reply and page is an RNS SINGLE-destination message: encrypted to
  the operator's identity. Only the announce is cleartext.
- Residual, accepted: a forged message CLAIMING a listed source while that
  identity is unknown here triggers ONE broadcast path request per 300 s per
  listed hash (`_nudge`). 1-in → 1-out, bounded, counted (`nudged`).
- A per-operator shared secret is REJECTED: the LXMF identity is the factor,
  the same model every LXMF app uses; a typed secret sits in the thread
  history of the very phone the scenario loses.

### B1 — default display name leaks the hostname into a cleartext announce (spec Q1)

> **LANDED 2026-10-14 (Opus 5.5, builder ≠ the gate-10 reviewer):** default is
> now `MeshForge Operator`, `socket` import dropped; `test_default_display_name_keeps_the_hostname_out_of_the_announce`
> plants a hostname and is RED on the old line. The gateway's
> `MeshForge Gateway (<hostname>)` (same class) is NOT changed — already announced
> fleet-wide, so its cache is spent; changing it is the operator's call. Next per
> this verdict: ONE box, the operator's own identity, the 4 acceptance legs.

`_display_name = cfg.display_name or f"MeshForge Operator ({socket.gethostname()})"`
rides in announce `app_data` on HawaiiNet, a public RNS network, and every
node that hears it caches it — a later rename does not recall it. The box
already names itself INSIDE the encrypted body (measured reply prefix
`dude-AI@VolcanoAI:`, and `compose_page` prefixes `<box>:`), so nothing is
lost by a non-identifying default. Fix: default to `MeshForge Operator` (or
an empty display name). The gateway's `MeshForge Gateway (<hostname>)` is the
same class; out of this pass's scope, same one-liner.

### Spec Q2 — airtime: 1800 s is the quietest announcer on the box

Measured defaults: gateway `rns.announce_interval` **300 s**, broadcast
bridge **600 s**, operator line **1800 s**. The line adds one sixth of the
gateway's announce load. CALC: a ~240 B LXMF delivery announce at SF7/250 kHz
CR4/5 is ≈0.19 s on air (358 symbols × 0.512 ms + preamble); nine lines at
1800 s = 18/h ≈ 3.4 s/h before transport rebroadcast (× (1+T) for T
transport nodes sharing the channel). Keep 1800 s; the number worth
questioning on this fleet is the gateway's 300 s. Pages: at most one per
300 s per box per operator; `desired_method` defaults to DIRECT (link
handshake, ≥3 packets) although every reply measured here fits one packet
(max 91 B; cap 237 B) — see S6.

### Spec Q3 — a lost phone: low-information, read-only, revoke is the gap

What a listed identity can learn, measured on this box: `status` → node
directory total, federation peers, watchdog/mini counts; `whatsup` → active
signal CLASS names (e.g. `detector_blind_any`); `wd` → signal count/worst
class; `link <lat,lon> <lat,lon>` → path math on coordinates the ASKER
supplies; pages → degraded/wedge class names. No verb acts; there is no relay
verb (test-pinned); nothing it answers reaches a radio. `node <id>` and
`link <node-id> …` answer NOTHING on this surface today — see S2.
Revoke today = edit `gateway.json` + restart the gateway (which also restarts
the LXMF leg). That is the gap, not the disclosure: the in-app surface the
spec already owes must carry **revoke** (write the config, restart the line
only — `OperatorLine.stop()`/`start()` exist). BELIEVED, not exercised: the
fork's `Reticulum.is_blackholed(identity)` (seen in `unpack_from_bytes`) is a
box-wide RNS-level ban that would complement it.

### Spec Q5 — page flooding: bounded in airtime, unbounded in attention

`alert_min_interval_s=300` caps a box to one page per 5 min regardless of
flap rate; coalesced lines read `+N more`. Worst case 288 pages/day/box per
operator from one flapping class. Not an RF problem; a human one. Owed to
BIC-2, not this gate: a flap damper (K transitions of one key inside W →
one `FLAPPING <key>` line, then silence for that key for an hour).

### F1 (`4f94f92e`) — reviewed, holds

`_write_new`: `mkstemp` (0600) → `fsync` → `os.link` (EEXIST rather than
clobber) → unlink tmp → dir fsync best-effort. `FileExistsError` re-entry
loads the RACER's file and verifies it like any existing one. Dangling
symlink refused; zero-byte refused with size in the message
(`test_real_rns_zero_byte_identity_is_refused` uses the pinned RNS, not the
fake). Gateway: `except IdentityUnreadable` precedes `except Exception`, so
only that class skips the rnsd diagnosis; a real rnsd fault raises before
`_setup_lxmf` and still takes the diagnosis branch; `_lxmf_identity_error`
reaches `get_status()`. Nit: a non-EEXIST `OSError` from `os.link` (a
filesystem without hard links) surfaces as a generic start failure without
the `identity_error` witness — acceptable, the path is `~/.config` on ext4
fleet-wide. Upstream nit, ours to own: LXMF `unpack_from_bytes` line 815
assigns a LOCAL `signature_validated = False` in the SOURCE_UNKNOWN branch;
harmless only because `__init__` initialises the attribute False.

### Should-fix before the FLEET roll (not before the one-box acceptance)

- **S0 (DONE by the reviewer, test-only, unreviewed by a third):**
  `tests/test_operator_line.py` could not run alone — `tests/` is a package,
  so pytest puts the repo root on `sys.path`, and the file imported
  `_fake_rns_identity` without the `sys.path.insert` every sibling carries;
  alone → collection error rc=2, after another file → 604 passed. It passed
  in CI and the full suite only by collection order. Fixed in place; alone →
  32 passed rc=0.
- **S1 answered-query witness.** Every DROP is counted but an ANSWER leaves
  only `answered += 1`: no source, no intent, no time. The lost-phone question
  ("was my line queried while the phone was gone?") is unanswerable. One INFO
  line per answer (source prefix, intent, bytes) and the Phase-1 responder's
  audit append (`oracle_log_path`) — two lines.
- **S2 `node <id>` is advertised and dead.** `_default_snapshot()` passes no
  `nodes`, so the directory is empty (measured: `nodes in directory = 0`,
  `node moc → lookup unavailable (no directory)`), yet `help` offers it. Either
  drop `node` from THIS line's help, or wire the directory — and then decide
  deliberately, because `link <node-id> <lat,lon>` becomes a DISTANCE ORACLE
  on every node with a position, positions shared on private channels
  included (three queries trilaterate). An instrument that advertises a verb
  it cannot answer fails at legibility.
- **S3 `pages` counts hand-offs, not deliveries.** `_deliver_lxmf` returns
  True at `handle_outbound`; LXMF retries up to `MAX_DELIVERY_ATTEMPTS=5` and
  then fails where the line cannot see. Register the message's delivery and
  failed callbacks → `page_delivered` / `page_failed`; label the status field
  `pages_handed_off` until then. BIC-2's leg, a counter now.
- **S4 LXMF's own anti-spam is one argument away and unused.**
  `register_delivery_identity(..., stamp_cost=None)`, router
  `enforce_stamps=False`. The destination is announced on a public network and
  inherits the gateway's exposure to link-establishment floods. An OPT-IN
  `inbound_stamp_cost` (default 0 = off; Sideband/Columba support stamps) is
  the lever to pull if `strangers` ever climbs. Do not turn it on by default:
  it costs the operator's phone proof-of-work per message.
- **S5 journal flood.** `Operator line IGNORED unverified sender …` is one INFO
  line per inbound message; rate-limit per source (once per `_NUDGE_S`),
  keep the counter.
- **S6 airtime per reply.** `desired_method=OPPORTUNISTIC` for replies and
  pages: LXMF falls back to DIRECT automatically when the content exceeds
  `ENCRYPTED_PACKET_MAX_CONTENT`, so it is safe, and it turns a ≥3-packet
  link exchange into one packet on LoRa for every answer measured here.

### Carried forward unchanged

Items 2 (in-app surface — now including **revoke**) and 3 (the 4-leg live
acceptance on one box) of "Owed before ANY box enables it" stand. Order:
B1 → enable on ONE box with the operator's own identity → the 4 legs →
S1–S6 → fleet.
