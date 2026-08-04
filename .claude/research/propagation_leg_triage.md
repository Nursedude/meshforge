# LXMF propagation leg — triage runbook

> The →R half of the 2026-07-20/21 propagation arc. The arc shipped its probes
> and its eval cases; this is the knowledge entry those cases retrieve against.
> Doctrine: a resolved incident compiles to THREE artifacts — the probe (→R),
> the runbook (→R), and the eval case (→L). Shipping 2 of 3 is what made the
> weekly `local_brain_eval` gate fail honestly.

An LXMF **propagation node** holds a message for a peer that is offline right
now and delivers it when the peer returns. Without one, a message to an offline
peer fails outright. Four watchers cover the leg, and their whole value is that
they answer *different* questions — conflating them is the recurring triage
error.

| probe | watches | fires when |
|---|---|---|
| `probe_lxmf_propagation_unused` | nodes are AVAILABLE but we adopted none | `gateway.json` `rns.propagation_node` empty while the node cache holds heard propagation nodes |
| `probe_lxmf_propagation_node_dark` | the ADOPTED node still ANNOUNCES | our configured node goes quiet (STALE) or was never heard (UNHEARD) |
| `probe_propagation_soak_degraded` | the adopted node actually STORES AND FORWARDS | the hourly drill's envelope fails, or the drill goes silent |
| `probe_aredn_organ_undeclared` | (companion shape, different organ) | see `structural_dark_burndown.md` |

All live in `src/utils/watchdog_probes_gateway_lxmf.py` and
`src/utils/watchdog_probes_propagation.py`.

---

## 1. "Propagation nodes are available but unused" — adoption is a TRUST decision

`probe_lxmf_propagation_unused` fires when the gateway has *heard* propagation
nodes announce (measured: 14–15 within 6 h on both gateway boxes) while
`gateway.json` `rns.propagation_node` is empty.

**Do not just point it at the nearest node it found.** A propagation node sees
**stored-traffic metadata** — who is messaging whom, and when. Adoption is a
trust decision, not a mechanical fix, which is why the probe names the gap and
deliberately declines to prescribe a node. MeshForge's answer was to stand up
**our own** node (`lxmd` on moc1) rather than adopt a stranger's.

**Adopting alone is strictly worse than staying unadopted.** It trades a
WATCHED gap for an UNWATCHED dependency. Adoption therefore ships *with*
`probe_lxmf_propagation_node_dark`, never before it. The moment
`rns.propagation_node` is set, the unused probe goes **INERT** by design — one
fault, one owner.

**Silence from this probe is not "propagation is fine."** It is INERT wherever
there is no `gateway.json` or no node cache, and a **stale cache HOLDS** rather
than claiming availability from dead bytes. So a box showing no such signal may
simply have no gateway organ at all.

⚠️ Adoption edits `gateway.json` and needs a `meshforge-gateway` **restart** —
never mid-soak, because the wedge watchdog's `os._exit(2)` would activate new
code non-deterministically.

---

## 2. Node dark vs an RNS-wide wedge — silence is not proof of health

`probe_lxmf_propagation_node_dark` has **two legs with different fixes**:

- **STALE** — the node is in the cache but its newest announce is older than
  several announce periods. It answered once and stopped.
- **UNHEARD** — the configured hash is absent from the cache entirely. Almost
  always a **wrong or truncated hash** — precisely the failure adoption itself
  introduces.

**The guard that makes it honest**: it fires ONLY when some *other* propagation
announce reached this box inside the window. That is positive proof the box can
currently **hear the propagation announce class**. Without it, an **RNS-wide
transport wedge** would be relabelled as this node's death. With it, the wedge
HOLDS as **indeterminate** and stays with its own owners
(`rns_rpc_unresponsive` and friends). This is honest_failure_modes #2 applied
to a liveness probe: **unobservable is not dark.**

So "the watchdog is silent about it" does **not** mean store-and-forward is
working. Silence can mean INERT, indeterminate, or held.

**Evidence is the durable operator-owned cache**
`~/.cache/meshforge/rns_nodes.json` — **never the journal**. Fleet boxes run
`Storage=volatile`, so an absence of log lines proves nothing.

**Detection is bounded below by roughly 18 h**, because a propagation node
announces every **360 minutes** and a stranger's interval is not ours to know.
For a degraded-capability signal, being late and right beats fast and wrong.
It is `degraded`, escalation-only: store-and-forward stopping degrades delivery
to OFFLINE peers only; live delivery is unaffected.

---

## 3. Node dark RIGHT AFTER a restart — it is the cache gap, not your hash

**Live 2026-07-21.** The UNHEARD leg fired minutes after the gateway restart
that *adoption itself requires*, claiming the configured node had NEVER been
heard. **It was FALSE**, and the hash was fine.

Root cause was a **writer with no reader** (honest_failure_modes #4):
`node_models.to_dict()` wrote `service_type`, but `node_tracker._load_cache()`
dropped it. Every restart therefore erased the RNS service type of every cached
node until it announced again — up to a propagation node's **360-minute**
interval. The probe matches on `service_type`, so it was blind to a node the
box had heard 7× in 25 h and had delivered a store-and-forward message through
ten minutes earlier. Measured: **2 of 9,115** cached entries carried
`service_type`, the oldest stamped *after* the restart.

**TWO defects were needed, and the first fix alone did not cure it:**
(a) the loader dropped `service_type`, and (b) `_merge_node()` never refreshed
`service_*` either, so an announce from an ALREADY-KNOWN node did not restore
it. Verified live: after the next announce the entry had a fresh `last_seen`
and `service_type` **still None**, and the probe kept firing. It was
**unrecoverable once lost, not self-healing** — the prediction "it heals on the
next announce" was wrong, and the observation corrected it the same day.

**Triage by the RESTART CLOCK, not by re-typing the hash.** If the gateway
restarted within ~6 h and the node is otherwise alive — it announces,
`lxmd --status` shows it serving, a drill delivers — it is this cache gap.

⚠️ **`rnprobe lxmf.propagation` is NOT a delivery test.** It reports **100%
packet loss against a healthy node**, proven by a control from a box that had
just completed a full round-trip through it. Do not treat that loss figure as
evidence of anything.

---

## 4. `propagation_soak_degraded` while `node_dark` is clean — announcing ≠ working

The two probes are **complementary and must not be conflated**:

- `node_dark` firing → the node stopped **announcing** (or the hash is wrong).
- `propagation_soak_degraded` firing while `node_dark` is clean → the node is
  alive and announcing but is **NOT doing its job**: it is not storing and
  forwarding.

`node_dark` watches announces; it **cannot see** whether the node stores and
forwards. A node announcing perfectly while silently dropping every stored
message reads clean on it forever — announce-liveness standing in as a proxy
for the property actually depended on.

The gap was structural in a 2-person lab: traffic to an OFFLINE peer essentially
never happens organically, so the realistic failure was adopting the organ and
having it be quietly useless for months with every gate green. The existing
exerciser could not close it either — `lxmf_multi_user_synth` builds every
`LXMessage` with no `desired_method`, so all its traffic is **DIRECT**.

**Cure**: an hourly drill that MANUFACTURES offline-peer traffic — a receiver
that never announced, so no direct path can exist, is sent a **PROPAGATED**
message and then pulls it back. `probe_propagation_soak_degraded` consumes it
with two legs:

- **ENVELOPE** — `pass_envelope` false.
- **SILENCE** — newest `prop-*.json` older than ~2.5 cadences. For a
  fixed-cadence generator, **going quiet IS the failure.**

⚠️ A **stale passing envelope must fire SILENCE**, never read clean — the
exerciser-died-and-left-a-happy-file shape. Mutation-pinned by
`test_propagation_soak_a_stale_PASS_still_fires_silence`.

---

## Quick decision table

| symptom | read it as |
|---|---|
| unused fires | available, unadopted — a trust decision; prefer our own node |
| unused silent | may be INERT (no gateway/cache) or held — not "fine" |
| node_dark UNHEARD, gateway restarted <6 h ago | the `service_type` cache gap; check the restart clock, not the hash |
| node_dark UNHEARD, no recent restart | genuinely wrong/truncated hash |
| node_dark STALE | the node answered and stopped |
| node_dark indeterminate/held | we could not hear the propagation class at all — suspect RNS-wide wedge, its own owners |
| soak_degraded + node_dark clean | node announces but does not store/forward |
| soak SILENCE | the drill itself died — never read the last PASS as current |

Related: `structural_dark_burndown.md` (the sibling shape — watching for what
we were never told about), `.claude/rules/honest_failure_modes.md` (#2
unobservable ≠ dark, #4 reader/writer wire together).
