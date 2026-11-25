# Maps honesty arc — phases (from the 2026-11-06 maps audit)

Source: claude.ai doc "MeshForge Maps (:5000) — Deep Research, Audit & Plan" (artifact UieYc41forRdEiQuSBrsA1), audited on the manager 11-06 — provenance row "Maps deep-research doc audited". ROADMAP item: `MAP-H` (one line; MF012 cap) — **in NOW, operator 2026-11-06 ("promote MAP-H into NOW"), MeshForge-scoped**: H3, H4, H5 + the AREDN walk, H9. The maps/MA rows (H6 lqm, H7, H8) and phases 2–4 below are NOT in NOW. **Progress log = the doc's "Progress — read this first" table**: every session that moves MAP-H adds a dated row there (commit · deployed? · next), in the same session as the commit. Order: honesty → one source of truth → performance → features. Land in MF first, port under `parity_check.py`.

## Phase 1 — MAP-H: stop the map lying

H1 demo nodes on a failed fetch + H2 unknown reason → ok: BUILT 11-06 (`tests/test_map_source_honesty.py`, red-first). Still open, each red-first: H3 `/` answers JSON 503 while warming (serve HTML that polls `/api/status`) + handlers never wait on `_collect_lock` (serve the last snapshot with its age) + ONE collect deadline → `collect_timeout`; H4 `last_heard or 0` (MF `map_data_collector.py:1217`, MA same) → `None` = "never"; H5 AREDN request errors → `None` and link fields default 0 (`aredn.py:234-248, 390-395`) → tri-state, missing = unknown; AREDN neighbour walk serial `timeout=3` under `_collect_lock` (`_map_collector_aredn.py:132`) → bounded pool on the `AREDN_SYSINFO_*` timeouts; H6 maps `isinstance(lqm, list)` (`aredn_collector.py:202`) — **MEASURED 11-06 on hap + moc5 (4.26.10.0, api 2.0): `lqm` is a DICT `{enabled, config, info}`** → read `lqm.info.trackers`, replace the invented fixture with a real capture; H7 maps worldmap nodes "online" by presence (`aredn_collector.py:392-394`); H8 MA records 0 nodes at DEBUG when meshforge-maps is down (MA `map_data_collector.py:1262-1279`) → up / empty / unobservable at WARNING; H9 `telemetry_poller` counts "CLI exit 0" (request SENT) as success (`telemetry_poller.py:394-400`); drop the `/json/nodes` "Strategy 1" probe (#76). Gate: on a live box, stop each collector in turn → the map says blind, never healthy or empty.

## Phase 2 — one source of truth (was MAP-SOT)
`utils/map_legend.py` → `status_legend.json` / `/api/legend` (colours, freshness tiers, offline colours, glyphs) read by `node_map.html`, topology, `coverage_map.py`, `fleet.html`, TUI `emoji.py` (MEASURED: RNS is `#26c6da` in `getNodeColor` and `#ab47bc` in topology/legend, and `#ab47bc` is also MQTT; gateways have no offline colour); `node_feature.py` (sibling of `canonical_message.py`, byte-identical in 3 repos via `parity_check.py`) owns key names (`rns` vs maps `reticulum`, `channel_utilization` vs `channel_util`) + per-value `{value, ts, source}`; per-value telemetry age (battery/env age today = node `lastHeard`); freshness tiers live/quiet/stale/gone + unobservable; maps' three thresholds derived from one table. Frontier-class review of the contract before it lands. MA ports: #17 gateway guard, reason diagnostics.

## Phase 3 — redraw diet, metrics, navigation (was MAP-PERF)
Markers are already diffed (audit reframed the complaint): links `clearLayers()` every update (`node_map.html` linkLayer), overlapping 60 s fetches (no in-flight guard / `AbortController`), the 60 s timer holds the original `refreshNodes` so topology never refreshes, maps WS path `setLatLng` unconditionally (`meshforge-maps.js:1977`, 2nd copy of the 09-25 "it rolls" bug), maps `setStyle`/`refreshClusters` every cycle; ETag/304 on geojson; `map_metrics` gaps (per-source last-success age, collect duration, lock wait, in-flight, fds; histogram tops at 10 s; `no_positions` counted as an error); nav between `/`, `/fleet`, `/inbox` + a link-check test; split maps `map_server.py` (2306) + `meshforge-maps.js` (2761) under MF025. Gate: 10-min quiet-mesh soak = 0 marker updates. Not observed in a browser yet — measure before building.

## Phase 4 — features no other map has (was MAP-X)
map features no other mesh map has: a blindness layer (each collector's heard footprint hatches grey when it goes dark), heard-by edges (a line only for a packet a fleet box received, SNR + age), the best-available-route overlay (BIC-6 drawn), two-ended link view (A's TX beside B's RX), 24 h scrubber over `node_history`, a truth badge per value (measured / relayed / asserted), `/map/selftest`. From the 11-06 maps audit; after MAP-H + MAP-SOT.

## H14 + H15 — the map draws SEEN nodes as dead (operator 2026-11-08, MEASURED)

Operator report 11-08 07:2x: on VolcanoAI :5000 "most nodes that say off are not — off as far as
transport but available, listening, getting metrics". Popup for moc3: `○ Offline · Last heard 203d ago`.
MEASURED same minute (`/api/nodes/geojson`, 49,921 features, read-only):
- **H14** — 37 Meshtastic nodes a fleet PEER heard < 2 h ago, **37 / 37 `is_online: false`**.
  `_map_collector_federation.py:349` hard-codes `is_online: False` ("don't claim freshness").
  moc3's federated copy: `last_seen` 5 min old, `seen_by_peers` moc2 · moc1 · moc5 → drawn Offline.
  The inverse of H1/H2 and the same lie: a state the map did not measure. Our own fleet box decoding the
  node on its radio IS a measurement. Fix = a third state, not `true`: "heard by moc2 4 min ago"
  (the peer's clock → hfm #6: clamp future, tag skew), distinct from "heard here".
- **H15** — 4 nodes drawn TWICE: a local stub `mesh_!ebfa1b11` ("203d ago", VolcanoAI's own nodedb —
  VolcanoAI is LongFast, moc3 is SHORT_TURBO, so this radio can NEVER hear it) beside the fresh federated
  `!ebfa1b11`. The local-vs-federated `local_index` misses on the `mesh_` id prefix; the popup the operator
  opened was the stale one. (WB7N Extra Wismesh Tag, Little Brother 2, Meshtastic 8065, meshforge moc3.)
- Wording: a node this radio cannot hear (declared other segment) must not read "Offline" — "not heard
  by this box's radio · heard by <peer> <age>". Same closed vocabulary as INBOX-2 step 3
  (`utils/inbox_heard.py`: heard / not_heard / unobservable / cannot_hear) — one vocabulary, hfm #5.
- Order (operator 11-08 "prioritize accordingly"): H14 + H15 (honesty, red-first) → H12 → H4 → H5 →
  Phase 3 (link animation + choppy redraw: operator-observed 11-08 — `linkLayer.clearLayers()` every
  update, no in-flight guard; MEASURE in a browser before building).

### ⚠️ CORRECTED 2026-11-08 08:14 — H14's premise was the WRONG QUANTITY (attempt 1 NOT shipped)
Non-author `code-review medium` finding 1, then MEASURED: a federated `last_seen` is the PEER'S
COLLECTOR WRITE TIME (`node_history.py` stamps every local-origin directory row `now` each cycle,
by design), not when its radio heard the node. moc2 federates moc3 at "55 min ago" while moc2's
own `last_heard` for moc3 is 478 h. So "37 / 37 heard < 2 h" measured bookkeeping; attempt 1
(`presence` from that stamp) would have painted dead nodes green. Patch kept in the session
scratchpad only. Before any fleet-presence state: the federation export must carry the peer's
RADIO hearing time with its own trust (and a frozen-clock peer must read unobservable).
What IS true, measured the same hour:
- **H4 is the bigger cause of false "Offline":** VolcanoAI's radio says `num_online_nodes=92`
  (firmware: heard ≤ 2 h); the map has 24 local meshtastic nodes heard ≤ 2 h, 13 ≤ 15 min, and
  **166 / 388 with no or zero `last_heard`**. Explain the 92-vs-24 gap first (which source drops
  lastHeard), then H4 red-first. Also a definitional gap: map "Offline" = not heard in 15 min
  (`online_status_threshold_minutes`), firmware "online" = 2 h — the label must say which.
- **moc2 meshtasticd clock frozen:** 10 / 10 JSON lines `"timestamp":0` (started 10-26 20:58,
  persistent-issues "started before NTP sync" row) — its nodedb `lastHeard` is frozen, so every
  box federating moc2's view inherits it. Cure = restart meshtasticd on moc2 now NTP is synced
  (operator go). moc3 + VolcanoAI clean (0 / 10, 0 / 78).
- H15 (`mesh_` prefix) stands, but per review it is ALSO split inside federation itself
  (`map_federation.py` merges peers on the raw id) — canonicalise once at the federation edge.
- Review also: other surfaces read raw `is_online` (status card, sidebar, topology, links) —
  any new state must go through ONE reader.

### SHIPPED 2026-11-08 `940ada70` — the 2 h window + H15 one id (operator "2h to match firmware")
Firmware `NodeDB.cpp:1692` NUM_ONLINE_SECS = 2 h, read at source; ONE seam in `collect()`
(`_normalize_meshtastic_props`), ONE id rule (`map_federation.canonical_node_id`). Reviews r1
code-review medium · r2 Fable by running · r3 Fable by running CLEAN (+3 fixed). Full suite
18440 passed. QUEUED (not this commit): (1) `_load_cache` zeroes is_online at its own 900 s —
reached only when every source is empty; (2) the gateway `UnifiedNodeTracker` window stays
15 min for every network → the TUI can still say offline where the map says online (named
divergence; decide per network, not by copy); (3) a probe for the `wait_for_ntp_sync.sh`
fail-open witness (moc2 sat 13 days with every node's lastHeard frozen and nothing read it).

### SHIPPED 2026-11-08 evening — H14 follow-ups `c284516e` `50283000` `e9b1064f` (deployed 10/10 `9cf8812f`; `e9b1064f` deploy at push)
- ONE peer_status serializer + envelope + trust call + witness vocabulary for `/api/status` and geojson (they had drifted); online-window settings validated once (`_usable_minutes`, [1 s, 1 yr]).
- A gateway box (moc) now reads "peer's map defers its radio to its gateway (#17)" — from the peer's own `source_diagnostics`, ONE status probe per poll, tri-state, hold ≤ 3 polls; the federation panel says it per peer. LIVE VERIFIED 19:31 incl. the SERVED page JS in chromium.
- `tests/_federation_net_guard.py`: 14 pre-existing poll tests were making real GETs every run — closed.
- Open: r3 F2 (stamped rows from before a gateway restart hide a fresh deferral as "frozen"); MA port; gateway tracker as its own witness.

### SHIPPED 2026-11-08 `9672ca99` — H14 done right: the peer's RADIO heard time, with its own trust
Seam: the radio nodedb drain stamps `radio_last_heard` (never via_mqtt; own row `is_self` from the
EXISTING connection's myInfo) → `node_history.radio_heard` (reads ONLY that prop) → directory export →
federation `radio_heard_by` + per-peer `radio_newest_by_network` → `utils.peer_radio_heard` trusts a
peer's radio per NETWORK only while its newest sits inside that network's window (frozen clock / deaf /
old code / failed / backoff / clock ahead = unobservable, named) → `props.peer_heard` on the local
winner or federated entry; is_online untouched; one page reader. Reviews r1 medium · r2–r4 Fable
subagent by running (r3: MQTT merge laundering + own-node self-attest; r4 CLEAN). LIVE 14:25: moc3 on
the manager reads "heard by meshforge-moc2 30 min ago" (was only "204d"); honest_status exit 0 @
`32bd0b81`. QUEUED: gateway boxes (moc, moc3) are not witnesses — their maps defer the radio to the
gateway (#17), so the reason should say so and the gateway's tracker needs its own trust;
`/api/status` peer_status (`_map_status_endpoints.py`) lacks the witness fields; MA twin port.

## Queued from the round-1 non-author review of H1+H2 (2026-11-06)

- F5: the other Python readers of `reason_if_zero` still carry their own lists — `node_counts.py:89` (any reason ≠ ok → count None, so `no_positions`/`no_data` disagree with the map), `watchdog_probes_aredn.py:296`, `map_metrics.py:188` (counts `no_positions` as an error). Move each onto `utils.map_source_reasons.reason_state` — red-first, one consumer per commit; `node_counts` is a TUI/fleet-line behaviour change, so name it.
- A source that returns without calling `_record_diagnostic` now HOLDS last cycle's reason (the F2 pre-stamp keeps it). Every collector records today (BELIEVED, by grep); a test that fails when one doesn't would make it a guarantee.

## Live drill of H1+H2 after deploy (2026-11-06 14:20–14:24 HST, VolcanoAI :5000, real Chrome)

Fleet 10/10 `1cf1f91e`, honest_status rc 0. Page open + map restart → `refreshNodes()` → "Map blind since 02:23 PM — map server warming up (cold start). Showing the last 440 nodes it saw (greyed); retrying.", panes greyed, no demo node; cleared on ready; `?demo=1` loads demo ONLY on a failed fetch (healthy → live, by design). Cold start ~27 s. Found, not fixed:
- **H3 seen live** — a browser that LOADS `/` while warming gets the raw 503 JSON body: no page, no banner.
- **H10 permanent "2 degraded / 9"** — `node_tracker` + `public_fallback` record only `latency_ms`, never `reason_if_zero` → `unobservable` on a healthy box (Py + JS agree). Honest, but it trains the operator to read past "degraded". Give each a real reason (or stop listing a source that cannot report). This is the "a source that never calls `_record_diagnostic`" case above, MEASURED: two of nine.
- **H11 false banner text** — the `?demo=1` branch shows "…showing DEMO nodes (?demo=1), not live nodes. Showing the last N nodes it saw (greyed)" — the N are DEMO nodes, not nodes it saw (`showMapBlind` suffix applied after `loadNodes(DEMO_GEOJSON)`).
- **⚠️ Constraint on H3 #4, MEASURED n=1:** `meshcore_public` took **25.7 s** on a healthy collect (70,483 nodes, `map.meshcore.dev`). A ~20 s overall deadline would mark it `collect_timeout` every cycle on the manager. The deadline must sit above the slowest HEALTHY source (sample `latency_ms` over many collects first), or that source moves out of the deadlined set — decide from data, not this one sample.

**Operator 2026-11-06 14:53: "use the manager for the drill, 45 s deadline is fine"** — #5 runs on the MANAGER (lab map is off by design); #4 default = 45 s.

**H3 #4 deadline — MEASURED fleet-wide 2026-11-06 14:45 (24 h of `MapDataCollector latencies` journal lines, read-only):** VolcanoAI n=281 total p50 11.5 s / max 37.6 s, `meshcore_public` uncached p50 11.5 / p90 20.9 / **max 34.1 s**; moc1 total max 25.9 s (`public_fallback` max 24.3 s); moc2/moc4 ≤ 8.2 s (`aredn_worldmap`); moc5/kiai ≤ 5.8 s; moc ≤ 7.7 s; moc3/meshanchor-server/lehua/lab run no map. Every other source ≤ 2.4 s. **A ~20 s deadline would time out >10 % of VolcanoAI's HEALTHY meshcore_public fetches → default ≈ 45 s** (above the slowest healthy max). After H3 #1 lands, the deadline bounds FRESHNESS only (no request waits), so it is its own slice: sources move into a bounded worker that abandons the late one (the `_collect_interface_bounded` pattern) — concurrency, frontier review before deploy.

**H3 progress 2026-11-06 (Opus 5.5, staged in a scratch worktree, then committed on main):** #3 pages + static load while warming (`_serves_while_warming`; `/api/*` + JSON routes stay 503; traversal guard planted-and-caught). #1 request threads read `MapDataCollector.snapshot()` — never `_collect_lock`; stale → ONE kicked background refresh; none → 503 `warming` (geojson + topology). #2 `X-Snapshot-Age` + `X-Snapshot-Stale-After` headers, `/api/status.snapshot` {none|ok|stale, age_s, stale_after_s, refresh_failures}; stale = 2 × `periodic_refresh_seconds` (the refresher missed twice — a 30 s line would banner every page view, the H10 class); page banner reads the server's line. Monotonic age anchor. Open: #4 deadline (above), #5 live 50-curl drill + control. **#4 BUILT 2026-11-07** (`_map_collector_deadline.py`): ONE `collect_deadline_seconds` (45 s, clamped (0, 3600]); each source in a bounded worker on what is left; once < a floor is left each remaining source gets `SOURCE_FLOOR_SECONDS` 5 s (a wedged meshtasticd no longer blinds the rest — Fable r1 drilled it); an abandoned worker writes only a thread-local sink, merged on a clean join; not restarted while its previous worker lives; worst case = deadline + floor × 10, the kicked refresh's lock wait derived from it. 3 Fable rounds by running, last clean; provenance row 11-07.

**H3 deployed + #5 drilled 2026-11-06 19:04 HST (Opus 5.5, VolcanoAI):** fleet 10/10 `38f2b253` (fleet_sync rc 0 → fleet_pull rc 0 → honest_status rc 0, 18148 passed); live map PID 1969828 serves `X-Snapshot-Age`. Drill, OLD `1cf1f91e` vs NEW `4338639e` interleaved O/N/O/N on 127.0.0.1:5091, isolated (scratch HOME+TMPDIR, LOGNAME/SUDO_USER unset, RNS/LXMF import shim, meshtasticd → 192.0.2.1; live config + `/tmp/meshforge_rns_client/config` diffed UNCHANGED, `@rns/volcano` still rnsd, :9443 200): **browser leg PASS 2/2 each** — `/` during warm-up OLD `503 application/json`, NEW `200 text/html`. **Pile-up leg INCONCLUSIVE** — the control could not fail: OLD also answered all 50 geojson with a fast 503 (threads 5–7, p99 1.04/1.22 s; NEW 1.04/0.05 s — the ~1 s tail is on both, BELIEVED TCP-connect retry). BELIEVED: the lock pile-up lives on a REFRESH after the first snapshot, which a cold start never reaches; owed = a warm instance + a slowed refresh (new experiment, operator's go). Side note: drill instances could not bind the fixed WS port :5001 (live map kept it) — a second map on one box has no WS.

**H3 #5 WARM pile-up drill 2026-11-06 19:16–19:23 HST (operator: "go ahead with the warm pile-up drill"):** same isolation; warm (first 200 at ~36 s) → wait 33 s past the 30 s `collect_cache_max_age` → one trigger request + 50 parallel geojson 1 s later; O/N/O/N + one NEW connect-timing run. Pre-registered: OLD threads climb + p99 ≫ 1 s; NEW flat + p99 < 1 s. **Control FAILED as it must:** OLD threads 6→57 and 7→58 (+51 = one blocked thread per request), p50 2.18/1.78 s, every request waited out the ~3 s refresh (`with self._collect_lock`, old `map_data_collector.py:692`). **NEW: threads 6→9 and 6→7, p50 0.02 s, all 200.** NEW p99 = 1.03 s misses "< 1 s" BY THE LETTER; MEASURED cause (new-3 run, `%{time_connect}`): all 8 slow requests have connect 1.01–1.04 s and connect→first-byte 2–30 ms; the 42 others connect ≤ 47 ms — the server answers every request in < 0.05 s, the 1 s is TCP SYN retry. **Verdict: #5 PASS on server time; the < 1 s end-to-end line is not met because of the listen backlog, a separate defect.** → NEW finding **H13**: `ThreadingHTTPServer` keeps Python's `request_queue_size = 5` (`map_data_service.py:725`, `:833`; somaxconn 4096) — > ~5 simultaneous connects wait 1 s (old AND new). Cure shape: subclass with `request_queue_size = 128`, test-pinned; red-first via a 50-connect test. **BUILT `b60166c5` 2026-11-06** (red-first, mutation 5 → `assert 6 == 50`; warm drill on the fixed tree max 0.087 / 0.062 s, connect ≤ 38 ms; two review rounds, provenance row). **DEPLOYED 2026-11-06 21:28** (fleet_sync+pull rc 0; `ss` Send-Q 128 on all 7 map boxes, full honest_status rc 0 8/9+WARN). Queued from H13: (a) ~~MeshAnchor twin~~ **DONE MA `7949ea6c`** (hand port, Fable review by running: 16 mutants, 1 blocking = MA-only `TestSelfPortRegistrationIsWired` read the removed literal, fixed; MA CI success; deployed 22:09 — meshanchor-server Send-Q 128, burst 50/50 200, max connect 26 ms); review survivors (HTTPServer base, daemon_threads=False) pinned back in MF `abc3b18d`; (b) ~~`config_api.py:1177` + `metrics_server.py:60` keep backlog 5~~ **DONE 2026-11-07** MF `5a72ea9e` + MA `a0a63ea6`: one `utils/http_listener.LISTEN_BACKLOG` (hfm #5) read by MapHTTPServer / ConfigAPITCPServer / MetricsTCPServer; `MetricsServer.stop()` now closes its fd; repo-wide guard = AST finds listener shapes, the VALUE is read from the imported class (seam after two review rounds broke a by-pattern check). **DEPLOYED 11-07**: MF fleet 10/10 `1442816d`; meshanchor-server `meshanchor-daemon` (operator go) `:8081` Send-Q 5→128, burst 50/50 200 ×2, max connect 37 ms; full honest_status rc 0 8/9+WARN. Residual queue (round 5, unverified NITs + 2 self-made traps): review_provenance 2026-11-07 row. (c) ~~10 test harnesses~~ **DONE** same commits — MF 10 sites in 9 files / MA 6 files build the live listener via `_make_http_server` (`test_map_host_rebinding.py` held 2). Still open, both twins: `MapServer.stop()` has no `server_close()` (changes live map shutdown — its own change). Live check after: config dir unchanged except `messages.db` (19:23:06 = live map PID 1969828 "Stored message 4710" from MQTT — not the drill), `@rns/` owner rnsd, :5091 released.

**H3 reviews 2026-11-06:** r1 `/code-review medium` (contextless fork) 9 findings → all fixed via two seams in `utils/_map_collector_snapshot.py` (ONE atomic publish `(geojson, mono, generation)` read once per request; ONE collect witness for every driver). r2 Fable subagent BY RUNNING, 5 confirmed → #1 generation keys leaked every past snapshot's bytes (publish now clears the cache), #2 lock-wait timeout now witnessed (counter + rate-limited WARNING + `stale_reason`), #3 publish ages the wall stamp too (the two headline never-wait tests passed on a representation), failed kick no longer re-warms; every fix planted-reverted → its test fails (r1 8/8, r2 5/5). Queued, NOT fixed:
- **H12 DONE 2026-11-20 `044efb13` (NOT deployed — operator go pending)** — verdict `all_dark_reason` (attempted-this-cycle voters; `NON_WITNESS_SOURCES`), `blind_cycles`, `snapshot.state=blind`, `X-Map-Blind`, page banner; wrapper seam for diagnostic-less sources; mqtt/rns down = unreachable; replayed cache not re-saved. Residuals: nested `_timed_collect` late-worker leak (latent); replayed cache still recorded in history (H14 design — decide); banner unseen in a browser. Original: **H12 every source dark reads `ok` with 0 nodes** — `_timed_collect` isolates each source (returns `[]`), so a cycle where EVERY attempted source is `unreachable` publishes an empty snapshot, `collect_failures` 0, state `ok` (r2#4, CONFIRMED). The per-source reasons say it; the snapshot verdict does not. Decide: count an all-dark cycle as a failure / carry it into `last_error`, red-first.
- page JS pinned by string presence only (r2#5): a 200 WITHOUT the age headers clears the banner (NaN > NaN), and the stale branch still sets the API pill healthy. Needs a JS-capable check or the live browser drill (#5).
- `collect()`'s own cache check is still wall clock (`_last_collect`) while snapshots are monotonic — pre-existing, now visibly two clocks (hfm #6).

## H3 — acceptance, written BEFORE code (2026-11-06; build in a fresh session)

Measured today: `collect()` cache-miss path does `with self._collect_lock:` (`map_data_collector.py:692`) — every HTTP caller with a stale cache BLOCKS, no deadline, behind a collect that can run 45 s TCP + 15 s CLI + source caps + the serial AREDN walk; a background refresher (`:373-428`, non-blocking acquire) already exists. `map_http_handler.py:591` answers EVERY path, `/` included, with JSON 503 while `is_warming`.

Pass = ALL of these, each a test that FAILS on `c639e260` first:
1. A request thread NEVER waits on `_collect_lock`: with the lock held by a planted 30 s collect, `collect()` from a handler returns within 1 s — the last snapshot when one exists, else a "warming" signal. (Only the background refresher and prewarm collect.)
2. The served snapshot carries its age (`X-Snapshot-Age` header + a field in `/api/status`) — a stale snapshot is SAID, never passed off as fresh (the H1 banner reads it).
3. While warming, `/` serves the map HTML (200); only `/api/*` answer 503 `warming`; the page shows the H1 blind banner "warming up (cold start)" and retries.
4. ONE overall collect deadline (setting, default ~20 s): a source past it records `collect_timeout` and the collect finishes with the rest.
5. Live, on the lab box (not a gateway): 50 parallel `curl /api/nodes/geojson` during a cold start → thread count flat (`ls /proc/<pid>/task | wc -l` before/after), p99 < 1 s, and a browser shows the banner, not a raw JSON page.
Control that can fail: the same 50-curl drill on `c639e260` must show the pile-up (threads climb, p99 ≫ 1 s), or the drill is not measuring the defect.
Not in scope: changing what the background refresher collects. Review: contextless non-author pass before deploy (concurrency = upshift; queue a frontier read if this session is not one).

## H16 + the CoreScope comparison (operator 2026-11-17: "yes queue them - i follow your recommendations")

**Question asked:** can :5000 do what CoreScope's live map does? Read 11-17 (code + CoreScope README/UI guide; CoreScope NOT run).

**H16 — an animation draws a path nobody measured (MAP-H class).** `web/node_map.html:2663` animates a live message `msg.from_id → 'local'`: a straight line from the sender to this box, whatever the hop count. A 3-hop packet is drawn as direct. BELIEVED from reading (not observed on screen). Red-first: a hop>0 message must not draw a direct sender→box line; hops unknown → no line, say so.

**Physics (why CoreScope's route animation does not port to Meshtastic):** a MeshCore packet carries its path (repeater hashes), so one packet draws a route. A Meshtastic packet carries hop count (`hopStart − hopLimit`) and, since fw 2.6, ONE byte of the last relay (ambiguous: several nodes share a byte). A Meshtastic route drawn from one packet is invented; measured routes come only from traceroutes (`traceroute_history.db`, ROADMAP TRH-1).

**What :5000 has (VERIFIED 11-17):** websocket live message feed (:5001 listening on VolcanoAI + moc), a particle "message flow" animation, a 24 h playback slider of NODE snapshots (not packets), animated link lines, the MAP-H honesty layer.

**Plan, in order (beat CoreScope on truth; PARITY-1 "copy, then match or beat"):**
1. H16 (above) — first; it is a defect. **DONE 2026-11-20 `e68094df`** (not deployed: the map's listener half needs a map restart; page half is live from disk). Root: `message_listener` mapped missing `hopStart` → `hops_away=0`; now None (absent `hopLimit` = wire 0, proto3). Line only for `hops_away === 0` to the `is_self` marker; rows say direct / N hops — path not measured / hops unknown / not recorded. Residuals: (a) review F5 — if `is_self` is lost in a merge, a direct packet draws no line silently; check `_merge_feature` keeps it. **(a) MEASURED 2026-11-24 before 17:20 HST (`date` at write; live `/api/nodes/geojson` on VolcanoAI/moc/moc1/moc4/moc5/kiai): no feature anywhere carries `is_self: false` (the merge's fill-gaps rule cannot lose a True no source contradicts) → BELIEVED a non-issue. `is_self` exists only where the MAP drains its own radio (`_drain_interface_nodes`, "via tcp"): moc5 = a positioned marker → the line can draw; moc4 = tagged but GPS-less (in `nodes_without_position`) → no marker, no line, row still says "direct" (honest: no endpoint); moc/moc1 = `deferred_to_gateway` → own id unknown → no line (honest). No code change.** (b) `store_incoming` keeps no `hops_away` → stored rows say 'not recorded'. (c) page half static-pinned, never seen in a browser.
2. Heard-by fan per packet: one line per fleet box that RECEIVED it, labelled SNR · hops · age; nothing for a box that did not (= Phase 4 heard-by edges; our fleet is the observer network).
3. Last-hop CANDIDATES from the 1-byte relay id, drawn as candidates ("relayed by one of A, B"), never one confident line.
4. Traceroute routes on the map (TRH-1) — the only measured Meshtastic paths.
5. Packet replay (VCR over heard-by events we already record), beside the node-snapshot slider.
6. MeshAnchor: true MeshCore route animation from packet paths (honest there).
7. Run CoreScope in the lab as the reference (PARITY-1: references in the lab only); parity test on the same MeshCore traffic.

### Step 2 — heard-by fan: design OPEN, two prerequisites MEASURED missing (2026-11-24 17:38 HST, `date`)

**Scope (operator 11-24, AskUserQuestion: "Our sends first"):** THIS box's own `/inbox` sends, on the center map (VolcanoAI) first. Reuse BIC-2's judged boxes (`inbox_legs.heard_from` → `boxes`): ONE reader, so the map and `/inbox` can never name different hearers (hfm #5).

**Acceptance sentence (pre-registered, not yet buildable):** an `/inbox` send whose line reads "heard by A, B" draws, on the center map, one line from the SENDING radio's marker to A's and B's markers, each labelled "heard +Ns after send · SNR/hops not recorded"; nothing for `not_heard` / `unobservable` / `cannot_hear`; a hearer with no position is LISTED ("heard (no position)"), never drawn; a kept hearing says "kept". END = the operator sends from `/inbox` and the map fans to exactly the boxes the inbox names.

**Prerequisite P1 — alias → radio node id: ABSENT (measured).** No spool (`truth_spool/<alias>.json` sections: alias, box_truth, clock, deployment, ingress, radio_probe{tcp_listening,usb_present,usb_held}, rx_heard, role, services, …), no config, no `docs/fleet_roles.yaml` maps "moc4" to its radio's node id. Each box must report its OWN node id, read from its OWN radio (memory: own id NEVER inferred from traffic — moc4's journal shows `Received routing from=0x896b1917`, its own id, which is exactly the inference that rule bans). Candidate sources, none decided: (a) the box's map `_own_node_id` (TCP drain only — the PhoneAPI client class that force-closed the map, 11-11; HTTP-collected boxes have none); (b) a meshtasticd boot-banner line (none found by grep on moc4 — journal starts 11-02, banner wording UNKNOWN); (c) an HTTP :9443 endpoint (UNKNOWN whether one exposes it on Linux meshtasticd). Lands in the box_truth export → `box_truth_*.py` is byte-identical with MeshAnchor (parity) → a TWIN change.

**Prerequisite P2 — the fan's origin on VolcanoAI: ABSENT (measured).** VolcanoAI's `/api/nodes/geojson` carries no `is_self` feature; which radio carries the center's `/inbox` sends is not established here. No origin → no line (draw nothing rather than invent one).

**Label limit (measured):** `rx_heard` entries are `[from, id, rx_ts]` only — no SNR, no hops. The fan can say WHEN each box heard it, never how well; anything else is "not recorded".

**Tier:** novel cross-twin export field + new map layer = frontier-class design (model_advisor upshift). Next: frontier session + operator grilling on P1's source; then red-first slices P1 → P2 → page.

## H4a — this radio's GPS-less records lost to federation (2026-11-20, MEASURED; design REVERSED after r3)

MEASURED 15:28 HST on the lab map (read-only): radio `num_online_nodes=37`, `num_total_nodes=400`;
map positioned-from-radio 226 (24 heard ≤ 2 h), position-less from radio **25** (should be 174),
federated position-less stubs 157 (no `is_online`, `last_seen` = peer WRITE time). The 11-08 premise
"H4 (`or 0`) is the bigger cause" is REFUTED: no/zero `last_heard` on positioned nodes is now 28, not 166.
Cause: position-less writers set no `source_origin` → priority 0 → any peer `local_radio` row won.

- r1 `e8bb8135`: tag every position-less writer; at equal trust draw OUR record at a peer's fix.
- r2 `0349c49c`: adopt at any trust ≤ ours (operator "adopt lower-trust positions"); popup + ring; witness.
- r3 `bba86d86`: `best_fix` beside the federation winner; `utils/map_borrowed_position` as one owner.
- **r3 review not clean, same defect kind a 3rd time → design REVERSED (operator 18:4x: "switch to the
  federated representation").** An adopted node as a LOCAL feature at not-our-coordinates is a new
  hybrid every geometry consumer must learn (Prometheus, topology inferred links, coverage, tile warm,
  collapse). Next: draw it as a FEDERATED feature (every consumer already treats those as not ours) +
  this radio's hearing in separate fields (`heard_here*`) + ONE exception (history stores our hearing
  as a GPS-less `local_radio` row). Keep r1's tagging (the actual cause). Findings + residuals:
  `.claude/audits/review_provenance.md` 2026-11-20 H4a rows.
- r4 `f5a15a1a` (2026-11-20): REBUILT as the federated representation — our GPS-less record stays
  ours (history keeps the hearing with no new code); a peer fix = an ordinary federated feature +
  `heard_here*`; `best_fix` only for winners without a fix (second pass); r2/r3 (b) unwound.
- r5 `604d6c6d`: r4 Fable review fixed — `heard_here` reads ONLY H14 `radio_last_heard` (per-network
  window, tri-state, None = unknown); adopted nodes out of federation totals; `strip_radio_stamp`
  strips `heard_here*` on cache replay; status None on error.
- **r5 Fable review NOT clean → r6, fresh session, SEAM FIRST.** F1 (reachable): a node GPS-less on
  this radio AND positioned by a lower-trust LOCAL source + a peer `local_radio` fix takes the
  pre-existing higher-trust REPLACE path — page "not heard here" for a node heard 60 s ago, counted
  twice. Third time a merge path did not know the new marker → design ONE owner of the
  local-vs-peer decision per (net,id) before patching. F2: `better_fix` TypeError on a non-numeric
  peer `last_seen` crashes the poll (r4 flagged; deferring it was wrong). Nits + evidence:
  provenance r5 row.
- **Acceptance RESTATED (r4 review):** the page's online counter cannot move with this change
  (position-less nodes never enter page counts; federated markers are `is_online: False`). Live,
  after deploy: ~174 local position-less from the radio; online measured over features +
  `nodes_without_position` ≈ radio `num_online_nodes`; `/api/status federation.position_adopted` a
  number.
- `main` @ `604d6c6d` carries r1–r5, CI success 604d6c6d (44877869655); NOT deployed — do not deploy as-is.

### H4a r6 — the SEAM: one owner of local-vs-peer per (net,id) (2026-11-20 22:34 HST, operator "go — include row 10")

**Root (read, BELIEVED):** `_merge_federation` indexes local features FIRST and adds a position-less
entry only `if key not in local_index` (`_map_collector_federation.py:290-303`); no position-less
writer skips a key that already has a feature. A node positioned by `mqtt_local` (70) AND heard
GPS-less by our radio (`local_radio`, 100) is judged on the 70 — our hearing never enters the
decision. r5 F1 is not "a path missing the marker"; the local side of the decision sees ONE of our
records, picked by insertion order, not trust.

Legend: LF = local positioned feature (trust f) · LP = local position-less record (trust p — every
writer now tags `local_radio` 100 / `rns_path_table` 90) · R = peer winner row (trust r) · fix = R's
own valid fix or `best_fix`.

| # | local | peer | before r6 | r6 rule |
|---|---|---|---|---|
| 1 | — | R + fix | federated marker | unchanged |
| 2 | — | R no fix, other peer has one | position-less fed stub; `best_fix` ignored | **DEFERRED** (not our hearing; own slice) |
| 3 | — | R no fix | fed stub | unchanged |
| 4 | LF | r ≤ f | local wins, `peer_heard` | unchanged |
| 5 | LF | r > f, fix | local replaced by federated, counted fed-only | unchanged (pre-existing upgrade; no hearing involved) |
| 6 | LF | r > f, no fix | **our marker popped → fed stub, position LOST** | I3: keep our marker + `peer_heard` |
| 7 | LP | r ≤ p, fix | stub ours + fed marker + `heard_here`, adopted | unchanged |
| 8 | LP | r ≤ p, no fix | stub ours + `peer_heard` | unchanged |
| 9 | LP | r > p (RNS 90 vs peer `local_radio`) | **stub DROPPED before history reads it — hearing lost** | I1: = rows 7/8 at any trust |
| 10 | LF+LP | r ≤ f / no peer | marker + stub: **counted in both lists** | I2: ONE marker (ours) carrying our hearing; stub leaves the served list, history still gets it |
| 11 | LF+LP | r > f | **r5 F1:** fed marker w/o `heard_here`, stub stays → "not heard here" + twice | fold first, then: fix → fed marker + `heard_here`, adopted, not fed-only; no fix → row 10 |

**Invariants (the test oracle, every row):**
- **I1** our position-less hearing is never discarded (history always receives it) and is the ONLY
  source of `heard_here` — any trust, any path.
- **I2** a node our radio hears is never counted federated-only, and appears in at most one of
  {features, served `nodes_without_position`} as OURS — except row 7's documented shape (stub ours +
  a federated marker at a peer fix; federation totals exclude it).
- **I3** a trust win never removes geometry without supplying geometry.
- **I4** at most one marker per (net,id).
- **F2 + its twin:** ONE winner rule (trust, then a `last_seen` compare that never raises — a
  non-numeric value loses) used by BOTH the poll merge (`map_federation.py` ~1076, same `or 0`
  compare, same crash) AND `better_fix`.

**Seam (BELIEVED design, red tests first):**
1. `map_federation.row_wins(candidate, current)` — the one winner rule; poll merge + `better_fix` call it.
2. `_fold_local_hearing(features, position_less)` — local-local, runs every cycle (federation on or
   off), after operator positions, before the merge: an LP whose (net,id) has an LF moves its
   `radio_last_heard` onto the feature and leaves the served list; the moved records go to
   `self._folded_hearings`, which the history loop reads beside `_nodes_without_position` (the ONE
   new consumer — history keeps the `local_radio` row, origin and all).
3. `_merge_federation` decides from a local view per key `{fkey, pl_idx, heard: bool}`; the peer
   path for any key we hear goes through ONE branch (rows 7–11), never the REPLACE path.
4. Line budget: `map_data_collector.py` is 1,499/1,500 (MF025) — collector edits net ≤ 0 lines; logic
   lives in `_map_collector_federation.py`.

**Acceptance (restated for row 10):** served `nodes_without_position` SHRINKS by the measured LF+LP
overlap (UNKNOWN — `curl` of the live geojson was refused by the session's safety layer 22:2x;
operator one-liner: `python3 <scratch>/lfp_measure.py`); history row count for those nodes
unchanged; page online counter unchanged; `position_adopted` a number. Review: Fable BY RUNNING,
export under `~/.cache/meshforge-review/`.

**r6 build + reviews (2026-11-20 22:4x–23:3x HST, uncommitted until a clean round):**
- r6 review r1 (Fable, by running) NOT clean → fixed red-first: F1 JSON-bigint `last_seen` →
  OverflowError (`_row_time` now UNKNOWN); F2 a lower-trust `best_fix` replaced our marker (trust =
  the FIX's own origin); F3 the fold hid our hearing → **operator option 1**: our marker carries
  `heard_here*` (one `heardHere()` phrasing in `nodeStatusText`), `is_online`/counts untouched; P1 the
  disk cache replayed a PEER's federated marker as a LOCAL feature (seam held 1 cycle) → replay skips
  federated; P2 operator-promoted nodes lost our hearing from history → the fold owns removal.
  Also: `meshcore_public` position-less stubs are NOT our hearing (`HEARING_ORIGINS`).
- r6 review r2 (Fable, by running) NOT clean, all r1 fixes held → fixed red-first: F1 the fold wrote
  into a SOURCE's dict (meshcore_public serves one cached list 600 s → stale hearing replayed) and
  the older `_attach` path did the same with `peer_heard` → ONE copy-on-write `_own_props`; newer
  stamp wins; F2 "Not heard here … · radio hearing unknown" → "Not heard in …"; F3 a promoted record
  with no `network` never folded (matches as `unknown`).
- **Deferred (not reachable by a known writer, or a separate slice):** row 2; duplicate GPS-less
  stubs per key (order-dependent); two local features per key; `row_wins` ≠ old rule only for
  `last_seen ≤ 0`/bool; **F4: MeshCore/RNS hearing records carry no radio stamp → option 1 can only
  say "radio hearing unknown" for them — stamping those writers is its own slice.**
- Reviewer r2 broke isolation (a probe outside pytest → ~44 real HTTP attempts to placeholder hosts
  `peer-a`/`peer-b`, none answered): briefs must say "scratch probes outside pytest lose
  `_federation_net_guard` — patch `fetch_peer_directory` and `_fetch_peer_status_extras`".
- **r6 DEPLOYED 2026-11-21 00:30–00:33 HST** (`e8c82d8d`, fleet 10/10, `honest_status` FULL exit 0 at 01:00).
  Live n=1 (VolcanoAI, operator ran the read-only script): I2 overlap 0; 15 our markers carry
  `heard_here`; `position_adopted` 20 = adopted markers carrying `heard_here`; online over our
  features + position-less 38 vs radio `num_online_nodes` 37 measured 11-20 15:28 — NOT same-instant,
  UNKNOWN. Browser (operator's screen): an adopted marker's status line read exactly as specified —
  VERIFIED; a folded (our) marker not yet seen.
- **Queued finding (pre-existing since `9672ca99`, made visible by r6):** the popup's "Last heard
  here" row on EVERY federated marker prints the peer's raw `last_seen` — its collector WRITE time
  (H14's wrong quantity), as an unformatted epoch (`web/node_map.html` `lastHeardText =
  props.last_seen || 'Unknown'`, formatted only when `last_heard` exists). Seen live under a status
  line saying "last 78 d ago". Fix: a federated marker's row = THIS radio's `heard_here_at` as an
  age, else "not heard by this radio"; never a peer write time, never a raw epoch.

## H4 survey — `last_heard or 0` consumers (2026-11-24, READ, no code)

Every server reader of `last_heard` already treats `<= 0` as unknown: the page (`web/node_map.html`
`ageOf`: `n <= 0 → null → 'Unknown'`), `_is_node_online` (`_map_collector_config.py:251`),
`cross_protocol_collapse._plausible_instant`, `node_history` (gates `0 < upstream`), the merge's
"fresher" compare. ONE exception: `_map_collector_trackers._too_old` (`now - 0 > window` → DROPS a
node whose age is unknown, against its own comment "unknown age is held") — its producer
`node_tracker.to_geojson:964` emits None, so BELIEVED unreachable today. → H4's live harm BELIEVED
nil; the cheapest real step is `_too_old` guarding `> 0` itself (red-first), NOT a 29-file
`or 0`→None migration. Upgrade to MEASURED: count `last_heard == 0` features per source on a live map.
