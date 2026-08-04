# Structural-dark burndown — closing the rows where absence looked like health

> The →R half of the 2026-07-20 structural-dark burndown. The arc shipped its
> fixes and its eval cases; this is the knowledge entry those cases retrieve
> against. Doctrine: a resolved incident compiles to THREE artifacts — the
> probe/fix (→R), the runbook (→R), and the eval case (→L).

"Structural dark" = a place where the *shape* of the code or the detector makes
a real fault **unobservable**, so a silent monitor and a healthy system look
identical. Two rows below; both are cases where **a falling number or a quiet
probe was not good news**.

---

## Row 2 — `send_to_rns` returned a bare bool that collapsed five outcomes

`send_to_rns` used to return a bare `bool`. That `False` collapsed **five
distinct outcomes** into one value:

    not_connected · no_lxmf_source · circuit_open · no_path · send_error

The mesh oracle's RNS leg called it as `bool(send_to_rns(...))`, so a **genuine
send crash landed in the BENIGN bucket** and `oracle_delivery_degraded`
**under-counted real failures**.

**Cure**: `RnsSendResult` — a frozen dataclass with `__bool__`, so every
truthiness call site is unchanged — plus a `.reason` the responder records into
the audit log. The vocabulary is pinned as `RNS_SEND_REASONS` in
`src/gateway/bridge_send_mixin.py`.

### Two traps this row guards

**(1) Reading a SHRINKING `benign_rns_ambiguous` as fewer failures.** That
counter measures **what we cannot TELL APART**. It falls because reasons are
now *named*, not because delivery improved. The real-failure count can **rise
at the same time**, and that is **the fix working**. So
`benign_rns_ambiguous → 0` after a gateway deploy means the ambiguity was
removed, not that RNS delivery got better.

**(2) Assuming a named benign reason is a failure.** A named `no_path` or
`circuit_open` stays **excluded** from the confirmable failure set — it is a
benign non-delivery, **not a failure**. **Only `send_error` is a failure.**

⚠️ Deliberate non-port: MeshAnchor's `bridge_send_mixin` is diverged (no
`bounded_call`/`_on_wedge` machinery) and has no `bridge_rns_events_mixin`, so
it has no consumer for the richer result.

---

## Row 5 — an unadopted AREDN organ, and why absence could never close it

The AREDN organ had two watchers, and **both required a STATEMENT the operator
had to remember to make**:

- `aredn_node_ips` in `map_settings.json` (the configured-source legs), or
- `organ_expectations.aredn` in `deployment.json` (the role-aware leg, added
  2026-07-19).

So the **2026-06-12 origin state** — an AREDN-site box found with its organ
silently dormant while every "AREDN" node on its map came from the *worldmap
fallback* — was **still undetectable** on a box that was never configured and
never declared. You cannot detect a forgotten declaration by looking for a
missing declaration.

**Cure: `probe_aredn_organ_undeclared`, which closes the row with POSITIVE
EVIDENCE only.** It fires when `localnode.local.mesh` **resolves** — which it
cannot do without an AREDN node serving DNS on that LAN — **AND** that address
answers `sysinfo`, while the box carries **neither** statement. Measured
2026-07-20: resolves on the AREDN-site box to exactly its configured node IP;
does **not** resolve on a same-fleet box with no AREDN path. Lives in
`src/utils/watchdog_probes_aredn.py`.

### Three traps this row guards

**(1) Trying to close the row by absence.** "No declaration" cannot distinguish
a **forgotten organ** from a box that has **no AREDN anywhere near it**. That
is why the detector waits for a **node to answer**, not for a config to be
missing.

**(2) Reading a silent probe as "no AREDN organ exists."** A box with **no
AREDN LAN is correctly invisible because there is nothing to observe** — not
because it was checked and cleared. So a fleet box showing no
`aredn_organ_undeclared` signal and no AREDN nodes on its map is **not** thereby
known-fine; on that box the question is simply unasked.

**How you WOULD know a box at an AREDN site had never been configured**: the
positive-evidence leg is exactly that test — does `localnode.local.mesh`
resolve and answer `sysinfo` from this box? If yes and neither statement
exists, the organ is **dormant**, and the probe says so.

**(3) Expecting a page.** It is **escalation-only** by seed policy: an
unadopted organ is **lost coverage, not an outage**, and by construction has
already been that way a long time.

**Companion fix**: an **ABSENT** `map_settings.json` used to collapse into the
same "unreadable" as a **corrupt** one, so the probe returned indeterminate
*before* the declaration leg — which meant **wiping the whole settings file**,
the strongest form of the wipe class, was invisible on a declared box.

---

## The shape both rows share

The domain is excellent at watching **what it was told about** and had almost
nothing for **what it was never told about**. The 2026-07-20 optional-organ
sweep measured it: of ~50 watchdog signal classes, exactly **ONE** watched for
an available-but-**UNADOPTED** capability (`aredn_organ_undeclared`) — 31 were
liveness, 13 drift, 4 configured-but-broken. `probe_lxmf_propagation_unused`
became the second such watcher; see `propagation_leg_triage.md`.

| ask | wrong reading | honest reading |
|---|---|---|
| an ambiguity counter fell | delivery improved | we can now TELL THINGS APART; real failures may rise |
| a named `no_path` | a failure | benign non-delivery; only `send_error` counts |
| a probe is silent | the organ is healthy | it may be INERT, indeterminate, or have nothing to observe |
| no declaration found | nothing to configure | indistinguishable from never-configured — demand positive evidence |

Related: `propagation_leg_triage.md`,
`.claude/rules/honest_failure_modes.md` (#2 absence of evidence is not evidence
of absence, #4 reader and writer wire together, #9 every swallow leaves a
witness).
