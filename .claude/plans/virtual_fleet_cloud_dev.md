# Cloud dev for the MeshForge domain — duplicating the living lab (2026-08-27)

> Operator question: the dev environment IS the living lab — built on
> real-time failures. How do we duplicate that for cloud development?
> Honest answer up front: you can duplicate the DRILL, not the DISCOVERY.
> The physical lab's irreplaceable output is unsolicited failure —
> physics, power, RF, SD wear. Cloud gets everything else.

## Maps posture (grounded 2026-08-27)

The public map is a VPS fed by OUTBOUND rsync (`meshforge-cloud-push.timer`
600s on the manager → `push_snapshot.sh`; `cloud_map_freshness.sh` probes
the whole chain end-to-end). Verified healthy post-storm: data.geojson 19s
old. Storm failure mode was STALENESS (fleet couldn't push), not downtime —
and the architecture is already CGNAT/Starlink-ready because nothing dials
in. With Starlink + battery/solar the push chain simply keeps running
through grid outages, which is exactly the operator's instinct: no map
redesign needed — the reliability work IS the power/uplink work.

Two hardening items:
1. **The page must wear its data age** (honest_failure_modes: absence of
   fresh data must not read as fresh). Verify the public page shows
   "data as of <ts>" / goes visibly stale-bannered when Last-Modified
   ages out; add it if absent. A frozen map that looks live is the lie
   viewers act on during a storm.
2. The local NOC maps (:5000 per box) are the LAN-side leg and already
   WAN-independent — keep the pair (VPS for the world, :5000 for the
   bench) rather than adding a third path.

## Duplicating the living lab — three tiers

**Tier 1 — failure corpus (mostly exists, formalize the 4th artifact).**
Every resolved incident already compiles to probe + runbook + eval case
(hfm #10). Add the FIXTURE: the actual corpse bytes (zero-byte ratchets,
NUL'd jsonl lines, truncated msgpack) into tests/fixtures/, so cloud CI
regression-tests against REAL failure artifacts forever, not synthetic
approximations. Cost: minutes per incident, at incident time.

**Tier 2 — the virtual fleet (the real answer).** A containerized N-node
fleet in the cloud: one rnsd per container linked over TCPInterface,
gateway + lxmd + echo + tracer running the SAME lab organs — the drill
machinery (gateway_rt_canary, gateway_resource_canary, synth soak,
propagation soak) was built against the real fleet and drives a virtual
one unchanged. meshtasticd runs `--sim` (already supported in config) in
place of RF. On top, a chaos layer reproducing every SOFTWARE class the
lab has taught us:
  - SIGKILL mid-write → the zero-byte state-file class
  - libfaketime / clock steps → the clock-skew class
  - tc/iptables partitions → the WAN-outage + path-decay classes
  - disk-full, OOM, thread-limit caps → the resource classes
This gives cloud sessions (and CI, at PR time) live-ish drills between
"10,950 mocked tests" and "the physical fleet" — today that middle tier
does not exist, so every integration truth needs the real lab.

**Tier 3 — cloud sessions with lab reach (careful tier).** Post-Starlink,
an outbound anchor (the alaula reverse-tunnel pattern generalized, or WG)
can give a cloud session read-only probe access to the REAL fleet.
Doctrine: artifacts-out first (the fleet already pushes journals, ledger,
memory, vault outbound — cloud dev consumes those); read-only probes
second; WRITE on the fleet stays with sessions ON the fleet (blast-radius
rule — a cloud agent must not be able to restart a radio box).

## The division of labor this buys

  cloud  = development at scale, regression, ports, review, drills of
           KNOWN classes (Tier 1+2), consuming lab artifacts (Tier 3)
  lab    = discovery: unsolicited physics — the CH341 leak, the PA that
           wasn't dead, the SD that lies, the storm
Move everything but discovery to the cloud so lab/frontier attention
concentrates on what only the lab produces. The virtual fleet is the one
NEW build; Tiers 1 and 3 are formalizations of pipes that already run.

## First concrete steps (ranked)
1. pw2lab rebuild = the LAB-ZERO restore drill (in progress, operator).
2. Fixture-per-incident rule added to the hfm #10 artifact list.
3. ~~Prototype the virtual fleet~~ DONE 2026-08-27 (process-orchestrated,
   `lab/virtual_fleet.py`): smoke green (tracer PING/ACK), and `canary`
   runs the REAL gateway (bridge_cli) in a sandbox HOME against the
   vfleet-gw node — gateway_rt_canary VERDICT OK confirmed=4.0s,
   reproduced. The build itself earned its keep: it live-caught an
   in-process RNS singleton race (node_tracker attaches first and pins
   the instance for the whole process — the sandbox gateway briefly
   attached to the REAL mesh, now guarded by a runtime breach detector
   in the canary flow + the process-wide MESHFORGE_RNS_CONFIGDIR
   resolution root), and flushed two hardcoded-path drifts (rpc-key
   alignment check vs the ReticulumPaths SSOT; fixed /tmp client-config
   collision). CHAOS LAYER SHIPPED same day (`chaos` command, 3 drills,
   reproduced 2x): baseline canary; the Lala zero-byte class end-to-end
   (SIGKILL gateway + real 0-byte ratchet corpse -> quarantine guard
   observed firing -> canary green); transport partition via SIGSTOP
   (canary MUST fail during the fault — a green canary through a
   partition is the lie — and heal after SIGCONT). Fault injector is
   containment-tested (planted symlink escape refused). CI WIRED 2026-08-27: the 'Virtual Fleet (smoke + chaos)'
   job runs both on every push/PR (green in 1m43s, run 33147645481) —
   its first two runs each caught a real SSOT drift (sandbox_check's
   hardcoded /etc storage probe; the channel resolver's read-leak to the
   real meshtasticd). Not a required context until it soaks. Next:
   clock-skew drill (libfaketime).
4. Public map staleness banner check (item 1 above).
