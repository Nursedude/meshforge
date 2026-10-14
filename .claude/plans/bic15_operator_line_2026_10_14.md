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
| Announce | at start, then every `announce_interval_sec` (default 1800 s). Display name defaults to `MeshForge Operator (<hostname>)` | READ |
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
