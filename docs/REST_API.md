# MeshForge REST API

> **Generated against:** <!--STAT:version-->0.7.0-beta<!--/STAT--> — the endpoint index
> below is GENERATED from the route code by `scripts/rest_doc.py` and pinned by
> `tests/test_rest_doc.py`; a route added or removed without `--update` fails CI.
> Until 2026-10-22 this page documented 29 endpoints (the system, tools,
> analytics, service and config families) that no server routed; they were
> removed, not kept as "planned".

---

## Overview

Three HTTP servers ship with MeshForge:

- **Map service** (`meshforge-map`, port 5000) — the web map, the `/inbox`
  chat page, the `/fleet` NOC page, node/network/message JSON, and a proxy to
  meshtasticd's own HTTP API. Bound on the LAN; reads and writes are gated as
  described below.
- **Config API** (the `meshforge` daemon, `127.0.0.1:8081`, loopback only) —
  read and write gateway settings by dotted path (`/config/rns.port`).
- **Prometheus exporter** (default port 9090) — `/metrics` plus JSON and
  Prometheus-query shims for Grafana.

Responses are JSON unless the route serves a page. Errors carry an `error`
field with a description.

---

## Authentication

**Reads need no login** (they stay gated by the Host rule below and, for the
sensitive tier, by the box's trusted LAN). **Every write needs an operator
session** — default-deny: any `POST`/`PUT`, plus the side-effect reads
`/fleet/logs`, `/api/v1/fromradio` and `/json/blink`. Without one the map
answers `401`:

```json
{"error": "operator login required",
 "how": "on the box: python3 scripts/meshforge_web.py pair, then open /login",
 "detail": "no live operator session in this browser"}
```

Getting a session (once per box, per browser):

1. On the box, as the operator (not sudo): `python3 scripts/meshforge_web.py pair`
   prints a one-time code (8 characters, 5 minutes, burns after 5 wrong tries).
2. Open `http://<box>:5000/login` from the box's LAN and type it. The browser
   gets an `HttpOnly; SameSite=Strict` cookie that lasts 30 days from last use.
3. `python3 scripts/meshforge_web.py revoke-all` logs every browser out;
   `status` shows the session count.

A write must also carry this page's `Origin` — a copied cookie in `curl` is
refused. `GET /api/auth/status` tells a page which mode it is in, plus the
refusal count. The cookie travels over plain HTTP: use this on a LAN you
control, never a forwarded port.

### The Host rule (DNS-rebinding defence)

The map answers a request only when the `Host` it was addressed to is an IP
literal, a bare name (`moc`), or a local-only name (`.local`, `.home.arpa`,
`.internal`, `.local.mesh`). Anything else — a public name — gets `403`, on
every route except `/healthz` and `/metrics`. A public name is exactly what a
DNS-rebinding page sends: a hostile site re-points its own name at this box and
then reads the API from your browser's trusted address.

**Behind a reverse proxy**, forward the upstream's own host, not the browser's:

- **nginx** — the default (`proxy_set_header Host $proxy_host`) already does.
- **Caddy** — forwards the browser's Host by default; add
  `header_up Host {upstream_hostport}` to the `reverse_proxy` block.

---

## Endpoints

`*` marks a prefix route: everything under it is handled by the same code.
"Served by" is the first docstring sentence of the function that answers.

<!-- BEGIN GENERATED: scripts/rest_doc.py --update — do not edit by hand -->

### Map service — `meshforge-map`, port 5000

| Method | Path | Login | Served by |
|---|---|---|---|
| GET | `/` | — | Serve the node_map.html file. |
| GET | `/api/auth/status` | — | Which mode is this browser in — so the page can show it. |
| GET | `/api/coverage/*` | — | Serve terrain-aware coverage prediction for a location. |
| GET | `/api/fleet/truth` | — | Serve the honest fleet-truth SSOT (`/api/fleet/truth`). |
| GET | `/api/gateway/delivery` | — | Fork C — honest delivery lifecycle counters. |
| GET | `/api/gateway/queue` | — | Queue backpressure stats for the watchdog (Issue #74). |
| GET | `/api/inbox` | — | GET /api/inbox — conversations, gated like /api/messages/received. |
| GET | `/api/los/*` | — | Serve line-of-sight analysis between two points. |
| GET | `/api/messages/queue` | — | Serve pending messages from the gateway message queue. |
| GET | `/api/messages/received*` | — | Serve received (inbound) messages from the messages database. |
| GET | `/api/messages/rx-status` | — | Serve the RX (message listener) status. |
| GET | `/api/network/interfaces` | — | Read-only snapshot of RNS.Transport.interfaces. |
| GET | `/api/network/rns/paths` | — | Read-only snapshot of RNS Transport.path_table. |
| GET | `/api/network/topology` | — | Serve network topology data for D3.js visualization. |
| GET | `/api/nodes/directory` | — | Serve the persistent node directory as a GeoJSON FeatureCollection. |
| GET | `/api/nodes/geojson` | — | Serve live node GeoJSON with optional bbox/region/preset filtering. |
| GET | `/api/nodes/history` | — | Serve node history summary and unique nodes list. |
| GET | `/api/nodes/snapshot*` | — | Serve a historical network snapshot for playback. |
| GET | `/api/nodes/trajectory/*` | — | Serve trajectory GeoJSON for a specific node. |
| GET | `/api/proxy/status` | — | Serve API proxy status and statistics. |
| GET | `/api/radio/channels` | — | Serve channels from the connected radio — through a short cache. |
| GET | `/api/radio/info` | — | Serve radio device information. |
| GET | `/api/radio/nodes` | — | Serve nodes from directly connected radio. |
| GET | `/api/radio/status` | — | Serve radio connection status. |
| GET | `/api/region-presets` | — | Serve available region preset definitions. |
| GET | `/api/settings` | — | Serve current map settings (selected region). |
| GET | `/api/status` | — | Serve server status including radio connection info. |
| GET | `/api/v1/fromradio*` | operator | Serve multiplexed FromRadio packets via the API proxy. |
| GET | `/api/weather` | — | Serve space weather and HF band conditions for map overlay. |
| GET | `/api/websocket/status` | — | Serve WebSocket server status and connection info. |
| GET | `/fleet` | — | Serve the honest fleet NOC visual (`/fleet` → web/fleet.html). |
| GET | `/fleet/cascade` | — | Cascade detector snapshot — pre-failure fingerprints state. |
| GET | `/fleet/delivery` | — | Serve THIS box's delivery view — the domain's END — as JSON. |
| GET | `/fleet/dups` | — | Serve the cross-box dedup rollup (dedup/identity arc STEP 4c). |
| GET | `/fleet/logs` | operator | Return recent journalctl lines for an allowlisted unit. |
| GET | `/fleet/slo` | — | Serve the MeshAnchor `/fleet/slo` peer contract. |
| GET | `/fleet/tests` | — | Return the list of allowlisted tests + last-fire info per unit. |
| GET | `/fleet/tracer-fires` | — | Return this host's recent tracer fires for one peer. |
| GET | `/fleet/uplink` | — | Serve THIS box's Starlink obstruction map, live from the dish. |
| GET | `/fleet/wan` | — | Serve the WAN ladder (utils.wan_path): latest run + 24h history. |
| GET | `/healthz` | — | Cold-start-safe health endpoint. |
| GET | `/inbox` | — | Serve the browser chat inbox (`/inbox` → web/inbox.html, BIC-1). |
| GET | `/index.html` | — | Serve the node_map.html file. |
| GET | `/json/blink` | operator | Proxy a /json/* endpoint from meshtasticd. |
| GET | `/json/nodes` | — | Proxy a /json/* endpoint from meshtasticd. ⚠️ meshtasticd never serves it (ESP32-only, #76) — expect 404 |
| GET | `/json/report` | — | Proxy a /json/* endpoint from meshtasticd. ⚠️ meshtasticd never serves it (ESP32-only, #76) — expect 404 |
| GET | `/lab/rollup` | — | Serve the lab rollup markdown produced by meshforge-lab-rollup. |
| GET | `/lab/rollup/alphabetical` | — | Serve the lab rollup markdown produced by meshforge-lab-rollup. |
| GET | `/lab/synth-rollup` | — | Serve the lab rollup markdown produced by meshforge-lab-rollup. |
| GET | `/login` | — | The pairing-code login page (BIC-3). |
| GET | `/mesh` | — | Serve the Meshtastic web client from disk. |
| GET | `/mesh/*` | — | Serve the Meshtastic web client from disk. |
| GET | `/metrics` | — | Prometheus scrape endpoint (/metrics). |
| POST | `/api/inbox/send` | operator | POST /api/inbox/send — same gates as /api/radio/message (it keys a licensed transmitter), then ``inbox_api`` validates, sends, records. |
| POST | `/api/radio/message` | operator | Handle POST /api/radio/message - send a message via radio. |
| POST | `/api/settings` | operator | Handle POST /api/settings — save map settings. |
| POST | `/api/v1/toradio*` | operator | Forward ToRadio protobuf to meshtasticd via the proxy. |
| POST | `/fleet/run-test` | operator | POST /fleet/run-test — fire an allowlisted lab unit. |
| POST | `/json/blink` | operator | Proxy a POST JSON endpoint to meshtasticd (blink, restart, etc.). |
| POST | `/login` | — | Trade a one-time pairing code for an operator session cookie. |
| POST | `/logout` | — | End this browser's operator session. |
| POST | `/mesh/api/v1/toradio*` | operator | Forward ToRadio protobuf to meshtasticd via the proxy. |
| POST | `/mesh/json/blink` | operator | Proxy a POST JSON endpoint to meshtasticd (blink, restart, etc.). |
| POST | `/mesh/restart` | operator | Proxy a POST JSON endpoint to meshtasticd (blink, restart, etc.). |
| POST | `/restart` | operator | Proxy a POST JSON endpoint to meshtasticd (blink, restart, etc.). |
| PUT | `/api/v1/toradio*` | operator | Forward ToRadio protobuf to meshtasticd via the proxy. |
| PUT | `/mesh/api/v1/toradio*` | operator | Forward ToRadio protobuf to meshtasticd via the proxy. |

- `HEAD` on any path: HEAD on the static tree (stdlib) — behind the same Host rule.
- `OPTIONS` on any path: Handle CORS preflight requests.
- Any other `GET` path falls through to the stdlib static-file server over `web/`.

### Prometheus exporter — default port 9090

| Method | Path | Served by |
|---|---|---|
| GET | `/` | Serve index page with available endpoints. |
| GET | `/api/json/meshcore*` | Serve ``/api/json/meshcore[/contacts]`` on a MetricsHTTPHandler. |
| GET | `/api/json/metrics` | Serve metrics as JSON for Grafana Infinity plugin. |
| GET | `/api/json/nodes` | Serve node data as JSON for Grafana. |
| GET | `/api/json/status` | Serve system status as JSON. |
| GET | `/api/v1/label*` | Serve Prometheus label values API for Grafana variable queries. |
| GET | `/api/v1/labels*` | Serve Prometheus labels API for Grafana compatibility. |
| GET | `/api/v1/query*` | Serve Prometheus query API for Grafana compatibility. |
| GET | `/api/v1/query_range*` | Serve Prometheus query_range API for Grafana time-series panels. |
| GET | `/health` | Serve health check endpoint. |
| GET | `/healthz` | Serve health check endpoint. |
| GET | `/metrics` | Serve Prometheus metrics. |
| POST | `/api/json/metrics` | Serve metrics as JSON for Grafana Infinity plugin. |
| POST | `/api/json/nodes` | Serve node data as JSON for Grafana. |
| POST | `/api/json/status` | Serve system status as JSON. |
| POST | `/api/v1/label*` | Serve Prometheus label values API for Grafana variable queries. |
| POST | `/api/v1/labels*` | Serve Prometheus labels API for Grafana compatibility. |
| POST | `/api/v1/query*` | Serve Prometheus query API for Grafana compatibility. |
| POST | `/api/v1/query_range*` | Serve Prometheus query_range API for Grafana time-series panels. |
| POST | `/metrics` | Serve Prometheus metrics. |

- `OPTIONS` on any path: Handle OPTIONS request (CORS preflight).

### Config API — `meshforge` daemon, 127.0.0.1:8081

| Method | Path | Served by |
|---|---|---|
| DELETE | `/config/{dotted.path}` | Delete configuration value at path (reset to default). |
| GET | `/config/_audit` | Get recent configuration changes. |
| GET | `/config/_paths` | List all configuration paths. |
| GET | `/config/{dotted.path}` | Get configuration value by dot-notation path. |
| PATCH | `/config/{dotted.path}` | Partially update configuration at path. |
| POST | `/config/_reset` | Reset configuration to defaults. |
| PUT | `/config/{dotted.path}` | Set configuration value at path. |

<!-- END GENERATED -->

---

## Examples

```bash
# server + radio status (no login needed for reads)
curl http://<box>:5000/api/status

# live nodes as GeoJSON
curl http://<box>:5000/api/nodes/geojson

# one gateway setting, from the box itself
curl http://127.0.0.1:8081/config/rns.port
```
