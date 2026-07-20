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

### 2c. The shape-A probe MUST land in the same push

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
