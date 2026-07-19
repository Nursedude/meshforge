# Federation watched per-vantage — structural-dark row 6 (2026-07-19)

> Closes the `federation_digest_federator_only` blind spot from the Known Blind
> Spots registry (`STRUCTURAL_DARK` in `src/utils/fleet_truth.py`, byte-locked
> MF↔MA). Row narrowed, not deleted — the residual is named at the bottom.

## The blind spot

`FederationPeerSource` (`src/mini_dudeai/presets/meshforge_fleet.py`) polls
`http://localhost:5000/api/status` — **the box's OWN map**, not a remote one.
Every map-running box already computes `federation.peer_status` locally, but
only the federator (VolcanoAI) wired the source; every other box set
`MINI_DUDEAI_ENABLE_FEDERATION=0`. So federation health had exactly ONE
vantage, and the fleet could not tell these two apart:

- **peer C is down** — every vantage sees C unhealthy;
- **the path to C from box A is broken** — A sees C unhealthy, B does not.

That distinction is free: the data was already being computed on six boxes and
thrown away.

## What changed

`MINI_DUDEAI_ENABLE_FEDERATION=1` on every **map-running** box, and the two
federation rules ported from the federator seed into
`configs/mini_dudeai_rules.fleet_gateway.json`.

**The gating fact is "does this box run a local :5000 map?" — nothing else.**
The env lives in `~/.config/meshforge/mini_dudeai.env` (pulled in by the user
unit's `EnvironmentFile=`), **not** in the unit file itself; grepping the unit
finds nothing and reads as "unset". The consumer of record is the live process:

```bash
tr '\0' '\n' < /proc/$(systemctl --user show meshforge-mini-dudeai -p MainPID --value)/environ \
  | grep MINI_DUDEAI_ENABLE
```

## The two policies that keep this quiet

**1. Escalations, never pages, off the federator.** The gateway seed's
`federation_peer_unhealthy_unexpected` uses `propose_escalation` — visible in
the mini brief and `/fleet` escalations, and nothing else. Box-down PAGING
ownership stays exactly where it was: the manager's `fleet_offline_check`
(3-fail → ntfy) plus `manager_deadman` for the manager itself. Without this,
one dead peer would fan out to a phone page from every vantage that sees it —
N pages for one fault. Pinned by
`TestFederationPerVantageRow6::test_gateway_federation_rules_never_page`.

**2. The digest half closes by DECLARATION, not by wiring.**
`situation_digest.md` is a federator artifact; watching its staleness on a box
that never writes one is meaningless, not merely noisy.
`MINI_DUDEAI_ENABLE_DIGEST=0` stays everywhere but the federator, by design.

**3. The soak canary's suppression ports too.** moc3's permanent federation
backoff is DELIBERATE (gateway-only box, RNS 1.3.8 soak canary) and shows up in
every vantage's local view — `moc3_federation_backoff_known_normal`
(`annotate_digest`, so the deliberate non-escalation still leaves an on-box
witness) now exists in BOTH seeds. **Retire the two copies together** when the
RNS roll ends and moc3 rejoins federation.

## The trap: never wire this on a map-less box

A box with no local `:5000` emits a `source_error` Condition (subject
`federator`) **every tick**, pinning `src_errors` in its brief and every
rollup. That is the declared-absent-vs-error confusion `honest_failure_modes`
exists to stop: a box that deliberately runs no map is not blind, it has
nothing to see. It does not page (the `source_error_watchdog` rule globs
subject `watchdog`, not `federator`) — it just quietly makes the box look
unhealthy forever.

**Before enabling on any box, check the vantage is real:**

```bash
curl -s -m 8 http://localhost:5000/api/status | jq '.federation.peer_status | length'
```

- errors / no `federation` key → **leave it 0**
- `0` → safe to enable but **inert today** (federation configured, no peers)
- `>0` → a real vantage

## Fleet state at closure (2026-07-19)

| Box | Vantage | Setting |
|-----|---------|---------|
| VolcanoAI (federator) | 6 peers | 1 (unchanged; also the only DIGEST=1) |
| moc | 4 peers | 1 |
| moc1 | 3 peers | 1 |
| moc2 | 3 peers | 1 |
| kiai | 5 peers | 1 |
| moc4, moc5 | 0 peers | 1 — inert, self-activating if peers are configured |
| moc3 | map stopped for the RNS soak | **0** — no vantage |
| meshanchor-server | `/api/status` has no `federation` key (MA's own NOC) | **0** |

## Residual (why the row is narrowed, not removed)

A box running no local map has no vantage at all, and a box whose federation
lists zero peers is inert — neither is covered. Both are visible as table rows
above rather than as silence; when moc3's map comes back at the RNS roll, flip
it to 1 and it joins.
