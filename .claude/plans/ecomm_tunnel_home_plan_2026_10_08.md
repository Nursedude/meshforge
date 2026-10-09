# ECOMM kit → home tunnel — plan (2026-10-08; option A CHOSEN by the operator)

**Goal (operator 09-15):** "reachable over starlink — want to see it from home."
The field kit is **Starlink Mini + alaula (OpenWrt One) + MacBook**, kiai
optional. alaula is the always-carried core, so the tunnel lives on alaula.

## What was measured (2026-10-08, read-only)

| Fact | Value | So |
|---|---|---|
| Home egress IPv4 | `74.244.37.132` | public egress exists… |
| m1 WAN | `192.168.1.219` behind the Starlink router `192.168.1.1` | …but home is double-NAT behind Starlink; Starlink residential IPv4 = CGNAT (ASSERTED — plan-dependent, not queried) → **no inbound at home** |
| Home global IPv6 | none (only ULA `fda4:…` on VolcanoAI) | m1 does not hand out Starlink IPv6 today |
| alaula | OpenWrt 24.10.7 (mediatek/filogic); **no wireguard / tailscale installed**; only ULA `fdca:…/60` | packages needed |
| alaula radios | radio1 5 GHz = STA to `Meshforge 5` (home uplink); radio0 2.4 GHz = AP `Nahele`, **disabled** | field uplink = STA to the Mini's SSID on radio1 |

**Consequence:** both ends sit behind NAT that refuses inbound. A tunnel needs
a **rendezvous both sides dial OUT to** — or IPv6 end to end (unmeasured).

## Options

| | How | For | Against |
|---|---|---|---|
| **A. WireGuard hub on a tiny VPS** (recommended) | $4–6/mo public VM runs WireGuard; alaula and the manager box (VolcanoAI) are peers with `PersistentKeepalive = 25`; the VPS only forwards between peers | ours end to end; no third-party control plane; WireGuard is in the OpenWrt kernel (`kmod-wireguard`, `luci-proto-wireguard`); tiny | one more internet-facing box to patch and watch (it joins the fleet registry; UDP 51820 only, SSH key-only) |
| B. Tailscale | `tailscale` package on alaula + manager box; DERP relays traverse double CGNAT | fastest to stand up; no VPS | a company's control plane holds the keys to the field kit (the 05-29 upstream-governance question); ~30 MB binary on alaula's NAND |
| C. IPv6 direct | enable Starlink IPv6 PD through m1 and at the Mini; WireGuard over global IPv6 | no rendezvous at all | UNMEASURED: whether the Mini delegates IPv6 to a downstream router and whether Starlink admits unsolicited inbound IPv6 — probe before relying on it |
| D. RNS over the internet | kiai's rnsd to a public RNS transport / I2P interface | domain-native, carries LXMF (the END) | only when kiai is carried; not a management path (no SSH/LuCI) |

**Recommendation:** **A** as the management path, **D** kept as the message
path when kiai travels. Run the cheap **C probe** first — if both ends get
global IPv6 with inbound, A's VPS becomes a fallback, not a dependency.

## Design (option A)

- Tunnel subnet `10.77.0.0/24`: VPS `.1`, VolcanoAI `.2`, alaula `.3`, kiai `.4`
  (kiai only if it travels; it routes via alaula anyway).
- alaula: `wg0` interface, peer = VPS, `AllowedIPs = 10.77.0.0/24`,
  keepalive 25. Firewall zone `vpn`: accept SSH + LuCI **from 10.77.0.2 only**;
  forward vpn → lan so home can reach kiai/MacBook at `192.168.77.x`; masq off.
- VPS: `AllowedIPs` per peer (`/32`), IP forwarding on, firewall drops
  everything except UDP 51820 + SSH (key-only, from the tunnel).
- VolcanoAI: wg peer; route `192.168.77.0/24` via `10.77.0.3`.
- **Fleet wiring:** registry entries for `alaula-wg` (`10.77.0.3`) and the VPS;
  `fleet_offline_check` `via:` for kiai = alaula (the 08-10 lesson); the kit's
  `detached` posture then reads REJOINED the moment the tunnel comes up.
- **Never** cable alaula's LAN into the house L2 (10-06 rule) — unchanged.

## Field Wi-Fi (alaula)

- radio1 (5 GHz) = STA uplink. Add a second STA profile for the **Mini's SSID**
  beside `Meshforge 5`; install **travelmate** so alaula picks whichever uplink
  is present (home m1 vs field Mini) — no hand-editing in the field.
- radio0 (2.4 GHz) = AP `Nahele` for the MacBook (key set; radio OFF until
  deployed). Uplink and AP on different radios = no halved throughput.
- Mini: power-save/sleep schedule OFF (Starlink app).

## Steps (each gated; nothing runs without the operator's go)

1. **C probe** (read-only): Mini IPv6 delegation + inbound test from home.
2. Pick A or B (operator decision; A recommended).
3. Provision VPS (operator: provider + payment), harden, add to registry.
4. alaula: install `kmod-wireguard luci-proto-wireguard wireguard-tools travelmate`; back up `/etc/config/*` first (`.bak-tunnel-<date>`).
5. Bring up wg at HOME first (alaula still on m1) — prove VolcanoAI → `10.77.0.3` SSH.
6. Field drill: alaula on the Mini, MacBook on `Nahele`; from home: ssh alaula over the tunnel, ping a MacBook address, read kiai if carried. Acceptance = from home, a command lands on the kit **while it is on Starlink Mini**, not while it is on m1.
7. Touch-log + persistent_issues tell row; `detached` REJOINED observed.

## Decisions (operator, 2026-10-08)

- **Option A** (WireGuard hub on a VPS). Region US-West (LA/Seattle); provider + payment = operator.
- **Direction: home → kit only.** The MacBook does not need the lab when remote, so the
  `vpn` zone accepts SSH/LuCI **from 10.77.0.2 only** and there is **no** kit → lab forward.
- SSIDs: `Nahele` = alaula's OWN AP (MacBook joins it). The **Mini's SSID** (set up weeks ago,
  operator to retrieve) = alaula's field UPLINK, added beside `Meshforge 5`; travelmate picks.

## Open questions for the operator

- VPS provider chosen + created (operator), its public IPv4 handed over.
- The Mini's SSID/passphrase (for travelmate) — enter in LuCI yourself or hand them over.
