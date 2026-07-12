# templates/openwrt — router-resident MeshForge

Artifacts for router-class fleet members (OpenWrt / AREDN / MikroTik-hosted
OpenWrt). The reference deployment is an OpenWrt One (OpenWrt 24.10, 1 GB,
musl) running patched meshtasticd with a USB LoRa radio, reachable only via
a reverse SSH tunnel. Everything here is a TEMPLATE: operator values (hosts,
IPs, tunnels, device ids) stay in on-box config or untracked wrappers
(MF014) — never in this repo.

## meshforge-scout — router telemetry agent (v1)

Busybox-ash agent collecting meshtasticd + host health into one JSON tick:

| field | what / why |
|---|---|
| `service.running/pid` | procd view via `ubus call service list` (pidof fallback, witnessed) |
| `meshtasticd.vsz_kb/rss_kb/maps/age_s` | `/proc/<pid>` — `maps` is the #10468 leak metric; FATAL on 32-bit's ~65k mmap cap |
| `radio_tcp` | TCP connect-only probe of the PhoneAPI port (never reads it — #17) |
| `host.*` | uptime / load / MemAvailable |
| `persistence` | is the meshtasticd `data_dir` on tmpfs? (the config-vanishes-on-reboot gotcha) |
| `opkg_hold` | is the package pinned against a stock-feed clobber? (apk boxes: unobservable note) |

Every field is tri-state (value, or `null` + a witness in `errors`/`notes`);
`ok=false` whenever `errors` is non-empty. `check` adds a local threshold
verdict + soak-format log line, and performs ONE service restart past
`MAPS_RESTART` (this absorbs the mapwatch cron's action — retire mapwatch
only after a ≥48 h parity soak against it).

### Install (on the router, as root)

```sh
# from the manager box: scripts/router_scout_enroll.sh does all of this.
mkdir -p /etc/meshforge
cp meshforge-scout /usr/bin/meshforge-scout && chmod 755 /usr/bin/meshforge-scout
cp scout.conf.example /etc/meshforge/scout.conf   # then edit
grep -q '^/etc/meshforge/' /etc/sysupgrade.conf || echo '/etc/meshforge/' >> /etc/sysupgrade.conf
# crontab (crontab -e / /etc/crontabs/root):
#   */15 * * * * /usr/bin/meshforge-scout check
/usr/bin/meshforge-scout collect && /usr/bin/meshforge-scout show
```

Tick + log + conf live under `/etc/meshforge/` — on the overlay (persists
reboot) and in `sysupgrade.conf` (persists flashes). Write volume is ~1 KB
× 96/day: negligible overlay wear.

### Standalone vs fleet

- **Standalone**: the cron line above is the whole system — local tick,
  local `scout.log`, local threshold restart. Useful with zero fleet.
- **Fleet**: the manager box pulls the tick over the ssh channel it already
  owns (`scripts/router_scout_pull.sh`, wired to `cron_verdict.sh
  router_scout`), lands it for kilo (`kilo collect` scout leg → node in
  `kilo status`) and for the `router_scout_degraded` watchdog probe
  (fires degraded when the agent goes dark while the mirror stays fresh,
  when a tick reports `ok=false`, or on an unparseable mirrored tick;
  INERT on boxes with no mirrored ticks). The pull-cron's own death is
  `cron_verdict_stale`'s beat (#78).

### 32-bit / MikroTik notes

The #10468 pthread-stack leak strands one 8 MB-stack mapping per radio
interrupt. On 64-bit it's a slow VSZ balloon; on 32-bit the process dies at
the ~65,530 mapping kernel cap in ~2-3 days. `MAPS_RESTART` must sit WELL
below that cap; the fleet default 55000 is a backstop — a patched build
(pine64 libpinedio-usb fix) holds ~110 maps flat and should never approach
it. If a patched box trips this, the leak is BACK: treat it as a new
incident, not noise.

### AREDN notes

AREDN nodes are OpenWrt underneath; scout runs unmodified where resources
allow. Heed the raven lesson (`scripts/raven_soak_watch.sh` header): a
56 MB hAP could not host a Meshtastic bridge — scout itself is a few KB of
ash + one cron, safe even there, but the SERVICE it watches may not be.

### v2 sketch (deliberately deferred)

collectd/MQTT publisher emitting the same tick JSON (the tick file is the
interface; downstream consumers don't change). Triggers: a second router
enrolls, or sub-15-min resolution is needed. Blockers noted at design time:
collectd-mod-exec runs as `nobody` (can't read root meshtasticd's
`/proc/<pid>/maps`), collectd-mod-mqtt TLS is broken upstream
(openwrt/packages#8288) — plain MQTT inside the tunnel/LAN only.

## Fleet naming templates (Arc 2 — landing later)

`dnsmasq-fleet.conf.template`, `uci-dnsmasq-fleet.sh.template`,
`mikrotik-fleet-dns.rsc.template` will live here; generated from the
operator's `~/.config/meshforge/fleet_naming.json` registry by
`scripts/fleet_naming_audit.py --emit-*`, never committed with real values.
