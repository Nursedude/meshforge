# MeshForge Reference Node — Phase 0 Build Plan

> **Decision LOCKED 2026-07-17** (operator): **Concept A** (NOC-in-a-box reference
> host) · **brain-node tier** (Tier 1 rule loop + Tier 2 Ollama 4B) · **Pi 5
> prototype** (off-the-shelf, no PCB). Rationale + concept comparison in the
> companion `meshforge-reference-node-design-seed.md`.
>
> **Status**: adopted build plan. **Hardware-gated** — nothing here runs until a
> Pi 5 + parts are on the bench at moc1. No code committed. Calibration: BELIEVED
> (design + grounded in real installer/profile scripts); nothing VERIFIED on
> hardware that doesn't exist yet.

---

## Bill of materials (recommendations + why)

| Part | Recommendation | Why |
|---|---|---|
| **Compute** | Raspberry Pi 5, **16 GB** | Brain node runs Ollama `qwen3:4b` q4 (~2.5–3 GB resident) *concurrently* with the NOC stack (gateway + map collector + mini-dudeai). Proven substrate was Pi 5 / 15 Gi. 8 GB is the floor and risks contention under a map-collector spike + inference; 16 GB buys headroom. |
| **Cooling** | Active cooler (official Pi 5 Active Cooler or fan case) | **Non-negotiable for a brain node.** Sustained 4B inference is the thermal load; the "65 °C no-throttle" figure assumes active cooling. |
| **Power** | Official 27 W USB-C PD supply | Pi 5 + HAT + NVMe + sustained inference draws hard. Underpowered supply → brownout resets (cf. VolcanoAI external-mains reset class — power integrity is a real failure mode on this fleet). |
| **Storage** | NVMe SSD via Pi 5 M.2 HAT; **boot from NVMe** | Ollama model files are GB-scale; SD-card wear + load latency hurt a 24/7 node. Bounded JSONL history is tiny, but the model store isn't. NVMe survives the write cycles. |
| **LoRa radio** | **SPI LoRa HAT — MeshAdv HAT (LF)**, matching moc (`!32962f10`) | **Constraint #1**: SPI, not USB/CH341 → immune to the firmware#10468 leak by construction. MeshAdv is the fleet's known-good SPI reference radio. |
| **RTC + GPS** | **GPS+RTC mini HAT** (the `.248` board — model TBC) — supersedes the plain-RTC plan | **Upgraded constraint-#2 answer for a REMOTE node.** An RTC alone only *survives* power cycles; a GPS with **PPS** is an authoritative off-grid time source (effectively stratum-0, **no NTP needed**) — which is exactly what a node 30 mi out with no internet backhaul requires. RTC holds time across cold boot / GPS-denied gaps; GPS+PPS re-disciplines it on every fix. Together they don't just survive the clock-forgery class (honest_failure_modes #6) — they **remove the NTP dependency that caused it.** *Bonus capability below.* Pi 5 onboard RTC + cell remains the fallback if the HAT is absent. |

### GPS bonus — a domain capability, not just timekeeping
The GPS half earns its place beyond constraint #2. MeshForge **owns** the RF
tools (link budget, Fresnel, FSPL — `utils/rf.py`) and coverage maps, all of
which need accurate node positions. A GPS-disciplined node is **self-locating**:
it feeds its own coordinates into the RF/coverage math, the node tracker / map
(no manual position entry), and its Meshtastic position beacons. For a
*drop-at-a-site* remote deployment (the 30-mi arc), self-location is a real
operational win — deploy it and it reports where it is. Add a GPS-antenna
sky-view line to the 30-mi site scorecard.

### Physical-integration note — this is the CM5 motivator (now sharper)
Pi 5 has **one 40-pin header** and **one PCIe lane**. Contending for them:
the **SPI LoRa HAT** (SPI + GPIO), the **GPS+RTC mini HAT** (GPS UART/I²C + a
**PPS GPIO** + RTC I²C), the **M.2 HAT** (PCIe + standoffs), and the active
cooler on top. ⚠️ **Verify pin conflicts** between the MeshAdv SPI HAT and the
GPS/RTC HAT before stacking (shared GPIO / the PPS pin). This is *more* header
contention than the original plan — which **sharpens the CM5 carrier-board
argument**: the Phase 1 board should integrate RTC + GPS (with PPS routing) +
SPI LoRa + M.2 + clean power on one PCB, designing the whole stack away.
**Phase 0 accepts the stack (and proves the pinout); Phase 1 designs it away.**

---

## Provisioning sequence (against real entry points)

1. **OS**: flash Pi OS 64-bit (Bookworm) to NVMe; boot from NVMe.
2. **Base NOC**: `git clone` MeshForge → `scripts/install_noc.sh` (the
   pip-hardened install path from the 2026-06-23 installer arc — never bare pip).
3. **Deployment profile**: `python3 src/launcher.py --profile full`
   → writes `deployment.json`. `full` = meshtasticd + rnsd + mosquitto (the
   brain-node gateway substrate; `src/utils/deployment_profiles.py`).
4. **SPI HAT + constraint #3**: install the MeshAdv overlay, **pin meshtasticd to
   :9443**, apply `_sanitize_hat_overlay` so no `Webserver: Port: 443` smuggles
   in (Issue #58).
5. **rnsd + constraint #4**: image the fork RNS substrate; gate every
   RNS-dependent unit on `rnstatus` host-wait (the `10-wait-for-rnsd.conf`
   pattern from Issue #82) — instance-name-agnostic, fail-closed.
6. **Role**: add a `reference-node` (or reuse `full-gateway`) entry to
   `docs/fleet_roles.yaml`, then `scripts/provision_role.py --set-role
   reference-node` + `--apply`. Declares which units run.
7. **Tier 1 brain**: mini-dudeai systemd daemon (already templated in
   `templates/systemd/`) + nightly dream timer. Dependency-free stdlib Python.
8. **Tier 2 brain**: install Ollama, `ollama pull
   qwen3:4b-instruct-2507-q4_K_M`, apply the **`CPUWeight=20` drop-in** so
   inference yields to production under contention (2026-07-15 CPU-citizenship
   lesson — the hog is `llama-server`, `nice` was theater).
9. **Gate**: `scripts/verify_post_install.sh` (Issue #23).

---

## The four-constraint verification checklist (the point of Phase 0)

Phase 0 exists to **prove the design neutralizes four incident classes on real
hardware** — not to assume it. Each gets a pass/fail:

1. **CH341 leak (constraint #1)** — `wc -l /proc/$(pgrep -x meshtasticd)/maps`
   flat over 30 min. **PASS** = ~8 stack pairs stable, not climbing. (Trivially
   true on SPI — but we *verify*, because the whole thesis is "designed out, not
   watched for.")
2. **RTC + GPS time (constraint #2)** — (a) set a known time, power-cycle with
   **no network**, confirm the RTC holds it without an NTP step; (b) with the
   network **off**, confirm the GPS gets a fix and **PPS disciplines the clock**
   (e.g. `gpsd` + `chrony`/`ppstest`) to authoritative time. **PASS** = clock
   survives cold boot on the RTC *and* re-disciplines off-grid via GPS+PPS with
   no NTP reachable. Also confirm the node reports its own GPS position into
   `/api/nodes` / the map.
3. **:9443 (constraint #3)** — `curl -sf http://localhost:9443` responds after
   reboot; grep `config.d/` confirms no `Webserver: Port: 443` leaked.
   **PASS** = :9443 bound across reboot.
4. **rnsd substrate (constraint #4)** — `sudo ss -xnpl | grep "@rns/"` owner is
   rnsd; RNS-dependent units come up green on boot with no crashloop.
   **PASS** = clean bind, low `NRestarts`, units active.

---

## Eyes on it — enroll in moc's instruments

Per the fleet survey, **host on moc1, watch from moc**:
- Add the node to moc's kilo registry `~/.config/meshforge/kilo_nodes.json` →
  it appears on moc's **K1 link-matrix** (per-edge RF soundings, baseline drift)
  and the **map collector :5000** (`/api/nodes`, node-health).
- Cross-box gives us the Pi 5B bench (moc1) + the live RF instrument (moc)
  without compromise.

---

## What I can prep now vs what waits for hardware

- **Waits for the bench** (can't flash a node that doesn't exist): the entire
  provisioning + verification sequence above.
- **Prep-able now, but deliberately deferred**: a `reference-node` role in
  `fleet_roles.yaml` and a provisioning script. **Held on purpose** — Phase 0's
  first manual bring-up *teaches* the real provisioning steps; scripting it blind
  would be guessing, and adding an unused role to the SSOT yaml is clutter until
  the node exists. Same discipline as the RNS merge: make it boring by hand
  first, then automate the proven path.

---

## Exit criteria — Phase 0 → Phase 1 (the CM5 carrier board)

Phase 0 is **done** when: all four constraints verify-neutralized on the bench
over a multi-day soak **and** the node is visible + healthy on moc's link-matrix
and map collector. *Then* the CM5 carrier-board conversation ("hardware specific
to the MeshForge domain," in its fullest form) opens with a **proven spec** — RTC
+ SPI header + M.2 + clean power on one PCB, designing away the Pi 5 HAT-stack
friction — not a guess. Never commit the irreversible (silicon) step before the
image is boring on stock hardware.

---

## Grounding

Real entry points confirmed on-repo 2026-07-17: `scripts/install_noc.sh`,
`scripts/provision_role.py` (roles → `docs/fleet_roles.yaml`),
`scripts/verify_post_install.sh`, `src/utils/deployment_profiles.py` (profiles:
radio_maps/monitor/meshcore/gateway/**full**), `templates/systemd/` (mini-dudeai
units). Hardware constraints from `persistent_issues.md` (#10468, #58, #82,
RTC-less clock class). Deploy target from the fleet topology survey (moc = eyes,
moc1 = host).
