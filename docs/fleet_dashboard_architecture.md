# Fleet Dashboard Architecture

> The view at `http://<meshanchor-host>:5000/fleet` is the operator's
> primary eyes-on for the NOC. This doc captures what's wired today,
> the contract between MeshForge and MeshAnchor, and the tiered
> roadmap for growing the surface.
>
> Authoritative artifact for future sessions extending the dashboard.
> Update it when shipping anything that touches `/fleet*` on either
> side.

---

## The split: visual vs data

Two repos cooperate to render the fleet dashboard. They are **not**
interchangeable.

| Concern | Owner | Files |
|---|---|---|
| HTML / JS / CSS for the dashboard | **MeshAnchor** | `meshanchor/web/fleet.html` |
| HTTP route layer + panel server | MeshAnchor | `meshanchor/src/utils/_map_fleet.py` (`FleetEndpointsMixin`) |
| `/fleet/rollup` peer poller | MeshAnchor | `meshanchor/src/monitoring/fleet_rollup.py` |
| Per-box health snapshot (`/fleet/slo`) | **MeshForge** | `meshforge/src/utils/fleet_snapshot.py` |
| Per-box `/fleet/slo` route | MeshForge | `meshforge/src/utils/map_http_handler.py:_serve_fleet_slo` |
| Lab traffic markdown rollup | MeshForge | `meshforge/scripts/lab_traffic_rollup.sh` |
| Lab traffic `/lab/rollup*` HTTP | MeshForge | `meshforge/src/utils/map_http_handler.py` |

**The contract**: MA polls each peer's `/fleet/slo` every 15s (with a
~5s overall budget) via `fleet_rollup.py:_fetch_peer_snapshot`. The
JSON shape is locked — see the docstring at the top of
`fleet_snapshot.py`. Anything new MeshForge wants on the dashboard
ships as a new top-level key in that snapshot; MA's panel JS reads it
and renders. No new endpoint required for the common case.

Endpoints MA already serves (visible in the dashboard):

| Route | Purpose | Refresh |
|---|---|---|
| `/fleet` / `/fleet.html` | Dashboard HTML | static |
| `/fleet/slo` | This box's snapshot | 5s |
| `/fleet/activity` | Per-host activity stream | 5s |
| `/fleet/rollup` | Cross-peer poll bundle | 15s (~5s response) |
| `/fleet/federation` | RNS announce table | 15s |
| `/fleet/lab-rollup` | Lab traffic markdown | 15s |
| `/fleet/blackouts` | Active incident banner | 10s |
| `/fleet/history` | Drilldown sparkline | on-click |
| `/fleet/health` | Aggregate health | on-demand |

Existing dashboard panels (`fleet.html`):
1. Top Boundaries (call volume)
2. Activity
3. Fleet Rollup (per-peer status grid)
4. Federation Round-Trip (lab tracer · worst-fail-first)
5. Federation Peers (RNS / LXMF)
6. Soft Failures

---

## Tiered roadmap

Tiers are about **time horizon and risk**, not priority. T0 ships in
the session it's planned; T1 in the next 1–2 sessions; T2+ is
catalogued for opportunity windows. Update tier status as items ship.

### T0 — Ship this session

These close gaps that the operator has **felt the absence of** in
real incidents.

- **Schedule Health panel** *(shipping 2026-05-15)*
  - **Why now**: moc1's `meshforge-tracer.timer` went `NEXT: -` after
    a hung oneshot service on 2026-05-14 12:30 HST. The freeze stayed
    silent for 18h until cross-fleet fail rates exposed it. Direct
    sight of timer state would have caught it before any traffic
    metric moved.
  - **Data**: enrich `/fleet/slo` with a `schedules` block —
    per-host list of `{name, scope, next_fire_unix, last_fire_unix,
    age_s, stale}`. Built from `systemctl [--user] list-timers --all
    --output=json`. Filter to `meshforge*`, `meshanchor*`, `moc-*`
    prefixes.
  - **Visual**: new panel in `fleet.html` rendering each host's
    timer list. Red badge per timer when `stale=true` (next is
    null) OR `age_s > 2 × interval`. Headline number on the panel:
    "N timers stale across fleet" — surfaces in the page title bar
    when ≥1.

### T1 — Next 1–2 sessions

- **Log tail viewer per host** *(backend shipped 2026-05-15)* — sample
  N last ERROR/WARN lines from key services. Backend:
  `/fleet/logs?unit=<name>&n=<int>&priority=<level>` lives at
  `meshforge/src/utils/fleet_logs.py`. Allowlisted units cover
  meshforge daemons + rnsd / meshtasticd / mosquitto + user-scope
  lab units (tracer / echo / synth-soak / lab-rollup / nomadnet).
  60s in-process cache; XDG_RUNTIME_DIR injection for user scope
  (same daemon-context fix as schedules block, commit 2dfca78).
  System scope shells via `sudo -n /usr/bin/journalctl` — sudoers
  drop-in required per box (deferred to deployment).
  **Outstanding**: MA dashboard panel that consumes this. Single
  unit picker + tail render. Tier transitions to shipped when the
  panel lands.
- **Tracer fire detail drilldown** — click a Federation Round-Trip
  cell, see the per-fire JSON for that pair over the last hour.
  Data already exists at `~/.local/state/meshforge/tracer/*.json`.
- **Run Tests panel** *(shipped 2026-05-15)* — operator one-click
  triggers for `meshforge-tracer.service`, `meshforge-synth-soak`,
  `meshforge-lab-rollup`, `meshforge-ci-status`.
  Backend: `GET /fleet/tests` returns the allowlist + last-fire
  metadata; `POST /fleet/run-test` body `{"test": "<id>"}` fires
  the unit via `systemctl --user start`. Allowlist lives in
  `map_http_handler._FLEET_TESTS` (one dict entry per test = the
  whole add-a-test contract). Dashboard panel renders one button
  per test with last-fire-age chips; fires show green/red result
  inline + auto-refresh the list after 8s.
- **CI status pill** — surface GitHub Actions state from the
  existing `meshforge-ci-status.timer` user-scope poll. Already
  fires; the data isn't on the dashboard yet.

### T2 — Opportunity windows

- **Cron / non-systemd job catalog** — if/when MeshForge grows
  cron-driven work, surface its state alongside systemd timers.
- **Plugin shape** — extract panel-rendering into a registered
  list so adding a new panel = one JS module + one Python feed.
  Today the panels are inline in `fleet.html`. Don't refactor
  pre-T1; refactor when there are 3+ T1-shipped panels to migrate.
- **WebSocket push** — replace polling with server-push for
  `activity` and `schedules` (the high-cadence panels). Reduces
  load and latency.
- **PWA / extension shape** — package the dashboard as a standalone
  app (browser extension, PWA install, or native shell). Requires
  decoupling from `:5000` (today the dashboard is served by the
  map daemon).

### T3 — Future vision

- **Alerts + history** — store the schedule-health, soft-failure,
  and federation-RTT history. Surface as a timeline. Pair with
  per-channel notification (Slack/email/SMS).
- **Cross-NOC federation rendering** — if a third NOC stack joins,
  the panel layout should keep working. Pre-T3, document the
  per-stack column convention so it doesn't grow ad-hoc.
- **Read-only operator on-call view** — strip the "run tests" /
  drilldown affordances for a glanceable on-call mode.

---

## Conventions for future sessions

When you add a panel:

1. **Pick the side first.** New visualization with existing data
   → MA. New data, no panel yet → MF + MA. Don't add an
   `/api/fleet/*` endpoint to MeshForge unless the data genuinely
   can't fit in `/fleet/slo`.
2. **Extend `slo` over inventing endpoints.** Adding a top-level
   key in `build_slo_snapshot()` flows through MA's existing
   poller for free. New endpoints require MA's `_fetch_peer_snapshot`
   changes and a JS fetcher.
3. **Refresh budget**: 5s for cheap reads (file/proc), 15s for
   anything that subprocess-spawns or polls the LXMF stack. Stay
   under 1s per call — MA's rollup is bounded.
4. **Stale signals beat live errors.** A panel that goes silent
   when its data source breaks is worse than a panel that says
   "stale 18h" loudly. Mirror today's red badge convention.
5. **Document tier transitions here.** When something graduates
   from T1 to shipped, move the bullet up and add the ship date +
   commit. When the doc disagrees with the code, fix the code or
   the doc — same session.
6. **Don't refactor for plugin-shape pre-T2.** Three panels of
   inline JS in `fleet.html` is fine. Five becomes annoying. The
   refactor pays for itself only with critical mass.

---

## Cross-references

- `project_cmd_diag_analyzer_roadmap.md` — adjacent NOC tooling
  (FleetHealthHandler T0 in TUI, this is the web equivalent)
- `project_fleet_rollup_full_picture.md` — current state of
  the MA peer-polling layer
- `project_rnsd_rpc_listener_wedge.md` — the incident that motivated
  T0 schedule-health (open follow-up #2 there is now this T0 work)
- `docs/birc_2026_05_17_noc_screenshot_slide.md` — early design notes
  for the dashboard layout (predates the panel set that shipped)
