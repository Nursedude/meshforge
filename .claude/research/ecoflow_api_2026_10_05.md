# EcoFlow telemetry for a Pi shutdown trigger: research, 2026-10-05

> Read-only web and source research. No device contacted, nothing installed, no account created.
> Tags: **VERIFIED-src** means read in the cited primary source or repo at the cited commit this session.
> **ASSERTED** means my reasoning, not measured. **UNKNOWN** means no source says.
>
> **Revision 2 (same day):** the owner gave the real models, **EcoFlow DELTA 3** (1024 Wh LFP, 1800 W)
> and **EcoFlow RIVER 2 Pro** (LFP, 800 W, 1600 W peak). Revision 1 assumed DELTA Max / DELTA Pro, and
> every model-specific conclusion has been re-derived. The DELTA Pro / Max material is kept only where it
> still informs the answer, marked **[not our models]**.

## The models (read this first)

- **Mapping, owner-stated and unconfirmed:** tent "1600" = RIVER 2 Pro; yurt "3600" = DELTA 3, which
  powers Starlink and a switch. ASSERTED: the labels are probably surge or X-Boost figures. 1600 W is the
  owner-quoted RIVER 2 Pro peak. Whether DELTA 3 surge is 3600 W is UNKNOWN; I did not verify it.
- **"DELTA 3" is a family, and the serial-number (SN) prefix decides which code path applies**
  (VERIFIED-src, `rabits/ha-ef-ble` `eflib/device_mappings.py` @ `511e0470`):

  | SN prefix | Name in that file | Protocol generation |
  |---|---|---|
  | `P231` | DELTA 3 | v3 (protobuf) |
  | `P32x` | DELTA 3 Classic | v3 |
  | `P351` | DELTA 3 Plus | v3 |
  | `D361` / `D365` | DELTA 3 (1500) | v2 |
  | `R621` / `R623` | RIVER 2 Pro | v2 |

  Search-result retailer comparisons describe DELTA 3 (Classic) and DELTA 3 Plus as both 1024 Wh / 1800 W.
  The "1500" variant is a different capacity class, so the owner's 1024 Wh points to `P231`, `P32x` or
  `P351` (ASSERTED). **Action for the owner:** read only the first 4 characters of each SN in the app's
  device info. That settles every row below. Do not put full SNs in the repo.

## Verdict

1. **The yurt DELTA 3 has no official cloud-API support in the best-maintained client.** In tolwi's
   public-API registry the line `# "DELTA 3": public_delta3.Delta3` is commented out, so tolwi reads
   DELTA 3 only through the **private** app API (VERIFIED-src, `devices/registry.py` @ `38986e6e`).
   Whether EcoFlow's official platform serves DELTA 3 quota at all is UNKNOWN: the official doc is a
   JS-only page I could not read.
2. **The RIVER 2 Pro is supported by the official public API** in tolwi:
   `"RIVER 2 Pro": public_river2_pro.River2Pro` (VERIFIED-src, same file).
3. **Both are supported over BLE** by `rabits/ha-ef-ble` (active: v1.2.0 2026-10-01, HEAD `511e0470`
   2026-10-03). BLE is the only fully local path. The device allows **one BLE connection at a time**.
4. **There is no local LAN path for either model.** No source shows one. The only historical one (TCP
   :8055) was Gen-1 DELTA / RIVER **[not our models]** and is dead.
5. **The cloud cannot be the trigger for the yurt anyway.** The DELTA 3 powers Starlink, so in an outage
   the cloud goes UNKNOWN exactly when it is needed (ASSERTED).
6. **DELTA 3 carries an explicit AC-plugged flag** (`plug_in_info_ac_charger_flag`), which is a direct
   "grid present" bit and better than inferring from watts (VERIFIED-src: the field exists in ha-ef-ble
   and in tolwi's protobuf path).

## 1. Official developer platform (IoT Open Platform)

- **It exists.** Portals: `https://developer.ecoflow.com` (US/global) and
  `https://developer-eu.ecoflow.com` (EU). Both are JS single-page apps, and fetch and curl returned only
  the shell (the bundle yielded no doc text), so **the official text is UNKNOWN verbatim**. Everything
  below comes from client code.
- **Auth** (VERIFIED-src, `tolwi/hassio-ecoflow-cloud` `api/public_api.py` @ `38986e6e`):
  - Every request carries the headers `accessKey`, `nonce`, `timestamp` and `sign`.
  - `sign` is the hex HMAC-SHA256, keyed by the secretKey, over this string:
    `<sorted k=v params>&accessKey=..&nonce=..&timestamp=..`. The query parameters are sorted; the three
    auth fields are appended after them, not sorted in.
  - Keys come from the developer console after an approval step.
- **Endpoints** (VERIFIED-src):
  - `GET https://{api.ecoflow.com|api-e.ecoflow.com}/iot-open/sign/device/quota/all?sn=<SN>` returns a
    full state snapshot.
  - `GET /iot-open/sign/certification` returns MQTT credentials.
  - MQTT topics: `/open/<certAccount>/<SN>/quota` and `/open/<certAccount>/<SN>/status`, over TLS on
    port 8883 (per the `MichelFR/ha-ecoflow-iot` README).
  - A write endpoint (`PUT /iot-open/sign/device/quota`) exists. **Never use it for this purpose.**
- **Per-model support:**
  - **RIVER 2 Pro: supported** in tolwi's public registry (VERIFIED-src).
  - **DELTA 3: not enabled** in tolwi's public registry; `public/delta3.py` exists but its registry entry
    is commented out (VERIFIED-src). The file history shows "Preliminary Delta 3 Support (#537)"
    (`e769da91`, 2025-07-28) and a refactor (`eadd9773`, 2025-12-22). In `devices/registry.py` the
    private table maps `DELTA_3`, `DELTA_3_1500` and `DELTA_3_MAX_PLUS`, and the public table keeps only
    "DELTA 3 Max Plus".
  - A second-tier source, `windsurf/hassio-ecoflow-eu` (2 stars, last commit `b73e9164` 2026-04-19),
    says Developer-API REST SET is "blocked" for D361/D362/D381/R641/R651 (code 1006). It recommends app
    login for Delta 3, and tested only the D361 "1500". Read access through the developer API for DELTA 3
    is UNKNOWN from this.
- **Field names, RIVER 2 Pro** (VERIFIED-src, tolwi `devices/internal/river2_pro.py` @ `38986e6e`; the
  public class flattens these with `to_plain`, so confirm the exact keys with one `quota/all` call):

  | Need | Key |
  |---|---|
  | SoC % | `bms_emsStatus.lcdShowSoc` (combined), `bms_bmsStatus.soc` |
  | AC-in watts | `inv.inputWatts` |
  | AC-in voltage (grid present, derived) | `inv.acInVol` (mV) |
  | Total in / out W | `pd.wattsInSum` / `pd.wattsOutSum` |
  | AC out W | `inv.outputWatts` |
  | Remaining time | `bms_emsStatus.dsgRemainTime`, `pd.remainTime` (min) |

- **Field names, DELTA 3** (private API and protobuf, VERIFIED-src: tolwi `devices/internal/delta3.py`
  @ `38986e6e`): SoC `cms_batt_soc` / `bms_batt_soc`; AC-in W `pow_get_ac_in`; AC-in V
  `plug_in_info_ac_in_vol`; total in / out `pow_in_sum_w` / `pow_out_sum_w`; AC out `pow_get_ac_out`;
  remaining `bms_dsg_rem_time` / `cms_chg_rem_time`. These are names from the private app channel, not
  the open platform.
- **Rate limits: UNKNOWN.** No primary source states them. openHAB warns that using one developer
  account "multiple times in parallel … will disturb event updates".
- **Needs internet: yes**, for every cloud path, official or private (VERIFIED-src: the hosts are
  EcoFlow cloud names).
- **Private app-API path** (tolwi "private" mode, windsurf "App Login"): it uses the owner's EcoFlow
  account email and password against undocumented endpoints. It is also cloud, and it puts account
  credentials on a Pi. Not recommended (ASSERTED).

## 2. Local options

| Project | Path | Our models? | Status | Fields for the trigger |
|---|---|---|---|---|
| `rabits/ha-ef-ble` | **BLE**, reverse-engineered, encrypted | **DELTA 3** (`P231`; also Classic `P32x`, Plus `P351`) **and RIVER 2 Pro** (`R621`/`R623`) | **Active**: v1.2.0 2026-10-01, HEAD `511e0470` 2026-10-03, 399 stars | **DELTA 3** (`eflib/devices/_delta3_base.py`): `battery_level`←`cms_batt_soc`, `ac_input_power`←`pow_get_ac_in`, `ac_output_power`←`pow_get_ac_out`, `input_power`/`output_power`←`pow_in_sum_w`/`pow_out_sum_w`, **`plugged_in_ac`←`plug_in_info_ac_charger_flag`**, `remaining_time_discharging`←`cms_dsg_rem_time`. **RIVER 2 Pro** (`river2.py`, subclassed by `river2_pro.py`): `battery_level`←`f32_show_soc`, `input_power`/`output_power`←`watts_in_sum`/`watts_out_sum`, `ac_input_power`←`inv.input_watts`, `ac_output_power`←`inv.output_watts`, `remaining_time_discharging`←`ems.dsg_remain_time`. No AC-plugged flag is listed for RIVER 2. |
| `tolwi/hassio-ecoflow-cloud` | Cloud (public or private API) | RIVER 2 Pro public; DELTA 3 private only | Active: v1.7.1 2026-08-18, HEAD `38986e6e` 2026-10-02 | See §1 |
| `vwt12eh8/hassio-ecoflow` **[not our models]** | Local TCP :8055 | Gen-1 only (RIVER Max/Pro, DELTA Mini/Max, DELTA Pro Wi-Fi) | Archived, last push 2023-05-04 | Kept as evidence that EcoFlow closed the one local port by firmware (disc. #58, 2022-09-22) |

- **BLE prerequisites** (VERIFIED-src, ha-ef-ble README): the device must be bound to the account; the
  EcoFlow **User ID** is needed (fetched once by a login during setup); only one BLE connection at a time;
  "Firmware updates may break this integration".
- **BLE outside Home Assistant** (BELIEVED, not verified across all files): the `eflib/` package is
  `bleak`-based, and the files read import no Home Assistant code.
- ⚠️ **A write-time hazard in the RIVER 2 code** (VERIFIED-src, `river2.py` line 58):
  `ac_input_power = raw_field(pb_inv.input_watts).default_when_missing(0)`. A **missing** reading
  becomes **0 W**, which looks exactly like "grid lost" (honest_failure_modes #1). A trigger built on it
  must check field freshness or presence itself, or it can shut the fleet down on a dropped packet.
  The DELTA 3 `pb_field` definitions carry no such default in the lines read.
- **No local LAN API or local MQTT** was found for DELTA 3 or RIVER 2 Pro (UNKNOWN that one exists).
  Search summaries agree that the Wi-Fi path is cloud-mediated.

## 3. Failure modes for an outage trigger

- **WAN down means the cloud is blind** (ASSERTED). The DELTA 3 powers Starlink. A stale cloud reading
  must read as UNKNOWN, never "on grid" or "on battery". The Pi clocks may also go stale (see the
  persistent_issues WAN row).
- **A policy that needs the cloud never fires** (ASSERTED). Base the shutdown timer on local evidence:
  BLE, or a grid probe that does not depend on EcoFlow.
- **BLE at about 10 m** (ASSERTED, physics): routine in line of sight. The yurt wall plus the unit's case
  make it marginal, so measure RSSI from the chosen Pi spot. Pi onboard Bluetooth shares an antenna with
  2.4 GHz Wi-Fi; a USB BT adapter is the usual fix.
- **One BLE client** (VERIFIED-src): while the Pi holds the link, the owner's phone app cannot use BLE.
  The app's Wi-Fi/cloud view still works while the WAN is up (ASSERTED).
- **Firmware drift** (VERIFIED-src warning): an OTA update can break BLE.
- **Missing read as zero** (VERIFIED-src, RIVER 2 code above): treat "no fresh packet" as UNKNOWN.

## 4. Recommendation (a recommendation, not a measurement)

**Prototype BLE first, against the yurt DELTA 3, once its SN prefix is confirmed as `P231`, `P32x` or
`P351`.** Use ha-ef-ble's `eflib` from a standalone, read-only Python script (no setter calls). Log
`plugged_in_ac`, `battery_level`, `ac_input_power`, `output_power` and `remaining_time_discharging` with
a per-field freshness timestamp, and measure RSSI and reconnect behaviour for 24 h. The **trigger signal
is `plugged_in_ac` going false and staying false for N seconds of fresh packets**; SoC and runtime set
the delay. **Then the tent RIVER 2 Pro over BLE**, from a Pi near it. That model has no plugged flag in
eflib, so trigger on fresh `ac_input_power == 0` with the missing-reads-as-zero guard. The official
public API is a cloud fallback for the tent unit only. **In parallel:** a grid probe that does not use
EcoFlow at all (ping a mains-powered device upstream of each station) as the independent "AC lost"
witness; requiring two witnesses guards against a BLE misread.

- VERIFIED-src: the SN-to-model mapping; BLE support and field names for both models; RIVER 2 Pro in the
  public registry; DELTA 3 public entry commented out; the RIVER 2 missing-as-zero default; signing and
  endpoints; :8055 being dead and Gen-1 only.
- ASSERTED: the label-to-unit mapping, cloud blindness during WAN loss, BLE range through the walls,
  `eflib` usable without HA.
- UNKNOWN: whether the official platform serves DELTA 3 quota; rate limits; the exact SN prefixes of the
  owner's units; DELTA 3 surge rating.

## Sources

- EcoFlow developer portal (JS SPA, content not fetchable): https://developer.ecoflow.com/us/document/introduction , https://developer-eu.ecoflow.com/us/document/generalInfo
- tolwi/hassio-ecoflow-cloud @ `38986e6e2534037d039cb4e6e3eb2165a8e40c1d` (2026-10-02), release v1.7.1 (2026-08-18): https://github.com/tolwi/hassio-ecoflow-cloud
  - `custom_components/ecoflow_cloud/devices/registry.py` (public table: RIVER 2 Pro enabled, DELTA 3 commented)
  - `devices/internal/{delta3,river2_pro}.py`, `devices/public/{delta3,river2_pro,data_bridge}.py`, `api/public_api.py`
  - DELTA 3 history: commits `e769da91` (2025-07-28), `eadd9773` (2025-12-22)
- rabits/ha-ef-ble @ `511e04702c83ddcc90cdcd575a1bbeffe2f061ad` (2026-10-03), release v1.2.0 (2026-10-01): https://github.com/rabits/ha-ef-ble
  - README (Delta 3 family and River 2 (Pro, Max) sensor tables; BLE single-connection limit)
  - `custom_components/ef_ble/eflib/device_mappings.py`, `eflib/devices/{_delta3_base,delta3,delta3_plus,delta3_classic,river2,river2_pro}.py`
- windsurf/hassio-ecoflow-eu @ `b73e91648672a992210b9e9ee859d0d3f9d6d50e` (2026-04-19), low-trust (2 stars): https://github.com/windsurf/hassio-ecoflow-eu
- MichelFR/ha-ecoflow-iot (MQTT TLS 8883): https://github.com/MichelFR/ha-ecoflow-iot
- openHAB EcoFlow binding (parallel-use warning): https://www.openhab.org/addons/bindings/ecoflow/
- apis.io EcoFlow Quota API (endpoint list): https://apis.io/apis/ecoflow/ecoflow-quota-api/
- [not our models] vwt12eh8/hassio-ecoflow (archived; `PORT = 8055`): https://github.com/vwt12eh8/hassio-ecoflow ; https://github.com/vwt12eh8/hassio-ecoflow/discussions/58 ; https://github.com/vwt12eh8/hassio-ecoflow/issues/69
- DELTA 3 vs DELTA 3 Plus (1024 Wh / 1800 W both; retailer comparison, secondary): https://e-catalog.com/cmp/156126/delta-3-plus-vs-delta-3/
