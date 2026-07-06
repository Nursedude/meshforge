# meshing-around bot audit — data sources, output quality, API manifest (2026-07-06)

> First adversarial pass over the fleet bot's external-data surface
> (fork `Nursedude/meshing-around`, branch `meshforge`, base upstream
> `fde22f7` v1.9.9.8; upstream drift at audit time = 2 cosmetic
> `modules/space.py` commits, tag v1.9.9.9). Every finding below was
> verified by reading the code path (file:line) and, where marked, by
> live log evidence from the production bot box or live API reproduction.
> Companion session doc: memory `project_meshing_around_bot_audit_2026_07_06`.

## The defect class (same as MeshForge #80)

A degraded fetch maps to a valid-looking value. The bot's only exception
guard is the packet-level `except` in `onReceive` (`mesh_bot.py:2347`);
the command dispatch (`mesh_bot.py:183`) has **no per-handler guard**, and
`send_message` silently drops empty replies (`modules/system.py:858`). So
failures land in one of four buckets:

- **honest-error** — user gets `"error fetching data"` (context-free but honest)
- **no-reply-silence** — uncaught parse exception; user sees nothing
- **valid-looking-garbage** — failure reads as `"No alerts found."` / `"No DX spots found."`
- **mangled** — reply truncated to a single character (wxa/wxalert)

## Top findings (all code-verified; upstream-inherited unless noted)

1. **`wxa`/`wxalert` single-character replies** — `mesh_bot.py:484-485`
   applies `weatherAlert[0]` to plain-string returns: fetch failure → user
   receives literal **`e`**; `wxalert` with active alerts → **first letter
   of the alert** (e.g. `W` during a Winter Storm Warning). Emergency-adjacent
   command broken at the send boundary. **Filed upstream: SpudGunMan#324.**
2. **Safety commands convert fetch failure into "No alerts found."** —
   earthquake (`locationdata.py:869-881`), NOAA scales (`space.py:151`),
   NINA (`globalalert.py:46-48`), `mwx` with unset zone (`mesh_bot.py:1355`).
   A user checking hazards after an event cannot distinguish all-clear from
   internet-down.
3. **Silent no-reply paths** — tide `['predictions']` KeyError on CO-OPS
   200-with-error-JSON (`locationdata.py:230`); wx `['properties']['forecast']`
   KeyError (`locationdata.py:276`); `hfcond`/`solar` un-wrapped
   `requests.get` + `parseString` (`space.py:22-27,73-83`); solar
   `xray_flux` UnboundLocalError (`space.py:90`); `valert` `.json()` outside
   try (`locationdata.py:757`); alert-XML `parseString` outside try
   (`locationdata.py:428,532`); quake w/o magnitude IndexError
   (`locationdata.py:889`).
4. **Silent default-location substitution** — `get_node_location`
   (`system.py:643-681`) falls back to config lat/lon with no marker; wx,
   tide, sun, moon, satpass, earthquake, riverflow present operator-site
   data as the requester's. Privacy compounding: `whereami` redaction
   compares the *unrounded* config value against the *2dp-fuzzed* fallback
   (`locationdata.py:48` vs `system.py:651`) → never matches → no-GPS
   `whereami` returns a street-level address near the operator's site.
   **Live on our deployment.**
5. **Missing timeouts** — `wx_meteo.py:10` (wx + riverflow when
   `UseMeteoWxAPI=True`) and `dxspot.py:105` have NO timeout; a hung socket
   stalls the single packet-processing path indefinitely.
6. **Delivered-value bugs** — Open-Meteo precip *probability* labeled as
   rainfall amount (`wx_meteo.py:177`); wind direction from last day printed
   for all days (`wx_meteo.py:67-85`); NOAA metric request ignored → °F to
   metric users (`locationdata.py:260`); tide 12h rendering (12:xx→AM,
   `locationdata.py:238-243`); **moon waning phases unreachable**
   (`space.py:223-238` — illum ordering); satpass network error reported as
   user-input mistake (`space.py:309`); Hawaii/Alaska grids (BL/BP/AL
   missing) routed to RepeaterBook *international* endpoint
   (`locationdata.py:85`) — **live on our deployment**.
7. **Hygiene** — NO User-Agent on any api.weather.gov / CO-OPS / SWPC /
   USGS / FEMA call (NWS documents UA-less requests may be denied — a future
   403 becomes finding 2/3 behavior); NewsAPI `sortBy={x}shedAt` typo
   (`rss.py:158`); `http://` endpoints (artscipub scrape, hackaday RSS
   default); wiki UA placeholder email; bare `except:` in bbstools/smtp;
   NO cache/throttle on wx/tide/solar/quake/alerts (channel spam hits the
   APIs directly); error paths log no exception detail (the tide outage was
   undiagnosable from logs — cause dropped at `locationdata.py:219-228`).
8. **NINA endpoint dead** — `nina.api.proxy.bund.dev` NXDOMAIN globally;
   direct `warnung.bund.de/api31/` verified HTTP 200. Produced 737 warnings
   on our bot before 06-20 config-off. **Filed upstream: SpudGunMan#325.**

## Live evidence (bot box logs, June–July)

- `tide` transient outage 06-29/06-30: station lookup OK, datagetter leg
  failed 3×; user retried 3× and got `error fetching data` each time; logs
  carry NO cause (masked by finding 7). Reproduced both legs healthy from
  the box on 07-06 (200/200, ~1s each; timeout 15s). Tide succeeds routinely
  otherwise (successes on 15 distinct days).
- `error fetching data` transmitted over the air 5× total; also echoed back
  through the RNS bridge.
- 737 NINA warnings (through 06-19); 21 hamtest file-not-found; 4 USGS
  volcano-alert fetch warnings; 3,715 `Network is unreachable` interface
  retries (box-side, not API-side).
- **hamtest root cause**: `install.sh:214` copies `etc/data/* → data/` at
  install time only; the box's install predated the hamtest data and updates
  never re-sync. FIXED on the box 2026-07-06 (`cp -rn etc/data/* data/`,
  no-clobber; 3 JSONs validate). Upstream gap: update path should re-sync,
  or `hamtest.py:24` should fall back to `etc/data/`.

## External endpoint manifest (API version-control baseline)

Track drift against this list; a change here = a bot-visible risk.

| Endpoint | API / product | Risk notes |
|---|---|---|
| `api.weather.gov/points/{lat},{lon}` → `…/forecast` | NWS API | no UA; JSON keys unguarded |
| `api.weather.gov/alerts/active.atom?point=` | NWS alerts ATOM | no UA; feeds wxa/wxalert mangle |
| `api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/tidepredstations.json` | CO-OPS MDAPI | |
| `api.tidesandcurrents.noaa.gov/api/prod/datagetter` | CO-OPS **legacy** datagetter | watch for deprecation |
| `www.hamqsl.com/solarxml.php` | N0NBH XML (hobbyist SLA) | parse unguarded 2 of 3 uses |
| `services.swpc.noaa.gov/text/drap_global_frequencies.txt` | SWPC DRAP | line-format scrape |
| `services.swpc.noaa.gov/products/noaa-scales.json` | SWPC scales | failure→NO_ALERTS |
| `api.n2yo.com/rest/v1/satellite/visualpasses/` | n2yo v1 (key in URL) | |
| `earthquake.usgs.gov/fdsnws/event/1/query` | USGS FDSN v1 XML | failure→NO_ALERTS |
| `volcanoes.usgs.gov/hans-public/api/volcano/getCapElevated` | USGS HANS | `.json()` outside try |
| `api.water.noaa.gov/nwps/v1/gauges/{uid}` | NOAA NWPS v1 | |
| `api.open-meteo.com/v1/forecast`, `flood-api.open-meteo.com/v1/flood` | Open-Meteo v1 | **NO timeout** |
| `apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/feed` | FEMA IPAWS CAP 1.2 | per-link failures drop alerts |
| `www.repeaterbook.com/repeaters/prox_result.php` (+row) | HTML scrape | markup-fragile; HI/AK misroute |
| `www.artscipub.com/mobile/showstate.asp` | HTML scrape, cleartext | |
| `en.wikipedia.org/api/rest_v1/page/summary/` | Wikimedia REST v1 | 5s hardcoded timeout |
| `opensky-network.org/api/states/all` | OpenSky (anon) | heavy anon rate limits |
| `newsapi.org/v2/everything` | NewsAPI v2 | sortBy typo; key in query |
| `spothole.app/api/v1/spots` | Spothole v1 | **NO timeout**; failure→"No DX spots" |
| Nominatim via geopy | reverse geocode | OSM policy 1 req/s |
| `nina.api.proxy.bund.dev/api31/` | **DEAD (NXDOMAIN)** | #325; use `warnung.bund.de/api31/` |
| gov.uk / metoffice / data.police.uk | unwired/dead code | crime fns would NameError |

## Fix roadmap

**Fork-first (no upstream dependency), in order:**
1. Wrap dispatch at `mesh_bot.py:183` — honest "command failed" instead of silence.
2. `isinstance(tuple)` guard at `mesh_bot.py:484` (#324 fix).
3. Identifying User-Agent on all NOAA-family calls + timeouts on wx_meteo/dxspot.
4. Error paths: log `type(e).__name__` + status code (makes the next tide
   outage diagnosable); failure ≠ `NO_ALERTS` on safety commands.
5. Small TTL cache in front of NOAA-family calls (channel-spam shield).

**Upstream filed:** #324 (wxalert mangle), #325 (NINA dead endpoint).
**Upstream queue (file as PRs mature):** failure→NO_ALERTS class, missing
timeouts, precip-probability mislabel, moon waning phases, NewsAPI typo,
HI/AK repeater misroute, whereami fuzz-vs-redaction privacy mismatch.

**Deploy state:** the bot box fork switch (deferred task
`meshing-around-fork-deploy` step 2) remains PENDING — pre-steps done
2026-07-06 (hamtest data restored; config backup taken), the repoint +
restart is operator-gated. The fork stays at v1.9.9.8 base until the switch
lands (preserves the proven zero-code-delta repoint); merge v1.9.9.9 after.
