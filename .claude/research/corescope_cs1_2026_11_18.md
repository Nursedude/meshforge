# CoreScope / CS-1 primary-source read: 2026-11-18

> Session of 2026-11-18 (~16:00 to 18:20 HST). Read-only. Upstream code read at the pinned SHAs in
> § Sources. The community instance was queried with anonymous GETs only. meshanchor-server was read
> with `systemctl cat/status`, `pip show`, `ss -ltn` and file greps only. No port was opened, no
> client connected, nothing was written anywhere except this file.
> Prior notes re-used: `landscape_2026_11_04.md` (CoreScope row, MC-OBS/HI-OBS) and
> `plans/maps_audit_2026_11_06.md` § H16. Every fact below was re-read this session.

## Verdict

**The planned data path works as designed. No translation layer is needed. Five things must change
or be decided before the build.**

- **The shape fits (VERIFIED).** CoreScope's ingestor accepts any JSON on `meshcore/<IATA>/<observer>/packets`
  that carries a hex `raw` field. It decodes the packet itself; `SNR`, `RSSI`, `score`, `direction`,
  `origin` and `timestamp` are optional. The companion's 0x88 frame carries exactly those raw bytes.
  The same firmware hook (`Dispatcher::logRxRaw`) feeds both the repeater's `RAW:` log line, which
  meshcoretomqtt reads, and the companion's 0x88 push.
- **Prior art exists (VERIFIED).** `agessaman/meshcore-packet-capture` (MIT) already does
  "companion `RX_LOG_DATA` → meshcoretomqtt-shaped JSON". Its builder is commented "match mctomqtt.py
  exactly". It is also the one non-firmware observer on the Hawaii instance today. It cannot run beside
  MeshAnchor, though: both need the companion's one serial port (BELIEVED, single-owner serial). So
  MeshAnchor should publish itself, and copy that tool's payload as the reference.
- **Change 1: Docker is not installed on meshanchor-server (VERIFIED: no `docker`/`podman`).** CS-1 says
  "upstream image". That means installing a container runtime on a radio box (a new footprint), or
  running the upstream static arm64 binaries instead (`make crossbuild` exists; the release assets
  hold only `corescope-decrypt-*`). That is an operator call.
- **Change 2: the image bundles its own mosquitto (1883) and Caddy (80/443) (VERIFIED, image config).**
  The host mosquitto listens on loopback only (`127.0.0.1:1883`, `[::1]:1883`, VERIFIED). The
  container therefore needs `DISABLE_MOSQUITTO=true`, `DISABLE_CADDY=true`, and host networking (or a
  second listener) to reach it. With default bridge networking, a container cannot reach the host's
  127.0.0.1 (BELIEVED, Docker networking semantics).
- **Change 3: 0x88 has a size ceiling (VERIFIED in code).** The frame is sent only when
  `len + 3 <= MAX_FRAME_SIZE (176)`, so on-air packets over 173 bytes (up to `MAX_TRANS_UNIT` 255) are
  silently not logged. The observer cannot see those packets, and nothing counts them. The MeshAnchor
  side owes a witness: a count of seen frames by length, and a doc line saying >173 B is unobservable.
- **Change 4: retention is opt-in for packets (VERIFIED).** `retention.packetDays` defaults to 0, which
  means never delete, when unset. The example config uses 30. A pinned config must set it.
- **Change 5: privacy and identity (VERIFIED from CoreScope docs).** The observer id is the companion's
  public key, and observer pubkeys "are public on `/api/observers`". If the outward leg is ever turned
  on, the RAK4631's key becomes a public observer id on the community instance. That instance's
  default broker login is shared (personal logins are opt-in and EMQX-only).
- **No contradiction with "outward leg default OFF".** But the 11-04 note said "feed hawaiimesh, not a
  rival self-hosted instance". CS-1 (operator 11-18, "both") supersedes it. Also, ROADMAP PARITY-1
  says "References run in the lab only", and meshanchor-server declares no role (fleet_roles.yaml). Is
  running CoreScope there a "reference in the lab"? That is for the operator to settle (§ Open questions).

## 1. meshcoretomqtt: `Cisien/meshcoretomqtt` @ `9b4e022e` (2026-10-13), `mctomqtt.py` `__version__ = "1.3.0.0-preview"`

| Claim | Tag | Source |
|---|---|---|
| Topic templates (defaults): `status = meshcore/{IATA}/{PUBLIC_KEY}/status`, `packets = …/packets`, `debug = …/debug`, `raw = …/raw`. Only `{IATA}` and `{PUBLIC_KEY}` are substituted; per-broker `[broker.topics]` overrides, including a per-broker `iata` | VERIFIED | `config.toml.example` `[topics]`; `bridge/topics.py:19-48` |
| An unknown pubkey becomes the literal `UNKNOWN` in the topic | VERIFIED | `bridge/topics.py:33` |
| Packet JSON: `origin` (str, node name), `origin_id` (str, pubkey hex), `timestamp` (ISO-8601 with `+00:00`, from `datetime.now(timezone.utc)`), `type`=`"PACKET"`, `direction` `"rx"`/`"tx"`, `time` `"HH:MM:SS"`, `date` `"d/m/yyyy"`, `len`, `packet_type`, `route` (`F`/`D`), `payload_len`, `raw` (hex). RX adds `SNR`, `RSSI`, `score`, `duration`, `hash`; route `D` adds `path`. **Every numeric field is a JSON string** (regex groups), and missing groups are `null` | VERIFIED | `bridge/message_parser.py:27-93` |
| Status JSON: `status`, `timestamp`, `origin`, `origin_id`, `radio`, `model`, `firmware_version`, `client_version`, optional `stats` | VERIFIED | `bridge/mqtt_publish.py:216-232` |
| **Radio side: a REPEATER over USB serial, parsing TEXT log lines**, not a companion and not binary frames. Two regexes: `… U RAW: <hex>` and `… U: RX\|TX, len=… (type=…, route=…, payload_len=…) SNR=… RSSI=… score=… [time=…] [hash=…] [[path]]`. The README requires `-D MESH_PACKET_LOGGING=1` | VERIFIED | `bridge/message_parser.py:18-24`; README "Hardware Setup" |
| It sends CLI commands: `time`, `get name`, `get public.key`, **`get prv.key`** (for the auth token), `get radio`, `ver`, `board`, `stats-*` | VERIFIED | `bridge/serial_connection.py:93-240` |
| Auth: `password`, `token` (JWT signed by the device's Ed25519 private key, username `v1_<PUBKEY>`, 1 h TTL; `owner`/`email` claims only with TLS+verify), or `none` | VERIFIED | `bridge/mqtt_manager.py:300-345`; `auth_token.py:20-41` |
| TLS: `[broker.tls] enabled`/`verify` → paho `tls_set(CERT_REQUIRED)` or `CERT_NONE`; transport `tcp` or `websockets` | VERIFIED | `bridge/broker_client.py:83-89` |
| QoS: configured QoS 1 is **forced to 0** ("qos 1 can cause retry storms") | VERIFIED | `bridge/mqtt_publish.py:50-52` |
| Retain: packets are never retained (`safe_publish` default `False`). The "online" status published on connect and the LWT "offline" use `broker.retain`, **default True**. The periodic status uses `retain=False` | VERIFIED | `bridge/mqtt_manager.py:193-202, 375-396`; `mqtt_publish.py:93-99` |
| Licence: **MIT** ("Copyright (c) 2025 Andrew") | VERIFIED | `LICENSE`; GitHub API `license.spdx_id = MIT` |
| No GitHub release exists (`releases/latest` → 404); install is from the branch | VERIFIED | GitHub API |

**Implication for CS-1 (BELIEVED):** MeshAnchor does not run meshcoretomqtt; it reproduces the *shape*.
What CoreScope actually reads (§ 2) is a small subset: `raw` is required, and `SNR`/`RSSI`/`timestamp`/`origin`
are worth sending. Send numbers as JSON numbers or strings; CoreScope's `toFloat64` takes either
(BELIEVED from the name and use at `main.go:1140-1170`; the function body was not read).

## 2. CoreScope: `Kpa-clawbot/CoreScope`

Code read on `master` @ `b2c8693c` (2026-11-18). The ingest lines quoted below were re-checked at the
**v3.18.0 tag commit `12f9c5c6`** and sit at identical line numbers.

| Claim | Tag | Source |
|---|---|---|
| **Latest release v3.18.0, published 2026-11-17T16:15:52Z**, tag commit `12f9c5c6866693d4786ab5203066e69bd68263b6` | VERIFIED | GitHub API `releases/latest`; `git ls-remote` |
| Licence **GPL-3.0** (also the image label `org.opencontainers.image.licenses`) | VERIFIED | `LICENSE`; API; image config |
| Subscribes to the configured `topics` per source, default `meshcore/#`, at QoS 0 | VERIFIED | `cmd/ingestor/main.go:195-205` |
| Dispatch by topic: `meshcore/client/<pk>/{packets,rf,regions,names}` (a mobile-companion path, gated, off by default); `meshcore/status` and `meshcore/events/connection` skipped; `…/<obs>/neighbors`; `…/<obs>/status`; otherwise the packet path. `meshcore/message/channel/*` and `…/direct/*` are a second "companion bridge" format | VERIFIED | `main.go:924-1022, 1296, 1402` |
| **Packet path = "Format 1: Raw packet (meshcoretomqtt / Cisien format)"**: takes `msg["raw"]` (hex string) → `DecodePacket`. Optional `SNR`/`snr`, `RSSI`/`rssi`, `score`, `direction`, `origin`, `rx_radio_id`, `region`, `timestamp`. Observer id = topic `parts[2]`, region = `parts[1]` | VERIFIED | `main.go:1089-1185` |
| **It accepts meshcoretomqtt's shape unchanged** (the code names it as Format 1) | VERIFIED | `main.go:1089` |
| `timestamp` handling: >14 h future or >30 d past → ingest time; a naive (zone-less) value off by >15 min is clamped. Send an explicit `+00:00`/`Z` | VERIFIED | `main.go:1726-1760` |
| ADVERTs: ed25519 signature validated by default (`validateSignatures` default true), invalid ones dropped and recorded; geo-filter applies | VERIFIED | `main.go:1189-1225`; `config.go` `ShouldValidateSignatures` |
| Status: a retained replay, or a payload older than the liveness age, refreshes metadata but **does not stamp `last_seen`**. Fields read: `origin`, `noise_floor`, `tx_air_secs`, `rx_air_secs`, `battery_mv`, `uptime_secs`, `model`, `firmware`/`firmware_version`, `client_version`, `radio`, `stats` | VERIFIED | `main.go:1022-1080, 1532-1600`; `status_liveness.go:37-50` |
| Broker config: `mqttSources[]` = `{name, broker, username, password, rejectUnauthorized, topics, iataFilter, connectTimeoutSec, region, clientId}`. **Multi-broker: yes** (one client per source) | VERIFIED | `cmd/ingestor/config.go:20-32`; `main.go:160-215` |
| TLS: `ssl://` or `wss://` broker URL → system CA pool; `rejectUnauthorized:false` → `InsecureSkipVerify`. There is no client-cert option | VERIFIED | `main.go:810-822` |
| IATA: per-source `iataFilter` (packets only; status/neighbors exempt) plus global `observerIATAWhitelist` (all types) and `observerBlacklist` | VERIFIED | `main.go:983-1000, 1076-1087` |
| Env: `DB_PATH`, `MQTT_BROKER` (+ `MQTT_TOPIC`, which builds one source), `GOMEMLIMIT`, `CHANNEL_KEYS_PATH`, `CORESCOPE_INGESTOR_STATS`, `DISABLE_MOSQUITTO`, `DISABLE_CADDY`, `ENABLE_PPROF` | VERIFIED | `config.go:593-611`; `main.go:41,76,1836`; `docker/entrypoint-go.sh:28-42` |
| DB: SQLite, default `data/meshcore.db` (the image volume is `/app/data`) | VERIFIED | `config.go:610-611`; image config `Volumes` |
| Retention (**consumed by the ingestor only**): `nodeDays` default 7 (moved to inactive), `observerDays` 14, `metricsDays` default 30, **`packetDays` 0 = disabled when unset** (example sets 30), `observerPurgeDays` 0 | VERIFIED | `config.go:240-300, 395-410`; `config.example.json` `retention._comment` |
| Server memory caps: `packetStore.{retentionHours, maxMemoryMB, hotStartupHours}`, `runtime.maxMemoryMB`/`GOMEMLIMIT`. README: "set to ≥1.5× working set … Lower values trigger a GC death-spiral" | VERIFIED | `cmd/server/config.go:415-437`; README perf table |
| Image `ghcr.io/kpa-clawbot/corescope`: `v3.18.0` and `latest` are OCI indexes with **linux/amd64 and linux/arm64**, sharing the same digests (arm64 `sha256:7a496608d1fc…`) | VERIFIED | anonymous registry GET of the manifests, this session |
| The arm64 v3.18.0 image: layers sum to **62,837,043 B compressed**; entrypoint `/entrypoint.sh` (supervisord); exposes 1883/80/443; `CORESCOPE_SERVICE_USER=root` by default (`RUN_AS_UID` can change it) | VERIFIED | image manifest + config blob; `Dockerfile:168,197-206` |
| Footprint, documented: "~300 MB memory for 56K+ packets" (README); a 9.4 GB staging DB is mentioned | VERIFIED (their claim) | README; `cmd/ingestor/config.go` AnalysisLimit comment |
| Footprint, **measured on a live instance**: corescope.hawaiimesh.net reports RSS 222 MB, heap 137 MB, store 17,622 packets, `estimatedMB 138` (§ 4) | VERIFIED (their `/api/health`) | GET `/api/health` |
| Disk footprint for our volume, and RAM for one observer | UNKNOWN | measure after a soak: `du` of the data dir + RSS by day |
| Read API (all `GET`, no auth in the router): `/api/health` (status, version, commit, memory, packetStore), `/api/healthz` (readiness), `/api/stats` (totals, version), `/api/nodes`, `/api/nodes/{pubkey}`, `/api/nodes/{pubkey}/health`, `/api/nodes/network-status`, `/api/packets`, `/api/packets/{id}`, `/api/observers`, `/api/observers/metrics/summary`, `/api/scope-audit?window=1h\|24h\|7d&region=&mode=declared\|transport`, `/api/scope-stats`, `/api/config/regions`. Admin routes are wrapped in `requireAdmin` | VERIFIED | `cmd/server/routes.go:290-399`; `scope_audit.go:1092-1117` |
| Version lives in `/api/health` and `/api/stats` (`version`, `commit`, `buildTime`); there is no separate `/api/version` route | VERIFIED | `cmd/server/types.go:60-75, 223-230`; routes grep |
| No SDR/spectrum, Meshtastic or Reticulum ingest anywhere in `cmd/` or `internal/` | VERIFIED (grep, 0 hits) | grep at `b2c8693c` |

**GPL-3.0 if we only RUN it unmodified and read its API (BELIEVED, licence reading, not legal advice).**
GPL-3.0 obligations attach to *conveying* the program (§§ 4-6). Running it privately and reading its
HTTP API is a separate program talking over a network, not a derivative work. GPL-3.0 has no AGPL-style
network clause. MeshForge/MeshAnchor code stays under its own licence. If we ever redistribute a
modified image or binary, the source obligations apply. If we copy CoreScope *code* into our repos,
that code is GPL-3.0. PARITY-1's "copy, then match" must therefore copy *behaviour*, not source.

## 3. MeshCore companion 0x88: `meshcore-dev/MeshCore` @ `75b04ef0` (main, 2026-11-14, `FIRMWARE_VERSION "v1.18.0"`, ver code 14)

| Claim | Tag | Source |
|---|---|---|
| `#define PUSH_CODE_LOG_RX_DATA 0x88` | VERIFIED | `examples/companion_radio/MyMesh.cpp:125` |
| **Frame:** `[0]=0x88`, `[1]=int8(snr*4)` (0.25 dB steps), `[2]=int8(rssi)` (dBm, truncated), `[3..]=raw on-air packet bytes`, length `len` | VERIFIED | `MyMesh.cpp:291-302` |
| It is sent only if `_serial->isConnected() && len + 3 <= MAX_FRAME_SIZE`, where `MAX_FRAME_SIZE = 176` → **raw ≤ 173 B**. `MAX_TRANS_UNIT = 255`, so 174-255 B packets are never pushed, with no counter | VERIFIED | `MyMesh.cpp:292`; `src/helpers/BaseSerialInterface.h:5`; `src/MeshCore.h:23` |
| **Always on over USB serial: no enable flag, no `#if`.** `ArduinoSerialInterface::isConnected()` returns `true` ("no way of knowing, so assume yes") | VERIFIED | `MyMesh.cpp:291`; `src/helpers/ArduinoSerialInterface.cpp:16-18` |
| It fires from `Dispatcher::checkRecv()` on **every** received frame, *before* `tryParsePacket`, so frames that later fail to parse are included. The repeater's `MESH_PACKET_LOGGING` "RAW:" line is the same hook (`logRxRaw`) | VERIFIED | `src/Dispatcher.cpp:195-200`; `examples/simple_repeater/MyMesh.cpp:470-476` |
| RX only; the companion pushes no TX-log frame (CoreScope's `direction` is optional) | BELIEVED (no other `PUSH_CODE_LOG_*` in companion) | grep `MyMesh.cpp` |
| **Introduced** in commit `8f609524bd` (2025-03-09, "companion: added new PUSH_CODE_LOG_RX_DATA (0x88) for inspecting all received packets"). First tag containing it: **`companion-v1.2.2`** | VERIFIED | `git log -S` + `git tag --contains` on a full-history clone |
| Noise floor / airtime for the status topic: `CMD_GET_STATS` (56) type `STATS_TYPE_RADIO` returns `noise_floor` int16, last RSSI, last SNR×4, `tx_air_secs`, `rx_air_secs`, which are exactly CoreScope's status fields | VERIFIED | `MyMesh.cpp:58-73, 1928-1940`; CoreScope `main.go:1532+` |
| **meshcore_py exposes it as `EventType.RX_LOG_DATA` (`"rx_log_data"`)**, payload `{raw_hex, recv_time, snr, rssi, payload, payload_length, route_type, payload_type, path_len, path, …}`. Note `raw_hex` = everything after the 0x88 byte, *including* the SNR/RSSI bytes; **`payload` is the clean packet** to put in `raw` | VERIFIED | `meshcore_py` @ `50c92e96` `src/meshcore/reader.py:682-724`, `events.py:41`, `packets.py:137` |
| In meshcore_py since `55af0b2` (2025-04-12); first tag `v2.0.0` | VERIFIED | `git log -S RX_LOG_DATA` on full clone |
| **Installed on meshanchor-server: `meshcore 2.3.7`** in `/opt/meshanchor/venv` (the venv in `meshanchor.service` ExecStart). Its `reader.py:607-648` has the same `LOG_DATA → RX_LOG_DATA` branch; its parser has the short-payload guard. PyPI latest is 2.3.19 (2026-11-11) | VERIFIED | `ssh … pip show meshcore`; file grep; PyPI JSON |
| The serial port is held by `meshanchor-daemon.service` (`/opt/meshanchor/src/daemon.py start --foreground`, User=wh6gxz), **not** `meshanchor.service` (that is the orchestrator) | VERIFIED (`/proc/<pid>/fd` → ttyACM) | read-only `/proc` listing |
| MeshAnchor @ `c1685dcb` subscribes to no `RX_LOG_DATA` today (0 hits in `src/`) | VERIFIED | grep on the box |
| Companion firmware on the RAK4631: MeshAnchor state records `fw_build "19-Apr-2026"`; `firmware_version` is `null` in 1158 records | VERIFIED (record) / UNKNOWN (version string) | grep of `~/.local/share/meshanchor` |
| That build has 0x88 | BELIEVED: build date ≫ 2025-03-09 and the code is unconditional. Check: the first `rx_log_data` event after MeshAnchor subscribes |

`agessaman/meshcore-packet-capture` @ `122d959f` (MIT) subscribes to `EventType.RX_LOG_DATA`
(`src/meshcore_packet_capture/packet_capture.py:3811`). It prefers `payload`, falling back to
`raw_hex[4:]` (`:3585-3605`), and builds the mctomqtt-identical dict (`:3540-3557`). VERIFIED.

## 4. corescope.hawaiimesh.net: anonymous GETs, 2026-11-18 ~18:10 HST (server_time 2026-11-19T04:10Z)

| Claim | Tag | Source |
|---|---|---|
| Runs **CoreScope v3.18.0, commit `12f9c5c`, engine `go`**, buildTime 2026-11-17T15:42:27Z, uptime 20 h 28 m | VERIFIED | `GET /api/health` |
| Answer 200 without auth: `/api/healthz`, `/api/health`, `/api/stats`, `/api/observers`, `/api/nodes`, `/api/packets`, `/api/scope-audit`, `/api/scope-stats`, `/api/config/regions`, `/api/observers/metrics/summary`, `/api/config/client` | VERIFIED | GETs (status code + top-level keys only) |
| Counts: **8 observers** (by IATA: HNL 1, ITO 2, MUE 1, NGF 2, OGG 2), all seen in the last ~5 min. 68 nodes (45 repeaters, 18 companions, 5 rooms). 17,622 packets, 60,807 observations, 9,218 packets/24 h, 384 in the last hour. scope-audit 24 h lists 11 repeaters | VERIFIED | `/api/stats`, `/api/observers`, `/api/scope-audit` (aggregates only) |
| Region list: `HNL, ITO, MUE, NGF, OGG` | VERIFIED | `/api/config/regions` |
| Observer software: **7/8 run the Gessaman "observer" firmware** (`client_version meshcore/v1.17.1.x-observer-…` / `v1.18.0.x-observer-…`); **1/8 is `meshcore-packet-capture`** (the companion path); 0 run meshcoretomqtt | VERIFIED | `/api/observers` `client_version` (aggregated) |
| Observer hardware: Heltec V3 ×3, Heltec V4 OLED, Heltec V4.3 OLED, Heltec WSL3, LilyGo T-Beam, ThinkNode M7 | VERIFIED | `/api/observers` `model` (aggregated) |
| Community MeshCore radio: **910.525 MHz, BW 62.5, SF7**, CR 5 (6 of 8), CR 8 (2 of 8) | VERIFIED | `/api/observers` `radio` (aggregated) |
| `clientRxCoverage` and `clientRfSamples` are **off** on that instance | VERIFIED | `/api/config/client` |
| Its MQTT port / login policy (the prior note cites "MQTT 1884 per their wiki") | UNKNOWN this session. hawaiimesh.net is a Wiki.js SPA, and the static GET carried no page content. Not probed, per the rules | — |

## 5. Our lab hardware, and how our domain can be part of the Hawaii community

Operator 11-18: standalone MeshCore already works; the question is what OUR hardware and domain can
*contribute*. **Options only. Nothing here recommends reflashing anything.** ⚠️ Reflashing a fleet radio
changes which mesh it serves. The **HawaiiNet RNode (903.625 MHz) is a public contract**, and the
Meshtastic radios carry the two-preset fleet. **Neither may be repurposed.**

### 5a. Inventory (from `plans/bench_g_threat_model_2026_11_17.md` § Assets, measured 11-17; not re-measured)

| Box | Device | In use as | Can it be a MeshCore observer? | Tag + source |
|---|---|---|---|---|
| meshanchor-server | RAK4631 (nRF52840 + SX1262), `FB51466C…` | **MeshCore companion** (MeshAnchor) | **Yes, as-is**: 0x88 over USB is always on (§ 3). This is CS-1. No reflash needed | VERIFIED |
| meshanchor-server | the same RAK4631 → Gessaman Wi-Fi observer firmware | — | **No.** The observer-firmware releases list ESP32 boards only (Heltec V3/V4, LilyGo T3S3/T-Beam/TETH, RAK3112, Station G2/G3, Xiao S3), no RAK4631/nRF52 (no Wi-Fi). Upstream MeshCore has a `rak4631` variant (companion/repeater) | VERIFIED: `agessaman/MeshCore` releases `observer-mqtt-latest` (updated 2026-11-09) and `-beta-latest` asset names; `MeshCore/variants/rak4631` @ `75b04ef0` |
| VolcanoAI | Heltec V4 `80:F1:B2:A7:C3:94` | **HawaiiNet RNode (rnsd)** | Hardware-capable (`variants/heltec_v4`; `heltec_v4_repeater_observer_mqtt` exists). **NOT AN OPTION: it is the public RNS contract** | VERIFIED capability / operator rule |
| VolcanoAI | USB Toad (CH341 USB→SPI, `1a86:5512`, not a tty) | meshtasticd | No upstream MeshCore target: there is no Linux/portduino variant in `variants/`, and the Toad is host-driven SPI, not an MCU | BELIEVED (absence in `variants/` @ `75b04ef0`) |
| meshforge-lab | Genesis Mini (ESP32-S3-MINI-1-N4R2), `D0:CF:13:0A:68:68` | **bench dev board** (the only lab-role radio) | No MeshCore variant (0 matches in `variants/` @ `75b04ef0`, matching the 11-15 code search). Whether an LR1262 (SX1262) module is fitted: UNKNOWN. A port would be our own build, not upstream | VERIFIED absence / UNKNOWN fit |
| kiai | CP2102 `0001` | RNode | Board model UNKNOWN (CP2102 names no board). Fleet RNode: not an option | UNKNOWN model |
| moc5 | CP2102 `0001` (+ Espressif `44:1B:F6:6B:57:5C`) | identity the operator's to say | UNKNOWN | operator |
| moc2 | Espressif `80:F1:B2:A7:4D:80` (dudeclaw; the 06-21 doc says Heltec V4, ESP32-S3 + SX1262) | claw edge | Hardware-capable (heltec_v4 observer build exists). Its role (claw/RNS) is the operator's. Note: bench_g lists **one** Espressif by-id on moc2, the coordinator brief says 3 boards → re-count with `ls /dev/serial/by-id/` | BELIEVED (06-21 doc) / count UNKNOWN |
| moc1 / moc5 | RTL-SDR / Airspy | receive-only SDR | Not a MeshCore node (no TX, no MeshCore demod upstream). Its value is § 5b | VERIFIED (no TX) |

### 5b. What our domain adds beyond "one more observer"

| Our capability | What the community / CoreScope would consume | Tag |
|---|---|---|
| **The RAK4631 companion as observer #9** (0x88 → `meshcore/<IATA>/<pk>/packets` + `/status` with `noise_floor`/airtime from `CMD_GET_STATS`) | CoreScope Format 1 + the status fields (§ 2). Same consumer as their 8 observers | VERIFIED path; IATA UNKNOWN |
| **Multi-site observers.** Today one MeshCore radio. More sites would need more MeshCore radios, which the fleet does not have spare (§ 5a) | CoreScope's multi-observer trace and neighbor graph get better with more vantages (the prior note's CoreScope row) | BELIEVED; how many sites our fleet spans is the operator's to state |
| **SDR channel occupancy on the community MeshCore channel.** `src/utils/sdr_analysis.py:100-105` already watches `meshcore 910.525 / 62.5` at moc5, beside 903.625, 905.750 and 906.875 | CoreScope has **no** SDR/spectrum ingest (0 hits). The closest field is the observer `noise_floor` (dBm, from the radio). Ours is **dBFS at a fixed gain, not dBm** (`scripts/sdr_interference.py` docstring), so it does not map to that field. It would be shared as a published artifact or report (interference on 910.525, the Airspy spur map), not as a CoreScope topic | VERIFIED (absence + our units) / BELIEVED (form of sharing) |
| **Cross-mesh truth**: Meshtastic + RNS + MeshCore occupancy and health side by side, on shared 902-928 MHz spectrum | Nothing in CoreScope consumes it (MeshCore-only). The consumer is people: a band-sharing view for the Hawaii community (four fleet channels plus 910.525 in one band) | BELIEVED |
| **Honest "unobservable"**: e.g. the 0x88 >173 B blind spot, a companion that stops pushing, a retained status that is not liveness | CoreScope already refuses retained status as liveness (`main.go:1042-1060`). Upstream offer-back candidates: the 173 B ceiling (a MeshCore *finding*), and CS-ISSUE's two doc defects. Findings in the operator's words (memory rule) | VERIFIED (their liveness rule; our ceiling) / BELIEVED (offer value) |
| **Reading `/api/scope-audit` + `/api/observers` into the MOC** (CS-READ, now in CS-1) | Their public read API, no auth (§ 4). Consumes theirs, gives nothing back by itself | VERIFIED endpoints |

## Open questions for the operator

1. **Container runtime on meshanchor-server**: install Docker (new footprint on a radio box), or run
   CoreScope's static arm64 binaries under systemd? The upstream image cannot run without a runtime.
2. **PARITY-1 vs CS-1**: is meshanchor-server's own CoreScope a "reference in the lab", or a production
   service? The box declares no role today.
3. **IATA code** for meshanchor-server's observer: HNL / ITO / MUE / NGF / OGG?
4. **Observer identity**: OK for the RAK4631's public key to become a public observer id if the outward
   leg is ever turned on?
5. **Outward-leg credentials**: ask hawaiimesh for a personal login (CoreScope's `observerLogins`, which
   needs their EMQX + accounts), or use their shared login? TLS on their broker: UNKNOWN.
6. moc5's CP2102 `0001`, and how many dudeclaw boards moc2 carries (1 by-id measured 11-17).
7. Which sites does the fleet span, for the "multi-site" claim?

## What this does NOT establish

- It does not show that our RAK4631 *emits* 0x88 today. Nothing subscribed and nothing was observed. That
  is code + build date only. The check is the first `rx_log_data` event once MeshAnchor subscribes.
- No CoreScope instance of ours was run. The RAM/disk numbers are the community instance's and the README's
  (eight observers, not one), so our footprint is UNKNOWN until soaked.
- Not read: the hawaiimesh wiki content (an SPA), and their broker port/TLS/ACL.
- The CoreScope `toFloat64`, `DecodePacket` and `BuildPacketData` bodies were not read. Behaviour on
  malformed `raw` was taken from the call site only.
- How often >173 B packets occur on the Hawaii mesh: UNKNOWN (measure by length histogram).
- The GPL reading is not legal advice.
- The agessaman observer firmware was read through its release asset names only, not its source.

## Sources (pinned)

- `github.com/Cisien/meshcoretomqtt` @ `9b4e022e298020ef3359b36d8c364931de058af5`
- `github.com/Kpa-clawbot/CoreScope` @ `b2c8693cbc18f3a042649b58db6946893f0684f3` (master) and tag v3.18.0 → `12f9c5c6866693d4786ab5203066e69bd68263b6`
- `ghcr.io/v2/kpa-clawbot/corescope/manifests/v3.18.0` (OCI index; arm64 `sha256:7a496608d1fc50e1d2607f02ed51748f5fbdf865d523d00bfeb81bf382f14c25`)
- `github.com/meshcore-dev/MeshCore` @ `75b04ef06def54b348400cb1b138afb6749ffd4f`; 0x88 intro `8f609524bda17154ec6290a570ad83ab5e82263e`
- `github.com/meshcore-dev/meshcore_py` @ `50c92e960e0002d0a3a34af0fda23a518ead6331`; RX_LOG_DATA intro `55af0b2`
- `github.com/agessaman/meshcore-packet-capture` @ `122d959f047cb1750218abfadbab7e05bc6c9ba2`
- `github.com/agessaman/MeshCore` releases `observer-mqtt-latest`, `observer-mqtt-beta-latest` (asset lists, API)
- `https://corescope.hawaiimesh.net/api/{health,healthz,stats,observers,nodes,packets,scope-audit,scope-stats,config/regions,config/client,observers/metrics/summary}` (GETs, 2026-11-19T04:10Z)
- meshanchor-server: `systemctl cat meshanchor.service` / `meshanchor-daemon.service`, `pip show meshcore`, `ss -ltn`, `dpkg-query -W mosquitto` (2.0.21-1), MeshAnchor @ `c1685dcb`

## Decisions (operator 2026-11-18, after this research)

- **IATA = `ITO`** for meshanchor-server's observer (operator).
- **No container runtime: CoreScope runs as upstream binaries under systemd** (operator leaned binaries,
  "your call"; recommendation taken). The binaries are **extracted from the pinned upstream arm64 image
  by digest** (`sha256:7a496608d1fc…`, v3.18.0), not rebuilt by us: upstream builds them with
  `CGO_ENABLED=1`, `CC=zigcc`, tags `netgo,osusergo,sqlite_omit_load_extension` and version ldflags
  (`Dockerfile` @ `12f9c5c6` lines 29, 57, 82-125), and neither box has Go. A registry blob pull needs
  no runtime. UNKNOWN until extracted: whether they are static or need host libs (`file`/`ldd` them).
- **What the image's entrypoint did must move into the unit, explicitly** (`docker/entrypoint-go.sh` @
  `12f9c5c6`, 42 lines): config lookup at `/app/data/config.json`; supervisord running ingestor + server
  (+ mosquitto + Caddy unless `DISABLE_*`); `CORESCOPE_SERVICE_USER=root` default. Ours: two units
  (ingestor, server), a non-root user, `Environment=`/`EnvironmentFile=` spelled out, config path
  absolute, our host mosquitto, no Caddy. A parity check compares the image's `Env`/`Cmd`/supervisord
  programs with our units at each pin bump.

### Binaries extracted and inspected (2026-11-18 19:2x HST by `date`, VolcanoAI scratch, nothing executed)

- **Pulled anonymously from ghcr, no container runtime:** arm64 manifest `sha256:7a496608d1fc…`
  (= the digest above), 20 blobs (config + layers), each sha256 re-checked against its digest.
- **`/app/corescope-server`, `/app/corescope-ingestor`, `/app/corescope-decrypt`: ELF 64-bit aarch64,
  statically linked, stripped, no dynamic section, no PT_INTERP** (VERIFIED, `file` + `readelf -d/-l`).
  No host libraries needed. sha256 prefixes: server `e7d310fc07f68ec5`, ingestor `03e7bcba01321486`,
  decrypt `026b3bc984265193`.
- The server also needs `/app/public/` (the web UI assets) beside it. `/app/.image-version` = `v3.18.0`;
  `/app/.git-commit` = `unknown` (the live `/api/health` commit comes from build flags, not this file).
- **Image config:** `User` unset (root), `Env` = PATH + `CORESCOPE_SERVICE_USER=root`, `Entrypoint`
  `/entrypoint.sh`, `WorkingDir /app`, volumes `/app/data`, `/data/caddy`.
- **What our units must reproduce** (supervisord `supervisord-no-mosquitto-no-caddy.conf`, VERIFIED):
  `corescope-ingestor -config /app/config.json` and `corescope-server -config-dir /app -db
  /app/data/meshcore.db -public /app/public -port 3000`, both `directory=/app`, autorestart,
  `stopsignal=TERM`, `stopwaitsecs=20`. Our paths replace `/app` with an absolute install dir; the
  server port (3000) must not clash with anything on meshanchor-server (check `ss -ltn` before install).

### Correction (operator 2026-11-18)

- The operator has **no account** on corescope.hawaiimesh.net (an earlier message said they did). Its login
  page offers passkey login and account creation. The community's "run an observer" path is on
  hawaiimesh.net (the wiki) and corescope.hawaiimesh.net. Until an account exists, the outward leg could
  use only the shared community login, so it stays off by default.
