I have sufficient cross-checked material across all five research questions. Writing the report now.

---

# Out-of-Band ESP32 Watchdog for a Hard-Freezing SBC — Patterns, Products, and a Recommended Design

**Scope:** Using a memory-constrained, independently-powered ESP32 (WiFi + a lightweight pub/sub bus to a separate "brain" host + LoRa mesh decode) on the *same LAN subnet* as a Raspberry Pi that hard-freezes, as an out-of-band witness that (a) detects the freeze from a different vantage than a remote monitor, (b) reports over a path the dead Pi can't take down, and (c) optionally power-cycles the Pi safely.

A note on confidence: the engineering principles below are well-corroborated across independent sources (kernel docs, Memfault/Interrupt, vendor docs, multiple maker writeups) and I flag the few places sources disagree or where a claim rests on a single source. Product feature details (especially Witty Pi 5) are **BELIEVED** from search snippets — I was unable to fetch the primary pages (several returned HTTP 403); verify against the linked vendor docs before purchase.

---

## A. Patterns Synthesis — Detect, Report, (optionally) Reset

### A.1 The core dichotomy: who initiates the liveness signal?

There are two fundamentally different architectures for an external low-power MCU watching a more-powerful host. They detect *different* failure classes, and the choice is the single most important design decision.

**Pattern 1 — "Host pets the MCU" (host emits heartbeat; MCU alarms on absence).**
The host periodically emits a heartbeat (GPIO pulse, serial character, I²C register poke, or an MQTT message). The MCU runs a timer; if the heartbeat doesn't arrive within the window, the MCU declares the host dead and acts. This is the classic external-watchdog topology — a 555 or a supervisor IC waits for the host to "pat the dog," and on timeout drives the host's reset line low [*A Guide to Watchdog Timers for Embedded Systems — https://interrupt.memfault.com/blog/firmware-watchdog-best-practices*; *How to design a watchdog circuit — https://www.onzuu.com/blog/how-to-design-a-watchdog-circuit*]. A PC version of exactly this: "the PC had to send a heartbeat signal, i.e. a fixed character via a USB-to-serial adapter regularly. If it doesn't do so, the watchdog resets the PC after five minutes" [*Tom's Projects: A PC Watchdog — https://tomscircuits.blogspot.com/2014/11/a-pc-watchdog.html*].

- **Strength — detects a *true freeze* with high specificity.** The heartbeat is emitted by a userspace/kernel process *on the host*. A genuine kernel hang, scheduler deadlock, or PMIC/brownout fault stops the heartbeat. There is no ambiguity introduced by the network path, because the heartbeat can ride a *wired local channel* (GPIO/UART/I²C) that doesn't traverse the LAN at all. This is why Linux's own integration is built this way: `systemd` "will regularly ping the watchdog hardware, and if systemd or the kernel hang, this ping will not happen and the hardware will automatically reset the system" — set `RuntimeWatchdogSec=20s` and systemd pets `/dev/watchdog` every 10 s [*systemd for Administrators, Part XV — http://0pointer.de/blog/projects/watchdog.html*; *Configure systemd watchdog… — https://gist.github.com/mharsch/e942fc0f0092f69ea5904a727542340f*].
- **Weakness — only as good as the heartbeat's coverage.** If the heartbeat is emitted by a cron job or a shallow script, the host can be functionally dead (disk full, all services wedged) while the heartbeat keeps firing — a "still petting the dog while otherwise hung" failure. Memfault/Interrupt's guidance is to make the petting *prove real work*: have multiple subsystems check in to a software supervisor that only pets the hardware when all are healthy, rather than a dumb periodic kick [*Interrupt — firmware-watchdog-best-practices*]. The "smart watchdog" extension monitors the *content* of communications, not just their presence [*Improving IoT System Robustness Using Watchdog Timers — https://www.digikey.com/en/articles/improving-iot-system-robustness-using-watchdog-timers*].

**Pattern 2 — "MCU actively probes the host" (MCU pings/TCP/HTTP-checks the host).**
The MCU originates probes: ICMP ping, a TCP connect to a known port, an ARP query, or an HTTP GET against a health endpoint, and alarms when probes stop succeeding.

- **Strength — zero host-side cooperation required,** and it can verify *service-level* liveness (the health endpoint responds, not just the kernel). Good for catching "the box is up but the app is dead."
- **Weakness — it conflates host death with path death.** A failed ping from the MCU can mean the host froze *or* the switch port flapped, the host's NIC reset, or (if probing across a router) the WAN dropped. Discriminating these requires layering probe types (see §D). It also can't see a freeze that leaves the network stack answering — a Linux kernel can keep replying to ICMP/ARP from softirq context while userspace is fully wedged, so ping-only probing has false negatives.

**The practical answer is to run BOTH, on independent channels, and fuse them.** Pattern 1 over a wired local link is your high-confidence freeze detector; Pattern 2 over the LAN is your corroborating/secondary vantage and your service-liveness check. The kernel-watchdog literature makes the same point at the silicon level: an internal WDT and an *independent external* WDT catch different faults, and the external one (own clock, own power) is the backstop for the failures the internal one can't see — "an external WatchDog can… power cycle the computer, which will bring it back when the [internal reboot] does not restart in all conditions, especially in low-power/brownout conditions" [*SwitchDoc Labs: Adding a WatchDog Timer — https://www.switchdoc.com/2019/07/skyweather-adding-watchdog/*].

### A.2 Why an *independent* watchdog is the whole point

The defining reliability property is **fault independence**: the watcher must not share the failure domain of the watched. The kernel watchdog API and Interrupt both stress that a watchdog with its *own clock source and own power* survives faults that take down the monitored system [*The Linux Watchdog driver API — https://www.kernel.org/doc/html/latest/watchdog/watchdog-api.html*; *Interrupt — firmware-watchdog-best-practices*]. Your ESP32 already has the three independences that matter:

1. **Power independence** — its own supply, so a Pi brownout/PMIC fault (the exact class an internal reboot can't recover [*SwitchDoc*]) doesn't blind the witness.
2. **Compute/clock independence** — its own MCU and timer, so a Pi kernel lockup can't stop the alarm timer.
3. **Comms independence** — its own WiFi *and* LoRa, so it can report even when the Pi (which may host or route part of your normal telemetry path) is dead.

This is precisely the architectural reason the latest hardware watchdog HATs moved their logic *off* the Pi: "Previous Witty Pi boards depend on software running on the Raspberry Pi for scheduling, which can fail if the OS crashes or does not boot. With Witty Pi 5, all scheduling logic runs on the onboard RP2350 MCU" [*Witty Pi 5 HAT+ — https://www.cnx-software.com/2026/01/19/witty-pi-5-hat-a-raspberry-pi-rp2350-based-power-scheduler-with-time-temperature-and-voltage-based-triggers/*]. Your ESP32 is the off-host MCU — you already have the right topology.

### A.3 Reporting patterns

- **MQTT/pub-sub heartbeat + Last Will & Testament (LWT).** If the brain host (or a broker it reaches) is the reporting target, the cleanest "dead host" primitive is MQTT keep-alive + LWT: a client registers a "will" message that the broker publishes automatically if the client disconnects ungracefully (e.g., its power died). "The MQTT last will and testament facility can be used to identify hosts that have stopped communicating (as long as the MQTT broker is still up)" [*HiveMQ — MQTT LWT — https://www.hivemq.com/blog/mqtt-essentials-part-9-last-will-and-testament/*; *HankB/MQTT_will — https://github.com/HankB/MQTT_will*]. The broker declares a client dead if no packet arrives within **1.5×** the keep-alive interval [*HiveMQ — Keep Alive — https://www.hivemq.com/blog/mqtt-essentials-part-10-alive-client-take-over/*]. Caveat: LWT detects the *Pi's MQTT client* dying — useful as a corroborating signal, but it only works "as long as the broker is up," and if the broker runs *on the freezing Pi* it's worthless. Your ESP32's own bus to the brain host is the independent reporting path that doesn't have this dependency.
- **Out-of-band radio (LoRa).** Your ESP32's LoRa leg is a genuinely independent egress: it can report "Pi DOWN" over the mesh even if WiFi/LAN and the Pi are both gone. This is the strongest part of your hand — it's an out-of-band channel in the true sense (different PHY, different power, no shared infrastructure with the Pi).

---

## B. Concrete Reference Projects & Products

**Open / DIY external-MCU watchdogs**
- **ESP32 watchdog with relay power-cycle (F1ATB).** An ESP32 monitors a device and cuts/restores power via relay on heartbeat loss — directly analogous to your use case [*ESP32 Watchdog example — https://f1atb.fr/esp32-watchdog-example/*]. (I could not fetch the body — HTTP 403 — so treat the specifics as unverified; the title/abstract match the pattern.)
- **SwitchDoc external watchdog + USB PowerControl.** Pi pets the watchdog board over GPIO; on ~200 s without a pet, a 300 ms trigger drives a "USB PowerControl board (a solid-state relay)" that cuts USB power to the Pi and reboots it. Explicitly motivated by power-cycling for brownout recovery [*SwitchDoc — https://www.switchdoc.com/2019/07/skyweather-adding-watchdog/*].
- **555/supervisor-IC external watchdogs.** Classic monostable 555 (T ≈ 1.1·R·C) drives the host's active-low reset; for production, dedicated supervisor ICs (TI TPS382x, MAX6316, ADM811) are recommended over discrete timers [*Onzuu — watchdog circuit — https://www.onzuu.com/blog/how-to-design-a-watchdog-circuit*].
- **Pi-side software watchdog that pings a host.** `pingtest.sh` + systemd: `WatchdogSec=60`, `RETRIES=10`, half-interval sleep, `FailureAction=reboot-force` when retries exhaust [*mharsch gist — https://gist.github.com/mharsch/e942fc0f0092f69ea5904a727542340f*]. Useful as a *complement* on the Pi, but note it's in-band (reboots itself; can't recover a true freeze) — your external ESP32 is the backstop it can't be.

**Commercial power-management / watchdog HATs for Pi**
- **UUGear Witty Pi 5 HAT+ (RP2350).** RTC + power management with all scheduling on the onboard MCU so it survives an OS crash/no-boot; watchdog implemented via I²C register polling that updates a heartbeat value [*UUGear Witty Pi 5 — https://www.uugear.com/product/witty-pi-5/*; *CNX — https://www.cnx-software.com/2026/01/19/...*]. Earlier Witty Pi (ATtiny841) had *no* watchdog due to 8 KB firmware exhaustion — a relevant cautionary note about MCU memory limits [*Witty Pi Watchdog Functionality — https://www.uugear.com/forums/technial-support-discussion/witty-pi-watchdog-functionality/*]. (Feature claims **BELIEVED** from snippets; verify on vendor page.)
- **Witty Pi 4 / Adafruit** as the prior generation [*Adafruit 5704 — https://www.adafruit.com/product/5704*].

**Out-of-band / smart-power for the heavier-iron analogy (design inspiration, not Pi-scale)**
- **Switched smart PDU** — remote per-outlet power cycle; "incident response time drops from hours to roughly 30 seconds" by bouncing the exact outlet [*Yosun — Smart PDU Remote Reboot — https://www.yosunpdu.com/news/enhancing-it-resilience-with-switched-smart-pdu-remote-reboot-capabilities/*; *Opengear — vendor-neutral power control — https://opengear.com/blog/online-demo-tour-vendor-neutral-power-control/*]. The SBC-scale equivalent of a switched outlet is a Tasmota/Shelly smart plug or a relay on the Pi's USB-C supply.
- **IPMI / BMC (iDRAC, iLO, Redfish)** — the canonical out-of-band model: a separate management processor with its own NIC/power lets an admin "reset or power cycle the system to get a hung OS running again," and crucially *verifies machine state before rebooting to protect data integrity* [*Wikipedia — IPMI — https://en.wikipedia.org/wiki/Intelligent_Platform_Management_Interface*; *Cycle.io — Out-of-Band Management — https://cycle.io/learn/out-of-band-management*]. Your ESP32 is a poor-man's BMC for a Pi — adopt its discipline of "confirm state, then act."

**Pi reset/power semantics (for the actuator design)**
- **GLOBAL_EN → GND = power-cycle/wake.** "Connected to the power-management chip; pulling it low is similar to recycling the power" and "will restart the Pi 4 from a halted state" [*Raspberry Pi Forums — Reset Button Pi4 — https://forums.raspberrypi.com/viewtopic.php?t=243530*].
- **RUN → GND = hard reset.** "A hard reset… the OS doesn't get to do a clean shutdown first so you risk data corruption… equivalent to flipping power off then on again" [*Raspberry Pi Forums — Reset and Shutdown buttons — https://forums.raspberrypi.com/viewtopic.php?t=254739*].
- **Software PMIC reset** exists (`vcmailbox 0x00030057`) but requires a *responsive* OS and clean unmount — useless for a freeze [*Raspberry Pi Forums — PMIC Reset — https://forums.raspberrypi.com/viewtopic.php?t=331443*].

**Reset semantics summary (cite the table to the forum threads above):**

| Method | What it does | Recovers a true freeze? | Risk |
|---|---|---|---|
| Cut/restore supply (relay/MOSFET/smart-plug on USB-C) | Full power cycle; clears PMIC/brownout latch | **Yes — most thorough** | FS corruption; inrush; needs settle time |
| GLOBAL_EN → GND | "Recycle power" via PMIC, restart from halt | Yes (PMIC-mediated) | Like power cycle |
| RUN → GND | Hard reset, no clean shutdown | Yes (if not a power fault) | FS corruption; won't fix brownout |
| `vcmailbox` PMIC reset (software) | Clean software-triggered PMIC reset | **No** — needs live OS | n/a for freeze |

For a *hard freeze*, prefer a **power cycle** (relay/MOSFET on the supply, or a controllable smart plug) because it also clears PMIC/brownout latch-ups an internal reset cannot [*SwitchDoc*]. RUN-pin reset is simpler to wire but won't recover a power-fault freeze.

---

## C. Recommended Design for Your Situation

**Topology:** ESP32 (own power) as an out-of-band witness on the same subnet, fusing a wired host-heartbeat (Pattern 1) with active LAN probes (Pattern 2), reporting to the brain host over its independent bus **and** LoRa, with an *optional, gated* relay/MOSFET power-cycle of the Pi.

### C.1 Detection (fuse two independent channels)

1. **Primary — wired heartbeat (Pattern 1, highest specificity).** Run one wire between the Pi and the ESP32 and have a tiny Pi-side service emit a heartbeat the ESP32 reads. Two memory-cheap options:
   - **GPIO pulse:** Pi toggles a GPIO every N seconds (drive from `systemd` so a *kernel/systemd* hang stops it). ESP32 watches the edge.
   - **Serial char over the Pi's UART** (the "fixed character" pattern [*Tom's Projects*]). One byte every N seconds; trivial RAM cost on the ESP32.
   This channel is immune to LAN faults and gives you the cleanest "kernel froze" signal. Make the Pi-side emitter prove *real* health, not just liveness — gate it behind a check that key services are up, so a half-dead box stops petting (the Interrupt "supervisor pets only when all subsystems healthy" rule [*Interrupt — firmware-watchdog-best-practices*]).
2. **Secondary — active LAN probe (Pattern 2, corroboration + service liveness).** ESP32 probes the Pi over WiFi: a TCP connect to a known service port (proves userspace is alive, stronger than ICMP) plus an ARP-level reachability check (see §D). Memory-cheap; no host cooperation needed.
3. **Fusion rule.** Declare **HOST_FROZEN** only when the *wired heartbeat is absent* AND the *LAN probe fails* for N consecutive cycles. If the wired heartbeat is alive but the LAN probe fails → it's a *network/service* problem, not a freeze → report, don't reset. If the wired heartbeat is absent but ARP still answers → host network stack alive, userspace likely wedged → escalate, but this is the case to be careful with.

### C.2 Reporting (independent of the dead Pi)

- Publish state transitions (`HOST_UP` / `HOST_DEGRADED` / `HOST_FROZEN` / `RESET_ISSUED` / `RECOVERED`) to the brain host over the ESP32's own pub-sub bus. The brain host (with the LLM) decides escalation; the ESP32 stays a dumb sensor/actuator as intended.
- **Mirror critical alerts over LoRa.** This is your true out-of-band path — it works when WiFi/LAN and the Pi are all gone. Make `HOST_FROZEN` and `RESET_ISSUED` go out on both bus and LoRa.
- If a broker is involved, set an **MQTT LWT** on the *Pi's* client so the broker emits "Pi disconnected" within 1.5× keep-alive [*HiveMQ — LWT*] — a free corroborating signal — but do **not** make this your only detector (it fails if the broker rides the Pi).

### C.3 Optional auto-reset — the boot-loop-safe state machine

Drive a relay/MOSFET on the Pi's USB-C supply (preferred, clears PMIC/brownout [*SwitchDoc*]), or GLOBAL_EN→GND for a softer PMIC-mediated cycle. Gate it with this state machine, which encodes every safety rule from §below:

```
States: HEALTHY → SUSPECT → CONFIRMED → RESETTING → COOLDOWN → (HEALTHY | LOCKOUT)

HEALTHY:
  heartbeat OK and probe OK → stay.
  first miss → SUSPECT.

SUSPECT (debounce):
  require N consecutive missed heartbeats AND M failed probes
  (N≈3–5, spanning > worst-case GC/IO stall) before believing it.
  if planned_reboot_flag set (see below) → ignore, return HEALTHY.
  any success → HEALTHY (reset counters).
  threshold met → CONFIRMED.

CONFIRMED:
  report HOST_FROZEN on bus + LoRa.
  if resets_in_window >= MAX_RESETS  → LOCKOUT (do NOT reset).
  if auto_reset_enabled and not manual_disable → RESETTING.
  else stay CONFIRMED (alert-only).

RESETTING:
  assert reset/power-cut for a fixed pulse; if power-cycle, hold OFF
  for a settle interval (e.g. 5–10 s) then restore.
  record reset timestamp; resets_in_window++.
  → COOLDOWN.

COOLDOWN (boot grace, exponential backoff):
  suppress all detection for boot_grace (e.g. 90–180 s) so a booting
  Pi isn't mistaken for frozen — the "extra setup delay after a
  watchdog fault" rule. Backoff grows each cycle.
  after grace: if recovery CONFIRMED (heartbeat returns for K cycles)
       → HEALTHY (reset backoff).
  else → back to CONFIRMED with longer backoff.

LOCKOUT:
  stop auto-resets; raise a loud, sticky alert (bus + LoRa);
  require manual re-arm. Prevents infinite power-cycling of a box
  that won't come back.
```

**Why each guard exists (sourced):**
- **N missed heartbeats + debounce** — never act on one miss; an SBC can stall briefly under IO/GC. Standard watchdog retry discipline (`RETRIES=10` in the systemd ping gist [*mharsch gist*]).
- **Boot grace / startup delay** — the canonical anti-boot-loop rule: "the reset signal is delayed so the watchdog becomes enabled later, allowing time for the computer to boot… to avoid the system becoming stuck in an endless cycle of incomplete reboots" [*Watchdog timer — Wikipedia — https://en.wikipedia.org/wiki/Watchdog_timer*]. Disable the watchdog during a known reboot so it doesn't fight the boot [*Analog Devices — Disable the Watchdog Timer during System Reboot — https://www.analog.com/en/resources/design-notes/disable-the-watchdog-timer-during-system-reboot.html*].
- **MAX_RESETS per window → LOCKOUT** — bounded retries with a defined give-up action: Sun/Oracle ALOM uses `sys_maxbootfail` + `sys_bootfailrecovery` (max 6) to "keep the system from going through an endless cycle of reboots" [*Oracle ALOM Watchdog Timer — https://docs.oracle.com/cd/E19102-01/n440.srvr/817-5481-11/understanding_wdtimer.html*].
- **Exponential backoff** — widen the interval each failed cycle so a chronically-dead box isn't hammered; "extra setup delay every time it detects a watchdog fault" [*Wikipedia — Watchdog timer*].
- **Confirm recovery before re-arming** — only return to HEALTHY after the heartbeat genuinely comes back for K cycles; this is the BMC discipline of "verify state before acting" [*Cycle.io — Out-of-Band Management*].
- **Planned-reboot suppression** — before a deliberate Pi reboot, have the Pi raise a flag (a retained MQTT topic, a GPIO level, or a "going down" message the ESP32 latches) so the watcher doesn't power-cycle mid-maintenance.
- **Manual override / disable + alert-only mode** — a hard switch (physical or a bus command) to disable auto-reset, and a default *alert-only* mode you trust before enabling actuation.

### C.4 Memory-constrained ESP32 notes
Keep the ESP32 a pure sensor/actuator: fixed-size counters and timers, no dynamic buffers, no on-device decision logic beyond the state machine above. The Witty Pi history is the cautionary tale — an ATtiny841 couldn't fit a watchdog in 8 KB [*UUGear forum*]; you have far more headroom on an ESP32, but the discipline (small static state, intelligence on the brain host) is correct for tens-of-kB free heap. Use the ESP32's *own* hardware WDT (TWDT, default 5 s [*IoT Assistant — enable hardware WDT on ESP32 — https://iotassistant.io/esp32/enable-hardware-watchdog-timer-esp32-arduino-ide/*]) so the *witness itself* self-recovers — a watcher that can silently hang is worse than none.

---

## D. "Host froze" vs "network path down" — the discrimination logic

This is the capability your same-subnet ESP32 has that a *remote* SSH/HTTP prober structurally lacks. A remote probe that fails cannot tell whether the host died or the WAN/route to it died — the two are indistinguishable from afar. A **same-subnet** watcher can resolve it by descending the stack:

1. **ARP vs ICMP layering.** On the local subnet, ARP operates at L2 and "hosts generally cannot block ARP requests/responses and still communicate," whereas ICMP can be dropped or fail to be answered by a wedged userspace [*Nmap — Host Discovery Techniques — https://nmap.org/book/host-discovery-techniques.html*]. The decision matrix [*Microsoft — Troubleshoot TCP/IP — https://learn.microsoft.com/en-us/troubleshoot/windows-server/networking/troubleshoot-tcp-ip-communication-guidance*; *Linux.com — Ping: ICMP vs ARP — https://www.linux.com/news/ping-icmp-vs-arp/*]:
   - **ARP replies, ICMP/TCP fail** → host is *present at L2/L3* but not servicing higher layers → **host frozen or services wedged** (NIC powered, kernel may be answering from softirq while userspace is dead). This is the freeze signature.
   - **ARP gets no reply ("destination host unreachable")** → the host isn't answering at L2 at all → **NIC off / powered down / cable-or-switch fault** — i.e., either a *full* power-loss freeze *or* a local link failure (your wired heartbeat then disambiguates these two).
2. **The wired heartbeat is the tiebreaker the network can't provide.** Because it doesn't traverse the LAN, a *present* wired heartbeat + *failing* LAN probe = **network/link problem, not a host freeze** → report, do **not** reset. A *dead* wired heartbeat + *dead* ARP = **host truly down** → eligible for reset. This two-source fusion is what makes same-subnet + wired far more discriminating than any remote prober.
3. **Same-subnet vs remote, explicitly.** Your remote central monitor sees "Pi unreachable" and cannot know if it's the Pi or the path; it may also be blinded if the Pi participates in the telemetry path. The ESP32, sitting on the same L2 segment, sees the Pi's ARP/L2 presence directly and reports over LoRa/its own bus — so a transition like "remote says down, ESP32 says ARP-present-but-frozen" is *new information* that localizes the fault to the host, not the WAN.

**Caveat / contradiction to flag:** ICMP-fails-but-ARP-succeeds is *usually* a frozen/blocking host, but it can also be a host that merely firewalls ICMP while otherwise healthy [*Linux.com — Ping: ICMP vs ARP*]. That's exactly why you should probe a **TCP service port** (or your health endpoint) rather than relying on ICMP, and why the **wired heartbeat is the authoritative freeze signal** — the LAN probes corroborate and localize, but the wire decides.

---

## E. Security for an MCU that can observe and reset a host

An MCU that can power-cycle a host is a high-value target: anyone who can forge a reset command has a remote DoS / data-corruption button. MQTT/IoT control channels are, by default, unauthenticated and unencrypted [*einfochips — ESP32 Wi-Fi & MQTT Security — https://www.einfochips.com/blog/esp32-wi-fi-mqtt-security-how-to-protect-your-iot-devices-from-cyber-threats/*; *network-king — MQTT Security Problems — https://network-king.net/mqtt-security-problems-and-proven-solutions-for-iot-infrastructure/*]. Apply, in priority order:

1. **TLS on the control bus + broker cert pinning.** "By default MQTT is not encrypted and doesn't authenticate devices." Use TLS ≥ 1.2; embed the root CA in the ESP32 firmware so it verifies the broker and resists MITM [*coolplaydev — Secure MQTT for ESP32 — https://coolplaydev.com/secure-mqtt-for-esp32*; *einfochips*]. (Mind ESP32 heap: TLS handshakes are RAM-hungry on a tens-of-kB budget — size accordingly or use a PSK ciphersuite.)
2. **Authenticate every actor + ACL the reset topic.** Username/password or token (JWT) auth, and **ACLs/RBAC** so only the brain host's identity may publish to the reset/command topic; the ESP32 subscribes to commands but only from authorized publishers [*einfochips*; *DIY Usthad — MQTT Authentication — https://diyusthad.com/2025/01/mqtt-authentication-enabling-secure-remote-access-to-your-mosquitto-broker.html*]. "Missing access controls enable attackers to manipulate critical systems" [*network-king*].
3. **Authorize the *action*, not just the channel.** The reset command should be a signed/nonce'd, non-replayable instruction (so a captured packet can't be replayed to force a reset), and the ESP32 should additionally require its *own locally-confirmed* HOST_FROZEN state before honoring an external reset — i.e., a remote command alone can't reset a healthy box. Defense-in-depth: local fusion logic + authenticated command must agree.
4. **Minimize network exposure.** Keep the control bus on the LAN/VPN, never expose the broker or the ESP32's command interface to the WAN; segment if possible. The IPMI world's hard-won lesson is that out-of-band management planes are juicy targets and belong on isolated management networks [*ServerMall — IPMI/BMC in 2026 — https://servermall.com/blog/ipmi-bmc-ikvm-redfish-what-it-is-and-how-it-works/*].
5. **Physical/local override.** A hardware disable for the reset relay (jumper/switch) so a compromised bus still can't actuate, plus the LOCKOUT/alert-only defaults from §C.3. The LoRa leg should be *report-only* by default — don't accept reset commands over an unauthenticated radio link.

---

## Bottom line

- **Architecture:** your ESP32 already has the three independences (power, compute, comms) that define a real out-of-band witness — the same reason watchdog HATs moved logic off the Pi [*CNX/Witty Pi 5*].
- **Detection:** fuse a **wired host heartbeat** (Pattern 1, the authoritative freeze signal, LAN-immune) with **active LAN probes** (Pattern 2, service liveness + corroboration). Same-subnet ARP/L2 visibility + the wire is what lets you distinguish *host froze* from *path down* — a discrimination a remote prober cannot make [*Nmap*; *Microsoft TCP/IP*].
- **Reset (optional):** prefer a **power cycle** (clears PMIC/brownout [*SwitchDoc*]); gate it with the **debounce → confirm → MAX_RESETS → backoff → boot-grace → confirm-recovery → LOCKOUT** state machine, with planned-reboot suppression and a manual disable [*Wikipedia*; *Oracle ALOM*; *Analog Devices*].
- **Report:** over the independent bus *and* LoRa, with MQTT LWT as a corroborating (not sole) signal [*HiveMQ*].
- **Secure:** TLS + cert-pinning + per-topic ACLs + signed/non-replayable reset commands + local-state agreement + physical override + no WAN exposure [*einfochips*; *coolplaydev*; *ServerMall*].

**Verification status:** Engineering principles and the freeze-vs-network discrimination logic are **well-corroborated** across independent authoritative sources cited inline. Product-specific feature claims (Witty Pi 5 watchdog mechanics, F1ATB project internals) are **BELIEVED** from search snippets only — several primary pages returned HTTP 403 and I could not read the full text; confirm those against the linked vendor/project pages before relying on exact specs.

### Sources
- [A Guide to Watchdog Timers for Embedded Systems — https://interrupt.memfault.com/blog/firmware-watchdog-best-practices](https://interrupt.memfault.com/blog/firmware-watchdog-best-practices)
- [How to design a watchdog circuit — https://www.onzuu.com/blog/how-to-design-a-watchdog-circuit](https://www.onzuu.com/blog/how-to-design-a-watchdog-circuit)
- [Tom's Projects: A PC Watchdog — https://tomscircuits.blogspot.com/2014/11/a-pc-watchdog.html](https://tomscircuits.blogspot.com/2014/11/a-pc-watchdog.html)
- [systemd for Administrators, Part XV — http://0pointer.de/blog/projects/watchdog.html](http://0pointer.de/blog/projects/watchdog.html)
- [Configure systemd watchdog to reboot if it can't ping a host (mharsch gist) — https://gist.github.com/mharsch/e942fc0f0092f69ea5904a727542340f](https://gist.github.com/mharsch/e942fc0f0092f69ea5904a727542340f)
- [The Linux Watchdog driver API — https://www.kernel.org/doc/html/latest/watchdog/watchdog-api.html](https://www.kernel.org/doc/html/latest/watchdog/watchdog-api.html)
- [Improving IoT System Robustness Using Watchdog Timers (DigiKey) — https://www.digikey.com/en/articles/improving-iot-system-robustness-using-watchdog-timers](https://www.digikey.com/en/articles/improving-iot-system-robustness-using-watchdog-timers)
- [SwitchDoc Labs: Adding a WatchDog Timer — https://www.switchdoc.com/2019/07/skyweather-adding-watchdog/](https://www.switchdoc.com/2019/07/skyweather-adding-watchdog/)
- [ESP32 Watchdog example (F1ATB) — https://f1atb.fr/esp32-watchdog-example/](https://f1atb.fr/esp32-watchdog-example/)
- [Enable hardware WDT on ESP32 (IoT Assistant) — https://iotassistant.io/esp32/enable-hardware-watchdog-timer-esp32-arduino-ide/](https://iotassistant.io/esp32/enable-hardware-watchdog-timer-esp32-arduino-ide/)
- [Witty Pi 5 HAT+ (CNX Software) — https://www.cnx-software.com/2026/01/19/witty-pi-5-hat-a-raspberry-pi-rp2350-based-power-scheduler-with-time-temperature-and-voltage-based-triggers/](https://www.cnx-software.com/2026/01/19/witty-pi-5-hat-a-raspberry-pi-rp2350-based-power-scheduler-with-time-temperature-and-voltage-based-triggers/)
- [Witty Pi 5 HAT+ product page (UUGear) — https://www.uugear.com/product/witty-pi-5/](https://www.uugear.com/product/witty-pi-5/)
- [Witty Pi Watchdog Functionality (UUGear forum) — https://www.uugear.com/forums/technial-support-discussion/witty-pi-watchdog-functionality/](https://www.uugear.com/forums/technial-support-discussion/witty-pi-watchdog-functionality/)
- [Witty Pi 4 HAT (Adafruit 5704) — https://www.adafruit.com/product/5704](https://www.adafruit.com/product/5704)
- [Reset Button Raspberry Pi 4 (RPi Forums) — https://forums.raspberrypi.com/viewtopic.php?t=243530](https://forums.raspberrypi.com/viewtopic.php?t=243530)
- [Reset and Shutdown/Power-on buttons on Pi 4 (RPi Forums) — https://forums.raspberrypi.com/viewtopic.php?t=254739](https://forums.raspberrypi.com/viewtopic.php?t=254739)
- [PMIC Reset at reboot (RPi Forums) — https://forums.raspberrypi.com/viewtopic.php?t=331443](https://forums.raspberrypi.com/viewtopic.php?t=331443)
- [Watchdog timer (Wikipedia) — https://en.wikipedia.org/wiki/Watchdog_timer](https://en.wikipedia.org/wiki/Watchdog_timer)
- [Understanding the ALOM Watchdog Timer (Oracle) — https://docs.oracle.com/cd/E19102-01/n440.srvr/817-5481-11/understanding_wdtimer.html](https://docs.oracle.com/cd/E19102-01/n440.srvr/817-5481-11/understanding_wdtimer.html)
- [Disable the Watchdog Timer during System Reboot (Analog Devices) — https://www.analog.com/en/resources/design-notes/disable-the-watchdog-timer-during-system-reboot.html](https://www.analog.com/en/resources/design-notes/disable-the-watchdog-timer-during-system-reboot.html)
- [Smart PDU Remote Reboot (Yosun) — https://www.yosunpdu.com/news/enhancing-it-resilience-with-switched-smart-pdu-remote-reboot-capabilities/](https://www.yosunpdu.com/news/enhancing-it-resilience-with-switched-smart-pdu-remote-reboot-capabilities/)
- [Vendor-Neutral Power Management (Opengear) — https://opengear.com/blog/online-demo-tour-vendor-neutral-power-control/](https://opengear.com/blog/online-demo-tour-vendor-neutral-power-control/)
- [Intelligent Platform Management Interface (Wikipedia) — https://en.wikipedia.org/wiki/Intelligent_Platform_Management_Interface](https://en.wikipedia.org/wiki/Intelligent_Platform_Management_Interface)
- [Out-of-Band Management — IPMI/iDRAC/iLO (Cycle.io) — https://cycle.io/learn/out-of-band-management](https://cycle.io/learn/out-of-band-management)
- [IPMI, BMC, iKVM & Redfish in 2026 (ServerMall) — https://servermall.com/blog/ipmi-bmc-ikvm-redfish-what-it-is-and-how-it-works/](https://servermall.com/blog/ipmi-bmc-ikvm-redfish-what-it-is-and-how-it-works/)
- [Host Discovery Techniques (Nmap) — https://nmap.org/book/host-discovery-techniques.html](https://nmap.org/book/host-discovery-techniques.html)
- [Troubleshoot TCP/IP communication (Microsoft Learn) — https://learn.microsoft.com/en-us/troubleshoot/windows-server/networking/troubleshoot-tcp-ip-communication-guidance](https://learn.microsoft.com/en-us/troubleshoot/windows-server/networking/troubleshoot-tcp-ip-communication-guidance)
- [Ping: ICMP vs. ARP (Linux.com) — https://www.linux.com/news/ping-icmp-vs-arp/](https://www.linux.com/news/ping-icmp-vs-arp/)
- [MQTT Last Will and Testament (HiveMQ) — https://www.hivemq.com/blog/mqtt-essentials-part-9-last-will-and-testament/](https://www.hivemq.com/blog/mqtt-essentials-part-9-last-will-and-testament/)
- [MQTT Keep Alive and Client Take-Over (HiveMQ) — https://www.hivemq.com/blog/mqtt-essentials-part-10-alive-client-take-over/](https://www.hivemq.com/blog/mqtt-essentials-part-10-alive-client-take-over/)
- [MQTT_will: monitor host up via LWT (HankB/GitHub) — https://github.com/HankB/MQTT_will](https://github.com/HankB/MQTT_will)
- [ESP32 Wi-Fi & MQTT Security (einfochips) — https://www.einfochips.com/blog/esp32-wi-fi-mqtt-security-how-to-protect-your-iot-devices-from-cyber-threats/](https://www.einfochips.com/blog/esp32-wi-fi-mqtt-security-how-to-protect-your-iot-devices-from-cyber-threats/)
- [Secure MQTT for ESP32 (coolplaydev) — https://coolplaydev.com/secure-mqtt-for-esp32](https://coolplaydev.com/secure-mqtt-for-esp32)
- [MQTT Authentication (DIY Usthad) — https://diyusthad.com/2025/01/mqtt-authentication-enabling-secure-remote-access-to-your-mosquitto-broker.html](https://diyusthad.com/2025/01/mqtt-authentication-enabling-secure-remote-access-to-your-mosquitto-broker.html)
- [MQTT Security Problems Solved (network-king) — https://network-king.net/mqtt-security-problems-and-proven-solutions-for-iot-infrastructure/](https://network-king.net/mqtt-security-problems-and-proven-solutions-for-iot-infrastructure/)