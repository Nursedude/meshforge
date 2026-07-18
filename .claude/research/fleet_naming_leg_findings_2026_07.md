# Fleet-naming / DNS-leg findings — July 2026

Groundable lore for the offline oracle / local tier (honest_failure_modes
point 10: a resolved incident compiles to a probe/rule, a lore entry, AND a
tier-L eval case). Both findings came out of the 2026-07-17 `mf.internal`
DNS-leg rollout (client drop-ins on 8 boxes, config migration off the naming
audit, per-box drift cron). Full operational record: plan §9 of the 30-mi
remote-deploy plan (not a corpus root — this doc is the version the local
oracle can retrieve and cite).

Both are honest-failure-modes classes caught LIVE during deployment, fixed
same-day with RED-proven tests.

---

## aredn-gate-raw-alias — two consumers of one setting, one translated (2026-07-17)

Switching `aredn_node_ips` in `map_settings.json` from a raw IP to the fleet
alias `hap` made the map's AREDN source report `unreachable` (fix-quality
diagnostics: attempted 1, yielded 0) while the node itself was healthy and
answering on :8080.

Defect class: **two consumers of one setting, only one translated**
(honest_failure_modes #5). `_collect_aredn` ran the entry through the
names-first layer (`connect_target` → `hap.mf.internal`, resolvable), but the
reachability gate `_get_aredn_node_ip` RE-READ the RAW setting and called
`socket.connect_ex(("hap", 8080))` — a bare alias is not a resolvable
hostname, so the connect failed and the gate mapped a healthy node to
`unreachable`. The client half worked; the gate half was blind — a
reader/writer style split where the translation was wired into one path only.

Cure (MF `ffd22c6c`): the gate now takes the TRANSLATED target list from its
caller and probes exactly what the client will connect to; the raw-settings
read survives only as the legacy leg for direct callers. Regression test pins
that the gate probes `hap.mf.internal`, never the raw alias.

Verify after any names-first migration: `/api/status` →
`source_diagnostics.aredn` must read `reason_if_zero: ok` with
`notes: local node <name>` — an `unreachable` right after an alias migration
is THIS class, not a down node.

Related registry fact: the `hap` entry (AREDN node WH6GXZ-6-BI-ECOM) shares
its address with moc1 (the hAP is moc1's NAT front). A shared `ip_fallback`
is a registry ERROR unless one side declares the other via
`shares_front_with` — a dangling declaration errors, and a third claimant of
the same address is still refused (MF `9fc1adcf`).

---

## drift-cron-fleet-hosts — membership file exists only on the manager (2026-07-17)

The new per-box `fleet_naming_drift` cron (wraps `fleet_naming_audit.py
--json`, reduces to one `cron_verdict` exit) returned **UNKNOWN (exit 2,
"audit did not produce a report") on every box except the manager** at first
fleet-wide seeding.

Cause: the audit took its alias list from `~/.config/meshforge/fleet_hosts`
— the MANAGER box's ssh-target list (deliberately excludes self and
no-inbound-ssh boxes). Only the manager carries that file, so on the other 7
boxes the audit exited 2 before producing JSON. The UNKNOWN leg of the drift
check worked exactly as designed — it refused to read "couldn't run" as
healthy — but the cron was useless off-manager.

Cure (MF `1f47ee33`): explicit `--hosts-from-registry` flag — the naming
registry (`~/.config/meshforge/fleet_naming.json`, distributed to every
DNS-client box) is the membership source for per-box resolution auditing; a
broken registry stays exit 2. An explicit flag, NOT a silent fallback:
falling back quietly when fleet_hosts is absent would change audit semantics
without a witness. Side benefit: the registry list is WIDER than fleet_hosts
(includes self, m1, alaula, hap), so even the manager's own audit coverage
grew from 8 to 12 hosts.

Tell for recurrence: any fleet tool that reads a manager-vantage file
(fleet_hosts, the manager's ssh config) will silently or loudly fail when a
cron runs it per-box — audit membership must come from a fleet-distributed
artifact.
