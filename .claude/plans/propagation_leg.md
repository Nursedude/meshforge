# LXMF propagation leg — the next shape-C organ (opened 2026-07-20)

> Written BEFORE execution so the arc survives a session turnover. Origin: the
> optional-organ sweep the operator asked for after structural-dark row 5
> closed. That sweep's structural result — of 50 signal classes, exactly ONE
> (`aredn_organ_undeclared`) watches for an *available-but-unadopted*
> capability; 31 are liveness, 13 drift, 4 configured-but-broken — is the reason
> this row exists.

## The finding (verified 2026-07-20, live)

A real LXMF propagation node announces on the RNS network and **our own
gateways already parse the announce and throw it away**:

```
Parsed announce aed1f551: type=LXMF_PROPAGATION, name=j]@p@
Discovered RNS node: aed1f551 (j]@p@) [LXMF_PROPAGATION]
```
926 such lines on moc / 1624 on moc3 over 7 days, while BOTH gateway boxes
carry `gateway.json rns.propagation_node: ''`.

Consequence: LXMF to an offline peer just fails. With a propagation node it is
stored and forwarded when the peer returns — the same value AREDN buys, on the
delivery layer instead of the transport layer.

## STATUS: slice 1 SHIPPED 2026-07-20 (MF `6f42d477`, MA mirror)

`probe_lxmf_propagation_unused` is live fleet-wide. Live dispositions read from
each box's OWN watchdog state (the consumer of record, not the wiring):
moc `active` — "15 LXMF propagation node(s) heard within 6h (nearest
rns_360b1aa372f4aee0, 9 min ago)"; moc3 same; moc5 `inert — no gateway.json`;
moc1/kiai `inert — no RNS node cache`. honest_status 5/6 PASS + 1 truthful WARN
(the finding itself). Mutation-verified: making the stale-cache branch reset the
streak fails `test_lxmf_propagation_holds_on_stale_or_unreadable_cache`.
Eval `oracle-lxmf-propagation-unused-is-a-trust-decision`.

**Slice 2 (adoption) is OPEN and is the operator's call — see the trust
boundary below.**

## STATUS: slice 2 STEP 1 SHIPPED 2026-07-20 (MF `a8848bab` + `4fb6d646`)

**Our own propagation node is LIVE on moc1**: `3968a2eeac25e2e7a7961f25842d3d85`,
`lxmd 1.0.1+mf.1` on `/usr/bin/python3` (the fleet pin — consumer-of-record
checked, no venv surprise). Templates are repo-tracked:
`templates/lxmd/config.example` + `templates/systemd/lxmd.service`.

Siting evidence (all 9 boxes surveyed live): moc1 was the idlest box in the
fleet (load 0.17/0.08/0.08), had 2× the disk headroom of any candidate (102G),
healthy rnsd, and hosts no gateway — so the node's fate is not coupled to a
`meshforge-gateway` restart or its `os._exit(2)` wedge path. Ruled out on
evidence: moc3 (259M mem free + RNS canary), meshanchor-server (load 3.4),
moc/moc3 (gateway coupling), VolcanoAI (#4/#5 reset history + already sole manager).

VERIFIED at the consumer of record: **both** gateways independently logged
`Parsed announce 3968a2ee: type=LXMF_PROPAGATION` within ~1s of start AND of
restart — RNS reachability moc1→moc/moc3 proven end-to-end, not inferred.

Two live-caught fixes during bring-up, both observability:
- **`PYTHONUNBUFFERED=1`** — lxmd's stdout is block-buffered under systemd, so
  a perfectly healthy node produced ZERO journal lines. A running organ nobody
  can observe is this fleet's recurring tax (honest_failure_modes #9).
- **no `-s/--service`** — that flag redirects logs to a private file, which
  would make the daemon dark to journalctl and to any journal-reading probe.

Config posture, deliberate: `autopeer = no` (autopeering would sync our stored
fleet traffic OUT to the foreign nodes we stood this up to avoid),
`message_storage_limit = 500` stated explicitly rather than left implicit,
`auth_required = no` (public service, bounded store), identity 0600.

### ⚠️ CORRECTION to this doc's own trust rationale (verified 2026-07-20)

The "the node currently announcing is FOREIGN (**garbled display name**)" line
below is partly an artifact of OUR parser, not evidence about that operator.
Our node, announcing the perfectly ordinary name `WH6GXZ MeshForge PN`, is
logged by our own gateways as `name=j^x(` — the same shape as the stranger's
`j]@p@`.

Root cause, source-verified: `LXMFParser.parse` (`src/gateway/rns_services.py:112`)
serves BOTH `lxmf.delivery` and `lxmf.propagation`, but only understands the
DELIVERY app_data shape `msgpack([display_name_bytes, stamp_cost])`. A
propagation announce is a 7-element array whose element 0 is the boolean
`False` (`LXMRouter.get_propagation_node_app_data`); the real name lives in
element **6**, `metadata[PN_META_NAME]`, as UTF-8. So the ladder reads a bool /
falls through to the marker heuristic and renders raw msgpack bytes as a "name".

This is honest_failure_modes #1 in its purest form — an inapplicable parse
mapped to a valid-looking value (a display name) instead of `unnamed` /
`undecodable` — and it propagated all the way into a **trust judgment** in this
very plan. The decision to run our own node still stands on its own merits
(metadata custody, uptime control), but the "garbled = sketchy stranger"
evidence was our bug. **Queued as its own row** — parser fix + propagation-shape
tests + the twin in MeshAnchor; do NOT bolt it onto the adoption commit.

## The two slices — do NOT merge them

1. **DETECTOR (this row, watchdog-only, always-safe per the burn-down's
   sequencing rule).** Signal: a propagation node is reachable/announced and
   this gateway is configured to use none. Config-free positive evidence, the
   row-5 shape.
2. **ADOPTION (operator decision, deferred).** Setting `rns.propagation_node`
   edits `gateway.json` and needs a `meshforge-gateway` restart — gateway code
   path, and ⚠️ the gateway's wedge watchdog calls `os._exit(2)`, so never do
   this mid-soak. **Trust boundary**: the node currently announcing is FOREIGN
   (garbled display name) and a propagation node sees stored-traffic metadata.
   Strictly better option: stand one up on our own rnsd and point the fleet at
   it. That choice is the operator's, not the detector's.

## STATUS: slice 2 STEP 2c SHIPPED EARLY, 2a/2b STILL PENDING (2026-07-20)

**Read this before running anything below.** The 2026-07-20 session that was
asked to "do step 2" found the node had soaked **28 minutes**, not a day
(`lxmd` active since 09:37 HST; the step-1 commits landed 09:34–09:36). The
soak gate below is explicit — *"prove it survived a day before anything
depends on it"* — so adoption was NOT run. Operator decision, recorded: **do
2c now, adopt tomorrow.**

So the ordering constraint in §2c ("must land in the same push as adoption")
is satisfied from the *other* direction — the probe is already in main, which
is strictly safer than the reverse. **The remaining work is 2a → 2a-bis →
2b → 2d**, where **2a-bis (prove store-and-forward before adopting) was added
on the operator's call the same evening** — read it, it changes the sequence
and carries a hard gate: a drill that does not deliver means do NOT adopt.

> **UPDATE 2026-07-21: 2a and 2a-bis both PASSED** — the hard gate is cleared,
> store-and-forward is proven live. Remaining: **2b → 2d**. See the
> "2a + 2a-bis PASSED" status block below before running anything.

Shipped in this push: `probe_lxmf_propagation_node_dark`
(`watchdog_probes_gateway.py`), signal class + BOTH seeds + probes facade +
runner + `fleet_truth.py` row (ported byte-identical to MeshAnchor, whose
registry row is the only mirror slice 1 has too — MA carries no probe body) +
eval case `oracle-lxmf-propagation-node-dark-vs-rns-wide-wedge` + 9 tests.
Design notes the next session should not have to re-derive:

- **Evidence** = the same `~/.cache/meshforge/rns_nodes.json` slice 1 reads.
  Verified live 2026-07-20: both gateways carry our node under the FULL
  `rns_hash` `3968a2eeac25e2e7a7961f25842d3d85` *and* the short `id` form
  `rns_3968a2eeac25e2e7`, so the matcher accepts either.
- **Two legs**: STALE (in cache, silent past the window) vs UNHEARD (hash
  absent entirely — a wrong/truncated hash, i.e. the failure adoption itself
  introduces). Different fixes, so they are reported distinctly.
- **The honesty guard** (the part worth protecting): it fires ONLY when some
  OTHER propagation announce reached the box inside the window — positive
  proof the box can hear the class. Otherwise an RNS-wide wedge would read as
  "our node died". Mutation-verified: deleting that guard fails
  `test_lxmf_propagation_dark_holds_when_the_box_hears_no_propagation`;
  letting a forged future stamp count fresh, and collapsing the stale-cache
  HOLD into a reset, each fail their own test too.
- **Window** = 3 × the 360-min announce interval (~18h). Passive announce
  observation cannot beat that, and a stranger's interval is not ours to
  know. Detection is deliberately late-and-right for a `degraded` signal.
- **RESIDUAL, state it plainly when claiming this leg**: a node that keeps
  announcing but silently refuses to STORE still reads clean. Real
  store-and-forward proof needs a round-trip to a deliberately-offline peer,
  which nothing exercises yet.

Verified this turn: lint `exit 0`; `parity_check.py` `exit 0` (in sync);
`test_watchdog_probes/fleet_truth/mini_dudeai/regression_guards` 1174 passed;
MA `fleet_truth` 60 passed.

## STATUS: 2a + 2a-bis PASSED 2026-07-21 — store-and-forward is PROVEN

**Both gates cleared. 2b (adoption) is the next action and is UNRUN** — it was
not started because it restarts both gateways and the plan requires the
operator's "anything soaking?" answer first.

**2a — soak read-back (PASS).** moc1 `lxmd` `active`, `NRestarts=0`, up since
2026-07-20 09:37 HST = **22h24m** (the plan said ~24h; 22h with a clean journal
was judged to satisfy "survived a day" — call it if you disagree). Warning
journal over 25h: `-- No entries --`. Host healthy (load 0.13, 102G free).
Announce continuity at the CONSUMERS: **7** `Discovered RNS node: 3968a2ee`
lines in 25h on **both** moc and moc3, latest 03:37 HST, spaced exactly 6h =
the configured 360-min interval. Node self-status: store 0 B, 0 peers.

**2a-bis — store-and-forward drill (PASS, live).** Sender A on moc, receiver B
on moc3, throwaway identities under `/tmp/propdrill`, nothing touching gateway
config or `delivery_counters`.

- B (`fd87b3f39360571c42ff036fc13c1d11`) never announced. Unreachability proven
  before sending, not assumed: `rnprobe` → `Path request timed out`,
  `rnpath -t` → `No path known`. A direct delivery was therefore impossible.
- A sent with `desired_method=PROPAGATED` → `SENT` in 18s.
- Node stored it under **B's real destination hash** (verified by reading the
  first 16 bytes of the messagestore file, which is how lxmd itself indexes:
  `LXMRouter.py:558`).
- A exited. B came up, pulled → **received the exact message**, marker matched.
- Confirmed at the node, not the client: `1 propagation messages served to
  clients`, store drained 2 → 1.

**Retention correction:** `message_storage_limit = 500` is **500 MB**, not 500
messages (`0.0% utilised of 500.00 MB`). The 2a-ter note about "pushing toward
that boundary" should be sized in bytes — 288 B per small message means ~1.8M
messages, so eviction is effectively unreachable by a drill and the real
boundary worth testing is per-message (`256 KB limit`) or sync (`10.24 MB`).

**Left behind, deliberately:** one 288 B phantom message addressed to
`108ffbd1f2c010e712ef666d0903b443`, from the first (buggy) send attempt below.
Its identity has no private key anywhere, so nobody can ever fetch it. Left in
place rather than deleting files under a running node whose soak evidence
mattered; it expires on the node's own retention.

### ⚠️ Three upstream API facts this doc got WRONG — corrected live 2026-07-21

The 2a-bis API block was written from source-reading and **three of its four
calls do not behave as stated**. All three are honest_failure_modes #1 in the
upstream library (an inapplicable/failed call mapped to a valid-looking value).
The working drill is saved as `.claude/plans/propagation_drill.py` — promote
THAT for slice 3 rather than re-deriving:

1. **`set_inbound_propagation_node()` RAISES.** `NotImplementedError:
   Inbound/outbound propagation node differentiation is currently not
   implemented` (`LXMRouter.py:428`, lxmf 1.0.1+mf.1). The receiver uses
   `set_outbound_propagation_node()` too — one setter serves both directions.
2. **`RNS.Identity.from_bytes()` loads a PRIVATE key, never a public one.** Fed
   a 64-byte public key it does not fail — it silently constructs a valid-looking
   but completely different identity. This cost the first drill run: the message
   was stored at a phantom address and the pull returned `PR_COMPLETE` with zero
   messages, which reads exactly like "the node dropped it." Use
   `RNS.Identity(create_keys=False)` + `load_public_key()`.
3. **`load_public_key()` has no `return` statement.** Its docstring promises
   `True if the key was loaded, otherwise False`; it returns `None` on success
   AND failure (`Identity.py:786-802`). Never branch on its return — check that
   `identity.hash` got populated.

**The guard worth keeping** (now in the drill): before sending, assert the
computed destination hash equals B's published hash and fail LOUD on mismatch.
Without it, a misaddressed message still reports `SENT` and the node still
reports "stored" — a green path that proves nothing. That is the same
proxy-verification trap calibrated_claims rule 7 warns about.

**Diagnostic that pinpointed it**, worth reusing: poll
`router.propagation_transfer_state` against the `LXMF.LXMRouter.PR_*` constants.
`PR_COMPLETE` + zero messages = the node answered and had nothing for you
(address mismatch), which is a completely different fault from `PR_NO_PATH`,
`PR_LINK_FAILED`, or `PR_NO_ACCESS`.

## ▶ STEP 2 — EXECUTE THIS (armed 2026-07-20, earliest run 2026-07-21)

> Operator decision recorded 2026-07-20: **soak the node one day, then adopt.**
> Written by the session that shipped step 1, for a session that will NOT have
> its context. Everything needed is here; re-derive nothing from memory.
> Facts you need: node hash `3968a2eeac25e2e7a7961f25842d3d85`, hosted by
> `lxmd.service` on **moc1**, config `/var/lib/lxmd/config`.

### 2a. Soak read-back FIRST — do not adopt a node you haven't re-checked

Adoption makes this node load-bearing for LXMF delivery, so prove it survived
a day before anything depends on it. On moc1:

```bash
systemctl is-active lxmd; systemctl show lxmd -p NRestarts --value   # expect active / 0
sudo timeout 20 lxmd --config /var/lib/lxmd --rnsconfig /etc/reticulum --status
sudo journalctl -u lxmd --no-pager -p warning --since "25 hours ago"  # expect empty
df -h /; uptime                                                       # host still healthy
```

**Gate:** `NRestarts` must still be 0 (a restart loop here is the #69/#82
bind-race gate doing its job — investigate, do NOT adopt). Uptime should read
~24h. A non-empty warning journal is a stop-and-read, not a proceed.

Independently, announce continuity from a CONSUMER (announce_interval is 360
min, so expect roughly 4 in 24h — this is the leg that proves the fleet can
still find it, which is the whole point):

```bash
for h in moc moc3; do ssh $h 'sudo journalctl -u meshforge-gateway --since "25 hours ago" \
  --no-pager -o cat | grep -c "Discovered RNS node: 3968a2ee"'; done
```

**Zero on both boxes = STOP.** The node is unreachable to its future consumers;
adopting would configure a hash nobody can resolve. Note the fleet journals are
`Storage=volatile`, so absence after a box reboot is unobservable, not proof —
re-check with a live `rnstatus`/`rnprobe` before concluding anything.

### 2a-bis. PROVE store-and-forward BEFORE adopting (operator-approved 2026-07-20)

> Inserted after the operator's read of step 2c: *"store and forward pretty
> important to test out meaningfully… we need the traffic."* He is right, and
> it exposes a hole in what 2c shipped. **Run this BEFORE 2b.**

**The hole.** `probe_lxmf_propagation_node_dark` watches whether the node
**announces**. It does NOT watch whether the node **stores and forwards**. A
node that announces perfectly while silently dropping every stored message
reads *clean* on that probe, forever. In a 9-box, 2-person lab, traffic to an
offline peer essentially never happens organically — so the realistic failure
is: adopt, and the organ is quietly useless for months while every gate stays
green. That is announce-liveness standing in as a proxy for the property we
actually care about (honest_failure_modes #1 in a liveness costume).

**So invert the risk.** The plan as originally written adopts first and hopes.
Prove the node does its job while ZERO boxes depend on it — a failed drill
then costs nothing, where a failed adoption costs two gateways.

**Gate: if the drill does not deliver, do NOT run 2b.** A node that cannot
store-and-forward is worse than no node: adopting it would silently swallow
offline-peer mail that today at least fails loudly.

The drill (one-shot, manual, no gateway config touched):

1. Two throwaway LXMF identities, A (sender) and B (receiver), on separate
   boxes — NOT the gateway identities, so nothing pollutes real counters.
2. B is **not running**. Confirm it is genuinely unreachable first (`rnprobe`
   to B's destination fails) — otherwise a DIRECT delivery would pass the
   drill while proving nothing about propagation.
3. A sets the outbound propagation node to ours and sends with the PROPAGATED
   method, so the message goes to the node's store rather than to B.
4. A exits. Wait past a path-expiry window so no direct route survives.
5. Bring B up, point it at the same node, and pull. **B must receive it.**

API facts, verified live 2026-07-20 against the installed `lxmf 1.0.1+mf.1`
(do not re-derive, and do not guess these — an earlier draft of this arc
invented a shape and was wrong):

```python
LXMF.LXMessage.PROPAGATED == 3          # vs DIRECT == 2, OPPORTUNISTIC == 1
LXMF.LXMessage(dest, source, content, title, desired_method=LXMF.LXMessage.PROPAGATED)
router.set_outbound_propagation_node(destination_hash)      # sender side
router.set_inbound_propagation_node(destination_hash)       # receiver side
router.request_messages_from_propagation_node(identity, max_messages=0)   # the pull
```

Our node: `3968a2eeac25e2e7a7961f25842d3d85` (lxmd on moc1).

⚠️ **The existing traffic generator cannot do this** — checked, not assumed:
`src/lab/lxmf_multi_user_synth.py:324` builds `LXMF.LXMessage(...)` with **no
`desired_method`**, so every synth message is DIRECT, and `grep -rn
propagation src/lab/*.py` returns NOTHING. The whole synth soak exercises
live-peer delivery only. That is exactly why store-and-forward is untested
today, and it is why the drill needs its own sender/receiver rather than a
flag on the existing tool.

### 2a-ter. Slice 3 — automate the drill; it IS the traffic generator

Once the drill passes once by hand, the durable form is the same artifact the
operator wants for load/telemetry. Clone the proven pattern rather than
inventing one: `scripts/lab_synth_soak_fire.sh` +
`meshforge-synth-soak.timer` write a `pass_envelope` JSON that
`probe_synth_soak_degraded` consumes, with a SILENCE leg for when the
exerciser itself stops. The propagation analogue is that plus a deliberately
offline receiver. Each run also yields the send→stored→synced-on-return
latency, which is the real SLO of store-and-forward and something nothing in
the fleet measures today.

Design constraints to honour from the start (each is a trap this repo has
already paid for once):

- **Tag the synthetic traffic and keep it OUT of `delivery_counters`.** The
  whole #74 arc was making `confirmation_rate` honest; feeding it manufactured
  messages would re-corrupt the exact metric we fixed.
- **`message_storage_limit = 500` was picked blind.** A drill that also
  pushes toward that boundary is how we learn what eviction actually does.
  Finding out during a real outage is the bad version.
- **Silence must be a failure.** Same lesson as synth-soak: a fixed-cadence
  generator that stops emitting is indistinguishable from "nothing to report"
  unless staleness is itself the fault.
- **Atomic publish.** `lab_synth_soak_fire.sh` writes `.partial` then renames
  precisely because a direct `>"$out"` left the newest envelope unparseable
  mid-run and false-fired the probe (the 2026-06-15 moc incident). Copy that.
- Volume, later: rotating sender/receiver pairs across the 9 boxes exercise
  real multi-hop RNS paths rather than a loopback — closer to what the 30-mile
  deploy will face ([[project_openwrt_remote_deploy_plan_2026_07_15]]).

### 2b. Confirm nothing is soaking, then adopt

⚠️ Adoption restarts `meshforge-gateway` on moc and moc3. The gateway's wedge
watchdog calls `os._exit(2)`, so a restart mid-soak makes new code activate
non-deterministically. Ask the operator "anything soaking?" — and never run
`fleet_sync.sh` for this (it restarts gateways fleet-wide on any `^src/` diff).

Per gateway box (moc, then moc3 — one at a time, verify between):

```bash
# gateway.json lives under the gateway's config dir; find it, don't assume:
sudo grep -rl '"propagation_node"' /etc/meshforge /var/lib/meshforge ~/.config/meshforge 2>/dev/null
# set rns.propagation_node = "3968a2eeac25e2e7a7961f25842d3d85", then:
sudo systemctl restart meshforge-gateway
```

Verify at the consumer of record — the gateway's own log proving the LXMF
router accepted the outbound propagation node, NOT that the file contains the
string:

```bash
sudo journalctl -u meshforge-gateway --since "3 min ago" --no-pager -o cat | grep -i propagation
```

Then prove DELIVERY, not path presence (2026-07-19 lesson: after a rolling
rnsd restart a box can latch a stale multi-hop path that `rnpath -t` reports
as fresh while delivery is 100% lost):

```bash
rnprobe lxmf.propagation 3968a2eeac25e2e7a7961f25842d3d85   # from moc AND moc3
```

The real end-to-end proof is an LXMF message to an OFFLINE peer that lands when
it returns. If a cheap version of that isn't available, say so — that claim
stays BELIEVED, and name it as such.

### 2c. The shape-A probe — ✅ ALREADY SHIPPED 2026-07-20, do not rebuild it

> Skip to 2d. `probe_lxmf_propagation_node_dark` is in main; see the STATUS
> block at the top of this file for its design and the mutation evidence.
> What remains is to confirm it is LIVE at the consumer of record after
> adoption — see the "Done looks like" line, which is unchanged.
>
> The original requirement is kept verbatim below because it explains WHY the
> ordering matters, and that reasoning is still load-bearing.

The moment `propagation_node` is set, `probe_lxmf_propagation_unused` goes
INERT by design — so without this, the fleet trades a watched gap for an
UNWATCHED dependency, which is strictly worse than before step 2. Do not split
these across commits.

New probe (`watchdog_probes_gateway.py`, alongside its slice-1 sibling):
configured propagation node stopped answering. Honest self-guards, non-negotiable:

- `propagation_node` unset → INERT (the slice-1 probe owns that state; one
  fault, one owner);
- gateway not installed/running → INERT;
- observation source unreadable → indeterminate, streak HELD (an ABSENT file is
  an observation; an UNREADABLE one is a failure to observe — never collapse them);
- volatile-journal absence is NOT node-absence;
- 2-tick debounce; `degraded`; escalation-only seed rule.

Feed EVERY closed-enum gate — they fail until fed, which is the system working:
`SIGNAL_CLASSES` in `watchdog_probe_core.py`, the documented-enum literal in
`tests/test_watchdog_probes.py`, BOTH seeds
(`configs/mini_dudeai_rules.{federator,fleet_gateway}.json`), the probes facade
`__all__` + `watchdog_probes_drift` re-export, `watchdog_runner`. Plus: a
mutation check that the guard catches a regression, a registry row in
`fleet_truth.py` (byte-locked → copy to MeshAnchor), an eval case in
`evals/local_brain/seed.jsonl` (honest_failure_modes #10), and a
`TEMPLATE_PROVENANCE`-style honesty pass — `honest_status.sh` will tell you.

### 2d. Ship it

lint + `parity_check.py` + FULL suite **after the final edit** (not before);
push; `wait_for_ci.sh`; `fleet_pull.sh`; restart `meshforge-watchdog`
fleet-wide; `python3 /opt/meshforge/scripts/promote_seed_rules.py --apply` per
box (ABSOLUTE path — remote ssh cwd is $HOME, not the repo). Verify the new
probe's disposition at the live watchdog via `/api/fleet/truth`, then
`honest_status.sh` (`exit 0`; UNKNOWN is never a pass).

**Done looks like:** `lxmf_propagation_unused` INERT on moc/moc3 (adoption took)
and the new shape-A probe `active`/healthy (the dependency is watched). If the
first is INERT and the second didn't ship, you are worse off than yesterday.

## Slice 2 — what a fresh session needs to know FIRST (researched 2026-07-20)

**The client/server asymmetry is the whole shape of this slice. Do not assume
symmetry.**

- **CLIENT side is FULLY WIRED already.** Two live consumers call
  `set_outbound_propagation_node()`:
  `src/gateway/_rns_bridge_connection.py:305` (the gateway's LXMF router — the
  main path) and `src/gateway/meshtastic_broadcast_bridge.py:556`, both fed from
  `GatewayConfig.rns.propagation_node` (`src/gateway/config.py:434`, default
  `""`). NomadNet has its own `[client] propagation_node` handler
  (`src/launcher_tui/handlers/_nomadnet_config_ops.py:306`).
  → **Adopting an existing node is genuinely just a 32-hex-char config value +
  a gateway restart.** No code needed.
- **SERVER side does NOT exist in MeshForge.** `grep` for
  `enable_propagation|propagation_node=True|announce_propagation` across `src/`
  returns NOTHING. MeshForge can USE a propagation node; it has never been able
  to BE one. Standing one up on our own rnsd is therefore an OPS/BUILD task
  (NomadNet node-mode or an LXMF propagation daemon on an existing rnsd box),
  not a config flip — size it honestly before promising it.

**So the operator's decision is really three:**
1. Adopt one of the ~15 heard nodes (fast, but a stranger holds our metadata);
2. stand up our own (better trust posture, real work, and it becomes a fleet
   service someone must keep alive — a new organ with its own liveness needs);
3. leave it, and let the probe keep the gap visible (a legitimate, honest
   outcome — the row-3/row-8 "accept" precedent).

**If (2) is chosen, remember**: a propagation node the fleet depends on is
itself an organ that can go dark, so it needs its own liveness leg. And the
detector shipped in slice 1 goes INERT the moment `propagation_node` is set —
nothing currently checks that a CONFIGURED node still answers (that would be a
shape-A leg, and it should land WITH the adoption, not after it).

**Deploy constraint (unchanged)**: adoption edits `gateway.json` and needs a
`meshforge-gateway` restart. Never mid-soak — the gateway's wedge watchdog
calls `os._exit(2)`, so new code activates non-deterministically.

## Design constraints for the detector

- **Observation source must be root-readable and config-free.** Candidates, in
  preference order — verify before choosing:
  a. the map's `/api/status` or node directory, if the RNS node inventory
     carries the LXMF_PROPAGATION type (node_tracker records it: "Discovered
     RNS node ... [LXMF_PROPAGATION]");
  b. the LXMF router's own on-disk state under the gateway's LXMF storage;
  c. the gateway journal (precedent: `probe_mqtt_root_drift` is journal-only)
     — ⚠️ fleet boxes run `Storage=volatile`, so a journal window is short;
     never read absence-of-lines as absence-of-node.
- **Honest failure modes (walk `.claude/rules/honest_failure_modes.md`):**
  - no propagation announce seen → INERT, never "no node exists" (absence of
    evidence, especially on a volatile journal);
  - `propagation_node` already set → INERT (adopted; a separate leg could
    later check the configured one is still reachable = shape A);
  - gateway not installed/running on this box → INERT;
  - observation source unreadable → indeterminate, streak HELD;
  - 2-tick debounce; `degraded`; **escalation-only** seed rule (an unadopted
    capability is lost coverage, not an outage — row 5 + row 9 precedent).
- One fault, one owner: this must go INERT the moment the config exists, the
  way `aredn_organ_undeclared` yields to the configured-source legs.

## Execution checklist (mirrors row 5, which worked)

1. Pick the observation source by READING live data first (row 3/7/9 lesson:
   the premise is usually stale).
2. Probe + honest self-guards; new signal class in `watchdog_probe_core.py`.
3. Feed EVERY closed-enum gate — they will fail until fed, which is the system
   working: `SIGNAL_CLASSES`, the documented-enum literal in
   `tests/test_watchdog_probes.py`, BOTH seeds
   (`configs/mini_dudeai_rules.{federator,fleet_gateway}.json`), the probes
   facade `__all__` + `watchdog_probes_drift` re-export, `watchdog_runner`.
4. Tests incl. a mutation check that the guard actually catches a regression.
5. Registry row in `fleet_truth.py` (byte-locked → copy to MeshAnchor) + eval
   case in `evals/local_brain/seed.jsonl` (honest_failure_modes #10).
6. lint + parity + FULL suite AFTER the final edit; push; `wait_for_ci.sh`.
7. `fleet_pull.sh`, restart `meshforge-watchdog` fleet-wide, then
   `python3 /opt/meshforge/scripts/promote_seed_rules.py --apply` per box
   (ABSOLUTE path — remote ssh cwd is $HOME, not the repo).
8. Verify at the CONSUMER OF RECORD — the live watchdog's disposition via
   `/api/fleet/truth`, not the wiring. Then `honest_status.sh`.

## Session gotchas worth carrying (earned today)

- MF025's 1,500-line cap is a SPLIT trigger, never a number to raise.
- After a split, every test seam must patch the NEW module or it stubs nothing.
- CI runs minimal-deps: an assertion that depends on `import RNS` succeeding
  passes locally and fails in CI.
- An ABSENT file is an observation; an UNREADABLE file is a failure to
  observe. Never collapse them.
- The repo's own honesty invariants caught a dead endpoint before it shipped —
  when a test objects to a detector's target, believe the test.
