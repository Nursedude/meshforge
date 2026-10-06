# EcoFlow telemetry for a Pi shutdown trigger: research, 2026-10-05

> Read-only web and source research. No device contacted, nothing installed, no account created.
> Tags: **VERIFIED-src** means read in the cited primary source or repo at the cited commit this session.
> **ASSERTED** means my reasoning, not measured. **UNKNOWN** means no source says.

## Verdict

1. **The cloud API needs the internet, so it cannot be the outage trigger here.** EcoFlow's official
   IoT Open Platform (HTTP plus MQTT, access key and secret key, HMAC-SHA256 signing) supports both
   candidate models. It is cloud-only (`api.ecoflow.com` and `mqtt.ecoflow.com`). The Starlink path runs
   off the yurt EcoFlow, so the WAN is the first thing an outage can take down. ASSERTED: when the WAN
   dies, the API goes UNKNOWN, not "on battery". Use it as a secondary or enrichment path only.
2. **No supported local LAN API exists.** A local TCP port (8055) was reverse-engineered on DELTA Pro
   and DELTA Max Wi-Fi. EcoFlow firmware closed it in 2022, it reportedly came back on one DELTA Pro
   WLAN firmware in 2023, and the project was archived in 2023. Treat it as dead or unreliable.
3. **BLE is the only fully local path.** `rabits/ha-ef-ble` is active (v1.2.0, 2026-10-01) and lists
   **DELTA Pro** but **not DELTA Max**. The device allows only **one BLE connection at a time**, so the
   phone app and the Pi conflict.
4. **Which models these are is UNKNOWN.** "1600" most likely means **DELTA Max 1600** (1612 Wh, an
   older Gen-1 unit). It is unlikely to be a DELTA 2 Max (2048 Wh). "3600" most likely means **DELTA Pro**
   (3600 Wh). DELTA Pro 3 holds 4096 Wh but also has 3600 W AC output, so that reading stays possible.
   **Confirm both from the label or the app's device-info page before building anything.** On the
   current data, the yurt unit (the one that matters for Starlink) has a BLE path, and the tent unit
   (if it is a DELTA Max) has only the cloud path.
5. **A cheap fallback that needs no vendor dependency** (ASSERTED, a different method): let the Pi
   watch something the grid powers, upstream of the EcoFlow. For example, a mains-powered LAN device
   that stops answering. That gives you "AC-in lost" without any EcoFlow protocol.

## 1. Official developer platform (IoT Open Platform)

- **It exists.** Portals: `https://developer.ecoflow.com` (US/global) and `https://developer-eu.ecoflow.com`
  (EU). Both are JavaScript single-page apps, and WebFetch and curl returned only the shell, so **I could
  not read the official text directly this session (UNKNOWN for verbatim official wording)**. Everything
  below comes from client code that implements the platform.
- **Auth** (VERIFIED-src, `tolwi/hassio-ecoflow-cloud` `api/public_api.py` @ `38986e6e`):
  - Every request carries the headers `accessKey`, `nonce`, `timestamp` and `sign`.
  - `sign` is the hex HMAC-SHA256, keyed by the secretKey, over this string:
    `<sorted k=v params>&accessKey=..&nonce=..&timestamp=..`. The query parameters are sorted; the three
    auth fields are appended after them, not sorted in.
  - Keys come from the developer console after an approval that "can take a few days" (search-result
    summary of the platform docs; not read verbatim).
- **HTTP endpoints** (VERIFIED-src, same file):
  - `GET https://{api.ecoflow.com|api-e.ecoflow.com}/iot-open/sign/device/quota/all?sn=<SN>` returns a
    full snapshot of the device's state values.
  - `GET /iot-open/sign/certification` returns MQTT credentials.
  - Write endpoints also exist (`PUT /iot-open/sign/device/quota`, per apis.io). **Never use them for
    this purpose.**
- **MQTT** (VERIFIED-src): topics are `/open/<certAccount>/<SN>/quota` (pushed telemetry) and
  `/open/<certAccount>/<SN>/status` (online/offline). The broker is TLS on port 8883, per the
  `MichelFR/ha-ecoflow-iot` README.
- **Supported models** (VERIFIED-src: public-API device classes exist in `tolwi` `devices/public/`):
  `delta_pro.py`, `delta_max.py`, `delta2_max.py`, `delta_pro_3.py`, plus others. The openHAB binding
  lists only Delta 2, Delta 2 Max and PowerStream.
- **Field names** in quota keys (VERIFIED-src, tolwi device definitions @ `38986e6e`):

| Need | DELTA Pro / DELTA Max | DELTA 2 Max | DELTA Pro 3 |
|---|---|---|---|
| SoC % | `ems.lcdShowSoc` (combined), `bmsMaster.soc` | `bms_emsStatus.lcdShowSoc`, `bms_bmsStatus.soc` | `cmsBattSoc`, `bmsBattSoc` |
| AC-in watts | `inv.inputWatts` | `inv.inputWatts` | `powGetAcIn` |
| AC-in voltage (grid present) | `inv.acInVol` (mV) | `inv.acInVol` (mV) | `plugInInfoAcInFeq` (freq) |
| Total in / out W | `pd.wattsInSum` / `pd.wattsOutSum` | same | `powInSumW` / `powOutSumW` |
| AC out W | `inv.outputWatts` | `inv.outputWatts` | `powGetAc` |
| Runtime remaining | `ems.dsgRemainTime` (min) | `bms_emsStatus.dsgRemainTime` | `cmsDsgRemTime` |

  There is no field that literally means "grid present". ASSERTED: derive it from `inv.acInVol > 0` or
  `inv.inputWatts > 0`. Firmware may report a small standby input, so set the threshold empirically.
  The tolwi code shows that DELTA 2 Max public data is flattened from the private names (`to_plain`),
  so verify the exact keys with one `quota/all` call.
- **Rate limits: UNKNOWN.** No primary source stated them. A search summary mentioned daily MQTT
  client-ID limits but gave no figure. The openHAB doc says that one developer account "can not be used
  multiple times in parallel, as doing so will disturb event updates". **That matters if Home Assistant
  or another tool already uses the same keys.**
- **Needs internet: yes.** Every host is a public EcoFlow cloud name (VERIFIED-src). tolwi's README says
  it connects via "EcoFlow's MQTT broker at mqtt.ecoflow.com … not local network connectivity".

## 2. Local options (no cloud)

| Project | Path | Models | Status | Notes |
|---|---|---|---|---|
| `vwt12eh8/hassio-ecoflow` | **Local TCP :8055** (private binary protocol) | RIVER Max/Pro, DELTA Mini/Max, DELTA Pro (Wi-Fi) | **Archived**, last push 2023-05-04 | Disc. #58 (2022-09-22): "local APIs are no longer available after updating to the latest firmware". Issue #69 (2023-02-14): the port is "free again" on DELTA Pro WLAN V3.0.2.21. Search results also report that the local port works only while the device can reach the cloud (UNKNOWN; not primary). |
| `rabits/ha-ef-ble` | **BLE**, reverse-engineered, encrypted packets | DELTA Pro, Delta 2 (Max), Delta 3 family, Delta Pro 3, DPU, River 2/3, … **not DELTA Max** | **Active**: v1.2.0 2026-10-01, HEAD `511e0470` 2026-10-03, 399 stars | Needs the device bound to your account, plus your EcoFlow **User ID** (fetched once by a login during setup). The device accepts **one BLE connection at a time**. Firmware updates may break it. The `eflib/` package uses `bleak`; the files I read import no Home Assistant code, so it is BELIEVED reusable outside HA (not checked across all files). Delta Pro sensors include Battery Level, AC Input Power/Voltage and Output Power. Runtime remaining is not listed for Delta Pro and is listed as disabled-by-default for Delta 2/Max. |
| `tolwi/hassio-ecoflow-cloud` | Cloud: official public API **or** private app API | Very broad, incl. DELTA Pro, DELTA Max, DELTA 2 Max, DP3 | **Active**: v1.7.1 2026-08-18, HEAD `38986e6e` 2026-10-02, 898 stars | Cloud only |
| `MichelFR/ha-ecoflow-iot` | Cloud: official API, MQTT-first with HTTP fallback | Delta 2/2 Max/Pro/Pro 3/… | Active (push 2026-10-04), small (12 stars) | Cloud only |

There is no official local LAN API and no local MQTT broker option documented by any source found
(UNKNOWN that one exists). Redirecting the device's MQTT to a local broker is discussed as theoretical in
`nielsole/ecoflow-bt-reverse-engineering`. Nothing production-grade turned up.

## 3. Failure modes for an outage trigger

- **WAN down means the cloud is blind** (ASSERTED from the architecture). The yurt EcoFlow powers
  Starlink. Any WAN loss (Starlink outage, the EcoFlow cutting AC out, or a brownout) removes the cloud
  path at exactly the moment you need it. MQTT staleness and HTTP errors must read as **UNKNOWN**, never
  "on grid". Per honest_failure_modes #2, a missing reading is not evidence of grid power. Per the
  persistent_issues WAN-outage row, the Pi clocks may also go stale.
- **Asymmetric hazard** (ASSERTED): "cloud unreachable plus local grid probe down" should still let the
  timer run on local evidence. A shutdown policy that waits for cloud confirmation never fires during a
  WAN-dark outage.
- **BLE at about 10 m** (ASSERTED, physics): BLE at 10 m line-of-sight is routine. Through tent fabric
  it is likely fine. Through a yurt wall plus the metal case of the station it is marginal, so measure
  RSSI from the intended Pi spot. Pi 4/5 onboard Bluetooth shares an antenna with 2.4 GHz Wi-Fi, so a USB
  BT dongle with a better antenna, or an ESPHome BT proxy, is the usual fix (ha-ef-ble notes its
  proxy limits only for the Smart Home Panels).
- **One BLE client at a time** (VERIFIED-src): while the Pi holds the link, the owner's phone app cannot
  use BLE. The phone's Wi-Fi/cloud view still works while the WAN is up (ASSERTED).
- **Firmware drift** (VERIFIED-src, both reverse-engineered projects warn): an OTA update can break BLE
  or close a local port. Pin it by disabling auto-update in the app if that option exists (UNKNOWN).
- **Credential parallelism** (VERIFIED-src, openHAB): reusing one developer key pair across clients
  disrupts updates.

## 4. Recommendation (a recommendation, not a measurement)

**Prototype first: the BLE path against the yurt unit, after confirming it is a DELTA Pro.** Use
`rabits/ha-ef-ble`'s `eflib` from a standalone Python script on the nearest Pi, read-only: no setter
calls, connect, subscribe, log SoC / AC-in W / out W. Then measure the RSSI and the reconnect behaviour
over 24 h. This is the only path that survives the WAN dying, and the yurt unit is the one whose outage
also kills Starlink. **In parallel, cheap and independent:** add a grid-presence probe that does not
depend on EcoFlow at all (a mains-powered device upstream of the station that the Pi pings) as the
primary "AC lost" signal. Treat EcoFlow SoC/runtime as enrichment that sets the shutdown delay.
**Use the official cloud API only for the tent unit** if it is a DELTA Max (no BLE support listed), and
only as a degraded-trust input, since it is blind during the outage it is meant to detect.

- VERIFIED-src: the platform's existence, the endpoints, the signing method, the field names (from client
  code @ commit), the BLE model list and the one-connection limit, the archival of the local-port project
  and the firmware closure.
- ASSERTED: the model identification (1600 = DELTA Max, 3600 = DELTA Pro), cloud blindness during WAN
  loss as a practical matter, BLE range through the walls, the grid-present threshold, and `eflib` being
  usable outside HA.
- UNKNOWN: the official rate limits, the verbatim official docs (the portal is a JS SPA), whether
  :8055 is open on current firmware, and the actual models.

## Sources

- EcoFlow developer portal (JS SPA, content not readable by fetch): https://developer.ecoflow.com/us/document/introduction , https://developer-eu.ecoflow.com/us/document/generalInfo
- tolwi/hassio-ecoflow-cloud @ `38986e6e2534037d039cb4e6e3eb2165a8e40c1d` (2026-10-02), release v1.7.1 (2026-08-18): https://github.com/tolwi/hassio-ecoflow-cloud
  - `custom_components/ecoflow_cloud/api/public_api.py`, `devices/public/{delta_pro,delta_max,delta2_max,delta_pro_3}.py`, `devices/internal/{delta_max,delta2_max}.py`
- rabits/ha-ef-ble @ `511e04702c83ddcc90cdcd575a1bbeffe2f061ad` (2026-10-03), release v1.2.0 (2026-10-01): https://github.com/rabits/ha-ef-ble (README; `custom_components/ef_ble/eflib/devices/delta_pro.py`)
- rabits/ef-ble-reverse (protocol notes): https://github.com/rabits/ef-ble-reverse
- vwt12eh8/hassio-ecoflow (archived; `PORT = 8055` in `custom_components/ecoflow/ecoflow/__init__.py`): https://github.com/vwt12eh8/hassio-ecoflow ; discussion #58 https://github.com/vwt12eh8/hassio-ecoflow/discussions/58 ; issue #69 https://github.com/vwt12eh8/hassio-ecoflow/issues/69
- openHAB EcoFlow binding (channels, parallel-use warning): https://www.openhab.org/addons/bindings/ecoflow/
- MichelFR/ha-ecoflow-iot (MQTT TLS 8883, official API): https://github.com/MichelFR/ha-ecoflow-iot
- apis.io EcoFlow Quota API (endpoint list): https://apis.io/apis/ecoflow/ecoflow-quota-api/
- nielsole/ecoflow-bt-reverse-engineering (MQTT redirect discussion): https://github.com/nielsole/ecoflow-bt-reverse-engineering/issues/2
- DELTA Max 1600 = 1612 Wh (retailer spec sheet): https://www.cliftoncameras.co.uk/uploads/specifications/EcoFlow%20DELTA%20Max%201600%20UK%20Specifications.pdf
- DELTA Pro = 3600 Wh (retailer listing): https://solartown.com/solar-products/ecoflow-delta-pro-portable-power-station-3600w-ac-output-3600wh-battery/
